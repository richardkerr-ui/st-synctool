"""Projects registry — persistent CRUD for known sync projects.

Registry lives at ~/Documents/STSyncTool/projects.json.
Each project is keyed by its project_id (stable hash of local+server paths).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import paths as _paths
PROJECTS_REGISTRY = _paths.projects_registry()


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


def _locked_update(mutate):
    """M14.3: atomic read-modify-write of the registry under a sidecar lock, so
    two concurrent offloads recording fingerprints/merges never lose each other's
    entries. Same-host-multiprocess only — the registry must stay on the local
    STSyncTool tree (flock is advisory on NAS). ``mutate(data) -> data``."""
    from core.file_lock import locked_json_update
    return locked_json_update(PROJECTS_REGISTRY, mutate, default={})


def list_projects() -> list:
    """All registered projects, sorted by display_name."""
    projects = []
    for p in _load().values():
        if not isinstance(p, dict) or "project_id" not in p:
            continue
        if "display_name" not in p:
            p = dict(p, display_name=Path(p.get("local_path", "")).name or p["project_id"])
        projects.append(p)
    return sorted(projects, key=lambda p: p["display_name"].lower())


def get_project(project_id: str) -> Optional[dict]:
    return _load().get(project_id)


def upsert_project(project_id: str, local_path: str, server_path: str,
                   display_name: str = "", latest_manifest: str = "") -> dict:
    """Create or update a project entry. Returns the saved entry."""
    if not project_id:
        return {}
    now = datetime.now(timezone.utc).isoformat()
    holder = {}

    def _mut(data):
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
        holder["entry"] = entry
        return data

    _locked_update(_mut)
    return holder.get("entry", {})


def record_merge(project_id: str, files_changed: int, conflicts: int,
                 preserve_renames: int, manifest_path: str = "") -> None:
    """Append a merge session to the project's history log and update last_merged_at."""
    now = datetime.now(timezone.utc).isoformat()

    def _mut(data):
        if project_id not in data:
            return data
        entry = data[project_id]
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
        return data

    _locked_update(_mut)


def find_by_local_path(local_path: str) -> Optional[dict]:
    """Return the first project whose local_path matches exactly."""
    for p in _load().values():
        # projects.json mixes project entries with namespaced keys whose values
        # are dicts or lists (offload_ledger, app_settings, ...). Skip anything
        # that is not a project entry, mirroring list_projects().
        if not isinstance(p, dict) or "project_id" not in p:
            continue
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


def list_dest_presets() -> list:
    """Return sorted list of preset names."""
    return sorted(_load_presets().keys())


def get_dest_preset(name: str) -> list:
    """Return list of destination dicts for the named preset, or []."""
    return _load_presets().get(name, [])


def save_dest_preset(name: str, dests: list) -> None:
    """Create or overwrite a named destination preset."""
    def _mut(data):
        data.setdefault("offload_dest_presets", {})[name] = dests
        return data
    _locked_update(_mut)


def delete_dest_preset(name: str) -> None:
    """Remove a named destination preset if it exists."""
    def _mut(data):
        data.setdefault("offload_dest_presets", {}).pop(name, None)
        return data
    _locked_update(_mut)


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
    def _mut(data):
        data.setdefault("naming_preferences", {})[pattern] = choice
        return data
    _locked_update(_mut)


# ---------------------------------------------------------------------------
# Generic app settings (flat key/value store)
# Stored in projects.json under top-level key "app_settings".
# ---------------------------------------------------------------------------

def get_app_setting(key: str, default=None):
    """Return a stored app setting, or default if not set."""
    return _load().get("app_settings", {}).get(key, default)


def save_app_setting(key: str, value) -> None:
    """Persist an app setting."""
    def _mut(data):
        data.setdefault("app_settings", {})[key] = value
        return data
    _locked_update(_mut)


# ---------------------------------------------------------------------------
# M12.2 offload fingerprint ledger — records each completed offload so the
# next one can warn about a duplicate card. Stored under "offload_ledger".
# ---------------------------------------------------------------------------

_LEDGER_MAX = 500   # cap so the registry never grows without bound


def list_offload_fingerprints() -> list:
    """Return all recorded offload fingerprints (newest last)."""
    return _load().get("offload_ledger", [])


def record_offload_fingerprint(record: dict) -> None:
    """Append one offload fingerprint record, trimming to the most recent.

    M14.3: routed through the sidecar lock so two concurrent offloads recording
    fingerprints to the shared registry never drop each other's entries."""
    def _mut(data):
        ledger = data.setdefault("offload_ledger", [])
        ledger.append(record)
        if len(ledger) > _LEDGER_MAX:
            del ledger[: len(ledger) - _LEDGER_MAX]
        return data
    _locked_update(_mut)
