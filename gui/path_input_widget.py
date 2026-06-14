from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QComboBox, QPushButton, QFileDialog, QSizePolicy,
    QApplication,
)
from PyQt6.QtCore import pyqtSignal, QEvent
from pathlib import Path

from core.dnd import folder_from_dropped_paths
from utils.gdrive_utils import is_gdrive_url
from gui import theme
from gui.ui_helpers import make_interactive, start_dir_for


class PathInputWidget(QWidget):
    pathChanged = pyqtSignal(str)

    def __init__(self, kind: str = "source", parent=None, clipboard_url: bool = True):
        super().__init__(parent)
        self._kind = kind
        self._clipboard_url = clipboard_url   # offer to paste a Drive URL from the clipboard
        self._build_ui()
        # DITs drag a card folder onto the field rather than clicking Browse.
        self.setAcceptDrops(True)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._combo.setMinimumWidth(300)

        # Public handle to the underlying QLineEdit
        self.input = self._combo.lineEdit()
        self.input.setPlaceholderText("Drag a folder here, or paste a path / Drive URL…")
        self.input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._combo)

        # Clipboard-aware "Paste Drive link" affordance — appears only when the
        # field is empty and the clipboard holds a Google Drive URL, so a DIT who
        # just copied a share link can fill the field in one click.
        self._paste_btn = QPushButton("⎘ Paste Drive link")
        self._paste_btn.setVisible(False)
        make_interactive(self._paste_btn, tooltip="Paste the Google Drive link from your clipboard.")
        self._paste_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.GOLD};"
            f" border:1px solid {theme.GOLD}; border-radius:4px; padding:2px 8px; }}"
        )
        self._paste_btn.clicked.connect(self._paste_clipboard_url)
        layout.addWidget(self._paste_btn)
        if self._clipboard_url:
            self.input.installEventFilter(self)

        # Public handle to the browse button (so callers can rewire it)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setMinimumWidth(110)
        self.browse_btn.setFixedWidth(100)
        make_interactive(self.browse_btn)
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.browse_btn)

    # ── Drag-and-drop: accept a dropped folder (or file → its parent) ────────
    @staticmethod
    def _drop_paths(mime):
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self._drop_paths(e.mimeData()):
            self._combo.setStyleSheet(f"QComboBox {{ border:1px solid {theme.GOLD}; }}")
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragLeaveEvent(self, e):
        self._combo.setStyleSheet("")
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._combo.setStyleSheet("")
        folder = folder_from_dropped_paths(self._drop_paths(e.mimeData()))
        if folder:
            self.set_path(folder)
            self.add_to_recent(folder)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            f"Select {self._kind} folder",
            start_dir_for(self.text()),   # open where the field already points
        )
        if folder:
            self.set_path(folder)

    # ── Clipboard-aware Drive-URL paste ──────────────────────────────────────
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.FocusIn:
            self._refresh_paste_hint()
        return super().eventFilter(obj, event)

    def _refresh_paste_hint(self):
        if not self._clipboard_url:
            return
        clip = (QApplication.clipboard().text() or "").strip()
        self._paste_btn.setVisible(bool(clip) and is_gdrive_url(clip) and not self.text())

    def _paste_clipboard_url(self):
        clip = (QApplication.clipboard().text() or "").strip()
        if clip:
            self.set_path(clip)
            self.add_to_recent(clip)
        self._paste_btn.setVisible(False)

    def _on_text_changed(self, text: str):
        # Once the field has content the paste hint is irrelevant.
        if text and self._paste_btn.isVisible():
            self._paste_btn.setVisible(False)
        self.pathChanged.emit(text)

    # ── Public API ──────────────────────────────────────────────
    def text(self) -> str:
        return self._combo.currentText().strip()

    def setText(self, path: str):
        self.set_path(path)

    def set_path(self, path: str):
        self._combo.setCurrentText(path)

    def add_to_recent(self, path: str):
        if not path:
            return
        idx = self._combo.findText(path)
        if idx >= 0:
            self._combo.removeItem(idx)
        self._combo.insertItem(0, path)
        self._combo.setCurrentIndex(0)
        while self._combo.count() > 10:
            self._combo.removeItem(10)
