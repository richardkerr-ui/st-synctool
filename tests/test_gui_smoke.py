"""
GUI smoke tests — each tab and MainWindow can instantiate without crashing,
key widgets are present, and critical initial states are correct.

Uses pytest-qt's qtbot fixture for widget lifecycle management.
No workers are started; external I/O is mocked at the module boundary.
"""

import sys
import pytest
from unittest.mock import MagicMock


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
        fake = type("S", (), {"escalate": False, "status_line": lambda self: None,
                              "banner": lambda self: None})()
        monkeypatch.setattr(mw, "_LogShipWorker", mw._LogShipWorker)
        monkeypatch.setattr("core.log_sync.pending_status", lambda *a, **k: fake)
        window._refresh_pending_activity_banner()
        assert not window._pending_banner.isVisible()

    def test_pending_banner_shows_when_pending(self, window, monkeypatch):
        fake = type("S", (), {"escalate": False,
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

    # M11.2: Settings
    def test_settings_button_present(self, window):
        assert hasattr(window, "_settings_btn")
        assert window._settings_btn.text() == "Settings"

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
