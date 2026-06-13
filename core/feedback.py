"""M7.3 — "Report a problem" feedback bundle.

Collects recent logs from ``~/Documents/STSyncTool/`` plus the app version and
OS info into a single zip a beta tester can email. No network, no upload; the
GUI just shows the resulting file in Finder so the tester attaches it manually.

This module is headless and importable without PyQt6. The current time and the
base directory are injectable so the bundle is fully testable.
"""

from __future__ import annotations

import json
import platform
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core.version import __version__ as APP_VERSION

STSYNC_DIR = Path.home() / "Documents" / "STSyncTool"

# Only the human-readable evidence trails go in a feedback bundle. Manifests and
# the activity index can be large and are not needed to diagnose a tester report,
# so they are deliberately excluded to keep the zip emailable.
FEEDBACK_SUBDIRS = ("logs", "offload_logs")

# Logs older than this are unlikely to relate to the problem being reported and
# only bloat the bundle.
DEFAULT_MAX_AGE_DAYS = 14


@dataclass(frozen=True)
class FeedbackBundle:
    """Result of building a feedback zip."""
    path: Path
    file_count: int
    system_info: dict


def collect_system_info(now: Optional[datetime] = None) -> dict:
    """Return app version + OS/platform info embedded in every bundle."""
    when = now or datetime.now()
    return {
        "app_version": APP_VERSION,
        "generated_at": when.isoformat(timespec="seconds"),
        "os": platform.platform(),
        "os_version": platform.mac_ver()[0] or platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def system_info_text(info: dict) -> str:
    """Render the system info dict as a readable header block."""
    lines = ["ST SyncTool — feedback bundle", ""]
    labels = {
        "app_version": "App version",
        "generated_at": "Generated at",
        "os": "OS",
        "os_version": "OS version",
        "machine": "Machine",
        "python": "Python",
    }
    for key, label in labels.items():
        if key in info:
            lines.append(f"{label}: {info[key]}")
    return "\n".join(lines) + "\n"


def gather_recent_logs(
    base_dir: Path = STSYNC_DIR,
    now: Optional[datetime] = None,
    max_age_days: Optional[int] = DEFAULT_MAX_AGE_DAYS,
    subdirs=FEEDBACK_SUBDIRS,
) -> list:
    """Return ``(relpath, abspath)`` for log files to include.

    ``relpath`` is POSIX, relative to ``base_dir``, so it maps onto the zip
    layout. Files modified more than ``max_age_days`` ago are skipped
    (``max_age_days=None`` keeps everything). Missing subdirs are ignored.
    """
    base = Path(base_dir)
    when = now or datetime.now()
    cutoff = None
    if max_age_days is not None:
        cutoff = when.timestamp() - max_age_days * 86400
    out = []
    for sub in subdirs:
        d = base / sub
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            if cutoff is not None and f.stat().st_mtime < cutoff:
                continue
            out.append((f.relative_to(base).as_posix(), f))
    return out


def build_feedback_zip(
    dest_path: Path,
    base_dir: Path = STSYNC_DIR,
    now: Optional[datetime] = None,
    max_age_days: Optional[int] = DEFAULT_MAX_AGE_DAYS,
    subdirs=FEEDBACK_SUBDIRS,
    info_fn: Callable[[Optional[datetime]], dict] = collect_system_info,
) -> FeedbackBundle:
    """Write a feedback zip to ``dest_path`` and return a :class:`FeedbackBundle`.

    The zip always contains a ``system_info.txt`` (version + OS info) at its
    root plus every recent log preserving its ``logs/`` / ``offload_logs/``
    relative path. Written atomically (tmp + rename).
    """
    dest = Path(dest_path)
    info = info_fn(now)
    logs = gather_recent_logs(base_dir, now=now, max_age_days=max_age_days, subdirs=subdirs)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("system_info.txt", system_info_text(info))
        zf.writestr("system_info.json", json.dumps(info, indent=2))
        for rel, abs_path in logs:
            zf.write(abs_path, rel)
    tmp.replace(dest)

    return FeedbackBundle(path=dest, file_count=len(logs), system_info=info)


def default_bundle_path(now: Optional[datetime] = None, base_dir: Path = STSYNC_DIR) -> Path:
    """Default location/name for a feedback bundle: timestamped, in the logs dir."""
    when = now or datetime.now()
    stamp = when.strftime("%Y%m%d_%H%M%S")
    return Path(base_dir) / "logs" / f"st_synctool_feedback_{stamp}.zip"
