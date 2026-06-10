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
    QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QTextEdit, QVBoxLayout,
    QWidget, QWizard, QWizardPage,
)

from core.setup_checks import (
    CheckResult, CheckStatus,
    create_gdrive_remote, run_all_checks, check_rclone_auth,
)
from utils.gdrive_utils import RCLONE_REMOTE
from core.oauth_config import (
    get_active_remote, is_remote_using_default_rclone_creds,
    list_drive_remotes, get_remote_account_email, save_active_remote,
)
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


class AccountFetchWorker(QThread):
    """Fetch the Google account email for a single rclone remote."""
    result_ready = pyqtSignal(str, str)  # (remote_name, email_or_empty)

    def __init__(self, remote_name: str):
        super().__init__()
        self.remote_name = remote_name

    def run(self):
        email = get_remote_account_email(self.remote_name) or ""
        self.result_ready.emit(self.remote_name, email)


class RemoteAuthCheckWorker(QThread):
    """Check whether a single remote can authenticate successfully."""
    result_ready = pyqtSignal(str, object)  # (remote_name, CheckResult)

    def __init__(self, remote_name: str):
        super().__init__()
        self.remote_name = remote_name

    def run(self):
        result = check_rclone_auth(self.remote_name, timeout=15)
        self.result_ready.emit(self.remote_name, result)


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
        self.setSubTitle("Choose an account or connect a new one.")

        self._validated = False
        self._selected_remote: Optional[str] = None
        self._radio_buttons: dict = {}   # remote_name -> QRadioButton
        self._email_labels: dict = {}    # remote_name -> QLabel
        self._fetch_workers: list = []
        self._auth_worker: Optional[RemoteAuthCheckWorker] = None
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        self._outer = QVBoxLayout()

        self._list_frame = QFrame()
        self._list_frame.setStyleSheet(
            f"QFrame {{ border: 1px solid #333; border-radius: 6px; "
            f"background: {theme.CHARCOAL_LIGHT}; }}"
        )
        self._list_layout = QVBoxLayout(self._list_frame)
        self._list_layout.setContentsMargins(12, 10, 12, 10)
        self._list_layout.setSpacing(8)
        self._outer.addWidget(self._list_frame)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        self._outer.addWidget(sep)

        new_row = QHBoxLayout()
        self._new_button = QPushButton("Connect New Account")
        self._new_button.setStyleSheet(theme.primary_button_style())
        self._new_button.clicked.connect(self._start_new_connect)
        new_row.addWidget(self._new_button)
        new_row.addStretch()
        self._outer.addLayout(new_row)

        self._shared_check = QCheckBox(
            "Shared Drive (Team Drive) — leave unchecked for personal Drive"
        )
        self._shared_check.setChecked(False)
        self._outer.addWidget(self._shared_check)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.hide()
        self._outer.addWidget(self._progress)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._outer.addWidget(self._status_label)

        self._outer.addStretch()
        self.setLayout(self._outer)

    def initializePage(self):
        self._validated = False
        self._selected_remote = None
        self._clear_list()

        existing = list_drive_remotes()
        if not existing:
            placeholder = QLabel(
                "No Drive connections found. Click 'Connect New Account' to get started."
            )
            placeholder.setStyleSheet(f"color: {theme.MUTED_TEXT};")
            placeholder.setWordWrap(True)
            self._list_layout.addWidget(placeholder)
        else:
            for remote in existing:
                self._add_remote_row(remote)
            # Pre-select the currently active remote, or the first one
            active = get_active_remote()
            pre_select = active if active in self._radio_buttons else existing[0]
            self._radio_buttons[pre_select].setChecked(True)
            self._on_remote_selected(pre_select)

        self.completeChanged.emit()

    def _clear_list(self):
        for worker in self._fetch_workers:
            worker.quit()
        self._fetch_workers.clear()
        self._radio_buttons.clear()
        self._email_labels.clear()
        for btn in self._button_group.buttons():
            self._button_group.removeButton(btn)
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_remote_row(self, remote_name: str):
        row = QHBoxLayout()

        radio = QRadioButton(remote_name)
        radio.setStyleSheet(f"color: {theme.CREAM}; font-weight: bold;")
        radio.toggled.connect(lambda checked, r=remote_name: checked and self._on_remote_selected(r))
        self._button_group.addButton(radio)
        self._radio_buttons[remote_name] = radio
        row.addWidget(radio)

        email_label = QLabel("fetching account...")
        email_label.setStyleSheet(f"color: {theme.MUTED_TEXT}; font-size: 12px;")
        self._email_labels[remote_name] = email_label
        row.addWidget(email_label)
        row.addStretch()

        container = QWidget()
        container.setLayout(row)
        self._list_layout.addWidget(container)

        worker = AccountFetchWorker(remote_name)
        worker.result_ready.connect(self._on_email_ready)
        self._fetch_workers.append(worker)
        worker.start()

    def _on_email_ready(self, remote_name: str, email: str):
        label = self._email_labels.get(remote_name)
        if not label:
            return
        if email:
            label.setText(f"— {email}")
            label.setStyleSheet(f"color: {theme.MUTED_TEXT}; font-size: 12px;")
        else:
            label.setText("— account unknown")
            label.setStyleSheet(f"color: {theme.ACCENT_GOLD}; font-size: 12px;")

    def _on_remote_selected(self, remote_name: str):
        if self._selected_remote == remote_name:
            return
        self._selected_remote = remote_name
        self._validated = False
        self._status_label.setText(f"Checking '{remote_name}'...")
        self._progress.show()
        self.completeChanged.emit()

        if self._auth_worker and self._auth_worker.isRunning():
            self._auth_worker.quit()

        self._auth_worker = RemoteAuthCheckWorker(remote_name)
        self._auth_worker.result_ready.connect(self._on_auth_check_done)
        self._auth_worker.start()

    def _on_auth_check_done(self, remote_name: str, result: CheckResult):
        if remote_name != self._selected_remote:
            return
        self._progress.hide()

        if result.status == CheckStatus.OK:
            using_defaults = is_remote_using_default_rclone_creds(remote_name)
            if using_defaults:
                self._validated = False
                self._status_label.setText(
                    f"'{remote_name}' uses rclone's shared OAuth client, which Google "
                    "throttles. Click 'Connect New Account' to re-authenticate with "
                    "Signal Theory's credentials for full-speed transfers."
                )
            else:
                self._validated = True
                self._status_label.setText(
                    f"'{remote_name}' is connected and working. "
                    "Click Next to continue, or connect a different account below."
                )
                save_active_remote(remote_name)
                self.wizard().setProperty("chosen_remote", remote_name)
        else:
            self._validated = False
            self._status_label.setText(
                f"[!] '{remote_name}' failed: {result.message}  "
                "Try 'Connect New Account' to re-authenticate."
            )

        self.completeChanged.emit()

    def _start_new_connect(self):
        existing = list_drive_remotes()
        new_name = "gdrive"
        if new_name in existing:
            i = 2
            while f"gdrive{i}" in existing:
                i += 1
            new_name = f"gdrive{i}"

        self._new_button.setEnabled(False)
        self._progress.show()
        self._status_label.setText(
            "Opening browser — sign in with your Signal Theory Google account, "
            "then return here."
        )

        self._create_worker = RemoteCreateWorker(new_name, self._shared_check.isChecked())
        self._create_worker.finished_with_result.connect(
            lambda r: self._on_new_connect_done(new_name, r)
        )
        self._create_worker.start()

    def _on_new_connect_done(self, new_name: str, result: CheckResult):
        self._progress.hide()
        self._new_button.setEnabled(True)

        if result.status == CheckStatus.OK:
            save_active_remote(new_name)
            self.wizard().setProperty("chosen_remote", new_name)
            self._validated = True
            self._status_label.setText(f"Connected as '{new_name}'. Click Next to continue.")
            self._add_remote_row(new_name)
            self._radio_buttons[new_name].setChecked(True)
            self._selected_remote = new_name
        else:
            self._status_label.setText(f"Connection failed: {result.message}")
            QMessageBox.warning(
                self, "Connection failed",
                f"Could not connect to Google Drive.\n\n{result.message}\n\n"
                "Common causes:\n"
                "  - Browser closed before completing sign-in\n"
                "  - Permission was denied\n"
                "  - Network issue\n\n"
                "Try again or check rclone output in Terminal."
            )

        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._validated


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

        chosen = self.wizard().property("chosen_remote") or RCLONE_REMOTE
        self.worker = CheckWorker(chosen)
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
        self.setMinimumSize(700, 600)

        self.setProperty("chosen_remote", get_active_remote())

        self.addPage(WelcomePage())
        self.addPage(SystemCheckPage())
        self.addPage(DriveConnectPage())
        self.addPage(VerifyPage())

        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)

    @property
    def chosen_remote(self) -> str:
        return self.property("chosen_remote") or get_active_remote()


def should_show_wizard() -> bool:
    results = run_all_checks()
    for r in results:
        if r.status in (CheckStatus.MISSING, CheckStatus.ERROR):
            return True
    return False
