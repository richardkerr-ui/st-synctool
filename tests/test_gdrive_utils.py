"""Tests for utils/gdrive_utils.py — is_gdrive_url and gdrive_url_to_rclone.

These functions have 20 and 12 callers respectively and had zero test coverage.
"""

import pytest
from unittest.mock import patch

import utils.gdrive_utils as gu
from utils.gdrive_utils import is_gdrive_url, parse_gdrive_id, gdrive_url_to_rclone


# ── is_gdrive_url ─────────────────────────────────────────────────────────────

class TestIsGdriveUrl:
    def test_standard_folders_url(self):
        assert is_gdrive_url(
            "https://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I"
        )

    def test_user_scoped_folders_url(self):
        assert is_gdrive_url(
            "https://drive.google.com/drive/u/0/folders/1A2B3C4D5E6F7G8H9I"
        )

    def test_open_id_url(self):
        assert is_gdrive_url(
            "https://drive.google.com/open?id=1A2B3C4D5E6F7G8H9I"
        )

    def test_local_absolute_path_is_not_gdrive(self):
        assert not is_gdrive_url("/Volumes/NAS/project")

    def test_local_relative_path_is_not_gdrive(self):
        assert not is_gdrive_url("relative/path/to/folder")

    def test_empty_string_is_not_gdrive(self):
        assert not is_gdrive_url("")

    def test_none_equivalent_empty(self):
        # Function guards on falsy input
        assert not is_gdrive_url("")

    def test_partial_url_no_match(self):
        assert not is_gdrive_url("drive.google.com/drive/folders/abc")

    def test_http_not_https_no_match(self):
        # Patterns require https://
        assert not is_gdrive_url(
            "http://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I"
        )

    def test_folder_id_with_hyphens_and_underscores(self):
        assert is_gdrive_url(
            "https://drive.google.com/drive/folders/1a-B_C2D3E4F5G6H7I8"
        )

    def test_u_1_scoped_url(self):
        assert is_gdrive_url(
            "https://drive.google.com/drive/u/1/folders/XYZ123"
        )


# ── gdrive_url_to_rclone ──────────────────────────────────────────────────────

class TestGdriveUrlToRclone:
    @pytest.fixture(autouse=True)
    def patch_remote(self, monkeypatch):
        """Pin RCLONE_REMOTE so tests are not affected by the test environment."""
        monkeypatch.setattr(gu, "RCLONE_REMOTE", "gdrive")

    def test_standard_url_returns_remote_colon(self):
        remote, flags = gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/FOLDER_ID_123"
        )
        assert remote == "gdrive:"

    def test_standard_url_returns_drive_root_flag(self):
        _, flags = gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/FOLDER_ID_123"
        )
        assert "--drive-root-folder-id" in flags
        assert "FOLDER_ID_123" in flags

    def test_open_id_url(self):
        remote, flags = gdrive_url_to_rclone(
            "https://drive.google.com/open?id=OPEN_ID_456"
        )
        assert remote == "gdrive:"
        assert "OPEN_ID_456" in flags

    def test_user_scoped_url(self):
        _, flags = gdrive_url_to_rclone(
            "https://drive.google.com/drive/u/0/folders/U0_FOLDER_ID"
        )
        assert "U0_FOLDER_ID" in flags

    def test_non_gdrive_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Not a recognizable Google Drive folder URL"):
            gdrive_url_to_rclone("/local/path")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            gdrive_url_to_rclone("")

    def test_flags_list_has_exactly_two_items(self):
        # Expected format: ["--drive-root-folder-id", "<id>"]
        _, flags = gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/ABC123"
        )
        assert len(flags) == 2
        assert flags[0] == "--drive-root-folder-id"
        assert flags[1] == "ABC123"

    def test_custom_remote_name_reflected(self, monkeypatch):
        monkeypatch.setattr(gu, "RCLONE_REMOTE", "my_drive")
        remote, _ = gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/ABC123"
        )
        assert remote == "my_drive:"


# ---------------------------------------------------------------------------
# M1.5 coverage top-up: remote detection and clipboard helpers
# ---------------------------------------------------------------------------

import json as _json
from types import SimpleNamespace as _NS

from utils.gdrive_utils import _detect_rclone_remote, get_rclone_remote, get_clipboard_gdrive_url


class TestDetectRcloneRemote:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ST_SYNC_RCLONE_REMOTE", raising=False)
        monkeypatch.setattr("utils.gdrive_utils.Path.home", lambda: tmp_path)

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("ST_SYNC_RCLONE_REMOTE", "enviro:")
        assert _detect_rclone_remote() == "enviro"

    def test_saved_config_used(self, tmp_path):
        cfg = tmp_path / ".config" / "st_synctool" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(_json.dumps({"active_remote": "saved_remote"}))
        assert _detect_rclone_remote() == "saved_remote"

    def test_corrupt_config_falls_through_to_listremotes(self, tmp_path):
        cfg = tmp_path / ".config" / "st_synctool" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{broken")
        with patch("utils.gdrive_utils.subprocess.run",
                   return_value=_NS(stdout="gdrive:\nother:\n")):
            assert _detect_rclone_remote() == "gdrive"

    def test_prefers_gdrive_among_remotes(self):
        with patch("utils.gdrive_utils.subprocess.run",
                   return_value=_NS(stdout="other:\ngdrive:\n")):
            assert _detect_rclone_remote() == "gdrive"

    def test_first_remote_when_no_gdrive(self):
        with patch("utils.gdrive_utils.subprocess.run",
                   return_value=_NS(stdout="teamdrive:\nbackup:\n")):
            assert _detect_rclone_remote() == "teamdrive"

    def test_default_when_rclone_missing(self):
        with patch("utils.gdrive_utils.subprocess.run", side_effect=OSError):
            assert _detect_rclone_remote() == "gdrive"

    def test_default_when_no_remotes(self):
        with patch("utils.gdrive_utils.subprocess.run", return_value=_NS(stdout="")):
            assert _detect_rclone_remote() == "gdrive"


class TestGetRcloneRemote:
    def test_delegates_to_active_remote(self, monkeypatch):
        monkeypatch.setattr("core.oauth_config.get_active_remote", lambda: "live_remote")
        assert get_rclone_remote() == "live_remote"


class TestGetClipboardGdriveUrl:
    def test_returns_url_when_clipboard_has_one(self):
        url = "https://drive.google.com/drive/folders/abc123"
        with patch("pyperclip.paste", return_value=f"  {url}  ".strip() and url):
            assert get_clipboard_gdrive_url() == url

    def test_none_for_non_drive_text(self):
        with patch("pyperclip.paste", return_value="hello world"):
            assert get_clipboard_gdrive_url() is None

    def test_none_when_clipboard_unavailable(self):
        with patch("pyperclip.paste", side_effect=RuntimeError):
            assert get_clipboard_gdrive_url() is None


# ---------------------------------------------------------------------------
# M3: connection-string helper for Drive-to-Drive
# ---------------------------------------------------------------------------

from utils.gdrive_utils import gdrive_url_to_connstr


class TestGdriveUrlToConnstr:
    def test_builds_connection_string(self, monkeypatch):
        monkeypatch.setattr(gu, "RCLONE_REMOTE", "gdrive")
        url = "https://drive.google.com/drive/folders/abc-123_X"
        assert gdrive_url_to_connstr(url) == "gdrive,root_folder_id=abc-123_X:"

    def test_custom_remote_name(self, monkeypatch):
        monkeypatch.setattr(gu, "RCLONE_REMOTE", "teamdrive")
        url = "https://drive.google.com/open?id=zzz999"
        assert gdrive_url_to_connstr(url) == "teamdrive,root_folder_id=zzz999:"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Not a recognizable"):
            gdrive_url_to_connstr("/local/path")
