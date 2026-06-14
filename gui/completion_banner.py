"""Shared big job-completion banner used across Transfer, Merge, Offload, Verify.

A persistent banner the user can't miss when a job finishes (the bottom toast
fades too fast), green for success and coral for a problem, with an ✕ to
dismiss. Each tab supplies its own message via show_result().
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from gui import theme
from gui.ui_helpers import make_interactive


class CompletionBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CompletionBanner")
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 8, 10)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.msg, stretch=1)
        self._close = QPushButton("✕")
        self._close.setFixedSize(24, 24)
        self._close.setToolTip("Dismiss — also clears when you start the next job")
        make_interactive(self._close)
        self._close.clicked.connect(self.dismiss)
        lay.addWidget(self._close)

    def show_result(self, text: str, ok: bool) -> None:
        """Show the banner: green when ok, coral when there's a problem."""
        bg = theme.VERDICT_GREEN if ok else theme.VERDICT_CORAL
        fg = "#0c1a0f" if ok else "#1a0c0c"
        self.msg.setText(text)
        self.setStyleSheet(
            f"QFrame#CompletionBanner {{ background:{bg}; border-radius:6px; }}"
            f" QLabel {{ background:transparent; color:{fg}; font-size:15px; font-weight:bold; }}"
            f" QPushButton {{ background:transparent; color:{fg}; border:none; font-weight:bold; }}"
        )
        self.setVisible(True)

    def dismiss(self) -> None:
        self.setVisible(False)
