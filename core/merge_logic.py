"""Pure merge orchestration helpers — no Qt dependency."""

from pathlib import Path

from core.manifest import generate_manifest_fast
from core import rclone_bridge
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone


def build_server_manifest(server_path: str, base_manifest=None, log_cb=None, progress_cb=None):
    """Return a manifest for the server side, routing through rclone for GDrive URLs
    or generate_manifest_fast for local paths."""
    if is_gdrive_url(server_path):
        if log_cb:
            log_cb("Server is Google Drive — fetching via rclone lsjson...", "info")
        remote, flags = gdrive_url_to_rclone(server_path)
        return rclone_bridge.lsjson_to_manifest(remote, extra_flags=flags, label="server")
    p = Path(server_path)
    if not p.exists():
        raise RuntimeError(f"Server path does not exist: {server_path}")
    if log_cb:
        log_cb(f"Scanning local server path: {p}", "info")
    return generate_manifest_fast(p, base_manifest=base_manifest, label="server",
                                  progress_cb=progress_cb)
