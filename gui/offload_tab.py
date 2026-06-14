"""
Offload tab — camera card / audio recorder ingest.

Phase 5 items 22–40 (SYNCTOOL_CONTEXT.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QCheckBox, QLineEdit, QSpinBox, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog,
    QDialogButtonBox, QComboBox, QInputDialog, QMessageBox,
    QSplitter, QFileDialog, QSizePolicy, QFrame, QAbstractScrollArea,
)

from gui import theme
from gui.log_widget import LogWidget
from core.offload import (
    OffloadSource, OffloadDest, OffloadConfig, CellResult, CellState,
    run_offload, scan_naming_patterns, detect_cross_source_duplicates,
    detect_subfolder_collisions,
)
from core.thumbnail import ffmpeg_available, pillow_available
import core.projects as projects
from utils.volume_watcher import VolumeWatcher

# ---------------------------------------------------------------------------
# Cell state styling
# ---------------------------------------------------------------------------

_STATE_STYLE: dict[CellState, tuple[str, str]] = {
    CellState.PENDING:    ("#555",             "Pending"),
    CellState.HASHING:    (theme.ACCENT_INFO,  "Hashing…"),
    CellState.COPYING:    (theme.ACCENT_GOLD,  "Copying…"),
    CellState.VERIFYING:  (theme.ACCENT_INFO,  "Verifying…"),
    CellState.COMMITTING: (theme.ACCENT_INFO,  "Committing…"),
    CellState.THUMBNAILS: (theme.ACCENT_INFO,  "Thumbnails…"),
    CellState.DONE:       (theme.VERDICT_GREEN, "✓ Done"),
    CellState.FAILED:     (theme.VERDICT_CORAL, "✕ Failed"),
    CellState.SKIPPED:    (theme.VERDICT_MUTED, "· Skipped"),
}


# ---------------------------------------------------------------------------
# Row widgets
# ---------------------------------------------------------------------------

class SourceRowWidget(QWidget):
    removed = pyqtSignal(object)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self._build_ui(index)

    def _build_ui(self, index: int):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self._enable = QCheckBox()
        self._enable.setChecked(True)
        self._enable.setFixedWidth(20)
        layout.addWidget(self._enable)

        self._label = QLineEdit()
        self._label.setPlaceholderText(f"Label (e.g. CAM_A)")
        self._label.setText(f"Source {index}")
        self._label.setFixedWidth(110)
        layout.addWidget(self._label)

        self._path = QLineEdit()
        self._path.setPlaceholderText("Source path…")
        self._path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._path)

        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        # Subfolder kept as a hidden attribute so to_offload_source() still works
        self._subfolder = QLineEdit()
        self._subfolder.setVisible(False)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.setFixedHeight(28)
        remove_btn.setToolTip("Remove this source")
        remove_btn.setStyleSheet(
            f"color:{theme.ACCENT_CORAL};font-weight:bold;font-size:13px;"
        )
        remove_btn.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(remove_btn)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select source folder", str(Path.home())
        )
        if folder:
            self._path.setText(folder)
            if not self._label.text().strip() or self._label.text().startswith("Source "):
                self._label.setText(Path(folder).name)

    def to_offload_source(self) -> Optional[OffloadSource]:
        path_str = self._path.text().strip()
        label    = self._label.text().strip()
        if not path_str or not label:
            return None
        return OffloadSource(
            label=label,
            path=Path(path_str),
            subfolder=self._subfolder.text().strip(),
            enabled=self._enable.isChecked(),
        )


class DestRowWidget(QWidget):
    removed = pyqtSignal(object)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self._build_ui(index)

    def _build_ui(self, index: int):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self._enable = QCheckBox()
        self._enable.setChecked(True)
        self._enable.setFixedWidth(20)
        layout.addWidget(self._enable)

        self._label = QLineEdit()
        self._label.setPlaceholderText(f"Label (e.g. RAID_1)")
        self._label.setText(f"Dest {index}")
        self._label.setFixedWidth(110)
        layout.addWidget(self._label)

        self._path = QLineEdit()
        self._path.setPlaceholderText("Destination path…")
        self._path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._path)

        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(28)
        remove_btn.setFixedHeight(28)
        remove_btn.setToolTip("Remove this destination")
        remove_btn.setStyleSheet(
            f"color:{theme.ACCENT_CORAL};font-weight:bold;font-size:13px;"
        )
        remove_btn.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(remove_btn)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select destination folder", str(Path.home())
        )
        if folder:
            self._path.setText(folder)
            if not self._label.text().strip() or self._label.text().startswith("Dest "):
                self._label.setText(Path(folder).name)

    def to_offload_dest(self) -> Optional[OffloadDest]:
        path_str = self._path.text().strip()
        label    = self._label.text().strip()
        if not path_str or not label:
            return None
        return OffloadDest(
            label=label,
            path=Path(path_str),
            enabled=self._enable.isChecked(),
        )

    def set_from_dict(self, d: dict):
        self._label.setText(d.get("label", ""))
        self._path.setText(d.get("path", ""))
        self._enable.setChecked(d.get("enabled", True))

    def to_dict(self) -> dict:
        return {
            "label":   self._label.text().strip(),
            "path":    self._path.text().strip(),
            "enabled": self._enable.isChecked(),
        }


# ---------------------------------------------------------------------------
# Status matrix widget
# ---------------------------------------------------------------------------

class StatusMatrixWidget(QTableWidget):
    """M×N grid: sources as rows, destinations as columns."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setStyleSheet(
            "QTableWidget { gridline-color: #3a3a3a; }"
            "QHeaderView::section { background:#2a2a2a; color:#888; padding:4px; border:none; }"
        )

    def configure(self, sources: list[str], dests: list[str]) -> None:
        self.setRowCount(len(sources))
        self.setColumnCount(len(dests))
        self.setHorizontalHeaderLabels(dests)
        self.setVerticalHeaderLabels(sources)
        for r in range(len(sources)):
            for c in range(len(dests)):
                item = QTableWidgetItem("Pending")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(__import__("PyQt6.QtGui", fromlist=["QColor"]).QColor("#555"))
                self.setItem(r, item.row() if False else r, item)
                # row/col set correctly below
        # rebuild properly
        for r, src_label in enumerate(sources):
            for c, dst_label in enumerate(dests):
                self._set_cell(r, c, CellState.PENDING)

    def update_cell(self, src_label: str, dst_label: str, state: CellState) -> None:
        src_labels = [self.verticalHeaderItem(r).text() for r in range(self.rowCount())]
        dst_labels = [self.horizontalHeaderItem(c).text() for c in range(self.columnCount())]
        if src_label not in src_labels or dst_label not in dst_labels:
            return
        r = src_labels.index(src_label)
        c = dst_labels.index(dst_label)
        self._set_cell(r, c, state)

    def update_cell_progress(self, src_label: str, dst_label: str, text: str) -> None:
        """Overwrite the cell text without changing its color (preserves last state color)."""
        from PyQt6.QtGui import QColor
        src_labels = [self.verticalHeaderItem(r).text() for r in range(self.rowCount())]
        dst_labels = [self.horizontalHeaderItem(c).text() for c in range(self.columnCount())]
        if src_label not in src_labels or dst_label not in dst_labels:
            return
        r = src_labels.index(src_label)
        c = dst_labels.index(dst_label)
        item = self.item(r, c)
        if item:
            item.setText(text)

    def _set_cell(self, row: int, col: int, state: CellState) -> None:
        from PyQt6.QtGui import QColor
        color, text = _STATE_STYLE.get(state, ("#555", state.value))
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor(color))
        self.setItem(row, col, item)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class OffloadWorker(QObject):
    status_changed = pyqtSignal(str, str, object)   # src_label, dst_label, CellState
    log            = pyqtSignal(str, str)
    progress       = pyqtSignal(str, str, int, int)
    finished       = pyqtSignal(list, dict, str)    # results, manifests, log_path
    error          = pyqtSignal(str)

    def __init__(
        self,
        sources: list,
        dests: list,
        config: OffloadConfig,
    ):
        super().__init__()
        self._sources    = sources
        self._dests      = dests
        self._config     = config
        self._cancelled  = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            results, manifests, log_path = run_offload(
                self._sources,
                self._dests,
                self._config,
                status_cb=lambda s, d, state: self.status_changed.emit(s, d or "", state),
                log_cb=lambda m, l: self.log.emit(m, l),
                progress_cb=lambda s, d, n, t: self.progress.emit(s, d, n, t),
                cancelled_cb=lambda: self._cancelled,
            )
            self.finished.emit(results, manifests, str(log_path))
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Summary dialog
# ---------------------------------------------------------------------------

class SummaryDialog(QDialog):
    def __init__(self, results: list, log_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Offload Complete — Summary")
        self.setMinimumWidth(640)
        self._build_ui(results, log_path)

    def _build_ui(self, results: list, log_path: str):
        from PyQt6.QtGui import QColor
        layout = QVBoxLayout(self)

        total   = len(results)
        done    = sum(1 for r in results if r.state == CellState.DONE)
        failed  = sum(1 for r in results if r.state == CellState.FAILED)
        skipped = sum(1 for r in results if r.state == CellState.SKIPPED)

        summary_lbl = QLabel(
            f"<b>{done}/{total}</b> transfers complete"
            + (f" &nbsp;|&nbsp; <span style='color:{theme.ACCENT_CORAL}'>{failed} failed</span>" if failed else "")
            + (f" &nbsp;|&nbsp; {skipped} skipped" if skipped else "")
        )
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("font-size:14px;margin-bottom:8px;")
        layout.addWidget(summary_lbl)

        # Per-source eject indicators
        src_labels = list(dict.fromkeys(r.source_label for r in results))
        for src_label in src_labels:
            src_results = [r for r in results if r.source_label == src_label]
            all_done    = all(r.state in (CellState.DONE, CellState.SKIPPED) for r in src_results)
            any_failed  = any(r.state == CellState.FAILED for r in src_results)
            if all_done and not any_failed:
                color = theme.VERDICT_GREEN
                msg   = f"✓ {src_label} — Safe to eject"
            elif all_done:
                color = theme.VERDICT_CORAL
                msg   = f"⚠ {src_label} — Errors on some destinations — review before ejecting"
            else:
                color = theme.VERDICT_MUTED
                msg   = f"• {src_label} — Not all destinations completed"
            eject_lbl = QLabel(msg)
            eject_lbl.setStyleSheet(f"color:{color};font-weight:bold;font-size:13px;")
            layout.addWidget(eject_lbl)

            # M10.1: safe-to-format clearance (verification-based, stricter than
            # eject). Logic lives in core.clearance; this only renders it.
            from core.clearance import compute_clearance
            verdict = compute_clearance(src_label, results)
            clr_color = theme.VERDICT_GREEN if verdict.cleared else theme.VERDICT_GOLD
            clr_icon  = "✓" if verdict.cleared else "⚠"
            clr_lbl = QLabel(f"{clr_icon} {verdict.to_text()}")
            clr_lbl.setWordWrap(True)
            clr_lbl.setStyleSheet(f"color:{clr_color};font-size:12px;margin-left:14px;")
            layout.addWidget(clr_lbl)

        layout.addSpacing(8)

        # Result table
        table = QTableWidget(len(results), 4)
        table.setHorizontalHeaderLabels(["Source", "Destination", "Status", "Files"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for row, r in enumerate(results):
            color, text = _STATE_STYLE.get(r.state, ("#ccc", r.state.value))
            for col, val in enumerate([r.source_label, r.dest_label, text, str(r.files_copied)]):
                item = QTableWidgetItem(val)
                if col == 2:
                    item.setForeground(__import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(color))
                table.setItem(row, col, item)
        layout.addWidget(table)

        log_lbl = QLabel(f"Chain-of-custody log: <code>{log_path}</code>")
        log_lbl.setTextFormat(Qt.TextFormat.RichText)
        log_lbl.setStyleSheet(f"color:{theme.MUTED_TEXT};font-size:11px;margin-top:6px;")
        layout.addWidget(log_lbl)

        for r in results:
            if r.thumbnail_result and r.thumbnail_result.get("contact_sheet_path"):
                cs_path = r.thumbnail_result["contact_sheet_path"]
                cs_lbl = QLabel(f"Contact sheet ({r.source_label}): <code>{cs_path}</code>")
                cs_lbl.setTextFormat(Qt.TextFormat.RichText)
                cs_lbl.setStyleSheet(f"color:{theme.MUTED_TEXT};font-size:11px;")
                layout.addWidget(cs_lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ---------------------------------------------------------------------------
# Filename normalisation prompt (Phase 7, item 55)
# ---------------------------------------------------------------------------

class NormalisationPromptDialog(QDialog):
    """
    Pre-offload dialog asking whether to normalise sequential camera filenames.

    Shows the detected pattern, a concrete example transformation, and (when
    applicable) a cross-source collision warning.  Exposes .normalize (bool)
    and .remember (bool) after exec().
    """

    def __init__(
        self,
        pattern_name: str,
        example_files: list,
        cross_source_dupes: set,
        forced: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.normalize = False
        self.remember  = False
        self.setWindowTitle("Filename Normalisation")
        self.setMinimumWidth(520)
        self._pattern_name = pattern_name
        self._build_ui(pattern_name, example_files, cross_source_dupes, forced)

    def _build_ui(self, pattern_name, example_files, cross_source_dupes, forced):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        if forced:
            header = QLabel("<b>Cross-source filename collision detected</b>")
            header.setStyleSheet(f"color:{theme.ACCENT_CORAL};font-size:14px;")
        else:
            header = QLabel(f"<b>Sequential camera naming detected: {pattern_name}</b>")
            header.setStyleSheet(f"color:{theme.ACCENT_GOLD};font-size:14px;")
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        if forced:
            body = (
                "Files from different sources share identical names. Normalisation appends "
                "a unique 8-character hash suffix derived from each file's content, making "
                "every filename unambiguous in your NLE."
            )
        else:
            body = (
                f"Generic sequential filenames such as <code>{pattern_name.replace('X', '1')}"
                f"</code> can cause false relinking in Premiere and DaVinci Resolve when "
                f"footage from different shoots is combined. Normalisation appends a unique "
                f"8-character content hash to each filename."
            )
        desc = QLabel(body)
        desc.setTextFormat(Qt.TextFormat.RichText)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        if cross_source_dupes:
            sample = sorted(cross_source_dupes)[:5]
            extra  = " …" if len(cross_source_dupes) > 5 else ""
            dupe_lbl = QLabel(
                "Shared filenames: <code>" + ", ".join(sample) + extra + "</code>"
            )
            dupe_lbl.setTextFormat(Qt.TextFormat.RichText)
            dupe_lbl.setStyleSheet(f"color:{theme.ACCENT_CORAL};font-size:11px;")
            dupe_lbl.setWordWrap(True)
            layout.addWidget(dupe_lbl)

        if example_files:
            orig = example_files[0]
            p    = Path(orig)
            ex_lbl = QLabel(
                f"Example: <code>{orig}</code> "
                f"&nbsp;→&nbsp; <code>{p.stem}_????????{p.suffix}</code>"
            )
            ex_lbl.setTextFormat(Qt.TextFormat.RichText)
            ex_lbl.setStyleSheet(f"color:{theme.MUTED_TEXT};font-size:12px;")
            layout.addWidget(ex_lbl)

        note = QLabel(
            "The rename is applied at the destination only — your source card is never modified."
        )
        note.setStyleSheet(f"color:{theme.MUTED_TEXT};font-size:11px;font-style:italic;")
        layout.addWidget(note)

        layout.addSpacing(4)

        self._remember_check = QCheckBox(
            f"Remember this choice for {pattern_name} files"
        )
        layout.addWidget(self._remember_check)

        buttons = QDialogButtonBox()
        norm_btn = buttons.addButton(
            "Normalize (Recommended)", QDialogButtonBox.ButtonRole.AcceptRole
        )
        skip_label = "Skip (not recommended)" if forced else "Skip"
        skip_btn = buttons.addButton(skip_label, QDialogButtonBox.ButtonRole.RejectRole)
        norm_btn.clicked.connect(self._on_normalize)
        skip_btn.clicked.connect(self._on_skip)
        layout.addWidget(buttons)

    def _on_normalize(self):
        self.normalize = True
        self.remember  = self._remember_check.isChecked()
        self.accept()

    def _on_skip(self):
        self.normalize = False
        self.remember  = self._remember_check.isChecked()
        self.reject()


# ---------------------------------------------------------------------------
# Volume-detection banner
# ---------------------------------------------------------------------------

class VolumeBanner(QFrame):
    """
    Non-modal banner displayed when a qualifying media-card volume is detected.
    Emits accepted() when the user clicks [Add] and dismissed() for [Dismiss].
    """

    accepted  = pyqtSignal(dict)   # payload: the volume info dict
    dismissed = pyqtSignal(str)    # payload: mount_path

    def __init__(self, volume_info: dict, parent=None):
        super().__init__(parent)
        self._info = volume_info
        self._build_ui()

    def _build_ui(self):
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"VolumeBanner {{ background:{theme.CHARCOAL_LIGHT};"
            f" border:1px solid {theme.ACCENT_GOLD}; border-radius:6px; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        icon = QLabel("💾")
        icon.setStyleSheet("font-size:18px;")
        layout.addWidget(icon)

        info = self._info
        text = (
            f"<b>New volume '{info['volume_name']}' detected</b> "
            f"({info['total_size_str']}, {info['filesystem']}, "
            f"contains <code>{info['marker']}</code>) — Add as source?"
        )
        lbl = QLabel(text)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(f"color:{theme.TEXT_PRIMARY};font-size:12px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl, stretch=1)

        add_btn = QPushButton("Add")
        add_btn.setStyleSheet(theme.primary_button_style())
        add_btn.setFixedWidth(70)
        add_btn.clicked.connect(lambda: self.accepted.emit(self._info))
        layout.addWidget(add_btn)

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setFixedWidth(70)
        dismiss_btn.clicked.connect(lambda: self.dismissed.emit(self._info["mount_path"]))
        layout.addWidget(dismiss_btn)


# ---------------------------------------------------------------------------
# Main tab
# ---------------------------------------------------------------------------

class OffloadTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_rows: list[SourceRowWidget] = []
        self._dest_rows:   list[DestRowWidget]   = []
        self._thread: Optional[QThread] = None
        self._worker: Optional[OffloadWorker] = None
        # (src_label, dst_label) -> monotonic start time for the COPYING phase
        self._copy_start: dict[tuple, float] = {}
        # mount_path -> VolumeBanner, for volumes currently showing a banner
        self._banners: dict[str, VolumeBanner] = {}
        # mount_paths the user dismissed this session (cleared on unmount)
        self._dismissed: set[str] = set()
        # mount_paths already offered (banner shown or accepted) this session
        self._offered: set[str] = set()
        self._build_ui()
        self._start_volume_watcher()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(theme.tab_stylesheet(theme.tab_accent("Offload")))
        # Scroll the whole tab so a short window scrolls instead of squishing the
        # source/dest panels, matrix and log; a tall window lets them expand.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setSpacing(13)
        root.setContentsMargins(20, 16, 20, 12)

        # Banner container — banners are inserted here dynamically
        self._banner_container = QWidget()
        self._banner_layout = QVBoxLayout(self._banner_container)
        self._banner_layout.setContentsMargins(0, 0, 0, 0)
        self._banner_layout.setSpacing(4)
        self._banner_container.setVisible(False)
        root.addWidget(self._banner_container)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_source_panel())
        splitter.addWidget(self._build_dest_panel())
        splitter.setSizes([520, 520])
        root.addWidget(splitter)

        root.addWidget(self._build_options_bar())
        root.addWidget(self._build_matrix_group())
        root.addWidget(self._build_log_and_actions())

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _build_source_panel(self) -> QGroupBox:
        group = QGroupBox("SOURCES (READ-ONLY)")
        layout = QVBoxLayout(group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(160)
        scroll.setMaximumHeight(280)
        self._sources_container = QWidget()
        self._sources_layout = QVBoxLayout(self._sources_container)
        self._sources_layout.setSpacing(2)
        self._sources_layout.setContentsMargins(0, 0, 0, 0)
        self._sources_layout.addStretch()
        scroll.setWidget(self._sources_container)
        layout.addWidget(scroll)

        add_btn = QPushButton("+ Add Source")
        add_btn.clicked.connect(self._add_source)
        layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self._add_source()
        return group

    def _build_dest_panel(self) -> QGroupBox:
        group = QGroupBox("DESTINATIONS")
        layout = QVBoxLayout(group)

        # Preset bar (Load / Save — Delete removed, Save overwrites)
        preset_bar = QHBoxLayout()
        preset_bar.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(160)
        preset_bar.addWidget(self._preset_combo)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load_preset)
        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self._save_preset)
        preset_bar.addWidget(load_btn)
        preset_bar.addWidget(save_btn)
        preset_bar.addStretch()
        layout.addLayout(preset_bar)
        self._refresh_preset_combo()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(160)
        scroll.setMaximumHeight(280)
        self._dests_container = QWidget()
        self._dests_layout = QVBoxLayout(self._dests_container)
        self._dests_layout.setSpacing(2)
        self._dests_layout.setContentsMargins(0, 0, 0, 0)
        self._dests_layout.addStretch()
        scroll.setWidget(self._dests_container)
        layout.addWidget(scroll)

        add_btn = QPushButton("+ Add Destination")
        add_btn.clicked.connect(self._add_dest)
        layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self._add_dest()
        return group

    def _build_options_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("OptionsBar")
        bar.setStyleSheet(
            f"QWidget#OptionsBar {{ background:{theme.CHARCOAL_LIGHT}; border:1px solid {theme.BORDER}; border-radius:6px; }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(16)

        # 1. Contact sheets + max frames
        _thumb_ok = ffmpeg_available() and pillow_available()
        self._thumb_check = QCheckBox("Contact sheets")
        if _thumb_ok:
            self._thumb_check.setToolTip(
                "After the primary destination commits, extract thumbnails and build a PDF "
                "contact sheet from destination files."
            )
        else:
            missing = []
            if not ffmpeg_available():
                missing.append("ffmpeg (brew install ffmpeg)")
            if not pillow_available():
                missing.append("Pillow (pip install Pillow)")
            self._thumb_check.setEnabled(False)
            self._thumb_check.setToolTip("Requires: " + ", ".join(missing))
        layout.addWidget(self._thumb_check)

        max_lbl = QLabel("max")
        max_lbl.setStyleSheet(f"color:{theme.MUTED_TEXT}; font-size:11px;")
        layout.addWidget(max_lbl)
        self._max_frames_spin = QSpinBox()
        self._max_frames_spin.setRange(1, 4)
        self._max_frames_spin.setValue(4)
        self._max_frames_spin.setFixedWidth(50)
        self._max_frames_spin.setToolTip("Maximum thumbnail frames per clip (adaptive: short clips use fewer)")
        layout.addWidget(self._max_frames_spin)

        # Divider
        div1 = QFrame()
        div1.setFrameShape(QFrame.Shape.VLine)
        div1.setFixedWidth(1)
        div1.setStyleSheet(f"background:{theme.BORDER}; border:none;")
        layout.addWidget(div1)

        # 2. Stop on fail
        self._stop_on_fail = QCheckBox("Stop on first destination failure")
        layout.addWidget(self._stop_on_fail)

        # M10.3: optional ASC MHL v2.0 sidecar for post-house interoperability.
        self._export_mhl_chk = QCheckBox("Export ASC MHL (.mhl)")
        self._export_mhl_chk.setToolTip(
            "Write an ASC Media Hash List sidecar next to each manifest, "
            "for verification in Silverstack, YoYotta and similar tools")
        layout.addWidget(self._export_mhl_chk)

        # Divider
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.VLine)
        div2.setFixedWidth(1)
        div2.setStyleSheet(f"background:{theme.BORDER}; border:none;")
        layout.addWidget(div2)

        # 3. Retries (advanced)
        retries_lbl = QLabel("Retries per file")
        retries_lbl.setStyleSheet(f"color:{theme.MUTED_TEXT}; font-size:12px;")
        layout.addWidget(retries_lbl)
        self._retries_spin = QSpinBox()
        self._retries_spin.setRange(1, 10)
        self._retries_spin.setValue(3)
        self._retries_spin.setFixedWidth(60)
        layout.addWidget(self._retries_spin)

        layout.addStretch()

        # Auto-detect (kept but moved to end)
        self._autodetect_check = QCheckBox("Auto-detect media cards")
        self._autodetect_check.setToolTip(
            "Show a banner when a removable media card (DCIM, CLIP, etc.) is mounted. "
            "Detection only — never starts a copy automatically."
        )
        self._autodetect_check.setChecked(
            bool(projects.get_app_setting("volume_autodetect", True))
        )
        self._autodetect_check.stateChanged.connect(self._on_autodetect_toggled)
        layout.addWidget(self._autodetect_check)

        return bar

    def _build_matrix_group(self) -> QGroupBox:
        group = QGroupBox("TRANSFER STATUS")
        layout = QVBoxLayout(group)
        self._matrix = StatusMatrixWidget()
        self._matrix.setMinimumHeight(90)
        self._matrix.setMaximumHeight(200)
        layout.addWidget(self._matrix)
        return group

    def _build_log_and_actions(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Action row above log panel
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("▶  Start Offload")
        self._start_btn.setObjectName("primaryBtn")
        self._start_btn.setMinimumHeight(40)
        self._start_btn.clicked.connect(self._start_offload)
        btn_row.addWidget(self._start_btn)

        self._cancel_btn = QPushButton("✕  Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setMinimumHeight(36)
        self._cancel_btn.clicked.connect(self._cancel_offload)
        btn_row.addWidget(self._cancel_btn)

        self._offload_status_lbl = QLabel("Ready")
        self._offload_status_lbl.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:12px;")
        btn_row.addStretch()
        btn_row.addWidget(self._offload_status_lbl)
        layout.addLayout(btn_row)

        self._log = LogWidget(
            "Offload log",
            placeholder="Add sources and destinations, then Start Offload — "
                        "per-card progress and clearance will appear here.")
        self._log.setMinimumHeight(160)
        layout.addWidget(self._log)
        return container

    # ── Volume auto-detection ──────────────────────────────────────────────

    def _start_volume_watcher(self):
        self._watcher = VolumeWatcher(self)
        self._watcher.volume_mounted.connect(self._on_volume_mounted)
        self._watcher.volume_unmounted.connect(self._on_volume_unmounted)
        if not self._watcher.available:
            self._autodetect_check.setEnabled(False)
            self._autodetect_check.setToolTip(
                "Volume auto-detection requires pyobjc (pip install pyobjc-framework-AppKit)."
            )

    def _on_autodetect_toggled(self, state: int):
        projects.save_app_setting("volume_autodetect", bool(state))

    def showEvent(self, event):
        super().showEvent(event)
        if self._autodetect_check.isChecked():
            for info in self._watcher.scan_existing():
                self._on_volume_mounted(info)

    def _on_volume_mounted(self, info: dict):
        if not self._autodetect_check.isChecked():
            return
        path = info["mount_path"]
        if path in self._dismissed or path in self._banners or path in self._offered:
            return
        banner = VolumeBanner(info, self)
        banner.accepted.connect(self._on_banner_accepted)
        banner.dismissed.connect(self._on_banner_dismissed)
        self._banners[path] = banner
        self._offered.add(path)
        self._banner_layout.addWidget(banner)
        self._banner_container.setVisible(True)

    def _on_volume_unmounted(self, mount_path: str):
        # Withdraw the banner (if any) for this volume
        banner = self._banners.pop(mount_path, None)
        if banner is not None:
            self._banner_layout.removeWidget(banner)
            banner.deleteLater()
            if not self._banners:
                self._banner_container.setVisible(False)
        # Clear session records so a remount shows the banner again
        self._dismissed.discard(mount_path)
        self._offered.discard(mount_path)

    def _on_banner_accepted(self, info: dict):
        path = info["mount_path"]
        self._dismiss_banner(path)
        # Populate a new source row (label + path pre-filled, enabled)
        row = SourceRowWidget(len(self._source_rows) + 1, self)
        row.removed.connect(self._remove_source)
        row._label.setText(info["label"])
        row._path.setText(info["mount_path"])
        self._source_rows.append(row)
        self._sources_layout.insertWidget(self._sources_layout.count() - 1, row)

    def _on_banner_dismissed(self, mount_path: str):
        self._dismissed.add(mount_path)
        self._dismiss_banner(mount_path)

    def _dismiss_banner(self, mount_path: str):
        banner = self._banners.pop(mount_path, None)
        if banner is not None:
            self._banner_layout.removeWidget(banner)
            banner.deleteLater()
            if not self._banners:
                self._banner_container.setVisible(False)

    # ── Source / dest row management ───────────────────────────────────────

    def _add_source(self):
        row = SourceRowWidget(len(self._source_rows) + 1, self)
        row.removed.connect(self._remove_source)
        self._source_rows.append(row)
        # Insert before the trailing stretch
        self._sources_layout.insertWidget(self._sources_layout.count() - 1, row)

    def _remove_source(self, row: SourceRowWidget):
        if len(self._source_rows) <= 1:
            return
        self._source_rows.remove(row)
        self._sources_layout.removeWidget(row)
        row.deleteLater()

    def _add_dest(self):
        row = DestRowWidget(len(self._dest_rows) + 1, self)
        row.removed.connect(self._remove_dest)
        self._dest_rows.append(row)
        self._dests_layout.insertWidget(self._dests_layout.count() - 1, row)

    def _remove_dest(self, row: DestRowWidget):
        if len(self._dest_rows) <= 1:
            return
        self._dest_rows.remove(row)
        self._dests_layout.removeWidget(row)
        row.deleteLater()

    # ── Preset management ──────────────────────────────────────────────────

    def _refresh_preset_combo(self):
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("(select preset)")
        for name in projects.list_dest_presets():
            self._preset_combo.addItem(name)
        self._preset_combo.blockSignals(False)

    def _load_preset(self):
        name = self._preset_combo.currentText()
        if name == "(select preset)":
            return
        dests = projects.get_dest_preset(name)
        if not dests:
            return
        # Remove all existing dest rows
        for row in list(self._dest_rows):
            self._dests_layout.removeWidget(row)
            row.deleteLater()
        self._dest_rows.clear()
        for d in dests:
            row = DestRowWidget(len(self._dest_rows) + 1, self)
            row.removed.connect(self._remove_dest)
            row.set_from_dict(d)
            self._dest_rows.append(row)
            self._dests_layout.insertWidget(self._dests_layout.count() - 1, row)
        if not self._dest_rows:
            self._add_dest()

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in projects.list_dest_presets():
            reply = QMessageBox.question(
                self, "Overwrite Preset",
                f"A preset named '{name}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        dests = [r.to_dict() for r in self._dest_rows]
        projects.save_dest_preset(name, dests)
        self._refresh_preset_combo()
        idx = self._preset_combo.findText(name)
        if idx >= 0:
            self._preset_combo.setCurrentIndex(idx)

    # ── Offload execution ──────────────────────────────────────────────────

    def _collect_inputs(self) -> tuple[list, list]:
        sources = [r.to_offload_source() for r in self._source_rows]
        dests   = [r.to_offload_dest()   for r in self._dest_rows]
        sources = [s for s in sources if s is not None]
        dests   = [d for d in dests   if d is not None]
        return sources, dests

    def _ask_resume(self, active_src, active_dst):
        """M4.1: if any source/dest pair has interrupted staging, offer Resume.

        Returns True (resume), False (start fresh, stale staging discarded)
        or None (user cancelled). Detection logic lives in core/offload.py.
        """
        from core.offload import find_resumable_staging, discard_stale_staging

        found = []
        for s in active_src:
            for d in active_dst:
                hit = find_resumable_staging(s, d)
                if hit:
                    found.append((s, d, hit[0], hit[1]))
        if not found:
            return False

        detail = "\n".join(
            f"  • {s.label} → {d.label}: {len(state.get('completed', []))} file(s) already staged"
            for s, d, _, state in found
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Resume available")
        box.setText(
            "An earlier offload was interrupted. Files already copied can be "
            "re-verified and reused instead of recopying the whole card:\n\n"
            f"{detail}\n\n"
            "Resume reuses only files that re-verify against the original "
            "source hashes. Start Fresh discards the partial copy."
        )
        resume_btn = box.addButton("Resume", QMessageBox.ButtonRole.AcceptRole)
        fresh_btn = box.addButton("Start Fresh", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is resume_btn:
            return True
        if clicked is fresh_btn:
            for _, _, staging, _ in found:
                discard_stale_staging(staging)
            return False
        return None

    def _start_offload(self):
        sources, dests = self._collect_inputs()

        if not sources:
            QMessageBox.warning(self, "Offload", "Add at least one source with a label and path.")
            return
        if not dests:
            QMessageBox.warning(self, "Offload", "Add at least one destination with a label and path.")
            return

        active_src = [s for s in sources if s.enabled]
        active_dst = [d for d in dests   if d.enabled]
        if not active_src:
            QMessageBox.warning(self, "Offload", "Enable at least one source.")
            return
        if not active_dst:
            QMessageBox.warning(self, "Offload", "Enable at least one destination.")
            return

        # KNOWN-ISSUE-FIX: warn before starting if two sources resolve to the
        # same destination subfolder (Phase 5 #24). Their files would merge into
        # one directory at every destination. Let the operator confirm or cancel.
        collisions = detect_subfolder_collisions(active_src)
        if collisions:
            detail = "\n".join(
                f"  • '{folder}' ← {', '.join(labels)}"
                for folder, labels in sorted(collisions.items())
            )
            resp = QMessageBox.warning(
                self,
                "Subfolder collision",
                "Two or more sources resolve to the same destination "
                "subfolder, so their files will be merged into one directory "
                "at every destination:\n\n"
                f"{detail}\n\n"
                "Give each source a distinct subfolder name to keep them "
                "separate. Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        # Build matrix
        src_labels = [s.label for s in active_src]
        dst_labels = [d.label for d in active_dst]
        self._matrix.configure(src_labels, dst_labels)

        normalize = self._check_normalisation(active_src)

        # M4.1: never silently reuse an interrupted offload's staging — ask.
        resume_staging = self._ask_resume(active_src, active_dst)
        if resume_staging is None:
            return  # user cancelled

        config = OffloadConfig(
            max_retries=self._retries_spin.value(),
            stop_on_first_failure=self._stop_on_fail.isChecked(),
            generate_thumbnails=self._thumb_check.isChecked() and self._thumb_check.isEnabled(),
            thumbnail_max_frames=self._max_frames_spin.value(),
            normalize_filenames=normalize,
            resume_staging=resume_staging,
            export_mhl=self._export_mhl_chk.isChecked(),
        )

        self._copy_start.clear()
        self._worker = OffloadWorker(sources, dests, config)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.status_changed.connect(self._on_status_changed)
        self._worker.log.connect(self._log.log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_done)

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._offload_status_lbl.setText("Running…")
        self._log.clear()
        self._log.log("Starting offload…", "info")
        self._thread.start()

    def _check_normalisation(self, active_sources: list) -> bool:
        """
        Item 55. Scan source directories for sequential naming patterns and
        cross-source duplicates, then prompt the user if action is warranted.

        Returns True if normalisation should be applied for this offload run.
        """
        # Build lightweight filename-only manifests (hashes not yet known)
        lightweight: dict = {}
        for src in active_sources:
            if not Path(src.path).is_dir():
                continue
            lightweight[src.label] = {
                str(f.relative_to(src.path)): {"size": 0, "checksum": "", "algorithm": "sha256"}
                for f in Path(src.path).rglob("*") if f.is_file()
            }

        if not lightweight:
            return False

        # Cross-source duplicate check (item 54 trigger — unconditional prompt)
        dupes  = detect_cross_source_duplicates(lightweight)
        forced = bool(dupes)

        # Per-source sequential pattern detection (item 53)
        best_scan: dict = {}
        for mfst in lightweight.values():
            scan = scan_naming_patterns(mfst)
            if scan["detected"] or (forced and scan.get("pattern_name")):
                if scan.get("match_ratio", 0) > best_scan.get("match_ratio", 0):
                    best_scan = scan

        if not best_scan and not forced:
            return False

        pattern_name  = best_scan.get("pattern_name", "sequential")
        example_files = best_scan.get("example_files", [])

        # Check stored preference (item 56) — skip preference check when forced
        if not forced:
            stored = projects.get_naming_preference(pattern_name)
            if stored == "normalize":
                return True
            if stored == "skip":
                return False
            # stored == "ask" or None → fall through to dialog

        dlg = NormalisationPromptDialog(
            pattern_name=pattern_name,
            example_files=example_files,
            cross_source_dupes=dupes,
            forced=forced,
            parent=self,
        )
        dlg.exec()

        if dlg.remember and pattern_name != "sequential":
            projects.save_naming_preference(
                pattern_name,
                "normalize" if dlg.normalize else "skip",
            )

        return dlg.normalize

    def _cancel_offload(self):
        if self._worker:
            self._worker.cancel()
            self._log.log("Cancellation requested…", "warning")
        self._cancel_btn.setEnabled(False)

    def _on_status_changed(self, src_label: str, dst_label: str, state: CellState):
        if dst_label:
            self._matrix.update_cell(src_label, dst_label, state)
            if state == CellState.COPYING:
                import time as _time
                self._copy_start[(src_label, dst_label)] = _time.monotonic()

    def _on_progress(self, src_label: str, dst_label: str, bytes_done: int, bytes_total: int):
        import time as _time
        pct = int(bytes_done / bytes_total * 100) if bytes_total else 0

        def _fmt(n: int) -> str:
            if n >= 1 << 30:
                return f"{n / (1 << 30):.1f} GB"
            if n >= 1 << 20:
                return f"{n / (1 << 20):.0f} MB"
            return f"{n / (1 << 10):.0f} KB"

        elapsed = _time.monotonic() - self._copy_start.get((src_label, dst_label), _time.monotonic())
        rate = bytes_done / elapsed if elapsed > 0.5 else 0
        if rate > 0 and bytes_total > bytes_done:
            secs_left = (bytes_total - bytes_done) / rate
            if secs_left >= 60:
                eta = f"{int(secs_left // 60)}m {int(secs_left % 60)}s"
            else:
                eta = f"{int(secs_left)}s"
            eta_str = f" ~{eta}"
        else:
            eta_str = ""

        cell_text = f"{pct}% ({_fmt(bytes_done)}/{_fmt(bytes_total)}{eta_str})"
        self._matrix.update_cell_progress(src_label, dst_label, cell_text)

    def _on_finished(self, results: list, manifests: dict, log_path: str):
        done   = sum(1 for r in results if r.state == CellState.DONE)
        failed = sum(1 for r in results if r.state == CellState.FAILED)
        sheets = [
            r.thumbnail_result["contact_sheet_path"]
            for r in results
            if r.thumbnail_result and r.thumbnail_result.get("contact_sheet_path")
        ]
        self._log.log(
            f"Offload complete — {done} succeeded, {failed} failed. Log: {log_path}",
            "success" if not failed else "warning",
        )
        for sp in sheets:
            self._log.log(f"Contact sheet: {sp}", "success")
        dlg = SummaryDialog(results, log_path, self)
        dlg.exec()

    def _on_error(self, msg: str):
        self._log.log(f"Offload engine error: {msg}", "error")
        QMessageBox.critical(self, "Offload Error", msg)

    def _on_thread_done(self):
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._offload_status_lbl.setText("Ready")
        self._thread = None
        self._worker = None

    def load_demo_data(self):
        """
        Pre-fill the first source and destination rows with the demo folder
        paths for the onboarding tutorial. Skips rows that already have content.
        """
        from core.demo import ensure_demo_folder

        try:
            src, dst = ensure_demo_folder()
        except Exception:
            return

        if self._source_rows:
            row = self._source_rows[0]
            if not row._path.text().strip():
                row._path.setText(str(src))
                row._label.setText("CAM_A")

        if self._dest_rows:
            row = self._dest_rows[0]
            if not row._path.text().strip():
                row._path.setText(str(dst))
                row._label.setText("RAID_1")
