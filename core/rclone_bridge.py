import json, re, subprocess, socket, getpass, shutil, threading
from datetime import datetime, timezone
from core.manifest import SCHEMA_VERSION

RCLONE_BIN = "rclone"

_current_proc = None
_current_proc_lock = threading.Lock()

# Matches rclone --stats-one-line output. Real-world format observed:
#   "2026/06/08 15:38:36 NOTICE: 19.996 MiB / 2.421 GiB, 1%, 0 B/s, ETA - (xfr#0/20)"
_PROGRESS_RE = re.compile(r"\d[\d.]*\s*[KMGTPE]?i?B\s*/\s*\d[\d.]*\s*[KMGTPE]?i?B,\s*(\d+)\s*%")


def is_rclone_installed() -> bool:
    return shutil.which(RCLONE_BIN) is not None


def _run(args, timeout=300, log_cb=None, progress_cb=None):
    global _current_proc
    if log_cb:
        log_cb(f"  rclone {' '.join(args)}", "info")

    proc = subprocess.Popen(
        [RCLONE_BIN] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    with _current_proc_lock:
        _current_proc = proc

    stdout_chunks, stderr_chunks = [], []

    def reader(stream, chunks, is_stderr):
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
                stripped = line.rstrip()
                if not stripped:
                    continue
                if is_stderr:
                    m = _PROGRESS_RE.search(stripped)
                    if m and progress_cb:
                        try:
                            progress_cb(int(m.group(1)), stripped)
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
        files[item["Path"]] = {
            "type": "file",
            "size": item.get("Size", 0),
            "modtime": item.get("ModTime", ""),
            "checksums": cs,
            "gdrive_url": gdrive_url,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "root": remote_path,  # display label only — use server_path for the server side
        "server_path": remote_path,
        "operation": "",
        "project_id": "",
        "workstation": socket.gethostname(),
        "user": getpass.getuser(),
        "file_count": len(files),
        "renames": [],
        "checksum_context": {
            "algorithm": "rclone-lsjson",
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

    return r.returncode == 0


def copyto(src, dst, src_flags=None, dst_flags=None, log_cb=None):
    """Copy a single file with --checksum verification. Used by Merge tab."""
    args = ["copyto", src, dst, "--checksum"]
    for f in (src_flags, dst_flags):
        if f:
            args.extend(f)
    r = _run(args, timeout=24 * 3600, log_cb=log_cb)
    return r.returncode == 0


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
