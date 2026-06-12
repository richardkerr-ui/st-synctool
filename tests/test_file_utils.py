"""Tests for utils/file_utils.py — format_bytes.

format_bytes is a pure function with no external dependencies.
"""

import pytest

from utils.file_utils import format_bytes


class TestFormatBytes:
    def test_bytes_range(self):
        assert format_bytes(0) == "0.0 B"
        assert format_bytes(1) == "1.0 B"
        assert format_bytes(1023) == "1023.0 B"

    def test_kilobytes_boundary(self):
        assert format_bytes(1024) == "1.0 KB"
        assert format_bytes(1536) == "1.5 KB"

    def test_megabytes(self):
        assert format_bytes(1024 * 1024) == "1.0 MB"
        assert format_bytes(int(1.5 * 1024 * 1024)) == "1.5 MB"

    def test_gigabytes(self):
        assert format_bytes(1024 ** 3) == "1.0 GB"

    def test_terabytes(self):
        assert format_bytes(1024 ** 4) == "1.0 TB"

    def test_petabytes_overflow(self):
        # Anything larger than 1024 TB falls through to PB
        assert format_bytes(1024 ** 5) == "1.0 PB"

    def test_fractional_display(self):
        result = format_bytes(int(2.7 * 1024 * 1024 * 1024))
        assert result.startswith("2.")
        assert "GB" in result
