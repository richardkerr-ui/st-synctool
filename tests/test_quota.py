"""Tests for core/quota.py (M10.2 Drive quota awareness)."""

from datetime import datetime

import pytest

from core import quota


# --------------------------------------------------------------------------- #
# (b) rclone error classification — against captured-style stderr fixtures
# --------------------------------------------------------------------------- #

# Real-world rclone stderr fragments for Google API quota / rate-limit failures.
RATE_LIMIT_FIXTURES = [
    "2026/06/12 10:01:02 ERROR : file.mov: Failed to copy: googleapi: Error 403: "
    "User Rate Limit Exceeded, userRateLimitExceeded",
    "ERROR : a.r3d: googleapi: Error 403: Rate Limit Exceeded, rateLimitExceeded",
    "ERROR : googleapi: Error 403: The daily limit has been exceeded, dailyLimitExceeded",
    "ERROR : googleapi: Error 403: quotaExceeded",
]


@pytest.mark.parametrize("stderr", RATE_LIMIT_FIXTURES)
def test_classify_rate_limit(stderr):
    cls = quota.classify_rclone_error(stderr)
    assert cls is not None
    assert cls.kind == "rate_limit"
    assert "daily upload limit" in cls.message
    assert "midnight" in cls.message


def test_classify_storage_full_beats_rate_limit():
    # "storageQuotaExceeded" contains the substring "quotaExceeded"; the more
    # specific storage-full classification must win.
    stderr = ("ERROR : googleapi: Error 403: The user's Drive storage quota has "
              "been exceeded, storageQuotaExceeded")
    cls = quota.classify_rclone_error(stderr)
    assert cls is not None
    assert cls.kind == "storage_full"
    assert "out of storage space" in cls.message


def test_classify_case_insensitive():
    assert quota.classify_rclone_error("USERRATELIMITEXCEEDED").kind == "rate_limit"


@pytest.mark.parametrize("stderr", ["", None, "some unrelated network timeout",
                                    "ERROR : permission denied"])
def test_classify_unknown_returns_none(stderr):
    assert quota.classify_rclone_error(stderr) is None


# --------------------------------------------------------------------------- #
# (a) daily upload tally — persistence and TZ-aware day boundary reset
# --------------------------------------------------------------------------- #

@pytest.fixture
def tally_path(tmp_path):
    return tmp_path / "upload_tally.json"


def test_today_uploaded_empty(tally_path):
    assert quota.today_uploaded(path=tally_path) == 0


def test_record_and_read_back(tally_path):
    now = datetime(2026, 6, 12, 9, 0, 0)
    assert quota.record_upload(100, now=now, path=tally_path) == 100
    assert quota.record_upload(50, now=now, path=tally_path) == 150
    assert quota.today_uploaded(now=now, path=tally_path) == 150


def test_record_resets_on_new_day(tally_path):
    day1 = datetime(2026, 6, 12, 23, 59, 0)
    day2 = datetime(2026, 6, 13, 0, 1, 0)
    quota.record_upload(500, now=day1, path=tally_path)
    assert quota.today_uploaded(now=day1, path=tally_path) == 500
    # New calendar day: yesterday's total is not visible and a new record starts fresh.
    assert quota.today_uploaded(now=day2, path=tally_path) == 0
    assert quota.record_upload(20, now=day2, path=tally_path) == 20
    assert quota.today_uploaded(now=day2, path=tally_path) == 20


@pytest.mark.parametrize("bad", [0, -5, None, "x", 1.5])
def test_record_ignores_non_positive_int(tally_path, bad):
    now = datetime(2026, 6, 12, 9, 0, 0)
    quota.record_upload(100, now=now, path=tally_path)
    assert quota.record_upload(bad, now=now, path=tally_path) == 100


def test_record_atomic_no_tmp_left(tally_path):
    now = datetime(2026, 6, 12, 9, 0, 0)
    quota.record_upload(100, now=now, path=tally_path)
    assert not tally_path.with_suffix(tally_path.suffix + ".tmp").exists()
    assert tally_path.exists()


def test_today_uploaded_corrupt_file(tally_path):
    tally_path.write_text("{ not json")
    assert quota.today_uploaded(path=tally_path) == 0


def test_today_uploaded_negative_value_clamped(tally_path):
    import json
    tally_path.write_text(json.dumps({"date": quota._today_str(None), "bytes": -10}))
    assert quota.today_uploaded(path=tally_path) == 0


def test_tally_floor_text(tally_path):
    now = datetime(2026, 6, 12, 9, 0, 0)
    assert quota.tally_floor_text(now=now, path=tally_path) is None
    quota.record_upload(620 * 1024 ** 3, now=now, path=tally_path)
    text = quota.tally_floor_text(now=now, path=tally_path)
    assert text is not None
    assert "At least" in text
    assert "620" in text
    assert "not counted" in text


def test_record_persist_failure_swallowed(tally_path, monkeypatch):
    # A write failure must never raise out of record_upload.
    import pathlib
    orig = pathlib.Path.write_text

    def boom(self, *a, **k):
        if str(self).endswith(".tmp"):
            raise OSError("disk full")
        return orig(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    # Should not raise; returns the computed total even though persistence failed.
    assert quota.record_upload(100, path=tally_path) == 100
