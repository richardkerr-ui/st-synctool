"""M11.2 — Settings dialog (thin Qt layer over core.settings).

Lets the user configure the shared Drive remote base for the org-wide activity
log and toggle log shipping. No logic lives here beyond reading/writing
``core.settings``; everything testable is in core.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QDialogButtonBox,
)

from core import settings as app_settings
from gui import theme


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self._build_ui()
        self._load()

    def _build_ui(self):
        root = QVBoxLayout(self)

        header = QLabel("Org-wide activity log")
        header.setStyleSheet(f"color:{theme.CREAM};font-size:14px;font-weight:bold;")
        root.addWidget(header)

        blurb = QLabel(
            "Set the shared Google Drive folder that every machine ships its "
            "logs and manifests to. Use an rclone remote path, for example "
            "gdrive:ST_SyncTool_Activity. Leave blank to disable org reporting.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
        root.addWidget(blurb)

        form = QFormLayout()
        self.remote_base_input = QLineEdit()
        self.remote_base_input.setPlaceholderText("gdrive:ST_SyncTool_Activity")
        form.addRow("Activity remote base:", self.remote_base_input)
        root.addLayout(form)

        self.shipping_chk = QCheckBox("Ship logs to the shared folder (recommended)")
        root.addWidget(self.shipping_chk)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _load(self):
        # Show only an explicitly-set value; leave the field empty when relying on
        # the shipped default, and surface that default as placeholder text so the
        # user sees what shipping will use if they leave it blank.
        explicit = app_settings.get_setting("activity_remote_base", "") or ""
        self.remote_base_input.setText(explicit)
        default = app_settings.default_activity_remote_base()
        if default:
            self.remote_base_input.setPlaceholderText(f"{default}  (shipped default)")
        self.shipping_chk.setChecked(app_settings.log_shipping_enabled())

    def _save(self):
        app_settings.set_activity_remote_base(self.remote_base_input.text())
        app_settings.set_log_shipping_enabled(self.shipping_chk.isChecked())
        self.accept()
