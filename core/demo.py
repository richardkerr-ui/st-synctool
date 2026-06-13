"""
core/demo.py
------------
Creates (and returns paths to) a persistent demo folder that new users can
use to try Transfer, Offload, and Verify without touching real production files.

Folder lives in the macOS Application Support directory so it persists across
sessions. On non-macOS platforms it falls back to ~/.local/share/…

Structure created
-----------------
<app_support>/demo/
    source/
        DCIM/A001..C001/  (zero-byte .mov/.arw stubs)
        AUDIO/            (zero-byte .wav stubs)
        MISC/NOTES.txt    (real text content)
    destination/          (empty — Transfer tab lands here)
    verify_sample/        (pre-populated stubs + manifest for Verify tab)
    README_DEMO.txt
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import textwrap
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# App support root
# ─────────────────────────────────────────────────────────────────────────────

def _app_support_dir() -> Path:
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Signal Theory" / "ST SyncTool"


def demo_root() -> Path:
    """Return the demo root directory (may not exist yet)."""
    return _app_support_dir() / "demo"


def demo_source() -> Path:
    return demo_root() / "source"


def demo_destination() -> Path:
    return demo_root() / "destination"


def demo_verify_sample() -> Path:
    """Pre-populated folder + manifest for the Verify tutorial step."""
    return demo_root() / "verify_sample"


def demo_verify_manifest() -> Path:
    return demo_verify_sample() / "st_manifest.json"


# ─────────────────────────────────────────────────────────────────────────────
# File manifest
# ─────────────────────────────────────────────────────────────────────────────

_STUB_FILES: list[tuple[str, bytes | None]] = [
    # DCIM/A001 — primary camera (ARRI/Sony-style naming)
    ("source/DCIM/A001/A001C001_260610_R0FH.mov",    None),
    ("source/DCIM/A001/A001C002_260610_R0FH.mov",    None),
    ("source/DCIM/A001/A001C003_260610_R0FH.mov",    None),
    ("source/DCIM/A001/A001C004_260610_R0FH.mov",    None),
    # DCIM/B001 — secondary camera
    ("source/DCIM/B001/B001C001_260610_R0FH.mov",    None),
    ("source/DCIM/B001/B001C002_260610_R0FH.mov",    None),
    ("source/DCIM/B001/B001C003_260610_R0FH.mov",    None),
    # DCIM/C001 — stills
    ("source/DCIM/C001/C001C001_260610_R0FH.arw",    None),
    ("source/DCIM/C001/C001C002_260610_R0FH.arw",    None),
    ("source/DCIM/C001/C001C003_260610_R0FH.arw",    None),
    ("source/DCIM/C001/C001C004_260610_R0FH.arw",    None),
    ("source/DCIM/C001/C001C005_260610_R0FH.arw",    None),
    # AUDIO
    ("source/AUDIO/SND001_BOOM.wav",                  None),
    ("source/AUDIO/SND002_BOOM.wav",                  None),
    ("source/AUDIO/SND003_LAV_A.wav",                 None),
    ("source/AUDIO/SND004_LAV_B.wav",                 None),
    # Notes (has real content so it's findable in a text editor)
    (
        "source/MISC/NOTES.txt",
        textwrap.dedent("""\
            Demo shoot notes — 2026-06-10
            ==============================
            Scene 1  — Wide exterior, golden hour
            Scene 2  — OTS dialogue, INT kitchen
            Scene 3  — Close-ups, hands
            Scene 4  — B-roll, city street

            All files in DCIM/A001 and B001 are zero-byte stubs.
            They exist so you can try Transfer, Offload, and Verify
            without using real camera cards or cloud storage.
        """).encode(),
    ),
]

_README = textwrap.dedent("""\
    ST SyncTool — Demo Folder
    =========================
    This folder was created by ST SyncTool for the onboarding tutorial.
    It contains zero-byte file stubs that mimic a real camera card offload,
    so you can try every feature without needing real production files.

    source/        — pretend camera card (DCIM + AUDIO)
    destination/   — empty; Transfer tab lands here
    verify_sample/ — pre-populated folder + manifest for the Verify tab

    Safe to delete at any time. ST SyncTool will recreate it on next launch.
""").encode()


# ─────────────────────────────────────────────────────────────────────────────
# verify_sample builder
# ─────────────────────────────────────────────────────────────────────────────

# sha256 of an empty file — all zero-byte stubs share this hash.
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _build_verify_sample(root: Path) -> None:
    """
    Populate demo/verify_sample/ with copies of the source stubs and a valid
    st_manifest.json so the Verify tab can run without the user first doing a
    Transfer.

    Only creates files that don't already exist so repeated calls are safe.
    """
    sample = demo_verify_sample()
    sample.mkdir(parents=True, exist_ok=True)

    manifest_path = demo_verify_manifest()

    # Copy stubs (relative paths mirror source/ structure)
    rel_paths = []
    for rel_str, content in _STUB_FILES:
        # Strip leading "source/" to get the relative path inside verify_sample
        rel = rel_str[len("source/"):]
        dest_file = sample / rel
        if not dest_file.exists():
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_bytes(content if content is not None else b"")
        rel_paths.append((rel, dest_file))

    if not manifest_path.exists():
        # Build a minimal but structurally valid manifest
        from datetime import datetime, timezone
        import socket, getpass

        files: dict = {}
        total_size = 0
        for rel, fpath in rel_paths:
            stat = fpath.stat()
            size = stat.st_size
            total_size += size
            files[rel] = {
                "type": "file",
                "size": size,
                "modtime": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "checksums": {"sha256": _EMPTY_SHA256},
                "hash_algorithm": "sha256",
                "gdrive_url": "",
            }

        manifest = {
            "schema_version": "1.1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": "demo_verify_sample",
            "root": str(sample),
            "destination": str(sample),
            "server_path": "",
            "operation": "demo",
            "project_id": "demo",
            "workstation": socket.gethostname(),
            "user": getpass.getuser(),
            "file_count": len(files),
            "total_size_bytes": total_size,
            "renames": [],
            "checksum_context": {
                "algorithm": "sha256",
                "gdrive_mode": False,
            },
            "files": files,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def ensure_demo_folder() -> tuple[Path, Path]:
    """
    Create (or verify) the full demo folder structure.

    Returns (source_path, destination_path).
    Safe to call multiple times — skips files that already exist.
    """
    root = demo_root()
    root.mkdir(parents=True, exist_ok=True)

    readme = root / "README_DEMO.txt"
    if not readme.exists():
        readme.write_bytes(_README)

    for rel_path, content in _STUB_FILES:
        full = root / rel_path
        if not full.exists():
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(content if content is not None else b"")

    dest = demo_destination()
    dest.mkdir(parents=True, exist_ok=True)

    _build_verify_sample(root)

    return demo_source(), dest


def demo_exists() -> bool:
    """True if the demo source folder has already been created."""
    return demo_source().exists()


# ─────────────────────────────────────────────────────────────────────────────
# Merge demo — two genuinely diverged folders for the onboarding tutorial
# ─────────────────────────────────────────────────────────────────────────────

def demo_merge_local() -> Path:
    return demo_root() / "merge_local"


def demo_merge_server() -> Path:
    return demo_root() / "merge_server"


def demo_merge_manifest() -> Path:
    """Base manifest representing the shared starting point before divergence."""
    return demo_root() / "merge_base_manifest.json"


# Each entry: (rel_path, base_bytes, local_bytes_or_None, server_bytes_or_None)
# None in local/server means the file was deleted on that side.
# Matching base bytes = unchanged on that side.
_MERGE_FILES: list[tuple[str, bytes, bytes | None, bytes | None]] = [
    (
        "DCIM/A001/scene_01.txt",
        b"Scene 1: wide exterior",
        b"Scene 1: wide exterior [YOUR EDIT]",    # LOCAL_CHANGED
        b"Scene 1: wide exterior",                 # unchanged on server
    ),
    (
        "DCIM/A001/scene_02.txt",
        b"Scene 2: OTS dialogue",
        b"Scene 2: OTS dialogue",                  # unchanged locally
        b"Scene 2: OTS dialogue [SERVER EDIT]",    # SERVER_CHANGED
    ),
    (
        "DCIM/A001/scene_03.txt",
        b"Scene 3: close-ups",
        b"Scene 3: close-ups [YOUR REVISION]",     # BOTH_CHANGED — conflict
        b"Scene 3: close-ups [SERVER REVISION]",   # BOTH_CHANGED — conflict
    ),
    (
        "DCIM/B001/b_roll.txt",
        b"B-roll: city street",
        b"B-roll: city street",                    # unchanged both sides
        b"B-roll: city street",
    ),
    (
        "AUDIO/sound_report.txt",
        b"Sound report: nominal",
        None,                                       # DELETED_LOCAL — you deleted it
        b"Sound report: nominal",                  # still on server
    ),
    (
        "MISC/notes.txt",
        b"Notes: all good",
        b"Notes: all good",                         # unchanged locally
        b"Notes: director approved",               # SERVER_CHANGED
    ),
]

# Files that only appear in local (LOCAL_ONLY)
_MERGE_LOCAL_ONLY: list[tuple[str, bytes]] = [
    ("DCIM/A001/new_footage.txt", b"New clip: morning light - added locally"),
]

# Files that only appear on server (SERVER_ONLY)
_MERGE_SERVER_ONLY: list[tuple[str, bytes]] = [
    ("DCIM/B001/server_addition.txt", b"Drone shot - added on server"),
]


def _sha256_of(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _write_if_missing(path: Path, content: bytes) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def ensure_demo_merge_folders() -> tuple[Path, Path, Path]:
    """
    Create (or verify) the diverged demo folders and base manifest used by
    the Merge tutorial.

    Returns (local_path, server_path, base_manifest_path).
    Safe to call multiple times — skips files that already exist.

    Divergence summary (7 interesting diffs):
        LOCAL_CHANGED  — scene_01.txt   (you edited it)
        SERVER_CHANGED — scene_02.txt, notes.txt (server edited)
        BOTH_CHANGED   — scene_03.txt   (conflict — both sides changed it)
        LOCAL_ONLY     — new_footage.txt (you added it)
        SERVER_ONLY    — server_addition.txt (server added it)
        DELETED_LOCAL  — sound_report.txt (you deleted it, server still has it)
    """
    from datetime import datetime, timezone
    import socket
    import getpass

    local_root  = demo_merge_local()
    server_root = demo_merge_server()
    manifest_path = demo_merge_manifest()

    local_root.mkdir(parents=True, exist_ok=True)
    server_root.mkdir(parents=True, exist_ok=True)

    # ── Write local and server files ──────────────────────────────────────
    for rel, _base, local_bytes, server_bytes in _MERGE_FILES:
        if local_bytes is not None:
            _write_if_missing(local_root / rel, local_bytes)
        if server_bytes is not None:
            _write_if_missing(server_root / rel, server_bytes)

    for rel, content in _MERGE_LOCAL_ONLY:
        _write_if_missing(local_root / rel, content)

    for rel, content in _MERGE_SERVER_ONLY:
        _write_if_missing(server_root / rel, content)

    # ── Write base manifest (records the ORIGINAL shared state) ──────────
    if not manifest_path.exists():
        now = datetime.now(timezone.utc).isoformat()
        files: dict = {}
        total_size = 0

        for rel, base_bytes, _local, _server in _MERGE_FILES:
            size = len(base_bytes)
            total_size += size
            files[rel] = {
                "type": "file",
                "size": size,
                "modtime": now,
                "checksums": {"sha256": _sha256_of(base_bytes)},
                "hash_algorithm": "sha256",
                "gdrive_url": "",
            }

        manifest = {
            "schema_version": "1.1",
            "created_at": now,
            "label": "demo_merge_base",
            "root": str(local_root),
            "destination": str(server_root),
            "server_path": str(server_root),
            "operation": "demo",
            "project_id": "demo_merge",
            "workstation": socket.gethostname(),
            "user": getpass.getuser(),
            "file_count": len(files),
            "total_size_bytes": total_size,
            "renames": [],
            "checksum_context": {
                "algorithm": "sha256",
                "gdrive_mode": False,
            },
            "files": files,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))

    return local_root, server_root, manifest_path


# ─────────────────────────────────────────────────────────────────────────────
# History demo — illustrative org activity records for the History tab / tour
# ─────────────────────────────────────────────────────────────────────────────

def demo_activity_records(now=None) -> list:
    """Representative activity records for the History tab when it is empty
    (onboarding tour / fresh install). Spans several machines, operations and
    verdicts, and includes one machine that is deliberately stale (>7 days) so
    the org-health staleness banner demonstrates too. Timestamps are relative to
    `now` so the staleness window is always meaningful.

    Returns a list of plain dicts in the activity-shard shape (no files written).
    """
    from datetime import datetime, timedelta
    now = now or datetime.now()

    def at(days_ago, hours=0):
        return (now - timedelta(days=days_ago, hours=hours)).isoformat()

    return [
        {"operation": "offload", "timestamp": at(0, 1), "workstation": "KC-RichardK",
         "user": "richard.kerr", "project": "Mythical_S1", "source": "A001",
         "dests": ["NAS", "Shuttle"], "file_count": 312, "bytes": 1288490188,
         "verdict": "VERIFIED", "log_filename": "A001 09.14.02 demo.txt"},
        {"operation": "transfer", "timestamp": at(0, 4), "workstation": "KC-RichardK",
         "user": "richard.kerr", "project": "Mythical_S1", "source": "Edit_Pull",
         "dests": ["Local"], "file_count": 48, "bytes": 53687091,
         "verdict": "COMPLETE", "log_filename": ""},
        {"operation": "offload", "timestamp": at(1), "workstation": "KC-RichardK",
         "user": "richard.kerr", "project": "Mythical_S1", "source": "C001",
         "dests": ["NAS"], "file_count": 96, "bytes": 402653184,
         "verdict": "NOT_CLEARED", "log_filename": "C001 18.40.51 demo.txt"},
        {"operation": "verify", "timestamp": at(2), "workstation": "Cart-2",
         "user": "ed.dit", "project": "Archive_Q2", "source": "Archive_Q2",
         "dests": [], "file_count": 904, "bytes": 0, "verdict": "FAIL",
         "log_filename": ""},
        {"operation": "merge", "timestamp": at(3), "workstation": "Cart-2",
         "user": "ed.dit", "project": "ProjectY", "source": "ProjectY",
         "dests": ["nas:/ProjectY"], "file_count": 21, "bytes": 8388608,
         "verdict": "COMPLETE", "log_filename": ""},
        # Deliberately stale (>7 days) so the staleness banner demonstrates.
        {"operation": "offload", "timestamp": at(12), "workstation": "Cart-3",
         "user": "sam.dit", "project": "Reshoots", "source": "B002",
         "dests": ["NAS", "Shuttle"], "file_count": 144, "bytes": 644245094,
         "verdict": "VERIFIED", "log_filename": "B002 22.03.10 demo.txt"},
    ]
