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
from PyQt6.QtGui import QColor

from core import history, activity_index
from core.verdict_style import verdict_symbol
from core import settings as app_settings
from gui import theme

_ANY = "— all —"
_FILTER_LABELS = {"operation": "Operation", "workstation": "Workstation",
                  "user": "User", "project": "Project"}
_LOG_ROLE       = Qt.ItemDataRole.UserRole        # stores a row's custody-log filename
_TIMESTAMP_ROLE = Qt.ItemDataRole.UserRole + 1   # stores the ISO timestamp for fuzzy lookup
_OPERATION_ROLE = Qt.ItemDataRole.UserRole + 2   # stores the operation string
_RECORD_ROLE    = Qt.ItemDataRole.UserRole + 3   # stores the raw activity record dict


class _SortItem(QTableWidgetItem):
    """Table item that sorts by an explicit key (e.g. the When column sorts by
    ISO timestamp, not by its "3h ago" display text)."""
    def __init__(self, text: str, sort_key=None):
        super().__init__(text)
        self._key = text if sort_key is None else sort_key

    def __lt__(self, other):
        if isinstance(other, _SortItem):
            return self._key < other._key
        return super().__lt__(other)


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
        self.setStyleSheet(theme.tab_stylesheet(theme.tab_accent("History")))
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 12)
        root.setSpacing(12)

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

        # Org-health staleness warning (hidden unless a machine has gone quiet).
        self.staleness_label = QLabel("")
        self.staleness_label.setWordWrap(True)
        self.staleness_label.setStyleSheet(
            f"color:{theme.CHARCOAL};background:{theme.ACCENT_CORAL};"
            "border-radius:4px;padding:5px 8px;font-size:12px;")
        self.staleness_label.setVisible(False)
        root.addWidget(self.staleness_label)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
        root.addWidget(self.status_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["When", "Workstation", "Operation", "Project", "Details", "Verdict"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMouseTracking(True)   # so :hover repaints rows live
        self.table.setSortingEnabled(True)  # click a header to sort
        self.table.setStyleSheet(theme.table_stylesheet())
        # Details (col 4) takes the slack; the rest size to their content so the
        # Verdict glyph + word (e.g. "⚠ NOT_CLEARED") never truncates.
        hh = self.table.horizontalHeader()
        for col in (0, 1, 2, 3, 5):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)  # newest first
        self.table.cellDoubleClicked.connect(self._open_row_log)
        root.addWidget(self.table)

        # Empty state — shown instead of a blank grid on a fresh install.
        self._empty_label = QLabel(
            "No activity yet.\n\nRun an offload, transfer or verification and "
            "it'll appear here. Use “Refresh org activity” to pull other "
            "machines’ jobs once a remote base is set in Settings.")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color:{theme.TEXT_MUTED};font-size:13px;padding:40px;")
        self._empty_label.setVisible(False)
        root.addWidget(self._empty_label, stretch=1)

        hint = QLabel("Double-click a row to open its custody log (jobs run on this machine).")
        hint.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
        root.addWidget(hint)

    # ── data ──────────────────────────────────────────────────────────────────
    def reload(self):
        """Reload merged records (local + cached org) and repopulate filters/table."""
        self._records = activity_index.load_org_records()
        self._populate_filter_options()
        self._apply_filters()
        self._refresh_staleness()

    def load_demo_data(self):
        """Populate the tab with illustrative org activity for the onboarding
        tour / a fresh install, but only when there is no real history yet so it
        never masks actual records."""
        if self._records:
            return
        from core.demo import demo_activity_records
        self._records = demo_activity_records()
        self._populate_filter_options()
        self._apply_filters()
        self._refresh_staleness()
        self.status_label.setText(
            f"{len(self._records)} job(s)  (demo — click Refresh org activity to load real data)")

    def _refresh_staleness(self):
        warning = history.staleness_warning(self._records)
        if warning:
            self.staleness_label.setText(warning)
            self.staleness_label.setVisible(True)
        else:
            self.staleness_label.setVisible(False)

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
        raw_records = history.query_history(self._records, **self._active_filters())
        rows = [history.format_row(r) for r in raw_records]
        self._rows = rows
        # Populate with sorting off, then restore it — inserting into a live
        # sorted table reshuffles rows mid-loop and corrupts the mapping.
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, (row, raw) in enumerate(zip(rows, raw_records)):
            when = _SortItem(history.relative_date_label(row.timestamp),
                             sort_key=row.timestamp)
            when.setToolTip(history.full_timestamp_label(row.timestamp))
            # Store log filename, timestamp, operation, and raw record so
            # double-click works after re-sort and can show data for any op type.
            when.setData(_LOG_ROLE, row.log_filename)
            when.setData(_TIMESTAMP_ROLE, row.timestamp)
            when.setData(_OPERATION_ROLE, row.operation_label.lower())
            when.setData(_RECORD_ROLE, raw)
            self.table.setItem(r, 0, when)
            self.table.setItem(r, 1, _SortItem(row.workstation))
            self.table.setItem(r, 2, _SortItem(row.operation_label))
            self.table.setItem(r, 3, _SortItem(row.project_label))
            self.table.setItem(r, 4, _SortItem(row.details_text()))
            self.table.setItem(r, 5, _SortItem(row.verdict))
            self._style_operation_cell(r, row.operation_label)
            self._style_verdict_cell(r, row.verdict)
        self.table.setSortingEnabled(True)
        n = len(self._records)
        # Fresh install (no records at all) → show the guiding empty state
        # instead of a bare grid. Filtered-to-zero keeps the grid + status line.
        is_fresh = n == 0
        self._empty_label.setVisible(is_fresh)
        self.table.setVisible(not is_fresh)
        self.status_label.setText(
            f"{len(rows)} of {n} job(s)" if n else
            "No activity recorded yet (configure the remote base in Settings to "
            "see other machines).")

    def _style_operation_cell(self, row: int, operation: str):
        """Tint the Operation cell with its tab accent so the table reads at a
        glance (Transfer blue · Merge purple · Offload coral · Verify green)."""
        item = self.table.item(row, 2)
        if item is None or not operation:
            return
        item.setForeground(QColor(theme.tab_accent(operation)))

    def _style_verdict_cell(self, row: int, verdict: str):
        """Colour the verdict cell by severity and prefix an accessibility glyph
        (colour is never the sole signal, for colour-blind users)."""
        item = self.table.item(row, 5)
        if item is None or not verdict:
            return
        item.setText(f"{verdict_symbol(verdict)} {verdict}")
        item.setForeground(QColor(theme.verdict_color(verdict)))

    # ── per-row actions ───────────────────────────────────────────────────────
    def _open_row_log(self, row: int, _col: int):
        """Open the selected job's log in an in-app viewer window."""
        from gui.job_log_dialog import JobLogDialog
        when_item = self.table.item(row, 0)
        if when_item is None:
            return
        name = when_item.data(_LOG_ROLE)
        op = when_item.data(_OPERATION_ROLE)
        # Transfer jobs use the manifest JSON; never open a .txt for them.
        if op == "transfer" and name and not name.endswith(".json"):
            name = ""
        path = activity_index.find_local_log(name) if name else None
        if path is not None and op == "transfer" and path.suffix != ".json":
            path = None
        if path is None:
            ts = when_item.data(_TIMESTAMP_ROLE)
            if ts:
                path = activity_index.find_local_log_by_timestamp(ts, op)
                if path is not None and op == "transfer" and path.suffix != ".json":
                    path = None
        record = when_item.data(_RECORD_ROLE)
        dlg = JobLogDialog(path, record, parent=self)
        dlg.show()

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
