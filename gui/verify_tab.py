from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QFileDialog, QMessageBox, QFrame, QCheckBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject

from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
from core.manifest import load_manifest
from gui import theme
from core import rclone_bridge
from core import verify as _verify
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone


class VerifyWorker(QObject):
    progress = pyqtSignal(int, str)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, folder, manifest, deep=False, manifest_path=None, label=""):
        super().__init__()
        self.folder   = folder  # Keep raw string; could be URL
        self.manifest = manifest
        self.deep     = deep    # M5.1: download + hash each Drive file
        self.manifest_path = manifest_path  # M5.4: where to persist media_verify
        self.label    = label

    def run(self):
        # M5.0: all verification logic now lives in core.verify (headless,
        # testable). This worker is a thin adapter wiring callbacks to signals.
        try:
            results = _verify.verify_folder(
                self.folder, self.manifest,
                progress_cb=lambda pct, path: self.progress.emit(pct, path),
                log_cb=lambda msg, level: self.log.emit(msg, level),
                deep=self.deep,
            )
            # M5.4: persist the format-verification evidence so it survives the
            # window closing. Persistence must never fail the verify itself.
            log_cb = lambda msg, level: self.log.emit(msg, level)
            try:
                _verify.write_verify_report(
                    self.folder, results, label=self.label, deep=self.deep, log_cb=log_cb)
                if self.manifest_path:
                    _verify.persist_media_verify_to_manifest(
                        self.manifest_path, results, log_cb=log_cb)
            except Exception as pe:
                self.log.emit(f"  Could not persist verify results: {pe}", "warning")
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class BatchVerifyWorker(QObject):
    """M5.2: verify every registered project in one run. Thin adapter over
    core.verify.pairs_from_registry + batch_verify."""
    progress = pyqtSignal(int, str)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(list, list)   # summaries, skipped
    error    = pyqtSignal(str)

    def __init__(self, deep=False):
        super().__init__()
        self.deep = deep

    def run(self):
        try:
            pairs, skipped = _verify.pairs_from_registry()
            summaries = _verify.batch_verify(
                pairs,
                progress_cb=lambda pct, label: self.progress.emit(pct, label),
                log_cb=lambda msg, level: self.log.emit(msg, level),
                deep=self.deep,
            )
            self.finished.emit(summaries, skipped)
        except Exception as e:
            self.error.emit(str(e))


class VerifyTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manifest = None
        self._thread   = None
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(theme.tab_stylesheet(theme.tab_accent("Verify")))
        root = QVBoxLayout(self)
        root.setSpacing(14)
        root.setContentsMargins(20, 16, 20, 12)

        # ── Verify settings ──────────────────────────────────────
        input_group = QGroupBox("VERIFY SETTINGS")
        ig = QVBoxLayout(input_group)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Folder to Verify:   "))
        self.folder_input = PathInputWidget("verify_folder", self)
        self.folder_input.input.setPlaceholderText(
            "Local path  or  https://drive.google.com/drive/folders/..."
        )
        frow.addWidget(self.folder_input)
        ig.addLayout(frow)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Manifest File (.json):"))
        self.manifest_input = PathInputWidget("verify_manifest", self)
        self.manifest_input.browse_btn.clicked.disconnect()
        self.manifest_input.browse_btn.clicked.connect(self._browse_manifest)
        self.manifest_input.input.setPlaceholderText(
            "Required for Drive folders; auto-detects st_manifest.json for local folders"
        )
        mrow.addWidget(self.manifest_input)
        ig.addLayout(mrow)

        # M5.1: deep Drive verify — downloads each file and re-hashes it instead
        # of trusting Drive's metadata. Bandwidth-bound, so it is opt-in and only
        # meaningful for Drive folders.
        self.deep_chk = QCheckBox("Deep verify (downloads files) — Drive only")
        self.deep_chk.setToolTip(
            "Streams each Drive file through rclone to compute its SHA-256 locally,\n"
            "instead of trusting Google's stored hash. No local copy is kept.\n"
            "Bandwidth-bound: an estimate is shown when you start."
        )
        self.deep_chk.setEnabled(False)
        ig.addWidget(self.deep_chk)
        self.folder_input.input.textChanged.connect(self._update_deep_enabled)

        root.addWidget(input_group)

        # ── Action row ───────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.verify_btn = QPushButton("🛡  Run Verification")
        self.verify_btn.setObjectName("primaryBtn")
        self.verify_btn.setFixedHeight(40)
        self.verify_btn.clicked.connect(self._run_verify)
        btn_row.addWidget(self.verify_btn)

        self.batch_btn = QPushButton("📋  Verify All Projects")
        self.batch_btn.setFixedHeight(36)
        self.batch_btn.setToolTip(
            "Verify every project in the registry against its latest manifest,\n"
            "producing one consolidated OK / MISSING / MISMATCH report."
        )
        self.batch_btn.clicked.connect(self._run_batch_verify)
        btn_row.addWidget(self.batch_btn)

        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_verify)
        btn_row.addWidget(self.cancel_btn)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:12px; margin-left:auto;")
        btn_row.addStretch()
        btn_row.addWidget(self.status_label)
        root.addLayout(btn_row)

        # ── Summary cards ────────────────────────────────────────
        summary_group = QGroupBox("RESULTS")
        sg = QHBoxLayout(summary_group)
        sg.setSpacing(10)

        # Glyphs accompany colour so the tiles read for colour-blind users.
        card_defs = [
            ("_card_ok",      "—", theme.VERDICT_GREEN,   "✓ OK"),
            ("_card_extra",   "—", theme.VERDICT_GOLD,    "⚠ Extra files"),
            ("_card_missing", "—", theme.VERDICT_CORAL,   "✗ Missing"),
            ("_card_mismatch","—", theme.VERDICT_MAGENTA, "✗ Mismatch"),
        ]
        for attr, default_val, color, label_text in card_defs:
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background:{theme.CHARCOAL_LIGHT}; border-radius:6px; }}"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.setSpacing(3)
            # Colour accent bar above the number (per the redesign mockup).
            bar = QFrame()
            bar.setFixedSize(30, 4)
            bar.setStyleSheet(f"background:{color}; border-radius:2px;")
            bar_row = QHBoxLayout()
            bar_row.setContentsMargins(0, 0, 0, 4)
            bar_row.addStretch()
            bar_row.addWidget(bar)
            bar_row.addStretch()
            cl.addLayout(bar_row)
            num_lbl = QLabel(default_val)
            num_lbl.setStyleSheet(
                f"font-size:22px; font-weight:500; color:{color}; background:transparent;"
            )
            num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_lbl = QLabel(label_text)
            txt_lbl.setStyleSheet(
                f"font-size:11px; color:{theme.TEXT_MUTED}; background:transparent;"
            )
            txt_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(num_lbl)
            cl.addWidget(txt_lbl)
            sg.addWidget(card)
            setattr(self, attr, num_lbl)

        root.addWidget(summary_group)

        # ── Progress bar ─────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        # ── Single log panel ─────────────────────────────────────
        self.log = LogWidget("Results log", parent=self)
        root.addWidget(self.log, stretch=1)

    def _browse_manifest(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Manifest", "", "JSON Files (*.json)"
        )
        if path:
            self.manifest_input.setText(path)

    def _run_verify(self):
        folder_str   = self.folder_input.text()
        manifest_str = self.manifest_input.text()

        if not folder_str:
            QMessageBox.warning(self, "No Folder",
                                "Please enter a folder path or Drive URL.")
            return

        folder_is_url = is_gdrive_url(folder_str)
        if not folder_is_url:
            folder = Path(folder_str)
            if not folder.exists():
                QMessageBox.critical(self, "Folder Not Found",
                                     f"Folder does not exist:\n{folder}")
                return

        if not manifest_str:
            if folder_is_url:
                QMessageBox.warning(
                    self, "Manifest Required",
                    "Verifying a Drive folder requires an explicit manifest .json.\n"
                    "Pick the one saved during the Transfer that downloaded these files."
                )
                return
            auto = Path(folder_str) / "st_manifest.json"
            if auto.exists():
                manifest_str = str(auto)
                self.log.log(f"Auto-loaded manifest: {auto.name}", "info")
            else:
                QMessageBox.warning(self, "No Manifest",
                                    "No manifest selected and no st_manifest.json found.")
                return

        try:
            self._manifest = load_manifest(Path(manifest_str))
        except Exception as e:
            QMessageBox.critical(self, "Manifest Error",
                                 f"Could not load manifest:\n{e}")
            return

        file_count = len(self._manifest.get("files", {}))
        kind = "Drive folder" if folder_is_url else "local folder"
        self.log.log(f"Starting verification of {file_count} files in {kind}...", "info")
        self.log.clear_log()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.verify_btn.setEnabled(False)
        self.batch_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("Verifying…")

        deep = folder_is_url and self.deep_chk.isChecked()
        if deep:
            self.log.log("Deep verify enabled — files will be downloaded and re-hashed.", "info")

        self._thread = QThread()
        self._worker = VerifyWorker(
            folder_str, self._manifest, deep=deep,
            manifest_path=manifest_str, label=Path(folder_str).name)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(
            lambda p, f: (self.progress_bar.setValue(p), self.status_label.setText(f))
        )
        self._worker.log.connect(self.log.log)
        self._worker.finished.connect(self._on_verify_done)
        self._worker.error.connect(self._on_verify_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _run_batch_verify(self):
        # M5.2: verify every registered project in one run (consolidated report).
        self.log.clear_log()
        self.log.log("Starting batch verification of all registered projects...", "info")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.verify_btn.setEnabled(False)
        self.batch_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("Batch verifying…")

        self._thread = QThread()
        self._worker = BatchVerifyWorker(deep=False)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(
            lambda p, label: (self.progress_bar.setValue(p), self.status_label.setText(label))
        )
        self._worker.log.connect(self.log.log)
        self._worker.finished.connect(self._on_batch_done)
        self._worker.error.connect(self._on_verify_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_batch_done(self, summaries, skipped):
        self.verify_btn.setEnabled(True)
        self.batch_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)

        report = _verify.format_batch_report(summaries, skipped)
        n_fail = sum(1 for s in summaries if s.verdict == "FAIL")
        n_err = sum(1 for s in summaries if s.verdict == "ERROR")
        n_ok = sum(1 for s in summaries if s.verdict == "OK")
        level = "success" if (n_fail == 0 and n_err == 0) else "warning"
        self.log.log(
            f"Batch verification complete — {n_ok} OK | {n_fail} fail | {n_err} error "
            f"across {len(summaries)} project(s)", level,
        )
        self.status_label.setText(f"{n_ok}/{len(summaries)} projects OK")

        # Persist the consolidated report.
        import getpass, socket
        from datetime import datetime
        from core import paths as _paths
        log_dir = _paths.verify_reports_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = log_dir / f"batch_verify_{ts}.txt"
        header = (
            f"Date/Time  : {datetime.now().isoformat()}\n"
            f"Workstation: {socket.gethostname()}\n"
            f"User       : {getpass.getuser()}\n\n"
        )
        report_path.write_text(header + report)
        self.log.log(f"  Report saved: {report_path}", "info")
        QMessageBox.information(self, "Batch Verification Complete", report)

    def _update_deep_enabled(self, text):
        """Deep verify only applies to Drive folders; disable + uncheck for local."""
        is_url = is_gdrive_url(text.strip())
        self.deep_chk.setEnabled(is_url)
        if not is_url:
            self.deep_chk.setChecked(False)

    def _cancel_verify(self):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self.verify_btn.setEnabled(True)
        self.batch_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Cancelled")
        self.log.log("Verification cancelled by user.", "warning")

    def _on_verify_done(self, results):
        self.verify_btn.setEnabled(True)
        self.batch_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)

        ok       = sum(1 for r in results if r["status"] == "OK")
        missing  = sum(1 for r in results if r["status"] == "MISSING")
        mismatch = sum(1 for r in results if r["status"] == "MISMATCH")
        total    = len(results)

        # Populate summary cards
        self._card_ok.setText(str(ok))
        self._card_extra.setText("—")     # tracked in GDrive verify log only
        self._card_missing.setText(str(missing))
        self._card_mismatch.setText(str(mismatch))

        level = "success" if (missing == 0 and mismatch == 0) else "warning"
        self.log.log(
            f"Verification complete — {ok} OK | {mismatch} mismatch{'es' if mismatch != 1 else ''} | {missing} missing",
            level,
        )
        self.status_label.setText(f"{ok}/{total} files OK")
        self._write_verify_report(results)

    def _on_verify_error(self, msg):
        self.verify_btn.setEnabled(True)
        self.batch_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Error")
        self.log.log(f"Verify error: {msg}", "error")
        QMessageBox.critical(self, "Verify Error", msg)

    def _write_verify_report(self, results):
        import getpass, socket
        from datetime import datetime
        from core import paths as _paths
        log_dir = _paths.verify_reports_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"verify_{ts}.txt"
        lines = [
            "=" * 60,
            "SIGNAL THEORY -- ST SyncTool Verification Report",
            f"Date/Time  : {datetime.now().isoformat()}",
            f"Workstation: {socket.gethostname()}",
            f"User       : {getpass.getuser()}",
            f"Folder     : {self.folder_input.text()}",
            "=" * 60, "",
        ]
        for r in results:
            icon = "OK" if r["status"] == "OK" else "FAIL"
            lines.append(f"  [{icon}] {r['path']}")
            lines.append(f"         {r['detail']}")
        lines += ["", "END OF REPORT"]
        log_path.write_text("\n".join(lines))
        self.log.log(f"  Report saved: {log_path}", "info")
