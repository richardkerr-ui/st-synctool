"""Startup preflight checks for ST SyncTool.

Call run_preflight() at the very top of main.py, before any GUI/imports
that touch rclone. Eliminates the two most common setup failures:
  1. rclone too old -> deprecated OAuth flow -> invalid_client
  2. manual `rclone config` walkthrough (scope footgun)
"""

import re
import subprocess
import sys

MIN_RCLONE = (1, 60, 0)


def check_rclone() -> None:
    try:
        out = subprocess.run(
            ["rclone", "version"],
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


def ensure_remote(remote: str = "gdrive") -> None:
    from utils.gdrive_oauth import get_oauth_credentials

    existing = subprocess.run(
        ["rclone", "listremotes"], capture_output=True, text=True,
    ).stdout
    if f"{remote}:" in existing:
        return
    cid, csec = get_oauth_credentials()
    subprocess.run(
        [
            "rclone", "config", "create", remote, "drive",
            "client_id", cid,
            "client_secret", csec,
            "scope", "drive",
        ],
        check=True,
    )


def run_preflight(remote: str = "gdrive") -> None:
    check_rclone()
    ensure_remote(remote)
