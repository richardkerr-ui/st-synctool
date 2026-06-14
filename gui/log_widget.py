from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QLabel, QProgressBar, QFrame,
)
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

    def __init__(self, title: str = "", with_progress: bool = False,
                 placeholder: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._with_progress = with_progress
        self._placeholder = placeholder
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("LogFrame")
        frame.setStyleSheet(
            "QFrame#LogFrame {"
            "  background: #2a2a2a;"
            "  border: 1px solid #3a3a3a;"
            "  border-radius: 6px;"
            "}"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)
        outer.addWidget(frame)

        # Header row: title label (left) + Clear button (right)
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(6)
        if self._title:
            lbl = QLabel(self._title.upper())
            lbl.setStyleSheet(
                "font-size: 11px; color: #555; letter-spacing: 0.04em; background: transparent;"
            )
            hl.addWidget(lbl)
        hl.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(22)
        clear_btn.setFixedWidth(50)
        clear_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 11px; padding: 2px 8px;"
            "  background: #333; border: 1px solid #4a4a4a; border-radius: 4px; color: #888;"
            "}"
            "QPushButton:hover { background: #3a3a3a; }"
        )
        clear_btn.clicked.connect(self.clear_log)
        hl.addWidget(clear_btn)
        fl.addWidget(header)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #3a3a3a; border: none;")
        fl.addWidget(sep)

        # Optional progress area (shown during an active operation)
        if self._with_progress:
            self._progress_container = QWidget()
            self._progress_container.setStyleSheet("background: #1e1e1e;")
            pc = QVBoxLayout(self._progress_container)
            pc.setContentsMargins(8, 6, 8, 4)
            pc.setSpacing(3)

            # ── progress bar ────────────────────────────────────────────────
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setFixedHeight(4)
            self.progress_bar.setTextVisible(False)
            self.progress_bar.setStyleSheet(
                "QProgressBar { background: #333; border: none; border-radius: 2px; }"
                "QProgressBar::chunk { background: #F6BE00; border-radius: 2px; }"
            )
            pc.addWidget(self.progress_bar)

            # ── stats row: file count + ETA (left) | speed (right) ──────────
            stats_row = QHBoxLayout()
            stats_row.setContentsMargins(0, 0, 0, 0)
            stats_row.setSpacing(0)

            self._file_count_label = QLabel("")
            self._file_count_label.setStyleSheet(
                "color: #777; font-size: 11px; background: transparent;"
            )
            stats_row.addWidget(self._file_count_label)
            stats_row.addStretch()

            self._speed_label = QLabel("")
            self._speed_label.setStyleSheet(
                "color: #777; font-size: 11px; background: transparent;"
            )
            stats_row.addWidget(self._speed_label)
            pc.addLayout(stats_row)

            # ── currently-transferring filename ──────────────────────────────
            self.current_file_label = QLabel("")
            self.current_file_label.setStyleSheet(
                "color: #555; font-size: 11px; background: transparent;"
            )
            pc.addWidget(self.current_file_label)

            self._progress_container.setVisible(False)
            fl.addWidget(self._progress_container)

        # Log text body
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        if self._placeholder:
            # Empty-state guidance shown until the first line is logged.
            self._text.setPlaceholderText(self._placeholder)
        self._text.setFont(QFont("Courier New", 10))
        self._text.setStyleSheet(
            "QTextEdit {"
            "  background: #1e1e1e; color: #cccccc;"
            "  border: none; padding: 6px; border-radius: 0;"
            "}"
        )
        fl.addWidget(self._text)

    # ── Progress helpers ────────────────────────────────────────────────────

    def set_progress(
        self,
        pct: int,
        current_file: str = "",
        speed: str = "",
        eta: str = "",
        files_done: "int | None" = None,
        files_total: "int | None" = None,
    ):
        """Update the inline progress area.

        Parameters
        ----------
        pct          -- 0-100 percentage for the progress bar
        current_file -- filename currently being transferred (may be empty)
        speed        -- human-readable transfer speed, e.g. "12.3 MB/s"
        eta          -- time remaining string from rclone, e.g. "1m23s" or "-"
        files_done   -- number of files transferred so far
        files_total  -- total number of files to transfer
        """
        if not self._with_progress:
            return
        if not self._progress_container.isVisible():
            self._progress_container.setVisible(True)

        self.progress_bar.setValue(pct)

        # File count + ETA label
        if files_done is not None and files_total is not None:
            count_text = f"{files_done} / {files_total} files"
            if eta and eta != "-":
                count_text += f"  —  {eta} remaining"
            self._file_count_label.setText(count_text)
        elif eta and eta != "-":
            self._file_count_label.setText(f"{eta} remaining")
        else:
            self._file_count_label.setText("")

        # Speed label
        self._speed_label.setText(speed if speed else "")

        # Current file — truncate to last 2 path components for long paths
        if current_file:
            from pathlib import PurePosixPath
            parts = PurePosixPath(current_file.replace("\\", "/")).parts
            display = "/".join(parts[-2:]) if len(parts) > 2 else current_file
            self.current_file_label.setText(display)
        elif current_file == "":
            pass  # keep whatever was shown last
        else:
            self.current_file_label.setText("")

    def hide_progress(self):
        if not self._with_progress:
            return
        self._progress_container.setVisible(False)
        self.current_file_label.setText("")
        self._file_count_label.setText("")
        self._speed_label.setText("")

    # ── Public API ──────────────────────────────────────────────────────────

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
