from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QStatusBar, QLabel, QPushButton
)
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from PyQt6.QtCore import QTimer, Qt, QSettings

from PyQt6.QtCore import QThread, pyqtSignal

from gui.transfer_tab     import TransferTab
from gui.merge_tab        import MergeTab
from gui.verify_tab       import VerifyTab
from gui.offload_tab      import OffloadTab
from gui.history_tab       import HistoryTab
from gui.setup_wizard     import SetupWizard, should_show_wizard
from gui.tutorial_overlay import TutorialOverlay, tutorial_already_seen, reset_tutorial
from core.demo import (
    ensure_demo_folder, demo_verify_sample, demo_verify_manifest,
    ensure_demo_merge_folders,
)
from gui                  import theme
from core.setup_checks    import check_rclone_auth, CheckStatus, create_gdrive_remote, run_all_checks
from core.oauth_config    import get_active_remote, get_remote_account_email
from core.version         import __version__ as APP_VERSION
from core import update_check


class _UpdateCheckWorker(QThread):
    """M7.5: queries GitHub for a newer release off the main thread.

    Thin adapter — all logic is in core.update_check. Emits UpdateInfo or None;
    never raises (the core check is silent on every failure)."""
    finished = pyqtSignal(object)  # UpdateInfo | None

    def run(self):
        self.finished.emit(update_check.check_for_update())


class _LogShipWorker(QThread):
    """M9.1: ships the activity log to the shared Drive folder off the main thread.

    Thin adapter — all logic is in core.log_sync.ship_if_configured, which is a
    no-op unless a remote base is set in Settings and never raises."""
    finished = pyqtSignal(object)  # ShipResult | None

    def run(self):
        from core.log_sync import ship_if_configured
        self.finished.emit(ship_if_configured())


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
        self._restore_window_state()

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
        # ⌘, opens Settings, the macOS-standard preferences shortcut.
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self._open_settings)
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
        version = QLabel(f"v{APP_VERSION}")
        version.setStyleSheet("color:#444;font-size:11px;")
        self._account_label = QLabel()
        self._account_label.setStyleSheet(
            f"color:{theme.ACCENT_GREEN};font-size:11px;margin-right:8px;"
        )

        # Header action chips — shared style (theme.header_chip_style), brighter
        # than before with a leading glyph so they read as discoverable controls.
        chip_qss = theme.header_chip_style()

        # "Take the Tour" button — always visible so users can re-run it
        self._tour_btn = QPushButton("?  Tour")
        self._tour_btn.setFixedHeight(26)
        self._tour_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tour_btn.setStyleSheet(chip_qss)
        self._tour_btn.setToolTip("Replay the onboarding tour")
        self._tour_btn.clicked.connect(self._launch_tutorial)

        # M7.3: "Report a Problem" — zips recent logs + version/OS info to email.
        self._feedback_btn = QPushButton("✎  Report a Problem")
        self._feedback_btn.setFixedHeight(26)
        self._feedback_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._feedback_btn.setStyleSheet(chip_qss)
        self._feedback_btn.setToolTip("Bundle recent logs and app info into a zip to email")
        self._feedback_btn.clicked.connect(self._report_problem)

        # M11.2: Settings (org activity remote base + log-shipping toggle).
        self._settings_btn = QPushButton("⚙  Settings")
        self._settings_btn.setFixedHeight(26)
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.setStyleSheet(chip_qss)
        self._settings_btn.setToolTip("Configure the org-wide activity log")
        self._settings_btn.clicked.connect(self._open_settings)

        # Bootup-music toggle ("Sim Nights"). Turning it off is gated behind the
        # Dr. Zhivago exam; turning it back on is free. The glyph reflects state.
        self._music_btn = QPushButton()
        self._music_btn.setFixedHeight(26)
        self._music_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._music_btn.setStyleSheet(chip_qss)
        self._music_btn.clicked.connect(self._toggle_bootup_music)
        self._refresh_music_btn()

        header.addWidget(title)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(self._account_label)
        header.addWidget(self._music_btn)
        header.addWidget(self._settings_btn)
        header.addWidget(self._feedback_btn)
        header.addWidget(self._tour_btn)
        header.addSpacing(6)
        header.addWidget(version)
        root.addLayout(header)
        root.addSpacing(8)

        # Auth-health banner (hidden by default; shown when Drive auth fails)
        self._auth_banner = self._build_auth_banner()
        root.addWidget(self._auth_banner)

        # M5.3: scheduled-verify failure banner (hidden unless the last monthly
        # run recorded unacknowledged failures).
        self._sched_banner = self._build_scheduled_verify_banner()
        root.addWidget(self._sched_banner)
        self._refresh_scheduled_verify_banner()

        # M9.1: activity-log pending banner (hidden unless logs are waiting to ship)
        self._pending_banner = self._build_pending_activity_banner()
        root.addWidget(self._pending_banner)
        self._refresh_pending_activity_banner()

        # M7.5: update-available banner (hidden until a newer release is found).
        # Defer the check onto the event loop so it only runs in the live app,
        # never during bare construction in tests (avoids a QThread outliving
        # the window at teardown).
        self._update_banner = self._build_update_banner()
        root.addWidget(self._update_banner)
        QTimer.singleShot(0, self._start_update_check)
        # M9.1: ship any pending activity logs on launch (no-op unless configured).
        QTimer.singleShot(0, self._start_log_shipping)

        # Tutorial overlay (parented to central widget so it covers the tabs)
        # Created here so tab references are valid; started later.
        self._tutorial = None  # built lazily after tabs exist

        # Tabs — per-tab accent: the selected tab is underlined in its accent
        # colour (set initially to Transfer's, updated on every tab change).
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(theme.tabbar_stylesheet(theme.DEFAULT_ACCENT))

        self._transfer_tab = TransferTab(self)
        self._merge_tab    = MergeTab(self)
        self._offload_tab  = OffloadTab(self)
        self._verify_tab   = VerifyTab(self)
        self._history_tab  = HistoryTab(self)

        self.tabs.addTab(self._transfer_tab, "Transfer")
        self.tabs.addTab(self._merge_tab,    "Merge")
        self.tabs.addTab(self._offload_tab,  "Offload")
        self.tabs.addTab(self._verify_tab,   "Verify")
        self.tabs.addTab(self._history_tab,  "History")
        self.tabs.currentChanged.connect(self._on_tab_changed)
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

    def _build_scheduled_verify_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("schedBanner")
        banner.setStyleSheet(f"""
            QWidget#schedBanner {{
                background-color: {theme.CHARCOAL_LIGHT};
                border-bottom: 2px solid {theme.ACCENT_CORAL};
            }}
            QWidget#schedBanner QLabel {{
                color: {theme.CREAM};
                background: transparent;
            }}
        """)
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(12, 8, 12, 8)
        self._sched_banner_label = QLabel()
        self._sched_banner_label.setWordWrap(True)
        layout.addWidget(self._sched_banner_label, stretch=1)
        view_btn = QPushButton("Open Verify")
        view_btn.setStyleSheet(theme.primary_button_style())
        view_btn.clicked.connect(lambda: self.tabs.setCurrentWidget(self._verify_tab))
        layout.addWidget(view_btn)
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setStyleSheet(theme.primary_button_style())
        dismiss_btn.clicked.connect(self._dismiss_scheduled_verify_banner)
        layout.addWidget(dismiss_btn)
        banner.setVisible(False)
        return banner

    def _refresh_scheduled_verify_banner(self):
        from core import scheduled_verify
        state = scheduled_verify.read_pending_failures()
        if state:
            self._sched_banner_label.setText(scheduled_verify.format_failure_banner(state))
            self._sched_banner.setVisible(True)
        else:
            self._sched_banner.setVisible(False)

    def _dismiss_scheduled_verify_banner(self):
        from core import scheduled_verify
        scheduled_verify.acknowledge_failures()
        self._sched_banner.setVisible(False)

    def _build_pending_activity_banner(self) -> QWidget:
        """M9.1: passive status line for activity logs waiting to ship, escalating
        to a stronger banner once something has been pending 7+ days."""
        banner = QWidget()
        banner.setObjectName("pendingBanner")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(12, 6, 12, 6)
        self._pending_label = QLabel()
        self._pending_label.setWordWrap(True)
        layout.addWidget(self._pending_label, stretch=1)
        banner.setVisible(False)
        return banner

    def _refresh_pending_activity_banner(self):
        from core import log_sync
        try:
            status = log_sync.pending_status()
        except Exception:
            self._pending_banner.setVisible(False)
            return
        text = status.banner() if status.escalate else status.status_line()
        if not text:
            self._pending_banner.setVisible(False)
            return
        if status.escalate:
            border = theme.ACCENT_CORAL
        elif status.last_ok is False:
            border = theme.ACCENT_GOLD
        else:
            border = theme.CHARCOAL_LIGHT
        self._pending_banner.setStyleSheet(f"""
            QWidget#pendingBanner {{
                background-color: {theme.CHARCOAL_LIGHT};
                border-bottom: 2px solid {border};
            }}
            QWidget#pendingBanner QLabel {{ color: {theme.CREAM}; background: transparent; }}
        """)
        self._pending_label.setText(text)
        self._pending_banner.setVisible(True)

    def _build_update_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("updateBanner")
        banner.setStyleSheet(f"""
            QWidget#updateBanner {{
                background-color: {theme.CHARCOAL_LIGHT};
                border-bottom: 2px solid {theme.GOLD};
            }}
            QWidget#updateBanner QLabel {{
                color: {theme.CREAM};
                background: transparent;
            }}
        """)
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(12, 8, 12, 8)
        self._update_banner_label = QLabel()
        self._update_banner_label.setWordWrap(True)
        layout.addWidget(self._update_banner_label, stretch=1)
        download_btn = QPushButton("Download")
        download_btn.setStyleSheet(theme.primary_button_style())
        download_btn.clicked.connect(self._open_update_url)
        layout.addWidget(download_btn)
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setStyleSheet(theme.primary_button_style())
        dismiss_btn.clicked.connect(lambda: self._update_banner.setVisible(False))
        layout.addWidget(dismiss_btn)
        banner.setVisible(False)
        self._update_url = ""
        return banner

    def _start_update_check(self):
        # Best-effort, off the main thread; silent on any failure or offline.
        self._update_thread = QThread()
        self._update_worker = _UpdateCheckWorker()
        self._update_worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.finished.connect(self._on_update_check_done)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_thread.start()

    def _start_log_shipping(self):
        # Best-effort, off the main thread; silent no-op unless org activity is
        # configured in Settings. Guarded so overlapping triggers (launch +
        # tab navigation) never spawn a second concurrent ship.
        existing = getattr(self, "_ship_thread", None)
        if existing is not None and existing.isRunning():
            return
        self._ship_thread = QThread()
        self._ship_worker = _LogShipWorker()
        self._ship_worker.moveToThread(self._ship_thread)
        self._ship_thread.started.connect(self._ship_worker.run)
        self._ship_worker.finished.connect(self._ship_thread.quit)
        self._ship_worker.finished.connect(self._on_ship_done)
        self._ship_thread.start()

    def _on_ship_done(self, _result):
        # Refresh the pending-activity banner once a ship pass completes.
        self._refresh_pending_activity_banner()

    def _on_tab_changed(self, index):
        # Recolour the tab-bar underline to the active tab's accent.
        self.tabs.setStyleSheet(theme.tabbar_stylesheet(
            theme.tab_accent(self.tabs.tabText(index))))
        # M9.1: after the user finishes an op and navigates, drain pending logs
        # and refresh the banner (no background polling — fires only on nav).
        self._start_log_shipping()
        self._refresh_pending_activity_banner()
        widget = self.tabs.currentWidget()
        if widget is self._history_tab:
            self._history_tab.reload()

    def _on_update_check_done(self, info):
        if info is None:
            return
        self._update_url = info.url
        self._update_banner_label.setText(update_check.update_banner_text(info))
        self._update_banner.setVisible(True)

    def _open_update_url(self):
        if self._update_url:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(self._update_url))

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
                    "ST SyncTool keeps Signal Theory's work in sync between "
                    "Google Drive and the Synology NAS. Merge reconciles jobs you "
                    "took home on WFH days, Transfer pulls agency assets off Drive "
                    "(auto-recombining multipart zips), History shows who touched "
                    "what across the team, and Verify surfaces the integrity checks "
                    "Drive and the NAS hide. It also offloads camera cards. This "
                    "quick tour shows each — skip any time, replay via '? Tour'."
                ),
            },

            # ── Transfer tab ──────────────────────────────────────────────
            {
                "tab":    0,
                "widget": lambda: self.tabs.tabBar(),
                "title":  "The workflow",
                "body":   (
                    "Work flows left to right: Transfer files in, Merge "
                    "conflicts, Offload to drives, Verify integrity — and "
                    "History shows every job across all machines. Each tab is "
                    "independent; use only the ones you need."
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
                "widget": lambda: tt.export_mhl_chk,
                "title":  "Export ASC MHL",
                "body":   (
                    "Tick this to also write an industry-standard ASC MHL (.mhl) "
                    "sidecar next to the manifest, so post houses can verify your "
                    "delivery in Silverstack, YoYotta and similar — without "
                    "trusting our app."
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
                "widget": lambda: ot._thumb_check,
                "title":  "Contact sheets & MHL",
                "body":   (
                    "Optionally generate thumbnail contact sheets per card, and "
                    "tick 'Export ASC MHL' to write a .mhl alongside each manifest "
                    "for post-house verification."
                ),
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
            {
                "tab":    2,
                "widget": lambda: ot._start_btn,
                "title":  "\"Safe to format\" clearance",
                "body":   (
                    "After an offload, each source shows a verdict. You only get a "
                    "green \"safe to format\" once at least TWO destinations verified "
                    "clean — otherwise it's amber with the reason. Never wipe a card "
                    "on a single copy."
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
            {
                "tab":    3,
                "widget": lambda: vt.deep_chk,
                "title":  "Deep verify (Drive)",
                "body":   (
                    "For a Google Drive folder, tick this to stream every file "
                    "back and re-hash it, instead of trusting Drive's metadata. "
                    "It uses bandwidth, so an estimate is shown up front."
                ),
            },
            {
                "tab":    3,
                "widget": lambda: vt.batch_btn,
                "title":  "Verify all projects",
                "body":   (
                    "Re-verify every registered project in one run and get a "
                    "consolidated report — handy for periodically re-checking "
                    "your whole archive."
                ),
            },

            # ── History tab ───────────────────────────────────────────────
            {
                "tab":    4,
                "widget": lambda: self.tabs.tabBar(),
                "title":  "History — org-wide activity",
                "body":   (
                    "Every offload, transfer, merge and verify across all "
                    "machines, in one list. Your own jobs show instantly and "
                    "offline; this is the single record of who did what, when. "
                    "We've loaded a few demo jobs so you can see the layout."
                ),
                "padding": 4,
                "on_show": lambda: self._history_tab.load_demo_data(),
            },
            {
                "tab":    4,
                "widget": lambda: self._history_tab.refresh_btn,
                "title":  "Refresh org activity",
                "body":   (
                    "Pulls the other machines' summaries (kilobytes, never the "
                    "raw logs) so you see the whole org. Use the dropdowns to "
                    "filter by operation, machine, user or project, and "
                    "double-click a row to open its custody log."
                ),
            },

            # ── Header: Settings, Report a Problem, account ───────────────
            {
                "tab":    None,
                "widget": lambda: self._settings_btn,
                "title":  "Settings",
                "body":   (
                    "Configure the shared Drive folder your activity logs ship "
                    "to (pre-set for Signal Theory) and toggle log shipping. "
                    "Most users never need to touch this."
                ),
            },
            {
                "tab":    None,
                "widget": lambda: self._feedback_btn,
                "title":  "Report a Problem",
                "body":   (
                    "Hit a bug? This bundles your recent logs plus app and OS "
                    "info into a single zip and reveals it in Finder — just email "
                    "it to us. Nothing is uploaded automatically."
                ),
            },
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

    def _refresh_music_btn(self):
        """Sync the music chip's glyph and tooltip to the current setting."""
        from core import settings as app_settings
        on = app_settings.bootup_music_enabled()
        self._music_btn.setText("♪  Music" if on else "♪  Music (off)")
        self._music_btn.setToolTip(
            'Bootup music "Sim Nights" is on. Click to turn it off.'
            if on else 'Bootup music is off. Click to turn it back on.')

    def _toggle_bootup_music(self):
        """Toggle the bootup music. Turning it OFF requires passing the exam;
        turning it back ON is free. Persists immediately."""
        from core import settings as app_settings
        from core.bootup_sound import stop_bootup_music, play_bootup_music
        if app_settings.bootup_music_enabled():
            from gui.zhivago_quiz import run_disable_exam
            if not run_disable_exam(self):
                return
            app_settings.set_bootup_music_enabled(False)
            stop_bootup_music()
        else:
            app_settings.set_bootup_music_enabled(True)
            play_bootup_music()
        self._refresh_music_btn()

    def _open_settings(self):
        """M11.2: open the Settings dialog (thin — all logic in core.settings)."""
        from gui.settings_dialog import SettingsDialog
        SettingsDialog(self).exec()

    def _report_problem(self):
        """M7.3: build a feedback zip and reveal it in Finder for the tester to email.

        Thin GUI — all collection/zip logic lives in core.feedback."""
        from PyQt6.QtWidgets import QMessageBox, QFileDialog
        from core import feedback

        suggested = feedback.default_bundle_path()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save feedback bundle", str(suggested), "Zip archives (*.zip)"
        )
        if not path:
            return
        try:
            bundle = feedback.build_feedback_zip(path)
        except Exception as exc:  # never let a feedback failure crash the app
            QMessageBox.warning(self, "Report a Problem",
                                f"Could not build the feedback bundle:\n{exc}")
            return

        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(bundle.path.parent)))
        # Finder already revealed the zip; a routine toast beats a modal here.
        from gui.toast import show_toast
        show_toast(
            self,
            f"Saved {bundle.file_count} log file(s) to {bundle.path.name} — "
            "attach it to an email describing the problem.",
            "success", msecs=5000)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._tutorial and self._tutorial.isVisible():
            self._tutorial.resize(self.centralWidget().size())

    # ── window-state persistence (size + active tab) ──────────────────────────
    def _restore_window_state(self):
        s = QSettings()
        geo = s.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        idx = s.value("window/tab_index")
        if idx is not None:
            try:
                i = int(idx)
            except (TypeError, ValueError):
                return
            if 0 <= i < self.tabs.count():
                self.tabs.setCurrentIndex(i)

    def closeEvent(self, event):
        s = QSettings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/tab_index", self.tabs.currentIndex())
        super().closeEvent(event)
