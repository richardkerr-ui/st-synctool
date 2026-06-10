from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QCheckBox, QComboBox,
    QMessageBox, QSizePolicy, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont
from pathlib import Path

from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
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


class TransferWorker(QObject):
    # progress carries (pct: int, info: object) where info is either a plain str
    # (local transfers) or a dict with keys: line, speed, eta, files_done,
    # files_total, current_file (rclone transfers).
    progress = pyqtSignal(int, object)
    log      = pyqtSignal(str, str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, src, dst, gdrive_mode, mirror_mode, paranoid_mode, conflict_handler, extract_zips):
        super().__init__()
        self.src              = src
        self.dst              = dst
        self.gdrive_mode      = gdrive_mode
        self.mirror_mode      = mirror_mode
        self.paranoid_mode    = paranoid_mode
        self.conflict_handler = conflict_handler
        self.extract_zips     = extract_zips

    def run(self):
        try:
            result = route_transfer(
                self.src, self.dst,
                gdrive_mode=self.gdrive_mode,
                mirror_mode=self.mirror_mode,
                paranoid_verify=self.paranoid_mode,
                log_cb=lambda m, l: self.log.emit(m, l),
                progress_cb=lambda p, f: self.progress.emit(p, f),
                conflict_handler=self.conflict_handler,
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
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ── Source & Destination ─────────────────────────────────────────────
        io_group = QGroupBox("Source && Destination")
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
            lbl.setStyleSheet(f"font-size:11px; color:#555; background:transparent;")
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
        opts_group = QGroupBox("Options")
        opts_layout = QVBoxLayout(opts_group)

        opts_row1 = QHBoxLayout()
        opts_row1.addWidget(QLabel("On conflict:"))
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItems(["Skip existing", "Overwrite", "Rename copy"])
        self.conflict_combo.setCurrentIndex(1)
        opts_row1.addWidget(self.conflict_combo)
        opts_row1.addSpacing(20)
        self.extract_zip_chk = QCheckBox("Auto-extract multipart .zips after transfer")
        opts_row1.addWidget(self.extract_zip_chk)
        opts_row1.addSpacing(20)
        self.paranoid_chk = QCheckBox("Paranoid verification")
        self.paranoid_chk.setStyleSheet(f"color:{theme.TEXT_MUTED};")
        opts_row1.addWidget(self.paranoid_chk)
        opts_row1.addStretch()
        opts_layout.addLayout(opts_row1)

        danger = QFrame()
        danger.setObjectName("DangerZone")
        danger.setStyleSheet(
            "QFrame#DangerZone {"
            "  background:#2a1515; border:1px solid #5a2020; border-radius:6px;"
            "}"
            "QCheckBox { color:#A32D2D; }"
        )
        dl = QHBoxLayout(danger)
        dl.setContentsMargins(10, 6, 10, 6)
        dl.setSpacing(8)
        warn_icon = QLabel("⚠")
        warn_icon.setStyleSheet("color:#A32D2D; font-size:13px; background:transparent;")
        dl.addWidget(warn_icon)
        self.mirror_chk = QCheckBox(
            "Mirror mode — deletes files at destination not present in source"
        )
        dl.addWidget(self.mirror_chk)
        dl.addStretch()
        opts_layout.addWidget(danger)

        root.addWidget(opts_group)

        # ── Action row ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.start_btn = QPushButton("▶  Start Transfer")
        self.start_btn.setFixedHeight(36)
        self.start_btn.setStyleSheet(theme.primary_button_style())
        self.start_btn.clicked.connect(self._start_transfer)

        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_transfer)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:12px;")

        self.manifest_btn = QPushButton("📋  Generate Manifest Only")
        self.manifest_btn.setFixedHeight(36)
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

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        btn_row.addWidget(self.manifest_btn)
        root.addLayout(btn_row)

        # ── Log panel (includes inline progress bar) ─────────────────────────
        self.log = LogWidget("Transfer log", with_progress=True, parent=self)
        self.log.setMinimumHeight(180)
        root.addWidget(self.log)

        # Convenience aliases so progress-related methods need no changes
        self.progress_bar       = self.log.progress_bar
        self.current_file_label = self.log.current_file_label

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
        src = self.src_input.text()
        dst = self.dst_input.text()
        if not src or not dst:
            QMessageBox.warning(self, "Missing Input",
                                "Please enter both source and destination.")
            return

        if not check_and_prompt(self):
            return

        src_is_url = is_gdrive_url(src)
        dst_is_url = is_gdrive_url(dst)
        gdrive_mode = src_is_url or dst_is_url
        mirror_mode = self.mirror_chk.isChecked()

        if src_is_url and dst_is_url:
            QMessageBox.critical(
                self, "Unsupported",
                "Drive-to-Drive transfers aren't supported yet. "
                "Sync down to a local folder first, then up to the destination."
            )
            return

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
        self._status_label.setText("Transferring…")
        self.log.set_progress(0, current_file="Starting…")

        start_session()
        self._thread.start()

    def _cancel_transfer(self):
        if self._thread and self._thread.isRunning():
            # Kill the rclone subprocess if one is running
            killed = rclone_bridge.cancel_current()
            if killed:
                self.log.log("rclone subprocess terminated.", "warning")
            self._thread.quit()
            self._thread.wait(3000)
            end_session()
            self.log.log("Transfer cancelled by user.", "warning")
            self._reset_controls()

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
        end_session()
        self.log.set_progress(100, current_file="Complete")
        self._reset_controls()
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
        self._status_label.setText("Ready")
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
            "=" * 60,
        ]
        manifest = result.get("manifest", {})
        counts = manifest.get("status_counts")
        vmethod = manifest.get("verification_method", "")
        vfailures = manifest.get("verify_failures", [])
        if counts or vmethod:
            lines += ["", "SUMMARY:"]
            if vmethod == "paranoid":
                lines.append("  Verification: Paranoid (independent SHA-256 on source and destination)")
            elif vmethod == "rclone-checksum":
                lines.append("  Verification: rclone --checksum (source vs destination compared at transfer time)")
            if vfailures:
                lines.append(f"  VERIFICATION FAILURES: {len(vfailures)}")
            if counts:
                lines += [
                    f"  Uploaded : {counts.get('uploaded', 0)}",
                    f"  Updated  : {counts.get('updated', 0)}",
                    f"  Unchanged: {counts.get('unchanged', 0)}",
                    f"  Deleted  : {counts.get('deleted', 0)}",
                ]
        lines += ["", "FILES IN DESTINATION (POST-TRANSFER):"]
        for fname, fdata in manifest.get("files", {}).items():
            src_block = fdata.get("source_checksums", {}) or {}
            dst_block = fdata.get("dest_checksums",   {}) or {}
            algo = "SHA-256" if src_block.get("sha256") or dst_block.get("sha256") else "MD5"
            key  = "sha256"  if algo == "SHA-256" else "md5"
            src_cs = src_block.get(key, "N/A")
            dst_cs = dst_block.get(key, "N/A")
            status = fdata.get("status", "verified")
            lines += [
                f"  {fname}  [{status.upper()}]",
                f"    Size       : {format_bytes(fdata.get('size', 0))}",
                f"    {algo} src : {src_cs}",
                f"    {algo} dst : {dst_cs}",
                f"    Verified   : {fdata.get('verified', False)}",
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
