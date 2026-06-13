"""Generate the Quick Start screenshots (M7.4).

Renders the real MainWindow with the production theme under the offscreen Qt
platform, drives each tab into a representative state using the built-in demo
data, and saves a PNG per flow into docs/screenshots/.

Run on the Mac (needs PyQt6):

    QT_QPA_PLATFORM=offscreen /opt/homebrew/bin/python3.11 docs/capture_screenshots.py

No display, no screen-recording permission and no real card/rclone needed —
the demo folders are zero-byte stubs created under Application Support.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
OUT = REPO / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor

import gui.main_window as mw
import gui.offload_tab as ot
import gui.merge_tab as mt
import core.projects as proj
from gui import theme
from core import demo


def _qcolor(hex_str):
    h = hex_str.lstrip("#")
    return QColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _build_app():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, _qcolor(theme.CHARCOAL))
    pal.setColor(QPalette.ColorRole.WindowText, _qcolor(theme.CREAM))
    pal.setColor(QPalette.ColorRole.Base, _qcolor(theme.CHARCOAL))
    pal.setColor(QPalette.ColorRole.AlternateBase, _qcolor(theme.CHARCOAL_LIGHT))
    pal.setColor(QPalette.ColorRole.Text, _qcolor(theme.CREAM))
    pal.setColor(QPalette.ColorRole.Button, _qcolor(theme.CHARCOAL_LIGHT))
    pal.setColor(QPalette.ColorRole.ButtonText, _qcolor(theme.CREAM))
    pal.setColor(QPalette.ColorRole.Highlight, _qcolor(theme.GOLD))
    pal.setColor(QPalette.ColorRole.HighlightedText, _qcolor(theme.CHARCOAL))
    app.setPalette(pal)
    app.setStyleSheet(theme.app_stylesheet())
    return app


def _suppress_workers(monkey_targets):
    """Stop MainWindow's startup workers/network/wizard from firing in capture."""
    mw.should_show_wizard = lambda: False
    mw.check_rclone_auth = lambda *a, **k: MagicMock(status=mw.CheckStatus.OK, message="")
    mw.get_active_remote = lambda: "gdrive"
    mw.get_remote_account_email = lambda r: ""
    def _mock_watcher(self):
        self._watcher = MagicMock()
        self._watcher.available = False
        self._watcher.scan_existing = lambda: []
    ot.OffloadTab._start_volume_watcher = _mock_watcher
    mt.project_registry.list_projects = lambda: []
    proj.list_dest_presets = lambda: []
    mw._StartupCheckWorker.start = lambda self: None
    mw.MainWindow._start_update_check = lambda self: None
    mw.update_check.check_for_update = lambda *a, **k: None


def _save(app, window, name):
    app.processEvents()
    app.processEvents()
    path = OUT / name
    window.grab().save(str(path))
    print(f"  wrote {path.relative_to(REPO)}")


def main():
    app = _build_app()
    _suppress_workers(None)

    # Make sure the demo stubs + manifests exist so the tabs have real content.
    demo.ensure_demo_folder()
    demo.ensure_demo_merge_folders()

    window = mw.MainWindow()
    window.resize(1200, 820)
    window.show()
    app.processEvents()

    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    idx = {t: i for i, t in enumerate(titles)}

    # 0. Whole-window overview on the Transfer tab (header + tabs + version).
    window.tabs.setCurrentIndex(idx["Transfer"])
    _save(app, window, "00_overview.png")

    # 1. Transfer tab — source + destination prefilled with the demo folders.
    try:
        t = window._transfer_tab
        t.src_input.setText(str(demo.demo_source()))
        t.dst_input.setText(str(demo.demo_destination()))
    except Exception as e:
        print(f"  (transfer prep skipped: {e})")
    _save(app, window, "01_transfer.png")

    # 2. Offload tab.
    window.tabs.setCurrentIndex(idx["Offload"])
    try:
        otab = window._offload_tab
        # Give the source row a friendly label so the shot reads clearly.
        if hasattr(otab, "_label"):
            otab._label.setText("A001 — Day 1")
    except Exception as e:
        print(f"  (offload prep skipped: {e})")
    _save(app, window, "02_offload.png")

    # 3. Merge tab — demo divergence loaded so the diff table + summary show.
    window.tabs.setCurrentIndex(idx["Merge"])
    try:
        m = window._merge_tab
        m.load_demo_data()
        if hasattr(m, "_update_summary"):
            m._update_summary()
    except Exception as e:
        print(f"  (merge prep skipped: {e})")
    _save(app, window, "03_merge.png")

    # 4. Verify tab — point at the demo verify_sample folder.
    window.tabs.setCurrentIndex(idx["Verify"])
    try:
        v = window._verify_tab
        v.folder_input.setText(str(demo.demo_verify_sample()))
    except Exception as e:
        print(f"  (verify prep skipped: {e})")
    _save(app, window, "04_verify.png")

    print("Done.")


if __name__ == "__main__":
    main()
