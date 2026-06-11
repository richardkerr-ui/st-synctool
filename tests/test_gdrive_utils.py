"""Tests for utils/gdrive_utils.py — is_gdrive_url and gdrive_url_to_rclone.

These functions have 20 and 12 callers respectively and had zero test coverage.
"""

import pytest
from unittest.mock import patch

import utils.gdrive_utils as gu


# ── is_gdrive_url ─────────────────────────────────────────────────────────────

class TestIsGdriveUrl:
    def test_standard_folders_url(self):
        assert gu.is_gdrive_url(
            "https://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I"
        )

    def test_user_scoped_folders_url(self):
        assert gu.is_gdrive_url(
            "https://drive.google.com/drive/u/0/folders/1A2B3C4D5E6F7G8H9I"
        )

    def test_open_id_url(self):
        assert gu.is_gdrive_url(
            "https://drive.google.com/open?id=1A2B3C4D5E6F7G8H9I"
        )

    def test_local_absolute_path_is_not_gdrive(self):
        assert not gu.is_gdrive_url("/Volumes/NAS/project")

    def test_local_relative_path_is_not_gdrive(self):
        assert not gu.is_gdrive_url("relative/path/to/folder")

    def test_empty_string_is_not_gdrive(self):
        assert not gu.is_gdrive_url("")

    def test_none_equivalent_empty(self):
        # Function guards on falsy input
        assert not gu.is_gdrive_url("")

    def test_partial_url_no_match(self):
        assert not gu.is_gdrive_url("drive.google.com/drive/folders/abc")

    def test_http_not_https_no_match(self):
        # Patterns require https://
        assert not gu.is_gdrive_url(
            "http://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I"
        )

    def test_folder_id_with_hyphens_and_underscores(self):
        assert gu.is_gdrive_url(
            "https://drive.google.com/drive/folders/1a-B_C2D3E4F5G6H7I8"
        )

    def test_u_1_scoped_url(self):
        assert gu.is_gdrive_url(
            "https://drive.google.com/drive/u/1/folders/XYZ123"
        )


# ── gdrive_url_to_rclone ──────────────────────────────────────────────────────

class TestGdriveUrlToRclone:
    @pytest.fixture(autouse=True)
    def patch_remote(self, monkeypatch):
        """Pin RCLONE_REMOTE so tests are not affected by the test environment."""
        monkeypatch.setattr(gu, "RCLONE_REMOTE", "gdrive")

    def test_standard_url_returns_remote_colon(self):
        remote, flags = gu.gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/FOLDER_ID_123"
        )
        assert remote == "gdrive:"

    def test_standard_url_returns_drive_root_flag(self):
        _, flags = gu.gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/FOLDER_ID_123"
        )
        assert "--drive-root-folder-id" in flags
        assert "FOLDER_ID_123" in flags

    def test_open_id_url(self):
        remote, flags = gu.gdrive_url_to_rclone(
            "https://drive.google.com/open?id=OPEN_ID_456"
        )
        assert remote == "gdrive:"
        assert "OPEN_ID_456" in flags

    def test_user_scoped_url(self):
        _, flags = gu.gdrive_url_to_rclone(
            "https://drive.google.com/drive/u/0/folders/U0_FOLDER_ID"
        )
        assert "U0_FOLDER_ID" in flags

    def test_non_gdrive_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Not a recognizable Google Drive folder URL"):
            gu.gdrive_url_to_rclone("/local/path")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            gu.gdrive_url_to_rclone("")

    def test_flags_list_has_exactly_two_items(self):
        # Expected format: ["--drive-root-folder-id", "<id>"]
        _, flags = gu.gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/ABC123"
        )
        assert len(flags) == 2
        assert flags[0] == "--drive-root-folder-id"
        assert flags[1] == "ABC123"

    def test_custom_remote_name_reflected(self, monkeypatch):
        monkeypatch.setattr(gu, "RCLONE_REMOTE", "my_drive")
        remote, _ = gu.gdrive_url_to_rclone(
            "https://drive.google.com/drive/folders/ABC123"
        )
        assert remote == "my_drive:"
