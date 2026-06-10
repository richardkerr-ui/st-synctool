from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QComboBox, QPushButton, QFileDialog, QSizePolicy
)
from PyQt6.QtCore import pyqtSignal
from pathlib import Path


class PathInputWidget(QWidget):
    pathChanged = pyqtSignal(str)

    def __init__(self, kind: str = "source", parent=None):
        super().__init__(parent)
        self._kind = kind
        self._build_ui()

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
        self.input.setPlaceholderText("Paste path or GDrive URL…")
        self.input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._combo)

        # Public handle to the browse button (so callers can rewire it)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setMinimumWidth(110)
        self.browse_btn.setFixedWidth(100)
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.browse_btn)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            f"Select {self._kind} folder",
            self.text() or str(Path.home()),
        )
        if folder:
            self.set_path(folder)

    def _on_text_changed(self, text: str):
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
