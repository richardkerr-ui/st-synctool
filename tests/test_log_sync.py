"""Tests for core/log_sync.py (M9.1 log shipping)."""

import json
from datetime import datetime, timedelta

import pytest

from core import log_sync


@pytest.fixture
def base(tmp_path):
    """A fake ~/Documents/STSyncTool with a couple of log files."""
    (tmp_path / "offload_logs").mkdir()
    (tmp_path / "offload_logs" / "offload_20260612_ab12.txt").write_text("log a")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "verify_20260612.txt").write_text("verify log")
    (tmp_path / "manifests" / "ProjX").mkdir(parents=True)
    (tmp_path / "manifests" / "ProjX" / "st_manifest_20260612.json").write_text("{}")
    return tmp_path


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "ledger.json"


def _collecting_copy():
    calls = []
    def copy(local, remote):
        calls.append((local, remote))
    return copy, calls


# ── enumeration / new-file detection ─────────────────────────────────────────

def test_enumerate_finds_all_files(base):
    found = {rel for rel, _abs, _size in log_sync.enumerate_shippable(base)}
    assert found == {
        "offload_logs/offload_20260612_ab12.txt",
        "logs/verify_20260612.txt",
        "manifests/ProjX/st_manifest_20260612.json",
    }


def test_pending_excludes_already_shipped(base, ledger_path):
    copy, calls = _collecting_copy()
    log_sync.ship_logs("gdrive:Activity", base_dir=base, ledger_path=ledger_path,
                       copy_fn=copy, workstation="cart3", user="rk")
    # All shipped now -> nothing pending on a second pass.
    assert log_sync.pending_files(base, log_sync._read_ledger(ledger_path)) == []


def test_new_file_after_ship_is_detected(base, ledger_path):
    copy, calls = _collecting_copy()
    log_sync.ship_logs("gdrive:Activity", base_dir=base, ledger_path=ledger_path,
                       copy_fn=copy, workstation="cart3", user="rk")
    (base / "logs" / "verify_20260613.txt").write_text("new")
    pend = log_sync.pending_files(base, log_sync._read_ledger(ledger_path))
    assert [r for r, _a, _s in pend] == ["logs/verify_20260613.txt"]


# ── shipping + ledger ────────────────────────────────────────────────────────

def test_ship_copies_to_namespaced_remote(base, ledger_path):
    copy, calls = _collecting_copy()
    res = log_sync.ship_logs("gdrive:Activity/", base_dir=base, ledger_path=ledger_path,
                             copy_fn=copy, workstation="cart3", user="rk")
    assert res.shipped == 3 and res.failed == 0 and res.all_clear
    remotes = {r for _l, r in calls}
    assert "gdrive:Activity/cart3/rk/logs/verify_20260612.txt" in remotes
    # ledger persisted
    led = json.loads(ledger_path.read_text())
    assert len(led["shipped"]) == 3


def test_ship_is_idempotent(base, ledger_path):
    copy, calls = _collecting_copy()
    log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path, copy_fn=copy,
                       workstation="c", user="u")
    second = log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                                copy_fn=copy, workstation="c", user="u")
    assert second.shipped == 0           # nothing new
    assert len(calls) == 3               # not re-copied


def test_ship_never_deletes(base, ledger_path):
    # The only verb available to ship_logs is the injected copy. Prove it issues
    # copies only and the local files all still exist afterwards.
    copy, calls = _collecting_copy()
    log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path, copy_fn=copy,
                       workstation="c", user="u")
    assert all((base / rel).exists()
               for rel, _a, _s in log_sync.enumerate_shippable(base))
    assert len(calls) == 3  # 3 copies, nothing else


# ── offline retry ────────────────────────────────────────────────────────────

def test_failed_ship_retries_next_time(base, ledger_path):
    # Simulate offline: copy raises for everything.
    def offline(local, remote):
        raise RuntimeError("no network")
    res = log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                             copy_fn=offline, workstation="c", user="u")
    assert res.shipped == 0 and res.failed == 3 and res.pending == 3
    assert not res.all_clear

    # Back online: a real copy ships everything.
    copy, calls = _collecting_copy()
    res2 = log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                              copy_fn=copy, workstation="c", user="u")
    assert res2.shipped == 3 and res2.all_clear


def test_copy_exception_never_propagates(base, ledger_path):
    def boom(local, remote):
        raise RuntimeError("kaboom")
    # Must not raise.
    res = log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                             copy_fn=boom, workstation="c", user="u")
    assert res.failed == 3


def test_partial_failure_ships_the_rest(base, ledger_path):
    def flaky(local, remote):
        if "verify" in local:
            raise RuntimeError("flaky")
    res = log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                             copy_fn=flaky, workstation="c", user="u")
    assert res.shipped == 2 and res.failed == 1


# ── pending status + 7-day banner ────────────────────────────────────────────

def test_pending_status_clear(base, ledger_path):
    copy, _ = _collecting_copy()
    log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path, copy_fn=copy,
                       workstation="c", user="u")
    st = log_sync.pending_status(base, ledger_path=ledger_path)
    assert st.count == 0
    assert st.status_line() is None
    assert st.banner() is None


def test_pending_status_line(base, ledger_path):
    def offline(local, remote):
        raise RuntimeError("offline")
    day0 = datetime(2026, 6, 1, 9, 0)
    log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                       copy_fn=offline, workstation="c", user="u", now=day0)
    st = log_sync.pending_status(base, ledger_path=ledger_path, now=day0)
    assert st.count == 3
    assert "3 reports waiting" in st.status_line()
    assert st.escalate is False
    assert st.banner() is None


def test_pending_banner_after_7_days(base, ledger_path):
    def offline(local, remote):
        raise RuntimeError("offline")
    day0 = datetime(2026, 6, 1, 9, 0)
    log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                       copy_fn=offline, workstation="c", user="u", now=day0)
    later = day0 + timedelta(days=7)
    st = log_sync.pending_status(base, ledger_path=ledger_path, now=later)
    assert st.escalate is True
    assert st.oldest_age_days >= 7
    assert "7+ days" in st.banner()


def test_pending_since_cleared_after_ship(base, ledger_path):
    def offline(local, remote):
        raise RuntimeError("offline")
    day0 = datetime(2026, 6, 1)
    log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                       copy_fn=offline, workstation="c", user="u", now=day0)
    assert json.loads(ledger_path.read_text())["pending_since"]
    copy, _ = _collecting_copy()
    log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path, copy_fn=copy,
                       workstation="c", user="u", now=day0)
    assert json.loads(ledger_path.read_text())["pending_since"] == {}


def test_corrupt_ledger_recovered(base, ledger_path):
    ledger_path.write_text("{ not json")
    copy, _ = _collecting_copy()
    res = log_sync.ship_logs("r:", base_dir=base, ledger_path=ledger_path,
                             copy_fn=copy, workstation="c", user="u")
    assert res.shipped == 3


# --------------------------------------------------------------------------- #
# M9.1 trigger: ship_if_configured (settings-aware)
# --------------------------------------------------------------------------- #

def test_ship_if_configured_noop_when_not_configured(tmp_path):
    calls = []
    out = log_sync.ship_if_configured(base_dir=tmp_path, ledger_path=tmp_path / "l.json",
                                copy_fn=lambda a, b: calls.append((a, b)),
                                configured=False)
    assert out is None
    assert calls == []


def test_ship_if_configured_noop_when_base_empty(tmp_path):
    calls = []
    out = log_sync.ship_if_configured(base_dir=tmp_path, ledger_path=tmp_path / "l.json",
                                copy_fn=lambda a, b: calls.append((a, b)),
                                configured=True, remote_base="")
    assert out is None
    assert calls == []


def test_ship_if_configured_ships_when_configured(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "custody.txt").write_text("x")
    calls = []
    out = log_sync.ship_if_configured(base_dir=tmp_path, ledger_path=tmp_path / "l.json",
                                copy_fn=lambda a, b: calls.append((a, b)),
                                configured=True, remote_base="gdrive:Acts")
    assert out is not None and out.shipped == 1
    assert len(calls) == 1
    assert calls[0][1].startswith("gdrive:Acts/")


def test_ship_if_configured_reads_settings(tmp_path, monkeypatch):
    from core import settings
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", cfg)
    settings.set_activity_remote_base("gdrive:FromSettings", path=cfg)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "c.txt").write_text("x")
    calls = []
    out = log_sync.ship_if_configured(base_dir=tmp_path, ledger_path=tmp_path / "l.json",
                                copy_fn=lambda a, b: calls.append((a, b)))
    assert out is not None and out.shipped == 1
    assert calls[0][1].startswith("gdrive:FromSettings/")
