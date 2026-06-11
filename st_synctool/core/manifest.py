import json, os, socket, getpass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable
from core.checksum import compute_all

MANIFEST_FILENAME = "st_manifest.json"
LOCAL_MANIFEST_DIR = Path.home() / "Documents" / "STSyncTool" / "manifests"

def generate_manifest(folder: Path, label="source", dest_path=None,
                      gdrive=False, progress_cb=None) -> dict:
    files_list = [p for p in folder.rglob("*") if p.is_file()]
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
