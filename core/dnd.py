"""Resolve a drag-and-drop payload to a single folder path.

DITs naturally drag a card folder onto the window rather than clicking Browse.
A drop can carry several URLs and may point at a folder or a file; this picks
the sensible folder to use. Pure logic so the behaviour is unit-testable
without Qt — the widgets just hand us the local paths from the drop.
"""

from pathlib import Path
from typing import Iterable, Optional


def folder_from_dropped_paths(paths: Iterable[str]) -> Optional[str]:
    """Return the folder to use for a drop, or None if there's nothing usable.

    Preference order:
      1. the first dropped path that is an existing directory;
      2. otherwise the parent directory of the first dropped entry
         (so dropping a file targets its containing folder).
    """
    cleaned = [p for p in paths if p]
    if not cleaned:
        return None
    for p in cleaned:
        if Path(p).is_dir():
            return str(Path(p))
    return str(Path(cleaned[0]).parent)
