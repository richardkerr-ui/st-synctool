"""Pure helper functions for manifest metadata — no Qt dependency."""

import json
from datetime import datetime, timezone
from pathlib import Path


def manifest_age_days_from_iso(iso_str: str) -> int:
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
    except Exception:
        return 0


def manifest_age_days(path: str) -> int:
    """Days since manifest was created (reads created_at field, falls back to mtime)."""
    try:
        data = json.loads(Path(path).read_text())
        return manifest_age_days_from_iso(data.get("created_at", ""))
    except Exception:
        pass
    try:
        return int((datetime.now().timestamp() - Path(path).stat().st_mtime) / 86400)
    except Exception:
        return 0


def fmt_date(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        return datetime.fromisoformat(iso_str).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:16]


def fmt_size(size_bytes) -> str:
    """Human-readable file size string (e.g. 1.2 GB, 340 MB, 4.0 KB)."""
    if size_bytes is None:
        return "unknown"
    try:
        n = int(size_bytes)
    except (TypeError, ValueError):
        return str(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"
