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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from core import paths as _paths
STSYNC_DIR = _paths.base_dir()
ACTIVITY_DIR = _paths.activity_dir()

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


def shard_path(workstation: Optional[str] = None, *, activity_dir=None) -> Path:
    ws = workstation or socket.gethostname()
    base = Path(activity_dir) if activity_dir is not None else ACTIVITY_DIR
    return base / f"activity_{ws}.jsonl"


def append_activity(
    record: ActivityRecord,
    *,
    activity_dir=None,
) -> Path:
    """Append one summary line to this machine's shard, atomically.

    JSONL append is a single write of one newline-terminated line, so concurrent
    readers never see a torn line. Returns the shard path. Each machine only ever
    writes its own shard, so there is no cross-machine write contention.

    M14.3 — O_APPEND is sufficient here; no sidecar lock is needed because all
    three assumptions hold:
      (a) This is a true append (``open("a")``), not a read-modify-write — there
          is no prior state to lose, so no lost-update window exists.
      (b) Each record is a single fixed-shape summary line (project, counts,
          bytes, verdict — never an embedded file list), comfortably under the
          ~4 KB where a same-host O_APPEND write could plausibly tear on local
          APFS. Records do not grow with project size.
      (c) The shard always lives on the LOCAL tree (``ACTIVITY_DIR`` under
          ``~/Documents/STSyncTool``), never on a mounted NAS share where
          O_APPEND atomicity is not guaranteed. Org-wide aggregation copies these
          shards up separately (M9.1); it never writes them in place on a share.
    """
    path = shard_path(record.workstation, activity_dir=activity_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.to_json() + "\n")
    return path


def record_from_manifest(
    manifest: dict,
    *,
    operation: str,
    source: str = "",
    dests: Optional[list] = None,
    verdict: str = "",
    log_filename: str = "",
    now: Optional[datetime] = None,
) -> ActivityRecord:
    """Build an ActivityRecord from a manifest dict, deriving project/counts/bytes.

    `file_count` and `bytes` fall back to summing the manifest's files when the
    top-level totals are absent, so it works for any manifest shape.
    """
    files = manifest.get("files", {}) or {}
    file_count = manifest.get("file_count")
    if file_count is None:
        file_count = len(files)
    total_bytes = manifest.get("total_size_bytes")
    if total_bytes is None:
        total_bytes = sum(int((f or {}).get("size") or 0) for f in files.values())
    # Resolve a human-readable project name: prefer the display_name registered
    # in the projects registry, fall back to the manifest label (folder name),
    # then the hex project_id as a last resort. Never shows a raw hex hash in UI.
    project_id = manifest.get("project_id") or ""
    project_display = ""
    if project_id:
        try:
            from core import projects as _projects
            proj = _projects.get_project(project_id)
            if proj:
                project_display = proj.get("display_name") or ""
        except Exception:
            pass
    project_display = project_display or manifest.get("label") or project_id

    return record_for(
        operation,
        project=project_display,
        source=source,
        dests=dests or [],
        file_count=int(file_count or 0),
        bytes=int(total_bytes or 0),
        verdict=verdict,
        log_filename=log_filename,
        now=now,
        workstation=manifest.get("workstation") or None,
        user=manifest.get("user") or None,
    )


def safe_append_activity(record: ActivityRecord, *, activity_dir=None, log_cb=None):
    """Append an activity record, never raising — a logging failure must not
    affect the operation that produced it. Returns the shard path or None."""
    try:
        return append_activity(record, activity_dir=activity_dir)
    except Exception as exc:  # pragma: no cover - defensive
        if log_cb:
            log_cb(f"Activity not recorded: {exc}", "warning")
        return None


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
    now = now or datetime.now(timezone.utc)
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


# Local cache of other machines' shards downloaded from the org remote.
ORG_CACHE_DIR = _paths.activity_cache_dir()


def fetch_remote_shards(remote_base, cache_dir=ORG_CACHE_DIR, *, list_fn=None,
                        copy_fn=None, log_cb=None) -> list:
    """M9.3: download other machines' ``activity_*.jsonl`` shards from the org
    remote into ``cache_dir`` (kilobytes — never the raw logs). Returns the list
    of local filenames fetched. Never raises; a per-file failure is logged and
    skipped. ``list_fn(remote_base) -> [remote_path, ...]`` and
    ``copy_fn(remote_path, local_path)`` are injected (default to rclone)."""
    log = log_cb or (lambda m, l="info": None)
    if not remote_base:
        return []
    if list_fn is None or copy_fn is None:
        from core import rclone_bridge
        if list_fn is None:
            list_fn = lambda base: rclone_bridge.find_activity_shards(base)
        if copy_fn is None:
            copy_fn = lambda src, dst: rclone_bridge.copyto(src, dst)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    fetched = []
    try:
        remote_paths = list_fn(remote_base) or []
    except Exception as e:
        log(f"  Could not list org activity shards: {e}", "warning")
        return []
    for rp in remote_paths:
        name = Path(rp).name
        if not name.startswith("activity_") or not name.endswith(".jsonl"):
            continue
        try:
            copy_fn(rp, str(cache / name))
            fetched.append(name)
        except Exception as e:
            log(f"  Skipped org shard {name}: {e}", "warning")
    return fetched


def find_local_log(filename: str, base_dir=STSYNC_DIR) -> Optional[Path]:
    """M9.3: locate a custody/verify log by filename under the local STSyncTool
    dirs (logs/, offload_logs/). Returns the path or None. Used by the History
    tab's "open custody log" action for jobs run on this machine."""
    if not filename:
        return None
    base = Path(base_dir)
    # New human-readable report dirs, plus legacy names so pre-restructure logs
    # still open.
    subs = list(_paths.FEEDBACK_SUBDIRS) + [_paths.MANIFESTS, "logs", "offload_logs"]
    for sub in subs:
        cand = base / sub / filename
        if cand.is_file():
            return cand
    # Fall back to a recursive search in case of date-grouped / nested layouts.
    for sub in subs:
        d = base / sub
        if d.is_dir():
            for hit in d.rglob(filename):
                if hit.is_file():
                    return hit
    return None


def find_local_log_by_timestamp(
    timestamp_iso: str,
    operation: str,
    base_dir=STSYNC_DIR,
    window_seconds: int = 120,
) -> Optional[Path]:
    """Fallback for history rows written before log_filename was stored.

    Scans the report dirs for files whose name-encoded timestamp (YYYYMMDD_HHMMSS,
    local time) is within `window_seconds` of the record's UTC timestamp.
    Returns the closest match, or None.
    """
    import re
    TS_RE = re.compile(r"(\d{8}_\d{6})")

    try:
        rec_utc = datetime.fromisoformat(timestamp_iso)
        if rec_utc.tzinfo is None:
            rec_utc = rec_utc.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    base = Path(base_dir)
    subs = list(_paths.FEEDBACK_SUBDIRS) + [_paths.MANIFESTS, "logs", "offload_logs"]

    best: Optional[Path] = None
    best_delta: float = float("inf")

    for sub in subs:
        d = base / sub
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            m = TS_RE.search(f.name)
            if not m:
                continue
            try:
                # File timestamp is local-naive; convert to UTC for comparison.
                file_local = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
                file_utc = file_local.astimezone(timezone.utc)
            except ValueError:
                continue
            delta = abs((rec_utc - file_utc).total_seconds())
            if delta <= window_seconds and delta < best_delta:
                best_delta = delta
                best = f

    return best


def find_manifest_log(timestamp_iso: str, base_dir=STSYNC_DIR,
                      window_seconds: int = 120) -> Optional[Path]:
    """Like find_local_log_by_timestamp but restricted to JSON files under
    the Manifests directory. Used for transfer jobs where .txt reports are
    ignored in favour of the manifest."""
    import re
    TS_RE = re.compile(r"(\d{8}_\d{6})")

    try:
        rec_utc = datetime.fromisoformat(timestamp_iso)
        if rec_utc.tzinfo is None:
            rec_utc = rec_utc.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    base = Path(base_dir) / _paths.MANIFESTS
    if not base.is_dir():
        return None

    best: Optional[Path] = None
    best_delta: float = float("inf")

    for f in base.rglob("*.json"):
        m = TS_RE.search(f.name)
        if not m:
            continue
        try:
            file_local = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
            file_utc = file_local.astimezone(timezone.utc)
        except ValueError:
            continue
        delta = abs((rec_utc - file_utc).total_seconds())
        if delta <= window_seconds and delta < best_delta:
            best_delta = delta
            best = f

    return best


def load_org_records(*, local_dir=ACTIVITY_DIR, cache_dir=ORG_CACHE_DIR,
                     log_cb=None) -> list:
    """Merge this machine's shards with the cached org shards into one
    timestamp-sorted record list. When a shard filename appears in both, the
    local copy wins (our own data is authoritative and freshest)."""
    by_name: dict = {}
    for p in find_shards(cache_dir):
        by_name[p.name] = p
    for p in find_shards(local_dir):  # local overrides cache for same filename
        by_name[p.name] = p
    return merge_shards(list(by_name.values()), log_cb=log_cb)


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
    now = now or datetime.now(timezone.utc)
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
            dt = datetime.fromisoformat(ts)
            # Normalise to UTC-aware so subtraction never raises TypeError when
            # mixing naive (old) records with new UTC-aware ones.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            _now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
            days = (_now - dt).days
        except (ValueError, TypeError):
            continue
        out.append(WorkstationStaleness(
            workstation=ws, last_reported=ts, days_since=days,
            stale=days >= stale_after_days,
        ))
    out.sort(key=lambda s: s.days_since, reverse=True)
    return out
