"""Tests for core/thumbnail.py — _format_duration, ffmpeg_available, pillow_available.

These helpers had zero test coverage. _format_duration is pure; the
availability functions are mocked at the shutil.which layer.
"""

import sys
from unittest.mock import patch

import pytest

from core.thumbnail import _format_duration, ffmpeg_available, pillow_available


class TestFormatDuration:
    def test_none_returns_question_mark(self):
        assert _format_duration(None) == "?"

    def test_zero_seconds(self):
        assert _format_duration(0) == "0:00"

    def test_sub_minute(self):
        assert _format_duration(45) == "0:45"

    def test_one_minute(self):
        assert _format_duration(60) == "1:00"

    def test_minutes_and_seconds(self):
        assert _format_duration(90) == "1:30"
        assert _format_duration(125) == "2:05"

    def test_exactly_one_hour(self):
        assert _format_duration(3600) == "1:00:00"

    def test_hours_minutes_seconds(self):
        assert _format_duration(3661) == "1:01:01"
        assert _format_duration(7384) == "2:03:04"

    def test_sub_hour_no_hours_prefix(self):
        # Hours component = 0 → omit the leading "0:"
        result = _format_duration(3599)
        assert result.startswith("59:")
        assert ":" in result
        assert result.count(":") == 1

    def test_fractional_seconds_truncated(self):
        # Should truncate, not round
        assert _format_duration(1.9) == "0:01"
        assert _format_duration(59.99) == "0:59"


class TestFfmpegAvailable:
    def test_true_when_both_present(self):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            assert ffmpeg_available() is True

    def test_false_when_ffmpeg_missing(self):
        def _which(name):
            return None if name == "ffmpeg" else "/usr/bin/ffprobe"
        with patch("shutil.which", side_effect=_which):
            assert ffmpeg_available() is False

    def test_false_when_ffprobe_missing(self):
        def _which(name):
            return "/usr/bin/ffmpeg" if name == "ffmpeg" else None
        with patch("shutil.which", side_effect=_which):
            assert ffmpeg_available() is False

    def test_false_when_both_missing(self):
        with patch("shutil.which", return_value=None):
            assert ffmpeg_available() is False


class TestPillowAvailable:
    def test_true_when_pil_importable(self):
        import importlib
        # PIL ships as part of Pillow — if it's installed this just returns True
        try:
            import PIL  # noqa: F401
            assert pillow_available() is True
        except ImportError:
            pytest.skip("Pillow not installed in this environment")

    def test_false_when_pil_not_importable(self):
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _fake_import(name, *args, **kwargs):
            if name == "PIL":
                raise ImportError("mocked missing PIL")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            assert pillow_available() is False
