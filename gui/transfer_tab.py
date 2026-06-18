from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QCheckBox, QComboBox,
    QMessageBox, QSizePolicy, QFrame, QScrollArea, QLineEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont
from pathlib import Path

from dataclasses import dataclass

from gui.ui_helpers import make_interactive, awake_indicator, reveal_in_finder
from gui.completion_banner import CompletionBanner
from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
from gui.toast import show_toast
from gui.widgets.queue_panel import QueuePanel


@dataclass
class TransferJobSpec:
    src: str
    dst: str
    gdrive_mode: bool
    mirror_mode: bool
    paranoid_mode: bool
    conflict_handler: str
    extract_zips: bool
    export_mhl: bool
    job_name: str
from utils.file_utils import folder_size, free_space, format_bytes
from utils.gdrive_utils import is_gdrive_url
from core.transfer import (
    pre_flight_checks, route_transfer, extract_multipart_zip,
    TransferError, TransferWarning, estimate_time_seconds,
    GDRIVE_DAILY_LIMIT_BYTES,
)
from core.amphetamine import check_and_prompt, start_session, end_session
from core.demo import ensure_demo_folder
from gui import theme
from core import rclone_bridge
from core import projects as project_registry
from core.manifest import _project_id as _make_project_id


class TransferWorker(QObject):
    # progress carries (pct: int, info: object) where info is either a plain str
    # (local transfers) or a dict with keys: line, speed, eta, files_done,
    # files_total, current_file (rclone transfers).
    progress = pyqtSignal(int, object)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, src, dst, gdrive_mode, mirror_mode, paranoid_mode, conflict_handler,
                 extract_zips, export_mhl=False, job_name=""):
        super().__init__()
        self.src              = src
        self.dst              = dst
        self.gdrive_mode      = gdrive_mode
        self.mirror_mode      = mirror_mode
        self.paranoid_mode    = paranoid_mode
        self.conflict_handler = conflict_handler
        self.extract_zips     = extract_zips
        self.export_mhl       = export_mhl
        self.job_name         = job_name

    def run(self):
        try:
            result = route_transfer(
                self.src, self.dst,
                gdrive_mode=self.gdrive_mode,
                mirror_mode=self.mirror_mode,
                log_cb=lambda m, l: self.log.emit(m, l),
                progress_cb=lambda p, f: self.progress.emit(p, f),
                conflict_handler=self.conflict_handler,
                export_mhl=self.export_mhl,
                job_name=self.job_name,
            )
            if self.extract_zips and not is_gdrive_url(str(self.dst)):
                extract_multipart_zip(
                    self.dst,
                    log_cb=lambda m, l: self.log.emit(m, l),
                )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class TransferTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None
        self._cancelled = False
        self._last_dest = ""
        self._job_queue: list[TransferJobSpec] = []
        self._queue_counter = 0
        self._current_queue_index: int = -1
        self._build_ui()
        self._install_shortcuts()

    def _install_shortcuts(self):
        from PyQt6.QtGui import QShortcut, QKeySequence
        # Esc cancels a running transfer (no-op when idle); ⌘O browses for source.
        QShortcut(QKeySequence("Esc"), self, activated=self._cancel_transfer)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.src_input.browse_btn.click)

    def _build_ui(self):
        # GUI refresh: per-tab accent (blue for Transfer) tints section headers,
        # checkboxes and the primary button via one cascading stylesheet.
        self.setStyleSheet(theme.tab_stylesheet(theme.tab_accent("Transfer")))
        # Scroll the whole tab so a short window scrolls instead of squishing the
        # options + log; a tall window lets the log expand.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setSpacing(14)
        root.setContentsMargins(20, 16, 20, 12)

        # Persistent completion banner (the bottom toast fades too fast).
        self._banner = CompletionBanner()
        root.addWidget(self._banner)

        # ── Source & Destination ─────────────────────────────────────────────
        io_group = QGroupBox("SOURCE && DESTINATION")
        io_layout = QVBoxLayout(io_group)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source:     "))
        self.src_input = PathInputWidget("source", self)
        self.src_input.pathChanged.connect(self._update_preflight)
        self.src_input.pathChanged.connect(self._on_paths_changed)
        src_row.addWidget(self.src_input)
        io_layout.addLayout(src_row)

        dst_row = QHBoxLayout()
        dst_row.addWidget(QLabel("Destination:"))
        self.dst_input = PathInputWidget("destination", self)
        self.dst_input.pathChanged.connect(self._update_preflight)
        self.dst_input.pathChanged.connect(self._on_paths_changed)
        dst_row.addWidget(self.dst_input)
        io_layout.addLayout(dst_row)

        # Demo shortcut — right-aligned link below the path fields
        demo_row = QHBoxLayout()
        demo_row.setContentsMargins(0, 0, 0, 0)
        self._demo_link = QLabel(
            '<a href="demo" style="color:#555; font-size:11px; text-decoration:none;">'
            '⊙ Use demo folder</a>'
        )
        self._demo_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self._demo_link.linkActivated.connect(self._load_demo_folder)
        self._demo_link.setToolTip(
            "Pre-fills source and destination with a sample camera card structure\n"
            "so you can try the tool without using real files."
        )
        demo_row.addStretch()
        demo_row.addWidget(self._demo_link)
        io_layout.addLayout(demo_row)

        root.addWidget(io_group)

        # ── Job name / number ────────────────────────────────────────────────
        _job_row = QHBoxLayout()
        _job_row.addWidget(QLabel("Job Name / Number:"))
        self.job_name_input = QLineEdit()
        self.job_name_input.setPlaceholderText("e.g. 61060 Michelin Interviews  (optional — appears in History)")
        self.job_name_input.setToolTip(
            "Stored in the activity log so the History tab shows a readable job "
            "name instead of a folder name."
        )
        _job_row.addWidget(self.job_name_input)
        root.addLayout(_job_row)

        # ── Pre-flight summary row ───────────────────────────────────────────
        pf_frame = QFrame()
        pf_frame.setObjectName("PfFrame")
        pf_frame.setStyleSheet(
            "QFrame#PfFrame {"
            f"  background:{theme.CHARCOAL_LIGHT}; border:1px solid {theme.BORDER};"
            "  border-radius:6px;"
            "}"
        )
        pf_layout = QHBoxLayout(pf_frame)
        pf_layout.setContentsMargins(12, 8, 12, 8)
        pf_layout.setSpacing(0)

        for attr, label_text in [
            ("_pf_src_val",  "Source size"),
            ("_pf_dst_val",  "Free space"),
            ("_pf_time_val", "Est. time"),
        ]:
            item = QWidget()
            item.setStyleSheet("background:transparent;")
            il = QVBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"font-size:11px; color:{theme.MUTED_TEXT}; background:transparent;")
            val = QLabel("—")
            val.setStyleSheet(f"font-size:13px; font-weight:500; color:#555; background:transparent;")
            il.addWidget(lbl)
            il.addWidget(val)
            setattr(self, attr, val)
            pf_layout.addWidget(item)
            pf_layout.addSpacing(32)

        pf_layout.addStretch()
        self._pf_hint = QLabel("Enter paths to see transfer summary")
        self._pf_hint.setStyleSheet(f"font-size:12px; color:#555; background:transparent;")
        pf_layout.addWidget(self._pf_hint)

        root.addWidget(pf_frame)

        # ── Options ──────────────────────────────────────────────────────────
        opts_group = QGroupBox("OPTIONS")
        opts_layout = QVBoxLayout(opts_group)

        opts_row1 = QHBoxLayout()
        opts_row1.addWidget(QLabel("On conflict:"))
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItems(["Skip existing", "Overwrite", "Rename copy"])
        self.conflict_combo.setCurrentIndex(1)
        opts_row1.addWidget(self.conflict_combo)
        opts_row1.addSpacing(20)
        self.extract_zip_chk = QCheckBox("Auto-extract multipart .zips after transfer")
        make_interactive(
            self.extract_zip_chk,
            tooltip="After the transfer, automatically reassemble and unzip any "
                    "multipart .zip sets found at the destination.",
        )
        opts_row1.addWidget(self.extract_zip_chk)
        opts_row1.addSpacing(20)
        self.paranoid_chk = QCheckBox("Paranoid verification")
        self.paranoid_chk.setStyleSheet(f"color:{theme.TEXT_MUTED};")
        make_interactive(
            self.paranoid_chk,
            tooltip="Re-hash every file after copying and compare against the "
                    "source. Slower, but the strongest integrity guarantee.",
        )
        opts_row1.addWidget(self.paranoid_chk)

        # M10.3: optional ASC MHL v2.0 sidecar for post-house interoperability.
        self.export_mhl_chk = QCheckBox("Export ASC MHL (.mhl)")
        self.export_mhl_chk.setStyleSheet(f"color:{theme.TEXT_MUTED};")
        make_interactive(
            self.export_mhl_chk,
            tooltip="Write an ASC Media Hash List sidecar next to the manifest, "
                    "for verification in Silverstack, YoYotta and similar tools.",
        )
        opts_row1.addWidget(self.export_mhl_chk)
        opts_row1.addStretch()
        opts_layout.addLayout(opts_row1)

        danger = QFrame()
        danger.setObjectName("DangerZone")
        danger.setStyleSheet(
            f"QFrame#DangerZone {{"
            f"  background:transparent; border:1px solid {theme.CORAL}; border-radius:8px;"
            f"}}"
            f"QCheckBox {{ color:{theme.CORAL}; background:transparent; }}"
        )
        dl = QHBoxLayout(danger)
        dl.setContentsMargins(10, 6, 10, 6)
        dl.setSpacing(8)
        warn_icon = QLabel("⚠")
        warn_icon.setStyleSheet(f"color:{theme.CORAL}; font-size:13px; background:transparent;")
        dl.addWidget(warn_icon)
        self.mirror_chk = QCheckBox(
            "Mirror mode — deletes files at destination not present in source"
        )
        make_interactive(
            self.mirror_chk,
            tooltip="Make the destination an exact mirror of the source: files "
                    "at the destination that are not in the source are DELETED. "
                    "Destructive — use with care.",
        )
        dl.addWidget(self.mirror_chk)
        dl.addStretch()
        opts_layout.addWidget(danger)

        root.addWidget(opts_group)

        # ── Action row ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.start_btn = QPushButton("▶  Start Transfer")
        self.start_btn.setObjectName("primaryBtn")  # accent fill via tab stylesheet
        self.start_btn.setFixedHeight(40)
        make_interactive(
            self.start_btn,
            tooltip="Copy from source to destination, verifying every file and "
                    "writing a manifest when done.",
        )
        self.start_btn.clicked.connect(self._start_transfer)

        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setEnabled(False)
        make_interactive(
            self.cancel_btn,
            tooltip="Stop the transfer in progress. Files already copied and "
                    "verified are kept.",
        )
        self.cancel_btn.clicked.connect(self._cancel_transfer)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:12px;")

        self.manifest_btn = QPushButton("📋  Generate Manifest Only")
        self.manifest_btn.setFixedHeight(36)
        make_interactive(
            self.manifest_btn,
            tooltip="Hash the source and write a manifest without copying any "
                    "files — useful to fingerprint a folder in place.",
        )
        self.manifest_btn.setStyleSheet(
            "QPushButton {"
            f"  background:transparent; color:{theme.TEXT_MUTED};"
            f"  border:1px solid {theme.BORDER}; border-radius:4px; font-size:12px;"
            "}"
            "QPushButton:hover {"
            f"  color:{theme.TEXT_PRIMARY}; border-color:#555;"
            "}"
        )
        self.manifest_btn.clicked.connect(self._generate_manifest_only)

        self._awake_lbl = awake_indicator()   # M12.5 — shown only while transferring

        # Revealed on a successful transfer so the user can jump to the output.
        self._reveal_btn = QPushButton("📂  Reveal destination")
        self._reveal_btn.setFixedHeight(36)
        self._reveal_btn.setVisible(False)
        make_interactive(self._reveal_btn, tooltip="Open the destination folder in Finder.")
        self._reveal_btn.clicked.connect(self._reveal_destination)

        # Queue button — adds current settings as a pending job without starting.
        self._queue_btn = QPushButton("+ Queue")
        self._queue_btn.setFixedHeight(36)
        make_interactive(self._queue_btn, tooltip="Add these settings as a pending job in the queue.")
        self._queue_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.ACCENT_GOLD};"
            f"  border:1px solid {theme.ACCENT_GOLD}; border-radius:4px;"
            f"  padding:4px 10px; font-size:12px; }}"
            f"QPushButton:hover {{ background:#3a2a00; }}"
        )
        self._queue_btn.clicked.connect(self._queue_job)

        # Clear button — shown after a run completes (solo or queued).
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(36)
        self._clear_btn.setVisible(False)
        make_interactive(self._clear_btn, tooltip="Clear queue and reset the tab.")
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.TEXT_MUTED};"
            f"  border:1px solid {theme.BORDER}; border-radius:4px; padding:4px 10px; font-size:12px; }}"
            f"QPushButton:hover {{ color:#fff; border-color:#888; }}"
        )
        self._clear_btn.clicked.connect(self._clear_all_jobs)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self._queue_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addWidget(self._status_label)
        btn_row.addWidget(self._awake_lbl)
        btn_row.addStretch()
        btn_row.addWidget(self._reveal_btn)
        btn_row.addWidget(self.manifest_btn)
        root.addLayout(btn_row)

        # Queue panel (hidden until jobs are added)
        self._queue_panel = QueuePanel()
        self._queue_panel.setVisible(False)
        self._queue_panel.run_requested.connect(self._run_next)
        self._queue_panel.clear_requested.connect(self._clear_all_jobs)
        self._queue_panel.edit_requested.connect(self._edit_queued_job)
        self._queue_panel.remove_requested.connect(self._remove_queued_job)
        root.addWidget(self._queue_panel)

        # ── Log panel (includes inline progress bar) ─────────────────────────
        self.log = LogWidget(
            "Transfer log", with_progress=True, parent=self,
            placeholder="Set a source and destination, then Start Transfer — "
                        "progress and results will stream here.")
        self.log.setMinimumHeight(180)
        root.addWidget(self.log)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Convenience aliases so progress-related methods need no changes
        self.progress_bar       = self.log.progress_bar
        self.current_file_label = self.log.current_file_label

    # ── Queue helpers ─────────────────────────────────────────────────────────

    def _capture_job_spec(self) -> TransferJobSpec:
        src = self.src_input.text()
        dst = self.dst_input.text()
        src_is_url = is_gdrive_url(src)
        dst_is_url = is_gdrive_url(dst)
        return TransferJobSpec(
            src=src,
            dst=dst,
            gdrive_mode=src_is_url or dst_is_url,
            mirror_mode=self.mirror_chk.isChecked(),
            paranoid_mode=self.paranoid_chk.isChecked(),
            conflict_handler=self._conflict_handler_str(),
            extract_zips=self.extract_zip_chk.isChecked(),
            export_mhl=self.export_mhl_chk.isChecked(),
            job_name="",
        )

    def _queue_job(self):
        src = self.src_input.text()
        dst = self.dst_input.text()
        if not src or not dst:
            show_toast(self, "Enter source and destination before queuing.", "info")
            return
        self._queue_counter += 1
        spec = self._capture_job_spec()
        spec.job_name = f"Transfer {self._queue_counter}"
        path_summary = f"{src} → {dst}"
        idx = self._queue_panel.add_item(self._queue_counter, spec.job_name, path_summary)
        self._job_queue.append(spec)
        self._queue_panel.setVisible(True)
        # Reset inputs for next job (keep recent-path dropdowns intact)
        self.src_input.setText("")
        self.dst_input.setText("")
        self.job_name_input.setText("")

    def _run_next(self):
        if not self._job_queue:
            self._queue_panel.show_clear_button(True)
            self._clear_btn.setVisible(True)
            return
        spec = self._job_queue.pop(0)
        # Find first Pending row index
        panel_idx = next(
            (i for i, r in enumerate(self._queue_panel._rows) if r._status == "Pending"),
            0,
        )
        self._current_queue_index = panel_idx
        self._queue_panel.set_status(panel_idx, "Running")
        # Load spec back into widget fields then start
        self.src_input.setText(spec.src)
        self.dst_input.setText(spec.dst)
        self.job_name_input.setText(spec.job_name)
        self.mirror_chk.setChecked(spec.mirror_mode)
        self.paranoid_chk.setChecked(spec.paranoid_mode)
        ci = {"skip": 0, "overwrite": 1, "rename": 2}.get(spec.conflict_handler, 1)
        self.conflict_combo.setCurrentIndex(ci)
        self.extract_zip_chk.setChecked(spec.extract_zips)
        self.export_mhl_chk.setChecked(spec.export_mhl)
        self._start_transfer()

    def _edit_queued_job(self, index: int):
        if index < 0 or index >= len(self._job_queue):
            return
        spec = self._job_queue.pop(index)
        self._queue_panel.remove_row(index)
        if not self._queue_panel._rows:
            self._queue_panel.setVisible(False)
        # Load spec into fields so user can edit and re-queue/start
        self.src_input.setText(spec.src)
        self.dst_input.setText(spec.dst)
        self.job_name_input.setText(spec.job_name)
        self.mirror_chk.setChecked(spec.mirror_mode)
        self.paranoid_chk.setChecked(spec.paranoid_mode)
        ci = {"skip": 0, "overwrite": 1, "rename": 2}.get(spec.conflict_handler, 1)
        self.conflict_combo.setCurrentIndex(ci)
        self.extract_zip_chk.setChecked(spec.extract_zips)
        self.export_mhl_chk.setChecked(spec.export_mhl)

    def _remove_queued_job(self, index: int):
        if 0 <= index < len(self._job_queue):
            self._job_queue.pop(index)
            self._queue_panel.remove_row(index)
            if not self._queue_panel._rows:
                self._queue_panel.setVisible(False)

    def _clear_all_jobs(self):
        self._job_queue = []
        self._queue_counter = 0
        self._current_queue_index = -1
        self._queue_panel.clear_all()
        self._queue_panel.setVisible(False)
        self._clear_btn.setVisible(False)
        # Reset tab state
        self.src_input.setText("")
        self.dst_input.setText("")
        self.job_name_input.setText("")
        self._banner.dismiss()
        self._reveal_btn.setVisible(False)
        self._status_label.setText("Ready")
        self.log.clear_log()

    def _conflict_handler_str(self):
        return {0: "skip", 1: "overwrite", 2: "rename"}[self.conflict_combo.currentIndex()]

    def _load_demo_folder(self, _href=""):
        """Pre-fill source/destination with the demo camera card structure."""
        try:
            src, dst = ensure_demo_folder()
            self.src_input.setText(str(src))
            self.dst_input.setText(str(dst))
            # Style the link to indicate it was used
            self._demo_link.setText(
                '<a href="demo" style="color:#555; font-size:11px; text-decoration:none;">'
                '⊙ Demo loaded</a>'
            )
        except Exception as exc:
            QMessageBox.warning(self, "Demo folder", f"Could not create demo folder:\n{exc}")

    def _on_paths_changed(self, _text=""):
        gdrive = is_gdrive_url(self.src_input.text()) or is_gdrive_url(self.dst_input.text())
        rename_item_idx = 2
        model_item = self.conflict_combo.model().item(rename_item_idx)
        if model_item:
            model_item.setEnabled(not gdrive)
        if gdrive and self.conflict_combo.currentIndex() == rename_item_idx:
            self.conflict_combo.setCurrentIndex(1)
        # Paranoid checkbox only matters for Drive transfers — local↔local is always verified
        self.paranoid_chk.setEnabled(gdrive)
        if gdrive:
            self.paranoid_chk.setToolTip(
                "Compute SHA-256 locally on the non-Drive side and compare to Drive's hash.\n"
                "Slower but doesn't trust rclone's --checksum."
            )
        else:
            self.paranoid_chk.setChecked(False)
            self.paranoid_chk.setToolTip(
                "Not applicable — local-to-local transfers already compute independent\n"
                "SHA-256 on both sides as part of every copy."
            )

    def _update_preflight(self):
        src = self.src_input.text()
        dst = self.dst_input.text()

        if not src or not dst:
            for attr in ("_pf_src_val", "_pf_dst_val", "_pf_time_val"):
                getattr(self, attr).setText("—")
                getattr(self, attr).setStyleSheet(
                    "font-size:13px; font-weight:500; color:#555; background:transparent;"
                )
            self._pf_hint.setText("Enter paths to see transfer summary")
            self._pf_hint.setStyleSheet("font-size:12px; color:#555; background:transparent;")
            return

        # Paths entered — brighten the values from the muted placeholder colour so
        # the computed summary reads as active, not greyed out.
        for attr in ("_pf_src_val", "_pf_dst_val", "_pf_time_val"):
            getattr(self, attr).setStyleSheet(
                f"font-size:13px; font-weight:600; color:{theme.TEXT_PRIMARY};"
                " background:transparent;"
            )

        try:
            src_is_url = is_gdrive_url(src)
            dst_is_url = is_gdrive_url(dst)
            total = None

            if src_is_url:
                self._pf_src_val.setText("Google Drive")
            else:
                src_path = Path(src)
                if not src_path.exists():
                    self._pf_src_val.setText("Not found")
                    return
                total = folder_size(src_path)
                self._pf_src_val.setText(format_bytes(total))

            if dst_is_url:
                self._pf_dst_val.setText("Google Drive")
            else:
                dst_path = Path(dst)
                if dst_path.exists():
                    self._pf_dst_val.setText(format_bytes(free_space(dst_path)))
                else:
                    self._pf_dst_val.setText("—")

            if total is not None:
                secs = estimate_time_seconds(total)
                h = int(secs // 3600); m = int((secs % 3600) // 60); s = int(secs % 60)
                self._pf_time_val.setText(f"{h}h {m}m {s}s" if h else f"{m}m {s}s")
            else:
                self._pf_time_val.setText("—")

            # GDrive daily limit warning
            if total is not None and total > GDRIVE_DAILY_LIMIT_BYTES and dst_is_url:
                self._pf_hint.setText("⚠ Exceeds 750 GB daily limit")
                self._pf_hint.setStyleSheet(f"font-size:12px; color:{theme.CORAL}; background:transparent;")
            else:
                self._pf_hint.setText("")
        except Exception:
            pass

    def _start_transfer(self):
        self._cancelled = False
        src = self.src_input.text()
        dst = self.dst_input.text()
        if not src or not dst:
            if self._job_queue:
                self._run_next()
                return
            QMessageBox.warning(self, "Missing Input",
                                "Please enter both source and destination.")
            return

        if not check_and_prompt(self):
            return

        src_is_url = is_gdrive_url(src)
        dst_is_url = is_gdrive_url(dst)
        gdrive_mode = src_is_url or dst_is_url
        mirror_mode = self.mirror_chk.isChecked()

        # M3: Drive-to-Drive (both sides URLs) is supported — rclone copies
        # server-side with no local disk. route_transfer dispatches it to
        # transfer_folder_rclone; pre_flight_checks sizes the remote source and
        # enforces the 750 GB/day limit. No guard here.

        try:
            pre_flight_checks(
                src, dst,
                is_gdrive_dest=dst_is_url,
                log_cb=self.log.log,
            )
        except TransferError as e:
            QMessageBox.critical(self, "Transfer Error", str(e))
            return
        except TransferWarning as w:
            resp = QMessageBox.warning(
                self, "Destination Space Warning", str(w),
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if resp == QMessageBox.StandardButton.Cancel:
                return

        if mirror_mode:
            resp = QMessageBox.warning(
                self, "Mirror Mode Confirmation",
                "<b>Mirror mode is ON.</b><br><br>"
                f"Any files at <code>{dst}</code> that are NOT in <code>{src}</code> "
                "<b>will be permanently deleted</b>.<br><br>Continue?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if resp == QMessageBox.StandardButton.Cancel:
                return

        if not gdrive_mode:
            src_path = Path(src)
            dst_path = Path(dst)
            actual_dest, same_name = _resolve_dest_info(src_path, dst_path)
            if same_name:
                msg = (
                    "<b>Source and destination folders share the same name.</b><br><br>"
                    "Files will be merged directly into:<br>"
                    f"<code>{actual_dest}</code><br><br>"
                    "Existing files may be overwritten depending on conflict setting."
                )
            else:
                msg = (
                    "<b>A new folder will be created at the destination.</b><br><br>"
                    "Files will be copied to:<br>"
                    f"<code>{actual_dest}</code>"
                )
            resp = QMessageBox.question(
                self, "Confirm Destination", msg,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if resp == QMessageBox.StandardButton.Cancel:
                return
        else:
            action = "sync (mirror)" if mirror_mode else "copy"
            resp = QMessageBox.question(
                self, "Confirm Google Drive Transfer",
                f"<b>This will run rclone {action}.</b><br><br>"
                f"Source: <code>{src}</code><br>Destination: <code>{dst}</code><br>"
                f"Conflict handling: <b>{self._conflict_handler_str()}</b>",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if resp == QMessageBox.StandardButton.Cancel:
                return

        self._thread = QThread()
        self._worker = TransferWorker(
            src, dst,
            gdrive_mode=gdrive_mode,
            mirror_mode=mirror_mode,
            paranoid_mode=self.paranoid_chk.isChecked(),
            conflict_handler=self._conflict_handler_str(),
            extract_zips=self.extract_zip_chk.isChecked(),
            export_mhl=self.export_mhl_chk.isChecked(),
            job_name=self.job_name_input.text().strip(),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self.log.log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._reveal_btn.setVisible(False)
        self._banner.dismiss()
        self._status_label.setText("Transferring…")
        self.log.set_progress(0, current_file="Starting…")

        start_session()
        self._awake_lbl.setVisible(True)   # M12.5
        self._thread.start()

    def _cancel_transfer(self):
        if self._thread and self._thread.isRunning():
            # Set flag BEFORE killing the subprocess. Killing it causes the
            # worker to emit error() via a queued cross-thread signal. _on_error
            # checks this flag so it won't show a "Transfer Failed" dialog or
            # double-call end_session() / _reset_controls().
            self._cancelled = True
            killed = rclone_bridge.cancel_current()
            if killed:
                self.log.log("rclone subprocess terminated.", "warning")
            self._thread.quit()
            self._thread.wait(3000)
            end_session()
            self.log.log("Transfer cancelled by user.", "warning")
            self._reset_controls()
            self._cancelled = False

    def _on_progress(self, pct, info):
        if isinstance(info, dict):
            # Unpack rich rclone progress dict
            current_file = info.get("current_file") or ""
            speed        = info.get("speed") or ""
            eta          = info.get("eta") or ""
            files_done   = info.get("files_done")
            files_total  = info.get("files_total")
            self.log.set_progress(
                pct,
                current_file=current_file,
                speed=speed,
                eta=eta,
                files_done=files_done,
                files_total=files_total,
            )
        else:
            # Plain string from local transfers (or legacy callers)
            self.log.set_progress(pct, current_file=str(info) if info else "")

    def _on_finished(self, result):
        if self._cancelled:
            return  # _cancel_transfer already handled cleanup
        end_session()
        self.log.set_progress(100, current_file="Complete")
        self._reset_controls()
        errors = result.get("errors", [])
        if errors:
            self.log.log(f"Transfer complete with {len(errors)} error(s).", "warning")
            show_toast(self, f"Transfer finished with {len(errors)} error(s) — see log.", "warn")
            self._banner.show_result(
                f"⚠  TRANSFER FINISHED WITH {len(errors)} ERROR(S) — review the log "
                "before trusting the copy.", ok=False)
        else:
            self.log.log(f"Transfer complete  {result.get('actual_dest', '')}", "success")
            show_toast(self, "Transfer complete.", "success")
            fallback_count = (result.get("manifest", {})
                              .get("checksum_context", {})
                              .get("paranoid_fallback_count", 0))
            subtitle = (f"{fallback_count} file(s) verified via rclone-checksum, not independent SHA-256."
                        if fallback_count else "")
            self._banner.show_result(
                "✓  TRANSFER COMPLETE — all files copied and verified.", ok=True,
                subtitle=subtitle)
        # Offer a one-click jump to the output for local destinations.
        dest = result.get("actual_dest") or self.dst_input.text()
        self._last_dest = dest
        self._reveal_btn.setVisible(bool(dest) and not is_gdrive_url(dest))
        self.src_input.add_to_recent(self.src_input.text())
        self.dst_input.add_to_recent(self.dst_input.text())
        self._write_txt_log(result)
        src = self.src_input.text()
        if not errors and not (is_gdrive_url(src) and is_gdrive_url(dest)):
            local = dest if not is_gdrive_url(dest) else src
            remote = src if not is_gdrive_url(dest) else dest
            self._register_project(result, src=remote, dest=local)
        # Queue: mark current item done or failed and advance.
        if self._current_queue_index >= 0:
            status = "Failed" if errors else "Done"
            self._queue_panel.set_status(self._current_queue_index, status)
            self._current_queue_index = -1
        self._run_next()

    def _reveal_destination(self):
        dest = getattr(self, "_last_dest", "")
        if dest:
            reveal_in_finder(dest)

    def _on_error(self, msg):
        if self._cancelled:
            return  # _cancel_transfer already handled cleanup; skip spurious error dialog
        end_session()
        self._reset_controls()
        self.log.log(f"FATAL: {msg}", "error")
        self._banner.show_result("✕  TRANSFER FAILED — see the log.", ok=False)
        QMessageBox.critical(self, "Transfer Failed", msg)
        if self._current_queue_index >= 0:
            self._queue_panel.set_status(self._current_queue_index, "Failed")
            self._current_queue_index = -1
        self._run_next()

    def _reset_controls(self):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._status_label.setText("Ready")
        self._awake_lbl.setVisible(False)   # M12.5
        self.log.hide_progress()

    def _generate_manifest_only(self):
        src = self.src_input.text()
        if not src or is_gdrive_url(src) or not Path(src).exists():
            QMessageBox.warning(self, "No Source", "Enter a valid local source folder first.")
            return
        from core.manifest import generate_manifest, save_manifest
        self.log.log(f"Generating manifest for {src}...", "info")
        manifest = generate_manifest(
            Path(src), label="manual",
            progress_cb=lambda p, f: self._on_progress(p, f),
        )
        paths = save_manifest(manifest, source_dir=Path(src), name_hint=Path(src).name)
        self.log.log(f"Manifest saved ({len(manifest['files'])} files)", "success")
        for p in paths:
            self.log.log(f"  -> {p}", "info")

    def _register_project(self, result, src: str, dest: str):
        """Register the transfer destination in the projects registry.

        Only called on error-free transfers to local destinations, so Verify All
        and scheduled verify can find this folder. project_id mirrors the formula
        used by Merge: stable hash of (local_path, counterpart_path).
        """
        saved = result.get("saved_manifest_paths", [])
        project_id = _make_project_id(dest, src)
        try:
            project_registry.upsert_project(
                project_id,
                local_path=dest,
                server_path=src,
                latest_manifest=saved[0] if saved else "",
            )
        except Exception as e:
            self.log.log(f"  Could not register project: {e}", "warning")

    def _write_txt_log(self, result):
        import getpass, socket
        from pathlib import Path as _Path
        from datetime import datetime
        from core import paths as _paths
        log_dir = _paths.transfer_reports_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest = result.get("manifest", {})
        src_root = manifest.get("source_root", "")
        # Include the source folder name in the filename so logs are identifiable
        # without opening them.
        src_slug = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in _Path(src_root).name
        ).strip("_")[:60] if src_root else ""
        log_name = f"transfer_{src_slug + '_' if src_slug else ''}{ts}.txt"
        log_path = log_dir / log_name
        lines = [
            "=" * 60,
            "SIGNAL THEORY -- ST SyncTool Transfer Log",
            f"Date/Time  : {datetime.now().isoformat()}",
            f"Workstation: {socket.gethostname()}",
            f"User       : {getpass.getuser()}",
            f"Source     : {src_root}",
            f"Destination: {result.get('actual_dest', '')}",
            f"Same-name merge: {result.get('same_name', False)}",
            "=" * 60,
        ]
        all_files = {
            k: v for k, v in manifest.get("files", {}).items()
            if not _Path(k).name.startswith("._")
        }
        counts = manifest.get("status_counts")
        vmethod = manifest.get("verification_method", "") or \
                  manifest.get("checksum_context", {}).get("method", "")
        vfailures = manifest.get("verify_failures", [])
        error_count = manifest.get("error_count", len(result.get("errors") or []))
        lines += ["", "SUMMARY:"]
        if vmethod in ("paranoid",):
            lines.append("  Verification : Paranoid (independent xxHash128 on source and destination)")
        elif vmethod == "rclone-checksum":
            lines.append("  Verification : rclone --checksum (source vs destination compared at transfer time)")
        elif vmethod in ("local", "local-copy"):
            lines.append("  Verification : Local copy (xxHash128 computed pre- and post-copy)")
        else:
            lines.append(f"  Verification : {vmethod or 'unknown'}")
        lines.append(f"  Files        : {len(all_files)}")
        if counts:
            lines += [
                f"  Uploaded     : {counts.get('uploaded', 0)}",
                f"  Updated      : {counts.get('updated', 0)}",
                f"  Unchanged    : {counts.get('unchanged', 0)}",
                f"  Deleted      : {counts.get('deleted', 0)}",
            ]
        if vfailures:
            lines.append(f"  VERIFICATION FAILURES: {len(vfailures)}")
        if error_count:
            lines.append(f"  ERRORS       : {error_count}")
        lines += ["", "FILES IN DESTINATION (POST-TRANSFER):"]
        for fname, fdata in all_files.items():
            src_block = fdata.get("source_checksums", {}) or {}
            dst_block = fdata.get("dest_checksums",   {}) or {}
            cs_block  = fdata.get("checksums",        {}) or {}
            # Prefer xxh128, fall back to md5 then sha256 (Drive manifests may
            # only carry md5; paranoid mode carries sha256).
            for algo_key, algo_label in (("xxh128", "XXH128"), ("md5", "MD5"), ("sha256", "SHA-256")):
                src_cs = src_block.get(algo_key) or cs_block.get(algo_key)
                dst_cs = dst_block.get(algo_key) or cs_block.get(algo_key)
                if src_cs or dst_cs:
                    break
            else:
                algo_label, src_cs, dst_cs = "XXH128", "N/A", "N/A"
            status = fdata.get("status", "verified")
            lines += [
                f"  {fname}  [{status.upper()}]",
                f"    Size          : {format_bytes(fdata.get('size', 0))}",
                f"    {algo_label} src : {src_cs or 'N/A'}",
                f"    {algo_label} dst : {dst_cs or 'N/A'}",
            ]
        deleted = manifest.get("deleted_files", [])
        if deleted:
            lines += ["", "FILES DELETED FROM DESTINATION (mirror mode):"]
            for path in deleted:
                lines.append(f"  {path}")
        if result.get("errors"):
            lines += ["", "ERRORS:"]
            for e in result["errors"]:
                lines.append(f"  {e['file']} -- {e['error']}")
        lines += ["", "END OF LOG"]
        log_path.write_text("\n".join(lines))
        self.log.log(f"  Log saved: {log_path}", "info")


def _resolve_dest_info(src: Path, dst: Path):
    from core.transfer import resolve_folder_conflict
    return resolve_folder_conflict(src, dst)
