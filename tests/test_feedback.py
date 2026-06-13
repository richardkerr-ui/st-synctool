"""Tests for M7.3 feedback bundle (core/feedback.py)."""

import io
import json
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core import feedback
from core.version import __version__ as APP_VERSION


def _write(path: Path, text: str = "x", age_days: float = 0, now: datetime = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if age_days:
        when = (now or datetime.now()) - timedelta(days=age_days)
        ts = when.timestamp()
        os.utime(path, (ts, ts))


def test_collect_system_info_has_version_and_os():
    info = feedback.collect_system_info(now=datetime(2026, 6, 13, 10, 0, 0))
    assert info["app_version"] == APP_VERSION
    assert info["generated_at"] == "2026-06-13T10:00:00"
    assert info["os"]  # non-empty platform string
    assert info["python"]
    assert "machine" in info


def test_system_info_text_renders_labels():
    info = feedback.collect_system_info(now=datetime(2026, 6, 13))
    text = feedback.system_info_text(info)
    assert "App version:" in text
    assert APP_VERSION in text
    assert "OS:" in text


def test_gather_recent_logs_includes_subdirs(tmp_path):
    now = datetime(2026, 6, 13)
    _write(tmp_path / "Verify Reports" / "verify_1.txt")
    _write(tmp_path / "Offload Reports" / "custody_1.txt")
    _write(tmp_path / "manifests" / "m.json")  # not a feedback subdir
    logs = feedback.gather_recent_logs(tmp_path, now=now)
    rels = {r for r, _ in logs}
    assert rels == {"Verify Reports/verify_1.txt", "Offload Reports/custody_1.txt"}


def test_gather_recent_logs_skips_old(tmp_path):
    now = datetime(2026, 6, 13)
    _write(tmp_path / "Verify Reports" / "fresh.txt", age_days=1, now=now)
    _write(tmp_path / "Verify Reports" / "stale.txt", age_days=60, now=now)
    rels = {r for r, _ in feedback.gather_recent_logs(tmp_path, now=now, max_age_days=14)}
    assert rels == {"Verify Reports/fresh.txt"}


def test_gather_recent_logs_none_age_keeps_all(tmp_path):
    now = datetime(2026, 6, 13)
    _write(tmp_path / "Verify Reports" / "stale.txt", age_days=400, now=now)
    rels = {r for r, _ in feedback.gather_recent_logs(tmp_path, now=now, max_age_days=None)}
    assert rels == {"Verify Reports/stale.txt"}


def test_gather_recent_logs_missing_base_is_empty(tmp_path):
    assert feedback.gather_recent_logs(tmp_path / "nope") == []


def test_build_feedback_zip_contains_info_and_logs(tmp_path):
    now = datetime(2026, 6, 13, 9, 30, 0)
    _write(tmp_path / "Verify Reports" / "verify_1.txt", text="hello")
    _write(tmp_path / "Offload Reports" / "custody_1.txt", text="world")
    dest = tmp_path / "out" / "bundle.zip"

    bundle = feedback.build_feedback_zip(dest, base_dir=tmp_path, now=now)

    assert bundle.path == dest
    assert dest.exists()
    assert bundle.file_count == 2
    assert bundle.system_info["app_version"] == APP_VERSION

    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
        assert "system_info.txt" in names
        assert "system_info.json" in names
        assert "Verify Reports/verify_1.txt" in names
        assert "Offload Reports/custody_1.txt" in names
        assert zf.read("Verify Reports/verify_1.txt") == b"hello"
        si = json.loads(zf.read("system_info.json"))
        assert si["app_version"] == APP_VERSION
        assert "os" in si


def test_build_feedback_zip_no_logs_still_has_info(tmp_path):
    now = datetime(2026, 6, 13)
    dest = tmp_path / "bundle.zip"
    bundle = feedback.build_feedback_zip(dest, base_dir=tmp_path, now=now)
    assert bundle.file_count == 0
    with zipfile.ZipFile(dest) as zf:
        assert "system_info.txt" in zf.namelist()


def test_build_feedback_zip_atomic_no_tmp_left(tmp_path):
    now = datetime(2026, 6, 13)
    _write(tmp_path / "Verify Reports" / "a.txt")
    dest = tmp_path / "bundle.zip"
    feedback.build_feedback_zip(dest, base_dir=tmp_path, now=now)
    assert not (tmp_path / "bundle.zip.tmp").exists()


def test_default_bundle_path_timestamped(tmp_path):
    now = datetime(2026, 6, 13, 14, 5, 6)
    p = feedback.default_bundle_path(now=now, base_dir=tmp_path)
    assert p == tmp_path / "st_synctool_feedback_20260613_140506.zip"
