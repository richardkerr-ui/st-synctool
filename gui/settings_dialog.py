"""M11.2 — Settings dialog (thin Qt layer over core.settings).

Lets the user configure the shared Drive remote base for the org-wide activity
log and toggle log shipping. No logic lives here beyond reading/writing
``core.settings``; everything testable is in core.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QDialogButtonBox, QFrame,
)

from core import settings as app_settings
from gui import theme
from gui.ui_helpers import make_interactive


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
        make_interactive(self.shipping_chk)
        root.addWidget(self.shipping_chk)

        # M12.4: completion sound toggle.
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color:{theme.BORDER};")
        root.addWidget(divider)
        sound_header = QLabel("Offload completion")
        sound_header.setStyleSheet(f"color:{theme.CREAM};font-size:14px;font-weight:bold;")
        root.addWidget(sound_header)
        self.sound_chk = QCheckBox("Play a sound when an offload finishes")
        make_interactive(
            self.sound_chk,
            tooltip="Sound a chime on completion so you notice the result even "
                    "when you have stepped away from the cart.",
        )
        root.addWidget(self.sound_chk)

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
        if app_settings.default_activity_remote_base():
            self.remote_base_input.setPlaceholderText(
                "Leave blank to use the shared Signal Theory folder")
        self.shipping_chk.setChecked(app_settings.log_shipping_enabled())
        self.sound_chk.setChecked(app_settings.completion_sound_enabled())

    def _save(self):
        app_settings.set_activity_remote_base(self.remote_base_input.text())
        app_settings.set_log_shipping_enabled(self.shipping_chk.isChecked())
        app_settings.set_completion_sound_enabled(self.sound_chk.isChecked())
        self.accept()
