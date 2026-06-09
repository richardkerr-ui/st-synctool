from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton
from PyQt6.QtGui import QFont, QTextCursor
from datetime import datetime


class LogWidget(QWidget):
    _COLORS = {
        "info":    "#cccccc",
        "success": "#4caf50",
        "warning": "#ff9800",
        "error":   "#f44336",
    }
    _ICONS = {
        "info":    "ℹ",
        "success": "✔",
        "warning": "⚠",
        "error":   "✖",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Courier New", 10))
        self._text.setStyleSheet(
            "QTextEdit {"
            "  background:#1e1e1e; color:#cccccc;"
            "  border:1px solid #333; border-radius:4px;"
            "  padding:4px;"
            "}"
        )
        layout.addWidget(self._text)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedHeight(24)
        clear_btn.setFixedWidth(80)
        clear_btn.setStyleSheet("font-size:11px;")
        clear_btn.clicked.connect(self.clear_log)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

    # ── Public API ──────────────────────────────────────────────
    def log(self, message: str, level: str = "info"):
        color = self._COLORS.get(level, "#cccccc")
        icon  = self._ICONS.get(level, "•")
        ts    = datetime.now().strftime("%H:%M:%S")
        html  = (
            f'<span style="color:#555">[{ts}]</span> '
            f'<span style="color:{color}">{icon} {message}</span>'
        )
        self._text.append(html)
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    def clear_log(self):
        self._text.clear()

    def clear(self):
        self._text.clear()
