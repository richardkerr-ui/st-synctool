"""M5.3 — Scheduled monthly verification of registered archive folders.

No daemon, no background app. The app installs (on request) a launchd *agent*
plist that wakes once a month, relaunches the app with a scheduled-verify flag,
runs a batch verify over every registered project, writes a report to
``~/Documents/STSyncTool/logs/`` and records the outcome in a small state file.
On the next normal launch the app reads that state and surfaces any failures in
a dismissible banner ("2 archives failed verification on June 1").

This module is headless and importable without PyQt6. The only macOS-specific
parts are the ``launchctl`` calls in install/uninstall; the subprocess runner is
injectable so they are fully testable.
"""

from __future__ import annotations

import json
import plistlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core import verify as _verify
from core import paths as _paths

LAUNCH_AGENT_LABEL = "com.signaltheory.stsynctool.scheduledverify"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
STATE_PATH = _paths.scheduled_verify_state_path()

# CLI flag the agent passes so the relaunched app knows to run a verify and quit.
SCHEDULED_VERIFY_FLAG = "--scheduled-verify"


# --------------------------------------------------------------------------- #
# launchd plist
# --------------------------------------------------------------------------- #

def plist_path(label: str = LAUNCH_AGENT_LABEL) -> Path:
    return LAUNCH_AGENTS_DIR / f"{label}.plist"


def build_launchd_plist(
    program_args: list,
    *,
    label: str = LAUNCH_AGENT_LABEL,
    day: int = 1,
    hour: int = 3,
    minute: int = 0,
) -> bytes:
    """Build a launchd agent plist (monthly StartCalendarInterval) as bytes.

    `program_args` is the full argv the agent runs (e.g. the app executable plus
    SCHEDULED_VERIFY_FLAG). Defaults wake at 03:00 on the 1st of each month.
    """
    if not program_args:
        raise ValueError("program_args must not be empty")
    spec = {
        "Label": label,
        "ProgramArguments": list(program_args),
        "StartCalendarInterval": {"Day": day, "Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "StandardOutPath": str(_verify.VERIFY_LOGS_DIR / "scheduled_verify.out.log"),
        "StandardErrorPath": str(_verify.VERIFY_LOGS_DIR / "scheduled_verify.err.log"),
    }
    return plistlib.dumps(spec)


def is_scheduled(label: str = LAUNCH_AGENT_LABEL) -> bool:
    """True if the agent plist is installed on disk."""
    return plist_path(label).exists()


def install_schedule(
    program_args: list,
    *,
    label: str = LAUNCH_AGENT_LABEL,
    day: int = 1,
    hour: int = 3,
    minute: int = 0,
    runner: Callable = subprocess.run,
) -> Path:
    """Write the agent plist and load it via launchctl. Returns the plist path.

    `runner` is injectable for tests (defaults to subprocess.run). A previously
    loaded agent is unloaded first so re-install is idempotent.
    """
    path = plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = build_launchd_plist(program_args, label=label, day=day, hour=hour, minute=minute)
    # Atomic write so a crash can't leave a half-written plist launchd might read.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    # Reload: unload any existing instance (ignore failure), then load.
    runner(["launchctl", "unload", str(path)], capture_output=True)
    runner(["launchctl", "load", str(path)], capture_output=True)
    return path


def uninstall_schedule(
    *,
    label: str = LAUNCH_AGENT_LABEL,
    runner: Callable = subprocess.run,
) -> bool:
    """Unload and remove the agent plist. Returns True if a plist was removed."""
    path = plist_path(label)
    if not path.exists():
        return False
    runner(["launchctl", "unload", str(path)], capture_output=True)
    path.unlink()
    return True


# --------------------------------------------------------------------------- #
# the scheduled run + state
# --------------------------------------------------------------------------- #

def _read_state(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _write_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def run_scheduled_verify(
    *,
    now: Optional[datetime] = None,
    projects: Optional[list] = None,
    state_path: Path = STATE_PATH,
    log_dir: Path = _verify.VERIFY_LOGS_DIR,
    pairs_fn: Optional[Callable] = None,
    batch_fn: Optional[Callable] = None,
    log_cb: Optional[_verify.LogCallback] = None,
) -> dict:
    """Run a batch verify over the registry, write a report, record state.

    Returns the new state dict. All collaborators are injectable for testing.
    Failures (FAIL or ERROR verdicts) are recorded so the next normal launch can
    surface them; the pending flag is only cleared by acknowledge_failures().
    """
    now = now or datetime.now()
    pairs_fn = pairs_fn or _verify.pairs_from_registry
    batch_fn = batch_fn or _verify.batch_verify

    pairs, skipped = pairs_fn(projects)
    summaries = batch_fn(pairs, log_cb=log_cb)

    # Persist the human-readable consolidated report.
    report_text = _verify.format_batch_report(summaries, skipped)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    report_path = Path(log_dir) / f"scheduled_verify_{now.strftime('%Y%m%d_%H%M%S')}.txt"
    report_path.write_text(report_text)

    failures = [
        {"label": s.label, "folder": s.folder, "verdict": s.verdict,
         "error": s.error}
        for s in summaries if s.verdict != "OK"
    ]
    state = {
        "last_run": now.isoformat(),
        "last_run_display": now.strftime("%B %-d, %Y"),
        "total": len(summaries),
        "ok": sum(1 for s in summaries if s.verdict == "OK"),
        "failed": sum(1 for s in summaries if s.verdict == "FAIL"),
        "error": sum(1 for s in summaries if s.verdict == "ERROR"),
        "skipped": len(skipped),
        "failures": failures,
        "report_path": str(report_path),
        "acknowledged": False,
    }
    _write_state(state_path, state)
    return state


def read_pending_failures(*, state_path: Path = STATE_PATH) -> Optional[dict]:
    """Return the last scheduled-verify state iff it has unacknowledged failures.

    Returns None when there is no state, all archives passed, or the failures
    have already been acknowledged — so the launch banner stays hidden.
    """
    state = _read_state(state_path)
    if not state or state.get("acknowledged"):
        return None
    if state.get("failed", 0) or state.get("error", 0):
        return state
    return None


def acknowledge_failures(*, state_path: Path = STATE_PATH) -> None:
    """Mark the recorded failures as seen so the banner won't show again."""
    state = _read_state(state_path)
    if state:
        state["acknowledged"] = True
        _write_state(state_path, state)


def format_failure_banner(state: dict) -> str:
    """Render the next-launch banner text from a scheduled-verify state dict."""
    n = state.get("failed", 0) + state.get("error", 0)
    when = state.get("last_run_display", "the last scheduled run")
    noun = "archive" if n == 1 else "archives"
    return f"{n} {noun} failed verification on {when}."
