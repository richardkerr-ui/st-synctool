from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QComboBox, QWidget, QHBoxLayout, QLabel,
    QHeaderView, QAbstractItemView, QMenu, QApplication,
)
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtCore import Qt, QUrl, pyqtSignal

from gui import theme
from core.merge_ops import (
    ACT_PUSH, ACT_PULL, ACT_DELETE_LOCAL, ACT_DELETE_SERVER, ACT_SKIP
)
from core.comparison import conflict_suggested_action
from core.diff_summary import ACTION_OPTIONS_BY_STATE


class DiffTable(QTableWidget):
    _COLUMNS = ["Path", "State", "Action"]

    # Action options per state live in core/diff_summary.py: the single source
    # of truth shared with the M2 summary header computation. First item is
    # the default selection. BOTH_CHANGED uses a smart per-row default (see
    # load_results); its list defines the available options only.
    _ACTIONS_BY_STATE = ACTION_OPTIONS_BY_STATE

    _STATE_COLORS = theme.STATE_COLORS

    # Dark-adapted pill colors for the Changes table
    _PILL_COLORS: dict[str, tuple[str, str]] = {
        "LOCAL_CHANGED":  ("#0d2a45", "#5a9fd4"),
        "SERVER_CHANGED": ("#132a05", "#5a9a30"),
        "BOTH_CHANGED":   ("#2a0d0d", "#c07070"),
        "LOCAL_ONLY":     ("#2a2a2a", "#888888"),
        "SERVER_ONLY":    ("#2a2a2a", "#888888"),
        "DELETED_LOCAL":  ("#2a2a2a", "#555555"),
        "DELETED_SERVER": ("#2a2a2a", "#555555"),
        "DELETED_BOTH":   ("#2a2a2a", "#555555"),
        "RENAMED":        ("#2a1a40", "#9070c0"),
        "UNCHANGED":      ("#2a2a2a", "#555555"),
    }

    # Emitted when the selected row changes.
    # Carries the DiffResult for the newly selected row, or None when the
    # selection is cleared or a non-conflict row is selected.
    conflict_selected = pyqtSignal(object)

    # Emitted when any row's action combo changes (M2: was BOTH_CHANGED only).
    # Payload: (path, new_action_text). The parent uses it to refresh the
    # unresolved-conflict count and the summary header live.
    conflict_action_changed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._action_combos = {}
        self._row_states = {}     # path -> state_name
        self._gdrive_urls = {}    # path -> gdrive_url (from server/yours manifest entries)
        self._diff_results = {}   # path -> DiffResult (stored for selection signal)
        self._build_ui()

    def _build_ui(self):
        self.setColumnCount(len(self._COLUMNS))
        self.setHorizontalHeaderLabels(self._COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setMouseTracking(True)   # so :hover repaints rows live
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.setMinimumHeight(220)   # keep ~6 rows visible even with the conflict panel open
        # Shared zebra + hover + muted-selection look (theme.table_stylesheet).
        self.setStyleSheet(theme.table_stylesheet())
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    def load_results(self, results):
        self._action_combos.clear()
        self._row_states.clear()
        self._gdrive_urls.clear()
        self._diff_results.clear()
        self.setRowCount(len(results))

        for row, r in enumerate(results):
            state_name = r.state.name
            path_str = self._extract_path(r)
            self._row_states[path_str] = state_name
            self._diff_results[path_str] = r

            # Collect gdrive_url from server or local manifest entry
            gdrive_url = (
                (r.server_entry or {}).get("gdrive_url", "")
                or (r.yours_entry or {}).get("gdrive_url", "")
            )
            if gdrive_url:
                self._gdrive_urls[path_str] = gdrive_url

            path_item = QTableWidgetItem(path_str)
            path_item.setToolTip(path_str)
            # For RENAMED rows, show the original name as tooltip
            if state_name == "RENAMED" and r.renamed_from:
                path_item.setToolTip(f"{path_str}\n(renamed from: {r.renamed_from})")
            self.setItem(row, 0, path_item)

            state_label = state_name.replace("_", " ").title()
            if state_name == "RENAMED" and r.renamed_from:
                state_label = "Renamed"
            bg, fg = self._PILL_COLORS.get(state_name, ("#2a2a2a", "#888888"))
            pill_container = QWidget()
            pill_container.setStyleSheet("background: transparent;")
            pill_layout = QHBoxLayout(pill_container)
            pill_layout.setContentsMargins(4, 2, 4, 2)
            pill_layout.setSpacing(0)
            pill = QLabel(state_label)
            pill.setStyleSheet(
                f"background: {bg}; color: {fg};"
                " border-radius: 4px; padding: 2px 7px;"
                " font-size: 11px; font-weight: 500;"
            )
            pill_layout.addWidget(pill)
            pill_layout.addStretch()
            self.setCellWidget(row, 1, pill_container)

            options = self._ACTIONS_BY_STATE.get(state_name, [ACT_SKIP])
            combo = QComboBox()
            combo.addItems(options)

            # For BOTH_CHANGED rows, pre-select the mtime-based smart default
            # instead of always defaulting to Skip.
            if state_name == "BOTH_CHANGED":
                suggested = conflict_suggested_action(r)
                if suggested in options:
                    combo.setCurrentIndex(options.index(suggested))
                else:
                    combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(0)

            combo.setStyleSheet("QComboBox { padding:2px 6px; }")
            self.setCellWidget(row, 2, combo)
            self._action_combos[path_str] = combo

            # Every row reports action changes so the summary header stays live
            combo.currentTextChanged.connect(
                lambda text, p=path_str: self.conflict_action_changed.emit(p, text)
            )

        # ResizeToContents doesn't query setCellWidget() sizeHints automatically,
        # so force a column resize after all widgets are in place.
        self.resizeColumnToContents(1)
        self.resizeColumnToContents(2)

    def get_actions(self) -> dict:
        return {p: c.currentText() for p, c in self._action_combos.items()}

    def get_states(self) -> dict:
        """Return {path: state_name} for the currently loaded results."""
        return dict(self._row_states)

    def apply_newer_wins(self):
        """For every BOTH_CHANGED row, set the action dropdown to the mtime-based
        suggestion (Push if local newer, Pull if server newer, Skip if tied/unknown).
        All other rows are left unchanged."""
        for path_str, combo in self._action_combos.items():
            if self._row_states.get(path_str) != "BOTH_CHANGED":
                continue
            result = self._diff_results.get(path_str)
            if result is None:
                continue
            suggested = conflict_suggested_action(result)
            options = [combo.itemText(i) for i in range(combo.count())]
            if suggested in options:
                combo.setCurrentIndex(options.index(suggested))

    def _on_selection_changed(self):
        """Emit conflict_selected with the DiffResult when a BOTH_CHANGED row is
        selected, or None for any other selection state."""
        selected = self.selectedItems()
        if not selected:
            self.conflict_selected.emit(None)
            return
        row = self.currentRow()
        path_item = self.item(row, 0)
        if not path_item:
            self.conflict_selected.emit(None)
            return
        path = path_item.text()
        result = self._diff_results.get(path)
        if result and result.state.name == "BOTH_CHANGED":
            self.conflict_selected.emit(result)
        else:
            self.conflict_selected.emit(None)

    def set_action_for_selected(self, action: str) -> None:
        """Set the action combo for the currently selected row (any state)."""
        row = self.currentRow()
        if row < 0:
            return
        path_item = self.item(row, 0)
        if not path_item:
            return
        combo = self._action_combos.get(path_item.text())
        if combo is None:
            return
        options = [combo.itemText(i) for i in range(combo.count())]
        if action in options:
            combo.setCurrentIndex(options.index(action))

    def unresolved_conflict_count(self) -> int:
        """Return the number of BOTH_CHANGED rows whose action is still Skip."""
        count = 0
        for path, combo in self._action_combos.items():
            if self._row_states.get(path) == "BOTH_CHANGED":
                if combo.currentText() == ACT_SKIP:
                    count += 1
        return count

    def navigate_conflict(self, direction: int) -> None:
        """Select the next (+1) or previous (-1) BOTH_CHANGED row, wrapping around."""
        conflict_rows = [
            r for r in range(self.rowCount())
            if self.item(r, 0) and self._row_states.get(self.item(r, 0).text()) == "BOTH_CHANGED"
        ]
        if not conflict_rows:
            return
        current = self.currentRow()
        if direction > 0:
            candidates = [r for r in conflict_rows if r > current]
            target = candidates[0] if candidates else conflict_rows[0]
        else:
            candidates = [r for r in conflict_rows if r < current]
            target = candidates[-1] if candidates else conflict_rows[-1]
        self.selectRow(target)
        self.scrollTo(self.model().index(target, 0))

    def _show_context_menu(self, pos):
        row = self.rowAt(pos.y())
        if row < 0:
            return
        path_item = self.item(row, 0)
        if not path_item:
            return
        path = path_item.text()

        menu = QMenu(self)

        gdrive_url = self._gdrive_urls.get(path, "")
        if gdrive_url:
            open_action = menu.addAction("Open in Drive")
            open_action.triggered.connect(
                lambda checked=False, u=gdrive_url: QDesktopServices.openUrl(QUrl(u))
            )
            menu.addSeparator()

        copy_action = menu.addAction("Copy path")
        copy_action.triggered.connect(
            lambda checked=False, p=path: QApplication.clipboard().setText(p)
        )

        menu.exec(self.viewport().mapToGlobal(pos))

    def _extract_path(self, result) -> str:
        for attr in ("path", "rel_path", "relative_path", "name", "file"):
            if hasattr(result, attr):
                val = getattr(result, attr)
                if val:
                    return str(val)
        return str(result)
