"""Tests for M9.3 History presentation/query layer (core/history.py)."""

from datetime import date

import pytest

from core import history


def _rec(**kw):
    base = dict(operation="offload", timestamp="2026-06-12T14:30:00",
                workstation="Cart 3", user="dit", project="ProjectX",
                source="A001", dests=["NAS", "Shuttle"], file_count=312,
                bytes=1288490188, verdict="VERIFIED", log_filename="custody_a001.txt")
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# format_row + to_text
# --------------------------------------------------------------------------- #

def test_format_row_full():
    row = history.format_row(_rec())
    assert row.date_label == "Jun 12"
    assert row.workstation == "Cart 3"
    assert row.operation_label == "Offload"
    assert row.source == "A001"
    assert row.dests == ["NAS", "Shuttle"]
    assert row.file_count == 312
    assert row.verdict == "VERIFIED"
    assert row.log_filename == "custody_a001.txt"


def test_to_text_full_row():
    text = history.format_row(_rec()).to_text()
    assert text.startswith("Jun 12 · Cart 3 · Offload · A001 → NAS, Shuttle · 312 files · ")
    assert text.endswith("· VERIFIED")
    assert "GiB" in text  # 1.2 GiB humanized


def test_bytes_label_binary():
    row = history.format_row(_rec(bytes=1073741824))
    assert row.bytes_label == "1.0 GiB"


def test_to_text_omits_empty_segments():
    row = history.format_row(_rec(source="", dests=[], file_count=0,
                                  bytes=0, verdict=""))
    assert row.to_text() == "Jun 12 · Cart 3 · Offload"


def test_to_text_source_only_no_dests():
    text = history.format_row(_rec(dests=[])).to_text()
    assert "A001 ·" in text  # arrow segment is just the source
    assert "→" not in text


def test_operation_label_capitalizes():
    assert history.format_row(_rec(operation="verify")).operation_label == "Verify"
    assert history.format_row(_rec(operation="")).operation_label == ""


def test_date_label_bad_timestamp_falls_back():
    assert history.format_row(_rec(timestamp="not-a-date")).date_label == "not-a-date"


# --------------------------------------------------------------------------- #
# distinct_values
# --------------------------------------------------------------------------- #

def test_distinct_values_sorted_and_deduped():
    recs = [_rec(workstation="Cart 3"), _rec(workstation="Cart 1"),
            _rec(workstation="Cart 3"), _rec(workstation="")]
    assert history.distinct_values(recs, "workstation") == ["Cart 1", "Cart 3"]


def test_distinct_values_operation():
    recs = [_rec(operation="offload"), _rec(operation="merge"),
            _rec(operation="offload")]
    assert history.distinct_values(recs, "operation") == ["merge", "offload"]


# --------------------------------------------------------------------------- #
# filter_by_date
# --------------------------------------------------------------------------- #

def test_filter_by_date_none_returns_all():
    recs = [_rec(), _rec(timestamp="2026-01-01T00:00:00")]
    assert history.filter_by_date(recs) == recs


def test_filter_by_date_inclusive_bounds():
    recs = [
        _rec(timestamp="2026-06-10T09:00:00"),
        _rec(timestamp="2026-06-12T09:00:00"),
        _rec(timestamp="2026-06-15T09:00:00"),
    ]
    out = history.filter_by_date(recs, start=date(2026, 6, 12), end=date(2026, 6, 12))
    assert [r["timestamp"] for r in out] == ["2026-06-12T09:00:00"]


def test_filter_by_date_open_start():
    recs = [_rec(timestamp="2026-06-10T09:00:00"), _rec(timestamp="2026-06-15T09:00:00")]
    out = history.filter_by_date(recs, end=date(2026, 6, 11))
    assert [r["timestamp"] for r in out] == ["2026-06-10T09:00:00"]


def test_filter_by_date_drops_unparseable_when_bound_active():
    recs = [_rec(timestamp="garbage"), _rec(timestamp="2026-06-12T09:00:00")]
    out = history.filter_by_date(recs, start=date(2026, 6, 1))
    assert [r["timestamp"] for r in out] == ["2026-06-12T09:00:00"]


# --------------------------------------------------------------------------- #
# query_history + rows_for
# --------------------------------------------------------------------------- #

def test_query_history_newest_first():
    recs = [
        _rec(timestamp="2026-06-10T09:00:00"),
        _rec(timestamp="2026-06-15T09:00:00"),
        _rec(timestamp="2026-06-12T09:00:00"),
    ]
    out = history.query_history(recs)
    assert [r["timestamp"] for r in out] == [
        "2026-06-15T09:00:00", "2026-06-12T09:00:00", "2026-06-10T09:00:00"]


def test_query_history_oldest_first():
    recs = [_rec(timestamp="2026-06-15T09:00:00"), _rec(timestamp="2026-06-10T09:00:00")]
    out = history.query_history(recs, newest_first=False)
    assert out[0]["timestamp"] == "2026-06-10T09:00:00"


def test_query_history_combined_filters():
    recs = [
        _rec(operation="offload", workstation="Cart 1", timestamp="2026-06-12T09:00:00"),
        _rec(operation="merge", workstation="Cart 1", timestamp="2026-06-12T10:00:00"),
        _rec(operation="offload", workstation="Cart 2", timestamp="2026-06-12T11:00:00"),
        _rec(operation="offload", workstation="Cart 1", timestamp="2026-05-01T09:00:00"),
    ]
    out = history.query_history(recs, operation="offload", workstation="Cart 1",
                                start=date(2026, 6, 1))
    assert len(out) == 1
    assert out[0]["timestamp"] == "2026-06-12T09:00:00"


def test_rows_for_returns_formatted_rows():
    recs = [_rec(timestamp="2026-06-15T09:00:00"), _rec(timestamp="2026-06-10T09:00:00")]
    rows = history.rows_for(recs)
    assert all(isinstance(r, history.HistoryRow) for r in rows)
    assert rows[0].date_label == "Jun 15"  # newest first


def test_rows_for_respects_filter():
    recs = [_rec(operation="offload"), _rec(operation="verify")]
    rows = history.rows_for(recs, operation="verify")
    assert len(rows) == 1
    assert rows[0].operation_label == "Verify"
