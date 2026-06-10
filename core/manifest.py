import hashlib, json, socket, getpass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable
from core.checksum import compute_all

MANIFEST_FILENAME = "st_manifest.json"
LOCAL_MANIFEST_DIR = Path.home() / "Documents" / "STSyncTool" / "manifests"
SCHEMA_VERSION = "1.1"

# Fields backfilled when loading a manifest older than SCHEMA_VERSION.
_TOP_LEVEL_DEFAULTS = {
    "project_id": "",
    "renames": [],
    "checksum_context": {},
    "server_path": "",
    "operation": "",
    "filename_normalization": {"applied": False},
}
_FILE_ENTRY_DEFAULTS = {
    "gdrive_url": "",
}


def _project_id(local_path: str, server_path: str) -> str:
    """Stable 12-char hex ID derived from the (local_path, server_path) pair."""
    if not local_path:
        return ""
    key = f"{local_path}|{server_path or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]

def generate_manifest(folder: Path, label="source", dest_path=None,
                      gdrive=False, progress_cb=None,
                      server_path="", operation="") -> dict:
    files_list = [p for p in Path(folder).rglob("*") if p.is_file()]
    total = len(files_list)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "root": str(folder),  # display label only — use server_path for the server side
        "destination": dest_path or "",
        "server_path": server_path,
        "operation": operation,
        "project_id": _project_id(str(folder), server_path),
        "workstation": socket.gethostname(), "user": getpass.getuser(),
        "file_count": total,
        "renames": [],
        "checksum_context": {
            "algorithm": "md5" if gdrive else "sha256",
            "gdrive_mode": gdrive,
        },
        "files": {},
    }
    for i, path in enumerate(files_list):
        if progress_cb: progress_cb(int((i / total) * 100), path.name)
        rel = path.relative_to(folder).as_posix()
        stat = path.stat()
        hashes = compute_all(path, include_xxhash=not gdrive, include_md5=gdrive)
        manifest["files"][rel] = {
            "type": "file", "size": stat.st_size,
            "modtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "checksums": hashes,
            "gdrive_url": "",
        }
    manifest["total_size_bytes"] = sum(e["size"] for e in manifest["files"].values())
    return manifest

def save_manifest(manifest: dict, source_dir=None, dest_dir=None,
                  name_hint="", operation="") -> list:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = ["st_manifest"]
    if name_hint: parts.append(name_hint)
    op = operation or manifest.get("operation", "")
    if op: parts.append(op)
    parts.append(ts)
    fname = "_".join(parts) + ".json"

    # Per-project subdirectory under the central archive dir
    archive_dir = LOCAL_MANIFEST_DIR / name_hint if name_hint else LOCAL_MANIFEST_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    targets = [archive_dir / fname]
    if source_dir: targets.append(Path(source_dir) / MANIFEST_FILENAME)
    if dest_dir:   targets.append(Path(dest_dir)   / MANIFEST_FILENAME)
    for p in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest, indent=2))
        saved.append(p)
    return saved

def load_manifest(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    _migrate(data)
    return data


def _migrate(manifest: dict) -> None:
    """Backfill fields missing from pre-1.1 manifests, in-place."""
    version = manifest.get("schema_version", "1.0")
    if version >= SCHEMA_VERSION:
        return
    for key, default in _TOP_LEVEL_DEFAULTS.items():
        manifest.setdefault(key, default)
    for entry in manifest.get("files", {}).values():
        for key, default in _FILE_ENTRY_DEFAULTS.items():
            entry.setdefault(key, default)
    manifest["schema_version"] = SCHEMA_VERSION


def generate_manifest_fast(folder: Path, base_manifest=None, label="source",
                           dest_path=None, gdrive=False, progress_cb=None,
                           server_path="", operation="") -> dict:
    """Like generate_manifest, but uses modtime+size as a pre-filter against
    a base manifest. Files whose stat matches the base entry exactly are
    assumed unchanged and reuse the base hash. Massive speedup for big projects
    where most files don't change between syncs."""
    base_files = (base_manifest or {}).get("files", {})
    files_list = [pp for pp in Path(folder).rglob("*") if pp.is_file()]
    total = max(len(files_list), 1)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "root": str(folder),  # display label only — use server_path for the server side
        "destination": dest_path or "",
        "server_path": server_path,
        "operation": operation,
        "project_id": _project_id(str(folder), server_path),
        "workstation": socket.gethostname(), "user": getpass.getuser(),
        "file_count": len(files_list),
        "renames": [],
        "checksum_context": {
            "algorithm": "md5" if gdrive else "sha256",
            "gdrive_mode": gdrive,
        },
        "files": {},
    }

    reused = 0
    rehashed = 0

    for i, path in enumerate(files_list):
        if progress_cb:
            progress_cb(int((i / total) * 100), path.name)
        rel = path.relative_to(folder).as_posix()
        stat = path.stat()
        size = stat.st_size
        modtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        base_entry = base_files.get(rel)
        if (base_entry
                and base_entry.get("size") == size
                and base_entry.get("modtime") == modtime
                and base_entry.get("checksums")):
            hashes = base_entry["checksums"]
            reused += 1
        else:
            hashes = compute_all(path, include_xxhash=not gdrive, include_md5=gdrive)
            rehashed += 1

        manifest["files"][rel] = {
            "type": "file", "size": size,
            "modtime": modtime, "checksums": hashes,
            "gdrive_url": "",
        }

    manifest["total_size_bytes"] = sum(e["size"] for e in manifest["files"].values())
    manifest["scan_stats"] = {"reused_from_base": reused, "rehashed": rehashed}
    return manifest
