"""Tests for M10.1 safe-to-format clearance (core/clearance.py)."""

from dataclasses import dataclass, field
from typing import Optional

import pytest

from core.clearance import (
    MIN_CLEAN_DESTS,
    ClearanceVerdict,
    compute_clearance,
)


# Lightweight stand-in for CellResult — clearance reads only these attributes,
# and by design does not import core.offload (avoids the circular import).
@dataclass
class FakeCell:
    source_label: str
    dest_label: str
    state: str = "done"
    verified: Optional[bool] = True
    media_verify_log: list = field(default_factory=list)
    # M14.1: a clean cell is integrity-verified by default (a real content-hash
    # compare). Set False to model an rclone size+modtime-only "pass".
    integrity_verified: bool = True


def _clean(src, dest):
    return FakeCell(src, dest, state="done", verified=True, integrity_verified=True)


def _size_modtime_only(src, dest):
    # Committed and "passed", but only by size+modtime — no confirmed hash compare.
    return FakeCell(src, dest, state="done", verified=True, integrity_verified=False)


# ── Cleared (green) ────────────────────────────────────────────────────────

def test_two_clean_dests_is_cleared():
    results = [_clean("A001", "NAS"), _clean("A001", "Shuttle")]
    v = compute_clearance("A001", results)
    assert v.cleared is True
    assert v.clean_dest_count == 2
    assert v.total_dest_count == 2
    assert v.reason == ""
    assert "safe to format" in v.to_text().lower()
    assert "A001" in v.to_text()


def test_three_clean_dests_is_cleared():
    results = [_clean("A001", d) for d in ("NAS", "Shuttle", "LTO")]
    v = compute_clearance("A001", results)
    assert v.cleared is True
    assert v.clean_dest_count == 3


def test_min_clean_dests_is_two():
    assert MIN_CLEAN_DESTS == 2


# ── M14.1 integrity-verified gate ─────────────────────────────────────────────

def test_a_one_clean_integrity_dest_not_cleared():
    # (a) 1 clean integrity-verified destination → cleared=False.
    v = compute_clearance("A001", [_clean("A001", "NAS")])
    assert v.cleared is False
    assert v.clean_dest_count == 1


def test_b_two_dests_one_size_modtime_only_not_cleared():
    # (b) 2 destinations, one verified by size+modtime only → cleared=False.
    results = [_clean("A001", "NAS"), _size_modtime_only("A001", "Shuttle")]
    v = compute_clearance("A001", results)
    assert v.cleared is False
    assert v.clean_dest_count == 1   # the size+modtime dest does not count
    assert "do not count" in v.reason


def test_c_two_integrity_verified_dests_cleared():
    # (c) 2 integrity-verified destinations → cleared=True.
    results = [_clean("A001", "NAS"), _clean("A001", "Shuttle")]
    v = compute_clearance("A001", results)
    assert v.cleared is True
    assert v.clean_dest_count == 2


def test_d_two_dests_one_never_verified_not_cleared():
    # (d) 2 destinations where one never reached the verify step → cleared=False.
    unverified = FakeCell("A001", "Shuttle", state="done", verified=None)
    results = [_clean("A001", "NAS"), unverified]
    v = compute_clearance("A001", results)
    assert v.cleared is False


def test_size_modtime_only_does_not_block_when_enough_integrity_dests():
    # The common 2-local-disks + Drive case: two integrity-verified local disks
    # clear even when a third (Drive, pre-M15.2) is integrity-unconfirmed.
    results = [
        _clean("A001", "Disk1"),
        _clean("A001", "Disk2"),
        _size_modtime_only("A001", "Drive"),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is True
    assert v.clean_dest_count == 2


def test_default_integrity_verified_is_false_on_real_cellresult():
    # Absence of a confirmed hash compare is not a hash compare: the real
    # CellResult defaults integrity_verified to False.
    from core.offload import CellResult
    cell = CellResult(source_label="A001", dest_label="NAS")
    assert cell.integrity_verified is False


# ── Insufficient redundancy ──────────────────────────────────────────────────

def test_single_clean_dest_not_cleared():
    v = compute_clearance("A001", [_clean("A001", "NAS")])
    assert v.cleared is False
    assert v.clean_dest_count == 1
    assert "at least 2" in v.reason
    assert v.to_text().startswith("Not cleared:")


# ── Failures ────────────────────────────────────────────────────────────────

def test_any_failed_state_blocks_clearance():
    results = [
        _clean("A001", "NAS"),
        _clean("A001", "Shuttle"),
        FakeCell("A001", "LTO", state="failed", verified=None),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is False
    assert "failed" in v.reason
    assert "1 of 3" in v.reason


def test_verified_false_blocks_clearance():
    results = [
        _clean("A001", "NAS"),
        FakeCell("A001", "Shuttle", state="done", verified=False),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is False
    assert "failed" in v.reason


def test_media_verify_failed_blocks_clearance():
    results = [
        _clean("A001", "NAS"),
        FakeCell(
            "A001", "Shuttle", state="done", verified=True,
            media_verify_log=["MEDIA VERIFY FAILED: A001_C002.mov truncated"],
        ),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is False
    assert "failed" in v.reason


def test_media_verify_advisory_does_not_block():
    results = [
        _clean("A001", "NAS"),
        FakeCell(
            "A001", "Shuttle", state="done", verified=True,
            media_verify_log=["MEDIA VERIFY ADVISORY: A001_C002.mov no sidecar"],
        ),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is True


# ── Unverified (partial) ─────────────────────────────────────────────────────

def test_unverified_dest_not_cleared():
    results = [
        _clean("A001", "NAS"),
        FakeCell("A001", "Shuttle", state="done", verified=None),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is False
    assert "not verified" in v.reason


def test_skipped_state_counts_as_unverified():
    results = [
        _clean("A001", "NAS"),
        FakeCell("A001", "Shuttle", state="skipped", verified=None),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is False
    assert "not verified" in v.reason


def test_failure_outranks_unverified_in_reason():
    results = [
        _clean("A001", "NAS"),
        FakeCell("A001", "Shuttle", state="done", verified=None),
        FakeCell("A001", "LTO", state="failed", verified=False),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is False
    assert "failed" in v.reason  # failure reported ahead of the unverified one


# ── Filtering and edge cases ─────────────────────────────────────────────────

def test_only_matching_source_considered():
    results = [
        _clean("A001", "NAS"),
        _clean("A001", "Shuttle"),
        FakeCell("B002", "NAS", state="failed", verified=False),  # other source
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is True
    assert v.total_dest_count == 2  # B002's cell excluded


def test_no_destinations_for_source():
    v = compute_clearance("A001", [_clean("B002", "NAS")])
    assert v.cleared is False
    assert v.total_dest_count == 0
    assert "no destinations" in v.reason


def test_empty_results():
    v = compute_clearance("A001", [])
    assert v.cleared is False
    assert v.total_dest_count == 0


def test_verdict_is_frozen():
    v = compute_clearance("A001", [_clean("A001", "NAS")])
    with pytest.raises(Exception):
        v.cleared = True  # type: ignore[misc]


def test_accepts_enum_like_state():
    @dataclass
    class EnumLike:
        value: str

    results = [
        FakeCell("A001", "NAS", state=EnumLike("done"), verified=True),
        FakeCell("A001", "Shuttle", state=EnumLike("done"), verified=True),
    ]
    v = compute_clearance("A001", results)
    assert v.cleared is True


# ── M12.2 cross-run aggregation (prior_clean_dests) ──────────────────────────

def test_prior_dest_plus_one_current_clears():
    # Card was on NAS in an earlier run; this run adds Shuttle → 2 distinct → safe.
    results = [_clean("A001", "Shuttle")]
    v = compute_clearance("A001", results, prior_clean_dests={"NAS"})
    assert v.cleared is True
    assert v.clean_dest_count == 2
    assert v.from_earlier_count == 1


def test_prior_same_dest_not_double_counted():
    # Re-copying to the same destination adds no redundancy.
    results = [_clean("A001", "NAS")]
    v = compute_clearance("A001", results, prior_clean_dests={"NAS"})
    assert v.cleared is False
    assert v.clean_dest_count == 1
    assert v.from_earlier_count == 0


def test_current_failure_blocks_even_with_two_prior():
    # A fresh failure must not be hidden by past success.
    results = [FakeCell("A001", "Shuttle", state="failed", verified=False)]
    v = compute_clearance("A001", results, prior_clean_dests={"NAS", "LTO"})
    assert v.cleared is False
    assert "failed" in v.reason


def test_cleared_text_notes_earlier_offloads():
    results = [_clean("A001", "Shuttle")]
    v = compute_clearance("A001", results, prior_clean_dests={"NAS"})
    assert "earlier" in v.to_text().lower()


def test_two_prior_no_current_cells_clears():
    # Source already fully redundant from earlier runs, nothing new this run.
    v = compute_clearance("A001", [], prior_clean_dests={"NAS", "LTO"})
    assert v.cleared is True
    assert v.from_earlier_count == 2
