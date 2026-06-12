"""
Tests for core/transfer.py::transfer_folder.

copy_file and save_manifest are mocked — no real file hashing or disk I/O
beyond tmp_path fixture directories.
"""

import zipfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from core.transfer import (
    transfer_folder,
    pre_flight_checks,
    copy_file,
    resolve_folder_conflict,
    extract_multipart_zip,
    route_transfer,
    _compute_local_hashes,
    TransferError,
    TransferWarning,
)


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


# ---------------------------------------------------------------------------
# TestPreFlightChecks
# ---------------------------------------------------------------------------

class TestPreFlightChecks:
    """Tests for pre_flight_checks — patches filesystem + rclone helpers."""

    def _patched(self, monkeypatch, *, src_size=100, free=500, disk_total=1000,
                 disk_used=200, src_is_url=False, dst_is_url=False):
        """Return a context that wires monkeypatches and returns pre_flight_checks."""
        monkeypatch.setattr("core.transfer.is_gdrive_url",
                            lambda s: src_is_url if "drive.google.com/src" in s else dst_is_url)
        monkeypatch.setattr("core.transfer.folder_size", lambda p: src_size)
        monkeypatch.setattr("core.transfer.free_space", lambda p: free)
        import shutil
        fake_usage = MagicMock()
        fake_usage.total = disk_total
        fake_usage.used = disk_used
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake_usage)

    def test_happy_path_returns_summary_with_source_size(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        monkeypatch.setattr("core.transfer.folder_size", lambda p: 1024 * 1024)
        monkeypatch.setattr("core.transfer.free_space", lambda p: 10 * 1024 * 1024 * 1024)
        import shutil
        fake = MagicMock(); fake.total = 100 * 1024 ** 3; fake.used = 1 * 1024 ** 3
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake)
        result = pre_flight_checks(src, dst)
        assert "source_size" in result
        assert result["source_size"] == 1024 * 1024

    def test_raises_transfer_error_when_source_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        missing = tmp_path / "does_not_exist"
        dst = tmp_path / "dst"
        with pytest.raises(TransferError, match="Source does not exist"):
            pre_flight_checks(missing, dst)

    def test_raises_transfer_error_when_not_enough_free_space(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        monkeypatch.setattr("core.transfer.folder_size", lambda p: 200)
        monkeypatch.setattr("core.transfer.free_space", lambda p: 50)
        import shutil
        fake = MagicMock(); fake.total = 1000; fake.used = 900
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake)
        with pytest.raises(TransferError, match="Not enough space"):
            pre_flight_checks(src, dst)

    def test_raises_transfer_error_when_exceeds_gdrive_daily_limit(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        dst = "https://drive.google.com/drive/folders/abc"
        monkeypatch.setattr("core.transfer.is_gdrive_url",
                            lambda s: "drive.google.com" in s)
        big = 800 * 1024 ** 3  # 800 GiB > 750 GiB limit
        monkeypatch.setattr("core.transfer.folder_size", lambda p: big)
        with pytest.raises(TransferError, match="750 GB"):
            pre_flight_checks(src, dst, is_gdrive_dest=True)

    def test_raises_transfer_warning_when_disk_over_90_pct(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        monkeypatch.setattr("core.transfer.folder_size", lambda p: 100)
        monkeypatch.setattr("core.transfer.free_space", lambda p: 1000)
        import shutil
        # used=950, total=1000 -> (950 + 100) / 1000 = 105% > 90%
        fake = MagicMock(); fake.total = 1000; fake.used = 950
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake)
        with pytest.raises(TransferWarning, match="full after transfer"):
            pre_flight_checks(src, dst)

    def test_log_cb_called_when_provided(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        monkeypatch.setattr("core.transfer.folder_size", lambda p: 1024)
        monkeypatch.setattr("core.transfer.free_space", lambda p: 10 * 1024 ** 3)
        import shutil
        fake = MagicMock(); fake.total = 100 * 1024 ** 3; fake.used = 1 * 1024 ** 3
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake)
        log_cb = MagicMock()
        pre_flight_checks(src, dst, log_cb=log_cb)
        assert log_cb.called

    def test_estimated_human_format_under_one_hour(self, tmp_path, monkeypatch):
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        # 150 MB -> ~1 second at 150 MB/s
        monkeypatch.setattr("core.transfer.folder_size", lambda p: 150 * 1024 * 1024)
        monkeypatch.setattr("core.transfer.free_space", lambda p: 10 * 1024 ** 3)
        import shutil
        fake = MagicMock(); fake.total = 100 * 1024 ** 3; fake.used = 0
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake)
        result = pre_flight_checks(src, dst)
        # Should be "Xm Xs" format (no leading hour component)
        assert "h" not in result["estimated_human"]


# ---------------------------------------------------------------------------
# TestCopyFile
# ---------------------------------------------------------------------------

class TestCopyFile:
    """Tests for copy_file — patches compute_all and shutil.copy2."""

    CHECKSUMS_SHA = {"sha256": "deadbeef", "xxhash3_64": "cafebabe"}
    CHECKSUMS_MD5 = {"md5": "abcdef01", "sha256": "deadbeef"}

    def test_happy_path_returns_verified_true(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dst = tmp_path / "out" / "file.txt"
        with patch("core.transfer.compute_all", return_value=self.CHECKSUMS_SHA), \
             patch("shutil.copy2"):
            result = copy_file(src, dst)
        assert result["verified"] is True

    def test_happy_path_result_has_expected_keys(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dst = tmp_path / "out" / "file.txt"
        with patch("core.transfer.compute_all", return_value=self.CHECKSUMS_SHA), \
             patch("shutil.copy2"):
            result = copy_file(src, dst)
        assert "source_checksums" in result
        assert "dest_checksums" in result

    def test_raises_transfer_error_on_checksum_mismatch(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dst = tmp_path / "out" / "file.txt"
        src_cs = {"sha256": "aaaa"}
        dst_cs = {"sha256": "bbbb"}
        with patch("core.transfer.compute_all", side_effect=[src_cs, dst_cs]), \
             patch("shutil.copy2"):
            with pytest.raises(TransferError, match="Checksum mismatch"):
                copy_file(src, dst)

    def test_gdrive_mode_uses_md5_key_for_verification(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dst = tmp_path / "out" / "file.txt"
        cs = {"md5": "match123"}
        with patch("core.transfer.compute_all", return_value=cs), \
             patch("shutil.copy2"):
            result = copy_file(src, dst, gdrive_mode=True)
        assert result["verified"] is True

    def test_gdrive_mode_mismatch_raises(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dst = tmp_path / "out" / "file.txt"
        with patch("core.transfer.compute_all", side_effect=[{"md5": "aaa"}, {"md5": "bbb"}]), \
             patch("shutil.copy2"):
            with pytest.raises(TransferError, match="md5"):
                copy_file(src, dst, gdrive_mode=True)

    def test_progress_cb_lambda_passed_to_compute_all(self, tmp_path):
        """compute_all should receive a progress_cb lambda when progress_cb is set."""
        src = tmp_path / "file.txt"
        src.write_text("hello")
        dst = tmp_path / "out" / "file.txt"
        pcb = MagicMock()
        compute_calls = []

        def capture_compute(path, **kwargs):
            compute_calls.append(kwargs.get("progress_cb"))
            return self.CHECKSUMS_SHA

        with patch("core.transfer.compute_all", side_effect=capture_compute), \
             patch("shutil.copy2"):
            copy_file(src, dst, progress_cb=pcb)

        # Both compute_all calls should have received a non-None callable
        assert len(compute_calls) == 2
        for cb in compute_calls:
            assert callable(cb)

    def test_dst_parent_created_if_missing(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("data")
        dst = tmp_path / "deep" / "nested" / "file.txt"
        with patch("core.transfer.compute_all", return_value=self.CHECKSUMS_SHA), \
             patch("shutil.copy2"):
            copy_file(src, dst)
        assert dst.parent.exists()

    def test_log_cb_called_for_hash_and_verify_steps(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("data")
        dst = tmp_path / "out" / "file.txt"
        log_cb = MagicMock()
        with patch("core.transfer.compute_all", return_value=self.CHECKSUMS_SHA), \
             patch("shutil.copy2"):
            copy_file(src, dst, log_cb=log_cb)
        messages = [c.args[0] for c in log_cb.call_args_list]
        assert any("Hash" in m or "Hashing" in m for m in messages)


# ---------------------------------------------------------------------------
# TestResolveFolderConflict
# ---------------------------------------------------------------------------

class TestResolveFolderConflict:
    """Tests for resolve_folder_conflict — pure logic, no I/O."""

    def test_same_name_returns_dst_unchanged(self, tmp_path):
        src = tmp_path / "project"
        dst = tmp_path / "project"
        result, same = resolve_folder_conflict(src, dst)
        assert same is True
        assert result == dst

    def test_different_name_returns_dst_slash_src_name(self, tmp_path):
        src = tmp_path / "project"
        dst = tmp_path / "backup"
        result, same = resolve_folder_conflict(src, dst)
        assert same is False
        assert result == dst / "project"

    def test_edge_case_src_name_with_dots(self, tmp_path):
        src = tmp_path / "my.project.v2"
        dst = tmp_path / "archive"
        result, same = resolve_folder_conflict(src, dst)
        assert result == dst / "my.project.v2"
        assert same is False

    def test_edge_case_dst_equals_src_parent(self, tmp_path):
        # dst is src's own parent — common when user picks containing folder
        parent = tmp_path / "projects"
        src = parent / "shoot"
        result, same = resolve_folder_conflict(src, parent)
        # "shoot" != "projects", so nesting applies
        assert result == parent / "shoot"
        assert same is False


# ---------------------------------------------------------------------------
# TestExtractMultipartZip
# ---------------------------------------------------------------------------

class TestExtractMultipartZip:
    """Tests for extract_multipart_zip — uses real zip files in tmp_path."""

    def _make_zip(self, zip_path, filenames):
        """Create a zip archive containing the given filenames (with dummy content)."""
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name in filenames:
                zf.writestr(name, f"content of {name}")

    def test_happy_path_extracts_zip_files(self, tmp_path):
        self._make_zip(tmp_path / "batch.zip", ["a.txt", "b.txt"])
        result = extract_multipart_zip(tmp_path)
        assert len(result) == 1
        out_dir = tmp_path / "batch"
        assert out_dir.exists()
        assert (out_dir / "a.txt").exists()

    def test_returns_list_of_output_paths(self, tmp_path):
        self._make_zip(tmp_path / "alpha.zip", ["x.txt"])
        self._make_zip(tmp_path / "beta.zip", ["y.txt"])
        result = extract_multipart_zip(tmp_path)
        assert len(result) == 2
        names = {p.name for p in result}
        assert names == {"alpha", "beta"}

    def test_bad_zip_logged_as_error_and_skipped(self, tmp_path):
        (tmp_path / "corrupt.zip").write_text("this is not a zip")
        log_cb = MagicMock()
        result = extract_multipart_zip(tmp_path, log_cb=log_cb)
        assert result == []
        error_msgs = [c.args[0] for c in log_cb.call_args_list
                      if len(c.args) >= 2 and c.args[1] == "error"]
        assert any("corrupt.zip" in m for m in error_msgs)

    def test_missing_directory_returns_empty_list(self, tmp_path):
        missing = tmp_path / "nonexistent"
        result = extract_multipart_zip(missing)
        assert result == []

    def test_no_zips_returns_empty_list(self, tmp_path):
        (tmp_path / "not_a_zip.txt").write_text("hello")
        result = extract_multipart_zip(tmp_path)
        assert result == []

    def test_log_cb_called_on_successful_extract(self, tmp_path):
        self._make_zip(tmp_path / "test.zip", ["file.txt"])
        log_cb = MagicMock()
        extract_multipart_zip(tmp_path, log_cb=log_cb)
        assert log_cb.called


# ---------------------------------------------------------------------------
# TestComputeLocalHashes
# ---------------------------------------------------------------------------

class TestComputeLocalHashes:
    """Tests for _compute_local_hashes — patches compute_all to avoid disk I/O."""

    def test_happy_path_returns_relpath_keyed_dict(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        with patch("core.transfer.compute_all", return_value={"sha256": "ABCDEF"}):
            result = _compute_local_hashes(tmp_path)
        assert "file.txt" in result
        assert result["file.txt"] == "abcdef"

    def test_sha256_values_are_lowercased(self, tmp_path):
        (tmp_path / "upper.txt").write_text("data")
        with patch("core.transfer.compute_all", return_value={"sha256": "UPPERCASE"}):
            result = _compute_local_hashes(tmp_path)
        assert result["upper.txt"] == "uppercase"

    def test_subdir_files_keyed_by_relative_posix_path(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "clip.mov").write_text("video")
        with patch("core.transfer.compute_all", return_value={"sha256": "aabbcc"}):
            result = _compute_local_hashes(tmp_path)
        assert "subdir/clip.mov" in result

    def test_missing_sha256_in_compute_all_skips_entry(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        with patch("core.transfer.compute_all", return_value={"md5": "abc"}):
            result = _compute_local_hashes(tmp_path)
        assert result == {}

    def test_missing_directory_returns_empty_dict(self, tmp_path):
        result = _compute_local_hashes(tmp_path / "nonexistent")
        assert result == {}

    def test_empty_directory_returns_empty_dict(self, tmp_path):
        result = _compute_local_hashes(tmp_path)
        assert result == {}

    def test_hash_failure_logs_warning_and_continues(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        log_cb = MagicMock()
        call_count = {"n": 0}

        def flaky(path, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("permission denied")
            return {"sha256": "good"}

        with patch("core.transfer.compute_all", side_effect=flaky):
            result = _compute_local_hashes(tmp_path, log_cb=log_cb)

        # One file should succeed; one warned
        warnings = [c.args[0] for c in log_cb.call_args_list
                    if len(c.args) >= 2 and c.args[1] == "warning"]
        assert any("Hash failed" in w for w in warnings)
        assert len(result) == 1

    def test_log_cb_reports_file_count_and_size(self, tmp_path):
        (tmp_path / "file.txt").write_bytes(b"x" * 1024)
        log_cb = MagicMock()
        with patch("core.transfer.compute_all", return_value={"sha256": "abc123"}):
            _compute_local_hashes(tmp_path, log_cb=log_cb)
        msgs = [c.args[0] for c in log_cb.call_args_list]
        assert any("Hashed" in m for m in msgs)


# ---------------------------------------------------------------------------
# TestRouteTransfer
# ---------------------------------------------------------------------------

class TestRouteTransfer:
    """Tests for route_transfer dispatcher — mocks both branch functions."""

    def _is_drive(self, s):
        return "drive.google.com" in str(s)

    def test_local_to_local_calls_transfer_folder(self, tmp_path, monkeypatch):
        called = {}

        def fake_transfer_folder(src, dst, **kwargs):
            called["fn"] = "transfer_folder"
            return {"manifest": {}, "errors": [], "actual_dest": str(dst),
                    "same_name": False, "saved_manifest_paths": []}

        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        monkeypatch.setattr("core.transfer.transfer_folder", fake_transfer_folder)
        route_transfer(tmp_path / "src", tmp_path / "dst")
        assert called["fn"] == "transfer_folder"

    def test_drive_src_calls_transfer_folder_rclone(self, tmp_path, monkeypatch):
        called = {}

        def fake_rclone(src, dst, **kwargs):
            called["fn"] = "transfer_folder_rclone"
            return {"manifest": {}, "errors": [], "actual_dest": str(dst),
                    "same_name": False, "saved_manifest_paths": []}

        monkeypatch.setattr("core.transfer.is_gdrive_url", self._is_drive)
        monkeypatch.setattr("core.transfer.transfer_folder_rclone", fake_rclone)
        route_transfer("https://drive.google.com/drive/folders/abc", str(tmp_path))
        assert called["fn"] == "transfer_folder_rclone"

    def test_drive_dst_calls_transfer_folder_rclone(self, tmp_path, monkeypatch):
        called = {}

        def fake_rclone(src, dst, **kwargs):
            called["fn"] = "transfer_folder_rclone"
            return {"manifest": {}, "errors": [], "actual_dest": str(dst),
                    "same_name": False, "saved_manifest_paths": []}

        monkeypatch.setattr("core.transfer.is_gdrive_url", self._is_drive)
        monkeypatch.setattr("core.transfer.transfer_folder_rclone", fake_rclone)
        src = tmp_path / "src"
        src.mkdir()
        route_transfer(str(src), "https://drive.google.com/drive/folders/abc")
        assert called["fn"] == "transfer_folder_rclone"

    def test_gdrive_mode_flag_forwarded_to_transfer_folder(self, tmp_path, monkeypatch):
        received = {}

        def fake_transfer_folder(src, dst, gdrive_mode=False, **kwargs):
            received["gdrive_mode"] = gdrive_mode
            return {"manifest": {}, "errors": [], "actual_dest": str(dst),
                    "same_name": False, "saved_manifest_paths": []}

        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        monkeypatch.setattr("core.transfer.transfer_folder", fake_transfer_folder)
        route_transfer(tmp_path / "src", tmp_path / "dst", gdrive_mode=True)
        assert received["gdrive_mode"] is True

    def test_mirror_mode_forwarded_to_rclone(self, tmp_path, monkeypatch):
        received = {}

        def fake_rclone(src, dst, mirror_mode=False, **kwargs):
            received["mirror_mode"] = mirror_mode
            return {"manifest": {}, "errors": [], "actual_dest": str(dst),
                    "same_name": False, "saved_manifest_paths": []}

        monkeypatch.setattr("core.transfer.is_gdrive_url", self._is_drive)
        monkeypatch.setattr("core.transfer.transfer_folder_rclone", fake_rclone)
        route_transfer("https://drive.google.com/drive/folders/abc", str(tmp_path),
                       mirror_mode=True)
        assert received["mirror_mode"] is True

    def test_conflict_handler_forwarded_to_transfer_folder(self, tmp_path, monkeypatch):
        received = {}

        def fake_transfer_folder(src, dst, conflict_handler="skip", **kwargs):
            received["conflict_handler"] = conflict_handler
            return {"manifest": {}, "errors": [], "actual_dest": str(dst),
                    "same_name": False, "saved_manifest_paths": []}

        monkeypatch.setattr("core.transfer.is_gdrive_url", lambda s: False)
        monkeypatch.setattr("core.transfer.transfer_folder", fake_transfer_folder)
        route_transfer(tmp_path / "src", tmp_path / "dst", conflict_handler="rename")
        assert received["conflict_handler"] == "rename"


# ---------------------------------------------------------------------------
# TransferError — exception contract (7 callers raise/catch it)
# ---------------------------------------------------------------------------

class TestTransferError:
    def test_is_exception_subclass(self):
        assert issubclass(TransferError, Exception)

    def test_message_preserved(self):
        err = TransferError("Source does not exist: /tmp/x")
        assert str(err) == "Source does not exist: /tmp/x"

    def test_raise_and_catch(self):
        with pytest.raises(TransferError, match="boom"):
            raise TransferError("boom")

    def test_catchable_as_generic_exception(self):
        # GUI workers catch bare Exception; TransferError must not escape that net
        try:
            raise TransferError("x")
        except Exception as e:
            assert isinstance(e, TransferError)
