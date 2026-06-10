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
        DCIM/
            A001/
                A001C001_260610_R0FH.mov      (zero-byte)
                A001C002_260610_R0FH.mov
                A001C003_260610_R0FH.mov
                A001C004_260610_R0FH.mov
            B001/
                B001C001_260610_R0FH.mov
                B001C002_260610_R0FH.mov
                B001C003_260610_R0FH.mov
            C001/
                C001C001_260610_R0FH.arw     (stills — zero-byte)
                C001C002_260610_R0FH.arw
                C001C003_260610_R0FH.arw
                C001C004_260610_R0FH.arw
                C001C005_260610_R0FH.arw
        AUDIO/
            SND001_BOOM.wav
            SND002_BOOM.wav
            SND003_LAV_A.wav
            SND004_LAV_B.wav
        MISC/
            NOTES.txt           (small text file, not zero-byte)
    destination/                (empty — transfer lands here)
    README_DEMO.txt             (explains what the folder is)
"""

from __future__ import annotations

import os
import platform
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

    source/     — pretend camera card (DCIM + AUDIO)
    destination/ — empty; use as the Transfer destination

    Safe to delete at any time. ST SyncTool will recreate it if you
    click "Use demo folder" again.
""").encode()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def ensure_demo_folder() -> tuple[Path, Path]:
    """
    Create (or verify) the demo folder structure.

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

    return demo_source(), dest


def demo_exists() -> bool:
    """True if the demo source folder has already been created."""
    return demo_source().exists()
