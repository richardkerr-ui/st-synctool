"""Tests for core/media_verify.py — pure helper functions.

_extract_frame_number, _collect_sequence_frames, and _ffprobe_frame_count
had zero test coverage. _extract_frame_number feeds the gap-detection logic
in verify_image_sequence; a wrong parse produces a false-negative on a
corrupt sequence.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.media_verify import (
    _extract_frame_number,
    _collect_sequence_frames,
    _ffprobe_frame_count,
)


# ---------------------------------------------------------------------------
# _extract_frame_number
# ---------------------------------------------------------------------------

class TestExtractFrameNumber:
    def test_trailing_digits_extracted(self):
        assert _extract_frame_number("frame0001") == 1

    def test_leading_zeros_handled(self):
        assert _extract_frame_number("A001C002_0024") == 24

    def test_no_digits_returns_none(self):
        assert _extract_frame_number("no_numbers") is None

    def test_single_digit(self):
        assert _extract_frame_number("clip1") == 1

    def test_large_frame_number(self):
        assert _extract_frame_number("render_00001234") == 1234

    def test_digits_in_middle_ignored(self):
        # Only trailing digits count
        assert _extract_frame_number("A001_frame0099") == 99

    def test_empty_string_returns_none(self):
        assert _extract_frame_number("") is None

    def test_stem_with_only_digits(self):
        assert _extract_frame_number("0042") == 42


# ---------------------------------------------------------------------------
# _collect_sequence_frames
# ---------------------------------------------------------------------------

class TestCollectSequenceFrames:
    def test_collects_dpx_files(self, tmp_path):
        (tmp_path / "frame0001.dpx").touch()
        (tmp_path / "frame0002.dpx").touch()
        (tmp_path / "frame0003.dpx").touch()
        result = _collect_sequence_frames(tmp_path, (".dpx",))
        assert set(result.keys()) == {1, 2, 3}

    def test_collects_exr_files(self, tmp_path):
        (tmp_path / "render0010.exr").touch()
        result = _collect_sequence_frames(tmp_path, (".exr",))
        assert 10 in result

    def test_uppercase_extension_also_collected(self, tmp_path):
        (tmp_path / "frame0001.DPX").touch()
        result = _collect_sequence_frames(tmp_path, (".dpx",))
        assert 1 in result

    def test_files_without_frame_number_excluded(self, tmp_path):
        (tmp_path / "no_number.dpx").touch()
        result = _collect_sequence_frames(tmp_path, (".dpx",))
        assert len(result) == 0

    def test_non_matching_extension_excluded(self, tmp_path):
        (tmp_path / "frame0001.mov").touch()
        result = _collect_sequence_frames(tmp_path, (".dpx",))
        assert len(result) == 0

    def test_empty_directory_returns_empty_dict(self, tmp_path):
        result = _collect_sequence_frames(tmp_path, (".dpx", ".exr"))
        assert result == {}

    def test_frame_number_maps_to_filename(self, tmp_path):
        (tmp_path / "shot_0042.dpx").touch()
        result = _collect_sequence_frames(tmp_path, (".dpx",))
        assert result[42] == "shot_0042.dpx"

    def test_duplicate_frame_numbers_not_double_counted(self, tmp_path):
        # Same frame number in both .dpx and .DPX — only counted once
        (tmp_path / "frame0001.dpx").touch()
        (tmp_path / "frame0001.DPX").touch()
        result = _collect_sequence_frames(tmp_path, (".dpx",))
        assert list(result.keys()).count(1) == 1


# ---------------------------------------------------------------------------
# _ffprobe_frame_count
# ---------------------------------------------------------------------------

class TestFfprobeFrameCount:
    def _run_with(self, returncode, stdout):
        mock = MagicMock(returncode=returncode, stdout=stdout)
        with patch("subprocess.run", return_value=mock):
            return _ffprobe_frame_count(Path("/fake/clip.mov"), "ffprobe")

    def test_returns_frame_count_on_success(self):
        assert self._run_with(0, "240\n") == 240

    def test_returns_none_on_nonzero_returncode(self):
        assert self._run_with(1, "") is None

    def test_returns_none_on_empty_output(self):
        assert self._run_with(0, "") is None

    def test_returns_none_on_non_integer_output(self):
        assert self._run_with(0, "not_a_number\n") is None

    def test_returns_none_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=120)):
            result = _ffprobe_frame_count(Path("/fake/clip.mov"), "ffprobe")
        assert result is None

    def test_returns_none_on_os_error(self):
        with patch("subprocess.run", side_effect=OSError("ffprobe not found")):
            result = _ffprobe_frame_count(Path("/fake/clip.mov"), "ffprobe")
        assert result is None

    def test_strips_whitespace_from_output(self):
        assert self._run_with(0, "  120  \n") == 120
