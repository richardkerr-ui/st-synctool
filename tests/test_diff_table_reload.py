"""Regression test for gui/diff_table.py — conflict panel staleness on reload.

Bug: load_results called setRowCount(len) without clearing first. When two
consecutive scans produced the same row count, the selection on row 0 persisted
and itemSelectionChanged never re-fired, so the conflict detail panel kept
showing the previous scan's row (e.g. a one-sided rename row with no server
side) even though the table now displayed a different file.
"""
import pytest

from core.comparison import DiffResult, DiffState


def _entry(sha):
    return {"type": "file", "size": 10, "modtime": "2026-01-01T00:00:00+00:00",
            "checksums": {"sha256": sha}}


@pytest.fixture
def table(qtbot):
    from gui.diff_table import DiffTable
    t = DiffTable()
    qtbot.addWidget(t)
    return t


def test_reload_clears_stale_selection(table):
    # First scan: one BOTH_CHANGED row that exists only locally (no server side).
    first = [DiffResult("new.mov", DiffState.BOTH_CHANGED,
                        yours_entry=_entry("A"), server_entry=None)]
    table.load_results(first)
    table.selectRow(0)
    assert table.currentRow() == 0

    # Second scan, same row count, different file with a real server entry.
    second = [DiffResult("edit.prproj", DiffState.BOTH_CHANGED,
                         yours_entry=_entry("B"), server_entry=_entry("C"))]
    table.load_results(second)

    # Selection must not silently persist on row 0 from the previous load,
    # which is what left the conflict panel showing stale data.
    assert table.currentRow() == -1

    # And the stored result for the now-selected-by-user row carries the server
    # entry, so the panel will populate the SERVER column when clicked.
    table.selectRow(0)
    emitted = table._diff_results[table.item(0, 0).text()]
    assert emitted.server_entry is not None
    assert emitted.server_entry["checksums"]["sha256"] == "C"
