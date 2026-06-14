"""M7.1 — locate external binaries whether running from source or a frozen .app.

When PyInstaller freezes the app into ``ST SyncTool.app``, bundled helper
binaries (rclone, and optionally ffmpeg/ffprobe) are placed inside the bundle.
``find_binary`` looks there first when frozen, then falls back to the user's
PATH, so the same code works from source (`python main.py`) and from the DMG.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_bin_dirs() -> list:
    """Directories inside a frozen .app that may hold bundled binaries.

    A PyInstaller macOS bundle runs from ``…/ST SyncTool.app/Contents/MacOS/``;
    bundled binaries land either there or in the sibling ``Resources`` dir.
    Returns an empty list when running from source.
    """
    if not is_frozen():
        return []
    macos = Path(sys.executable).resolve().parent          # Contents/MacOS
    return [macos, macos.parent / "Resources"]


def find_binary(name: str) -> Optional[str]:
    """Return the path to ``name``, preferring a bundled copy when frozen.

    Falls back to ``shutil.which`` (the user's PATH) and returns None if the
    binary cannot be found anywhere. Behaviour from source is identical to a
    plain PATH lookup.
    """
    for d in bundle_bin_dirs():
        cand = d / name
        if cand.is_file():
            return str(cand)
    return shutil.which(name)


def app_icon_path() -> Optional[str]:
    """Return the app-icon PNG path (for QIcon at runtime), or None.

    Prefers a user-supplied ``assets/app_icon.png`` so the brand export can be
    dropped in without code changes. Resolves inside a frozen bundle via
    ``sys._MEIPASS`` / Resources, and from the repo's ``assets/`` when running
    from source.
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "app_icon.png")
    for d in bundle_bin_dirs():
        candidates.append(d / "assets" / "app_icon.png")
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / "app_icon.png")
    for c in candidates:
        if c and c.is_file():
            return str(c)
    return None


def prepend_bundle_to_path(env: Optional[dict] = None) -> None:
    """Put the frozen .app's bundled-binary dirs at the front of PATH.

    Call once at startup. After this, bare-name subprocess calls (``rclone``,
    ``ffmpeg`` …) anywhere in the app resolve to the bundled copy first, so call
    sites need no per-site changes. No-op when running from source.
    """
    dirs = bundle_bin_dirs()
    if not dirs:
        return
    e = env if env is not None else os.environ
    prefix = os.pathsep.join(str(d) for d in dirs)
    e["PATH"] = prefix + os.pathsep + e.get("PATH", "")
