"""Tests for gui/merge_tab.py — MergeTab._build_conflict_panel.

_build_conflict_panel is brand-new from Phase 3, has 69 outgoing calls,
and had zero coverage. These tests verify the panel's structure and
initial state without exercising worker threads or real file I/O.
"""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def merge_tab(qtbot, monkeypatch):
    import gui.merge_tab as mt
    monkeypatch.setattr(mt.project_registry, "list_projects", lambda: [])
    tab = mt.MergeTab()
    qtbot.addWidget(tab)
    tab.show()
    return tab


# ---------------------------------------------------------------------------
# Panel structure — built at __init__ time
# ---------------------------------------------------------------------------

class TestConflictPanelStructure:
    def test_conflict_panel_attribute_exists(self, merge_tab):
        assert hasattr(merge_tab, "_conflict_panel")

    def test_panel_hidden_by_default(self, merge_tab):
        assert not merge_tab._conflict_panel.isVisible()

    def test_local_metadata_labels_exist(self, merge_tab):
        for attr in ("_cp_local_size", "_cp_local_mtime", "_cp_local_hash"):
            assert hasattr(merge_tab, attr), f"MergeTab missing {attr}"

    def test_server_metadata_labels_exist(self, merge_tab):
        for attr in ("_cp_server_size", "_cp_server_mtime", "_cp_server_hash"):
            assert hasattr(merge_tab, attr), f"MergeTab missing {attr}"

    def test_verdict_label_exists(self, merge_tab):
        assert hasattr(merge_tab, "_cp_verdict")

    def test_metadata_labels_start_empty(self, merge_tab):
        for attr in ("_cp_local_size", "_cp_local_mtime", "_cp_local_hash",
                     "_cp_server_size", "_cp_server_mtime", "_cp_server_hash",
                     "_cp_verdict"):
            assert getattr(merge_tab, attr).text() == ""


# ---------------------------------------------------------------------------
# Panel visibility — toggled by conflict_selected signal
# ---------------------------------------------------------------------------

class TestConflictPanelVisibility:
    def _conflict_result(self):
        from core.comparison import DiffResult, DiffState
        return DiffResult(
            path="clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry={"size": 1024, "modtime": "2026-06-10T10:00:00+00:00",
                         "checksums": {"sha256": "aabbcc"}},
            server_entry={"size": 2048, "modtime": "2026-06-10T12:00:00+00:00",
                          "checksums": {"sha256": "ddeeff"}},
        )

    def test_panel_shows_when_conflict_selected(self, merge_tab, qtbot):
        merge_tab.diff_table.conflict_selected.emit(self._conflict_result())
        assert merge_tab._conflict_panel.isVisible()

    def test_panel_hides_when_none_emitted(self, merge_tab, qtbot):
        merge_tab.diff_table.conflict_selected.emit(self._conflict_result())
        assert merge_tab._conflict_panel.isVisible()
        merge_tab.diff_table.conflict_selected.emit(None)
        assert not merge_tab._conflict_panel.isVisible()

    def test_local_size_populated_on_selection(self, merge_tab, qtbot):
        merge_tab.diff_table.conflict_selected.emit(self._conflict_result())
        assert merge_tab._cp_local_size.text() != ""

    def test_server_size_populated_on_selection(self, merge_tab, qtbot):
        merge_tab.diff_table.conflict_selected.emit(self._conflict_result())
        assert merge_tab._cp_server_size.text() != ""

    def test_hash_label_shows_truncated_hash(self, merge_tab, qtbot):
        merge_tab.diff_table.conflict_selected.emit(self._conflict_result())
        assert "aabbcc" in merge_tab._cp_local_hash.text()
        assert "ddeeff" in merge_tab._cp_server_hash.text()


# ---------------------------------------------------------------------------
# Unresolved counter label — lives adjacent to the conflict panel
# ---------------------------------------------------------------------------

class TestUnresolvedCounterLabel:
    def test_unresolved_label_exists(self, merge_tab):
        assert hasattr(merge_tab, "_unresolved_lbl")

    def test_unresolved_label_hidden_before_scan(self, merge_tab):
        assert not merge_tab._unresolved_lbl.isVisible()

    def test_counter_updates_after_results_loaded(self, merge_tab, qtbot):
        from core.comparison import DiffResult, DiffState
        results = [
            DiffResult(
                path=f"clip{i}.mov",
                state=DiffState.BOTH_CHANGED,
                yours_entry={"size": 1, "checksums": {"sha256": f"aa{i}"}},
                server_entry={"size": 2, "checksums": {"sha256": f"bb{i}"}},
            )
            for i in range(3)
        ]
        merge_tab.diff_table.load_results(results)
        # Trigger the counter update the same way the tab does post-scan
        merge_tab._update_unresolved_count()
        assert merge_tab._unresolved_lbl.isVisible()
        assert "3" in merge_tab._unresolved_lbl.text()


# ---------------------------------------------------------------------------
# Diff-table state pill rendering (regression for the lost colour coding)
# ---------------------------------------------------------------------------

class TestStatePillStyling:
    """The state pill paints a coloured rounded fill via an ID-selector
    stylesheet. Two prior bugs blanked it: (1) a selector-less inline sheet was
    outranked by the app-wide `QWidget`/`QLabel` rules, and (2) an unbalanced
    `}}` made Qt fail to parse the sheet entirely. Both must stay fixed."""

    def _pill(self, merge_tab, state):
        from PyQt6.QtWidgets import QLabel
        from core.comparison import DiffResult
        merge_tab.diff_table.load_results([DiffResult(path="x.txt", state=state)])
        return merge_tab.diff_table.cellWidget(0, 1).findChild(QLabel)

    def test_pill_has_id_selector(self, merge_tab):
        from core.comparison import DiffState
        pill = self._pill(merge_tab, DiffState.SERVER_ONLY)
        assert pill.objectName() == "mergeStatePill"
        assert pill.styleSheet().startswith("QLabel#mergeStatePill {")

    def test_pill_braces_balanced(self, merge_tab):
        # The `}}` regression left an extra brace, so Qt dropped the whole sheet.
        from core.comparison import DiffState
        qss = self._pill(merge_tab, DiffState.SERVER_ONLY).styleSheet()
        assert qss.count("{") == qss.count("}") == 1

    def test_pill_carries_bucket_background(self, merge_tab):
        import gui.theme as theme
        from core.comparison import DiffState
        bg, _ = theme.merge_pill("SERVER_ONLY")
        assert bg in self._pill(merge_tab, DiffState.SERVER_ONLY).styleSheet()

    def test_distinct_buckets_distinct_colours(self, merge_tab):
        from core.comparison import DiffState
        out = self._pill(merge_tab, DiffState.LOCAL_ONLY).styleSheet()
        incoming = self._pill(merge_tab, DiffState.SERVER_ONLY).styleSheet()
        assert out != incoming
