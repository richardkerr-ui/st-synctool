"""Tests for core/thumbnail.py — format_helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.thumbnail import _format_bytes, _format_duration, _format_size, _load_fonts


# ---------------------------------------------------------------------------
# TestFormatDuration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_none_returns_question_mark(self):
        assert _format_duration(None) == "?"

    def test_zero_seconds(self):
        assert _format_duration(0) == "0:00"

    def test_sub_minute(self):
        assert _format_duration(45) == "0:45"

    def test_exactly_one_minute(self):
        assert _format_duration(60) == "1:00"

    def test_minutes_and_seconds(self):
        assert _format_duration(90) == "1:30"
        assert _format_duration(125) == "2:05"

    def test_exactly_one_hour(self):
        assert _format_duration(3600) == "1:00:00"

    def test_hours_minutes_seconds(self):
        assert _format_duration(3661) == "1:01:01"
        assert _format_duration(7384) == "2:03:04"

    def test_sub_hour_omits_hours_prefix(self):
        result = _format_duration(3599)
        assert result.startswith("59:")
        assert result.count(":") == 1

    def test_fractional_seconds_truncated(self):
        # int() truncates toward zero — must not round up
        assert _format_duration(1.9) == "0:01"
        assert _format_duration(59.99) == "0:59"

    def test_large_value(self):
        # 99 hours 59 minutes 59 seconds
        assert _format_duration(359999) == "99:59:59"


# ---------------------------------------------------------------------------
# TestFormatBytes
# ---------------------------------------------------------------------------


class TestFormatBytes:
    def test_bytes_range(self):
        assert _format_bytes(0.0) == "0.0 B"

    def test_bytes_below_kb_boundary(self):
        assert _format_bytes(1023.0) == "1023.0 B"

    def test_exactly_one_kb(self):
        assert _format_bytes(1024.0) == "1.0 KB"

    def test_megabyte_range(self):
        result = _format_bytes(1024.0 * 1024)
        assert result == "1.0 MB"

    def test_gigabyte_range(self):
        result = _format_bytes(1024.0 ** 3)
        assert result == "1.0 GB"

    def test_terabyte_range(self):
        result = _format_bytes(1024.0 ** 4)
        assert result == "1.0 TB"

    def test_petabyte_overflow(self):
        # Anything >= 1024 TB falls through to the PB fallback
        result = _format_bytes(1024.0 ** 5)
        assert result == "1.0 PB"

    def test_fractional_kb(self):
        result = _format_bytes(1536.0)   # 1.5 KB
        assert result == "1.5 KB"

    def test_fractional_mb(self):
        result = _format_bytes(1024.0 * 2.7)
        assert result == "2.7 KB"

    def test_very_small_value(self):
        assert _format_bytes(1.0) == "1.0 B"


# ---------------------------------------------------------------------------
# TestFormatSize
# ---------------------------------------------------------------------------


class TestFormatSize:
    def test_happy_path_small_file(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_bytes(b"hello")          # 5 bytes
        result = _format_size(f)
        assert result == "5.0 B"

    def test_happy_path_kilobytes(self, tmp_path):
        f = tmp_path / "medium.bin"
        f.write_bytes(b"x" * 2048)      # 2 KB
        result = _format_size(f)
        assert result == "2.0 KB"

    def test_missing_file_returns_question_mark(self, tmp_path):
        ghost = tmp_path / "does_not_exist.bin"
        assert _format_size(ghost) == "?"

    def test_stat_exception_returns_question_mark(self, tmp_path):
        f = tmp_path / "file.bin"
        f.write_bytes(b"data")
        with patch.object(Path, "stat", side_effect=OSError("permission denied")):
            result = _format_size(f)
        assert result == "?"

    def test_megabyte_range(self, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * (1024 * 1024))   # 1 MB exactly
        result = _format_size(f)
        assert result == "1.0 MB"

    def test_gigabyte_shown_with_stat_mock(self, tmp_path):
        f = tmp_path / "huge.bin"
        f.write_bytes(b"x")
        mock_stat = MagicMock()
        mock_stat.st_size = 1024 ** 3       # 1 GB
        with patch.object(Path, "stat", return_value=mock_stat):
            result = _format_size(f)
        assert result == "1.0 GB"

    def test_terabyte_shown_with_stat_mock(self, tmp_path):
        f = tmp_path / "archive.bin"
        f.write_bytes(b"x")
        mock_stat = MagicMock()
        mock_stat.st_size = 1024 ** 4       # 1 TB
        with patch.object(Path, "stat", return_value=mock_stat):
            result = _format_size(f)
        assert result == "1.0 TB"


# ---------------------------------------------------------------------------
# TestLoadFonts
# ---------------------------------------------------------------------------


class TestLoadFonts:
    """_load_fonts returns a 3-tuple of PIL font objects.

    All PIL calls are mocked so no font files or Pillow installation
    are required at test time.
    """

    def _make_font_mock(self):
        """Return a fresh sentinel font object."""
        return MagicMock(name="PILFont")

    def test_returns_three_tuple(self):
        fake_font = self._make_font_mock()
        with patch("PIL.ImageFont.truetype", return_value=fake_font) as mock_tt:
            result = _load_fonts()
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_truetype_called_with_size_16_14_12(self):
        font_calls = []

        def _record_truetype(path, size):
            font_calls.append(size)
            return MagicMock(name=f"font_{size}")

        with patch("PIL.ImageFont.truetype", side_effect=_record_truetype):
            _load_fonts()

        # Sizes must include 16 (bold-lg), 14 (md), 12 (sm)
        assert 16 in font_calls
        assert 14 in font_calls
        assert 12 in font_calls

    def test_falls_back_to_load_default_when_truetype_fails(self):
        default_font = MagicMock(name="DefaultFont")

        with patch("PIL.ImageFont.truetype", side_effect=OSError("no font file")):
            with patch("PIL.ImageFont.load_default", return_value=default_font) as mock_default:
                result = _load_fonts()

        # load_default must have been called (at least once per slot)
        assert mock_default.call_count >= 3
        # Every element should be the default sentinel
        assert all(f is default_font for f in result)

    def test_partial_fallback_second_candidate_succeeds(self):
        """If the first font path raises, the second candidate should succeed."""
        call_count = {"n": 0}

        def _selective_truetype(path, size):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("first path missing")
            return MagicMock(name=f"font_{size}_ok")

        with patch("PIL.ImageFont.truetype", side_effect=_selective_truetype):
            with patch("PIL.ImageFont.load_default") as mock_default:
                result = _load_fonts()

        # load_default must NOT have been called — second candidate covered it
        mock_default.assert_not_called()
        assert len(result) == 3

    def test_all_three_slots_are_independent(self):
        """Each slot (bold-lg, md, sm) gets its own font object."""
        fonts_returned = [MagicMock(name=f"f{i}") for i in range(10)]
        idx = {"i": 0}

        def _next_font(path, size):
            f = fonts_returned[idx["i"]]
            idx["i"] += 1
            return f

        with patch("PIL.ImageFont.truetype", side_effect=_next_font):
            bold_lg, md, sm = _load_fonts()

        # All three should be distinct mock objects
        assert bold_lg is not md
        assert md is not sm
        assert bold_lg is not sm
