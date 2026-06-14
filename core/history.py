"""M9.3 — History view presentation and query layer.

The data layer (load, merge, filter, staleness) lives in `core.activity_index`.
This module turns merged activity records into human-readable History rows and
adds the query helpers the History tab needs: date-range filtering, the distinct
values that populate each dropdown, and a single `rows_for` entry point that
filters then formats.

Pure logic, no PyQt6. The GUI renders the rows and dropdowns only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import humanize

from core import activity_index

# Fields a History dropdown can filter on, in display order.
FILTER_FIELDS = ("operation", "workstation", "user", "project")


@dataclass(frozen=True)
class HistoryRow:
    """One formatted History row, ready for the GUI to render as columns."""
    date_label: str
    workstation: str
    operation_label: str
    source: str
    dests: list = field(default_factory=list)
    file_count: int = 0
    bytes: int = 0
    verdict: str = ""
    log_filename: str = ""
    timestamp: str = ""

    @property
    def bytes_label(self) -> str:
        return humanize.naturalsize(self.bytes, binary=True) if self.bytes else ""

    def to_text(self) -> str:
        """Render a single-line summary, e.g.
        "Jun 12 · Cart 3 · Offload · A001 → NAS, Shuttle · 312 files · 1.2 GiB · VERIFIED".

        Empty segments are omitted so a sparse record still reads cleanly.
        """
        segs = [self.date_label, self.workstation, self.operation_label]
        if self.source or self.dests:
            arrow = " → ".join(p for p in (self.source, ", ".join(self.dests)) if p)
            if arrow:
                segs.append(arrow)
        if self.file_count:
            segs.append(f"{self.file_count} files")
        if self.bytes_label:
            segs.append(self.bytes_label)
        if self.verdict:
            segs.append(self.verdict)
        return " · ".join(s for s in segs if s)

    def details_text(self) -> str:
        """Render only the middle segments for the History table's Details
        column — source → dests · N files · size. When/Workstation/Operation/
        Verdict each have their own column, so they're omitted here to avoid
        repeating every field in one cell.
        """
        segs = []
        if self.source or self.dests:
            arrow = " → ".join(p for p in (self.source, ", ".join(self.dests)) if p)
            if arrow:
                segs.append(arrow)
        if self.file_count:
            segs.append(f"{self.file_count} files")
        if self.bytes_label:
            segs.append(self.bytes_label)
        return " · ".join(segs)


def _date_label(timestamp: str) -> str:
    """ISO timestamp to a short "Jun 12" label; falls back to the raw string."""
    try:
        dt = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return timestamp or ""
    return f"{dt:%b} {dt.day}"


def format_row(record: dict) -> HistoryRow:
    """Turn a merged activity record dict into a :class:`HistoryRow`."""
    op = (record.get("operation") or "").strip()
    return HistoryRow(
        date_label=_date_label(record.get("timestamp", "")),
        workstation=record.get("workstation", ""),
        operation_label=op[:1].upper() + op[1:] if op else "",
        source=record.get("source", ""),
        dests=list(record.get("dests") or []),
        file_count=int(record.get("file_count") or 0),
        bytes=int(record.get("bytes") or 0),
        verdict=record.get("verdict", ""),
        log_filename=record.get("log_filename", ""),
        timestamp=record.get("timestamp", ""),
    )


def distinct_values(records: list, field_name: str) -> list:
    """Sorted distinct non-empty values of a field, for populating a dropdown."""
    seen = {r.get(field_name) for r in records if r.get(field_name)}
    return sorted(seen)


def filter_by_date(records: list, *, start: Optional[date] = None,
                   end: Optional[date] = None) -> list:
    """Keep records whose timestamp date is within [start, end] inclusive.

    `start`/`end` are `date` objects (or None for an open end). A record with a
    missing or unparseable timestamp is dropped only when a bound is active.
    """
    if start is None and end is None:
        return list(records)
    out = []
    for r in records:
        ts = r.get("timestamp", "")
        try:
            d = datetime.fromisoformat(ts).date()
        except (ValueError, TypeError):
            continue
        if start is not None and d < start:
            continue
        if end is not None and d > end:
            continue
        out.append(r)
    return out


def query_history(records: list, *, operation: Optional[str] = None,
                  workstation: Optional[str] = None, user: Optional[str] = None,
                  project: Optional[str] = None, start: Optional[date] = None,
                  end: Optional[date] = None, newest_first: bool = True) -> list:
    """Filter merged records by field constraints and a date range, then sort."""
    out = activity_index.filter_records(
        records, operation=operation, workstation=workstation,
        user=user, project=project,
    )
    out = filter_by_date(out, start=start, end=end)
    out = sorted(out, key=lambda r: r.get("timestamp", ""), reverse=newest_first)
    return out


def rows_for(records: list, **query_kwargs) -> list:
    """Convenience: :func:`query_history` then :func:`format_row` for each result."""
    return [format_row(r) for r in query_history(records, **query_kwargs)]


def staleness_warning(records: list, *, now: Optional[datetime] = None) -> Optional[str]:
    """A one-line org-health warning naming workstations that have not reported in
    a while (>= STALE_AFTER_DAYS), or None when every machine is current.

    This is the headline payoff of the merged shards: an office producer can see
    at a glance whether any cart has stopped backing up.
    """
    stale = [s for s in activity_index.staleness(records, now=now) if s.stale]
    if not stale:
        return None
    parts = []
    for s in stale:
        try:
            d = datetime.fromisoformat(s.last_reported)
            when = f"{d:%b} {d.day}"
        except (ValueError, TypeError):
            when = s.last_reported or "unknown"
        parts.append(f"{s.workstation} (last reported {when})")
    noun = "machine has" if len(stale) == 1 else "machines have"
    return f"⚠ {len(stale)} {noun} not reported recently: " + ", ".join(parts)
