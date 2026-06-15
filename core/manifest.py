import hashlib, json, socket, getpass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable
from core.checksum import compute_all
from core.comparison import is_ignored_path

from core import paths as _paths
MANIFEST_FILENAME = "st_manifest.json"
LOCAL_MANIFEST_DIR = _paths.manifests_dir()
SCHEMA_VERSION = "1.2"

# Fields backfilled when loading a manifest older than SCHEMA_VERSION.
_TOP_LEVEL_DEFAULTS = {
    "project_id": "",
    "renames": [],
    "checksum_context": {},
    "counterpart_path": "",
    "operation": "",
    "filename_normalization": {"applied": False},
}
_FILE_ENTRY_DEFAULTS = {
    "gdrive_url": "",
}


# OVERNIGHT-FIX: each manifest entry must record which algorithm produced its
# primary hash (spec: hash_algorithm per entry, not just the hash value).
def _primary_algorithm(gdrive: bool) -> str:
    return "md5" if gdrive else "sha256"


def _project_id(local_path: str, counterpart_path: str) -> str:
    """Stable 12-char hex ID derived from the (local_path, counterpart_path) pair.

    Convention: local_path is always the on-disk root; counterpart_path is the
    remote/Drive path. Callers must not reverse the order — a reversed call
    produces a different (silently valid) ID and a duplicate project entry.
    """
    if not local_path:
        return ""
    key = f"{local_path}|{counterpart_path or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]

def generate_manifest(folder: Path, label="source", dest_path=None,
                      gdrive=False, progress_cb=None,
                      counterpart_path="", operation="") -> dict:
    # KNOWN-ISSUE-FIX: skip ignored paths (OS junk like .DS_Store, our own
    # st_manifest.json, staging/failure/thumbnail artifacts) so they never enter
    # a generated manifest. The merge diff already ignores these; generation did
    # not, which let a phantom .DS_Store into a post-merge manifest and produced
    # a spurious MISSING on Verify. Uses the unified ignore list from comparison.
    files_list = [p for p in Path(folder).rglob("*")
                  if p.is_file() and not is_ignored_path(p.relative_to(folder).as_posix())]
    total = len(files_list)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "root": str(folder),  # display label only
        "destination": dest_path or "",
        "counterpart_path": counterpart_path,
        "operation": operation,
        "project_id": _project_id(str(folder), counterpart_path),
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
            # OVERNIGHT-FIX: record which algorithm produced the primary hash
            "hash_algorithm": _primary_algorithm(gdrive),
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
    """Backfill fields missing from pre-1.2 manifests, in-place."""
    version = manifest.get("schema_version", "1.0")
    if version >= SCHEMA_VERSION:
        return
    # Schema 1.2: rename server_path -> counterpart_path. Copy old key when the
    # new key is absent so old on-disk manifests still display correctly.
    if "server_path" in manifest and "counterpart_path" not in manifest:
        manifest["counterpart_path"] = manifest["server_path"]
    for key, default in _TOP_LEVEL_DEFAULTS.items():
        manifest.setdefault(key, default)
    # OVERNIGHT-FIX: backfill hash_algorithm for pre-1.1 file entries. Prefer the
    # manifest-wide checksum_context.algorithm; otherwise infer from the checksum
    # keys that are actually present (sha256 > md5 > xxhash3_64).
    ctx_algo = (manifest.get("checksum_context") or {}).get("algorithm")
    for entry in manifest.get("files", {}).values():
        for key, default in _FILE_ENTRY_DEFAULTS.items():
            entry.setdefault(key, default)
        if "hash_algorithm" not in entry:
            cs = entry.get("checksums", {}) or {}
            if ctx_algo and ctx_algo in cs:
                entry["hash_algorithm"] = ctx_algo
            elif "sha256" in cs:
                entry["hash_algorithm"] = "sha256"
            elif "md5" in cs:
                entry["hash_algorithm"] = "md5"
            elif "xxhash3_64" in cs:
                entry["hash_algorithm"] = "xxhash3_64"
            else:
                entry["hash_algorithm"] = ctx_algo or "sha256"
    manifest["schema_version"] = SCHEMA_VERSION


# KNOWN-ISSUE-FIX: load_manifest backfills pre-1.2 manifests to the current
# schema in memory but never rewrites the file, so the archive keeps stale 1.0
# JSON on disk indefinitely. This is an OPT-IN sweep utility — it is never
# called automatically and the GUI does not invoke it. A human runs it (e.g.
# `python3 -c "from core.manifest import migrate_manifests_on_disk as m; m()"`)
# when they want on-disk consistency. Defaults to a dry run so nothing is
# touched unless the caller explicitly asks.
def needs_migration(path: Path) -> bool:
    """True if the JSON manifest at `path` is below SCHEMA_VERSION.

    A file that cannot be parsed as JSON returns False (it is not a manifest we
    can safely rewrite) and is left for the caller's error handling.
    """
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return str(data.get("schema_version", "1.0")) < SCHEMA_VERSION


def migrate_manifest_file(path: Path, backup: bool = True) -> bool:
    """Migrate a single on-disk manifest to SCHEMA_VERSION and rewrite it.

    Returns True if the file was rewritten, False if it was already current
    (or not a parseable manifest). When `backup` is True, the original bytes
    are preserved alongside as `<name>.json.bak` before the rewrite.
    """
    path = Path(path)
    try:
        raw = path.read_text()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return False
    if str(data.get("schema_version", "1.0")) >= SCHEMA_VERSION:
        return False
    _migrate(data)
    if backup:
        path.with_suffix(path.suffix + ".bak").write_text(raw)
    path.write_text(json.dumps(data, indent=2))
    return True


def migrate_manifests_on_disk(archive_dir: Optional[Path] = None,
                              dry_run: bool = True,
                              backup: bool = True) -> dict:
    """Sweep an archive directory and migrate every pre-1.2 manifest on disk.

    Opt-in and non-destructive by default:
      - `archive_dir` defaults to LOCAL_MANIFEST_DIR (recursed, so per-project
        subdirs are covered). Pass an explicit dir in tests.
      - `dry_run=True` (the default) only reports what *would* change; nothing
        is written.
      - `backup=True` keeps a `.json.bak` of each rewritten file.

    Returns {"scanned": int, "migrated": [str, ...], "skipped": int,
             "errors": [(str, str), ...], "dry_run": bool}.
    Only files named `st_manifest*.json` are considered; `.bak` files are
    ignored so re-running the sweep does not touch its own backups.
    """
    root = Path(archive_dir) if archive_dir is not None else LOCAL_MANIFEST_DIR
    report = {"scanned": 0, "migrated": [], "skipped": 0,
              "errors": [], "dry_run": dry_run}
    if not root.exists():
        return report
    for p in sorted(root.rglob("st_manifest*.json")):
        if p.suffix == ".bak" or p.name.endswith(".json.bak"):
            continue
        report["scanned"] += 1
        try:
            if not needs_migration(p):
                report["skipped"] += 1
                continue
            if dry_run:
                report["migrated"].append(str(p))
            elif migrate_manifest_file(p, backup=backup):
                report["migrated"].append(str(p))
            else:
                report["skipped"] += 1
        except Exception as exc:  # never let one bad file abort the sweep
            report["errors"].append((str(p), str(exc)))
    return report


def generate_manifest_fast(folder: Path, base_manifest=None, label="source",
                           dest_path=None, gdrive=False, progress_cb=None,
                           counterpart_path="", operation="") -> dict:
    """Like generate_manifest, but uses modtime+size as a pre-filter against
    a base manifest. Files whose stat matches the base entry exactly are
    assumed unchanged and reuse the base hash. Massive speedup for big projects
    where most files don't change between syncs."""
    base_files = (base_manifest or {}).get("files", {})
    # KNOWN-ISSUE-FIX: skip ignored paths (see generate_manifest) so junk never
    # enters a generated manifest. Unified ignore list from comparison.
    files_list = [pp for pp in Path(folder).rglob("*")
                  if pp.is_file() and not is_ignored_path(pp.relative_to(folder).as_posix())]
    total = max(len(files_list), 1)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "root": str(folder),  # display label only
        "destination": dest_path or "",
        "counterpart_path": counterpart_path,
        "operation": operation,
        "project_id": _project_id(str(folder), counterpart_path),
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
            # OVERNIGHT-FIX: record which algorithm produced the primary hash
            "hash_algorithm": _primary_algorithm(gdrive),
            "gdrive_url": "",
        }

    manifest["total_size_bytes"] = sum(e["size"] for e in manifest["files"].values())
    manifest["scan_stats"] = {"reused_from_base": reused, "rehashed": rehashed}
    return manifest
