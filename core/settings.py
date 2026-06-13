"""Application settings store (the keystone for the M9 activity-log cluster).

A single JSON config at ``~/.config/st_synctool/config.json`` already holds the
active rclone remote (written by ``core.oauth_config``). This module is the
general typed accessor over that same file: tolerant load, atomic merge-write
and per-key env overrides, plus typed convenience accessors for the settings the
org-wide activity log needs (the shared Drive remote base and the log-shipping
opt-out toggle).

Pure logic, no PyQt6. The path is injectable so it is fully testable. Writes are
atomic (tmp + rename) and always merge, so they never clobber keys owned by
other writers (e.g. ``active_remote``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

SETTINGS_PATH = Path.home() / ".config" / "st_synctool" / "config.json"

# Known setting keys and their defaults. Unknown keys are preserved on write but
# are not part of the typed surface.
DEFAULTS: dict = {
    "active_remote": "gdrive",
    # M9.1/M9.2/M9.3: rclone path the activity log + manifests ship to and the
    # org History view reads from, e.g. "gdrive:ST_SyncTool_Activity". Empty
    # means org activity is not configured yet and shipping is a no-op.
    "activity_remote_base": "",
    # M9.1: opt-out toggle for log shipping. Shipping is on by default once a
    # remote base is set.
    "log_shipping_enabled": True,
}

# Per-key environment overrides (read-only; they win over the file).
_ENV_OVERRIDES: dict = {
    "active_remote": "ST_SYNC_RCLONE_REMOTE",
    "activity_remote_base": "ST_SYNC_ACTIVITY_REMOTE",
}


def load_settings(path=None) -> dict:
    """Return the merged settings dict (defaults <- file), tolerant of corruption."""
    merged = dict(DEFAULTS)
    p = Path(path) if path is not None else SETTINGS_PATH
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                merged.update(data)
        except (json.JSONDecodeError, OSError):
            pass
    return merged


def get_setting(key: str, default: Any = None, *, path=None) -> Any:
    """Get one setting. An env override (if defined for the key) wins over the file."""
    env_var = _ENV_OVERRIDES.get(key)
    if env_var:
        env_val = os.environ.get(env_var, "").strip()
        if env_val:
            return env_val.rstrip(":") if key == "active_remote" else env_val
    settings = load_settings(path)
    if key in settings:
        return settings[key]
    return default if default is not None else DEFAULTS.get(key)


def set_setting(key: str, value: Any, *, path=None) -> dict:
    """Persist one setting, merging into the existing file (atomic). Returns the merged dict."""
    p = Path(path) if path is not None else SETTINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text())
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            pass
    existing[key] = value
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2))
    tmp.replace(p)
    return existing


# --------------------------------------------------------------------------- #
# Typed convenience accessors for the activity-log cluster
# --------------------------------------------------------------------------- #

def activity_remote_base(*, path=None) -> str:
    """The configured shared Drive remote base, or '' if unset. Trailing ':' kept."""
    return (get_setting("activity_remote_base", "", path=path) or "").strip()


def set_activity_remote_base(value: str, *, path=None) -> dict:
    return set_setting("activity_remote_base", (value or "").strip(), path=path)


def log_shipping_enabled(*, path=None) -> bool:
    """Whether log shipping is on. Defaults True, but is only meaningful once a
    remote base is configured (an empty base makes shipping a no-op regardless)."""
    return bool(get_setting("log_shipping_enabled", True, path=path))


def set_log_shipping_enabled(enabled: bool, *, path=None) -> dict:
    return set_setting("log_shipping_enabled", bool(enabled), path=path)


def activity_log_configured(*, path=None) -> bool:
    """True when shipping should actually run: a remote base is set and not opted out."""
    return bool(activity_remote_base(path=path)) and log_shipping_enabled(path=path)
