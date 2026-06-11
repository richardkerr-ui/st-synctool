"""
Tests for core/transfer.py::transfer_folder.

copy_file and save_manifest are mocked — no real file hashing or disk I/O
beyond tmp_path fixture directories.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from core.transfer import transfer_folder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COPY_RESULT = {
    "source_checksums": {"sha256": "aabbcc"},
    "dest_checksums": {"sha256": "aabbcc"},
    "verified": True,
}


def _make_src(tmp_path):
    """Create a src tree with one root file and one subdir file."""
    src = tmp_path / "src_folder"
    src.mkdir()
    (src / "root_file.txt").write_text("hello")
    subdir = src / "subdir"
    subdir.mkdir()
    (subdir / "clip.mov").write_text("video")
    return src


def _any_warning(log_cb, substr):
    return any(
        len(c.args) >= 2 and c.args[1] == "warning" and substr in c.args[0]
        for c in log_cb.call_args_list
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def log_cb():
    return MagicMock()


def _copy_file_side_effect(*args, **kwargs):
    """Return a fresh copy of COPY_RESULT each call so mutations don't bleed between entries."""
    return dict(COPY_RESULT)


@pytest.fixture
def mock_copy_file():
    with patch("core.transfer.copy_file", side_effect=_copy_file_side_effect) as m:
        yield m


@pytest.fixture
def mock_save_manifest():
    with patch("core.transfer.save_manifest", return_value=[]) as m:
        yield m


# ---------------------------------------------------------------------------
# TestReturnSchema
# ---------------------------------------------------------------------------

class TestReturnSchema:
    def test_result_has_required_keys(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        result = transfer_folder(src, dst, log_cb=log_cb)
        for key in ("manifest", "errors", "actual_dest", "same_name", "saved_manifest_paths"):
            assert key in result, f"missing key: {key}"

    def test_manifest_has_schema_fields(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        for field in ("schema_version", "source_root", "dest_root", "operation", "files", "file_count"):
            assert field in m, f"missing manifest field: {field}"

    def test_manifest_operation_is_transfer(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        assert m["operation"] == "transfer"

    def test_manifest_file_count_matches_files_dict(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        assert m["file_count"] == len(m["files"])


# ---------------------------------------------------------------------------
# TestFileHandling
# ---------------------------------------------------------------------------

class TestFileHandling:
    def test_copy_file_called_once_per_file(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        transfer_folder(src, dst, log_cb=log_cb)
        # src has 2 files: root_file.txt and subdir/clip.mov
        assert mock_copy_file.call_count == 2

    def test_root_file_keyed_by_posix_rel_path(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        assert "root_file.txt" in m["files"]

    def test_subdir_file_keyed_by_posix_rel_path(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        # Must be keyed as "subdir/clip.mov", not bare "clip.mov"
        assert "subdir/clip.mov" in m["files"]
        assert "clip.mov" not in m["files"]

    def test_file_entry_has_rel_path_field(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        entry = m["files"]["subdir/clip.mov"]
        assert entry["rel_path"] == "subdir/clip.mov"


# ---------------------------------------------------------------------------
# TestConflictHandlers
# ---------------------------------------------------------------------------

class TestConflictHandlers:
    def test_skip_does_not_copy_existing_file(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        dst.mkdir(parents=True)
        # Pre-create the root file at the destination so it already exists
        (dst / src.name / "root_file.txt").mkdir(parents=True, exist_ok=True)
        # Actually make it a file
        import shutil
        shutil.rmtree(dst / src.name / "root_file.txt", ignore_errors=True)
        dest_dir = dst / src.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "root_file.txt").write_text("existing")

        result = transfer_folder(src, dst, conflict_handler="skip", log_cb=log_cb)
        # copy_file should only be called for the file that did NOT exist
        called_dests = [str(c.args[1]) for c in mock_copy_file.call_args_list]
        assert not any("root_file.txt" in d for d in called_dests)

    def test_skip_logs_warning_for_skipped_file(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        dest_dir = dst / src.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "root_file.txt").write_text("existing")

        transfer_folder(src, dst, conflict_handler="skip", log_cb=log_cb)
        assert _any_warning(log_cb, "Skipped")

    def test_rename_renames_dest_file_with_conflict_suffix(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        dest_dir = dst / src.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "root_file.txt").write_text("existing")

        transfer_folder(src, dst, conflict_handler="rename", log_cb=log_cb)
        # copy_file should be called with the _conflict-renamed dest path
        called_dests = [str(c.args[1]) for c in mock_copy_file.call_args_list]
        assert any("_conflict" in d for d in called_dests)

    def test_overwrite_copies_even_when_file_exists(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        dest_dir = dst / src.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "root_file.txt").write_text("existing")

        transfer_folder(src, dst, conflict_handler="overwrite", log_cb=log_cb)
        # Both files should be copied (overwrite does not skip)
        assert mock_copy_file.call_count == 2


# ---------------------------------------------------------------------------
# TestProgress
# ---------------------------------------------------------------------------

class TestProgress:
    def test_progress_cb_called_during_file_loop(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        pcb = MagicMock()
        transfer_folder(src, dst, log_cb=log_cb, progress_cb=pcb)
        # Should have been called at least once mid-loop
        assert pcb.call_count >= 1

    def test_progress_cb_ends_at_100(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        pcb = MagicMock()
        transfer_folder(src, dst, log_cb=log_cb, progress_cb=pcb)
        pcts = [c.args[0] for c in pcb.call_args_list]
        assert pcts[-1] == 100


# ---------------------------------------------------------------------------
# TestErrors
# ---------------------------------------------------------------------------

class TestErrors:
    def test_copy_error_recorded_in_manifest_errors(self, tmp_path, log_cb, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        with patch("core.transfer.copy_file", side_effect=Exception("disk full")):
            result = transfer_folder(src, dst, log_cb=log_cb)
        assert len(result["manifest"]["errors"]) == 2  # both files fail

    def test_copy_error_not_added_to_files_dict(self, tmp_path, log_cb, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        with patch("core.transfer.copy_file", side_effect=Exception("disk full")):
            result = transfer_folder(src, dst, log_cb=log_cb)
        assert result["manifest"]["files"] == {}

    def test_error_in_one_file_does_not_abort_others(self, tmp_path, log_cb, mock_save_manifest):
        """First file raises; second file should still be attempted."""
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("first file fails")
            return dict(COPY_RESULT)

        with patch("core.transfer.copy_file", side_effect=flaky):
            result = transfer_folder(src, dst, log_cb=log_cb)

        assert call_count["n"] == 2
        assert len(result["manifest"]["errors"]) == 1
        assert result["manifest"]["file_count"] == 1


# ---------------------------------------------------------------------------
# TestManifest
# ---------------------------------------------------------------------------

class TestManifest:
    def test_gdrive_mode_true_uses_md5_algorithm(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, gdrive_mode=True, log_cb=log_cb)["manifest"]
        assert m["checksum_context"]["algorithm"] == "md5"

    def test_gdrive_mode_false_uses_sha256_algorithm(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, gdrive_mode=False, log_cb=log_cb)["manifest"]
        assert m["checksum_context"]["algorithm"] == "sha256"

    def test_gdrive_mode_true_file_entry_hash_algorithm(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, gdrive_mode=True, log_cb=log_cb)["manifest"]
        for entry in m["files"].values():
            assert entry["hash_algorithm"] == "md5"

    def test_gdrive_mode_false_file_entry_hash_algorithm(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        m = transfer_folder(src, dst, gdrive_mode=False, log_cb=log_cb)["manifest"]
        for entry in m["files"].values():
            assert entry["hash_algorithm"] == "sha256"

    def test_same_name_merge_true_when_names_match(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        """When src.name == dst.name the actual_dest IS dst (same-name merge)."""
        src = _make_src(tmp_path)
        # Make dst have the same leaf name as src
        dst = tmp_path / src.name
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        assert m["same_name_merge"] is True

    def test_same_name_merge_false_when_names_differ(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst_other"
        m = transfer_folder(src, dst, log_cb=log_cb)["manifest"]
        assert m["same_name_merge"] is False

    def test_save_manifest_called_once(self, tmp_path, log_cb, mock_copy_file, mock_save_manifest):
        src = _make_src(tmp_path)
        dst = tmp_path / "dst"
        transfer_folder(src, dst, log_cb=log_cb)
        mock_save_manifest.assert_called_once()
