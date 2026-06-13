"""Tests for core/scheduled_verify.py (M5.3 scheduled verification)."""

import plistlib
from datetime import datetime

import pytest

from core import scheduled_verify as sv
from core.verify import ProjectVerifySummary


# --------------------------------------------------------------------------- #
# launchd plist
# --------------------------------------------------------------------------- #

def test_build_plist_monthly_schedule():
    data = sv.build_launchd_plist(["/Applications/STSyncTool.app/Contents/MacOS/app",
                                   sv.SCHEDULED_VERIFY_FLAG])
    spec = plistlib.loads(data)
    assert spec["Label"] == sv.LAUNCH_AGENT_LABEL
    assert spec["ProgramArguments"][-1] == sv.SCHEDULED_VERIFY_FLAG
    assert spec["StartCalendarInterval"] == {"Day": 1, "Hour": 3, "Minute": 0}
    assert spec["RunAtLoad"] is False


def test_build_plist_custom_time():
    data = sv.build_launchd_plist(["app"], day=15, hour=2, minute=30)
    spec = plistlib.loads(data)
    assert spec["StartCalendarInterval"] == {"Day": 15, "Hour": 2, "Minute": 30}


def test_build_plist_empty_args_raises():
    with pytest.raises(ValueError):
        sv.build_launchd_plist([])


def test_install_writes_plist_and_loads(tmp_path, monkeypatch):
    label = "com.test.sv"
    monkeypatch.setattr(sv, "LAUNCH_AGENTS_DIR", tmp_path)
    calls = []
    runner = lambda args, **k: calls.append(args)

    path = sv.install_schedule(["app", sv.SCHEDULED_VERIFY_FLAG], label=label, runner=runner)

    assert path == tmp_path / f"{label}.plist"
    assert path.exists()
    assert sv.is_scheduled(label)
    # unload (idempotent reinstall) then load
    assert calls[0][:2] == ["launchctl", "unload"]
    assert calls[1][:2] == ["launchctl", "load"]
    assert not path.with_suffix(".plist.tmp").exists()


def test_uninstall_removes_plist(tmp_path, monkeypatch):
    label = "com.test.sv"
    monkeypatch.setattr(sv, "LAUNCH_AGENTS_DIR", tmp_path)
    calls = []
    runner = lambda args, **k: calls.append(args)
    sv.install_schedule(["app"], label=label, runner=runner)

    assert sv.uninstall_schedule(label=label, runner=runner) is True
    assert not sv.is_scheduled(label)
    # uninstalling again is a no-op
    assert sv.uninstall_schedule(label=label, runner=runner) is False


# --------------------------------------------------------------------------- #
# the scheduled run + state
# --------------------------------------------------------------------------- #

def _summary(label, verdict):
    if verdict == "OK":
        return ProjectVerifySummary(label, f"/a/{label}", 10, 10, 0, 0, 0)
    if verdict == "FAIL":
        return ProjectVerifySummary(label, f"/a/{label}", 10, 8, 1, 1, 0)
    return ProjectVerifySummary(label, f"/a/{label}", 0, 0, 0, 0, 0, error="boom")


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "scheduled_verify_state.json"


def test_run_writes_report_and_state(tmp_path, state_path):
    now = datetime(2026, 6, 1, 3, 0, 0)
    summaries = [_summary("Good", "OK"), _summary("Bad", "FAIL"), _summary("Err", "ERROR")]
    state = sv.run_scheduled_verify(
        now=now,
        state_path=state_path,
        log_dir=tmp_path,
        pairs_fn=lambda projects: ([{"label": "x", "folder": "/a", "manifest": {}}], []),
        batch_fn=lambda pairs, **k: summaries,
    )
    assert state["total"] == 3
    assert state["ok"] == 1 and state["failed"] == 1 and state["error"] == 1
    assert state["acknowledged"] is False
    assert state["last_run_display"] == "June 1, 2026"
    assert {f["label"] for f in state["failures"]} == {"Bad", "Err"}
    # report file written
    report = tmp_path / "scheduled_verify_20260601_030000.txt"
    assert report.exists()
    assert "Batch Verification Report" in report.read_text()
    # state persisted and reloads
    assert state_path.exists()


def test_run_all_ok_no_pending(tmp_path, state_path):
    now = datetime(2026, 6, 1)
    sv.run_scheduled_verify(
        now=now, state_path=state_path, log_dir=tmp_path,
        pairs_fn=lambda projects: ([], []),
        batch_fn=lambda pairs, **k: [_summary("Good", "OK")],
    )
    assert sv.read_pending_failures(state_path=state_path) is None


def test_read_pending_failures(tmp_path, state_path):
    now = datetime(2026, 6, 1)
    sv.run_scheduled_verify(
        now=now, state_path=state_path, log_dir=tmp_path,
        pairs_fn=lambda projects: ([], []),
        batch_fn=lambda pairs, **k: [_summary("Bad", "FAIL"), _summary("Err", "ERROR")],
    )
    pending = sv.read_pending_failures(state_path=state_path)
    assert pending is not None
    assert pending["failed"] == 1 and pending["error"] == 1


def test_acknowledge_hides_banner(tmp_path, state_path):
    now = datetime(2026, 6, 1)
    sv.run_scheduled_verify(
        now=now, state_path=state_path, log_dir=tmp_path,
        pairs_fn=lambda projects: ([], []),
        batch_fn=lambda pairs, **k: [_summary("Bad", "FAIL")],
    )
    assert sv.read_pending_failures(state_path=state_path) is not None
    sv.acknowledge_failures(state_path=state_path)
    assert sv.read_pending_failures(state_path=state_path) is None


def test_read_pending_no_state(state_path):
    assert sv.read_pending_failures(state_path=state_path) is None


def test_read_pending_corrupt_state(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{ not json")
    assert sv.read_pending_failures(state_path=state_path) is None


def test_format_failure_banner_plural():
    state = {"failed": 1, "error": 1, "last_run_display": "June 1, 2026"}
    assert sv.format_failure_banner(state) == "2 archives failed verification on June 1, 2026."


def test_format_failure_banner_singular():
    state = {"failed": 1, "error": 0, "last_run_display": "June 1, 2026"}
    assert sv.format_failure_banner(state) == "1 archive failed verification on June 1, 2026."
