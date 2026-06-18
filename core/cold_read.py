"""
core/cold_read.py — M14.2 cold-read verify.

Problem: when ``verify_local`` opens a destination file for hashing immediately
after the copy wrote it, macOS serves the read from the page cache. The hash then
compares RAM to RAM, so a failing SSD or flaky card reader passes verification
clean. "Verified" is partly a lie.

A genuinely cold read needs **two sides**:

  *Write side* (whatever calls shutil.copy2 / hands off to rclone): the bytes must
   physically reach the storage device before the verify read begins. On macOS
   ``fsync()`` is NOT sufficient — it flushes to the controller buffer, not to
   media. ``F_FULLFSYNC`` forces a full device flush. That seam lives in the copy
   path, outside this module; ``full_fsync`` below is provided for it to call.

  *Read side* (this module's ``cold_open``): after the write-side flush, the
   resident pages must be evicted so the read comes off the device.

**Honest status — macOS eviction is UNVERIFIED.** ``F_NOCACHE`` disables caching
on the fd going forward; it does NOT evict pages already resident from the write
that just completed. macOS has no *confirmed* per-file page-eviction primitive:
the documented constants are ``F_NOCACHE``, ``F_FULLFSYNC``, ``F_BARRIERFSYNC``,
``F_RDADVISE``; ``F_PURGE`` may or may not exist / behave as a per-fd evict on the
target OS versions. The system-wide ``purge`` command is nuclear and rejected.
``POSIX_FADV_DONTNEED`` (Linux) is advisory and may be ignored for recently
written pages. Therefore ``EVICTION_VERIFIED`` is False until the M14.2
real-device divergence experiment proves cold bytes actually came off the card
(see ROADMAP M14.2 manual check). Unit tests prove the plumbing only — a tmpfs
read is always warm and cannot prove coldness.
"""

from __future__ import annotations

import os
import sys

# macOS fcntl command constants (from <sys/fcntl.h>). Hard-coded because Python's
# fcntl module does not expose them as named attributes.
_F_NOCACHE = 48        # turn data caching off/on for this fd (going forward)
_F_FULLFSYNC = 51      # flush + ask the drive to flush to permanent storage

# Flip to True ONLY after the real-device divergence experiment confirms the read
# came off the device, not the cache. Keep code comments in sync.
EVICTION_VERIFIED = False


def full_fsync(fd) -> bool:
    """Write-side flush. Force buffered writes all the way to the storage media.

    Call on the WRITE file descriptor before closing, in whatever code path wrote
    the destination (this is the write seam — it lives outside ``verify_local``).
    On macOS issues ``F_FULLFSYNC`` (full device flush); elsewhere falls back to
    ``os.fsync``. Returns True when a full-device flush was issued, False when only
    a plain fsync was possible. Never raises for an ordinary unsupported-op error.
    """
    if sys.platform == "darwin":
        import fcntl
        try:
            fcntl.fcntl(fd, _F_FULLFSYNC)
            return True
        except OSError:
            os.fsync(fd)
            return False
    os.fsync(fd)
    return False


def coldness_label() -> str:
    """Human-readable description of how cold reads are on this platform."""
    if sys.platform == "darwin":
        return ("F_NOCACHE applied; per-file page eviction unverified on macOS "
                "(cache bypass not confirmed cold)") if not EVICTION_VERIFIED else \
               "verified cold on macOS"
    if sys.platform.startswith("linux"):
        return "POSIX_FADV_DONTNEED requested (advisory; not guaranteed cold)"
    return "cache bypass not confirmed cold on this platform"


def cold_open(path):
    """Open ``path`` for binary reading with the best cache-bypass available.

    macOS: open, then ``F_NOCACHE`` on the read fd, then *attempt* a
      ``POSIX_FADV_DONTNEED``-style eviction where the OS offers one. The eviction
      is UNVERIFIED (see module docstring / ``EVICTION_VERIFIED``).
    Linux/other POSIX: open, then ``posix_fadvise(DONTNEED)`` (advisory).
    Unsupported / unverified: plain ``open`` (labelled "cache bypass not confirmed
      cold").

    Returns an open binary file object. Best-effort: a failed fcntl/fadvise never
    prevents the read — it only means the read may be warm, which the per-file
    detail and the manual-checks table make explicit.
    """
    f = open(path, "rb")
    fd = f.fileno()
    try:
        if sys.platform == "darwin":
            import fcntl
            try:
                fcntl.fcntl(fd, _F_NOCACHE, 1)
            except OSError:
                pass
            # Attempt advisory eviction where present; unverified on macOS.
            _try_fadvise_dontneed(fd)
        elif sys.platform.startswith("linux"):
            _try_fadvise_dontneed(fd)
        # Other platforms: plain open, no cache bypass available.
    except Exception:
        # Cache-bypass is best-effort; never fail the open over it.
        pass
    return f


def _try_fadvise_dontneed(fd) -> bool:
    """Best-effort POSIX_FADV_DONTNEED on the whole file. Returns True if issued.

    Advisory only — the kernel may ignore it for dirty/recently-written pages.
    Absent on platforms without posix_fadvise (e.g. macOS lacks it; the call is
    simply skipped there)."""
    fadvise = getattr(os, "posix_fadvise", None)
    dontneed = getattr(os, "POSIX_FADV_DONTNEED", None)
    if fadvise is None or dontneed is None:
        return False
    try:
        fadvise(fd, 0, 0, dontneed)
        return True
    except OSError:
        return False
