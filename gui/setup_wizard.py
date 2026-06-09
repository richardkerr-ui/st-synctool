"""
First-launch setup wizard for ST SyncTool.

Replaces the README's manual rclone setup with a UI that can't be misread.
"""

from __future__ import annotations

import subprocess
import importlib
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout, QWidget, QWizard, QWizardPage,
)

from core.setup_checks import (
    CheckResult, CheckStatus,
    create_gdrive_remote, run_all_checks, check_rclone_auth,
)
from utils.gdrive_utils import RCLONE_REMOTE
from core.oauth_config import is_remote_using_default_rclone_creds
from gui import theme


class CheckWorker(QThread):
    finished_with_results = pyqtSignal(list)

    def __init__(self, remote_name: str = RCLONE_REMOTE):
        super().__init__()
        self.remote_name = remote_name

    def run(self):
        results = run_all_checks(self.remote_name)
        self.finished_with_results.emit(results)


class FixWorker(QThread):
    output = pyqtSignal(str)
    finished_with_code = pyqtSignal(int)

    def __init__(self, command: List[str]):
        super().__init__()
        self.command = command

    def run(self):
        try:
            proc = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as e:
            self.output.emit(f"Failed to start: {e}\n")
            self.finished_with_code.emit(-1)
            return

        for line in proc.stdout:
            self.output.emit(line)
        proc.wait()
        self.finished_with_code.emit(proc.returncode)


class RemoteCreateWorker(QThread):
    finished_with_result = pyqtSignal(object)

    def __init__(self, name: str, shared_drive: bool):
        super().__init__()
        self.name = name
        self.shared_drive = shared_drive

    def run(self):
        result = create_gdrive_remote(self.name, self.shared_drive)
        self.finished_with_result.emit(result)


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome to ST SyncTool")
        self.setSubTitle("Let's make sure your machine is ready.")

        layout = QVBoxLayout()
        body = QLabel(
            "This wizard will:\n\n"
            "  1.  Check that Homebrew, rclone, and Python packages are installed\n"
            "  2.  Install anything missing (with your permission)\n"
            "  3.  Connect ST SyncTool to your Google Drive account\n"
            "  4.  Verify the connection works\n\n"
            "Total time: about 2 minutes if everything's already set up, "
            "or 5-10 minutes if you need to install rclone and authenticate Drive."
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        self.setLayout(layout)


class SystemCheckPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("System Check")
        self.setSubTitle("Looking for required tools...")

        self.results: List[CheckResult] = []
        self._all_ok = False

        layout = QVBoxLayout()

        self.status_label = QLabel("Running checks...")
        layout.addWidget(self.status_label)

        self.results_box = QTextEdit()
        self.results_box.setReadOnly(True)
        self.results_box.setFont(QFont("Menlo", 11))
        layout.addWidget(self.results_box)

        button_row = QHBoxLayout()
        self.fix_button = QPushButton("Install missing components")
        self.fix_button.setStyleSheet(theme.primary_button_style())
        self.fix_button.setEnabled(False)
        self.fix_button.clicked.connect(self._run_fixes)
        button_row.addWidget(self.fix_button)

        self.recheck_button = QPushButton("Re-check")
        self.recheck_button.clicked.connect(self._start_checks)
        button_row.addWidget(self.recheck_button)
        layout.addLayout(button_row)

        self.fix_log = QTextEdit()
        self.fix_log.setReadOnly(True)
        self.fix_log.setFont(QFont("Menlo", 10))
        self.fix_log.setMaximumHeight(150)
        self.fix_log.hide()
        layout.addWidget(self.fix_log)

        self.setLayout(layout)

    def initializePage(self):
        self._start_checks()

    def _start_checks(self):
        self.fix_button.setEnabled(False)
        self.recheck_button.setEnabled(False)
        self.status_label.setText("Running checks...")
        self.results_box.clear()

        self.check_worker = CheckWorker()
        self.check_worker.finished_with_results.connect(self._on_checks_done)
        self.check_worker.start()

    def _on_checks_done(self, results: List[CheckResult]):
        self.results = results
        self.recheck_button.setEnabled(True)
        self._render_results()

        fixable = [r for r in results if r.can_auto_fix and r.status != CheckStatus.OK]
        self.fix_button.setEnabled(bool(fixable))

        blocking = [r for r in results
                    if r.status in (CheckStatus.MISSING, CheckStatus.ERROR)
                    and "remote" not in r.name and "authentication" not in r.name]
        self._all_ok = not blocking

        if self._all_ok:
            self.status_label.setText(
                "System ready. Click Next to set up Google Drive."
            )
        elif fixable:
            self.status_label.setText(
                f"{len(blocking)} component(s) need attention. "
                "Click 'Install missing components' to fix automatically."
            )
        else:
            self.status_label.setText(
                "Some components need manual install. See details above."
            )
        self.completeChanged.emit()

    def _render_results(self):
        color_map = {
            CheckStatus.OK:      theme.ACCENT_GREEN,
            CheckStatus.MISSING: theme.ACCENT_CORAL,
            CheckStatus.ERROR:   theme.ACCENT_CORAL,
            CheckStatus.WARNING: theme.ACCENT_GOLD,
        }
        icon_map = {
            CheckStatus.OK:      "OK",
            CheckStatus.MISSING: "MISSING",
            CheckStatus.ERROR:   "ERROR",
            CheckStatus.WARNING: "WARN",
        }

        rows = []
        for r in self.results:
            color = color_map[r.status]
            icon = icon_map[r.status]
            rows.append(
                f'<div style="margin-bottom: 10px;">'
                f'  <span style="color: {color}; font-weight: bold; '
                f'               font-family: Menlo, monospace;">[{icon}]</span>'
                f'  &nbsp;<span style="color: {theme.CREAM}; font-weight: bold;">'
                f'    {r.name}</span><br>'
                f'  <span style="color: {theme.MUTED_TEXT}; '
                f'               font-family: Menlo, monospace; '
                f'               margin-left: 18px;">{r.message}</span>'
            )
            if r.fix_hint and r.status != CheckStatus.OK:
                rows.append(
                    f'  <br><span style="color: {theme.ACCENT_INFO}; '
                    f'                   font-family: Menlo, monospace; '
                    f'                   margin-left: 18px;">-> {r.fix_hint}</span>'
                )
            rows.append('</div>')

        self.results_box.setHtml("".join(rows))

    def _run_fixes(self):
        fixable = [r for r in self.results
                   if r.can_auto_fix and r.status != CheckStatus.OK]
        if not fixable:
            return

        self.fix_log.show()
        self.fix_log.clear()
        self.fix_button.setEnabled(False)
        self.recheck_button.setEnabled(False)
        self._fix_queue = list(fixable)
        self._run_next_fix()

    def _run_next_fix(self):
        if not self._fix_queue:
            self.fix_log.append("\nAll fixes complete. Re-running checks...\n")
            importlib.invalidate_caches()
            self._start_checks()
            return

        result = self._fix_queue.pop(0)
        self.fix_log.append(f"\n$ {' '.join(result.fix_command)}\n")

        self.fix_worker = FixWorker(result.fix_command)
        self.fix_worker.output.connect(self.fix_log.insertPlainText)
        self.fix_worker.finished_with_code.connect(self._on_fix_done)
        self.fix_worker.start()

    def _on_fix_done(self, code: int):
        self.fix_log.append(f"\n(exit {code})\n")
        self._run_next_fix()

    def isComplete(self) -> bool:
        return self._all_ok


class DriveConnectPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Connect to Google Drive")
        self.setSubTitle(f"Set up the '{RCLONE_REMOTE}' rclone remote.")

        self._connected = False

        layout = QVBoxLayout()

        self.info_label = QLabel(
            "ST SyncTool needs access to your Google Drive.\n\n"
            "When you click Connect, your browser will open and ask you to "
            "sign in with your Signal Theory Google account. Grant access, "
            "then return here - the wizard will continue automatically."
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.shared_drive_check = QCheckBox(
            "This is a Shared Drive (Team Drive) - leave unchecked for personal Drive"
        )
        self.shared_drive_check.setChecked(False)
        layout.addWidget(self.shared_drive_check)

        self.connect_button = QPushButton("Connect to Google Drive")
        self.connect_button.setStyleSheet(theme.primary_button_style())
        self.connect_button.clicked.connect(self._start_connect)
        layout.addWidget(self.connect_button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch()
        self.setLayout(layout)

    def initializePage(self):
        auth_result = check_rclone_auth(RCLONE_REMOTE, timeout=10)
        if not auth_result.ok:
            return  # leave default UI; user will click Connect

        using_defaults = is_remote_using_default_rclone_creds(RCLONE_REMOTE)
        if using_defaults:
            # Remote works, but is on rclone's shared (throttled) OAuth client.
            # Refuse to advance until they migrate to ST's credentials.
            self._connected = False
            self.info_label.setText(
                f"Connected to Google Drive as '{RCLONE_REMOTE}', but this "
                f"remote is using rclone's shared OAuth credentials.\n\n"
                "Google throttles those across every rclone user worldwide. "
                "Click Reconnect to migrate to Signal Theory's own OAuth "
                "client for full-speed transfers."
            )
            self.connect_button.setText("Reconnect (recommended)")
            self.connect_button.setStyleSheet(theme.primary_button_style())
        else:
            self._connected = True
            self.info_label.setText(
                f"Already connected to Google Drive as '{RCLONE_REMOTE}' "
                f"with Signal Theory's OAuth credentials.\n\n"
                f"{auth_result.message}\n\n"
                "Click Next to continue. To re-authenticate or use a "
                "different account, click Reconnect."
            )
            self.connect_button.setText("Reconnect")
        self.completeChanged.emit()

    def _start_connect(self):
        self.connect_button.setEnabled(False)
        self.progress.show()
        self.status_label.setText(
            "Opening browser... complete sign-in there, then come back."
        )

        self.create_worker = RemoteCreateWorker(
            RCLONE_REMOTE,
            self.shared_drive_check.isChecked(),
        )
        self.create_worker.finished_with_result.connect(self._on_connect_done)
        self.create_worker.start()

    def _on_connect_done(self, result: CheckResult):
        self.progress.hide()
        self.connect_button.setEnabled(True)

        if result.status == CheckStatus.OK:
            self._connected = True
            self.status_label.setText(f"OK: {result.message}")
        else:
            self._connected = False
            self.status_label.setText(f"FAILED: {result.message}")
            QMessageBox.warning(
                self, "Connection failed",
                f"Could not create the rclone remote.\n\n{result.message}\n\n"
                "Common causes:\n"
                "  - You closed the browser before completing sign-in\n"
                "  - You denied permission\n"
                "  - Network connectivity issue\n\n"
                "Try again or check rclone's output in Terminal."
            )
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._connected


class VerifyPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Verify Connection")
        self.setSubTitle("Confirming everything works end-to-end.")

        self._verified = False

        layout = QVBoxLayout()
        self.status_label = QLabel("Testing...")
        layout.addWidget(self.status_label)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(QFont("Menlo", 11))
        layout.addWidget(self.details)

        self.setLayout(layout)

    def initializePage(self):
        self.status_label.setText("Testing connection to Google Drive...")
        self.details.clear()

        self.worker = CheckWorker()
        self.worker.finished_with_results.connect(self._on_done)
        self.worker.start()

    def _on_done(self, results: List[CheckResult]):
        auth = next((r for r in results if "authentication" in r.name), None)
        if auth and auth.ok:
            self._verified = True
            self.status_label.setText("Setup complete. You're ready to sync.")
            self.details.setPlainText(
                f"{auth.message}\n\n"
                "What you can do now:\n\n"
                "  - Transfer tab: move folders between local SSD and Drive\n"
                "  - Merge tab: reconcile diverged copies with a three-way diff\n"
                "  - Verify tab: confirm a folder matches its manifest\n\n"
                "Click Finish to start using ST SyncTool."
            )
        else:
            self.status_label.setText("Verification failed.")
            msg = auth.message if auth else "Could not run auth check."
            self.details.setPlainText(
                f"{msg}\n\n"
                "Go back to the previous page and try reconnecting."
            )
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._verified


class SetupWizard(QWizard):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("ST SyncTool - Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(700, 550)

        self.addPage(WelcomePage())
        self.addPage(SystemCheckPage())
        self.addPage(DriveConnectPage())
        self.addPage(VerifyPage())

        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)


def should_show_wizard() -> bool:
    results = run_all_checks()
    for r in results:
        if r.status in (CheckStatus.MISSING, CheckStatus.ERROR):
            return True
    return False
