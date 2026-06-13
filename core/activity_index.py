"""M9.2 — Per-machine activity summaries (the card index).

The app must never list or read thousands of raw logs over the network. Instead,
each completed job appends one compact summary line to a per-machine JSONL shard
(`activity/activity_{workstation}.jsonl`). Each machine writes only its own
shard, so there are no write conflicts and no server. The shard is shipped to the
shared Drive folder by M9.1 like any other file; org-wide queries merge the
shards (kilobytes) and fetch a raw custody log only when a human opens one.

Staleness flags (last-reported date per workstation) fall out of the merged
shards for free and cover M9.1's never-reopened-app gap.

Pure logic, no PyQt6. Paths and the current time are injectable for tests.
"""

from __future__ import annotations

import getpass
import json
import socket
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

STSYNC_DIR = Path.home() / "Documents" / "STSyncTool"
ACTIVITY_DIR = STSYNC_DIR / "activity"

# How many days without a new summary line marks a workstation as stale.
STALE_AFTER_DAYS = 7


@dataclass(frozen=True)
class ActivityRecord:
    """One line in a per-machine shard — a single completed job."""
    operation: str          # offload | transfer | merge | verify
    timestamp: str          # ISO8601
    workstation: str
    user: str
    project: str = ""
    source: str = ""
    dests: list = field(default_factory=list)
    file_count: int = 0
    bytes: int = 0
    verdict: str = ""       # e.g. VERIFIED | COMPLETE | PARTIAL_FAILURE | FAIL
    log_filename: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def shard_path(workstation: Optional[str] = None, *, activity_dir=ACTIVITY_DIR) -> Path:
    ws = workstation or socket.gethostname()
    return Path(activity_dir) / f"activity_{ws}.jsonl"


def append_activity(
    record: ActivityRecord,
    *,
    activity_dir=ACTIVITY_DIR,
) -> Path:
    """Append one summary line to this machine's shard, atomically.

    JSONL append is a single write of one newline-terminated line, so concurrent
    readers never see a torn line. Returns the shard path. Each machine only ever
    writes its own shard, so there is no cross-machine write contention.
    """
    path = shard_path(record.workstation, activity_dir=activity_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.to_json() + "\n")
    return path


def record_for(
    operation: str,
    *,
    project: str = "",
    source: str = "",
    dests: Optional[list] = None,
    file_count: int = 0,
    bytes: int = 0,
    verdict: str = "",
    log_filename: str = "",
    now: Optional[datetime] = None,
    workstation: Optional[str] = None,
    user: Optional[str] = None,
) -> ActivityRecord:
    """Build an ActivityRecord, stamping host/user/time so call sites stay terse."""
    now = now or datetime.now()
    return ActivityRecord(
        operation=operation,
        timestamp=now.isoformat(),
        workstation=workstation or socket.gethostname(),
        user=user or getpass.getuser(),
        project=project,
        source=source,
        dests=list(dests or []),
        file_count=file_count,
        bytes=bytes,
        verdict=verdict,
        log_filename=log_filename,
    )


# --------------------------------------------------------------------------- #
# loading + merging shards
# --------------------------------------------------------------------------- #

def load_shard(path, *, log_cb: Optional[Callable] = None) -> list:
    """Parse a shard into a list of dicts, skipping corrupt lines loudly.

    A partial/corrupt last line (a crash mid-write) or any malformed line is
    logged and skipped rather than aborting the whole shard.
    """
    log = log_cb or (lambda m, l="warning": None)
    path = Path(path)
    out: list = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        log(f"Activity shard unreadable: {path} ({e})", "warning")
        return out
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            log(f"Skipping corrupt line {lineno} in {path.name}", "warning")
            continue
        if isinstance(rec, dict):
            out.append(rec)
        else:
            log(f"Skipping non-object line {lineno} in {path.name}", "warning")
    return out


def merge_shards(paths, *, log_cb: Optional[Callable] = None) -> list:
    """Merge several shard files into one timestamp-sorted record list."""
    merged: list = []
    for p in paths:
        merged.extend(load_shard(p, log_cb=log_cb))
    merged.sort(key=lambda r: r.get("timestamp", ""))
    return merged


def find_shards(activity_dir=ACTIVITY_DIR) -> list:
    """All activity_*.jsonl shard paths under a directory (e.g. a synced mirror)."""
    d = Path(activity_dir)
    if not d.exists():
        return []
    return sorted(d.glob("activity_*.jsonl"))


# --------------------------------------------------------------------------- #
# query + staleness
# --------------------------------------------------------------------------- #

def filter_records(records: list, *, operation: Optional[str] = None,
                   workstation: Optional[str] = None, user: Optional[str] = None,
                   project: Optional[str] = None) -> list:
    """Filter merged records by any combination of fields (None = no constraint)."""
    def ok(r: dict) -> bool:
        return (
            (operation is None or r.get("operation") == operation)
            and (workstation is None or r.get("workstation") == workstation)
            and (user is None or r.get("user") == user)
            and (project is None or r.get("project") == project)
        )
    return [r for r in records if ok(r)]


@dataclass(frozen=True)
class WorkstationStaleness:
    workstation: str
    last_reported: str      # ISO8601 of the most recent record
    days_since: int
    stale: bool


def staleness(records: list, *, now: Optional[datetime] = None,
              stale_after_days: int = STALE_AFTER_DAYS) -> list:
    """Per-workstation last-reported date + staleness flag, newest-stale first.

    Covers M9.1's never-reopened-app gap: a cart that has not reported in
    `stale_after_days` shows up here ("Cart 3 hasn't reported since June 2").
    """
    now = now or datetime.now()
    latest: dict = {}
    for r in records:
        ws = r.get("workstation", "")
        ts = r.get("timestamp", "")
        if not ws or not ts:
            continue
        if ws not in latest or ts > latest[ws]:
            latest[ws] = ts

    out: list = []
    for ws, ts in latest.items():
        try:
            days = (now - datetime.fromisoformat(ts)).days
        except ValueError:
            continue
        out.append(WorkstationStaleness(
            workstation=ws, last_reported=ts, days_since=days,
            stale=days >= stale_after_days,
        ))
    out.sort(key=lambda s: s.days_since, reverse=True)
    return out
