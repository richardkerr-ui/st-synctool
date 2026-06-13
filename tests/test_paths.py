"""Tests for the on-disk layout (core/paths.py)."""

import os

from core import paths


def test_base_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    assert paths.base_dir() == tmp_path


def test_base_dir_default_without_env(monkeypatch):
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    from pathlib import Path
    assert paths.base_dir() == Path.home() / "Documents" / "STSyncTool"


def test_human_readable_subdirs(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    assert paths.offload_reports_dir() == tmp_path / "Offload Reports"
    assert paths.verify_reports_dir() == tmp_path / "Verify Reports"
    assert paths.transfer_reports_dir() == tmp_path / "Transfer Reports"
    assert paths.contact_sheets_dir() == tmp_path / "Contact Sheets"
    assert paths.manifests_dir() == tmp_path / "Manifests"


def test_internal_state_is_hidden(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    state = tmp_path / ".app-state"
    assert paths.app_state_dir() == state
    assert paths.activity_dir() == state / "activity"
    assert paths.activity_cache_dir() == state / "activity-cache"
    assert paths.upload_tally_path() == state / "upload_tally.json"
    assert paths.log_sync_ledger_path() == state / "log_sync_ledger.json"
    assert paths.scheduled_verify_state_path() == state / "scheduled_verify_state.json"


def test_ship_subdirs_cover_reports_manifests_activity():
    assert "Offload Reports" in paths.SHIP_SUBDIRS
    assert "Manifests" in paths.SHIP_SUBDIRS
    assert paths.ACTIVITY in paths.SHIP_SUBDIRS
    # Machine-only state is never shipped.
    assert ".app-state" not in paths.SHIP_SUBDIRS


def test_feedback_subdirs_are_reports_only():
    assert set(paths.FEEDBACK_SUBDIRS) == {"Offload Reports", "Verify Reports", "Transfer Reports"}
