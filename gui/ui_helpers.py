"""Small shared UI affordances applied consistently across every tab.

Qt stylesheets cannot set a cursor, so a pointing-hand hover state has to be
applied per widget. Centralising it here (plus the explanatory tooltip) keeps
the affordance identical on Transfer, Merge, Offload and Verify.
"""

from PyQt6.QtCore import Qt

# M12.5: the honest awake-indicator text. Amphetamine blocks idle sleep but NOT
# lid-close sleep — closing the lid on a bare laptop force-sleeps at firmware
# level and halts the copy. So we promise "awake" and caution about the lid,
# never claim lid-close protection.
AWAKE_INDICATOR_TEXT = "🔆 Keeping Mac awake — don't close the lid"
AWAKE_INDICATOR_TOOLTIP = (
    "While a job runs, ST SyncTool stops the Mac sleeping when idle.\n"
    "It cannot stop sleep when the lid is closed: on a laptop that needs "
    "clamshell mode (external display + power + keyboard/mouse).\n"
    "Closing the lid otherwise will pause the copy."
)


def awake_indicator(parent=None):
    """A standard, hidden 'keeping awake' indicator label, identical on every
    tab. Show it while a job runs and hide it when the job ends."""
    from PyQt6.QtWidgets import QLabel
    from gui import theme
    lbl = QLabel(AWAKE_INDICATOR_TEXT, parent)
    lbl.setToolTip(AWAKE_INDICATOR_TOOLTIP)
    lbl.setStyleSheet(f"color:{theme.ACCENT_GOLD}; font-size:12px;")
    lbl.setVisible(False)
    return lbl


def open_path(path) -> None:
    """Open a file or folder in its default app (folder → Finder window)."""
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtCore import QUrl
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal_in_finder(path) -> None:
    """Reveal an item in Finder with it selected (macOS `open -R`); fall back to
    opening the containing folder elsewhere or if reveal fails."""
    import subprocess
    import sys
    from pathlib import Path
    p = Path(path)
    if sys.platform == "darwin" and p.exists():
        try:
            subprocess.run(["open", "-R", str(p)], check=False)
            return
        except Exception:
            pass
    open_path(p if p.is_dir() else p.parent)


def make_interactive(*widgets, tooltip: str = None):
    """Give clickable controls (buttons, checkboxes) a pointing-hand cursor on
    hover, and optionally a long-hover explanatory tooltip.

    Returns the first widget so a creation expression can be wrapped inline.
    """
    for w in widgets:
        if w is None:
            continue
        w.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip is not None:
            w.setToolTip(tooltip)
    return widgets[0] if widgets else None
