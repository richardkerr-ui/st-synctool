"""Tests for M12.1 destination free-space preflight (core/space.py)."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.space import (
    HEADROOM_MIN_BYTES,
    OffloadSpaceError,
    SpaceVerdict,
    all_clear,
    blocking_message,
    check_destination_space,
    total_source_bytes,
)

GB = 1024 ** 3


# Lightweight stand-ins — space.py reads only .label/.path/.enabled.
@dataclass
class FakeDest:
    label: str
    path: Path
    enabled: bool = True


@dataclass
class FakeSource:
    label: str
    path: Path
    enabled: bool = True


def _dests(spec):
    """spec: {label: free_bytes}. Returns (dests, free_fn)."""
    free = {label: fb for label, fb in spec.items()}
    dests = [FakeDest(label, Path(f"/vol/{label}")) for label in spec]
    return dests, (lambda p: free[p.name])


# ── check_destination_space ─────────────────────────────────────────────────

def test_comfortable_fit_passes():
    dests, free_fn = _dests({"A": 100 * GB})
    [v] = check_destination_space(10 * GB, dests, free_fn=free_fn)
    assert v.ok and v.shortfall_bytes == 0
    assert all_clear([v])


def test_shortfall_fails_with_amount():
    dests, free_fn = _dests({"A": 5 * GB})
    [v] = check_destination_space(10 * GB, dests, free_fn=free_fn)
    assert not v.ok
    # short by required + headroom - free
    assert v.shortfall_bytes == (10 * GB + v.headroom_bytes) - 5 * GB
    assert "NOT ENOUGH SPACE" in v.message()


def test_headroom_blocks_an_exact_fit():
    # Free exactly equals the payload — the headroom margin must still fail it,
    # so we never fill a disk to the last byte.
    dests, free_fn = _dests({"A": 10 * GB})
    [v] = check_destination_space(10 * GB, dests, free_fn=free_fn)
    assert v.headroom_bytes > 0
    assert not v.ok


def test_just_enough_with_headroom_passes():
    required = 10 * GB
    dests, free_fn = _dests({"A": required})
    headroom = check_destination_space(required, dests, free_fn=free_fn)[0].headroom_bytes
    dests, free_fn = _dests({"A": required + headroom})
    [v] = check_destination_space(required, dests, free_fn=free_fn)
    assert v.ok


def test_mixed_verdicts_across_destinations():
    dests, free_fn = _dests({"Big": 100 * GB, "Small": 1 * GB})
    verdicts = check_destination_space(10 * GB, dests, free_fn=free_fn)
    by = {v.label: v for v in verdicts}
    assert by["Big"].ok
    assert not by["Small"].ok
    assert not all_clear(verdicts)


def test_drive_url_destination_skipped():
    dests = [FakeDest("Drive", Path("gdrive:Footage/A001"))]
    [v] = check_destination_space(
        10 * GB, dests, free_fn=lambda p: pytest.fail("should not probe a URL"))
    assert v.ok and v.skipped
    assert "skipped" in v.message()


def test_disabled_destination_ignored():
    dests = [FakeDest("On", Path("/vol/on")), FakeDest("Off", Path("/vol/off"), enabled=False)]
    verdicts = check_destination_space(1 * GB, dests, free_fn=lambda p: 100 * GB)
    assert [v.label for v in verdicts] == ["On"]


def test_unreadable_destination_fails_not_passes():
    def boom(p):
        raise OSError("no such volume")

    dests = [FakeDest("Gone", Path("/vol/gone"))]
    [v] = check_destination_space(1 * GB, dests, free_fn=boom)
    assert not v.ok and v.free_bytes == 0


# ── total_source_bytes ──────────────────────────────────────────────────────

def test_total_source_bytes_sums_enabled_only():
    sizes = {"S1": 3 * GB, "S2": 4 * GB, "S3": 5 * GB}
    sources = [
        FakeSource("S1", Path("/c/S1")),
        FakeSource("S2", Path("/c/S2")),
        FakeSource("S3", Path("/c/S3"), enabled=False),
    ]
    total = total_source_bytes(sources, size_fn=lambda p: sizes[p.name])
    assert total == 7 * GB  # S3 disabled


def test_empty_payload_has_no_headroom():
    dests, free_fn = _dests({"A": 1})  # tiny disk
    [v] = check_destination_space(0, dests, free_fn=free_fn)
    # zero payload must not be blocked by the headroom floor
    assert v.headroom_bytes == 0
    assert v.ok


# ── blocking_message ────────────────────────────────────────────────────────

def test_blocking_message_none_when_all_clear():
    dests, free_fn = _dests({"A": 100 * GB})
    verdicts = check_destination_space(1 * GB, dests, free_fn=free_fn)
    assert blocking_message(verdicts) is None


def test_blocking_message_lists_only_failures():
    dests, free_fn = _dests({"Big": 100 * GB, "Small": 1 * GB})
    verdicts = check_destination_space(10 * GB, dests, free_fn=free_fn)
    msg = blocking_message(verdicts)
    assert "Small" in msg
    assert "Big" not in msg


def test_headroom_floor_applies_to_small_payload():
    # A 10 MB payload still reserves the 200 MB floor, not 3% of 10 MB.
    dests, free_fn = _dests({"A": 100 * 1024 * 1024})  # 100 MB free
    [v] = check_destination_space(10 * 1024 * 1024, dests, free_fn=free_fn)
    assert v.headroom_bytes == HEADROOM_MIN_BYTES
    assert not v.ok
