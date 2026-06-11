from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton, QComboBox, QFileDialog
from PyQt6.QtCore import pyqtSignal, QSettings
from utils.gdrive_utils import get_clipboard_gdrive_url

MAX_RECENT = 12

class PathInputWidget(QWidget):
    pathChanged = pyqtSignal(str)
    def __init__(self, label="path", parent=None):
        super().__init__(parent)
        self._label = label
        self._settings = QSettings("SignalTheory","STSyncTool")
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter path or Google Drive URL…")
        self.input.focusInEvent = self._on_focus_in
        self.input.textChanged.connect(lambda t: self.pathChanged.emit(t))
        self.recent_btn = QComboBox(); self.recent_btn.setFixedWidth(28); self.recent_btn.setToolTip("Recent")
        self.recent_btn.addItem("▾"); self._load_recent()
        self.recent_btn.currentIndexChanged.connect(self._on_recent_selected)
        self.browse_btn = QPushButton("Browse…"); self.browse_btn.setFixedWidth(70)
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.input, stretch=1); layout.addWidget(self.recent_btn); layout.addWidget(self.browse_btn)

    def _on_focus_in(self, event):
        QLineEdit.focusInEvent(self.input, event)
        if not self.input.text():
            g = get_clipboard_gdrive_url()
            if g: self.input.setText(g)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, f"Select {self._label}")
        if path: self.input.setText(path); self.add_to_recent(path)

    def _load_recent(self):
        r = self._settings.value(f"recent_{self._label}", [], type=list)
        self.recent_btn.clear(); self.recent_btn.addItem("▾")
        for item in r: self.recent_btn.addItem(item)

    def _on_recent_selected(self, idx):
        if idx > 0: self.input.setText(self.recent_btn.itemText(idx)); self.recent_btn.setCurrentIndex(0)

    def add_to_recent(self, path):
        r = self._settings.value(f"recent_{self._label}", [], type=list)
        if path in r: r.remove(path)
        r.insert(0, path); r = r[:MAX_RECENT]
        self._settings.setValue(f"recent_{self._label}", r); self._load_recent()

    def text(self): return self.input.text().strip()
    def setText(self, t): self.input.setText(t)
