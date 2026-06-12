"""Tests for core/merge_logic.py — build_server_manifest routing."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_MANIFEST = {"files": {"a.txt": {"size": 10, "checksum": "abc"}}}


# ---------------------------------------------------------------------------
# GDrive path tests
# ---------------------------------------------------------------------------

class TestBuildServerManifestGDrive:
    """build_server_manifest routes GDrive URLs through rclone."""

    def test_gdrive_url_calls_lsjson_to_manifest(self):
        """A gdrive:// URL triggers rclone_bridge.lsjson_to_manifest."""
        with patch("core.merge_logic.is_gdrive_url", return_value=True) as mock_is, \
             patch("core.merge_logic.gdrive_url_to_rclone", return_value=("gdrive:Folder", ["--flag"])) as mock_url, \
             patch("core.merge_logic.rclone_bridge.lsjson_to_manifest", return_value=FAKE_MANIFEST) as mock_lsjson:

            from core.merge_logic import build_server_manifest
            result = build_server_manifest("https://drive.google.com/drive/folders/abc123")

        assert result is FAKE_MANIFEST
        mock_is.assert_called_once()
        mock_url.assert_called_once_with("https://drive.google.com/drive/folders/abc123")
        mock_lsjson.assert_called_once_with("gdrive:Folder", extra_flags=["--flag"], label="server")

    def test_gdrive_url_calls_log_cb(self):
        """log_cb is called when server is GDrive."""
        log_calls = []
        with patch("core.merge_logic.is_gdrive_url", return_value=True), \
             patch("core.merge_logic.gdrive_url_to_rclone", return_value=("gdrive:F", [])), \
             patch("core.merge_logic.rclone_bridge.lsjson_to_manifest", return_value=FAKE_MANIFEST):

            from core.merge_logic import build_server_manifest
            build_server_manifest("https://drive.google.com/x", log_cb=lambda msg, level: log_calls.append(msg))

        assert any("Google Drive" in c for c in log_calls)

    def test_gdrive_url_no_log_cb(self):
        """No error when log_cb is None for GDrive path."""
        with patch("core.merge_logic.is_gdrive_url", return_value=True), \
             patch("core.merge_logic.gdrive_url_to_rclone", return_value=("gdrive:F", [])), \
             patch("core.merge_logic.rclone_bridge.lsjson_to_manifest", return_value=FAKE_MANIFEST):

            from core.merge_logic import build_server_manifest
            result = build_server_manifest("https://drive.google.com/x")

        assert result is FAKE_MANIFEST


# ---------------------------------------------------------------------------
# Local path tests
# ---------------------------------------------------------------------------

class TestBuildServerManifestLocal:
    """build_server_manifest routes local paths through generate_manifest_fast."""

    def test_local_path_calls_generate_manifest_fast(self, tmp_path):
        """An existing local path triggers generate_manifest_fast."""
        with patch("core.merge_logic.is_gdrive_url", return_value=False), \
             patch("core.merge_logic.generate_manifest_fast", return_value=FAKE_MANIFEST) as mock_gen:

            from core.merge_logic import build_server_manifest
            result = build_server_manifest(str(tmp_path))

        assert result is FAKE_MANIFEST
        mock_gen.assert_called_once_with(
            tmp_path,
            base_manifest=None,
            label="server",
            progress_cb=None,
        )

    def test_local_path_passes_base_manifest(self, tmp_path):
        """base_manifest is forwarded to generate_manifest_fast."""
        base = {"files": {}}
        with patch("core.merge_logic.is_gdrive_url", return_value=False), \
             patch("core.merge_logic.generate_manifest_fast", return_value=FAKE_MANIFEST) as mock_gen:

            from core.merge_logic import build_server_manifest
            build_server_manifest(str(tmp_path), base_manifest=base)

        _, kwargs = mock_gen.call_args
        assert kwargs["base_manifest"] is base

    def test_local_path_passes_progress_cb(self, tmp_path):
        """progress_cb is forwarded to generate_manifest_fast."""
        cb = MagicMock()
        with patch("core.merge_logic.is_gdrive_url", return_value=False), \
             patch("core.merge_logic.generate_manifest_fast", return_value=FAKE_MANIFEST) as mock_gen:

            from core.merge_logic import build_server_manifest
            build_server_manifest(str(tmp_path), progress_cb=cb)

        _, kwargs = mock_gen.call_args
        assert kwargs["progress_cb"] is cb

    def test_local_path_calls_log_cb(self, tmp_path):
        """log_cb is called for local paths."""
        log_calls = []
        with patch("core.merge_logic.is_gdrive_url", return_value=False), \
             patch("core.merge_logic.generate_manifest_fast", return_value=FAKE_MANIFEST):

            from core.merge_logic import build_server_manifest
            build_server_manifest(str(tmp_path), log_cb=lambda msg, level: log_calls.append(msg))

        assert any(str(tmp_path) in c for c in log_calls)

    def test_local_path_no_log_cb(self, tmp_path):
        """No error when log_cb is None for local path."""
        with patch("core.merge_logic.is_gdrive_url", return_value=False), \
             patch("core.merge_logic.generate_manifest_fast", return_value=FAKE_MANIFEST):

            from core.merge_logic import build_server_manifest
            result = build_server_manifest(str(tmp_path))

        assert result is FAKE_MANIFEST

    def test_nonexistent_local_path_raises(self):
        """A non-existent local path raises RuntimeError."""
        with patch("core.merge_logic.is_gdrive_url", return_value=False):
            from core.merge_logic import build_server_manifest
            with pytest.raises(RuntimeError, match="does not exist"):
                build_server_manifest("/nonexistent/path/that/does/not/exist")
