"""Small shared UI affordances applied consistently across every tab.

Qt stylesheets cannot set a cursor, so a pointing-hand hover state has to be
applied per widget. Centralising it here (plus the explanatory tooltip) keeps
the affordance identical on Transfer, Merge, Offload and Verify.
"""

from PyQt6.QtCore import Qt


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
