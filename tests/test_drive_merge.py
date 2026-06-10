"""
Unit tests for the Drive-as-server Merge code path in core/merge_ops.py.

The server side of a Merge can be a Google Drive URL rather than a local path.
In that case push_file and pull_file route through rclone_bridge.copyto, and
delete_server routes through rclone_bridge.deletefile.

These tests use unittest.mock.patch on the rclone bridge so no real Drive
connection is required.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.merge_ops import (
    push_file,
    pull_file,
    delete_server,
    _dest_exists_remote,
)

# A realistic-looking Drive folder URL used throughout the tests
GDRIVE_URL = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWx"

# The rclone remote+path and flags returned by gdrive_url_to_rclone for that URL
RCLONE_REMOTE = "gdrive:"
RCLONE_FLAGS  = ["--drive-root-folder-id", "1AbCdEfGhIjKlMnOpQrStUvWx"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_rclone_convert():
    """Patch gdrive_url_to_rclone everywhere it is imported."""
    return patch(
        "core.merge_ops.gdrive_url_to_rclone",
        return_value=(RCLONE_REMOTE, RCLONE_FLAGS),
    )


# ---------------------------------------------------------------------------
# push_file — Drive server
# ---------------------------------------------------------------------------

class TestPushFileDrive:
    """push_file when server_root is a Google Drive URL."""

    def test_normal_push_calls_copyto_and_returns_truthy(self, tmp_path):
        """Happy path: local file exists, rclone succeeds."""
        local = tmp_path / "local"
        local.mkdir()
        (local / "clip.mov").write_bytes(b"footage")

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=False), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=True) as mock_copyto:

            result = push_file("clip.mov", local, GDRIVE_URL, preserve_on_overwrite=False)

        assert result
        mock_copyto.assert_called_once()
        # Destination must include the rclone remote path
        call_args = mock_copyto.call_args
        assert RCLONE_REMOTE in call_args[0][1]

    def test_normal_push_result_has_verified_true(self, tmp_path):
        local = tmp_path / "local"
        local.mkdir()
        (local / "scene.mov").write_bytes(b"data")

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=False), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=True):

            result = push_file("scene.mov", local, GDRIVE_URL, preserve_on_overwrite=False)

        assert result.get("verified") is True

    def test_rclone_failure_returns_false(self, tmp_path):
        """When rclone copyto fails, push_file must return False."""
        local = tmp_path / "local"
        local.mkdir()
        (local / "clip.mov").write_bytes(b"footage")

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=False), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=False):

            result = push_file("clip.mov", local, GDRIVE_URL, preserve_on_overwrite=False)

        assert result is False

    def test_preserve_on_overwrite_renames_destination(self, tmp_path):
        """
        When preserve_on_overwrite=True and the file already exists on Drive,
        the upload destination path must be renamed (not the original path).
        """
        local = tmp_path / "local"
        local.mkdir()
        (local / "clip.mov").write_bytes(b"new footage")

        # Simulate file already present on Drive
        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=True), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=True) as mock_copyto, \
             patch("core.merge_ops.getpass.getuser", return_value="richard.kerr"):

            result = push_file("clip.mov", local, GDRIVE_URL, preserve_on_overwrite=True)

        assert result
        # The renamed path must differ from the original
        renamed = result.get("renamed_to")
        assert renamed is not None
        assert renamed != "clip.mov"
        # The destination passed to copyto must use the renamed path
        dest_arg = mock_copyto.call_args[0][1]
        assert "clip.mov" not in dest_arg or renamed in dest_arg

    def test_missing_local_source_returns_false(self, tmp_path):
        """push_file must short-circuit when the local file does not exist."""
        local = tmp_path / "local"
        local.mkdir()

        with _patch_rclone_convert():
            result = push_file("ghost.mov", local, GDRIVE_URL, preserve_on_overwrite=False)

        assert result is False

    def test_log_cb_called_on_success(self, tmp_path):
        local = tmp_path / "local"
        local.mkdir()
        (local / "log_test.mov").write_bytes(b"data")
        log_calls = []

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=False), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=True):

            push_file("log_test.mov", local, GDRIVE_URL, preserve_on_overwrite=False,
                      log_cb=lambda m, l: log_calls.append((m, l)))

        assert log_calls, "log_cb was never called"

    def test_log_cb_called_on_rclone_failure(self, tmp_path):
        local = tmp_path / "local"
        local.mkdir()
        (local / "clip.mov").write_bytes(b"data")
        log_calls = []

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=False), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=False):

            push_file("clip.mov", local, GDRIVE_URL, preserve_on_overwrite=False,
                      log_cb=lambda m, l: log_calls.append((m, l)))

        error_calls = [c for c in log_calls if c[1] == "error"]
        assert error_calls, "No error-level log entry on rclone failure"


# ---------------------------------------------------------------------------
# pull_file — Drive server
# ---------------------------------------------------------------------------

class TestPullFileDrive:
    """pull_file when server_root is a Google Drive URL."""

    def test_normal_pull_calls_copyto_and_returns_truthy(self, tmp_path):
        local = tmp_path / "local"
        local.mkdir()

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=True) as mock_copyto:

            result = pull_file("clip.mov", local, GDRIVE_URL, preserve_on_overwrite=False)

        assert result
        mock_copyto.assert_called_once()
        # Source must include the rclone remote path
        src_arg = mock_copyto.call_args[0][0]
        assert RCLONE_REMOTE in src_arg

    def test_rclone_failure_returns_false(self, tmp_path):
        local = tmp_path / "local"
        local.mkdir()

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=False):

            result = pull_file("clip.mov", local, GDRIVE_URL, preserve_on_overwrite=False)

        assert result is False

    def test_preserve_on_overwrite_renames_destination(self, tmp_path):
        """
        When a local file already exists and preserve_on_overwrite=True,
        the downloaded file must be stored under a renamed path.
        """
        local = tmp_path / "local"
        local.mkdir()
        (local / "clip.mov").write_bytes(b"existing local")

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=True), \
             patch("core.merge_ops.getpass.getuser", return_value="alice.smith"):

            result = pull_file("clip.mov", local, GDRIVE_URL, preserve_on_overwrite=True)

        assert result
        renamed = result.get("renamed_to")
        assert renamed is not None
        assert renamed != "clip.mov"
        # Original local file must be untouched
        assert (local / "clip.mov").read_bytes() == b"existing local"

    def test_pull_creates_parent_directories(self, tmp_path):
        """Pulling a file in a sub-directory must create intermediate dirs."""
        local = tmp_path / "local"
        local.mkdir()

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.copyto", return_value=True):

            result = pull_file("subdir/clip.mov", local, GDRIVE_URL, preserve_on_overwrite=False)

        assert result
        assert (local / "subdir").exists()


# ---------------------------------------------------------------------------
# delete_server — Drive server
# ---------------------------------------------------------------------------

class TestDeleteServerDrive:
    """delete_server when server_root is a Google Drive URL."""

    def test_normal_delete_calls_deletefile_and_returns_true(self):
        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.deletefile", return_value=True) as mock_del:

            result = delete_server("clip.mov", GDRIVE_URL)

        assert result is True
        mock_del.assert_called_once()
        path_arg = mock_del.call_args[0][0]
        assert RCLONE_REMOTE in path_arg

    def test_rclone_deletefile_failure_returns_false(self):
        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.deletefile", return_value=False):

            result = delete_server("clip.mov", GDRIVE_URL)

        assert result is False

    def test_delete_passes_extra_flags_to_rclone(self):
        """deletefile must receive the drive-root-folder-id flags."""
        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.deletefile", return_value=True) as mock_del:

            delete_server("scene.mov", GDRIVE_URL)

        kwargs = mock_del.call_args[1] if mock_del.call_args[1] else {}
        # Flags could be in kwargs["extra_flags"] or positional
        call_repr = str(mock_del.call_args)
        assert "--drive-root-folder-id" in call_repr

    def test_log_cb_forwarded_on_success(self):
        log_calls = []

        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.deletefile", return_value=True):

            delete_server("clip.mov", GDRIVE_URL,
                          log_cb=lambda m, l: log_calls.append((m, l)))

        assert log_calls


# ---------------------------------------------------------------------------
# _dest_exists_remote — Drive path-existence check
# ---------------------------------------------------------------------------

class TestDestExistsRemote:
    """_dest_exists_remote routes to rclone.path_exists for Drive URLs."""

    def test_returns_true_when_rclone_says_exists(self):
        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=True):
            assert _dest_exists_remote(GDRIVE_URL, "clip.mov") is True

    def test_returns_false_when_rclone_says_missing(self):
        with _patch_rclone_convert(), \
             patch("core.merge_ops.rclone_bridge.path_exists", return_value=False):
            assert _dest_exists_remote(GDRIVE_URL, "ghost.mov") is False
