from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtGui import QTextCursor
from datetime import datetime

LEVEL_COLORS = {"error":"#f44747","warning":"#f4a744","success":"#6aa84f","info":"#cccccc"}

class LogWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setStyleSheet("QTextEdit{background:#1a1a1a;font-family:'SF Mono','Menlo','Consolas',monospace;font-size:11px;border:1px solid #333;border-radius:4px;padding:4px;}")

    def log(self, message: str, level: str = "info"):
        color = LEVEL_COLORS.get(level, LEVEL_COLORS["info"])
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"error":"✗","warning":"⚠","success":"✓","info":"·"}.get(level,"·")
        self.append(f'<span style="color:#555">[{ts}]</span> <span style="color:{color}">{prefix} {message}</span>')
        self.moveCursor(QTextCursor.MoveOperation.End)

    def clear_log(self): self.clear()
