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
