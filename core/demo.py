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
