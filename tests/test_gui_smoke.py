"""
GUI smoke tests — each tab and MainWindow can instantiate without crashing,
key widgets are present, and critical initial states are correct.

Uses pytest-qt's qtbot fixture for widget lifecycle management.
No workers are started; external I/O is mocked at the module boundary.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# PathInputWidget — Browse start dir + clipboard Drive-URL paste
# ---------------------------------------------------------------------------

class TestPathInputWidget:
    def test_start_dir_for_existing_dir_file_and_url(self, tmp_path):
        from gui.ui_helpers import start_dir_for
        f = tmp_path / "x.txt"; f.write_text("x")
        assert start_dir_for(str(tmp_path)) == str(tmp_path)        # dir → itself
        assert start_dir_for(str(f)) == str(tmp_path)               # file → parent
        assert start_dir_for("https://drive.google.com/x") == str(Path.home())
        assert start_dir_for("") == str(Path.home())

    def test_clipboard_paste_appears_when_drive_url_copied(self, qtbot):
        from PyQt6.QtWidgets import QApplication
        from gui.path_input_widget import PathInputWidget
        w = PathInputWidget("source"); qtbot.addWidget(w)
        QApplication.clipboard().setText("")
        # Copying a Drive link should reveal the button proactively, no focus.
        QApplication.clipboard().setText("https://drive.google.com/drive/folders/XYZ?usp=sharing")
        assert not w._paste_btn.isHidden()
        w._paste_clipboard_url()
        assert "drive.google.com" in w.text()
        assert w._paste_btn.isHidden()   # hides once filled

    def test_clipboard_paste_appears_on_app_refocus(self, qtbot):
        # Cross-app copies (browser → app) don't fire dataChanged on macOS, so
        # the hint must also refresh when the app regains focus.
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        from gui.path_input_widget import PathInputWidget
        w = PathInputWidget("source"); qtbot.addWidget(w)
        QApplication.clipboard().setText("https://drive.google.com/drive/u/0/folders/ABC")
        w._paste_btn.setVisible(False)   # simulate dataChanged having not fired
        w._on_app_state(Qt.ApplicationState.ApplicationActive)
        assert not w._paste_btn.isHidden()

    def test_clipboard_paste_hidden_for_non_url(self, qtbot):
        from PyQt6.QtWidgets import QApplication
        from gui.path_input_widget import PathInputWidget
        w = PathInputWidget("source"); qtbot.addWidget(w)
        QApplication.clipboard().setText("/Users/me/some/path")
        w._refresh_paste_hint()
        assert w._paste_btn.isHidden()

    def test_clipboard_paste_disabled_for_manifest_field(self, qtbot):
        from PyQt6.QtWidgets import QApplication
        from gui.path_input_widget import PathInputWidget
        w = PathInputWidget("base_manifest", clipboard_url=False); qtbot.addWidget(w)
        QApplication.clipboard().setText("https://drive.google.com/drive/folders/XYZ")
        w._refresh_paste_hint()
        assert w._paste_btn.isHidden()


# ---------------------------------------------------------------------------
# TransferTab
# ---------------------------------------------------------------------------

class TestTransferTabSmoke:
    @pytest.fixture
    def tab(self, qtbot):
        from gui.transfer_tab import TransferTab
        t = TransferTab()
        qtbot.addWidget(t)
        return t

    def test_instantiates(self, tab):
        tab.show()

    def test_key_widgets_present(self, tab):
        for attr in ("src_input", "dst_input", "start_btn", "conflict_combo",
                     "cancel_btn", "manifest_btn", "log", "export_mhl_chk"):
            assert hasattr(tab, attr), f"TransferTab missing: {attr}"

    def test_start_btn_label(self, tab):
        assert "Transfer" in tab.start_btn.text()

    def test_conflict_combo_has_at_least_three_options(self, tab):
        assert tab.conflict_combo.count() >= 3

    def test_has_cancel_and_browse_shortcuts(self, tab):
        from PyQt6.QtGui import QShortcut
        keys = {s.key().toString() for s in tab.findChildren(QShortcut)}
        assert "Esc" in keys and "Ctrl+O" in keys

    def test_reveal_button_hidden_until_success(self, tab):
        assert tab._reveal_btn.isHidden()

    def test_reveal_button_shows_after_local_transfer(self, tab, tmp_path):
        tab._on_finished({"errors": [], "actual_dest": str(tmp_path)})
        assert not tab._reveal_btn.isHidden()
        assert tab._last_dest == str(tmp_path)

    def test_reveal_button_stays_hidden_for_drive_dest(self, tab):
        url = "https://drive.google.com/drive/folders/abc"
        tab._on_finished({"errors": [], "actual_dest": url})
        assert tab._reveal_btn.isHidden()

    def test_preflight_values_brighten_when_paths_entered(self, tab, tmp_path):
        # Greyed-out summary regression: once both paths are set, the computed
        # values must use the active colour, not the muted #555 placeholder.
        tab.src_input.setText(str(tmp_path))
        tab.dst_input.setText(str(tmp_path))
        tab._update_preflight()
        assert "#555" not in tab._pf_src_val.styleSheet()
        from gui import theme
        assert theme.TEXT_PRIMARY in tab._pf_src_val.styleSheet()


# ---------------------------------------------------------------------------
# MergeTab
# ---------------------------------------------------------------------------

class TestMergeTabSmoke:
    @pytest.fixture
    def tab(self, qtbot, monkeypatch):
        import gui.merge_tab as mt
        monkeypatch.setattr(mt.project_registry, "list_projects", lambda: [])
        t = mt.MergeTab()
        qtbot.addWidget(t)
        return t

    def test_instantiates(self, tab):
        tab.show()

    def test_key_widgets_present(self, tab):
        for attr in ("scan_btn", "apply_btn", "newer_wins_btn", "diff_table",
                     "project_combo", "local_input", "server_input", "base_input", "log"):
            assert hasattr(tab, attr), f"MergeTab missing: {attr}"

    def test_apply_btn_initially_disabled(self, tab):
        assert not tab.apply_btn.isEnabled()

    def test_scan_btn_initially_enabled(self, tab):
        assert tab.scan_btn.isEnabled()

    # ── M2 summary header ────────────────────────────────────────────────

    def test_summary_label_hidden_before_scan(self, tab):
        assert hasattr(tab, "summary_label")
        assert not tab.summary_label.isVisible()

    def test_summary_label_renders_after_load(self, tab):
        from core.comparison import DiffResult, DiffState

        results = [
            DiffResult(path="a.mov", state=DiffState.LOCAL_ONLY),
            DiffResult(path="b.mov", state=DiffState.BOTH_CHANGED),
            DiffResult(path="c.mov", state=DiffState.DELETED_LOCAL),
        ]
        tab._diff_results = results
        tab.diff_table.load_results(results)
        tab._update_summary()
        tab.show()
        assert tab.summary_label.isVisible()
        text = tab.summary_label.text()
        assert "1 conflict needs review" in text
        assert "1 file will sync automatically" in text
        assert "1 deletion held for you" in text

    def test_summary_label_updates_on_action_change(self, tab):
        from core.comparison import DiffResult, DiffState
        from core.merge_ops import ACT_PUSH

        results = [DiffResult(path="b.mov", state=DiffState.BOTH_CHANGED)]
        tab._diff_results = results
        tab.diff_table.load_results(results)
        tab._update_summary()
        assert "1 conflict needs review" in tab.summary_label.text()

        # Changing the combo emits conflict_action_changed -> summary refresh
        combo = tab.diff_table._action_combos["b.mov"]
        combo.setCurrentText(ACT_PUSH)
        assert "1 file will sync automatically" in tab.summary_label.text()
        assert "conflict" not in tab.summary_label.text()


# ---------------------------------------------------------------------------
# OffloadTab
# ---------------------------------------------------------------------------

class TestOffloadTabSmoke:
    @pytest.fixture
    def tab(self, qtbot, monkeypatch):
        import gui.offload_tab as ot
        import core.projects as proj

        def _mock_watcher(self):
            self._watcher = MagicMock()
            self._watcher.available = False

        monkeypatch.setattr(ot.OffloadTab, "_start_volume_watcher", _mock_watcher)
        monkeypatch.setattr(proj, "list_dest_presets", lambda: [])
        t = ot.OffloadTab()
        qtbot.addWidget(t)
        return t

    def test_instantiates(self, tab):
        tab.show()

    def test_key_widgets_present(self, tab):
        for attr in ("_preset_combo", "_start_btn", "_cancel_btn", "_log",
                     "_export_mhl_chk"):
            assert hasattr(tab, attr), f"OffloadTab missing: {attr}"

    def test_one_source_row_created_at_init(self, tab):
        assert len(tab._source_rows) == 1

    def test_one_dest_row_created_at_init(self, tab):
        assert len(tab._dest_rows) == 1

    # M12.2 duplicate-card guard
    def test_duplicate_guard_proceeds_with_empty_ledger(self, tab, tmp_path, monkeypatch):
        from dataclasses import dataclass
        import core.offload_ledger as led

        @dataclass
        class S:
            label: str
            path: object
        src = tmp_path / "A001"; (src / "DCIM").mkdir(parents=True)
        (src / "DCIM" / "c.mov").write_bytes(b"data")
        monkeypatch.setattr(led, "fingerprint_source", lambda s: led.SourceFingerprint("A001", "v", 1, 4, ("DCIM",)))
        import core.projects as proj
        monkeypatch.setattr(proj, "list_offload_fingerprints", lambda: [])

        @dataclass
        class D:
            label: str
        assert tab._confirm_no_duplicate_card([S("A001", src)], [D("NAS")]) == "proceed"

    def test_already_done_dests_detects_prior_offload(self, tab, monkeypatch):
        from dataclasses import dataclass
        import core.offload_ledger as led
        import core.projects as proj

        fp = led.SourceFingerprint("A001", "RED", 3, 100, ("DCIM",))
        monkeypatch.setattr(led, "fingerprint_source", lambda s: fp)
        monkeypatch.setattr(proj, "list_offload_fingerprints",
                            lambda: [fp.to_record(["NAS"], "2026-06-14T10:00:00+00:00")])

        @dataclass
        class S:
            label: str
            path: object
        @dataclass
        class D:
            label: str
        done = tab._already_done_dests([S("A001", None)], [D("NAS"), D("LTO")])
        assert done == {"A001": ["NAS"]}   # NAS already done, LTO is new

    def test_duplicate_guard_aborts_when_user_declines(self, tab, monkeypatch):
        # Two sources → per-source confirm path uses QMessageBox.warning.
        from dataclasses import dataclass
        import core.offload_ledger as led
        import core.projects as proj
        from PyQt6.QtWidgets import QMessageBox

        fp = led.SourceFingerprint("A001", "RED", 3, 100, ("DCIM",))
        monkeypatch.setattr(led, "fingerprint_source", lambda s: fp)
        monkeypatch.setattr(proj, "list_offload_fingerprints",
                            lambda: [fp.to_record(["NAS"], "2026-06-14T10:00:00+00:00")])
        seen = {}
        def _warn(parent, title, text, *a, **k):
            seen["title"] = title
            return QMessageBox.StandardButton.No
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(_warn))

        @dataclass
        class S:
            label: str
            path: object
        @dataclass
        class D:
            label: str
        out = tab._confirm_no_duplicate_card([S("A001", None), S("B002", None)], [D("NAS")])
        assert out == "abort"
        assert "duplicate" in seen["title"].lower()

    # M12.4 completion banner
    def _cell(self, src, dst, done=True):
        from core.offload import CellResult, CellState
        r = CellResult(source_label=src, dest_label=dst)
        r.state = CellState.DONE if done else CellState.FAILED
        r.verified = True if done else False
        # M14.1: a done+verified offload cell is integrity-verified (xxh128 compare).
        r.integrity_verified = done
        return r

    def test_completion_banner_safe_when_cleared(self, tab, monkeypatch):
        from core import settings
        monkeypatch.setattr(settings, "completion_sound_enabled", lambda: False)
        results = [self._cell("A001", "NAS"), self._cell("A001", "LTO")]  # 2 clean dests
        tab._show_completion_banner(results)
        assert not tab._completion_banner.isHidden()
        assert "SAFE TO FORMAT" in tab._banner_msg.text()

    def test_completion_banner_warns_when_not_cleared(self, tab, monkeypatch):
        from core import settings
        monkeypatch.setattr(settings, "completion_sound_enabled", lambda: False)
        results = [self._cell("A001", "NAS")]  # only 1 dest → not cleared
        tab._show_completion_banner(results)
        assert not tab._completion_banner.isHidden()
        assert "DO NOT EJECT" in tab._banner_msg.text()

    # M12.6 throughput + ETA on the status line
    def test_progress_updates_rate_and_eta(self, tab):
        tab._on_progress("A001", "NAS", 10 * 1024 * 1024, 100 * 1024 * 1024)
        tab._on_progress("A001", "NAS", 60 * 1024 * 1024, 100 * 1024 * 1024)
        text = tab._offload_status_lbl.text()
        assert "/s" in text and "ETA" in text

    # Open/reveal + shortcuts + dest recall
    def test_has_cancel_and_browse_shortcuts(self, tab):
        from PyQt6.QtGui import QShortcut
        keys = {s.key().toString() for s in tab.findChildren(QShortcut)}
        assert "Esc" in keys and "Ctrl+O" in keys

    def test_recalls_last_offload_destinations(self, qtbot, monkeypatch):
        import gui.offload_tab as ot
        import core.projects as proj
        monkeypatch.setattr(ot.OffloadTab, "_start_volume_watcher",
                            lambda self: setattr(self, "_watcher", MagicMock(available=False)))
        monkeypatch.setattr(proj, "list_dest_presets", lambda: [])
        monkeypatch.setattr(proj, "get_app_setting",
                            lambda k, d=None: [{"label": "RAID_1", "path": "/Volumes/RAID_1", "enabled": True}]
                            if k == "last_offload_dests" else d)
        t = ot.OffloadTab()
        qtbot.addWidget(t)
        assert any(r.to_dict().get("path") == "/Volumes/RAID_1" for r in t._dest_rows)

    # M12.5 awake indicator
    def test_awake_indicator_hidden_at_rest(self, tab):
        assert tab._awake_lbl.isHidden()

    def test_awake_indicator_is_honest_about_the_lid(self, tab):
        # Must not promise lid-close protection; must mention the lid caution.
        text = tab._awake_lbl.text().lower()
        tip = tab._awake_lbl.toolTip().lower()
        assert "lid" in text
        assert "clamshell" in tip   # documents the real requirement


# ---------------------------------------------------------------------------
# VerifyTab
# ---------------------------------------------------------------------------

class TestVerifyTabSmoke:
    @pytest.fixture
    def tab(self, qtbot):
        from gui.verify_tab import VerifyTab
        t = VerifyTab()
        qtbot.addWidget(t)
        return t

    def test_instantiates(self, tab):
        tab.show()

    def test_key_widgets_present(self, tab):
        for attr in ("folder_input", "manifest_input", "verify_btn", "cancel_btn",
                     "status_label", "progress_bar", "log"):
            assert hasattr(tab, attr), f"VerifyTab missing: {attr}"

    def test_verify_btn_label(self, tab):
        assert "Verif" in tab.verify_btn.text()


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class TestMainWindowSmoke:
    @pytest.fixture
    def window(self, qtbot, monkeypatch):
        import gui.main_window as mw
        import gui.offload_tab as ot
        import gui.merge_tab as mt
        import core.projects as proj

        monkeypatch.setattr(mw, "should_show_wizard", lambda: False)
        monkeypatch.setattr(mw, "check_rclone_auth",
                            lambda *a, **kw: MagicMock(status=mw.CheckStatus.OK, message=""))
        monkeypatch.setattr(mw, "get_active_remote", lambda: "gdrive")
        monkeypatch.setattr(mw, "get_remote_account_email", lambda r: "")
        monkeypatch.setattr(ot.OffloadTab, "_start_volume_watcher", lambda self: None)
        monkeypatch.setattr(mt.project_registry, "list_projects", lambda: [])
        monkeypatch.setattr(proj, "list_dest_presets", lambda: [])
        # Suppress background workers that pytest-qt's event pumping would fire
        # mid-suite: the auth startup worker can call _launch_wizard (a modal that
        # aborts headlessly), and the M7.5 update-check spawns a network thread.
        # Both are correct in the live app; tests must not trigger them.
        monkeypatch.setattr(mw._StartupCheckWorker, "start", lambda self: None)
        monkeypatch.setattr(mw.MainWindow, "_start_update_check", lambda self: None)
        monkeypatch.setattr(mw.update_check, "check_for_update", lambda *a, **k: None)
        monkeypatch.setattr(mw.MainWindow, "_start_log_shipping", lambda self: None)

        w = mw.MainWindow()
        qtbot.addWidget(w)
        return w

    def test_instantiates(self, window):
        window.show()

    def test_has_five_tabs(self, window):
        assert window.tabs.count() == 5

    def test_tab_titles(self, window):
        titles = [window.tabs.tabText(i) for i in range(5)]
        assert titles == ["Transfer", "Merge", "Offload", "Verify", "History"]

    def test_status_bar_ready(self, window):
        assert "Ready" in window.statusBar().currentMessage()

    def test_auth_banner_hidden_at_startup(self, window):
        assert not window._auth_banner.isVisible()

    def test_tab_widget_types(self, window):
        from gui.transfer_tab import TransferTab
        from gui.merge_tab import MergeTab
        from gui.offload_tab import OffloadTab
        from gui.verify_tab import VerifyTab
        from gui.history_tab import HistoryTab
        assert isinstance(window._transfer_tab, TransferTab)
        assert isinstance(window._merge_tab, MergeTab)
        assert isinstance(window._offload_tab, OffloadTab)
        assert isinstance(window._verify_tab, VerifyTab)
        assert isinstance(window._history_tab, HistoryTab)

    # M7.5: update-available banner
    def test_update_banner_hidden_at_startup(self, window):
        assert not window._update_banner.isVisible()

    def test_update_banner_hidden_when_no_update(self, window):
        window._on_update_check_done(None)
        assert not window._update_banner.isVisible()

    def test_update_banner_shows_on_newer_release(self, window):
        from core.update_check import UpdateInfo
        window.show()
        window._on_update_check_done(UpdateInfo(version="v9.9.9",
                                                url="https://example/release"))
        assert window._update_banner.isVisible()
        assert "v9.9.9" in window._update_banner_label.text()
        assert window._update_url == "https://example/release"

    def test_version_label_uses_app_version(self, window):
        from core.version import __version__ as APP_VERSION
        labels = window.findChildren(type(window._update_banner_label))
        assert any(lbl.text() == f"v{APP_VERSION}" for lbl in labels)

    # M7.3: Report a Problem
    def test_feedback_button_present(self, window):
        assert hasattr(window, "_feedback_btn")
        assert "Report" in window._feedback_btn.text()

    def test_report_problem_builds_bundle(self, window, monkeypatch, tmp_path):
        import gui.main_window as mw
        from core import feedback
        dest = tmp_path / "fb.zip"
        # Stub the save dialog to return our path, and the reveal/info dialogs to no-ops.
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from PyQt6.QtGui import QDesktopServices
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(dest), "")))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda *a, **k: True))
        # Point the bundle at an empty tmp base so it does not read real logs.
        monkeypatch.setattr(feedback, "STSYNC_DIR", tmp_path)
        orig = feedback.build_feedback_zip
        monkeypatch.setattr(feedback, "build_feedback_zip",
                            lambda path, **k: orig(path, base_dir=tmp_path))
        window._report_problem()
        assert dest.exists()

    def test_report_problem_cancelled_is_noop(self, window, monkeypatch):
        from PyQt6.QtWidgets import QFileDialog
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: ("", "")))
        window._report_problem()  # must not raise

    # M9.1: pending-activity banner
    def test_pending_banner_hidden_when_nothing_pending(self, window, monkeypatch):
        import gui.main_window as mw
        fake = type("S", (), {"escalate": False, "last_ok": None,
                              "status_line": lambda self: None,
                              "banner": lambda self: None})()
        monkeypatch.setattr(mw, "_LogShipWorker", mw._LogShipWorker)
        monkeypatch.setattr("core.log_sync.pending_status", lambda *a, **k: fake)
        window._refresh_pending_activity_banner()
        assert not window._pending_banner.isVisible()

    def test_pending_banner_shows_when_pending(self, window, monkeypatch):
        fake = type("S", (), {"escalate": False, "last_ok": None,
                              "status_line": lambda self: "Activity log: 2 reports waiting to upload",
                              "banner": lambda self: None})()
        monkeypatch.setattr("core.log_sync.pending_status", lambda *a, **k: fake)
        window.show()
        window._refresh_pending_activity_banner()
        assert window._pending_banner.isVisible()
        assert "2 reports" in window._pending_label.text()

    def test_tab_change_is_safe(self, window):
        # Switching tabs triggers shipping + banner refresh + History reload.
        for i in range(window.tabs.count()):
            window.tabs.setCurrentIndex(i)  # must not raise

    def test_tutorial_steps_reference_real_widgets(self, window):
        # Every tour step's widget lambda must resolve to a live QWidget — guards
        # against typo'd attribute names after GUI changes. (on_show not invoked.)
        from PyQt6.QtWidgets import QWidget
        steps = window._build_tutorial_steps()
        assert len(steps) >= 20  # comprehensive tour across all five tabs
        for step in steps:
            w = step.get("widget")
            if w is not None:
                resolved = w()
                assert isinstance(resolved, QWidget), step.get("title")

    def test_tutorial_covers_new_features(self, window):
        titles = {s["title"] for s in window._build_tutorial_steps()}
        for expected in ("Export ASC MHL", "Deep verify (Drive)",
                         "History — org-wide activity", "Settings",
                         "Report a Problem"):
            assert expected in titles, expected

    # M11.2: Settings
    def test_settings_button_present(self, window):
        assert hasattr(window, "_settings_btn")
        # Header chips carry a leading glyph for discoverability (⚙ Settings).
        assert "Settings" in window._settings_btn.text()

    def test_settings_dialog_loads_and_saves(self, qtbot, tmp_path, monkeypatch):
        from gui.settings_dialog import SettingsDialog
        from core import settings as app_settings
        cfg = tmp_path / "config.json"
        monkeypatch.setattr(app_settings, "SETTINGS_PATH", cfg)
        dlg = SettingsDialog()
        qtbot.addWidget(dlg)
        dlg.remote_base_input.setText("gdrive:Acts")
        dlg.shipping_chk.setChecked(False)
        dlg._save()
        assert app_settings.activity_remote_base(path=cfg) == "gdrive:Acts"
        assert app_settings.log_shipping_enabled(path=cfg) is False


# ---------------------------------------------------------------------------
# M9.3: History tab
# ---------------------------------------------------------------------------

class TestHistoryTabSmoke:
    @pytest.fixture
    def tab(self, qtbot, monkeypatch):
        import gui.history_tab as ht
        records = [
            {"operation": "offload", "timestamp": "2026-06-12T10:00:00",
             "workstation": "Cart 1", "user": "dit", "project": "ProjX",
             "source": "A001", "dests": ["NAS"], "file_count": 10, "bytes": 1024,
             "verdict": "VERIFIED", "log_filename": "c.txt"},
            {"operation": "verify", "timestamp": "2026-06-11T09:00:00",
             "workstation": "Cart 2", "user": "ed", "project": "ProjY",
             "verdict": "FAIL"},
        ]
        monkeypatch.setattr(ht.activity_index, "load_org_records",
                            lambda **k: list(records))
        t = ht.HistoryTab()
        qtbot.addWidget(t)
        return t

    def test_instantiates_and_renders_rows(self, tab):
        tab.show()
        assert tab.table.rowCount() == 2

    def test_filter_options_populated(self, tab):
        ops = [tab._filter_combos["operation"].itemText(i)
               for i in range(tab._filter_combos["operation"].count())]
        assert "offload" in ops and "verify" in ops

    def test_filter_narrows_rows(self, tab):
        combo = tab._filter_combos["operation"]
        combo.setCurrentText("verify")
        assert tab.table.rowCount() == 1
        assert tab.table.item(0, 1).text() == "Cart 2"

    def test_refresh_without_remote_base_is_safe(self, tab, monkeypatch):
        import gui.history_tab as ht
        monkeypatch.setattr(ht.app_settings, "activity_remote_base", lambda **k: "")
        tab._refresh_org()  # must not raise or start a thread
        assert tab.refresh_btn.isEnabled()

    def test_default_sort_is_newest_first(self, tab):
        # Offload (Jun 12) is newer than Verify (Jun 11) → row 0.
        assert tab.table.item(0, 2).text() == "Offload"

    def test_sort_by_workstation_reorders(self, tab):
        from PyQt6.QtCore import Qt
        tab.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        assert tab.table.item(0, 1).text() == "Cart 1"  # alphabetical first

    def test_log_filename_travels_with_row_after_sort(self, tab):
        import gui.history_tab as ht
        from PyQt6.QtCore import Qt
        # Sort so visual order differs from insertion order; the custody-log
        # filename must still be reachable from the row's When item.
        tab.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        # Cart 1 (the offload) is row 0 now; its log filename is "c.txt".
        assert tab.table.item(0, 1).text() == "Cart 1"
        assert tab.table.item(0, 0).data(ht._LOG_ROLE) == "c.txt"

    def test_operation_cell_tinted_by_tab_accent(self, tab):
        from gui import theme
        # Row 0 is the offload record → Operation cell carries the Offload accent.
        item = tab.table.item(0, 2)
        assert item.text() == "Offload"
        assert item.foreground().color().name().lower() == \
            theme.tab_accent("Offload").lower()

    def test_populated_hides_empty_state(self, tab):
        # With records present the grid shows and the empty-state is hidden.
        assert tab.table.isVisibleTo(tab)
        assert not tab._empty_label.isVisibleTo(tab)

    def test_fresh_install_shows_empty_state(self, qtbot, monkeypatch):
        import gui.history_tab as ht
        monkeypatch.setattr(ht.activity_index, "load_org_records", lambda **k: [])
        t = ht.HistoryTab()
        qtbot.addWidget(t)
        assert t._empty_label.isVisibleTo(t)
        assert not t.table.isVisibleTo(t)

    def test_double_click_opens_local_log(self, tab, monkeypatch, tmp_path):
        import gui.history_tab as ht
        from PyQt6.QtGui import QDesktopServices
        log = tmp_path / "custody.txt"; log.write_text("x")
        monkeypatch.setattr(ht.activity_index, "find_local_log", lambda n: log)
        opened = []
        monkeypatch.setattr(QDesktopServices, "openUrl",
                            staticmethod(lambda url: opened.append(url) or True))
        tab._open_row_log(0, 0)  # first row has log_filename "c.txt"
        assert opened, "should open the custody log"

    def test_double_click_missing_log_sets_status(self, tab, monkeypatch):
        import gui.history_tab as ht
        monkeypatch.setattr(ht.activity_index, "find_local_log", lambda n: None)
        tab._open_row_log(0, 0)
        assert "not available" in tab.status_label.text()

    def test_staleness_label_hidden_when_fresh(self, tab, monkeypatch):
        import gui.history_tab as ht
        monkeypatch.setattr(ht.history, "staleness_warning", lambda *a, **k: None)
        tab.reload()
        assert not tab.staleness_label.isVisible()

    def test_load_demo_data_populates_when_empty(self, qtbot, monkeypatch):
        import gui.history_tab as ht
        monkeypatch.setattr(ht.activity_index, "load_org_records", lambda **k: [])
        t = ht.HistoryTab()
        qtbot.addWidget(t)
        assert t.table.rowCount() == 0
        t.load_demo_data()
        assert t.table.rowCount() >= 5  # demo rows loaded
        assert "demo" in t.status_label.text()

    def test_load_demo_data_does_not_mask_real_records(self, tab):
        # `tab` fixture already has 2 real records; demo must not overwrite them.
        before = tab.table.rowCount()
        tab.load_demo_data()
        assert tab.table.rowCount() == before

    def test_staleness_label_shows_warning(self, tab, monkeypatch):
        import gui.history_tab as ht
        monkeypatch.setattr(ht.history, "staleness_warning",
                            lambda *a, **k: "⚠ 1 machine has not reported recently: Cart 3 (last reported Jun 2)")
        tab.show()
        tab.reload()
        assert tab.staleness_label.isVisible()
        assert "Cart 3" in tab.staleness_label.text()


# ---------------------------------------------------------------------------
# M4.1: resume prompt (OffloadTab)
# ---------------------------------------------------------------------------

class TestOffloadResumePrompt:
    @pytest.fixture
    def tab(self, qtbot, monkeypatch):
        import gui.offload_tab as ot
        import core.projects as proj

        def _mock_watcher(self):
            self._watcher = MagicMock()
            self._watcher.available = False

        monkeypatch.setattr(ot.OffloadTab, "_start_volume_watcher", _mock_watcher)
        monkeypatch.setattr(proj, "list_dest_presets", lambda: [])
        t = ot.OffloadTab()
        qtbot.addWidget(t)
        return t

    def test_no_staging_returns_false_without_dialog(self, tab, tmp_path):
        from core.offload import OffloadSource, OffloadDest
        src = OffloadSource(label="A001", path=tmp_path / "card")
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        assert tab._ask_resume([src], [dst]) is False


# ---------------------------------------------------------------------------
# Drag-and-drop onto path fields (#6)
# ---------------------------------------------------------------------------

class _FakeDrop:
    """Stand-in for QDropEvent — exposes the bits the handlers use."""
    def __init__(self, mime):
        self._m = mime
        self.accepted = False

    def mimeData(self):
        return self._m

    def acceptProposedAction(self):
        self.accepted = True


def _folder_mime(path):
    from PyQt6.QtCore import QMimeData, QUrl
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(str(path))])
    return m


class TestDragAndDrop:
    def test_path_input_accepts_folder_drop(self, qtbot, tmp_path):
        from gui.path_input_widget import PathInputWidget
        d = tmp_path / "A001"
        d.mkdir()
        w = PathInputWidget("source")
        qtbot.addWidget(w)
        ev = _FakeDrop(_folder_mime(d))
        w.dropEvent(ev)
        assert w.text() == str(d)
        assert ev.accepted

    def test_drop_line_edit_file_drop_uses_parent(self, qtbot, tmp_path):
        from gui.drop_line_edit import DropLineEdit
        f = tmp_path / "clip.mov"
        f.write_text("x")
        le = DropLineEdit()
        qtbot.addWidget(le)
        ev = _FakeDrop(_folder_mime(f))
        le.dropEvent(ev)
        assert le.text() == str(tmp_path)
        assert ev.accepted


# ---------------------------------------------------------------------------
# Toast — inline confirmations (#7)
# ---------------------------------------------------------------------------

class TestToast:
    def test_show_toast_renders_message(self, qtbot):
        from PyQt6.QtWidgets import QWidget
        from gui.toast import show_toast
        parent = QWidget()
        parent.resize(600, 400)
        qtbot.addWidget(parent)
        t = show_toast(parent, "Apply complete — 3 action(s) succeeded.", "success")
        assert t.parent() is parent
        assert "Apply complete" in t.text()


# ---------------------------------------------------------------------------
# CompletionBanner (shared across Transfer / Merge / Offload / Verify)
# ---------------------------------------------------------------------------

class TestCompletionBanner:
    def test_show_result_ok_and_problem_and_dismiss(self, qtbot):
        from gui.completion_banner import CompletionBanner
        from gui import theme
        b = CompletionBanner(); qtbot.addWidget(b)
        assert b.isHidden()
        b.show_result("done", ok=True)
        assert not b.isHidden()
        assert theme.VERDICT_GREEN in b.styleSheet()
        assert b.msg.text() == "done"
        b.show_result("bad", ok=False)
        assert theme.VERDICT_CORAL in b.styleSheet()
        b.dismiss()
        assert b.isHidden()

    def test_subtitle_renders_amber_html_when_present(self, qtbot):
        from gui.completion_banner import CompletionBanner
        from PyQt6.QtCore import Qt
        b = CompletionBanner(); qtbot.addWidget(b)
        b.show_result("Transfer complete", ok=True, subtitle="3 files used rclone fallback")
        assert "3 files used rclone fallback" in b.msg.text()
        assert b.msg.textFormat() == Qt.TextFormat.RichText

    def test_no_subtitle_keeps_plain_text(self, qtbot):
        from gui.completion_banner import CompletionBanner
        from PyQt6.QtCore import Qt
        b = CompletionBanner(); qtbot.addWidget(b)
        b.show_result("All good", ok=True)
        assert b.msg.text() == "All good"
        assert b.msg.textFormat() == Qt.TextFormat.PlainText

    def test_every_job_tab_has_a_completion_banner(self, qtbot, monkeypatch):
        from gui.completion_banner import CompletionBanner
        from gui.transfer_tab import TransferTab
        from gui.verify_tab import VerifyTab
        import gui.merge_tab as mt
        import gui.offload_tab as ot
        import core.projects as proj
        monkeypatch.setattr(mt.project_registry, "list_projects", lambda: [])
        monkeypatch.setattr(proj, "list_dest_presets", lambda: [])
        monkeypatch.setattr(ot.OffloadTab, "_start_volume_watcher",
                            lambda self: setattr(self, "_watcher", MagicMock(available=False)))
        t = TransferTab(); qtbot.addWidget(t)
        v = VerifyTab(); qtbot.addWidget(v)
        m = mt.MergeTab(); qtbot.addWidget(m)
        o = ot.OffloadTab(); qtbot.addWidget(o)
        assert isinstance(t._banner, CompletionBanner)
        assert isinstance(v._banner, CompletionBanner)
        assert isinstance(m._banner, CompletionBanner)
        assert isinstance(o._completion_banner, CompletionBanner)


# ---------------------------------------------------------------------------
# SummaryDialog eject-gate — green only when cleared; gray when all_done but
# not cleared (the #10 fix).  Tests construct the dialog directly with
# synthetic CellResult fixtures so no actual offload I/O is needed.
# ---------------------------------------------------------------------------

class TestSummaryDialogEjectGate:
    """SummaryDialog.eject label color reflects clearance, not just completion."""

    def _make_results(self, src_label, dest_labels, state, verified=None):
        from core.offload import CellResult, CellState
        s = getattr(CellState, state)
        # M14.1: a verified offload pass is integrity-verified (xxh128 compare).
        integrity = verified is True
        return [CellResult(source_label=src_label, dest_label=d, state=s,
                           files_copied=1, verified=verified,
                           integrity_verified=integrity)
                for d in dest_labels]

    def test_green_when_all_done_and_cleared(self, qtbot):
        """Two DONE destinations satisfy clearance — eject label is green."""
        from gui.offload_tab import SummaryDialog
        from gui import theme
        results = self._make_results("A001", ["NAS1", "NAS2"], "DONE", verified=True)
        dlg = SummaryDialog(results, log_path="", prior_map=None)
        qtbot.addWidget(dlg)
        labels = [w for w in dlg.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)
                  if "Safe to eject" in w.text()]
        assert labels, "eject label not found"
        assert theme.VERDICT_GREEN in labels[0].styleSheet()

    def test_gray_when_all_done_but_single_dest_not_cleared(self, qtbot):
        """Single DONE destination — all_done but clearance requires ≥2 copies."""
        from gui.offload_tab import SummaryDialog
        from gui import theme
        results = self._make_results("A001", ["NAS1"], "DONE", verified=True)
        dlg = SummaryDialog(results, log_path="", prior_map=None)
        qtbot.addWidget(dlg)
        labels = [w for w in dlg.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)
                  if "Safe to eject" in w.text()]
        assert labels, "eject label not found"
        assert theme.VERDICT_MUTED in labels[0].styleSheet()
        assert theme.VERDICT_GREEN not in labels[0].styleSheet()

    def test_muted_when_a_dest_failed(self, qtbot):
        """One FAILED result means all_done is False — label is muted, not green."""
        from gui.offload_tab import SummaryDialog
        from core.offload import CellResult, CellState
        from gui import theme
        results = [
            CellResult(source_label="A001", dest_label="NAS1", state=CellState.DONE,
                       files_copied=1, verified=True),
            CellResult(source_label="A001", dest_label="NAS2", state=CellState.FAILED,
                       files_copied=0),
        ]
        dlg = SummaryDialog(results, log_path="", prior_map=None)
        qtbot.addWidget(dlg)
        QLabel = __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel
        labels = [w for w in dlg.findChildren(QLabel)
                  if "Not all destinations" in w.text()]
        assert labels, "incomplete-destinations label not found"
        assert theme.VERDICT_GREEN not in labels[0].styleSheet()


# ---------------------------------------------------------------------------
# Dr. Zhivago exam dialogs — gate for disabling bootup music. Construction-only
# smoke (a missing import here previously crashed the app at click time).
# ---------------------------------------------------------------------------

class TestZhivagoExamDialogs:
    def test_intro_and_quiz_dialogs_construct(self, qtbot):
        from gui.zhivago_quiz import ZhivagoIntroDialog, ZhivagoQuizDialog
        intro = ZhivagoIntroDialog(); qtbot.addWidget(intro)
        quiz_dlg = ZhivagoQuizDialog(); qtbot.addWidget(quiz_dlg)
        from core import zhivago_quiz as quiz
        # One stacked page per question, starting on the first.
        assert quiz_dlg._stack.count() == len(quiz.QUESTIONS)
        assert quiz_dlg._stack.currentIndex() == 0
        # Back disabled on Q1; Next labelled for paging, not submitting.
        assert not quiz_dlg._back_btn.isEnabled()
        assert quiz_dlg._next_btn.text() == "Next"

    def test_paging_to_last_question_switches_to_submit(self, qtbot):
        from gui.zhivago_quiz import ZhivagoQuizDialog
        from core import zhivago_quiz as quiz
        dlg = ZhivagoQuizDialog(); qtbot.addWidget(dlg)
        for _ in range(len(quiz.QUESTIONS) - 1):
            dlg._go_next()
        assert dlg._stack.currentIndex() == len(quiz.QUESTIONS) - 1
        assert dlg._next_btn.text() == "Submit exam"
        assert dlg._back_btn.isEnabled()
