"""Startup preflight checks for ST SyncTool.

Call run_preflight() at the very top of main.py, before any GUI/imports
that touch rclone. Eliminates the two most common setup failures:
  1. rclone too old -> deprecated OAuth flow -> invalid_client
  2. manual `rclone config` walkthrough (scope footgun)
"""

import re
import subprocess
import sys

from utils.resources import find_binary

MIN_RCLONE = (1, 60, 0)


def _rclone() -> str:
    """Resolve rclone: the copy bundled in the frozen .app, else PATH."""
    return find_binary("rclone") or "rclone"


def check_rclone() -> None:
    try:
        out = subprocess.run(
            [_rclone(), "version"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        sys.exit("rclone not found. Install with: brew install rclone")
    m = re.search(r"rclone v(\d+)\.(\d+)\.(\d+)", out)
    if m and tuple(map(int, m.groups())) < MIN_RCLONE:
        sys.exit(
            "rclone is too old and OAuth will fail (invalid_client). "
            "Run: brew upgrade rclone"
        )


def check_rclone_pinned_version() -> None:
    """M15.2: enforce the pinned rclone version floor. rclone flag/hash semantics
    drift between releases, so a too-old binary is refused rather than silently
    trusted. The pin lives in core.rclone_bridge.RCLONE_REQUIRED_VERSION."""
    from core import rclone_bridge
    ver = rclone_bridge.rclone_version()
    if ver and not rclone_bridge.meets_required_version(ver):
        sys.exit(
            f"rclone {ver} is older than the pinned "
            f"{rclone_bridge.RCLONE_REQUIRED_VERSION}; flag and backend-hash "
            f"semantics may differ and verification cannot be trusted. "
            f"Run: brew upgrade rclone"
        )


def ensure_remote(remote: str = "gdrive") -> None:
    from core.oauth_config import get_oauth_credentials

    existing = subprocess.run(
        [_rclone(), "listremotes"], capture_output=True, text=True,
    ).stdout
    if f"{remote}:" in existing:
        return
    cid, csec = get_oauth_credentials()
    subprocess.run(
        [
            _rclone(), "config", "create", remote, "drive",
            "client_id", cid,
            "client_secret", csec,
            "scope", "drive",
        ],
        check=True,
    )


def run_preflight(remote: str = "gdrive") -> None:
    check_rclone()
    check_rclone_pinned_version()
    ensure_remote(remote)
