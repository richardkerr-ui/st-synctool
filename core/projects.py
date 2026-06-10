"""Projects registry — persistent CRUD for known sync projects.

Registry lives at ~/Documents/STSyncTool/projects.json.
Each project is keyed by its project_id (stable hash of local+server paths).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECTS_REGISTRY = Path.home() / "Documents" / "STSyncTool" / "projects.json"


def _load() -> dict:
    if PROJECTS_REGISTRY.exists():
        try:
            return json.loads(PROJECTS_REGISTRY.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    PROJECTS_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    PROJECTS_REGISTRY.write_text(json.dumps(data, indent=2))


def list_projects() -> list:
    """All registered projects, sorted by display_name."""
    return sorted(_load().values(), key=lambda p: p.get("display_name", "").lower())


def get_project(project_id: str) -> Optional[dict]:
    return _load().get(project_id)


def upsert_project(project_id: str, local_path: str, server_path: str,
                   display_name: str = "", latest_manifest: str = "") -> dict:
    """Create or update a project entry. Returns the saved entry."""
    if not project_id:
        return {}
    data = _load()
    now = datetime.now(timezone.utc).isoformat()
    existing = data.get(project_id, {})
    entry = {
        "project_id":     project_id,
        "display_name":   display_name or existing.get("display_name") or Path(local_path).name,
        "local_path":     local_path,
        "server_path":    server_path,
        "created_at":     existing.get("created_at", now),
        "last_merged_at": existing.get("last_merged_at", ""),
        "latest_manifest": latest_manifest or existing.get("latest_manifest", ""),
        "history":        existing.get("history", []),
    }
    data[project_id] = entry
    _save(data)
    return entry


def record_merge(project_id: str, files_changed: int, conflicts: int,
                 preserve_renames: int, manifest_path: str = "") -> None:
    """Append a merge session to the project's history log and update last_merged_at."""
    data = _load()
    if project_id not in data:
        return
    entry = data[project_id]
    now = datetime.now(timezone.utc).isoformat()
    entry["last_merged_at"] = now
    if manifest_path:
        entry["latest_manifest"] = manifest_path
    entry.setdefault("history", []).append({
        "merged_at":       now,
        "files_changed":   files_changed,
        "conflicts":       conflicts,
        "preserve_renames": preserve_renames,
        "manifest_path":   manifest_path,
    })
    _save(data)


def find_by_local_path(local_path: str) -> Optional[dict]:
    """Return the first project whose local_path matches exactly."""
    for p in _load().values():
        if p.get("local_path") == local_path:
            return p
    return None


# ---------------------------------------------------------------------------
# Destination presets (Phase 5, item 27)
# Stored in projects.json under top-level key "offload_dest_presets".
# ---------------------------------------------------------------------------

def _load_presets() -> dict:
    raw = _load()
    return raw.get("offload_dest_presets", {})


def _save_presets(presets: dict) -> None:
    data = _load()
    data["offload_dest_presets"] = presets
    _save(data)


def list_dest_presets() -> list:
    """Return sorted list of preset names."""
    return sorted(_load_presets().keys())


def get_dest_preset(name: str) -> list:
    """Return list of destination dicts for the named preset, or []."""
    return _load_presets().get(name, [])


def save_dest_preset(name: str, dests: list) -> None:
    """Create or overwrite a named destination preset."""
    presets = _load_presets()
    presets[name] = dests
    _save_presets(presets)


def delete_dest_preset(name: str) -> None:
    """Remove a named destination preset if it exists."""
    presets = _load_presets()
    presets.pop(name, None)
    _save_presets(presets)


# ---------------------------------------------------------------------------
# Filename normalisation preference memory (Phase 7, item 56)
# Stored in projects.json under top-level key "naming_preferences".
# Values: "normalize" | "skip" | "ask"
# ---------------------------------------------------------------------------

def get_naming_preference(pattern: str) -> Optional[str]:
    """Return stored preference for a camera naming pattern, or None if not set."""
    return _load().get("naming_preferences", {}).get(pattern)


def save_naming_preference(pattern: str, choice: str) -> None:
    """Persist user's normalisation choice for a naming pattern."""
    data = _load()
    data.setdefault("naming_preferences", {})[pattern] = choice
    _save(data)


# ---------------------------------------------------------------------------
# Generic app settings (flat key/value store)
# Stored in projects.json under top-level key "app_settings".
# ---------------------------------------------------------------------------

def get_app_setting(key: str, default=None):
    """Return a stored app setting, or default if not set."""
    return _load().get("app_settings", {}).get(key, default)


def save_app_setting(key: str, value) -> None:
    """Persist an app setting."""
    data = _load()
    data.setdefault("app_settings", {})[key] = value
    _save(data)
