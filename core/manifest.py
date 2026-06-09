import json, socket, getpass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable
from core.checksum import compute_all

MANIFEST_FILENAME = "st_manifest.json"
LOCAL_MANIFEST_DIR = Path.home() / "Documents" / "STSyncTool" / "manifests"

def generate_manifest(folder: Path, label="source", dest_path=None,
                      gdrive=False, progress_cb=None) -> dict:
    files_list = [p for p in Path(folder).rglob("*") if p.is_file()]
    total = len(files_list)
    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label, "root": str(folder),
        "destination": dest_path or "",
        "workstation": socket.gethostname(), "user": getpass.getuser(),
        "file_count": total, "files": {},
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
        }
    manifest["total_size_bytes"] = sum(e["size"] for e in manifest["files"].values())
    return manifest

def save_manifest(manifest: dict, source_dir=None, dest_dir=None, name_hint="") -> list:
    LOCAL_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"st_manifest_{name_hint}_{ts}.json" if name_hint else f"st_manifest_{ts}.json"
    saved = []
    targets = [LOCAL_MANIFEST_DIR / fname]
    if source_dir: targets.append(Path(source_dir) / MANIFEST_FILENAME)
    if dest_dir:   targets.append(Path(dest_dir)   / MANIFEST_FILENAME)
    for p in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest, indent=2))
        saved.append(p)
    return saved

def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def generate_manifest_fast(folder: Path, base_manifest=None, label="source",
                           dest_path=None, gdrive=False, progress_cb=None) -> dict:
    """Like generate_manifest, but uses modtime+size as a pre-filter against
    a base manifest. Files whose stat matches the base entry exactly are
    assumed unchanged and reuse the base hash. Massive speedup for big projects
    where most files don't change between syncs."""
    base_files = (base_manifest or {}).get("files", {})
    files_list = [pp for pp in Path(folder).rglob("*") if pp.is_file()]
    total = max(len(files_list), 1)
    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label, "root": str(folder),
        "destination": dest_path or "",
        "workstation": socket.gethostname(), "user": getpass.getuser(),
        "file_count": len(files_list), "files": {},
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
        }

    manifest["total_size_bytes"] = sum(e["size"] for e in manifest["files"].values())
    manifest["scan_stats"] = {"reused_from_base": reused, "rehashed": rehashed}
    return manifest
