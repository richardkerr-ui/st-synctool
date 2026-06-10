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
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

_log = logging.getLogger(__name__)


# Signal Theory's Google Cloud OAuth client.
# Project: ST SyncTool. User type: Internal. Application type: Desktop.
_DEFAULT_CLIENT_ID = "371659471908-8kbtrluohvvjo02olfaism9nn7m5eal2.apps.googleusercontent.com"
_DEFAULT_CLIENT_SECRET = "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"

_CONFIG_PATH = Path.home() / ".config" / "st_synctool" / "oauth.json"
_APP_CONFIG_PATH = Path.home() / ".config" / "st_synctool" / "config.json"


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
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("Could not read OAuth config from %s: %s", _CONFIG_PATH, e)

    return _DEFAULT_CLIENT_ID, _DEFAULT_CLIENT_SECRET


def save_oauth_credentials(client_id: str, client_secret: str) -> None:
    """Persist override credentials to ~/.config/st_synctool/oauth.json."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps({
        "client_id": client_id,
        "client_secret": client_secret,
    }, indent=2))
    _CONFIG_PATH.chmod(0o600)


def list_drive_remotes() -> List[str]:
    """Return names of all rclone remotes with type = drive."""
    try:
        result = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    remotes = [r.rstrip(":") for r in result.stdout.splitlines() if r.strip()]
    drive_remotes = []
    for remote in remotes:
        try:
            show = subprocess.run(
                ["rclone", "config", "show", remote],
                capture_output=True, text=True, timeout=5,
            )
            if "type = drive" in show.stdout:
                drive_remotes.append(remote)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return drive_remotes


def get_remote_account_email(remote_name: str) -> Optional[str]:
    """
    Return the Google account email for a remote by calling Google's userinfo
    endpoint with the stored access token. Returns None if unavailable.
    """
    try:
        show = subprocess.run(
            ["rclone", "config", "show", remote_name],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    token_json = None
    for line in show.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("token"):
            _, _, val = stripped.partition("=")
            token_json = val.strip()
            break

    if not token_json:
        return None

    try:
        access_token = json.loads(token_json).get("access_token", "")
    except (json.JSONDecodeError, AttributeError):
        return None

    if not access_token:
        return None

    try:
        import requests
        resp = requests.get(
            "https://www.googleapis.com/oauth2/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("email")
    except Exception:
        pass

    return None


def get_active_remote() -> str:
    """Return the active rclone remote name (env var > saved config > 'gdrive')."""
    env_remote = os.environ.get("ST_SYNC_RCLONE_REMOTE", "").strip().rstrip(":")
    if env_remote:
        return env_remote
    if _APP_CONFIG_PATH.exists():
        try:
            data = json.loads(_APP_CONFIG_PATH.read_text())
            remote = (data.get("active_remote") or "").strip()
            if remote:
                return remote
        except (json.JSONDecodeError, OSError):
            pass
    return "gdrive"


def save_active_remote(remote_name: str) -> None:
    """Persist the active remote name to ~/.config/st_synctool/config.json."""
    _APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if _APP_CONFIG_PATH.exists():
        try:
            existing = json.loads(_APP_CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing["active_remote"] = remote_name
    _APP_CONFIG_PATH.write_text(json.dumps(existing, indent=2))


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
