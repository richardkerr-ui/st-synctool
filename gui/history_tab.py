"""M9.3 — In-app History browser.

Renders the merged per-machine activity index (local shards + cached org shards)
as readable rows with dropdown filters. "Refresh org activity" downloads the
other machines' summary shards (kilobytes) from the configured remote base; it
never lists or reads the raw logs over the network.

Thin Qt layer: all query/merge/format logic lives in core.history and
core.activity_index. This file renders rows and dropdowns and dispatches the
refresh to a background thread.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from core import history, activity_index
from core import settings as app_settings
from gui import theme

_ANY = "— all —"
_FILTER_LABELS = {"operation": "Operation", "workstation": "Workstation",
                  "user": "User", "project": "Project"}


class _RefreshWorker(QThread):
    """Downloads other machines' shards off the main thread (never raises)."""
    finished = pyqtSignal(int)  # number of shards fetched

    def run(self):
        try:
            fetched = activity_index.fetch_remote_shards(
                app_settings.activity_remote_base())
            self.finished.emit(len(fetched))
        except Exception:
            self.finished.emit(0)


class HistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._records = []          # merged raw records
        self._rows = []             # currently displayed HistoryRows
        self._build_ui()
        self.reload()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)

        filt = QHBoxLayout()
        self._filter_combos = {}
        for field in history.FILTER_FIELDS:
            filt.addWidget(QLabel(_FILTER_LABELS[field] + ":"))
            combo = QComboBox()
            combo.addItem(_ANY)
            combo.currentIndexChanged.connect(self._apply_filters)
            filt.addWidget(combo)
            self._filter_combos[field] = combo
        filt.addStretch()

        self.refresh_btn = QPushButton("Refresh org activity")
        self.refresh_btn.clicked.connect(self._refresh_org)
        filt.addWidget(self.refresh_btn)
        root.addLayout(filt)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
        root.addWidget(self.status_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["When", "Workstation", "Operation", "Details", "Verdict"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._open_row_log)
        root.addWidget(self.table)

        hint = QLabel("Double-click a row to open its custody log (jobs run on this machine).")
        hint.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
        root.addWidget(hint)

    # ── data ──────────────────────────────────────────────────────────────────
    def reload(self):
        """Reload merged records (local + cached org) and repopulate filters/table."""
        self._records = activity_index.load_org_records()
        self._populate_filter_options()
        self._apply_filters()

    def _populate_filter_options(self):
        for field, combo in self._filter_combos.items():
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(_ANY)
            for val in history.distinct_values(self._records, field):
                combo.addItem(val)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _active_filters(self) -> dict:
        out = {}
        for field, combo in self._filter_combos.items():
            val = combo.currentText()
            if val and val != _ANY:
                out[field] = val
        return out

    def _apply_filters(self):
        rows = history.rows_for(self._records, **self._active_filters())
        self._rows = rows
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            details = row.to_text()
            cells = [row.date_label, row.workstation, row.operation_label,
                     details, row.verdict]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
        n = len(self._records)
        self.status_label.setText(
            f"{len(rows)} of {n} job(s)" if n else
            "No activity recorded yet (configure the remote base in Settings to "
            "see other machines).")

    # ── per-row actions ───────────────────────────────────────────────────────
    def _open_row_log(self, row: int, _col: int):
        """M9.3: open the selected job's custody log if it exists locally."""
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        if row < 0 or row >= len(self._rows):
            return
        name = self._rows[row].log_filename
        path = activity_index.find_local_log(name) if name else None
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.status_label.setText(
                "Custody log not available locally for this job"
                + (f" ({name})" if name else "") + ".")

    # ── refresh ─────────────────────────────────────────────────────────────
    def _refresh_org(self):
        if not app_settings.activity_remote_base():
            self.status_label.setText(
                "Set an activity remote base in Settings to refresh org activity.")
            return
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Refreshing org activity…")
        self._refresh_worker = _RefreshWorker()
        self._refresh_worker.finished.connect(self._on_refresh_done)
        self._refresh_worker.start()

    def _on_refresh_done(self, fetched: int):
        self.refresh_btn.setEnabled(True)
        self.reload()
        self.status_label.setText(f"Refreshed — {fetched} machine shard(s) pulled.")
