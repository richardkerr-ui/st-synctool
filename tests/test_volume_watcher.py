"""Tests for utils/volume_watcher.py — pure helper functions.

_looks_like_media_card, _sanitise_label, _human_size, and _classify_volume
had zero test coverage. _looks_like_media_card and _classify_volume are the
most dangerous: a false negative silently drops a camera card from the
auto-detect banner; a false positive bothers users with banners for every
USB stick.
"""

import plistlib
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from utils.volume_watcher import (
    _looks_like_media_card,
    _sanitise_label,
    _human_size,
    _classify_volume,
)


# ---------------------------------------------------------------------------
# _looks_like_media_card
# ---------------------------------------------------------------------------

class TestLooksLikeMediaCard:
    def test_detects_dcim_at_root(self, tmp_path):
        (tmp_path / "DCIM").mkdir()
        found, marker = _looks_like_media_card(str(tmp_path))
        assert found is True
        assert marker == "DCIM"

    def test_detects_private_at_root(self, tmp_path):
        (tmp_path / "PRIVATE").mkdir()
        found, _ = _looks_like_media_card(str(tmp_path))
        assert found is True

    def test_detects_audio_at_root(self, tmp_path):
        (tmp_path / "AUDIO").mkdir()
        found, _ = _looks_like_media_card(str(tmp_path))
        assert found is True

    def test_detects_rdc_extension_at_root(self, tmp_path):
        (tmp_path / "A001.rdc").touch()
        found, marker = _looks_like_media_card(str(tmp_path))
        assert found is True
        assert marker == "A001.rdc"

    def test_detects_dcim_one_level_deep(self, tmp_path):
        nested = tmp_path / "EOS_DIGITAL"
        nested.mkdir()
        (nested / "DCIM").mkdir()
        found, marker = _looks_like_media_card(str(tmp_path))
        assert found is True
        assert "DCIM" in marker

    def test_returns_false_for_empty_volume(self, tmp_path):
        found, marker = _looks_like_media_card(str(tmp_path))
        assert found is False
        assert marker == ""

    def test_returns_false_for_non_media_directories(self, tmp_path):
        (tmp_path / "Documents").mkdir()
        (tmp_path / "Downloads").mkdir()
        found, _ = _looks_like_media_card(str(tmp_path))
        assert found is False

    def test_marker_dir_match_is_case_insensitive(self, tmp_path):
        # Function uppercases item.name before the set lookup, so lowercase matches
        (tmp_path / "dcim").mkdir()
        found, marker = _looks_like_media_card(str(tmp_path))
        assert found is True
        assert marker == "dcim"

    def test_ignores_dcim_file_not_directory(self, tmp_path):
        (tmp_path / "DCIM").touch()  # file, not dir
        found, _ = _looks_like_media_card(str(tmp_path))
        assert found is False


# ---------------------------------------------------------------------------
# _sanitise_label
# ---------------------------------------------------------------------------

class TestSanitiseLabel:
    def test_normal_name_returned_unchanged(self):
        assert _sanitise_label("A001") == "A001"

    def test_generic_no_name_returns_card_prefix(self):
        result = _sanitise_label("NO NAME")
        assert result.startswith("Card_")

    def test_generic_untitled_returns_card_prefix(self):
        result = _sanitise_label("UNTITLED")
        assert result.startswith("Card_")

    def test_untitled_with_number_returns_card_prefix(self):
        result = _sanitise_label("UNTITLED 1")
        assert result.startswith("Card_")

    def test_empty_string_returns_card_prefix(self):
        result = _sanitise_label("")
        assert result.startswith("Card_")

    def test_eos_digital_returns_card_prefix(self):
        result = _sanitise_label("EOS_DIGITAL")
        assert result.startswith("Card_")

    def test_unsafe_chars_replaced_with_underscore(self):
        result = _sanitise_label('my:vol/name<test>')
        assert ":" not in result
        assert "/" not in result
        assert "<" not in result
        assert ">" not in result

    def test_long_name_truncated_to_64_chars(self):
        long_name = "A" * 100
        assert len(_sanitise_label(long_name)) <= 64

    def test_strips_leading_trailing_whitespace(self):
        result = _sanitise_label("  MyCard  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")


# ---------------------------------------------------------------------------
# _human_size
# ---------------------------------------------------------------------------

class TestHumanSize:
    def test_bytes(self):
        assert _human_size(512) == "512.0 B"

    def test_kilobytes(self):
        assert _human_size(1024) == "1.0 KB"

    def test_megabytes(self):
        assert _human_size(1024 ** 2) == "1.0 MB"

    def test_gigabytes(self):
        assert _human_size(1024 ** 3) == "1.0 GB"

    def test_terabytes(self):
        assert _human_size(1024 ** 4) == "1.0 TB"

    def test_64gb_card(self):
        result = _human_size(64 * 1024 ** 3)
        assert "64" in result
        assert "GB" in result


# ---------------------------------------------------------------------------
# _classify_volume
# ---------------------------------------------------------------------------

class TestClassifyVolume:
    def _diskutil_plist(self, removable=True, ejectable=True,
                        volume_name="TestCard", total_size=64 * 1024 ** 3):
        return {
            "RemovableMedia": removable,
            "Ejectable": ejectable,
            "VolumeName": volume_name,
            "TotalSize": total_size,
            "FilesystemType": "ExFAT",
        }

    def test_returns_none_when_diskutil_fails(self, tmp_path):
        with patch("utils.volume_watcher._diskutil_info", return_value={}):
            assert _classify_volume(str(tmp_path)) is None

    def test_returns_none_for_non_removable(self, tmp_path):
        info = self._diskutil_plist(removable=False)
        with patch("utils.volume_watcher._diskutil_info", return_value=info):
            assert _classify_volume(str(tmp_path)) is None

    def test_returns_none_for_non_ejectable(self, tmp_path):
        info = self._diskutil_plist(ejectable=False)
        with patch("utils.volume_watcher._diskutil_info", return_value=info):
            assert _classify_volume(str(tmp_path)) is None

    def test_returns_none_when_no_media_marker(self, tmp_path):
        info = self._diskutil_plist()
        with patch("utils.volume_watcher._diskutil_info", return_value=info), \
             patch("utils.volume_watcher._looks_like_media_card", return_value=(False, "")):
            assert _classify_volume(str(tmp_path)) is None

    def test_returns_dict_for_valid_card(self, tmp_path):
        info = self._diskutil_plist()
        with patch("utils.volume_watcher._diskutil_info", return_value=info), \
             patch("utils.volume_watcher._looks_like_media_card", return_value=(True, "DCIM")):
            result = _classify_volume(str(tmp_path))
        assert result is not None
        assert result["looks_like_media_card"] is True
        assert result["marker"] == "DCIM"
        assert result["removable"] is True
        assert result["ejectable"] is True

    def test_label_is_sanitised_volume_name(self, tmp_path):
        info = self._diskutil_plist(volume_name="NO NAME")
        with patch("utils.volume_watcher._diskutil_info", return_value=info), \
             patch("utils.volume_watcher._looks_like_media_card", return_value=(True, "DCIM")):
            result = _classify_volume(str(tmp_path))
        assert result["label"].startswith("Card_")

    def test_total_size_str_is_human_readable(self, tmp_path):
        info = self._diskutil_plist(total_size=64 * 1024 ** 3)
        with patch("utils.volume_watcher._diskutil_info", return_value=info), \
             patch("utils.volume_watcher._looks_like_media_card", return_value=(True, "DCIM")):
            result = _classify_volume(str(tmp_path))
        assert "GB" in result["total_size_str"]
