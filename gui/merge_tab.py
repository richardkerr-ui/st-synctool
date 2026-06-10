import shutil
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QFileDialog, QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject

from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
from gui.diff_table import DiffTable
from core.manifest import generate_manifest, generate_manifest_fast, load_manifest, save_manifest, MANIFEST_FILENAME
from core.comparison import three_way_diff, DiffState
from core.amphetamine import check_and_prompt, start_session, end_session
from core import merge_ops, rclone_bridge
from core.merge_ops import (
    ACT_PUSH, ACT_PULL, ACT_DELETE_LOCAL, ACT_DELETE_SERVER, ACT_SKIP
)
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone
from gui import theme


def _build_server_manifest(server_path: str, base_manifest=None, log_cb=None, progress_cb=None):
    """Return a manifest for the server side, routing through rclone for GDrive URLs
    or generate_manifest_fast for local paths."""
    if is_gdrive_url(server_path):
        if log_cb: log_cb("Server is Google Drive — fetching via rclone lsjson...", "info")
        remote, flags = gdrive_url_to_rclone(server_path)
        return rclone_bridge.lsjson_to_manifest(remote, extra_flags=flags, label="server")
    p = Path(server_path)
    if not p.exists():
        raise RuntimeError(f"Server path does not exist: {server_path}")
    if log_cb: log_cb(f"Scanning local server path: {p}", "info")
    return generate_manifest_fast(p, base_manifest=base_manifest, label="server",
                                  progress_cb=progress_cb)


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
            # Load base manifest
            if self.base_manifest_path and self.base_manifest_path.exists():
                base = load_manifest(self.base_manifest_path)
                self.log.emit(f"Loaded base manifest: {self.base_manifest_path.name}", "info")
            else:
                auto = self.local_path / MANIFEST_FILENAME
                if auto.exists():
                    base = load_manifest(auto)
                    self.log.emit("Auto-loaded base manifest from local folder.", "info")
                else:
                    self.log.emit("No base manifest found — treating local as base.", "warning")
                    base = {"files": {}}

            # Scan local (fast — pre-filter on modtime+size vs base)
            self.log.emit("Scanning local folder...", "info")
            yours = generate_manifest_fast(
                self.local_path, base_manifest=base, label="yours",
                progress_cb=lambda p, f: self.progress.emit(p // 2, f),
            )
            stats = yours.get("scan_stats", {})
            self.log.emit(
                f"  Local scan: {stats.get('reused_from_base', 0)} reused, "
                f"{stats.get('rehashed', 0)} re-hashed",
                "info",
            )

            # Scan server (rclone or local)
            self.log.emit("Scanning server...", "info")
            server = _build_server_manifest(
                self.server_path, base_manifest=base,
                log_cb=lambda m, l: self.log.emit(m, l),
                progress_cb=lambda p, f: self.progress.emit(50 + p // 2, f),
            )
            sstats = server.get("scan_stats", {})
            if sstats:
                self.log.emit(
                    f"  Server scan: {sstats.get('reused_from_base', 0)} reused, "
                    f"{sstats.get('rehashed', 0)} re-hashed",
                    "info",
                )

            self.finished.emit(base, yours, server)
        except Exception as e:
            self.error.emit(str(e))


class ApplyWorker(QObject):
    progress = pyqtSignal(int, str)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)
    rescan_conflict = pyqtSignal(list)  # list of paths whose state changed during rescan

    def __init__(self, actions, local_path, server_path, base_manifest,
                 yours_manifest, server_manifest,
                 preserve_on_overwrite, rescan_before_apply):
        super().__init__()
        self.actions     = actions
        self.local_path  = Path(local_path)
        self.server_path = server_path
        self.base        = base_manifest
        self.yours       = yours_manifest
        self.server      = server_manifest
        self.preserve    = preserve_on_overwrite
        self.rescan      = rescan_before_apply

    def run(self):
        try:
            log = lambda m, l="info": self.log.emit(m, l)

            # Paranoid pre-apply re-scan
            if self.rescan:
                log("Re-scanning both sides before apply (paranoid check)...", "info")
                self.progress.emit(0, "Re-scanning local...")
                fresh_yours = generate_manifest_fast(
                    self.local_path, base_manifest=self.base, label="yours_recheck",
                )
                self.progress.emit(20, "Re-scanning server...")
                fresh_server = _build_server_manifest(
                    self.server_path, base_manifest=self.base,
                    log_cb=lambda m, l: self.log.emit(m, l),
                )
                fresh_results = three_way_diff(self.base, fresh_yours, fresh_server)
                fresh_state_by_path = {r.path: r.state.name for r in fresh_results}

                # Build a snapshot of original states for the actionable paths
                original_state_by_path = {}
                for r in three_way_diff(self.base, self.yours, self.server):
                    original_state_by_path[r.path] = r.state.name

                conflicts = []
                for path in self.actions:
                    orig = original_state_by_path.get(path, "DELETED_BOTH")
                    fresh = fresh_state_by_path.get(path, "DELETED_BOTH")
                    if orig != fresh:
                        conflicts.append((path, orig, fresh))

                if conflicts:
                    log(f"  {len(conflicts)} file(s) changed since initial scan — aborting apply",
                        "error")
                    for path, orig, fresh in conflicts[:10]:
                        log(f"    {path}: was {orig}, now {fresh}", "warning")
                    self.rescan_conflict.emit([c[0] for c in conflicts])
                    return
                log("  No drift detected — proceeding with apply", "success")

            # Execute actions
            total = max(len(self.actions), 1)
            results = {"success": [], "failed": [], "skipped": []}

            for i, (rel_path, action) in enumerate(self.actions.items()):
                self.progress.emit(int(20 + i / total * 70), f"{action}: {rel_path}")
                if action in (ACT_SKIP, ""):
                    results["skipped"].append(rel_path)
                    continue
                ok = False
                if action == ACT_PUSH:
                    ok = merge_ops.push_file(
                        rel_path, self.local_path, self.server_path,
                        preserve_on_overwrite=self.preserve, log_cb=log)
                elif action == ACT_PULL:
                    ok = merge_ops.pull_file(
                        rel_path, self.local_path, self.server_path,
                        preserve_on_overwrite=self.preserve, log_cb=log)
                elif action == ACT_DELETE_LOCAL:
                    ok = merge_ops.delete_local(rel_path, self.local_path, log_cb=log)
                elif action == ACT_DELETE_SERVER:
                    ok = merge_ops.delete_server(rel_path, self.server_path, log_cb=log)
                else:
                    log(f"  Unknown action {action!r} for {rel_path} — skipping", "warning")
                    results["skipped"].append(rel_path)
                    continue
                (results["success"] if ok else results["failed"]).append(rel_path)

            # Regenerate manifest + push to both sides
            self.progress.emit(92, "Regenerating manifest...")
            log("Regenerating manifest from new local state...", "info")
            new_manifest = generate_manifest_fast(
                self.local_path, base_manifest=self.yours, label="post-merge",
            )
            saved = save_manifest(new_manifest, source_dir=self.local_path,
                                  name_hint=self.local_path.name)
            log(f"  Local manifest saved to {len(saved)} location(s)", "info")

            self.progress.emit(96, "Uploading manifest to server...")
            try:
                manifest_local_path = self.local_path / MANIFEST_FILENAME
                if not manifest_local_path.exists():
                    raise FileNotFoundError(f"Manifest not written: {manifest_local_path}")
                if is_gdrive_url(self.server_path):
                    remote, flags = gdrive_url_to_rclone(self.server_path)
                    rclone_bridge.copyto(
                        str(manifest_local_path), f"{remote}{MANIFEST_FILENAME}",
                        dst_flags=flags, log_cb=log,
                    )
                else:
                    dst = Path(self.server_path) / MANIFEST_FILENAME
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(manifest_local_path, dst)
                log("  Server manifest updated", "success")
            except Exception as e:
                log(f"  Could not update server manifest: {e}", "error")
                self.error.emit(f"Server manifest upload failed: {e}")
                return

            self.progress.emit(100, "Done")
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


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
            "Optional — auto-detects st_manifest.json in local folder"
        )
        brow.addWidget(self.base_input)
        ig.addLayout(brow)

        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Local Folder (Yours):  "))
        self.local_input = PathInputWidget("merge_local", self)
        lrow.addWidget(self.local_input)
        ig.addLayout(lrow)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Server (Theirs):       "))
        self.server_input = PathInputWidget("merge_server", self)
        self.server_input.input.setPlaceholderText(
            "/Volumes/NAS/project  or  https://drive.google.com/drive/folders/..."
        )
        srow.addWidget(self.server_input)
        ig.addLayout(srow)

        root.addWidget(input_group)

        # Options
        opts_group = QGroupBox("Options")
        ol = QVBoxLayout(opts_group)

        self.preserve_chk = QCheckBox(
            "Preserve existing files on overwrite (rename incoming with date-initials suffix)"
        )
        self.preserve_chk.setChecked(True)
        ol.addWidget(self.preserve_chk)

        self.rescan_chk = QCheckBox(
            "Re-scan before apply (catches drift since initial scan)"
        )
        self.rescan_chk.setChecked(True)
        ol.addWidget(self.rescan_chk)

        root.addWidget(opts_group)

        # Buttons
        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("  Scan && Compare")
        self.scan_btn.setFixedHeight(36)
        self.scan_btn.setStyleSheet(theme.primary_button_style())
        self.scan_btn.clicked.connect(self._run_scan)

        self.apply_btn = QPushButton("  Apply Selected Actions")
        self.apply_btn.setFixedHeight(36)
        self.apply_btn.setStyleSheet(theme.success_button_style())
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply_actions)

        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.apply_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_label = QLabel("--")
        self.status_label.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
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
                                "Enter both Local and Server paths.")
            return
        if not check_and_prompt(self):
            return

        self.scan_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.progress_bar.setValue(0)

        self._scan_thread = QThread()
        self._scan_worker = ScanWorker(self.base_input.text() or None, local, server)
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

        results = three_way_diff(base, yours, server)
        self._diff_results = results
        visible = [r for r in results if r.state.name != "UNCHANGED"]
        self.diff_table.load_results(visible)

        total     = len(results)
        changed   = len(visible)
        conflicts = sum(1 for r in results if r.state.name == "BOTH_CHANGED")
        self.log.log(
            f"Scan complete — {total} files, {changed} differences, {conflicts} conflicts.",
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
        actionable = {p: a for p, a in actions.items() if a not in (ACT_SKIP, "")}
        if not actionable:
            QMessageBox.information(self, "Nothing To Do",
                                    "No actions selected.")
            return

        confirm = QMessageBox.question(
            self, "Confirm Apply",
            f"Apply {len(actionable)} action(s)?\n"
            f"Preserve on overwrite: {'ON' if self.preserve_chk.isChecked() else 'OFF'}\n"
            f"Re-scan before apply: {'ON' if self.rescan_chk.isChecked() else 'OFF'}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.apply_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.progress_bar.setValue(0)

        self._apply_thread = QThread()
        self._apply_worker = ApplyWorker(
            actionable,
            self.local_input.text(),
            self.server_input.text(),
            self._base_manifest,
            self._yours_manifest,
            self._server_manifest,
            preserve_on_overwrite=self.preserve_chk.isChecked(),
            rescan_before_apply=self.rescan_chk.isChecked(),
        )
        self._apply_worker.moveToThread(self._apply_thread)
        self._apply_thread.started.connect(self._apply_worker.run)
        self._apply_worker.progress.connect(
            lambda p, f: (self.progress_bar.setValue(p), self.status_label.setText(f))
        )
        self._apply_worker.log.connect(self.log.log)
        self._apply_worker.finished.connect(self._on_apply_done)
        self._apply_worker.rescan_conflict.connect(self._on_rescan_conflict)
        self._apply_worker.error.connect(lambda e: (
            self.log.log(f"Apply error: {e}", "error"),
            QMessageBox.critical(self, "Apply Error", e),
        ))
        self._apply_worker.finished.connect(self._apply_thread.quit)
        self._apply_worker.rescan_conflict.connect(self._apply_thread.quit)
        self._apply_worker.error.connect(self._apply_thread.quit)

        start_session()
        self._apply_thread.start()

    def _on_apply_done(self, results):
        end_session()
        self.apply_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        s = len(results.get("success", []))
        f = len(results.get("failed", []))
        sk = len(results.get("skipped", []))
        self.log.log(
            f"Apply complete — {s} succeeded, {f} failed, {sk} skipped.",
            "success" if f == 0 else "warning",
        )
        self.progress_bar.setValue(100)
        if f == 0:
            QMessageBox.information(self, "Apply Complete",
                                    f"{s} action(s) completed successfully.")
        else:
            QMessageBox.warning(self, "Apply Finished with Errors",
                                f"{s} succeeded, {f} failed. See log for details.")

    def _on_rescan_conflict(self, paths):
        end_session()
        self.apply_btn.setEnabled(False)  # force re-scan
        self.scan_btn.setEnabled(True)
        QMessageBox.warning(
            self, "Files Changed Since Scan",
            f"{len(paths)} file(s) changed since the initial scan.\n"
            "Apply was aborted to prevent overwriting current data.\n\n"
            'Click "Scan & Compare" again to refresh the diff.'
        )
