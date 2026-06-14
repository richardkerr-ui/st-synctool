"""Lightweight non-blocking toast for routine confirmations.

Reserve modal dialogs for decisions and destructive confirms; for "it worked"
feedback ("Apply complete", "Saved …") a toast keeps the flow. It floats near
the bottom of the given parent, colour-coded by kind, and auto-dismisses.
"""

from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import Qt, QTimer

from gui import theme

_KIND_COLORS = {
    "info":    theme.ACCENT_INFO,
    "success": theme.VERDICT_GREEN,
    "warn":    theme.VERDICT_GOLD,
    "error":   theme.VERDICT_CORAL,
}


def show_toast(parent, message: str, kind: str = "success", msecs: int = 3200) -> QLabel:
    """Show a transient toast over ``parent``. Returns the label (mainly for tests)."""
    accent = _KIND_COLORS.get(kind, theme.ACCENT_INFO)
    toast = QLabel(message, parent)
    toast.setObjectName("toast")
    toast.setWordWrap(True)
    toast.setTextFormat(Qt.TextFormat.PlainText)
    toast.setStyleSheet(
        f"background:{theme.CHARCOAL_LIGHT}; color:{theme.TEXT_PRIMARY};"
        f" border:1px solid {accent}; border-left:4px solid {accent};"
        f" border-radius:6px; padding:9px 16px; font-size:12px;"
    )
    toast.setMaximumWidth(max(260, parent.width() - 80))
    toast.adjustSize()
    _reposition(parent, toast)
    toast.show()
    toast.raise_()
    QTimer.singleShot(msecs, toast.deleteLater)
    return toast


def _reposition(parent, toast: QLabel) -> None:
    x = max(0, (parent.width() - toast.width()) // 2)
    y = max(0, parent.height() - toast.height() - 24)
    toast.move(x, y)
