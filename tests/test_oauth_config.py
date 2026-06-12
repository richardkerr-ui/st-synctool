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


# ---------------------------------------------------------------------------
# M1.5 coverage top-up: rclone-facing helpers (subprocess + requests mocked)
# ---------------------------------------------------------------------------

from types import SimpleNamespace
from unittest.mock import MagicMock, patch as _patch

import core.oauth_config as oc
from core.oauth_config import (
    get_remote_account_email, is_remote_using_default_rclone_creds,
    list_drive_remotes, save_active_remote,
)


def _proc(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


class TestListDriveRemotes:
    def test_filters_to_drive_type(self):
        def fake_run(args, **kw):
            if args[:2] == ["rclone", "listremotes"]:
                return _proc("gdrive:\ns3backup:\n")
            if args[-1] == "gdrive":
                return _proc("[gdrive]\ntype = drive\n")
            return _proc("[s3backup]\ntype = s3\n")
        with _patch("core.oauth_config.subprocess.run", side_effect=fake_run):
            assert list_drive_remotes() == ["gdrive"]

    def test_listremotes_failure_returns_empty(self):
        with _patch("core.oauth_config.subprocess.run", side_effect=OSError):
            assert list_drive_remotes() == []

    def test_config_show_failure_skips_remote(self):
        def fake_run(args, **kw):
            if args[:2] == ["rclone", "listremotes"]:
                return _proc("gdrive:\n")
            raise OSError
        with _patch("core.oauth_config.subprocess.run", side_effect=fake_run):
            assert list_drive_remotes() == []


class TestGetRemoteAccountEmail:
    def test_returns_email_on_success(self):
        token = '{"access_token": "tok123"}'
        show = _proc(f"[gdrive]\ntype = drive\ntoken = {token}\n")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"email": "dit@signaltheory.com"}
        with _patch("core.oauth_config.subprocess.run", return_value=show), \
             _patch("requests.get", return_value=resp):
            assert get_remote_account_email("gdrive") == "dit@signaltheory.com"

    def test_no_token_line_returns_none(self):
        with _patch("core.oauth_config.subprocess.run",
                    return_value=_proc("[gdrive]\ntype = drive\n")):
            assert get_remote_account_email("gdrive") is None

    def test_bad_token_json_returns_none(self):
        with _patch("core.oauth_config.subprocess.run",
                    return_value=_proc("token = {broken\n")):
            assert get_remote_account_email("gdrive") is None

    def test_empty_access_token_returns_none(self):
        with _patch("core.oauth_config.subprocess.run",
                    return_value=_proc('token = {"access_token": ""}\n')):
            assert get_remote_account_email("gdrive") is None

    def test_http_error_returns_none(self):
        show = _proc('token = {"access_token": "tok"}\n')
        with _patch("core.oauth_config.subprocess.run", return_value=show), \
             _patch("requests.get", side_effect=ConnectionError):
            assert get_remote_account_email("gdrive") is None

    def test_subprocess_failure_returns_none(self):
        with _patch("core.oauth_config.subprocess.run", side_effect=OSError):
            assert get_remote_account_email("gdrive") is None


class TestSaveActiveRemote:
    def test_writes_active_remote(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", cfg)
        save_active_remote("teamdrive")
        assert json.loads(cfg.read_text())["active_remote"] == "teamdrive"

    def test_preserves_existing_keys(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text('{"other": 1}')
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", cfg)
        save_active_remote("gdrive")
        data = json.loads(cfg.read_text())
        assert data == {"other": 1, "active_remote": "gdrive"}

    def test_corrupt_existing_file_is_replaced(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text("{broken")
        monkeypatch.setattr(oc, "_APP_CONFIG_PATH", cfg)
        save_active_remote("gdrive")
        assert json.loads(cfg.read_text())["active_remote"] == "gdrive"


class TestIsRemoteUsingDefaultRcloneCreds:
    def test_custom_client_id_returns_false(self):
        with _patch("core.oauth_config.subprocess.run",
                    return_value=_proc("client_id = abc.apps.googleusercontent.com\n")):
            assert is_remote_using_default_rclone_creds("gdrive") is False

    def test_missing_client_id_returns_true(self):
        with _patch("core.oauth_config.subprocess.run",
                    return_value=_proc("[gdrive]\ntype = drive\n")):
            assert is_remote_using_default_rclone_creds("gdrive") is True

    def test_empty_client_id_returns_true(self):
        with _patch("core.oauth_config.subprocess.run",
                    return_value=_proc("client_id =\n")):
            assert is_remote_using_default_rclone_creds("gdrive") is True

    def test_nonzero_returncode_returns_none(self):
        with _patch("core.oauth_config.subprocess.run",
                    return_value=_proc("", returncode=1)):
            assert is_remote_using_default_rclone_creds("gdrive") is None

    def test_subprocess_failure_returns_none(self):
        with _patch("core.oauth_config.subprocess.run", side_effect=OSError):
            assert is_remote_using_default_rclone_creds("gdrive") is None
