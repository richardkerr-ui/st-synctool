"""Tests for the application settings store (core/settings.py)."""

import json

import pytest

from core import settings


@pytest.fixture
def cfg(tmp_path):
    return tmp_path / "config.json"


def test_load_defaults_when_missing(cfg):
    s = settings.load_settings(cfg)
    assert s["active_remote"] == "gdrive"
    assert s["activity_remote_base"] == ""
    assert s["log_shipping_enabled"] is True


def test_load_merges_file_over_defaults(cfg):
    cfg.write_text(json.dumps({"activity_remote_base": "gdrive:Acts"}))
    s = settings.load_settings(cfg)
    assert s["activity_remote_base"] == "gdrive:Acts"
    assert s["active_remote"] == "gdrive"  # default still present


def test_load_tolerates_corrupt_file(cfg):
    cfg.write_text("{not json")
    s = settings.load_settings(cfg)
    assert s == settings.DEFAULTS


def test_set_setting_atomic_and_merges(cfg):
    settings.set_setting("active_remote", "work", path=cfg)
    settings.set_setting("activity_remote_base", "gdrive:Acts", path=cfg)
    s = settings.load_settings(cfg)
    assert s["active_remote"] == "work"
    assert s["activity_remote_base"] == "gdrive:Acts"
    assert not (cfg.parent / "config.json.tmp").exists()


def test_set_setting_preserves_unknown_keys(cfg):
    cfg.write_text(json.dumps({"some_other_writer_key": 42}))
    settings.set_setting("activity_remote_base", "gdrive:Acts", path=cfg)
    s = json.loads(cfg.read_text())
    assert s["some_other_writer_key"] == 42
    assert s["activity_remote_base"] == "gdrive:Acts"


def test_get_setting_falls_back_to_default(cfg):
    assert settings.get_setting("log_shipping_enabled", path=cfg) is True
    assert settings.get_setting("activity_remote_base", path=cfg) == ""


def test_env_override_wins(cfg, monkeypatch):
    cfg.write_text(json.dumps({"activity_remote_base": "gdrive:FromFile"}))
    monkeypatch.setenv("ST_SYNC_ACTIVITY_REMOTE", "gdrive:FromEnv")
    assert settings.get_setting("activity_remote_base", path=cfg) == "gdrive:FromEnv"


def test_env_override_active_remote_strips_colon(cfg, monkeypatch):
    monkeypatch.setenv("ST_SYNC_RCLONE_REMOTE", "work:")
    assert settings.get_setting("active_remote", path=cfg) == "work"


def test_get_setting_explicit_default_for_unknown_key(cfg):
    assert settings.get_setting("no_such_key", "fallback", path=cfg) == "fallback"


def test_set_setting_recovers_from_corrupt_file(cfg):
    cfg.write_text("{garbage")
    settings.set_setting("activity_remote_base", "gdrive:Acts", path=cfg)
    s = json.loads(cfg.read_text())
    assert s == {"activity_remote_base": "gdrive:Acts"}


# --------------------------------------------------------------------------- #
# typed accessors
# --------------------------------------------------------------------------- #

def test_activity_remote_base_accessors(cfg):
    assert settings.activity_remote_base(path=cfg) == ""
    settings.set_activity_remote_base("  gdrive:Acts  ", path=cfg)
    assert settings.activity_remote_base(path=cfg) == "gdrive:Acts"  # trimmed


def test_log_shipping_toggle(cfg):
    assert settings.log_shipping_enabled(path=cfg) is True
    settings.set_log_shipping_enabled(False, path=cfg)
    assert settings.log_shipping_enabled(path=cfg) is False


def test_activity_log_configured_requires_base_and_toggle(cfg):
    assert settings.activity_log_configured(path=cfg) is False  # no base
    settings.set_activity_remote_base("gdrive:Acts", path=cfg)
    assert settings.activity_log_configured(path=cfg) is True
    settings.set_log_shipping_enabled(False, path=cfg)
    assert settings.activity_log_configured(path=cfg) is False  # opted out
