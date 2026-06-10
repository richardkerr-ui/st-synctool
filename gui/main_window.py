from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QStatusBar, QLabel, QPushButton
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QTimer

from PyQt6.QtCore import QThread, pyqtSignal

from gui.transfer_tab  import TransferTab
from gui.merge_tab     import MergeTab
from gui.verify_tab    import VerifyTab
from gui.offload_tab   import OffloadTab
from gui.setup_wizard import SetupWizard, should_show_wizard
from gui              import theme
from core.setup_checks import check_rclone_auth, CheckStatus
from core.oauth_config import get_active_remote, get_remote_account_email


class _AccountLabelWorker(QThread):
    result_ready = pyqtSignal(str, str)  # (remote, email_or_empty)

    def __init__(self, remote: str):
        super().__init__()
        self.remote = remote

    def run(self):
        # _check_auth_health already ran rclone lsd which refreshes the token,
        # so the config file has a fresh access_token by the time we get here.
        email = get_remote_account_email(self.remote) or ""
        self.result_ready.emit(self.remote, email)


class MainWindow(QMainWindow):
    def __init__(self, force_setup: bool = False):
        super().__init__()
        self.setWindowTitle("ST SyncTool -- Signal Theory")
        self.setMinimumSize(1100, 780)
        self._force_setup = force_setup
        self._build_ui()

        # Defer wizard until the window is on screen so the dialog has a parent.
        QTimer.singleShot(100, self._maybe_launch_wizard)

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
        header.addWidget(title)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(self._account_label)
        header.addWidget(version)
        root.addLayout(header)
        root.addSpacing(8)

        # Auth-health banner (hidden by default; shown when Drive auth fails)
        self._auth_banner = self._build_auth_banner()
        root.addWidget(self._auth_banner)

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

        self.tabs.addTab(TransferTab(self),  "Transfer")
        self.tabs.addTab(MergeTab(self),     "Merge")
        self.tabs.addTab(OffloadTab(self),   "Offload")
        self.tabs.addTab(VerifyTab(self),    "Verify")
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
        """
        Coral-bordered banner for auth/connection issues.

        Coral matches theme.STATE_COLORS['BOTH_CHANGED'] — our 'decision
        needed' color. An expired OAuth token is functionally equivalent:
        Drive transfers will fail until the user acts.
        """
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

        fix_btn = QPushButton("Open Setup")
        fix_btn.setStyleSheet(theme.primary_button_style())
        fix_btn.clicked.connect(self._launch_wizard)
        layout.addWidget(fix_btn)

        banner.hide()
        return banner

    def _maybe_launch_wizard(self):
        if self._force_setup or should_show_wizard():
            self._launch_wizard()
        else:
            self._check_auth_health()

    def _launch_wizard(self):
        wiz = SetupWizard(self)
        wiz.exec()
        chosen = wiz.chosen_remote
        if chosen:
            import utils.gdrive_utils
            utils.gdrive_utils.RCLONE_REMOTE = chosen
        self._check_auth_health()

    def _check_auth_health(self):
        remote = get_active_remote()
        result = check_rclone_auth(remote, timeout=10)
        if result.status == CheckStatus.OK:
            self._auth_banner.hide()
            self.status_bar.showMessage("Ready")
            self._update_account_label(remote)
        else:
            self._account_label.setText("")
            self._banner_label.setText(
                f'<span style="color: {theme.ACCENT_CORAL}; font-weight: bold;">[!]</span>'
                f'&nbsp;&nbsp;<b>Google Drive connection issue:</b> {result.message}'
                f'&nbsp;&nbsp;<span style="color: {theme.MUTED_TEXT};">'
                f'Transfers to/from Drive will fail until this is fixed.</span>'
            )
            self._auth_banner.show()
            self.status_bar.showMessage("Drive auth issue — see banner")

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
