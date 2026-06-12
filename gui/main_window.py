from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QStatusBar, QLabel, QPushButton
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QTimer

from PyQt6.QtCore import QThread, pyqtSignal

from gui.transfer_tab     import TransferTab
from gui.merge_tab        import MergeTab
from gui.verify_tab       import VerifyTab
from gui.offload_tab      import OffloadTab
from gui.setup_wizard     import SetupWizard, should_show_wizard
from gui.tutorial_overlay import TutorialOverlay, tutorial_already_seen, reset_tutorial
from core.demo import (
    ensure_demo_folder, demo_verify_sample, demo_verify_manifest,
    ensure_demo_merge_folders,
)
from gui                  import theme
from core.setup_checks    import check_rclone_auth, CheckStatus, create_gdrive_remote, run_all_checks
from core.oauth_config    import get_active_remote, get_remote_account_email


class _ReconnectWorker(QThread):
    finished = pyqtSignal(object)  # CheckResult

    def __init__(self, remote: str):
        super().__init__()
        self.remote = remote

    def run(self):
        result = create_gdrive_remote(self.remote)
        self.finished.emit(result)


class _AccountLabelWorker(QThread):
    result_ready = pyqtSignal(str, str)  # (remote, email_or_empty)

    def __init__(self, remote: str):
        super().__init__()
        self.remote = remote

    def run(self):
        email = get_remote_account_email(self.remote) or ""
        self.result_ready.emit(self.remote, email)


class _StartupCheckWorker(QThread):
    """Runs run_all_checks() off the main thread so startup doesn't block."""
    finished = pyqtSignal(list)  # List[CheckResult]

    def __init__(self, remote: str):
        super().__init__()
        self.remote = remote

    def run(self):
        self.finished.emit(run_all_checks(self.remote))


class _AuthHealthWorker(QThread):
    """Runs check_rclone_auth() off the main thread for the periodic health check."""
    finished = pyqtSignal(object)  # CheckResult

    def __init__(self, remote: str):
        super().__init__()
        self.remote = remote

    def run(self):
        self.finished.emit(check_rclone_auth(self.remote, timeout=10))


class MainWindow(QMainWindow):
    def __init__(self, force_setup: bool = False):
        super().__init__()
        self.setWindowTitle("ST SyncTool -- Signal Theory")
        self.setMinimumSize(1100, 780)
        self._force_setup = force_setup
        self._build_ui()

        # Run startup checks off the main thread so the window paints immediately.
        self._startup_worker = _StartupCheckWorker(get_active_remote())
        self._startup_worker.finished.connect(self._on_startup_checks_done)
        QTimer.singleShot(100, self._startup_worker.start)

        # Periodic auth health check (every 10 minutes).
        self._auth_timer = QTimer(self)
        self._auth_timer.timeout.connect(self._check_auth_health)
        self._auth_timer.start(10 * 60 * 1000)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(0)

        # Header
        header = QHBoxLayout()
        title = QLabel("ST SyncTool")
        title.setFont(QFont("SF Pro Display", 18, QFont.Weight.Bold))
        title.setStyleSheet("color:#ffffff;letter-spacing:1px;")
        subtitle = QLabel("Signal Theory Productions")
        subtitle.setStyleSheet("color:#555;font-size:12px;margin-left:10px;")
        version = QLabel("v1.0.0")
        version.setStyleSheet("color:#444;font-size:11px;")
        self._account_label = QLabel()
        self._account_label.setStyleSheet(
            f"color:{theme.ACCENT_GREEN};font-size:11px;margin-right:8px;"
        )

        # "Take the Tour" button — always visible so users can re-run it
        self._tour_btn = QPushButton("? Tour")
        self._tour_btn.setFixedHeight(24)
        self._tour_btn.setStyleSheet("""
            QPushButton {
                background:#2a2a2a; color:#888;
                border:1px solid #3a3a3a; border-radius:4px;
                font-size:11px; padding:0 8px;
            }
            QPushButton:hover { background:#3a3a3a; color:#ccc; }
        """)
        self._tour_btn.setToolTip("Replay the onboarding tour")
        self._tour_btn.clicked.connect(self._launch_tutorial)

        header.addWidget(title)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(self._account_label)
        header.addWidget(self._tour_btn)
        header.addSpacing(6)
        header.addWidget(version)
        root.addLayout(header)
        root.addSpacing(8)

        # Auth-health banner (hidden by default; shown when Drive auth fails)
        self._auth_banner = self._build_auth_banner()
        root.addWidget(self._auth_banner)

        # Tutorial overlay (parented to central widget so it covers the tabs)
        # Created here so tab references are valid; started later.
        self._tutorial = None  # built lazily after tabs exist

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                background: #1e1e1e;
            }
            QTabBar::tab {
                background: #2a2a2a;
                color: #888;
                padding: 8px 22px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 13px;
            }
            QTabBar::tab:selected {
                background: #007acc;
                color: white;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected {
                background: #3a3a3a;
                color: #ccc;
            }
        """)

        self._transfer_tab = TransferTab(self)
        self._merge_tab    = MergeTab(self)
        self._offload_tab  = OffloadTab(self)
        self._verify_tab   = VerifyTab(self)

        self.tabs.addTab(self._transfer_tab, "Transfer")
        self.tabs.addTab(self._merge_tab,    "Merge")
        self.tabs.addTab(self._offload_tab,  "Offload")
        self.tabs.addTab(self._verify_tab,   "Verify")
        root.addWidget(self.tabs)

        # Status bar
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(
            "color:#555;font-size:11px;background:#1a1a1a;"
        )
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    # ------------------------------------------------------------------
    # Setup wizard + auth health
    # ------------------------------------------------------------------

    def _build_auth_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("authBanner")
        banner.setStyleSheet(f"""
            QWidget#authBanner {{
                background-color: {theme.CHARCOAL_LIGHT};
                border-bottom: 2px solid {theme.ACCENT_CORAL};
            }}
            QWidget#authBanner QLabel {{
                color: {theme.CREAM};
                background: transparent;
            }}
        """)
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(12, 8, 12, 8)

        self._banner_label = QLabel()
        self._banner_label.setWordWrap(True)
        layout.addWidget(self._banner_label, stretch=1)

        self._reconnect_btn = QPushButton("Reconnect Drive")
        self._reconnect_btn.setStyleSheet(theme.primary_button_style())
        self._reconnect_btn.clicked.connect(self._reconnect_drive)
        layout.addWidget(self._reconnect_btn)

        setup_btn = QPushButton("Open Setup")
        setup_btn.setStyleSheet(theme.primary_button_style())
        setup_btn.clicked.connect(self._launch_wizard)
        layout.addWidget(setup_btn)

        banner.hide()
        return banner

    def _on_startup_checks_done(self, results):
        """Called on the main thread when the off-thread startup checks finish."""
        needs_wizard = self._force_setup or any(
            r.status in (CheckStatus.MISSING, CheckStatus.ERROR) for r in results
        )
        if needs_wizard:
            self._launch_wizard()
        else:
            # Reuse the auth result already in `results` — no second network call.
            auth_result = next(
                (r for r in results if "authentication" in r.name), None
            )
            if auth_result is not None:
                self._apply_auth_result(auth_result)
            else:
                self._check_auth_health()
        self._maybe_launch_tutorial()

    def _launch_wizard(self):
        wiz = SetupWizard(self)
        wiz.exec()
        chosen = wiz.chosen_remote
        if chosen:
            import utils.gdrive_utils
            utils.gdrive_utils.RCLONE_REMOTE = chosen
        self._check_auth_health()

    def _check_auth_health(self):
        """Spawn an off-thread worker to check Drive auth without blocking the UI."""
        remote = get_active_remote()
        existing = getattr(self, "_auth_health_worker", None)
        if existing and existing.isRunning():
            return
        self._auth_health_worker = _AuthHealthWorker(remote)
        self._auth_health_worker.finished.connect(self._apply_auth_result)
        self._auth_health_worker.start()

    def _apply_auth_result(self, result):
        """Update the auth banner from a CheckResult (called on the main thread)."""
        if result.status == CheckStatus.OK:
            self._auth_banner.hide()
            self.status_bar.showMessage("Ready")
            self._update_account_label(get_active_remote())
        else:
            self._account_label.setText("")
            _empty = "empty token" in result.message.lower()
            if _empty:
                _msg = (
                    "Google Drive token is empty or incomplete. "
                    "Click <b>Reconnect Drive</b> to re-authenticate."
                )
            else:
                _msg = (
                    "Google Drive connection issue. "
                    "Click <b>Reconnect Drive</b> to re-authenticate, "
                    "or <b>Open Setup</b> for full options."
                )
            self._banner_label.setText(
                f'<span style="color: {theme.ACCENT_CORAL}; font-weight: bold;">[!]</span>'
                f'&nbsp;&nbsp;{_msg}'
            )
            self._auth_banner.show()
            self.status_bar.showMessage("Drive auth issue — see banner")

    def _reconnect_drive(self):
        remote = get_active_remote()
        self._reconnect_btn.setEnabled(False)
        self._banner_label.setText(
            "Opening browser for Google Drive authentication "
            "— sign in with your Signal Theory account, then grant access."
        )
        self._reconnect_worker = _ReconnectWorker(remote)
        self._reconnect_worker.finished.connect(self._on_reconnect_done)
        self._reconnect_worker.start()

    def _on_reconnect_done(self, result):
        self._reconnect_btn.setEnabled(True)
        self._check_auth_health()

    def _update_account_label(self, remote: str):
        self._account_label.setText(remote)
        existing = getattr(self, "_account_worker", None)
        if existing and existing.isRunning():
            existing.result_ready.disconnect()
            existing.quit()
            existing.wait(1000)
        self._account_worker = _AccountLabelWorker(remote)
        self._account_worker.result_ready.connect(self._on_account_label_ready)
        self._account_worker.start()

    def _on_account_label_ready(self, remote: str, email: str):
        if email:
            self._account_label.setText(f"{email} ({remote})")
        else:
            self._account_label.setText(remote)

    # ------------------------------------------------------------------
    # Tutorial
    # ------------------------------------------------------------------

    def _build_tutorial_steps(self) -> list:
        """
        Returns the ordered list of tour steps.

        Each step is a dict accepted by TutorialOverlay:
            tab     – QTabWidget index to activate (or None)
            widget  – zero-arg callable returning the target QWidget (or None)
            title   – card heading
            body    – card description
            padding – optional extra px around spotlight (default 10)

        To add, remove, or reorder steps, edit this list only — the overlay
        logic is completely generic.
        """
        tt = self._transfer_tab
        mt = self._merge_tab
        ot = self._offload_tab
        vt = self._verify_tab

        return [
            # ── Welcome ──────────────────────────────────────────────────
            {
                "tab":    0,
                "widget": None,
                "title":  "Welcome to ST SyncTool",
                "body":   (
                    "This quick tour walks you through the four main tabs: "
                    "Transfer, Merge, Offload, and Verify. "
                    "You can skip at any time and replay it via the '? Tour' button."
                ),
            },

            # ── Transfer tab ──────────────────────────────────────────────
            {
                "tab":    0,
                "widget": lambda: self.tabs.tabBar(),
                "title":  "Four-tab workflow",
                "body":   (
                    "Work flows left to right: Transfer files in, Merge "
                    "conflicts, Offload to drives, then Verify integrity. "
                    "Each tab is independent — use only the ones you need."
                ),
                "padding": 4,
            },
            {
                "tab":     0,
                "widget":  lambda: tt.src_input,
                "title":   "Source folder",
                "body":    (
                    "Type or browse to the folder you want to copy from. "
                    "We've loaded a demo camera card so you can try it right now — "
                    "hit Start Transfer when you're ready."
                ),
                "on_show": lambda: tt._load_demo_folder(),
            },
            {
                "tab":    0,
                "widget": lambda: tt.dst_input,
                "title":  "Destination folder",
                "body":   (
                    "Where the files land. Can be a local path or a "
                    "Google Drive URL. The preflight summary above updates "
                    "live to show folder sizes and estimated transfer time."
                ),
            },
            {
                "tab":    0,
                "widget": lambda: tt.conflict_combo,
                "title":  "Conflict handling",
                "body":   (
                    "Controls what happens when a file already exists at "
                    "the destination: Skip (leave it), Overwrite (replace it), "
                    "or Rename Copy (keep both). Overwrite is the default."
                ),
            },
            {
                "tab":    0,
                "widget": lambda: tt.start_btn,
                "title":  "Start Transfer",
                "body":   (
                    "Kicks off the copy. Progress and per-file status appear "
                    "in the log below. A manifest file is written alongside "
                    "the destination so you can verify later."
                ),
            },

            # ── Merge tab ────────────────────────────────────────────────
            {
                "tab":    1,
                "widget": lambda: mt.project_combo,
                "title":  "Select a project",
                "body":   (
                    "Projects group a local folder with its server counterpart. "
                    "Pick one here to auto-fill the paths below, or enter them manually."
                ),
            },
            {
                "tab":     1,
                "widget":  lambda: mt.scan_btn,
                "title":   "Scan & Compare",
                "body":    (
                    "We've pre-loaded a demo project with real diverged files — "
                    "7 differences across 5 states. Click Scan & Compare any time "
                    "to run a live comparison against these files."
                ),
                "on_show": lambda: mt.load_demo_data(),
            },
            {
                "tab":     1,
                "widget":  lambda: mt.diff_table,
                "title":   "Diff table",
                "body":    (
                    "Each row is a file with a status: green = only you changed it, "
                    "blue = only server changed it, coral = both changed (you decide). "
                    "Toggle the checkbox to include or exclude each action."
                ),
                "on_show": lambda: mt.load_demo_data(),
            },
            {
                "tab":    1,
                "widget": lambda: mt.newer_wins_btn,
                "title":  "Newer Wins",
                "body":   (
                    "One click resolves every conflict row automatically: "
                    "Push if your file is newer, Pull if the server's is newer, "
                    "or Skip if timestamps are equal. Great for batch resolving "
                    "after a shoot day."
                ),
            },
            {
                "tab":    1,
                "widget": lambda: mt.apply_btn,
                "title":  "Apply Selected Actions",
                "body":   (
                    "Executes the checked actions — copies, overwrites, or skips — "
                    "then writes a new manifest so the next merge starts clean."
                ),
            },

            # ── Offload tab ───────────────────────────────────────────────
            {
                "tab":     2,
                "widget":  lambda: ot._preset_combo,
                "title":   "Offload presets",
                "body":    (
                    "Save a named configuration of multiple sources and destinations "
                    "so a shoot day's offload is one click. Presets persist between sessions."
                ),
                "on_show": lambda: ot.load_demo_data(),
            },
            {
                "tab":    2,
                "widget": lambda: ot._start_btn,
                "title":  "Start Offload",
                "body":   (
                    "Copies all sources to all destinations simultaneously with "
                    "paranoid checksum verification. Designed for end-of-day "
                    "camera card offloads to two drives at once."
                ),
            },

            # ── Verify tab ────────────────────────────────────────────────
            {
                "tab":     3,
                "widget":  lambda: vt.folder_input,
                "title":   "Folder to verify",
                "body":    (
                    "Point this at any folder transferred by ST SyncTool. "
                    "We've loaded a demo sample so you can run it right now — "
                    "it will compare the files against the pre-built manifest."
                ),
                "on_show": lambda: (
                    vt.folder_input.setText(str(ensure_demo_folder()[0].parent / "verify_sample")),
                    vt.manifest_input.setText(str(demo_verify_manifest())),
                ),
            },
            {
                "tab":    3,
                "widget": lambda: vt.verify_btn,
                "title":  "Run Verification",
                "body":   (
                    "Checksums every file and reports OK, MISSING, or MISMATCH. "
                    "Run this after any transfer or offload to confirm nothing "
                    "was corrupted in transit."
                ),
            },

            # ── Auth / header ─────────────────────────────────────────────
            {
                "tab":    None,
                "widget": lambda: self._account_label,
                "title":  "Google Drive connection",
                "body":   (
                    "Your connected Drive account appears here. If it's missing "
                    "or a banner appears at the top, click 'Open Setup' to "
                    "re-authenticate. Auth refreshes automatically every 10 minutes."
                ),
            },

            # ── Done ──────────────────────────────────────────────────────
            {
                "tab":    0,
                "widget": None,
                "title":  "You're all set!",
                "body":   (
                    "That's the full tour. Replay it anytime with the '? Tour' "
                    "button in the top-right corner. Happy creating."
                ),
            },
        ]

    def _maybe_launch_tutorial(self):
        """Auto-launch the tour for first-time users."""
        if not tutorial_already_seen():
            # Small delay so the window is fully painted before the overlay appears
            QTimer.singleShot(400, self._launch_tutorial)

    def _launch_tutorial(self):
        """Build (or rebuild) and start the tutorial overlay."""
        reset_tutorial()  # reset so _finish() will mark it seen fresh
        if self._tutorial is None:
            self._tutorial = TutorialOverlay(self)
        self._tutorial.resize(self.centralWidget().size())
        self._tutorial.set_steps(self._build_tutorial_steps())
        self._tutorial.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._tutorial and self._tutorial.isVisible():
            self._tutorial.resize(self.centralWidget().size())
