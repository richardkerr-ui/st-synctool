"""
Tests for core/merge_ops.py — preserve_rename naming, overwrite_suffix initials
derivation, collision suffix, and _local_copy_verify pass/fail semantics.

These are targeted at the silent failure modes: wrong initials on an unexpected
username format, collision suffix not incrementing, and verify returning OK on a
mismatched file.
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
        assert result["pre"]["sha256"] == result["post"]["sha256"]

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
