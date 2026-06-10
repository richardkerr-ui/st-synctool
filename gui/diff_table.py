from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QComboBox, QWidget, QHBoxLayout, QLabel,
    QHeaderView, QAbstractItemView, QMenu, QApplication,
)
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtCore import Qt, QUrl

from gui import theme
from core.merge_ops import (
    ACT_PUSH, ACT_PULL, ACT_DELETE_LOCAL, ACT_DELETE_SERVER, ACT_SKIP
)


class DiffTable(QTableWidget):
    _COLUMNS = ["Path", "State", "Action"]

    # For each state, list of actions. First item is the default selection.
    _ACTIONS_BY_STATE = {
        "LOCAL_ONLY":     [ACT_PUSH,          ACT_DELETE_LOCAL,  ACT_SKIP],
        "SERVER_ONLY":    [ACT_PULL,          ACT_DELETE_SERVER, ACT_SKIP],
        "LOCAL_CHANGED":  [ACT_PUSH,          ACT_PULL,          ACT_SKIP],
        "SERVER_CHANGED": [ACT_PULL,          ACT_PUSH,          ACT_SKIP],
        "BOTH_CHANGED":   [ACT_SKIP,          ACT_PUSH,          ACT_PULL],
        "DELETED_LOCAL":  [ACT_SKIP,          ACT_DELETE_SERVER, ACT_PULL],
        "DELETED_SERVER": [ACT_SKIP,          ACT_DELETE_LOCAL,  ACT_PUSH],
        "DELETED_BOTH":   [ACT_SKIP],
        "RENAMED":        [ACT_SKIP,          ACT_PUSH,          ACT_PULL],
    }

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._action_combos = {}
        self._row_states = {}     # path -> state_name
        self._gdrive_urls = {}    # path -> gdrive_url (from server/yours manifest entries)
        self._build_ui()

    def _build_ui(self):
        self.setColumnCount(len(self._COLUMNS))
        self.setHorizontalHeaderLabels(self._COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.setStyleSheet(
            "QTableWidget { background:#1e1e1e; color:#cccccc;"
            "  gridline-color:#333; border:1px solid #333; border-radius:4px; }"
            "QHeaderView::section { background:#2a2a2a; color:#cccccc;"
            "  padding:4px; border:none; font-weight:bold; }"
            "QTableWidget::item { padding:4px; }"
            "QTableWidget::item:alternate { background:#252525; }"
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def load_results(self, results):
        self._action_combos.clear()
        self._row_states.clear()
        self._gdrive_urls.clear()
        self.setRowCount(len(results))

        for row, r in enumerate(results):
            state_name = r.state.name
            path_str = self._extract_path(r)
            self._row_states[path_str] = state_name

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
            combo.setCurrentIndex(0)
            combo.setStyleSheet("QComboBox { padding:2px 6px; }")
            self.setCellWidget(row, 2, combo)
            self._action_combos[path_str] = combo

    def get_actions(self) -> dict:
        return {p: c.currentText() for p, c in self._action_combos.items()}

    def get_states(self) -> dict:
        """Return {path: state_name} for the currently loaded results."""
        return dict(self._row_states)

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
