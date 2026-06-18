"""
QueuePanel — per-tab job queue widget.

Provides QueueItemRow (a single queued job) and QueuePanel (the scrollable
container with Run Queue and Clear buttons).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from gui import theme


# Status badge colours keyed by status string.
_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "Pending": (theme.TEXT_MUTED,       "#2a2a2a"),
    "Running": (theme.ACCENT_GOLD,      "#3a2a00"),
    "Done":    (theme.VERDICT_GREEN,    "#1a2a1a"),
    "Failed":  (theme.VERDICT_CORAL,    "#2a1a1a"),
    "Editing": (theme.ACCENT_INFO,      "#1a2030"),
}


class QueueItemRow(QWidget):
    """A single row in the job queue."""

    edit_clicked   = pyqtSignal()
    remove_clicked = pyqtSignal()

    def __init__(self, job_number: int, name: str, path_summary: str, parent=None):
        super().__init__(parent)
        self._status = "Pending"
        self._build_ui(job_number, name, path_summary)

    def _build_ui(self, job_number: int, name: str, path_summary: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Job number label
        num_lbl = QLabel(f"#{job_number}")
        num_lbl.setFixedWidth(28)
        num_lbl.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:12px; font-weight:bold;")
        layout.addWidget(num_lbl)

        # Editable job name
        self._name_edit = QLineEdit(name)
        self._name_edit.setFixedWidth(160)
        self._name_edit.setPlaceholderText("Job name")
        layout.addWidget(self._name_edit)

        # Path summary (truncated)
        self._path_lbl = QLabel(path_summary)
        self._path_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._path_lbl.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:11px;")
        self._path_lbl.setToolTip(path_summary)
        self._path_lbl.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._path_lbl, stretch=1)

        # Status badge
        self._status_lbl = QLabel("Pending")
        self._status_lbl.setFixedWidth(80)
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_lbl.setStyleSheet(
            f"color:{theme.TEXT_MUTED}; background:#2a2a2a;"
            " border-radius:3px; padding:2px 6px; font-size:11px; font-weight:bold;"
        )
        layout.addWidget(self._status_lbl)

        # Edit button
        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setFixedWidth(46)
        self._edit_btn.setFixedHeight(26)
        self._edit_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.TEXT_MUTED};"
            f"  border:1px solid {theme.BORDER}; border-radius:3px; font-size:11px; }}"
            f"QPushButton:hover {{ color:{theme.TEXT_PRIMARY}; border-color:#666; }}"
        )
        self._edit_btn.clicked.connect(self.edit_clicked)
        layout.addWidget(self._edit_btn)

        # Remove button
        self._remove_btn = QPushButton("x")
        self._remove_btn.setFixedWidth(26)
        self._remove_btn.setFixedHeight(26)
        self._remove_btn.setToolTip("Remove from queue")
        self._remove_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.ACCENT_CORAL};"
            f"  border:1px solid {theme.ACCENT_CORAL}; border-radius:3px; font-size:12px;"
            f"  font-weight:bold; }}"
            f"QPushButton:hover {{ background:#3a1a1a; }}"
            f"QPushButton:disabled {{ color:#555; border-color:#333; }}"
        )
        self._remove_btn.clicked.connect(self.remove_clicked)
        layout.addWidget(self._remove_btn)

        # Visual separator beneath the row
        self.setStyleSheet(
            f"QueueItemRow {{ border-bottom:1px solid {theme.BORDER}; background:transparent; }}"
        )

    def name(self) -> str:
        return self._name_edit.text()

    def set_status(self, status: str):
        self._status = status
        fg, bg = _STATUS_STYLES.get(status, (theme.TEXT_MUTED, "#2a2a2a"))
        display = status if status != "Done" else "Done ✓"
        if status == "Failed":
            display = "Failed ✗"
        self._status_lbl.setText(display)
        self._status_lbl.setStyleSheet(
            f"color:{fg}; background:{bg};"
            " border-radius:3px; padding:2px 6px; font-size:11px; font-weight:bold;"
        )
        # Disable remove while running
        self._remove_btn.setEnabled(status != "Running")
        self._edit_btn.setEnabled(status not in ("Running", "Done", "Failed"))


class QueuePanel(QWidget):
    """Scrollable job queue with Run Queue and Clear buttons."""

    run_requested    = pyqtSignal()
    clear_requested  = pyqtSignal()
    edit_requested   = pyqtSignal(int)   # index into the internal list
    remove_requested = pyqtSignal(int)   # index into the internal list

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[QueueItemRow] = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(4)

        # Header
        header = QLabel("Job Queue")
        header.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; font-size:13px; font-weight:bold;"
            " padding:2px 8px;"
        )
        outer.addWidget(header)

        # Scroll area for rows
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMaximumHeight(200)

        self._inner = QWidget()
        self._rows_layout = QVBoxLayout(self._inner)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._run_btn = QPushButton("Run Queue")
        self._run_btn.setFixedHeight(32)
        self._run_btn.setEnabled(False)
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.ACCENT_INFO}; color:#000;"
            " border-radius:4px; padding:4px 14px; font-weight:bold; font-size:12px; }"
            "QPushButton:hover { background:#4ab0e8; }"
            "QPushButton:pressed { background:#2a90c8; }"
            "QPushButton:disabled { background:#1a2030; color:#555; }"
        )
        self._run_btn.clicked.connect(self.run_requested)
        btn_row.addWidget(self._run_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(32)
        self._clear_btn.setVisible(False)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.TEXT_MUTED};"
            f"  border:1px solid {theme.BORDER}; border-radius:4px;"
            "  padding:4px 14px; font-size:12px; }"
            "QPushButton:hover { color:#fff; border-color:#888; }"
        )
        self._clear_btn.clicked.connect(self.clear_requested)
        btn_row.addWidget(self._clear_btn)

        btn_row.addStretch()
        outer.addLayout(btn_row)

        # Outer frame styling
        self.setStyleSheet(
            f"QueuePanel, QScrollArea, QWidget {{ background:{theme.CHARCOAL_LIGHT}; }}"
            f"QueuePanel {{ border:1px solid {theme.BORDER}; border-radius:6px; }}"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def add_item(self, job_number: int, name: str, path_summary: str) -> int:
        """Add a new job row. Returns its index."""
        idx = len(self._rows)
        row = QueueItemRow(job_number, name, path_summary)
        row.edit_clicked.connect(lambda i=idx: self.edit_requested.emit(i))
        row.remove_clicked.connect(lambda i=idx: self.remove_requested.emit(i))
        # Insert before the trailing stretch
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
        self._rows.append(row)
        self._update_run_btn()
        return idx

    def set_status(self, index: int, status: str):
        if 0 <= index < len(self._rows):
            self._rows[index].set_status(status)
        self._update_run_btn()

    def clear_all(self):
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._run_btn.setEnabled(False)
        self._clear_btn.setVisible(False)

    def set_run_button_enabled(self, enabled: bool):
        self._run_btn.setEnabled(enabled)

    def show_clear_button(self, visible: bool):
        self._clear_btn.setVisible(visible)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _update_run_btn(self):
        has_pending = any(r._status == "Pending" for r in self._rows)
        self._run_btn.setEnabled(has_pending)

    def remove_row(self, index: int):
        if 0 <= index < len(self._rows):
            row = self._rows.pop(index)
            self._rows_layout.removeWidget(row)
            row.deleteLater()
            # Reconnect lambdas for remaining rows (indices shifted)
            for i, r in enumerate(self._rows):
                # Disconnect all and reconnect
                try:
                    r.edit_clicked.disconnect()
                    r.remove_clicked.disconnect()
                except RuntimeError:
                    pass
                r.edit_clicked.connect(lambda idx=i: self.edit_requested.emit(idx))
                r.remove_clicked.connect(lambda idx=i: self.remove_requested.emit(idx))
        self._update_run_btn()
