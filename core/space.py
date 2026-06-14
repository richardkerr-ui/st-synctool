"""M12.1 destination free-space preflight.

A full destination is the worst possible failure for a DIT: today it surfaces
mid-copy as a non-retryable disk-full error, after work has already started.
This module sums the source size up front and refuses to begin a copy that
cannot fit, with a clear shortfall message per destination.

Pure and headless (no PyQt6). The destination free-space probe and the source
size probe are both injectable so the logic is unit-testable without a disk.
Google Drive URL destinations are skipped: server-side copies have no local
disk to check (the 750 GB/day warning still applies, enforced elsewhere).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from utils.file_utils import folder_size, format_bytes, free_space
from utils.gdrive_utils import is_gdrive_url

# An rclone remote string is "name:path" (e.g. "gdrive:Footage/A001"). A POSIX
# destination path never carries a ":" remote prefix, so this cleanly tells a
# server-side target apart from a local/NAS volume we can probe for free space.
_RCLONE_REMOTE = re.compile(r"^\w[\w +.-]*:")


def _is_non_local(path) -> bool:
    """True for a destination with no local disk to probe (Drive web URL or an
    rclone remote string)."""
    s = str(path)
    return is_gdrive_url(s) or bool(_RCLONE_REMOTE.match(s))

# Safety headroom so we never fill a destination to its very last byte (a
# brim-full disk corrupts filesystems and leaves no room for the manifest,
# custody log or contact sheets we write alongside the footage).
HEADROOM_FRAC = 0.03                       # 3% of the required size, plus...
HEADROOM_MIN_BYTES = 200 * 1024 * 1024     # ...never less than 200 MB


class OffloadSpaceError(Exception):
    """Raised to abort an offload before any byte is copied when a destination
    does not have enough free space. The message lists every failing dest."""


def _headroom(required_bytes: int) -> int:
    """Headroom margin for a given payload. Zero for an empty payload so a
    no-op offload is never blocked by the floor."""
    if required_bytes <= 0:
        return 0
    return max(int(required_bytes * HEADROOM_FRAC), HEADROOM_MIN_BYTES)


@dataclass(frozen=True)
class SpaceVerdict:
    label: str
    required_bytes: int
    headroom_bytes: int
    free_bytes: int
    ok: bool
    skipped: bool = False   # Drive URL / not a local disk — no check possible

    @property
    def needed_bytes(self) -> int:
        return self.required_bytes + self.headroom_bytes

    @property
    def shortfall_bytes(self) -> int:
        return max(0, self.needed_bytes - self.free_bytes)

    def message(self) -> str:
        if self.skipped:
            return (f"{self.label}: server-side destination, "
                    f"local free-space check skipped")
        if self.ok:
            return (f"{self.label}: OK — needs "
                    f"{format_bytes(self.required_bytes)}, "
                    f"{format_bytes(self.free_bytes)} free")
        return (f"{self.label}: NOT ENOUGH SPACE — needs "
                f"{format_bytes(self.required_bytes)} "
                f"(+{format_bytes(self.headroom_bytes)} headroom), only "
                f"{format_bytes(self.free_bytes)} free, short by "
                f"{format_bytes(self.shortfall_bytes)}")


def total_source_bytes(
    sources: list,
    size_fn: Callable[[Path], int] = folder_size,
) -> int:
    """Cheap stat-only sum of every enabled source's size. Used before the
    (expensive) pre-hash so a doomed copy is rejected with no wasted hashing."""
    total = 0
    for s in sources:
        if not getattr(s, "enabled", True):
            continue
        total += size_fn(s.path)
    return total


def check_destination_space(
    required_bytes: int,
    dests: list,
    free_fn: Callable[[Path], int] = free_space,
) -> list:
    """Return a SpaceVerdict per enabled destination.

    Each destination in an M×N offload receives *all* sources, so the same
    ``required_bytes`` (the sum across sources) is checked against every dest.
    A Drive URL dest is reported skipped+ok; an unreadable dest fails with a
    zero-free verdict rather than silently passing.
    """
    headroom = _headroom(required_bytes)
    verdicts: list = []
    for d in dests:
        if not getattr(d, "enabled", True):
            continue
        if _is_non_local(d.path):
            verdicts.append(SpaceVerdict(
                d.label, required_bytes, headroom, 0, ok=True, skipped=True))
            continue
        try:
            free = free_fn(d.path)
        except OSError:
            verdicts.append(SpaceVerdict(
                d.label, required_bytes, headroom, 0, ok=False))
            continue
        ok = free >= required_bytes + headroom
        verdicts.append(SpaceVerdict(
            d.label, required_bytes, headroom, free, ok=ok))
    return verdicts


def all_clear(verdicts: list) -> bool:
    return all(v.ok for v in verdicts)


def blocking_message(verdicts: list) -> Optional[str]:
    """Combined operator-facing message for the failing destinations, or None
    when every destination has room."""
    failed = [v for v in verdicts if not v.ok]
    if not failed:
        return None
    lines = ["Not enough free space to start this offload safely:"]
    lines += ["  • " + v.message() for v in failed]
    return "\n".join(lines)
