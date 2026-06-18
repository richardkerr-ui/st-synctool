"""
Tests for core/merge_ops.py — preserve_rename naming, overwrite_suffix initials
derivation, collision suffix, and _local_copy_verify pass/fail semantics.

These are targeted at the silent failure modes: wrong initials on an unexpected
username format, collision suffix incrementing on same-day clashes, and verify
returning OK on a mismatched file.
"""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from core.merge_ops import (
    overwrite_suffix,
    preserve_rename,
    push_file,
    pull_file,
    _local_copy_verify,
    _server_is_url,
    delete_local,
    delete_server,
    _dest_exists_local,
    _dest_exists_remote,
)


# ---------------------------------------------------------------------------
# overwrite_suffix — initials derivation
# ---------------------------------------------------------------------------

class TestOverwriteSuffix:
    def _suffix_for(self, username: str) -> str:
        with patch("core.merge_ops.getpass.getuser", return_value=username):
            return overwrite_suffix()

    def test_dotted_name_uses_first_letters(self):
        suffix = self._suffix_for("richard.kerr")
        assert suffix.endswith("-rk")

    def test_three_part_name_uses_all_initials(self):
        suffix = self._suffix_for("mary.jane.watson")
        assert suffix.endswith("-mjw")

    def test_single_word_username_uses_first_two_chars(self):
        suffix = self._suffix_for("johndoe")
        assert suffix.endswith("-jo")

    def test_single_char_username_does_not_crash(self):
        suffix = self._suffix_for("x")
        assert suffix.endswith("-x")

    def test_initials_are_lowercase(self):
        suffix = self._suffix_for("Richard.Kerr")
        assert suffix.endswith("-rk")

    def test_date_portion_format(self):
        # Suffix must start with YYYY-MM-DD
        suffix = self._suffix_for("richard.kerr")
        date_part = suffix[: suffix.rfind("-")]
        parts = date_part.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4  # year
        assert len(parts[1]) == 2  # month
        assert len(parts[2]) == 2  # day

    def test_empty_username_does_not_crash(self):
        # Degenerate case — getuser() should never return this, but guard anyway
        suffix = self._suffix_for("")
        assert "-" in suffix


# ---------------------------------------------------------------------------
# preserve_rename — output filename construction
# ---------------------------------------------------------------------------

class TestPreserveRename:
    def _rename(self, rel_path: str, username: str = "richard.kerr") -> str:
        with patch("core.merge_ops.getpass.getuser", return_value=username):
            return preserve_rename(rel_path)

    def test_stem_and_suffix_preserved(self):
        result = self._rename("project.prproj")
        assert result.startswith("project_")
        assert result.endswith(".prproj")

    def test_contains_initials(self):
        result = self._rename("final.mov", username="richard.kerr")
        assert "-rk" in result

    def test_directory_prefix_preserved(self):
        result = self._rename("edits/sequence.prproj")
        assert result.startswith("edits/sequence_")
        assert result.endswith(".prproj")

    def test_nested_directory_preserved(self):
        result = self._rename("a/b/c/clip.mov")
        assert result.startswith("a/b/c/clip_")

    def test_no_extension_does_not_crash(self):
        result = self._rename("README")
        assert "README_" in result

    def test_result_is_different_from_input(self):
        result = self._rename("clip.mov")
        assert result != "clip.mov"

    def test_two_users_same_file_produce_different_renames(self):
        r1 = self._rename("clip.mov", username="alice.smith")
        r2 = self._rename("clip.mov", username="bob.jones")
        assert r1 != r2

    def test_no_exists_fn_keeps_single_deterministic_name(self):
        # Backward-compatible default: without a collision probe, the name is the
        # plain date+initials form (no numeric suffix).
        with patch("core.merge_ops.getpass.getuser", return_value="richard.kerr"):
            r = preserve_rename("clip.mov")
        assert r.startswith("clip_") and r.endswith(".mov")
        assert "_2.mov" not in r

    def test_collision_increments_suffix(self):
        # Same-day, same-user collision: the first preserved name already exists,
        # so the second must increment to _2 rather than silently reusing it.
        with patch("core.merge_ops.getpass.getuser", return_value="richard.kerr"):
            first = preserve_rename("clip.mov")              # what already exists
            taken = {first}
            second = preserve_rename("clip.mov", exists_fn=lambda r: r in taken)
        assert second != first
        assert second.endswith("_2.mov")

    def test_collision_increments_past_multiple_taken_names(self):
        with patch("core.merge_ops.getpass.getuser", return_value="richard.kerr"):
            first = preserve_rename("clip.mov")
            second = preserve_rename("clip.mov", exists_fn=lambda r: r == first)
            taken = {first, second}
            third = preserve_rename("clip.mov", exists_fn=lambda r: r in taken)
        assert third.endswith("_3.mov")
        assert len({first, second, third}) == 3


# ---------------------------------------------------------------------------
# _local_copy_verify — pass/fail semantics
# ---------------------------------------------------------------------------

class TestLocalCopyVerify:
    def test_clean_copy_returns_truthy(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"important footage data")
        result = _local_copy_verify(src, dst)
        assert result  # truthy

    def test_clean_copy_has_verified_true(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"important footage data")
        result = _local_copy_verify(src, dst)
        assert result["verified"] is True

    def test_clean_copy_pre_and_post_hashes_match(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"important footage data")
        result = _local_copy_verify(src, dst)
        assert result["pre"]["xxh128"] == result["post"]["xxh128"]

    def test_destination_file_created(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"data")
        _local_copy_verify(src, dst)
        assert dst.exists()

    def test_corrupted_copy_returns_false(self, tmp_path):
        """
        Simulate a copy that silently produces wrong content.
        This is the exact failure mode we can't afford to miss.
        """
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"original content")

        # Intercept shutil.copy2 and write different bytes to dst
        original_copy2 = shutil.copy2
        def corrupt_copy(s, d, **kw):
            original_copy2(s, d, **kw)
            Path(d).write_bytes(b"corrupted content")

        with patch("core.merge_ops.shutil.copy2", side_effect=corrupt_copy):
            result = _local_copy_verify(src, dst)

        assert result is False

    def test_missing_source_returns_false(self, tmp_path):
        src = tmp_path / "does_not_exist.mov"
        dst = tmp_path / "dst.mov"
        result = _local_copy_verify(src, dst)
        assert result is False

    def test_copy_error_calls_log_cb(self, tmp_path):
        src = tmp_path / "ghost.mov"  # does not exist
        dst = tmp_path / "dst.mov"
        log_calls = []
        _local_copy_verify(src, dst, log_cb=lambda msg, lvl: log_calls.append((msg, lvl)))
        assert any("error" in lvl for _, lvl in log_calls)


# ---------------------------------------------------------------------------
# push_file / pull_file — preserve-on-overwrite integration (local paths only)
# ---------------------------------------------------------------------------

class TestPushFilePreserve:
    def test_push_no_preserve_overwrites_existing(self, tmp_path):
        local = tmp_path / "local"
        server = tmp_path / "server"
        local.mkdir(); server.mkdir()
        (local  / "clip.mov").write_bytes(b"new version")
        (server / "clip.mov").write_bytes(b"old version")

        result = push_file("clip.mov", local, str(server), preserve_on_overwrite=False)

        assert result
        assert (server / "clip.mov").read_bytes() == b"new version"

    def test_push_preserve_renames_incoming(self, tmp_path):
        local = tmp_path / "local"
        server = tmp_path / "server"
        local.mkdir(); server.mkdir()
        (local  / "clip.mov").write_bytes(b"new version")
        (server / "clip.mov").write_bytes(b"existing on server")

        with patch("core.merge_ops.getpass.getuser", return_value="richard.kerr"):
            result = push_file("clip.mov", local, str(server), preserve_on_overwrite=True)

        assert result
        # Original server file must be untouched
        assert (server / "clip.mov").read_bytes() == b"existing on server"
        # Renamed copy must exist
        renamed = result.get("renamed_to")
        assert renamed and renamed != "clip.mov"
        assert (server / renamed).exists()

    def test_push_missing_source_returns_false(self, tmp_path):
        local = tmp_path / "local"
        server = tmp_path / "server"
        local.mkdir(); server.mkdir()
        result = push_file("ghost.mov", local, str(server), preserve_on_overwrite=False)
        assert result is False


class TestPullFilePreserve:
    def test_pull_no_preserve_overwrites_existing(self, tmp_path):
        local = tmp_path / "local"
        server = tmp_path / "server"
        local.mkdir(); server.mkdir()
        (local  / "clip.mov").write_bytes(b"old local version")
        (server / "clip.mov").write_bytes(b"updated from server")

        result = pull_file("clip.mov", local, str(server), preserve_on_overwrite=False)

        assert result
        assert (local / "clip.mov").read_bytes() == b"updated from server"

    def test_pull_preserve_renames_incoming(self, tmp_path):
        local = tmp_path / "local"
        server = tmp_path / "server"
        local.mkdir(); server.mkdir()
        (local  / "clip.mov").write_bytes(b"existing local")
        (server / "clip.mov").write_bytes(b"server version")

        with patch("core.merge_ops.getpass.getuser", return_value="alice.smith"):
            result = pull_file("clip.mov", local, str(server), preserve_on_overwrite=True)

        assert result
        # Original local file must be untouched
        assert (local / "clip.mov").read_bytes() == b"existing local"
        renamed = result.get("renamed_to")
        assert renamed and renamed != "clip.mov"
        assert (local / renamed).exists()

    def test_pull_missing_server_source_returns_false(self, tmp_path):
        local = tmp_path / "local"
        server = tmp_path / "server"
        local.mkdir(); server.mkdir()
        result = pull_file("ghost.mov", local, str(server), preserve_on_overwrite=False)
        assert result is False


# ---------------------------------------------------------------------------
# _server_is_url — thin wrapper around is_gdrive_url
# ---------------------------------------------------------------------------

class TestServerIsUrl:
    def test_gdrive_url_returns_true(self):
        assert _server_is_url("https://drive.google.com/drive/folders/abc123") is True

    def test_local_path_returns_false(self):
        assert _server_is_url("/Volumes/NAS/Projects") is False

    def test_empty_string_returns_false(self):
        assert _server_is_url("") is False

    def test_non_gdrive_url_returns_false(self):
        assert _server_is_url("https://example.com/files") is False


# ---------------------------------------------------------------------------
# _dest_exists_local — checks whether rel_path exists under local_root
# ---------------------------------------------------------------------------

class TestDestExistsLocal:
    def test_returns_true_when_file_exists(self, tmp_path):
        (tmp_path / "clip.mov").write_bytes(b"data")
        assert _dest_exists_local(tmp_path, "clip.mov") is True

    def test_returns_false_when_file_missing(self, tmp_path):
        assert _dest_exists_local(tmp_path, "ghost.mov") is False

    def test_nested_path_found(self, tmp_path):
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        (subdir / "clip.mov").write_bytes(b"nested")
        assert _dest_exists_local(tmp_path, "a/b/clip.mov") is True

    def test_nested_path_missing(self, tmp_path):
        assert _dest_exists_local(tmp_path, "a/b/ghost.mov") is False

    def test_empty_rel_path_resolves_to_root(self, tmp_path):
        # Path(tmp_path / "") == tmp_path — a directory exists, so True
        assert _dest_exists_local(tmp_path, "") is True


# ---------------------------------------------------------------------------
# _dest_exists_remote — local-path branch (no rclone involved)
# ---------------------------------------------------------------------------

class TestDestExistsRemote:
    def test_local_server_returns_true_when_file_exists(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        (server / "clip.mov").write_bytes(b"data")
        assert _dest_exists_remote(str(server), "clip.mov") is True

    def test_local_server_returns_false_when_file_missing(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        assert _dest_exists_remote(str(server), "ghost.mov") is False

    def test_local_server_nested_path_found(self, tmp_path):
        server = tmp_path / "server"
        subdir = server / "edits"
        subdir.mkdir(parents=True)
        (subdir / "seq.prproj").write_bytes(b"proj")
        assert _dest_exists_remote(str(server), "edits/seq.prproj") is True

    def test_gdrive_url_delegates_to_rclone_bridge_true(self):
        gdrive_url = "https://drive.google.com/drive/folders/FAKE123"
        with patch("core.merge_ops.rclone_bridge.path_exists", return_value=True) as mock_pe:
            result = _dest_exists_remote(gdrive_url, "clip.mov")
        assert result is True
        mock_pe.assert_called_once()

    def test_gdrive_url_delegates_to_rclone_bridge_false(self):
        gdrive_url = "https://drive.google.com/drive/folders/FAKE123"
        with patch("core.merge_ops.rclone_bridge.path_exists", return_value=False):
            result = _dest_exists_remote(gdrive_url, "ghost.mov")
        assert result is False

    def test_gdrive_url_passes_extra_flags(self):
        gdrive_url = "https://drive.google.com/drive/folders/FAKE123"
        with patch("core.merge_ops.rclone_bridge.path_exists", return_value=True) as mock_pe:
            _dest_exists_remote(gdrive_url, "clip.mov")
        _, kwargs = mock_pe.call_args
        assert "extra_flags" in kwargs


# ---------------------------------------------------------------------------
# delete_local — unlink rel_path under local_root
# ---------------------------------------------------------------------------

class TestDeleteLocal:
    def test_happy_path_deletes_file(self, tmp_path):
        target = tmp_path / "clip.mov"
        target.write_bytes(b"footage")
        result = delete_local("clip.mov", tmp_path)
        assert result is True
        assert not target.exists()

    def test_already_absent_returns_true(self, tmp_path):
        result = delete_local("ghost.mov", tmp_path)
        assert result is True

    def test_already_absent_calls_log_cb_with_warning(self, tmp_path):
        log_calls = []
        delete_local("ghost.mov", tmp_path, log_cb=lambda msg, lvl: log_calls.append((msg, lvl)))
        assert any("warning" in lvl for _, lvl in log_calls)

    def test_successful_delete_calls_log_cb_with_warning(self, tmp_path):
        (tmp_path / "clip.mov").write_bytes(b"data")
        log_calls = []
        delete_local("clip.mov", tmp_path, log_cb=lambda msg, lvl: log_calls.append((msg, lvl)))
        assert any("warning" in lvl for _, lvl in log_calls)

    def test_permission_error_returns_false(self, tmp_path):
        import pathlib
        (tmp_path / "locked.mov").write_bytes(b"data")
        with patch.object(pathlib.Path, "unlink", side_effect=PermissionError("denied")):
            result = delete_local("locked.mov", tmp_path)
        assert result is False

    def test_permission_error_calls_log_cb_with_error(self, tmp_path):
        target = tmp_path / "locked.mov"
        target.write_bytes(b"data")
        log_calls = []

        import pathlib
        with patch.object(pathlib.Path, "unlink", side_effect=OSError("read-only filesystem")):
            delete_local(
                "locked.mov", tmp_path,
                log_cb=lambda msg, lvl: log_calls.append((msg, lvl))
            )
        assert any("error" in lvl for _, lvl in log_calls)

    def test_nested_rel_path_deleted(self, tmp_path):
        subdir = tmp_path / "edits"
        subdir.mkdir()
        target = subdir / "seq.prproj"
        target.write_bytes(b"project")
        result = delete_local("edits/seq.prproj", tmp_path)
        assert result is True
        assert not target.exists()

    def test_no_log_cb_does_not_raise(self, tmp_path):
        (tmp_path / "clip.mov").write_bytes(b"data")
        result = delete_local("clip.mov", tmp_path, log_cb=None)
        assert result is True


# ---------------------------------------------------------------------------
# delete_server — unlink rel_path on local-path server or via rclone
# ---------------------------------------------------------------------------

class TestDeleteServer:
    def test_happy_path_local_server_deletes_file(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        target = server / "clip.mov"
        target.write_bytes(b"footage")
        result = delete_server("clip.mov", str(server))
        assert result is True
        assert not target.exists()

    def test_local_server_already_absent_returns_true(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        result = delete_server("ghost.mov", str(server))
        assert result is True

    def test_local_server_already_absent_logs_warning(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        log_calls = []
        delete_server(
            "ghost.mov", str(server),
            log_cb=lambda msg, lvl: log_calls.append((msg, lvl))
        )
        assert any("warning" in lvl for _, lvl in log_calls)

    def test_local_server_success_logs_warning(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        (server / "clip.mov").write_bytes(b"data")
        log_calls = []
        delete_server(
            "clip.mov", str(server),
            log_cb=lambda msg, lvl: log_calls.append((msg, lvl))
        )
        assert any("warning" in lvl for _, lvl in log_calls)

    def test_local_server_nested_path_deleted(self, tmp_path):
        server = tmp_path / "server"
        subdir = server / "edits"
        subdir.mkdir(parents=True)
        target = subdir / "seq.prproj"
        target.write_bytes(b"proj")
        result = delete_server("edits/seq.prproj", str(server))
        assert result is True
        assert not target.exists()

    def test_local_server_os_error_returns_false(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        (server / "locked.mov").write_bytes(b"data")

        import pathlib
        with patch.object(pathlib.Path, "unlink", side_effect=OSError("locked")):
            outcome = delete_server("locked.mov", str(server))
        assert outcome is False

    def test_local_server_os_error_calls_log_cb(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        (server / "locked.mov").write_bytes(b"data")
        log_calls = []

        import pathlib
        with patch.object(pathlib.Path, "unlink", side_effect=OSError("locked")):
            delete_server(
                "locked.mov", str(server),
                log_cb=lambda msg, lvl: log_calls.append((msg, lvl))
            )
        assert any("error" in lvl for _, lvl in log_calls)

    def test_gdrive_url_delegates_to_rclone_bridge_success(self):
        gdrive_url = "https://drive.google.com/drive/folders/FAKE123"
        with patch("core.merge_ops.rclone_bridge.deletefile", return_value=True) as mock_del:
            result = delete_server("clip.mov", gdrive_url)
        assert result is True
        mock_del.assert_called_once()

    def test_gdrive_url_delegates_to_rclone_bridge_failure(self):
        gdrive_url = "https://drive.google.com/drive/folders/FAKE123"
        with patch("core.merge_ops.rclone_bridge.deletefile", return_value=False):
            result = delete_server("clip.mov", gdrive_url)
        assert result is False

    def test_gdrive_url_success_logs_warning(self):
        gdrive_url = "https://drive.google.com/drive/folders/FAKE123"
        log_calls = []
        with patch("core.merge_ops.rclone_bridge.deletefile", return_value=True):
            delete_server(
                "clip.mov", gdrive_url,
                log_cb=lambda msg, lvl: log_calls.append((msg, lvl))
            )
        assert any("warning" in lvl for _, lvl in log_calls)

    def test_gdrive_url_failure_does_not_log_success(self):
        gdrive_url = "https://drive.google.com/drive/folders/FAKE123"
        log_calls = []
        with patch("core.merge_ops.rclone_bridge.deletefile", return_value=False):
            delete_server(
                "clip.mov", gdrive_url,
                log_cb=lambda msg, lvl: log_calls.append((msg, lvl))
            )
        assert not any("warning" in lvl for _, lvl in log_calls)

    def test_no_log_cb_does_not_raise(self, tmp_path):
        server = tmp_path / "server"
        server.mkdir()
        (server / "clip.mov").write_bytes(b"data")
        result = delete_server("clip.mov", str(server), log_cb=None)
        assert result is True
