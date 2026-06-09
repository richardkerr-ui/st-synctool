"""
Google OAuth client credentials for ST SyncTool.

For desktop apps, the client_secret is not truly secret. Google's docs
explicitly allow distributing them with installed apps:
https://developers.google.com/identity/protocols/oauth2#installed

The credential is bundled with the app so users get fast transfers out
of the box. Can still be overridden per-user if needed.

Priority:
  1. ST_SYNC_GDRIVE_CLIENT_ID + ST_SYNC_GDRIVE_CLIENT_SECRET env vars
  2. ~/.config/st_synctool/oauth.json
  3. Bundled Signal Theory defaults
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple


# Signal Theory's Google Cloud OAuth client.
# Project: ST SyncTool. User type: Internal. Application type: Desktop.
_DEFAULT_CLIENT_ID = "371659471908-8kbtrluohvvjo02olfaism9nn7m5eal2.apps.googleusercontent.com"
_DEFAULT_CLIENT_SECRET = "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"

_CONFIG_PATH = Path.home() / ".config" / "st_synctool" / "oauth.json"


def get_oauth_credentials() -> Tuple[str, str]:
    """Return (client_id, client_secret) following the priority order above."""
    env_id = os.environ.get("ST_SYNC_GDRIVE_CLIENT_ID", "").strip()
    env_secret = os.environ.get("ST_SYNC_GDRIVE_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        return env_id, env_secret

    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            cid = (data.get("client_id") or "").strip()
            csec = (data.get("client_secret") or "").strip()
            if cid and csec:
                return cid, csec
        except (json.JSONDecodeError, OSError):
            pass

    return _DEFAULT_CLIENT_ID, _DEFAULT_CLIENT_SECRET


def save_oauth_credentials(client_id: str, client_secret: str) -> None:
    """Persist override credentials to ~/.config/st_synctool/oauth.json."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps({
        "client_id": client_id,
        "client_secret": client_secret,
    }, indent=2))
    _CONFIG_PATH.chmod(0o600)


def is_remote_using_default_rclone_creds(remote_name: str) -> Optional[bool]:
    """
    Detect whether an existing rclone remote was configured with rclone's
    shared (slow, throttled) OAuth client instead of a custom one.

    Returns:
        True  - using rclone defaults; transfer speeds will be throttled
        False - using custom credentials (ours or another team's)
        None  - couldn't determine (no remote, rclone failed, etc.)
    """
    try:
        result = subprocess.run(
            ["rclone", "config", "show", remote_name],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("client_id"):
            _, _, val = stripped.partition("=")
            if val.strip():
                return False
    return True
