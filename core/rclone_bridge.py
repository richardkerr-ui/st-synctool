import json, re, subprocess, socket, getpass, shutil, threading
from datetime import datetime, timezone
from core.manifest import SCHEMA_VERSION
from utils.resources import find_binary

RCLONE_BIN = "rclone"

# M15.2: pinned rclone version. rclone flag semantics and backend hash behaviour
# drift between releases, so the version is pinned and bumped DELIBERATELY (a code
# change), never silently picked up from the next build machine's rclone. This is
# the version build.sh must bundle and the floor preflight enforces at runtime.
RCLONE_REQUIRED_VERSION = "1.74.3"


def _rclone() -> str:
    """Resolve the rclone executable: bundled copy when frozen, else PATH."""
    return find_binary(RCLONE_BIN) or RCLONE_BIN


def _version_tuple(s):
    """('1.74.3') -> (1, 74, 3); None if unparseable."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s or "")
    return tuple(int(x) for x in m.groups()) if m else None


def rclone_version() -> "str|None":
    """Return the running rclone's version string (e.g. '1.74.3'), or None if
    rclone is absent/unparseable. Recorded per transfer in the custody log so a
    future dispute traces to the exact binary that ran the job (M15.2)."""
    try:
        out = subprocess.run([_rclone(), "version"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"rclone v(\d+\.\d+\.\d+)", out or "")
    return m.group(1) if m else None


def meets_required_version(version_str, required=RCLONE_REQUIRED_VERSION) -> bool:
    """True if ``version_str`` is >= the pinned floor. The pin is treated as a
    minimum at runtime (lenient for users who brew-upgrade); build.sh enforces an
    exact-match bundle for determinism."""
    have = _version_tuple(version_str)
    need = _version_tuple(required)
    return bool(have and need and have >= need)


# M15.2: backends whose `--checksum` produces a real content-hash comparison.
# Google Drive exposes md5 natively; the local backend lets rclone compute a hash
# on both sides. For these, `--checksum` hash-compares (not size+modtime). Other
# backends (NAS via SMB/NFS, exFAT remotes) must be confirmed by the M15.2 manual
# backend audit before they can be trusted as integrity-verified — until then a
# transfer touching them surfaces a loud custody-log error and does NOT count
# toward the M14.1 clearance gate.
def backend_supports_checksum(remote: str) -> bool:
    """Conservative: True only for backends known to hash-compare under
    --checksum (Google Drive, and local filesystem paths). Unknown/unconfirmed
    backends return False so the caller can warn loudly."""
    from utils.gdrive_utils import is_gdrive_url
    s = str(remote)
    if is_gdrive_url(s):
        return True
    # A connection-string Drive remote ("gdrive,root_folder_id=…:") or named
    # "gdrive:" remote also hash-compares on md5.
    if s.startswith("gdrive") and ":" in s.split("/", 1)[0]:
        return True
    # A bare local path (no "remote:" prefix) is the local backend — rclone
    # computes a hash on both sides.
    if "://" not in s and not re.match(r"^[A-Za-z0-9_-]+:", s):
        return True
    return False

_current_proc = None
_current_proc_lock = threading.Lock()

# Matches rclone --stats-one-line output. Real-world format observed:
#   "2026/06/08 15:38:36 NOTICE: 19.996 MiB / 2.421 GiB, 1%, 0 B/s, ETA - (xfr#0/20)"
#   "NOTICE: 45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47, chk#3/47)"
# Groups: (1) pct  (2) speed  (3) eta  (4) xfr_done  (5) xfr_total
_PROGRESS_RE = re.compile(
    r"\d[\d.]*\s*[KMGTPE]?i?B\s*/\s*\d[\d.]*\s*[KMGTPE]?i?B"
    r",\s*(\d+)\s*%"                              # group 1: percent
    r"(?:,\s*([\d.]+\s*[KMGTPE]?i?B/s))?"        # group 2: speed (optional)
    r"(?:,\s*ETA\s*([\w-]+))?"                    # group 3: ETA (optional)
    r"(?:.*\(xfr#(\d+)/(\d+))?"                  # groups 4-5: files done / total (optional)
)

# Matches rclone INFO lines that announce a file is actively being or was copied.
# Used to track the currently-transferring filename for live UI display.
# Handles: "INFO  : filename: Copying", "INFO  : filename: Copied ..."
_CURRENT_FILE_RE = re.compile(
    r"INFO\s*:\s+(.+?):\s+Cop(?:ying|ied)",
    re.IGNORECASE,
)


def is_rclone_installed() -> bool:
    return find_binary(RCLONE_BIN) is not None


# --------------------------------------------------------------------------- #
# I/O seam (M11.x): every rclone command goes through _run, which delegates to a
# swappable runner. The default shells out to the rclone binary; tests install a
# fake runner with set_rclone_runner() to exercise the full Drive paths (verify,
# transfer, merge, log-shipping, org-refresh) without a network or real rclone.
# --------------------------------------------------------------------------- #

_RUNNER = None  # None -> use _run_subprocess (the real backend)


def set_rclone_runner(fn):
    """Install a fake rclone runner. Returns the previous one (or None)."""
    global _RUNNER
    prev = _RUNNER
    _RUNNER = fn
    return prev


def reset_rclone_runner():
    """Restore the real subprocess runner."""
    global _RUNNER
    _RUNNER = None


def _run(args, timeout=300, log_cb=None, progress_cb=None):
    """Dispatch an rclone command through the active runner (real or fake)."""
    runner = _RUNNER or _run_subprocess
    return runner(args, timeout=timeout, log_cb=log_cb, progress_cb=progress_cb)


def _run_subprocess(args, timeout=300, log_cb=None, progress_cb=None):
    """Real backend: run an rclone command and stream stderr for progress + log.

    progress_cb receives (pct: int, info: dict) where info contains:
        line         -- original stats line (always present)
        speed        -- e.g. "12.3 MB/s" (may be None)
        eta          -- e.g. "1m2s" or "-" (may be None)
        files_done   -- int (may be None)
        files_total  -- int (may be None)
        current_file -- filename being transferred (may be None)

    Callers that only use the first argument (pct) are unaffected.
    """
    global _current_proc
    if log_cb:
        log_cb(f"  rclone {' '.join(args)}", "info")

    proc = subprocess.Popen(
        [_rclone()] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    with _current_proc_lock:
        _current_proc = proc

    stdout_chunks, stderr_chunks = [], []
    # Shared mutable state for the most recently seen transferring filename.
    # Both reader threads can write; the progress line reader consumes it.
    _state = {"current_file": None}
    _state_lock = threading.Lock()

    def reader(stream, chunks, is_stderr):
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
                stripped = line.rstrip()
                if not stripped:
                    continue
                if is_stderr:
                    # Check for a current-file INFO line first
                    fm = _CURRENT_FILE_RE.search(stripped)
                    if fm:
                        with _state_lock:
                            _state["current_file"] = fm.group(1).strip()

                    # Check for a stats NOTICE line
                    m = _PROGRESS_RE.search(stripped)
                    if m and progress_cb:
                        with _state_lock:
                            cur_file = _state["current_file"]
                        info = {
                            "line": stripped,
                            "speed": m.group(2),
                            "eta": m.group(3),
                            "files_done": int(m.group(4)) if m.group(4) is not None else None,
                            "files_total": int(m.group(5)) if m.group(5) is not None else None,
                            "current_file": cur_file,
                        }
                        try:
                            progress_cb(int(m.group(1)), info)
                        except Exception:
                            pass
                        continue
                if log_cb:
                    try:
                        log_cb(stripped, "info")
                    except Exception:
                        pass
        finally:
            stream.close()

    t_out = threading.Thread(target=reader, args=(proc.stdout, stdout_chunks, False), daemon=True)
    t_err = threading.Thread(target=reader, args=(proc.stderr, stderr_chunks, True), daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        with _current_proc_lock:
            _current_proc = None

    class _Result:
        pass
    r = _Result()
    r.returncode = proc.returncode
    r.stdout = "".join(stdout_chunks)
    r.stderr = "".join(stderr_chunks)
    return r


def cancel_current() -> bool:
    with _current_proc_lock:
        p = _current_proc
    if not p or p.poll() is not None:
        return False
    try:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        return True
    except Exception:
        return False


def _new_hasher(algo):
    """Build a streaming hasher for `algo`. xxh128 comes from the xxhash lib
    (M13: content-identity algorithm); md5 stays available via hashlib for the
    Drive-to-Drive deep-verify fallback (Drive's native algorithm)."""
    if algo == "xxh128":
        import xxhash
        return xxhash.xxh128()
    import hashlib
    return hashlib.new(algo)


def _cat_file(remote_path, algo, extra_flags=None, timeout=3600, chunk_size=1 << 20):
    """Stream a remote file via `rclone cat` and return its hex digest for `algo`."""
    global _current_proc
    args = ["cat"]
    if extra_flags:
        args.extend(extra_flags)
    args.append(remote_path)

    proc = subprocess.Popen(
        [_rclone()] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    with _current_proc_lock:
        _current_proc = proc

    h = _new_hasher(algo)
    try:
        while True:
            chunk = proc.stdout.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise TimeoutError(f"rclone cat timed out for {remote_path}")
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.returncode != 0:
            raise RuntimeError(f"rclone cat failed for {remote_path}: {stderr.strip()}")
        return h.hexdigest().lower()
    finally:
        for s in (proc.stdout, proc.stderr):
            try:
                s.close()
            except Exception:
                pass
        with _current_proc_lock:
            _current_proc = None


def cat_xxh128(remote_path, extra_flags=None, timeout=3600, chunk_size=1 << 20):
    """Stream a remote file and return its xxh128 hex digest (M13 deep verify).

    Downloads the bytes via `rclone cat` (nothing retained) and hashes them with
    the same content-identity algorithm local manifests use, so a deep verify
    compares like-for-like against the manifest's `xxh128` key."""
    return _cat_file(remote_path, "xxh128", extra_flags=extra_flags,
                     timeout=timeout, chunk_size=chunk_size)


def cat_md5(remote_path, extra_flags=None, timeout=3600, chunk_size=1 << 20):
    """Stream a remote file and return its MD5 hex digest (fallback for Drive-origin manifests)."""
    return _cat_file(remote_path, "md5", extra_flags=extra_flags,
                     timeout=timeout, chunk_size=chunk_size)


def lsjson(remote_path, extra_flags=None, with_checksum=True):
    args = ["lsjson", "--recursive"]
    if with_checksum:
        args.append("--hash")
    if extra_flags:
        args.extend(extra_flags)
    args.append(remote_path)
    r = _run(args, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"rclone lsjson failed: {r.stderr}")
    return json.loads(r.stdout)


def find_activity_shards(remote_base, extra_flags=None):
    """M9.3: list the full remote paths of every ``activity_*.jsonl`` shard under
    the org activity base (recursive). Used to pull other machines' summaries
    (kilobytes) without ever listing the raw logs."""
    args = ["lsjson", "--recursive", "--files-only"]
    if extra_flags:
        args.extend(extra_flags)
    args.append(remote_base)
    r = _run(args, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"rclone lsjson failed: {r.stderr}")
    base = remote_base.rstrip("/")
    out = []
    for entry in json.loads(r.stdout):
        rel = entry.get("Path", "")
        name = rel.rsplit("/", 1)[-1]
        if name.startswith("activity_") and name.endswith(".jsonl"):
            out.append(f"{base}/{rel}")
    return out


def remote_size(remote_path, extra_flags=None, timeout=120):
    args = ["size", "--json"]
    if extra_flags:
        args.extend(extra_flags)
    args.append(remote_path)
    r = _run(args, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"rclone size failed: {r.stderr}")
    data = json.loads(r.stdout)
    return int(data.get("bytes", 0)), int(data.get("count", 0))


def lsjson_to_manifest(remote_path, extra_flags=None, label="server"):
    items = lsjson(remote_path, extra_flags=extra_flags, with_checksum=True)
    files = {}
    for item in items:
        if item.get("IsDir"):
            continue
        cs = {}
        if "Hashes" in item:
            h = {k.lower(): v for k, v in item["Hashes"].items()}
            # M13: Drive's universal native hash is md5; that is the only key a
            # Drive-listing manifest carries (Drive-to-Drive entries stay md5-only
            # by design). rclone's "xxhash" is XXH3-64, a different algorithm and
            # width from our xxh128 content key, so it is deliberately not mapped
            # in — claiming xxh128 from a 64-bit digest would be a false identity.
            if "md5" in h: cs["md5"] = h["md5"].lower()
        drive_id = item.get("ID", "")
        gdrive_url = f"https://drive.google.com/file/d/{drive_id}/view" if drive_id else ""
        # MANIFEST-FIX: record hash_algorithm per entry so a manifest produced from
        # lsjson is complete on load (no reliance on backfill inference).
        if "md5" in cs:
            hash_algo = "md5"
        else:
            hash_algo = "rclone-lsjson"
        files[item["Path"]] = {
            "type": "file",
            "size": item.get("Size", 0),
            "modtime": item.get("ModTime", ""),
            "checksums": cs,
            "hash_algorithm": hash_algo,
            "gdrive_url": gdrive_url,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "root": remote_path,  # display label only
        "counterpart_path": remote_path,
        "operation": "",
        "project_id": "",
        "workstation": socket.gethostname(),
        "user": getpass.getuser(),
        "file_count": len(files),
        "renames": [],
        # MANIFEST-FIX: standardise checksum_context shape (method + gdrive_mode)
        # for cross-module interoperability.
        "checksum_context": {
            "algorithm": "rclone-lsjson",
            "method": "rclone",
            "gdrive_mode": True,
            "source": "lsjson --hash",
        },
        "files": files,
        "total_size_bytes": sum(v["size"] for v in files.values()),
    }


def sync(src, dst, mode="copy", conflict="overwrite",
         src_flags=None, dst_flags=None, dry_run=False,
         log_cb=None, progress_cb=None):
    if mode not in ("copy", "sync"):
        raise ValueError(f"Invalid rclone mode: {mode}")

    cmd = "sync" if mode == "sync" else "copy"
    args = [
        cmd, src, dst,
        "--checksum",
        "--transfers", "4",
        "--stats", "1s",
        "--stats-one-line",
        "--stats-log-level", "NOTICE",
        "--verbose",  # enables INFO lines per file (Copying/Copied) for live filename tracking
    ]

    if conflict == "skip":
        args.append("--ignore-existing")
    elif conflict == "update":
        args.append("--update")
    elif conflict == "rename":
        if log_cb:
            log_cb("'Rename copy' is not supported for Google Drive transfers - "
                   "falling back to Overwrite.", "warning")

    if dry_run:
        args.append("--dry-run")
    for flag_list in (src_flags, dst_flags):
        if flag_list:
            args.extend(flag_list)

    r = _run(args, timeout=24 * 3600, log_cb=log_cb, progress_cb=progress_cb)

    if r.returncode != 0 and log_cb:
        log_cb(f"rclone {cmd} exited with code {r.returncode}", "error")
        _log_quota_classification(r.stderr, log_cb)

    return r.returncode == 0


def copyto(src, dst, src_flags=None, dst_flags=None, log_cb=None):
    """Copy a single file with --checksum verification. Used by Merge tab."""
    args = ["copyto", src, dst, "--checksum"]
    for f in (src_flags, dst_flags):
        if f:
            args.extend(f)
    r = _run(args, timeout=24 * 3600, log_cb=log_cb)
    if r.returncode != 0 and log_cb:
        _log_quota_classification(r.stderr, log_cb)
    return r.returncode == 0


def copyto_result(src: str, dst: str) -> tuple:
    """Like copyto but returns (ok: bool, stderr: str) so callers can classify errors."""
    args = ["copyto", src, dst, "--checksum"]
    r = _run(args, timeout=24 * 3600)
    return r.returncode == 0, r.stderr or ""


def _log_quota_classification(stderr, log_cb):
    """Surface a plain-language Google quota / rate-limit message (M10.2)."""
    from core import quota
    cls = quota.classify_rclone_error(stderr)
    if cls and log_cb:
        log_cb(cls.message, "error")


def deletefile(path, extra_flags=None, log_cb=None):
    """Delete a single file from the remote. Used by Merge tab."""
    args = ["deletefile", path]
    if extra_flags:
        args.extend(extra_flags)
    r = _run(args, timeout=300, log_cb=log_cb)
    return r.returncode == 0


def path_exists(path, extra_flags=None):
    """Check if a single remote path exists by attempting to size it."""
    args = ["size", "--json", path]
    if extra_flags:
        args.extend(extra_flags)
    r = _run(args, timeout=30)
    return r.returncode == 0
