"""
gui/tutorial_overlay.py
-----------------------
Spotlight-style onboarding tour for ST SyncTool.

Usage
-----
    from gui.tutorial_overlay import TutorialOverlay
    overlay = TutorialOverlay(main_window)
    overlay.start()

Each step is a dict:
    {
        "tab":     int | None,          # switch to this tab index before showing
        "widget":  callable | None,     # lambda that returns the target QWidget
        "title":   str,
        "body":    str,
        "padding": int,                 # extra pixels around spotlight (default 8)
    }

If "widget" is None the card is centered with no spotlight.
"""

from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy,
)
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QFont
from PyQt6.QtCore import Qt, QRect, QPoint, QSettings, QTimer


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SETTINGS_KEY   = "tutorial/seen"
OVERLAY_ALPHA  = 180        # 0-255, darkness of the backdrop
CARD_WIDTH     = 360
CARD_PADDING   = 16
SPOTLIGHT_PAD  = 10         # default extra padding around the target widget
CORNER_RADIUS  = 8

# Colours (kept inline so this file has no theme import dependency)
_CARD_BG      = "#1e1e1e"
_CARD_BORDER  = "#007acc"
_TITLE_COLOR  = "#ffffff"
_BODY_COLOR   = "#cccccc"
_BTN_PRIMARY  = "#007acc"
_BTN_SKIP     = "#444444"
_BTN_TEXT     = "#ffffff"
_STEP_MUTED   = "#666666"


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _mark_seen():
    s = QSettings("Signal Theory", "ST SyncTool")
    s.setValue(SETTINGS_KEY, "1")


def tutorial_already_seen() -> bool:
    s = QSettings("Signal Theory", "ST SyncTool")
    return s.value(SETTINGS_KEY, "") == "1"


def reset_tutorial():
    """Call this to force the tour to show again (e.g. from a Help menu)."""
    s = QSettings("Signal Theory", "ST SyncTool")
    s.remove(SETTINGS_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# Card widget
# ─────────────────────────────────────────────────────────────────────────────

class _TutorialCard(QFrame):
    """Floating card that displays step title, body text, and nav buttons."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedWidth(CARD_WIDTH)
        self.setStyleSheet(f"""
            QFrame {{
                background: {_CARD_BG};
                border: 1.5px solid {_CARD_BORDER};
                border-radius: {CORNER_RADIUS}px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        layout.setSpacing(10)

        # Step counter  e.g. "Step 2 of 7"
        self._step_label = QLabel()
        self._step_label.setStyleSheet(f"color:{_STEP_MUTED}; font-size:11px; background:transparent; border:none;")
        layout.addWidget(self._step_label)

        # Title
        self._title_label = QLabel()
        self._title_label.setFont(QFont("SF Pro Display", 14, QFont.Weight.Bold))
        self._title_label.setStyleSheet(f"color:{_TITLE_COLOR}; background:transparent; border:none;")
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        # Body
        self._body_label = QLabel()
        self._body_label.setStyleSheet(f"color:{_BODY_COLOR}; font-size:13px; background:transparent; border:none;")
        self._body_label.setWordWrap(True)
        self._body_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(self._body_label)

        layout.addSpacing(4)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._skip_btn = QPushButton("Skip tour")
        self._skip_btn.setFixedHeight(30)
        self._skip_btn.setStyleSheet(f"""
            QPushButton {{
                background:{_BTN_SKIP}; color:{_BTN_TEXT};
                border:none; border-radius:4px; font-size:12px; padding:0 12px;
            }}
            QPushButton:hover {{ background:#555; }}
        """)

        self._prev_btn = QPushButton("← Back")
        self._prev_btn.setFixedHeight(30)
        self._prev_btn.setStyleSheet(f"""
            QPushButton {{
                background:{_BTN_SKIP}; color:{_BTN_TEXT};
                border:none; border-radius:4px; font-size:12px; padding:0 12px;
            }}
            QPushButton:hover {{ background:#555; }}
        """)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setFixedHeight(30)
        self._next_btn.setStyleSheet(f"""
            QPushButton {{
                background:{_BTN_PRIMARY}; color:{_BTN_TEXT};
                border:none; border-radius:4px; font-size:12px; font-weight:bold; padding:0 16px;
            }}
            QPushButton:hover {{ background:#0092f0; }}
        """)

        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._prev_btn)
        btn_row.addWidget(self._next_btn)
        layout.addLayout(btn_row)

    # ── Public setters ────────────────────────────────────────────────────

    def set_content(self, step_index: int, total: int, title: str, body: str):
        self._step_label.setText(f"Step {step_index + 1} of {total}")
        self._title_label.setText(title)
        self._body_label.setText(body)

        is_last = (step_index == total - 1)
        self._next_btn.setText("Done ✓" if is_last else "Next →")
        self._prev_btn.setVisible(step_index > 0)

        # Recalculate height to fit wrapped text
        self.adjustSize()

    @property
    def skip_btn(self):
        return self._skip_btn

    @property
    def prev_btn(self):
        return self._prev_btn

    @property
    def next_btn(self):
        return self._next_btn


# ─────────────────────────────────────────────────────────────────────────────
# Overlay widget
# ─────────────────────────────────────────────────────────────────────────────

class TutorialOverlay(QWidget):
    """
    Full-window translucent overlay.  Paints a dark backdrop with a
    transparent spotlight cutout around the current target widget, and
    positions a card near the spotlight.
    """

    def __init__(self, main_window):
        # Parent to centralWidget so the overlay fills the content area.
        # Keep a separate reference to the real MainWindow for tab access.
        super().__init__(main_window.centralWidget())
        self._main_win = main_window        # QMainWindow — for tabs, etc.
        self._steps    = []
        self._index    = 0

        # Sit on top of everything, but let mouse through where needed
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setWindowFlags(Qt.WindowType.Widget)

        # Card
        self._card = _TutorialCard(self)
        self._card.skip_btn.clicked.connect(self._finish)
        self._card.prev_btn.clicked.connect(self._prev)
        self._card.next_btn.clicked.connect(self._next)

        self.hide()

    # ── Public API ────────────────────────────────────────────────────────

    def set_steps(self, steps: list):
        """Supply the step list before calling start()."""
        self._steps = steps

    def start(self):
        if not self._steps:
            return
        self._index = 0
        self.resize(self.parentWidget().size())
        self.raise_()
        self.show()
        self._show_step(self._index)

    # ── Navigation ───────────────────────────────────────────────────────

    def _next(self):
        if self._index >= len(self._steps) - 1:
            self._finish()
        else:
            self._index += 1
            self._show_step(self._index)

    def _prev(self):
        if self._index > 0:
            self._index -= 1
            self._show_step(self._index)

    def _finish(self):
        _mark_seen()
        self.hide()

    # ── Step rendering ────────────────────────────────────────────────────

    def _show_step(self, index: int):
        step  = self._steps[index]
        total = len(self._steps)

        # Switch tabs if needed
        tab_index = step.get("tab")
        if tab_index is not None:
            tabs = getattr(self._main_win, "tabs", None)
            if tabs:
                tabs.setCurrentIndex(tab_index)

        # Longer delay so Qt fully repaints the new tab before we measure widgets
        QTimer.singleShot(80, lambda: self._render_step(step, index, total))

    def _render_step(self, step: dict, index: int, total: int):
        self._card.set_content(index, total, step["title"], step["body"])
        self.raise_()
        self._card.raise_()
        self._position_card(step)
        self.update()  # repaint backdrop/spotlight

    def _spotlight_rect(self, step: dict) -> QRect | None:
        """Return the spotlight rectangle in overlay-local coords, or None."""
        widget_fn = step.get("widget")
        if not widget_fn:
            return None
        try:
            widget = widget_fn()
        except Exception:
            return None
        if not widget or not widget.isVisible():
            return None

        pad   = step.get("padding", SPOTLIGHT_PAD)
        # Map into the coordinate space of the overlay's parent (centralWidget)
        tl    = widget.mapTo(self.parentWidget(), QPoint(0, 0))
        rect  = QRect(tl.x() - pad, tl.y() - pad,
                      widget.width() + pad * 2, widget.height() + pad * 2)
        return rect

    def _position_card(self, step: dict):
        """Place the card to the side of (or below/above) the spotlight."""
        spot  = self._spotlight_rect(step)
        ow, oh = self.width(), self.height()
        cw    = self._card.width()
        ch    = self._card.sizeHint().height()
        gap   = 16  # gap between spotlight edge and card

        if spot is None:
            # No spotlight — center the card
            x = (ow - cw) // 2
            y = (oh - ch) // 2
        else:
            # Try placing to the right, then left, then below, then above
            candidates = [
                (spot.right() + gap, spot.top()),                        # right
                (spot.left() - cw - gap, spot.top()),                    # left
                (spot.left(), spot.bottom() + gap),                      # below
                (spot.left(), spot.top() - ch - gap),                    # above
            ]
            x, y = candidates[0]
            for cx, cy in candidates:
                if 0 <= cx and cx + cw <= ow and 0 <= cy and cy + ch <= oh:
                    x, y = cx, cy
                    break

            # Clamp to overlay bounds
            x = max(gap, min(x, ow - cw - gap))
            y = max(gap, min(y, oh - ch - gap))

        self._card.move(x, y)
        self._card.resize(cw, max(ch, self._card.minimumSizeHint().height()))
        self._card.show()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        spot = None
        if self._steps and 0 <= self._index < len(self._steps):
            spot = self._spotlight_rect(self._steps[self._index])

        # Full overlay rect
        full = QPainterPath()
        full.addRect(0, 0, self.width(), self.height())

        if spot:
            # Cut out the spotlight
            cutout = QPainterPath()
            cutout.addRoundedRect(
                float(spot.x()), float(spot.y()),
                float(spot.width()), float(spot.height()),
                CORNER_RADIUS, CORNER_RADIUS,
            )
            backdrop = full - cutout

            # Draw backdrop
            painter.fillPath(backdrop, QColor(0, 0, 0, OVERLAY_ALPHA))

            # Draw spotlight border
            painter.setPen(QColor(_CARD_BORDER))
            painter.drawRoundedRect(spot, CORNER_RADIUS, CORNER_RADIUS)
        else:
            painter.fillPath(full, QColor(0, 0, 0, OVERLAY_ALPHA))

        painter.end()

    # ── Resize tracking ───────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible() and self._steps:
            step = self._steps[self._index]
            self._position_card(step)
            self.update()
