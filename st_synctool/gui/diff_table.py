from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QAbstractItemView, QComboBox, QLabel)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
from core.comparison import DiffResult, DiffState, STATE_LABELS
from utils.file_utils import format_bytes

ACTION_OPTIONS = ["— choose —","Keep Local","Keep Server","Keep Base","Delete","Manual Merge","Skip"]
COL_CHECK,COL_PATH,COL_STATE,COL_SIZE,COL_BASE,COL_YOURS,COL_SERVER,COL_ACTION = range(8)
HEADERS = ["","File Path","Status","Size","Base","Yours","Server","Action"]

class DiffTable(QWidget):
    actionChanged = pyqtSignal(str, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        bar = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All"); self.select_all_btn.clicked.connect(self._select_all)
        self.deselect_btn   = QPushButton("Deselect All"); self.deselect_btn.clicked.connect(self._deselect_all)
        self.bulk_action = QComboBox(); self.bulk_action.addItems(ACTION_OPTIONS)
        self.apply_bulk_btn = QPushButton("Apply to Selected"); self.apply_bulk_btn.clicked.connect(self._apply_bulk)
        self.summary_label = QLabel("")
        for w in (self.select_all_btn, self.deselect_btn): bar.addWidget(w)
        bar.addSpacing(12); bar.addWidget(QLabel("Bulk:")); bar.addWidget(self.bulk_action)
        bar.addWidget(self.apply_bulk_btn); bar.addStretch(); bar.addWidget(self.summary_label)
        layout.addLayout(bar)
        self.table = QTableWidget(); self.table.setColumnCount(8); self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(COL_PATH, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_CHECK,28); self.table.setColumnWidth(COL_ACTION,120)
        self.table.setColumnWidth(COL_STATE,130); self.table.setColumnWidth(COL_SIZE,80)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("QTableWidget{gridline-color:#2a2a2a} QHeaderView::section{background:#2a2a2a;color:#aaa;padding:4px;border:none}")
        layout.addWidget(self.table); self._results = []

    def load_results(self, results):
        self._results = results; self.table.setRowCount(len(results)); counts = {}
        for i, r in enumerate(results):
            chk = QTableWidgetItem(); chk.setCheckState(Qt.CheckState.Unchecked); self.table.setItem(i,COL_CHECK,chk)
            self.table.setItem(i,COL_PATH,QTableWidgetItem(r.path))
            label,color = STATE_LABELS[r.state]
            si = QTableWidgetItem(label); si.setForeground(QBrush(QColor(color)))
            f = QFont(); f.setBold(r.state==DiffState.BOTH_CHANGED); si.setFont(f)
            self.table.setItem(i,COL_STATE,si)
            size = (r.yours_entry or r.server_entry or {}).get("size",0)
            self.table.setItem(i,COL_SIZE,QTableWidgetItem(format_bytes(size)))
            def cs(e):
                if not e: return "—"
                v=(e.get("checksums",{})); v=v.get("sha256") or v.get("xxhash3_64") or v.get("md5") or ""
                return v[:10]+"…" if v else "—"
            self.table.setItem(i,COL_BASE,QTableWidgetItem(cs(r.base_entry)))
            self.table.setItem(i,COL_YOURS,QTableWidgetItem(cs(r.yours_entry)))
            self.table.setItem(i,COL_SERVER,QTableWidgetItem(cs(r.server_entry)))
            combo = QComboBox(); combo.addItems(ACTION_OPTIONS)
            defaults = {DiffState.SERVER_CHANGED:"Keep Server",DiffState.LOCAL_CHANGED:"Keep Local",DiffState.UNCHANGED:"Skip"}
            if r.state in defaults: combo.setCurrentText(defaults[r.state])
            combo.currentTextChanged.connect(lambda t,p=r.path: self.actionChanged.emit(p,t))
            self.table.setCellWidget(i,COL_ACTION,combo)
            counts[r.state] = counts.get(r.state,0)+1
        parts=[]
        for state,count in sorted(counts.items(),key=lambda x:x[0].value):
            l,c=STATE_LABELS[state]; parts.append(f'<span style="color:{c}">{count} {l}</span>')
        self.summary_label.setText("  ".join(parts)); self.summary_label.setTextFormat(Qt.TextFormat.RichText)

    def _select_all(self):
        for i in range(self.table.rowCount()): self.table.item(i,COL_CHECK).setCheckState(Qt.CheckState.Checked)
    def _deselect_all(self):
        for i in range(self.table.rowCount()): self.table.item(i,COL_CHECK).setCheckState(Qt.CheckState.Unchecked)
    def _apply_bulk(self):
        action=self.bulk_action.currentText()
        if action=="— choose —": return
        for i in range(self.table.rowCount()):
            if self.table.item(i,COL_CHECK).checkState()==Qt.CheckState.Checked:
                c=self.table.cellWidget(i,COL_ACTION)
                if c: c.setCurrentText(action)
    def get_actions(self):
        return {r.path: self.table.cellWidget(i,COL_ACTION).currentText()
                for i,r in enumerate(self._results) if self.table.cellWidget(i,COL_ACTION)}
