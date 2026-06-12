"""Tests for gui/merge_tab.py — _manifest_age_days_from_iso.

This helper parses ISO timestamps and returns integer day deltas. It has
no external dependencies but uses datetime.now(timezone.utc), which we
freeze so the tests are deterministic.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from gui.merge_tab import _manifest_age_days_from_iso


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestManifestAgeDaysFromIso:
    def _now(self):
        return datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)

    def _call(self, iso_str: str) -> int:
        with patch("gui.merge_tab.datetime") as mock_dt:
            mock_dt.fromisoformat.side_effect = datetime.fromisoformat
            mock_dt.now.return_value = self._now()
            return _manifest_age_days_from_iso(iso_str)

    def test_empty_string_returns_zero(self):
        assert _manifest_age_days_from_iso("") == 0

    def test_none_equivalent_returns_zero(self):
        # The guard is `if not iso_str`, so None would also hit it —
        # but callers always pass strings; empty string is the real edge case.
        assert _manifest_age_days_from_iso("") == 0

    def test_today_returns_zero(self):
        result = self._call(_iso(self._now()))
        assert result == 0

    def test_yesterday_returns_one(self):
        yesterday = self._now() - timedelta(days=1)
        result = self._call(_iso(yesterday))
        assert result == 1

    def test_seven_days_ago(self):
        week_ago = self._now() - timedelta(days=7)
        result = self._call(_iso(week_ago))
        assert result == 7

    def test_future_date_returns_zero(self):
        # max(0, ...) clamps negative deltas
        future = self._now() + timedelta(days=3)
        result = self._call(_iso(future))
        assert result == 0

    def test_invalid_string_returns_zero(self):
        assert _manifest_age_days_from_iso("not-a-date") == 0

    def test_malformed_iso_returns_zero(self):
        assert _manifest_age_days_from_iso("2026-13-45T99:99:99") == 0
