import json, re, subprocess, socket, getpass, shutil, threading
from datetime import datetime, timezone
from core.manifest import SCHEMA_VERSION
from utils.resources import find_binary

RCLONE_BIN = "rclone"


def _rclone() -> str:
    """Resolve the rclone executable: bundled copy when frozen, else PATH."""
    return find_binary(RCLONE_BIN) or RCLONE_BIN

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


def cat_sha256(remote_path, extra_flags=None, timeout=3600, chunk_size=1 << 20):
    """
    Stream a single remote file via `rclone cat` and return its SHA-256, without
    retaining the file on disk or buffering it in memory (M5.1 deep Drive verify).

    Bytes are read from stdout in chunks and folded into the hash incrementally,
    so a multi-GB clip costs only one chunk of RAM. Honours cancel_current() by
    registering the process in _current_proc.

    Returns the lowercase hex digest. Raises RuntimeError on a non-zero rclone
    exit (e.g. file missing, auth failure) or TimeoutError on timeout.
    """
    import hashlib

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

    h = hashlib.sha256()
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
            if "sha256" in h: cs["sha256"]     = h["sha256"].lower()
            if "sha1"   in h: cs["sha1"]       = h["sha1"].lower()
            if "md5"    in h: cs["md5"]        = h["md5"].lower()
            if "xxhash" in h: cs["xxhash3_64"] = h["xxhash"].lower()
        drive_id = item.get("ID", "")
        gdrive_url = f"https://drive.google.com/file/d/{drive_id}/view" if drive_id else ""
        # MANIFEST-FIX: record hash_algorithm per entry so a manifest produced from
        # lsjson is complete on load (no reliance on backfill inference).
        if "sha256" in cs:
            hash_algo = "sha256"
        elif "md5" in cs:
            hash_algo = "md5"
        elif "xxhash3_64" in cs:
            hash_algo = "xxhash3_64"
        elif "sha1" in cs:
            hash_algo = "sha1"
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
        # MANIFEST-FIX: standardise checksum_context shape (method + gdrive_mode +
        # paranoid_fallback_count) for cross-module interoperability.
        "checksum_context": {
            "algorithm": "rclone-lsjson",
            "method": "rclone",
            "gdrive_mode": True,
            "source": "lsjson --hash",
            "paranoid_fallback_count": 0,
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
