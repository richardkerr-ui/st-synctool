from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QFileDialog, QMessageBox
)
from PyQt6.QtCore import QThread, pyqtSignal, QObject

from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
from core.manifest import load_manifest
from core.checksum import compute_all
from gui import theme
from core import rclone_bridge
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone


class VerifyWorker(QObject):
    progress = pyqtSignal(int, str)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, folder, manifest):
        super().__init__()
        self.folder   = folder  # Keep raw string; could be URL
        self.manifest = manifest

    def run(self):
        try:
            if is_gdrive_url(str(self.folder)):
                self._verify_gdrive()
            else:
                self._verify_local()
        except Exception as e:
            self.error.emit(str(e))

    def _expected_checksums(self, entry):
        """Pull the most authoritative checksum block out of a manifest entry."""
        return (entry.get("dest_checksums")
                or entry.get("source_checksums")
                or entry.get("checksums", {}))

    def _verify_local(self):
        folder = Path(self.folder)
        files = self.manifest.get("files", {})
        total = max(len(files), 1)
        results = []

        for i, (rel_path, entry) in enumerate(files.items()):
            self.progress.emit(int(i / total * 100), rel_path)
            abs_path = folder / rel_path

            if not abs_path.exists():
                results.append({"path": rel_path, "status": "MISSING",
                                "detail": "File not found on disk"})
                self.log.emit(f"  MISSING: {rel_path}", "error")
                continue

            expected_cs = self._expected_checksums(entry)
            algo = ("sha256" if "sha256" in expected_cs else
                    "xxhash3_64" if "xxhash3_64" in expected_cs else "md5")
            actual = compute_all(
                abs_path,
                include_xxhash=(algo == "xxhash3_64"),
                include_md5=(algo == "md5"),
            )
            expected_val = (expected_cs.get(algo) or "").lower()
            actual_val   = (actual.get(algo) or "").lower()

            if expected_val == actual_val and expected_val:
                results.append({"path": rel_path, "status": "OK",
                                "detail": f"{algo}: {actual_val[:16]}..."})
                self.log.emit(f"  OK: {rel_path}", "success")
            else:
                results.append({"path": rel_path, "status": "MISMATCH",
                                "detail": f"Expected {expected_val[:16]}... | Got {actual_val[:16]}..."})
                self.log.emit(f"  MISMATCH: {rel_path}", "error")

        self.progress.emit(100, "Complete")
        self.finished.emit(results)

    def _verify_gdrive(self):
        """Verify a Drive folder by pulling hashes via rclone lsjson and comparing
        to the manifest. No file downloads — purely metadata-based."""
        self.log.emit("Fetching Drive folder hashes via rclone lsjson...", "info")
        remote, flags = gdrive_url_to_rclone(str(self.folder))
        try:
            items = rclone_bridge.lsjson(remote, extra_flags=flags, with_checksum=True)
        except Exception as e:
            self.error.emit(f"rclone lsjson failed: {e}")
            return

        # Build {rel_path: hashes_dict} from Drive listing
        drive_files = {}
        for item in items:
            if item.get("IsDir"):
                continue
            hashes = {k.lower(): (v or "").lower()
                      for k, v in (item.get("Hashes") or {}).items()}
            drive_files[item["Path"]] = hashes

        files = self.manifest.get("files", {})
        total = max(len(files), 1)
        results = []

        for i, (rel_path, entry) in enumerate(files.items()):
            self.progress.emit(int(i / total * 100), rel_path)

            if rel_path not in drive_files:
                results.append({"path": rel_path, "status": "MISSING",
                                "detail": "Not present in Drive folder"})
                self.log.emit(f"  MISSING: {rel_path}", "error")
                continue

            expected_cs = self._expected_checksums(entry)
            drive_hashes = drive_files[rel_path]

            # Pick the strongest hash available on both sides
            algo = None
            for candidate in ("sha256", "sha1", "md5"):
                if candidate in expected_cs and candidate in drive_hashes:
                    algo = candidate
                    break

            if algo is None:
                results.append({"path": rel_path, "status": "MISMATCH",
                                "detail": "No common hash algorithm between manifest and Drive"})
                self.log.emit(f"  MISMATCH (no common hash): {rel_path}", "error")
                continue

            expected_val = (expected_cs.get(algo) or "").lower()
            actual_val   = drive_hashes.get(algo, "")

            if expected_val == actual_val and expected_val:
                results.append({"path": rel_path, "status": "OK",
                                "detail": f"{algo}: {actual_val[:16]}..."})
                self.log.emit(f"  OK: {rel_path}", "success")
            else:
                results.append({"path": rel_path, "status": "MISMATCH",
                                "detail": f"Expected {expected_val[:16]}... | Got {actual_val[:16]}..."})
                self.log.emit(f"  MISMATCH: {rel_path}", "error")

        # Report extras on Drive not covered by manifest (info only)
        extras = set(drive_files.keys()) - set(files.keys())
        # Filter out internal/junk files
        extras = {p for p in extras if Path(p).name not in
                  ("st_manifest.json", ".DS_Store", "Thumbs.db", "desktop.ini")}
        if extras:
            self.log.emit(
                f"  Note: {len(extras)} file(s) present on Drive but not in manifest",
                "warning",
            )

        self.progress.emit(100, "Complete")
        self.finished.emit(results)


class VerifyTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manifest = None
        self._thread   = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        input_group = QGroupBox("Verify Settings")
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

        root.addWidget(input_group)

        btn_row = QHBoxLayout()
        self.verify_btn = QPushButton("  Run Verification")
        self.verify_btn.setFixedHeight(36)
        self.verify_btn.setStyleSheet(theme.primary_button_style())
        self.verify_btn.clicked.connect(self._run_verify)
        btn_row.addWidget(self.verify_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_label = QLabel("--")
        self.status_label.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
        root.addWidget(self.progress_bar)
        root.addWidget(self.status_label)

        results_label = QLabel("Results Log:")
        results_label.setStyleSheet(f"font-weight:bold;color:{theme.TEXT_MUTED};")
        root.addWidget(results_label)

        self.results_log = LogWidget(self)
        root.addWidget(self.results_log, stretch=1)

        self.log = LogWidget(self)
        self.log.setMaximumHeight(130)
        root.addWidget(self.log)

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
        self.results_log.clear_log()
        self.progress_bar.setValue(0)
        self.verify_btn.setEnabled(False)

        self._thread = QThread()
        self._worker = VerifyWorker(folder_str, self._manifest)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(
            lambda p, f: (self.progress_bar.setValue(p), self.status_label.setText(f))
        )
        self._worker.log.connect(self.results_log.log)
        self._worker.finished.connect(self._on_verify_done)
        self._worker.error.connect(self._on_verify_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_verify_done(self, results):
        self.verify_btn.setEnabled(True)
        ok       = sum(1 for r in results if r["status"] == "OK")
        missing  = sum(1 for r in results if r["status"] == "MISSING")
        mismatch = sum(1 for r in results if r["status"] == "MISMATCH")
        total    = len(results)
        level    = "success" if (missing == 0 and mismatch == 0) else "warning"
        self.log.log(
            f"Verification complete -- {ok} OK | {mismatch} MISMATCHES | {missing} MISSING",
            level,
        )
        self.progress_bar.setValue(100)
        self.status_label.setText(f"{ok}/{total} files OK")
        self._write_verify_report(results)

    def _on_verify_error(self, msg):
        self.verify_btn.setEnabled(True)
        self.log.log(f"Verify error: {msg}", "error")
        QMessageBox.critical(self, "Verify Error", msg)

    def _write_verify_report(self, results):
        import getpass, socket
        from datetime import datetime
        log_dir = Path.home() / "Documents" / "STSyncTool" / "logs"
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
