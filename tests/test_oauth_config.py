"""Tests for core/oauth_config.py — get_oauth_credentials and get_active_remote.

Both functions have multiple callers and had zero test coverage.
They are pure config-reader logic: env var > file > default, with no
network or subprocess calls. All filesystem reads are redirected via
monkeypatch to avoid touching real ~/.config/st_synctool state.
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

import core.oauth_config as oc
from core.oauth_config import get_oauth_credentials, get_active_remote, save_oauth_credentials


# ---------------------------------------------------------------------------
# get_oauth_credentials
# ---------------------------------------------------------------------------

class TestGetOauthCredentials:
    def test_returns_defaults_when_nothing_configured(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_ID", raising=False)
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_SECRET", raising=False)
        monkeypatch.setattr(oc, "_CONFIG_PATH", tmp_path / "oauth.json")
        cid, csec = get_oauth_credentials()
        assert cid == oc._DEFAULT_CLIENT_ID
        assert csec == oc._DEFAULT_CLIENT_SECRET

    def test_env_vars_take_priority_over_file_and_defaults(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ST_SYNC_GDRIVE_CLIENT_ID", "env-id")
        monkeypatch.setenv("ST_SYNC_GDRIVE_CLIENT_SECRET", "env-secret")
        # Write a file too — env should win
        cfg = tmp_path / "oauth.json"
        cfg.write_text(json.dumps({"client_id": "file-id", "client_secret": "file-secret"}))
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        cid, csec = get_oauth_credentials()
        assert cid == "env-id"
        assert csec == "env-secret"

    def test_file_overrides_defaults_when_no_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_ID", raising=False)
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_SECRET", raising=False)
        cfg = tmp_path / "oauth.json"
        cfg.write_text(json.dumps({"client_id": "custom-id", "client_secret": "custom-secret"}))
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        cid, csec = get_oauth_credentials()
        assert cid == "custom-id"
        assert csec == "custom-secret"

    def test_falls_back_to_defaults_on_corrupt_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_ID", raising=False)
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_SECRET", raising=False)
        cfg = tmp_path / "oauth.json"
        cfg.write_text("not valid json {{{")
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        cid, csec = get_oauth_credentials()
        assert cid == oc._DEFAULT_CLIENT_ID
        assert csec == oc._DEFAULT_CLIENT_SECRET

    def test_falls_back_to_defaults_when_file_has_empty_values(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_ID", raising=False)
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_SECRET", raising=False)
        cfg = tmp_path / "oauth.json"
        cfg.write_text(json.dumps({"client_id": "", "client_secret": ""}))
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        cid, csec = get_oauth_credentials()
        assert cid == oc._DEFAULT_CLIENT_ID
        assert csec == oc._DEFAULT_CLIENT_SECRET

    def test_partial_env_both_must_be_set(self, monkeypatch, tmp_path):
        # Only one env var set — should fall through to file/default
        monkeypatch.setenv("ST_SYNC_GDRIVE_CLIENT_ID", "partial-id")
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_SECRET", raising=False)
        monkeypatch.setattr(oc, "_CONFIG_PATH", tmp_path / "oauth.json")
        cid, csec = get_oauth_credentials()
        assert cid == oc._DEFAULT_CLIENT_ID


# ---------------------------------------------------------------------------
# get_active_remote
# ---------------------------------------------------------------------------

class TestGetActiveRemote:
    def test_env_var_takes_priority(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ST_SYNC_RCLONE_REMOTE", "myremote")
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", tmp_path / "config.json")
        assert get_active_remote() == "myremote"

    def test_env_var_strips_trailing_colon(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ST_SYNC_RCLONE_REMOTE", "myremote:")
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", tmp_path / "config.json")
        assert get_active_remote() == "myremote"

    def test_config_file_used_when_no_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_RCLONE_REMOTE", raising=False)
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"active_remote": "work-remote"}))
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", cfg)
        assert get_active_remote() == "work-remote"

    def test_falls_back_to_gdrive_when_no_env_and_no_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_RCLONE_REMOTE", raising=False)
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", tmp_path / "config.json")
        assert get_active_remote() == "gdrive"

    def test_falls_back_to_gdrive_on_corrupt_config(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_RCLONE_REMOTE", raising=False)
        cfg = tmp_path / "config.json"
        cfg.write_text("{{bad json")
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", cfg)
        assert get_active_remote() == "gdrive"

    def test_falls_back_to_gdrive_when_active_remote_empty_in_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_RCLONE_REMOTE", raising=False)
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"active_remote": ""}))
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", cfg)
        assert get_active_remote() == "gdrive"


# ---------------------------------------------------------------------------
# save_oauth_credentials
# ---------------------------------------------------------------------------

class TestSaveOauthCredentials:
    def test_writes_json_file(self, monkeypatch, tmp_path):
        cfg = tmp_path / "oauth.json"
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        save_oauth_credentials("my-id", "my-secret")
        assert cfg.exists()
        data = json.loads(cfg.read_text())
        assert data["client_id"] == "my-id"
        assert data["client_secret"] == "my-secret"

    def test_creates_parent_directory(self, monkeypatch, tmp_path):
        cfg = tmp_path / "deep" / "nested" / "oauth.json"
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        save_oauth_credentials("id", "secret")
        assert cfg.exists()

    def test_file_permissions_are_600(self, monkeypatch, tmp_path):
        import stat
        cfg = tmp_path / "oauth.json"
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        save_oauth_credentials("id", "secret")
        mode = oct(stat.S_IMODE(cfg.stat().st_mode))
        assert mode == "0o600"

    def test_overwrites_existing_file(self, monkeypatch, tmp_path):
        cfg = tmp_path / "oauth.json"
        cfg.write_text(json.dumps({"client_id": "old", "client_secret": "old"}))
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        save_oauth_credentials("new-id", "new-secret")
        data = json.loads(cfg.read_text())
        assert data["client_id"] == "new-id"

    def test_round_trip_with_get(self, monkeypatch, tmp_path):
        cfg = tmp_path / "oauth.json"
        monkeypatch.setattr(oc, "_CONFIG_PATH", cfg)
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_ID", raising=False)
        monkeypatch.delenv("ST_SYNC_GDRIVE_CLIENT_SECRET", raising=False)
        save_oauth_credentials("rt-id", "rt-secret")
        cid, csec = get_oauth_credentials()
        assert cid == "rt-id"
        assert csec == "rt-secret"
