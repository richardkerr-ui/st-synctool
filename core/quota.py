"""Google Drive quota awareness (M10.2).

We can never know an account's true 750 GB/day usage because uploads made
outside this app (browser, Drive desktop client) are invisible to us. Rather
than show a false gauge, this module provides two honest layers:

(a) A persisted daily tally of uploads made *through this app*, presented
    strictly as a floor ("at least N uploaded through ST SyncTool today").
(b) Classification of rclone's Google quota / rate-limit error output, so a
    cryptic failure becomes a plain-language message. Layer (b) is reliable
    regardless of outside-app uploads because Google itself is the source of
    truth for the error.

Pure logic, no PyQt6, fully unit-testable. Persistence and the current date
are injectable so the daily reset can be tested across day boundaries.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import humanize

# Google's documented per-account upload ceiling. Used only for context in the
# floor message — never as a true "remaining" figure, since outside-app uploads
# are invisible to us.
GOOGLE_DAILY_LIMIT_BYTES = 750 * 1024 ** 3

# Where the daily upload tally is persisted.
from core import paths as _paths
TALLY_PATH = _paths.upload_tally_path()


# --------------------------------------------------------------------------- #
# (b) rclone error classification
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RcloneErrorClass:
    """A recognised, plain-language explanation of an rclone failure."""
    kind: str       # "rate_limit" | "storage_full"
    message: str


# Google API reason strings (and human-readable variants) that mean the daily
# upload / rate limit was hit. Matched case-insensitively as substrings of the
# captured rclone stderr.
_RATE_LIMIT_MARKERS = (
    "userratelimitexceeded",
    "ratelimitexceeded",
    "dailylimitexceeded",
    "user rate limit exceeded",
    "rate limit exceeded",
    "quotaexceeded",            # "quotaExceeded" (request quota, not storage)
    "uploadbandwidthlimitexceeded",
)

# Distinct from the rate limit: the account is simply out of storage space.
# Conflating the two would be dishonest, so they get separate messages.
_STORAGE_FULL_MARKERS = (
    "storagequotaexceeded",
    "the user's drive storage quota has been exceeded",
    "limitexceeded: storage",
)

_RATE_LIMIT_MESSAGE = (
    "Google's daily upload limit was hit for this account. It resets at "
    "midnight Pacific time. Your files are safe — nothing was corrupted; "
    "resume the transfer after the reset."
)

_STORAGE_FULL_MESSAGE = (
    "This Google Drive account is out of storage space, so the upload could "
    "not complete. Free up space (or contact a Signal Theory Productions "
    "administrator) and resume. Your local files are unaffected."
)


def classify_rclone_error(stderr) -> RcloneErrorClass | None:
    """Inspect captured rclone stderr and return a plain-language classification.

    Returns None when the output does not match a known Google quota / rate-limit
    condition, so callers fall back to their generic error handling.
    """
    if not stderr:
        return None
    text = str(stderr).lower()
    # Storage-full is checked first: "storageQuotaExceeded" also contains the
    # substring "quotaexceeded", so the more specific case must win.
    for marker in _STORAGE_FULL_MARKERS:
        if marker in text:
            return RcloneErrorClass("storage_full", _STORAGE_FULL_MESSAGE)
    for marker in _RATE_LIMIT_MARKERS:
        if marker in text:
            return RcloneErrorClass("rate_limit", _RATE_LIMIT_MESSAGE)
    return None


# --------------------------------------------------------------------------- #
# (a) daily upload tally (floor)
# --------------------------------------------------------------------------- #

def _today_str(now: datetime | None) -> str:
    # Local date — the tally resets on the user's calendar day. (Google's own
    # reset is midnight Pacific; the floor figure is advisory so an exact match
    # to Google's clock is neither possible nor needed.)
    return (now or datetime.now()).strftime("%Y-%m-%d")


def _read_tally(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def today_uploaded(*, now: datetime | None = None, path: Path = TALLY_PATH) -> int:
    """Bytes uploaded through this app on the current local day (0 if a new day)."""
    data = _read_tally(path)
    if data.get("date") != _today_str(now):
        return 0
    value = data.get("bytes", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def record_upload(num_bytes, *, now: datetime | None = None,
                  path: Path = TALLY_PATH) -> int:
    """Add `num_bytes` to today's tally, resetting first if the day rolled over.

    Returns the new running total. Written atomically (tmp + rename) so a crash
    mid-write cannot corrupt the ledger. Failures to persist are swallowed: the
    tally is advisory and must never block a transfer.
    """
    if not isinstance(num_bytes, int) or num_bytes <= 0:
        return today_uploaded(now=now, path=path)
    today = _today_str(now)
    current = today_uploaded(now=now, path=path)
    total = current + num_bytes
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"date": today, "bytes": total}))
        tmp.replace(p)
    except OSError:
        pass
    return total


def tally_floor_text(*, now: datetime | None = None,
                     path: Path = TALLY_PATH) -> str | None:
    """Human-readable floor line, or None when nothing has been uploaded today."""
    uploaded = today_uploaded(now=now, path=path)
    if uploaded <= 0:
        return None
    return (
        f"At least {humanize.naturalsize(uploaded, binary=True)} uploaded "
        f"through ST SyncTool today (of Google's {humanize.naturalsize(GOOGLE_DAILY_LIMIT_BYTES, binary=True)} "
        f"daily limit; uploads made outside this app are not counted)."
    )
