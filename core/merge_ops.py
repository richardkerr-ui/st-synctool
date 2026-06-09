"""Single-file push/pull/delete operations for the Merge tab.
Abstracts local-vs-rclone server destinations behind a uniform API."""

import shutil
import getpass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from core.checksum import compute_all
from core import rclone_bridge
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone


# Action constants (single source of truth for Merge tab strings)
ACT_PUSH          = "Push to Server"
ACT_PULL          = "Pull from Server"
ACT_DELETE_LOCAL  = "Delete Local"
ACT_DELETE_SERVER = "Delete Server"
ACT_SKIP          = "Skip"
ACT_PLACEHOLDER   = "-- choose --"


def overwrite_suffix() -> str:
    """'YYYY-MM-DD-initials' suffix from current date + user.
    richard.kerr -> 2026-06-08-rk, johndoe -> 2026-06-08-jo."""
    today = datetime.now().strftime("%Y-%m-%d")
    user = getpass.getuser()
    if "." in user:
        initials = "".join(p[0] for p in user.split(".") if p).lower()
    else:
        initials = user[:2].lower()
    return f"{today}-{initials}"


def preserve_rename(rel_path: str) -> str:
    """project.prproj -> project_2026-06-08-rk.prproj (keeps directory prefix)."""
    p = Path(rel_path)
    return str(p.with_name(f"{p.stem}_{overwrite_suffix()}{p.suffix}"))


def _server_is_url(server_root: str) -> bool:
    return is_gdrive_url(server_root)


def _dest_exists_local(local_root: Path, rel_path: str) -> bool:
    return (local_root / rel_path).exists()


def _dest_exists_remote(server_root: str, rel_path: str) -> bool:
    if not _server_is_url(server_root):
        return (Path(server_root) / rel_path).exists()
    server_base, flags = gdrive_url_to_rclone(server_root)
    return rclone_bridge.path_exists(f"{server_base}{rel_path}", extra_flags=flags)


def _local_copy_verify(src: Path, dst: Path, log_cb=None) -> bool:
    """Local-to-local copy with SHA-256 verification."""
    try:
        pre  = compute_all(src, include_xxhash=False, include_md5=False)
        shutil.copy2(src, dst)
        post = compute_all(dst, include_xxhash=False, include_md5=False)
        if pre.get("sha256") != post.get("sha256"):
            if log_cb: log_cb(f"  Checksum mismatch after copy: {src.name}", "error")
            return False
        return True
    except Exception as e:
        if log_cb: log_cb(f"  Copy error: {e}", "error")
        return False


def push_file(rel_path, local_root: Path, server_root: str,
              preserve_on_overwrite: bool, log_cb=None) -> bool:
    """Push a single local file to server (with optional preserve-on-overwrite rename)."""
    src = local_root / rel_path
    if not src.exists():
        if log_cb: log_cb(f"  Push skipped (source missing): {rel_path}", "warning")
        return False

    dest_rel = rel_path
    if preserve_on_overwrite and _dest_exists_remote(server_root, rel_path):
        dest_rel = preserve_rename(rel_path)
        if log_cb: log_cb(f"  Preserve mode: uploading as {Path(dest_rel).name}", "info")

    if _server_is_url(server_root):
        server_base, flags = gdrive_url_to_rclone(server_root)
        ok = rclone_bridge.copyto(str(src), f"{server_base}{dest_rel}",
                                  dst_flags=flags, log_cb=log_cb)
    else:
        dst_path = Path(server_root) / dest_rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        ok = _local_copy_verify(src, dst_path, log_cb)

    if ok and log_cb:
        log_cb(f"  Pushed: {rel_path}"
               + (f" -> {dest_rel}" if dest_rel != rel_path else ""), "success")
    elif not ok and log_cb:
        log_cb(f"  Push failed: {rel_path}", "error")
    return ok


def pull_file(rel_path, local_root: Path, server_root: str,
              preserve_on_overwrite: bool, log_cb=None) -> bool:
    """Pull a single file from server to local (with optional preserve-on-overwrite rename)."""
    dest_rel = rel_path
    if preserve_on_overwrite and _dest_exists_local(local_root, rel_path):
        dest_rel = preserve_rename(rel_path)
        if log_cb: log_cb(f"  Preserve mode: downloading as {Path(dest_rel).name}", "info")

    dst = local_root / dest_rel
    dst.parent.mkdir(parents=True, exist_ok=True)

    if _server_is_url(server_root):
        server_base, flags = gdrive_url_to_rclone(server_root)
        ok = rclone_bridge.copyto(f"{server_base}{rel_path}", str(dst),
                                  src_flags=flags, log_cb=log_cb)
    else:
        src_path = Path(server_root) / rel_path
        if not src_path.exists():
            if log_cb: log_cb(f"  Pull failed (server source missing): {rel_path}", "error")
            return False
        ok = _local_copy_verify(src_path, dst, log_cb)

    if ok and log_cb:
        log_cb(f"  Pulled: {rel_path}"
               + (f" -> {dest_rel}" if dest_rel != rel_path else ""), "success")
    elif not ok and log_cb:
        log_cb(f"  Pull failed: {rel_path}", "error")
    return ok


def delete_local(rel_path, local_root: Path, log_cb=None) -> bool:
    target = local_root / rel_path
    if not target.exists():
        if log_cb: log_cb(f"  Delete-local skipped (already gone): {rel_path}", "warning")
        return True
    try:
        target.unlink()
        if log_cb: log_cb(f"  Deleted local: {rel_path}", "warning")
        return True
    except Exception as e:
        if log_cb: log_cb(f"  Delete-local failed: {rel_path}: {e}", "error")
        return False


def delete_server(rel_path, server_root: str, log_cb=None) -> bool:
    if _server_is_url(server_root):
        server_base, flags = gdrive_url_to_rclone(server_root)
        ok = rclone_bridge.deletefile(f"{server_base}{rel_path}",
                                      extra_flags=flags, log_cb=log_cb)
        if ok and log_cb: log_cb(f"  Deleted server: {rel_path}", "warning")
        return ok
    target = Path(server_root) / rel_path
    if not target.exists():
        if log_cb: log_cb(f"  Delete-server skipped (already gone): {rel_path}", "warning")
        return True
    try:
        target.unlink()
        if log_cb: log_cb(f"  Deleted server: {rel_path}", "warning")
        return True
    except Exception as e:
        if log_cb: log_cb(f"  Delete-server failed: {rel_path}: {e}", "error")
        return False
