#!/usr/bin/env python3
"""
write_gui_tabs.py
Run from inside st_synctool/ to write the four large GUI files.
"""

from pathlib import Path

files = {}

# ─────────────────────────────────────────────────────────────────────────────
# gui/transfer_tab.py
# ─────────────────────────────────────────────────────────────────────────────
files["gui/transfer_tab.py"] = '''
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QCheckBox, QComboBox,
    QMessageBox, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont
from pathlib import Path

from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
from utils.file_utils import folder_size, free_space, format_bytes
from utils.gdrive_utils import is_gdrive_url
from core.transfer import (
    pre_flight_checks, transfer_folder, extract_multipart_zip,
    TransferError, TransferWarning, estimate_time_seconds,
    GDRIVE_DAILY_LIMIT_BYTES,
)
from core.amphetamine import check_and_prompt, start_session, end_session


class TransferWorker(QObject):
    progress = pyqtSignal(int, str)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, src, dst, gdrive_mode, conflict_handler, extract_zips):
        super().__init__()
        self.src              = Path(src)
        self.dst              = Path(dst)
        self.gdrive_mode      = gdrive_mode
        self.conflict_handler = conflict_handler
        self.extract_zips     = extract_zips

    def run(self):
        try:
            result = transfer_folder(
                self.src, self.dst,
                gdrive_mode=self.gdrive_mode,
                log_cb=lambda m, l: self.log.emit(m, l),
                progress_cb=lambda p, f: self.progress.emit(p, f),
                conflict_handler=self.conflict_handler,
            )
            if self.extract_zips:
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
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Source / Destination
        io_group = QGroupBox("Source & Destination")
        io_layout = QVBoxLayout(io_group)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source:     "))
        self.src_input = PathInputWidget("source", self)
        self.src_input.pathChanged.connect(self._update_preflight)
        src_row.addWidget(self.src_input)
        io_layout.addLayout(src_row)

        dst_row = QHBoxLayout()
        dst_row.addWidget(QLabel("Destination:"))
        self.dst_input = PathInputWidget("destination", self)
        self.dst_input.pathChanged.connect(self._update_preflight)
        dst_row.addWidget(self.dst_input)
        io_layout.addLayout(dst_row)

        root.addWidget(io_group)

        # Pre-flight summary label
        self.preflight_label = QLabel("Enter source and destination to see transfer summary.")
        self.preflight_label.setWordWrap(True)
        self.preflight_label.setStyleSheet("color:#aaa;font-size:12px;padding:4px;")
        root.addWidget(self.preflight_label)

        # Options
        opts_group = QGroupBox("Options")
        opts_layout = QHBoxLayout(opts_group)
        opts_layout.addWidget(QLabel("On conflict:"))
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItems(["Skip existing", "Overwrite", "Rename copy"])
        opts_layout.addWidget(self.conflict_combo)
        opts_layout.addSpacing(20)
        self.extract_zip_chk = QCheckBox("Auto-extract multipart .zips after transfer")
        opts_layout.addWidget(self.extract_zip_chk)
        opts_layout.addStretch()
        root.addWidget(opts_group)

        # Progress
        prog_group = QGroupBox("Progress")
        prog_layout = QVBoxLayout(prog_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #333;border-radius:4px;text-align:center;height:20px;}"
            "QProgressBar::chunk{background:#007acc;border-radius:3px;}"
        )
        self.current_file_label = QLabel("—")
        self.current_file_label.setStyleSheet("color:#888;font-size:11px;")
        prog_layout.addWidget(self.progress_bar)
        prog_layout.addWidget(self.current_file_label)
        root.addWidget(prog_group)

        # Buttons
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶  Start Transfer")
        self.start_btn.setFixedHeight(36)
        self.start_btn.setStyleSheet(
            "background:#007acc;color:white;font-weight:bold;border-radius:4px;"
        )
        self.start_btn.clicked.connect(self._start_transfer)

        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_transfer)

        self.manifest_btn = QPushButton("📋  Generate Manifest Only")
        self.manifest_btn.setFixedHeight(36)
        self.manifest_btn.clicked.connect(self._generate_manifest_only)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.manifest_btn)
        root.addLayout(btn_row)

        # Log
        self.log = LogWidget(self)
        self.log.setMinimumHeight(180)
        root.addWidget(self.log)

    def _conflict_handler_str(self):
        return {0: "skip", 1: "overwrite", 2: "rename"}[self.conflict_combo.currentIndex()]

    def _update_preflight(self):
        src = self.src_input.text()
        dst = self.dst_input.text()
        if not src or not dst:
            return
        try:
            src_path = Path(src)
            if not src_path.exists():
                return
            total = folder_size(src_path)
            secs  = estimate_time_seconds(total)
            h = int(secs // 3600)
            m = int((secs % 3600) // 60)
            s = int(secs % 60)
            est = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

            dst_path = Path(dst)
            free = free_space(dst_path) if dst_path.exists() else None
            free_str = f" | Dest free: {format_bytes(free)}" if free else ""

            gdrive_warn = ""
            if is_gdrive_url(dst) and total > GDRIVE_DAILY_LIMIT_BYTES:
                gdrive_warn = "  ⚠ Exceeds 750 GB GDrive daily limit!"

            self.preflight_label.setText(
                f"Size: {format_bytes(total)}  |  Est. time: {est}{free_str}{gdrive_warn}"
            )
        except Exception:
            pass

    def _start_transfer(self):
        src = self.src_input.text()
        dst = self.dst_input.text()
        if not src or not dst:
            QMessageBox.warning(self, "Missing Input",
                                "Please enter both source and destination.")
            return

        if not check_and_prompt(self):
            return

        gdrive_mode = is_gdrive_url(src) or is_gdrive_url(dst)

        # Pre-flight
        try:
            pre_flight_checks(
                src, dst,
                is_gdrive_dest=is_gdrive_url(dst),
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

        # Same-name folder dialogue
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

        # Wire up worker thread
        self._thread = QThread()
        self._worker = TransferWorker(
            src, dst,
            gdrive_mode=gdrive_mode,
            conflict_handler=self._conflict_handler_str(),
            extract_zips=self.extract_zip_chk.isChecked(),
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
        self.progress_bar.setValue(0)

        start_session()
        self._thread.start()

    def _cancel_transfer(self):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()
            end_session()
            self.log.log("Transfer cancelled by user.", "warning")
            self._reset_controls()

    def _on_progress(self, pct, filename):
        self.progress_bar.setValue(pct)
        self.current_file_label.setText(filename)

    def _on_finished(self, result):
        end_session()
        self._reset_controls()
        self.progress_bar.setValue(100)
        errors = result.get("errors", [])
        if errors:
            self.log.log(f"Transfer complete with {len(errors)} error(s).", "warning")
        else:
            self.log.log(f"Transfer complete  {result.get('actual_dest', '')}", "success")
        self.src_input.add_to_recent(self.src_input.text())
        self.dst_input.add_to_recent(self.dst_input.text())
        self._write_txt_log(result)

    def _on_error(self, msg):
        end_session()
        self._reset_controls()
        self.log.log(f"FATAL: {msg}", "error")
        QMessageBox.critical(self, "Transfer Failed", msg)

    def _reset_controls(self):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.current_file_label.setText("—")

    def _generate_manifest_only(self):
        src = self.src_input.text()
        if not src or not Path(src).exists():
            QMessageBox.warning(self, "No Source", "Enter a valid source folder first.")
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

    def _write_txt_log(self, result):
        import getpass, socket
        from datetime import datetime
        log_dir = Path.home() / "Documents" / "STSyncTool" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"transfer_{ts}.txt"
        lines = [
            "=" * 60,
            "SIGNAL THEORY -- ST SyncTool Transfer Log",
            f"Date/Time  : {datetime.now().isoformat()}",
            f"Workstation: {socket.gethostname()}",
            f"User       : {getpass.getuser()}",
            f"Source     : {result.get('manifest', {}).get('source_root', '')}",
            f"Destination: {result.get('actual_dest', '')}",
            f"Same-name merge: {result.get('same_name', False)}",
            "=" * 60, "", "FILES TRANSFERRED:",
        ]
        for fname, fdata in result.get("manifest", {}).get("files", {}).items():
            src_cs = fdata.get("source_checksums", {}).get("sha256", "N/A")
            dst_cs = fdata.get("dest_checksums",   {}).get("sha256", "N/A")
            lines += [
                f"  {fname}",
                f"    Size       : {format_bytes(fdata.get('size', 0))}",
                f"    SHA-256 src: {src_cs}",
                f"    SHA-256 dst: {dst_cs}",
                f"    Verified   : {fdata.get('verified', False)}",
            ]
        if result.get("errors"):
            lines += ["", "ERRORS:"]
            for e in result["errors"]:
                lines.append(f"  {e['file']} -- {e['error']}")
        lines += ["", "END OF LOG"]
        log_path.write_text("\\n".join(lines))
        self.log.log(f"  Log saved: {log_path}", "info")


def _resolve_dest_info(src: Path, dst: Path):
    from core.transfer import resolve_folder_conflict
    return resolve_folder_conflict(src, dst)
'''

# ─────────────────────────────────────────────────────────────────────────────
# gui/merge_tab.py
# ─────────────────────────────────────────────────────────────────────────────
files["gui/merge_tab.py"] = '''
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject

from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
from gui.diff_table import DiffTable
from core.manifest import generate_manifest, load_manifest
from core.comparison import three_way_diff, DiffState
from core.amphetamine import check_and_prompt, start_session, end_session


class ScanWorker(QObject):
    progress = pyqtSignal(int, str)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(dict, dict, dict)
    error    = pyqtSignal(str)

    def __init__(self, base_manifest_path, local_path, server_path):
        super().__init__()
        self.base_manifest_path = Path(base_manifest_path) if base_manifest_path else None
        self.local_path         = Path(local_path)
        self.server_path        = server_path

    def run(self):
        try:
            if self.base_manifest_path and self.base_manifest_path.exists():
                base = load_manifest(self.base_manifest_path)
                self.log.emit(f"Loaded base manifest: {self.base_manifest_path.name}", "info")
            else:
                auto = self.local_path / "st_manifest.json"
                if auto.exists():
                    base = load_manifest(auto)
                    self.log.emit("Auto-loaded base manifest from local folder.", "info")
                else:
                    self.log.emit("No base manifest found -- treating local as base.", "warning")
                    base = {"files": {}}

            self.log.emit("Scanning local folder...", "info")
            yours = generate_manifest(
                self.local_path, label="yours",
                progress_cb=lambda p, f: self.progress.emit(p // 2, f),
            )

            self.log.emit("Scanning server...", "info")
            sp = str(self.server_path)
            if sp.startswith("/") or (len(sp) > 1 and sp[1] == ":"):
                server = generate_manifest(
                    Path(sp), label="server",
                    progress_cb=lambda p, f: self.progress.emit(50 + p // 2, f),
                )
            else:
                from core.rclone_bridge import lsjson_to_manifest
                server = lsjson_to_manifest(sp, label="server")
                self.log.emit("Server manifest built via rclone.", "info")

            self.finished.emit(base, yours, server)
        except Exception as e:
            self.error.emit(str(e))


class ApplyWorker(QObject):
    log      = pyqtSignal(str, str)
    progress = pyqtSignal(int, str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, actions, local_path, server_path, yours_manifest, server_manifest):
        super().__init__()
        self.actions       = actions
        self.local_path    = Path(local_path)
        self.server_path   = server_path
        self.yours         = yours_manifest
        self.server        = server_manifest

    def run(self):
        import shutil
        try:
            total = len(self.actions)
            for i, (rel_path, action) in enumerate(self.actions.items()):
                self.progress.emit(int(i / total * 100), rel_path)
                if action in ("Skip", "-- choose --", "Keep Local", ""):
                    continue
                local_file  = self.local_path / rel_path
                server_file = Path(self.server_path) / rel_path

                if action == "Keep Server" and server_file.exists():
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(server_file, local_file)
                    self.log.emit(f"  <- Pulled from server: {rel_path}", "success")
                elif action == "Delete":
                    if local_file.exists():
                        local_file.unlink()
                        self.log.emit(f"  Deleted: {rel_path}", "warning")
                elif action == "Manual Merge":
                    self._open_merge_tool(rel_path, local_file, server_file)

            self.progress.emit(100, "Done")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def _open_merge_tool(self, rel_path, local_file, server_file):
        import subprocess
        for tool in ["code", "meld", "kdiff3", "opendiff"]:
            try:
                if tool == "code":
                    subprocess.Popen([tool, "--diff", str(local_file), str(server_file)])
                else:
                    subprocess.Popen([tool, str(local_file), str(server_file)])
                self.log.emit(f"  Opened {tool} for: {rel_path}", "info")
                return
            except FileNotFoundError:
                continue
        self.log.emit(f"  No merge tool found for: {rel_path}", "error")


class MergeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_manifest   = None
        self._yours_manifest  = None
        self._server_manifest = None
        self._diff_results    = []
        self._scan_thread     = None
        self._apply_thread    = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        input_group = QGroupBox("Paths")
        ig = QVBoxLayout(input_group)

        brow = QHBoxLayout()
        brow.addWidget(QLabel("Base Manifest (.json):"))
        self.base_input = PathInputWidget("base_manifest", self)
        self.base_input.browse_btn.clicked.disconnect()
        self.base_input.browse_btn.clicked.connect(self._browse_manifest)
        self.base_input.input.setPlaceholderText(
            "Optional -- auto-detects st_manifest.json in local folder"
        )
        brow.addWidget(self.base_input)
        ig.addLayout(brow)

        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Local Folder (Yours):  "))
        self.local_input = PathInputWidget("merge_local", self)
        lrow.addWidget(self.local_input)
        ig.addLayout(lrow)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Server Folder (Theirs):"))
        self.server_input = PathInputWidget("merge_server", self)
        self.server_input.input.setPlaceholderText(
            "/Volumes/NAS/project  or  nas_remote:/project"
        )
        srow.addWidget(self.server_input)
        ig.addLayout(srow)

        root.addWidget(input_group)

        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("  Scan & Compare")
        self.scan_btn.setFixedHeight(36)
        self.scan_btn.setStyleSheet(
            "background:#2d6a9f;color:white;font-weight:bold;border-radius:4px;"
        )
        self.scan_btn.clicked.connect(self._run_scan)

        self.apply_btn = QPushButton("  Apply Selected Actions")
        self.apply_btn.setFixedHeight(36)
        self.apply_btn.setStyleSheet(
            "background:#3a7d44;color:white;font-weight:bold;border-radius:4px;"
        )
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply_actions)

        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.apply_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #333;border-radius:4px;text-align:center;height:18px;}"
            "QProgressBar::chunk{background:#2d6a9f;border-radius:3px;}"
        )
        self.status_label = QLabel("--")
        self.status_label.setStyleSheet("color:#888;font-size:11px;")
        root.addWidget(self.progress_bar)
        root.addWidget(self.status_label)

        self.diff_table = DiffTable(self)
        root.addWidget(self.diff_table, stretch=1)

        self.log = LogWidget(self)
        self.log.setMaximumHeight(160)
        root.addWidget(self.log)

    def _browse_manifest(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Base Manifest", "", "JSON Files (*.json)"
        )
        if path:
            self.base_input.setText(path)

    def _run_scan(self):
        local  = self.local_input.text()
        server = self.server_input.text()
        if not local or not server:
            QMessageBox.warning(self, "Missing Input",
                                "Enter both Local and Server folder paths.")
            return
        if not check_and_prompt(self):
            return

        self.scan_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.progress_bar.setValue(0)

        self._scan_thread = QThread()
        self._scan_worker = ScanWorker(
            self.base_input.text() or None, local, server
        )
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(
            lambda p, f: (self.progress_bar.setValue(p), self.status_label.setText(f))
        )
        self._scan_worker.log.connect(self.log.log)
        self._scan_worker.finished.connect(self._on_scan_complete)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.error.connect(self._scan_thread.quit)

        start_session()
        self._scan_thread.start()

    def _on_scan_complete(self, base, yours, server):
        end_session()
        self._base_manifest   = base
        self._yours_manifest  = yours
        self._server_manifest = server

        results  = three_way_diff(base, yours, server)
        self._diff_results = results
        visible  = [r for r in results if r.state.name != "UNCHANGED"]
        self.diff_table.load_results(visible)

        total     = len(results)
        changed   = len(visible)
        conflicts = sum(1 for r in results if r.state.name == "BOTH_CHANGED")
        self.log.log(
            f"Scan complete -- {total} files, {changed} differences, {conflicts} conflicts.",
            "success" if conflicts == 0 else "warning",
        )
        self.progress_bar.setValue(100)
        self.status_label.setText(f"{changed} differences found")
        self.scan_btn.setEnabled(True)
        self.apply_btn.setEnabled(changed > 0)

    def _on_scan_error(self, msg):
        end_session()
        self.scan_btn.setEnabled(True)
        self.log.log(f"Scan error: {msg}", "error")
        QMessageBox.critical(self, "Scan Error", msg)

    def _apply_actions(self):
        actions = self.diff_table.get_actions()
        actionable = {
            p: a for p, a in actions.items()
            if a not in ("Skip", "-- choose --")
        }
        if not actionable:
            QMessageBox.information(self, "Nothing To Do",
                                    "No actions selected in the table.")
            return

        confirm = QMessageBox.question(
            self, "Confirm Apply",
            f"Apply {len(actionable)} action(s)? This will modify files on disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.apply_btn.setEnabled(False)
        self._apply_thread = QThread()
        self._apply_worker = ApplyWorker(
            actionable,
            self.local_input.text(),
            self.server_input.text(),
            self._yours_manifest,
            self._server_manifest,
        )
        self._apply_worker.moveToThread(self._apply_thread)
        self._apply_thread.started.connect(self._apply_worker.run)
        self._apply_worker.progress.connect(
            lambda p, f: (self.progress_bar.setValue(p), self.status_label.setText(f))
        )
        self._apply_worker.log.connect(self.log.log)
        self._apply_worker.finished.connect(self._on_apply_done)
        self._apply_worker.error.connect(lambda e: self.log.log(e, "error"))
        self._apply_worker.finished.connect(self._apply_thread.quit)

        start_session()
        self._apply_thread.start()

    def _on_apply_done(self):
        end_session()
        self.apply_btn.setEnabled(True)
        self.log.log("All selected actions applied.", "success")
        self.progress_bar.setValue(100)
'''

# ─────────────────────────────────────────────────────────────────────────────
# gui/verify_tab.py
# ─────────────────────────────────────────────────────────────────────────────
files["gui/verify_tab.py"] = '''
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


class VerifyWorker(QObject):
    progress = pyqtSignal(int, str)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, folder, manifest):
        super().__init__()
        self.folder   = Path(folder)
        self.manifest = manifest

    def run(self):
        try:
            files = self.manifest.get("files", {})
            total = len(files)
            results = []

            for i, (rel_path, entry) in enumerate(files.items()):
                self.progress.emit(int(i / total * 100), rel_path)
                abs_path = self.folder / rel_path

                if not abs_path.exists():
                    results.append({"path": rel_path, "status": "MISSING",
                                    "detail": "File not found on disk"})
                    self.log.emit(f"  MISSING: {rel_path}", "error")
                    continue

                expected_cs = entry.get("checksums", {})
                algo = ("sha256" if "sha256" in expected_cs else
                        "xxhash3_64" if "xxhash3_64" in expected_cs else "md5")
                actual = compute_all(
                    abs_path,
                    include_xxhash=(algo == "xxhash3_64"),
                    include_md5=(algo == "md5"),
                )
                expected_val = expected_cs.get(algo, "")
                actual_val   = actual.get(algo, "")

                if expected_val == actual_val:
                    results.append({"path": rel_path, "status": "OK",
                                    "detail": f"{algo}: {actual_val[:16]}..."})
                    self.log.emit(f"  OK: {rel_path}", "success")
                else:
                    results.append({"path": rel_path, "status": "MISMATCH",
                                    "detail": f"Expected {expected_val[:16]}... | Got {actual_val[:16]}..."})
                    self.log.emit(f"  MISMATCH: {rel_path}", "error")

            self.progress.emit(100, "Complete")
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


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
        frow.addWidget(self.folder_input)
        ig.addLayout(frow)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Manifest File (.json):"))
        self.manifest_input = PathInputWidget("verify_manifest", self)
        self.manifest_input.browse_btn.clicked.disconnect()
        self.manifest_input.browse_btn.clicked.connect(self._browse_manifest)
        self.manifest_input.input.setPlaceholderText(
            "Optional -- auto-detects st_manifest.json in folder"
        )
        mrow.addWidget(self.manifest_input)
        ig.addLayout(mrow)

        root.addWidget(input_group)

        btn_row = QHBoxLayout()
        self.verify_btn = QPushButton("  Run Verification")
        self.verify_btn.setFixedHeight(36)
        self.verify_btn.setStyleSheet(
            "background:#6a3d9f;color:white;font-weight:bold;border-radius:4px;"
        )
        self.verify_btn.clicked.connect(self._run_verify)
        btn_row.addWidget(self.verify_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setStyleSheet(
            "QProgressBar{border:1px solid #333;border-radius:4px;text-align:center;height:18px;}"
            "QProgressBar::chunk{background:#6a3d9f;border-radius:3px;}"
        )
        self.status_label = QLabel("--")
        self.status_label.setStyleSheet("color:#888;font-size:11px;")
        root.addWidget(self.progress_bar)
        root.addWidget(self.status_label)

        results_label = QLabel("Results Log:")
        results_label.setStyleSheet("font-weight:bold;color:#aaa;")
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
            QMessageBox.warning(self, "No Folder", "Please select a folder to verify.")
            return

        folder = Path(folder_str)
        if not folder.exists():
            QMessageBox.critical(self, "Folder Not Found",
                                 f"Folder does not exist:\\n{folder}")
            return

        if not manifest_str:
            auto = folder / "st_manifest.json"
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
                                 f"Could not load manifest:\\n{e}")
            return

        file_count = len(self._manifest.get("files", {}))
        self.log.log(f"Starting verification of {file_count} files...", "info")
        self.results_log.clear_log()
        self.progress_bar.setValue(0)
        self.verify_btn.setEnabled(False)

        self._thread = QThread()
        self._worker = VerifyWorker(folder, self._manifest)
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
        log_path.write_text("\\n".join(lines))
        self.log.log(f"  Report saved: {log_path}", "info")
'''

# ─────────────────────────────────────────────────────────────────────────────
# gui/main_window.py
# ─────────────────────────────────────────────────────────────────────────────
files["gui/main_window.py"] = '''
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QStatusBar, QLabel
)
from PyQt6.QtGui import QFont

from gui.transfer_tab import TransferTab
from gui.merge_tab    import MergeTab
from gui.verify_tab   import VerifyTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST SyncTool -- Signal Theory")
        self.setMinimumSize(1100, 780)
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(0)

        # Header
        header = QHBoxLayout()
        title = QLabel("ST SyncTool")
        title.setFont(QFont("SF Pro Display", 18, QFont.Weight.Bold))
        title.setStyleSheet("color:#ffffff;letter-spacing:1px;")
        subtitle = QLabel("Signal Theory Productions")
        subtitle.setStyleSheet("color:#555;font-size:12px;margin-left:10px;")
        version = QLabel("v1.0.0")
        version.setStyleSheet("color:#444;font-size:11px;")
        header.addWidget(title)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(version)
        root.addLayout(header)
        root.addSpacing(8)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                background: #1e1e1e;
            }
            QTabBar::tab {
                background: #2a2a2a;
                color: #888;
                padding: 8px 22px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 13px;
            }
            QTabBar::tab:selected {
                background: #007acc;
                color: white;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected {
                background: #3a3a3a;
                color: #ccc;
            }
        """)

        self.tabs.addTab(TransferTab(self), "Transfer")
        self.tabs.addTab(MergeTab(self),    "Merge")
        self.tabs.addTab(VerifyTab(self),   "Verify")
        root.addWidget(self.tabs)

        # Status bar
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(
            "color:#555;font-size:11px;background:#1a1a1a;"
        )
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
'''

# ─────────────────────────────────────────────────────────────────────────────
# Write all files
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    for rel_path, content in files.items():
        target = Path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Strip the leading newline from the triple-quoted strings
        target.write_text(content.lstrip("\n"))
        print(f"  wrote  {rel_path}")

    print()
    print("=" * 44)
    print("All four GUI files written successfully.")
    print()
    print("Run the app:")
    print("  python main.py")
    print("=" * 44)
