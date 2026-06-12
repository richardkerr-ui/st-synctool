import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QFileDialog, QMessageBox, QCheckBox,
    QComboBox, QDialog, QListWidget, QListWidgetItem, QTextEdit,
    QDialogButtonBox, QGraphicsOpacityEffect, QFrame,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject

from gui.path_input_widget import PathInputWidget
from gui.log_widget import LogWidget
from gui.diff_table import DiffTable
from core.manifest import (
    generate_manifest_fast, load_manifest, save_manifest,
    MANIFEST_FILENAME, LOCAL_MANIFEST_DIR,
)
from core.comparison import three_way_diff, DiffState
from core.amphetamine import check_and_prompt, start_session, end_session
from core import merge_ops, rclone_bridge
from core.merge_ops import (
    ACT_PUSH, ACT_PULL, ACT_DELETE_LOCAL, ACT_DELETE_SERVER, ACT_SKIP
)
from core import projects as project_registry
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone
from gui import theme


# ── Module-level helpers ──────────────────────────────────────────────────────

def _manifest_age_days_from_iso(iso_str: str) -> int:
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
    except Exception:
        return 0


def _manifest_age_days(path: str) -> int:
    """Days since manifest was created (reads created_at field, falls back to mtime)."""
    try:
        data = json.loads(Path(path).read_text())
        return _manifest_age_days_from_iso(data.get("created_at", ""))
    except Exception:
        pass
    try:
        return int((datetime.now().timestamp() - Path(path).stat().st_mtime) / 86400)
    except Exception:
        return 0


def _fmt_date(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        return datetime.fromisoformat(iso_str).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:16]


def _fmt_size(size_bytes) -> str:
    """Human-readable file size string (e.g. 1.2 GB, 340 MB, 4.0 KB)."""
    if size_bytes is None:
        return "unknown"
    try:
        n = int(size_bytes)
    except (TypeError, ValueError):
        return str(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


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


# ── Dialogs ───────────────────────────────────────────────────────────────────

class ManifestBrowserDialog(QDialog):
    """Lists archived manifests for a project (per-project subdir), sorted newest-first."""

    def __init__(self, project_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Base Manifest")
        self.setMinimumWidth(580)
        self._selected_path = ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Archived manifests for: <b>{project_name}</b>"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._populate(project_name)

    def _populate(self, project_name: str):
        proj_dir = LOCAL_MANIFEST_DIR / project_name
        if not proj_dir.exists():
            self.list_widget.addItem(QListWidgetItem("No archived manifests found."))
            return
        manifests = sorted(
            proj_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for m in manifests:
            days = _manifest_age_days(str(m))
            age = f"{days}d old" if days > 0 else "today"
            item = QListWidgetItem(f"{m.name}  ({age})")
            item.setData(Qt.ItemDataRole.UserRole, str(m))
            self.list_widget.addItem(item)
        if manifests:
            self.list_widget.setCurrentRow(0)

    def _on_accept(self):
        item = self.list_widget.currentItem()
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and Path(path).exists():
                self._selected_path = path
                self.accept()

    def selected_path(self) -> str:
        return self._selected_path


# ── Background workers ────────────────────────────────────────────────────────

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
                    base = {"files": {}, "renames": []}

            # Scan local (fast — pre-filter on modtime+size vs base)
            self.log.emit("Scanning local folder...", "info")
            yours = generate_manifest_fast(
                self.local_path, base_manifest=base, label="yours",
                counterpart_path=self.server_path,
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
    progress        = pyqtSignal(int, str)
    log             = pyqtSignal(str, str)
    finished        = pyqtSignal(dict)
    error           = pyqtSignal(str)
    rescan_conflict = pyqtSignal(list)

    def __init__(self, actions, local_path, server_path, base_manifest,
                 yours_manifest, server_manifest,
                 preserve_on_overwrite, rescan_before_apply, conflict_count=0):
        super().__init__()
        self.actions        = actions
        self.local_path     = Path(local_path)
        self.server_path    = server_path
        self.base           = base_manifest
        self.yours          = yours_manifest
        self.server         = server_manifest
        self.preserve       = preserve_on_overwrite
        self.rescan         = rescan_before_apply
        self.conflict_count = conflict_count

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

                original_state_by_path = {
                    r.path: r.state.name
                    for r in three_way_diff(self.base, self.yours, self.server)
                }

                conflicts = [
                    (path, original_state_by_path.get(path, "DELETED_BOTH"),
                     fresh_state_by_path.get(path, "DELETED_BOTH"))
                    for path in self.actions
                    if original_state_by_path.get(path) != fresh_state_by_path.get(path, "DELETED_BOTH")
                ]

                if conflicts:
                    log(f"  {len(conflicts)} file(s) changed since initial scan — aborting apply",
                        "error")
                    for path, orig, fresh in conflicts[:10]:
                        log(f"    {path}: was {orig}, now {fresh}", "warning")
                    self.rescan_conflict.emit([c[0] for c in conflicts])
                    return
                log("  No drift detected — proceeding with apply", "success")

            # Execute actions — collect rename events (item 13)
            total   = max(len(self.actions), 1)
            results = {"success": [], "failed": [], "skipped": []}
            renames = []
            # MANIFEST-FIX (item 08): capture verified post-copy hashes from merge_ops
            # so they can be merged into the regenerated manifest instead of discarded.
            verified_entries = {}

            for i, (rel_path, action) in enumerate(self.actions.items()):
                self.progress.emit(int(20 + i / total * 70), f"{action}: {rel_path}")
                if action in (ACT_SKIP, ""):
                    results["skipped"].append(rel_path)
                    continue

                op_result = False
                if action == ACT_PUSH:
                    op_result = merge_ops.push_file(
                        rel_path, self.local_path, self.server_path,
                        preserve_on_overwrite=self.preserve, log_cb=log)
                elif action == ACT_PULL:
                    op_result = merge_ops.pull_file(
                        rel_path, self.local_path, self.server_path,
                        preserve_on_overwrite=self.preserve, log_cb=log)
                elif action == ACT_DELETE_LOCAL:
                    op_result = merge_ops.delete_local(rel_path, self.local_path, log_cb=log)
                elif action == ACT_DELETE_SERVER:
                    op_result = merge_ops.delete_server(rel_path, self.server_path, log_cb=log)
                else:
                    log(f"  Unknown action {action!r} for {rel_path} — skipping", "warning")
                    results["skipped"].append(rel_path)
                    continue

                if op_result:
                    results["success"].append(rel_path)
                    if isinstance(op_result, dict):
                        # MANIFEST-FIX (item 08): keep verified post-copy hashes so the
                        # post-merge manifest records the verification that just happened.
                        dest_rel = op_result.get("renamed_to") or rel_path
                        post = op_result.get("post")
                        if post:
                            verified_entries[dest_rel] = {
                                "checksums": post,
                                "hash_algorithm": "sha256",
                                "verification_method": "local-copy",
                            }
                        elif op_result.get("method"):
                            verified_entries[dest_rel] = {
                                "verification_method": op_result.get("method"),
                            }
                        if op_result.get("renamed_to"):
                            renames.append({
                                "from":   rel_path,
                                "to":     op_result["renamed_to"],
                                "reason": "preserve",
                            })
                else:
                    results["failed"].append(rel_path)

            # Regenerate manifest with counterpart_path and renames recorded
            self.progress.emit(92, "Regenerating manifest...")
            log("Regenerating manifest from new local state...", "info")
            new_manifest = generate_manifest_fast(
                self.local_path, base_manifest=self.yours, label="post-merge",
                counterpart_path=self.server_path, operation="post-merge",
            )
            new_manifest["renames"] = renames
            # MANIFEST-FIX (item 08): enrich regenerated entries with the verified
            # hashes captured during apply so they are persisted, not recomputed.
            for rel, extra in verified_entries.items():
                entry = new_manifest["files"].get(rel)
                if entry is not None:
                    if extra.get("checksums"):
                        entry.setdefault("checksums", {}).update(extra["checksums"])
                    if extra.get("hash_algorithm"):
                        entry["hash_algorithm"] = extra["hash_algorithm"]
                    if extra.get("verification_method"):
                        entry["verification_method"] = extra["verification_method"]
            saved = save_manifest(new_manifest, source_dir=self.local_path,
                                  name_hint=self.local_path.name, operation="post-merge")
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

            # Record merge in project registry (item 12)
            project_id = new_manifest.get("project_id", "")
            if project_id:
                archive_path = str(saved[0]) if saved else ""
                try:
                    project_registry.record_merge(
                        project_id,
                        files_changed=len(results["success"]),
                        conflicts=self.conflict_count,
                        preserve_renames=len(renames),
                        manifest_path=archive_path,
                    )
                    if archive_path:
                        project_registry.upsert_project(
                            project_id,
                            local_path=str(self.local_path),
                            server_path=self.server_path,
                            latest_manifest=archive_path,
                        )
                except Exception as e:
                    log(f"  Could not update project registry: {e}", "warning")

            results["renames"] = renames
            self.progress.emit(100, "Done")
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class ServerHealthWorker(QObject):
    result = pyqtSignal(str, str)  # (status: "ok"/"warn"/"error", message)

    def __init__(self, server_path: str, local_path: str):
        super().__init__()
        self.server_path = server_path
        self.local_path  = local_path

    def run(self):
        try:
            # Fetch server manifest
            if is_gdrive_url(self.server_path):
                remote, flags = gdrive_url_to_rclone(self.server_path)
                with tempfile.TemporaryDirectory() as tmpdir:
                    ok = rclone_bridge.copyto(
                        f"{remote}{MANIFEST_FILENAME}",
                        str(Path(tmpdir) / MANIFEST_FILENAME),
                        src_flags=flags,
                    )
                    if not ok:
                        self.result.emit("warn", "Could not fetch server manifest via rclone.")
                        return
                    server_m = load_manifest(Path(tmpdir) / MANIFEST_FILENAME)
            else:
                server_mf = Path(self.server_path) / MANIFEST_FILENAME
                if not server_mf.exists():
                    self.result.emit("warn", f"No manifest at server path: {server_mf}")
                    return
                server_m = load_manifest(server_mf)

            # Load local manifest for comparison
            local_mf = Path(self.local_path) / MANIFEST_FILENAME if self.local_path else None
            if not local_mf or not local_mf.exists():
                s_days = _manifest_age_days_from_iso(server_m.get("created_at", ""))
                self.result.emit("warn",
                    f"Server manifest: {server_m.get('file_count','?')} files, "
                    f"{s_days}d old — no local manifest to compare against.")
                return

            local_m = load_manifest(local_mf)
            s_id = server_m.get("project_id", "")
            l_id = local_m.get("project_id", "")
            s_ts = server_m.get("created_at", "")
            l_ts = local_m.get("created_at", "")
            s_fc = server_m.get("file_count", "?")
            l_fc = local_m.get("file_count", "?")

            if s_id and l_id and s_id != l_id:
                self.result.emit("error",
                    f"Project ID mismatch — server: {s_id}, local: {l_id}. "
                    "These manifests may belong to different projects.")
                return

            s_days = _manifest_age_days_from_iso(s_ts)
            l_days = _manifest_age_days_from_iso(l_ts)

            if s_ts == l_ts:
                self.result.emit("ok",
                    f"Server and local manifests are in sync "
                    f"({s_fc} files, {s_days}d old).")
            elif s_days < l_days:
                self.result.emit("warn",
                    f"Server manifest is NEWER than local "
                    f"(server {s_days}d / local {l_days}d, "
                    f"server {s_fc} files / local {l_fc} files). Re-scan recommended.")
            else:
                self.result.emit("warn",
                    f"Server manifest differs from local "
                    f"(server {s_days}d / local {l_days}d, "
                    f"server {s_fc} files / local {l_fc} files).")
        except Exception as e:
            self.result.emit("error", f"Health check failed: {e}")


# ── MergeTab ──────────────────────────────────────────────────────────────────

class MergeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_manifest    = None
        self._yours_manifest   = None
        self._server_manifest  = None
        self._diff_results     = []
        self._scan_thread      = None
        self._apply_thread     = None
        self._current_project_id = None
        self._detect_timer     = None
        self._build_ui()

    def _build_ui(self):
        """Orchestrates the MergeTab layout. Each section is built by a focused
        sub-builder that sets the relevant self.* attributes."""
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addLayout(self._build_project_row())
        root.addWidget(self._build_paths_group())
        root.addWidget(self._build_options_group())
        self._build_action_row(root)
        root.addWidget(self._build_diff_group(), stretch=1)
        root.addWidget(self._build_conflict_panel())
        self._build_log_panel(root)
        # Wire diff_table → conflict panel after both exist
        self.diff_table.conflict_selected.connect(self._on_conflict_selected)
        self.diff_table.conflict_action_changed.connect(self._on_conflict_action_changed)
        self._refresh_project_combo()

    def _build_project_row(self) -> QHBoxLayout:
        """Quick-load project combo + refresh button."""
        row = QHBoxLayout()
        row.addWidget(QLabel("Quick Load:"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(220)
        self.project_combo.addItem("— select project —")
        self.project_combo.currentIndexChanged.connect(self._on_project_selected)
        row.addWidget(self.project_combo, stretch=1)
        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(32)
        refresh_btn.setToolTip("Refresh project list")
        refresh_btn.clicked.connect(self._refresh_project_combo)
        row.addWidget(refresh_btn)
        row.addStretch()
        return row

    def _build_paths_group(self) -> QGroupBox:
        """Base manifest, local folder, and server path inputs."""
        group = QGroupBox("Paths")
        layout = QVBoxLayout(group)

        brow = QHBoxLayout()
        brow.addWidget(QLabel("Base Manifest (.json):"))
        self.base_input = PathInputWidget("base_manifest", self)
        self.base_input.browse_btn.clicked.disconnect()
        self.base_input.browse_btn.clicked.connect(self._browse_manifest)
        self.base_input.input.setPlaceholderText(
            "Optional — auto-detects st_manifest.json in local folder"
        )
        self.base_input.pathChanged.connect(self._update_stale_badge)
        brow.addWidget(self.base_input)
        self.stale_label = QLabel("")
        self.stale_label.setFixedWidth(90)
        brow.addWidget(self.stale_label)
        layout.addLayout(brow)

        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Local Folder (Yours):  "))
        self.local_input = PathInputWidget("merge_local", self)
        self.local_input.pathChanged.connect(self._on_local_path_changed)
        lrow.addWidget(self.local_input)
        layout.addLayout(lrow)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Server (Theirs):       "))
        self.server_input = PathInputWidget("merge_server", self)
        self.server_input.input.setPlaceholderText(
            "/Volumes/NAS/project  or  https://drive.google.com/drive/folders/..."
        )
        srow.addWidget(self.server_input)
        health_btn = QPushButton("Check")
        health_btn.setFixedWidth(60)
        health_btn.setToolTip("Quick-compare server manifest against local")
        health_btn.clicked.connect(self._check_server_health)
        srow.addWidget(health_btn)
        layout.addLayout(srow)

        return group

    def _build_options_group(self) -> QGroupBox:
        """Preserve-on-overwrite and re-scan checkboxes."""
        group = QGroupBox("Options")
        layout = QVBoxLayout(group)
        self.preserve_chk = QCheckBox(
            "Preserve existing files on overwrite (rename incoming with date-initials suffix)"
        )
        self.preserve_chk.setChecked(True)
        layout.addWidget(self.preserve_chk)
        self.rescan_chk = QCheckBox(
            "Re-scan before apply (catches drift since initial scan)"
        )
        self.rescan_chk.setChecked(True)
        layout.addWidget(self.rescan_chk)
        return group

    def _build_action_row(self, root: QVBoxLayout) -> None:
        """Scan/Apply/Newer-Wins buttons, status label, and progress bar."""
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

        self._apply_opacity = QGraphicsOpacityEffect()
        self._apply_opacity.setOpacity(0.4)
        self.apply_btn.setGraphicsEffect(self._apply_opacity)

        self.newer_wins_btn = QPushButton("Newer Wins")
        self.newer_wins_btn.setFixedHeight(36)
        self.newer_wins_btn.setToolTip(
            "For every conflict row, set the action to Push if local is newer, "
            "Pull if server is newer, or Skip if equal / unknown."
        )
        self.newer_wins_btn.setStyleSheet(
            f"QPushButton {{ background:#3a2a00; color:{theme.ACCENT_GOLD};"
            f"  border:1px solid {theme.ACCENT_GOLD}; border-radius:4px;"
            f"  padding:8px 14px; font-weight:bold; }}"
            f"QPushButton:hover {{ background:#4a3800; }}"
            f"QPushButton:pressed {{ background:#2a1e00; }}"
        )
        self.newer_wins_btn.clicked.connect(self._on_newer_wins)

        self.status_label = QLabel("Scan first to enable apply")
        self.status_label.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:12px;")

        self._unresolved_lbl = QLabel()
        self._unresolved_lbl.setStyleSheet(
            f"color:{theme.ACCENT_CORAL}; font-size:12px; font-weight:bold;"
        )
        self._unresolved_lbl.setVisible(False)

        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.newer_wins_btn)
        btn_row.addWidget(self._unresolved_lbl)
        btn_row.addWidget(self.status_label)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

    def _build_diff_group(self) -> QGroupBox:
        """Changes group box containing the diff table."""
        group = QGroupBox("Changes")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)
        self.diff_table = DiffTable(self)
        layout.addWidget(self.diff_table)
        return group

    def _build_conflict_panel(self) -> QFrame:
        """Side-by-side LOCAL/SERVER detail panel for BOTH_CHANGED rows.
        Hidden by default; shown when a conflict row is selected."""
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet(
            f"QFrame {{ background:#1e1212; border:1px solid #5a2020;"
            f"  border-radius:4px; padding:4px; }}"
            f"QLabel {{ background:transparent; color:#cccccc; }}"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        title = QLabel("Conflict detail")
        title.setStyleSheet(f"font-weight:bold; font-size:12px; color:{theme.CORAL};")
        layout.addWidget(title)

        cols = QHBoxLayout()
        cols.setSpacing(20)

        local_col = QVBoxLayout()
        local_col.setSpacing(2)
        local_col.addWidget(QLabel("<b>LOCAL</b>"))
        self._cp_local_size  = QLabel()
        self._cp_local_mtime = QLabel()
        self._cp_local_hash  = QLabel()
        for lbl in (self._cp_local_size, self._cp_local_mtime, self._cp_local_hash):
            lbl.setStyleSheet("font-family: monospace; font-size: 11px;")
            local_col.addWidget(lbl)
        cols.addLayout(local_col)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color:#5a2020;")
        cols.addWidget(sep)

        server_col = QVBoxLayout()
        server_col.setSpacing(2)
        server_col.addWidget(QLabel("<b>SERVER</b>"))
        self._cp_server_size  = QLabel()
        self._cp_server_mtime = QLabel()
        self._cp_server_hash  = QLabel()
        for lbl in (self._cp_server_size, self._cp_server_mtime, self._cp_server_hash):
            lbl.setStyleSheet("font-family: monospace; font-size: 11px;")
            server_col.addWidget(lbl)
        cols.addLayout(server_col)
        cols.addStretch()
        layout.addLayout(cols)

        self._cp_verdict = QLabel()
        self._cp_verdict.setStyleSheet(
            f"font-weight:bold; font-size:12px; color:{theme.ACCENT_GOLD};"
        )
        layout.addWidget(self._cp_verdict)

        # Quick-action row: resolve this conflict without touching the dropdown
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        keep_local_btn = QPushButton("Keep Local (Push)")
        keep_local_btn.setToolTip("Push the local version to server for this conflict")
        keep_local_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.ACCENT_INFO}; color:#000;"
            f"  border-radius:3px; padding:3px 10px; font-size:11px; }}"
            f"QPushButton:hover {{ background:#4ab0e8; }}"
        )
        keep_local_btn.clicked.connect(lambda: self._cp_keep_local())
        action_row.addWidget(keep_local_btn)

        keep_server_btn = QPushButton("Keep Server (Pull)")
        keep_server_btn.setToolTip("Pull the server version to local for this conflict")
        keep_server_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.ACCENT_GREEN}; color:#000;"
            f"  border-radius:3px; padding:3px 10px; font-size:11px; }}"
            f"QPushButton:hover {{ background:#5ec45e; }}"
        )
        keep_server_btn.clicked.connect(lambda: self._cp_keep_server())
        action_row.addWidget(keep_server_btn)

        action_row.addStretch()

        prev_btn = QPushButton("Prev conflict")
        prev_btn.setToolTip("Go to previous BOTH_CHANGED row")
        prev_btn.setStyleSheet(
            "QPushButton { padding:3px 8px; font-size:11px; }"
        )
        prev_btn.clicked.connect(lambda: self.diff_table.navigate_conflict(-1))
        action_row.addWidget(prev_btn)

        next_btn = QPushButton("Next conflict")
        next_btn.setToolTip("Go to next BOTH_CHANGED row")
        next_btn.setStyleSheet(
            "QPushButton { padding:3px 8px; font-size:11px; }"
        )
        next_btn.clicked.connect(lambda: self.diff_table.navigate_conflict(+1))
        action_row.addWidget(next_btn)

        layout.addLayout(action_row)

        panel.setVisible(False)
        self._conflict_panel = panel
        return panel

    def _build_log_panel(self, root: QVBoxLayout) -> None:
        """Merge log widget and the open-logs-folder link row."""
        self.log = LogWidget("Merge log", parent=self)
        self.log.setMaximumHeight(160)
        root.addWidget(self.log)

        logs_row = QHBoxLayout()
        logs_row.addStretch()
        open_logs_btn = QPushButton("Open logs folder")
        open_logs_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{theme.TEXT_MUTED};"
            f"  border:none; font-size:11px; text-decoration:underline; }}"
            f"QPushButton:hover {{ color:{theme.TEXT_PRIMARY}; }}"
        )
        open_logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_logs_btn.clicked.connect(self._open_logs_folder)
        logs_row.addWidget(open_logs_btn)
        root.addLayout(logs_row)

    # ── Project loader (item 15) ──────────────────────────────────────────────

    def _refresh_project_combo(self):
        self.project_combo.blockSignals(True)
        current_id = self._current_project_id
        self.project_combo.clear()
        self.project_combo.addItem("— select project —", userData=None)
        restore_idx = 0
        for i, proj in enumerate(project_registry.list_projects(), start=1):
            self.project_combo.addItem(proj["display_name"], userData=proj["project_id"])
            if proj["project_id"] == current_id:
                restore_idx = i
        self.project_combo.setCurrentIndex(restore_idx)
        self.project_combo.blockSignals(False)

    def _on_project_selected(self, index: int):
        project_id = self.project_combo.itemData(index)
        if not project_id:
            return
        proj = project_registry.get_project(project_id)
        if not proj:
            return
        self.local_input.setText(proj.get("local_path", ""))
        self.server_input.setText(proj.get("server_path", ""))
        latest = proj.get("latest_manifest", "")
        if latest and Path(latest).exists():
            self.base_input.setText(latest)
            self._update_stale_badge(latest)
        self._current_project_id = project_id
        self._refresh_history_panel()
        self.log.log(f"Loaded project: {proj['display_name']}", "info")

    # ── Auto-detect project on local path change (item 17) ───────────────────

    def _on_local_path_changed(self, text: str):
        if not text or len(text) < 3:
            return
        if self._detect_timer is None:
            self._detect_timer = QTimer(self)
            self._detect_timer.setSingleShot(True)
            self._detect_timer.timeout.connect(self._auto_detect_project)
        self._detect_timer.start(600)

    def _auto_detect_project(self):
        local = self.local_input.text()
        if not local:
            return
        proj = project_registry.find_by_local_path(local)
        if not proj:
            return
        if not self.server_input.text():
            self.server_input.setText(proj.get("server_path", ""))
        latest = proj.get("latest_manifest", "")
        if not self.base_input.text() and latest and Path(latest).exists():
            self.base_input.setText(latest)
            self._update_stale_badge(latest)
        self._current_project_id = proj["project_id"]
        self._refresh_history_panel()
        self.log.log(f"Auto-detected project: {proj['display_name']}", "info")

    # ── Stale manifest badge (item 18) ───────────────────────────────────────

    def _update_stale_badge(self, path: str = ""):
        path = path or self.base_input.text()
        if not path or not Path(path).exists():
            self.stale_label.setText("")
            return
        days = _manifest_age_days(path)
        if days >= 14:
            self.stale_label.setText(f"({days}d old)")
            self.stale_label.setStyleSheet(f"color:{theme.CORAL};font-size:11px;")
        elif days >= 7:
            self.stale_label.setText(f"({days}d old)")
            self.stale_label.setStyleSheet("color:#ff9800;font-size:11px;")
        elif days > 0:
            self.stale_label.setText(f"({days}d old)")
            self.stale_label.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:11px;")
        else:
            self.stale_label.setText("")

    # ── Manifest browser dialog (item 16) ────────────────────────────────────

    def _browse_manifest(self):
        local_name = Path(self.local_input.text()).name if self.local_input.text() else ""
        proj_dir = LOCAL_MANIFEST_DIR / local_name if local_name else None
        if proj_dir and proj_dir.exists() and any(proj_dir.glob("*.json")):
            dlg = ManifestBrowserDialog(local_name, self)
            if dlg.exec() and dlg.selected_path():
                self.base_input.setText(dlg.selected_path())
                self._update_stale_badge(dlg.selected_path())
                return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Base Manifest", "", "JSON Files (*.json)"
        )
        if path:
            self.base_input.setText(path)
            self._update_stale_badge(path)

    # ── Server health check (item 20) ────────────────────────────────────────

    def _check_server_health(self):
        server = self.server_input.text()
        if not server:
            QMessageBox.warning(self, "No Server Path", "Enter the server path first.")
            return
        self.log.log("Checking server manifest health...", "info")
        self._health_thread = QThread()
        self._health_worker = ServerHealthWorker(server, self.local_input.text())
        self._health_worker.moveToThread(self._health_thread)
        self._health_thread.started.connect(self._health_worker.run)
        self._health_worker.result.connect(self._on_health_result)
        self._health_worker.result.connect(self._health_thread.quit)
        self._health_thread.start()

    def _on_health_result(self, status: str, msg: str):
        level = "success" if status == "ok" else "warning" if status == "warn" else "error"
        self.log.log(f"Server health: {msg}", level)

    # ── Logs folder link ──────────────────────────────────────────────────────

    def _open_logs_folder(self):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        log_dir = Path.home() / "Documents" / "STSyncTool" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def _refresh_history_panel(self):
        pass  # History panel removed — data written to ~/Documents/STSyncTool/logs/

    def load_demo_data(self):
        """
        Pre-populate the path fields with the diverged demo folders for the
        onboarding tutorial, and inject representative rows into the diff table
        so the user can see what results look like.

        Safe to call multiple times — skips fields that already have real content.
        """
        from core.comparison import DiffState, DiffResult
        from core.demo import ensure_demo_merge_folders

        try:
            local, server, manifest = ensure_demo_merge_folders()
            if not self.local_input.text():
                self.local_input.setText(str(local))
            if not self.server_input.text():
                self.server_input.setText(str(server))
            if not self.base_input.text():
                self.base_input.setText(str(manifest))
                self._update_stale_badge(str(manifest))
        except Exception:
            pass

        # Show illustrative rows only when the table is currently empty
        # (i.e. no real scan has been run yet).
        if self.diff_table.rowCount() == 0:
            demo_results = [
                DiffResult("DCIM/A001/scene_01.txt",     DiffState.LOCAL_CHANGED),
                DiffResult("DCIM/A001/scene_02.txt",     DiffState.SERVER_CHANGED),
                DiffResult("DCIM/A001/scene_03.txt",     DiffState.BOTH_CHANGED),
                DiffResult("DCIM/A001/new_footage.txt",  DiffState.LOCAL_ONLY),
                DiffResult("DCIM/B001/server_addition.txt", DiffState.SERVER_ONLY),
                DiffResult("AUDIO/sound_report.txt",     DiffState.DELETED_LOCAL),
                DiffResult("MISC/notes.txt",             DiffState.SERVER_CHANGED),
            ]
            self.diff_table.load_results(demo_results)
            self.status_label.setText("7 differences found  (demo — click Scan & Compare to run for real)")

    # ── Scan ──────────────────────────────────────────────────────────────────

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
        self._apply_opacity.setOpacity(0.4)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Scanning…")

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
        self._update_unresolved_count()

        total     = len(results)
        changed   = len(visible)
        conflicts = sum(1 for r in results if r.state.name == "BOTH_CHANGED")
        self.log.log(
            f"Scan complete — {total} files, {changed} differences, {conflicts} conflicts.",
            "success" if conflicts == 0 else "warning",
        )
        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        if changed > 0:
            self.apply_btn.setEnabled(True)
            self._apply_opacity.setOpacity(1.0)
            self.status_label.setText(f"{changed} difference{'s' if changed != 1 else ''} found")
        else:
            self.apply_btn.setEnabled(False)
            self._apply_opacity.setOpacity(0.4)
            self.status_label.setText("No differences found")

        # Auto-register project (item 11)
        project_id = yours.get("project_id", "")
        if project_id:
            try:
                project_registry.upsert_project(
                    project_id,
                    local_path=self.local_input.text(),
                    server_path=self.server_input.text(),
                )
                if self._current_project_id != project_id:
                    self._current_project_id = project_id
                    self._refresh_project_combo()
                    self._refresh_history_panel()
            except Exception as e:
                self.log.log(f"  Could not register project: {e}", "warning")

    def _on_scan_error(self, msg):
        end_session()
        self.scan_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Scan failed")
        self.log.log(f"Scan error: {msg}", "error")
        QMessageBox.critical(self, "Scan Error", msg)

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _apply_actions(self):
        actions = self.diff_table.get_actions()
        actionable = {p: a for p, a in actions.items() if a not in (ACT_SKIP, "")}
        if not actionable:
            QMessageBox.information(self, "Nothing To Do", "No actions selected.")
            return

        conflicts = sum(1 for r in self._diff_results if r.state.name == "BOTH_CHANGED")
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
        self._apply_opacity.setOpacity(0.4)
        self.scan_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Applying…")

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
            conflict_count=conflicts,
        )
        self._apply_worker.moveToThread(self._apply_thread)
        self._apply_thread.started.connect(self._apply_worker.run)
        self._apply_worker.progress.connect(
            lambda p, f: (self.progress_bar.setValue(p), self.status_label.setText(f))
        )
        self._apply_worker.log.connect(self.log.log)
        self._apply_worker.finished.connect(self._on_apply_done)
        self._apply_worker.rescan_conflict.connect(self._on_rescan_conflict)
        self._apply_worker.error.connect(self._on_apply_error)
        self._apply_worker.finished.connect(self._apply_thread.quit)
        self._apply_worker.rescan_conflict.connect(self._apply_thread.quit)
        self._apply_worker.error.connect(self._apply_thread.quit)

        start_session()
        self._apply_thread.start()

    def _on_apply_done(self, results):
        end_session()
        self.scan_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        s  = len(results.get("success", []))
        f  = len(results.get("failed", []))
        sk = len(results.get("skipped", []))
        pr = len(results.get("renames", []))
        rename_note = f", {pr} preserve-rename{'s' if pr != 1 else ''}" if pr else ""
        self.log.log(
            f"Apply complete — {s} succeeded, {f} failed, {sk} skipped{rename_note}.",
            "success" if f == 0 else "warning",
        )
        self.status_label.setText(f"Applied {s} action{'s' if s != 1 else ''}")
        self.apply_btn.setEnabled(True)
        self._apply_opacity.setOpacity(1.0)
        self._refresh_history_panel()
        self._refresh_project_combo()
        if f == 0:
            QMessageBox.information(self, "Apply Complete",
                                    f"{s} action(s) completed successfully.")
        else:
            QMessageBox.warning(self, "Apply Finished with Errors",
                                f"{s} succeeded, {f} failed. See log for details.")

    def _on_apply_error(self, msg: str):
        end_session()
        self.scan_btn.setEnabled(True)
        self.apply_btn.setEnabled(False)
        self._apply_opacity.setOpacity(0.4)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Apply failed — scan again to retry")
        self.log.log(f"Apply error: {msg}", "error")
        QMessageBox.critical(self, "Apply Error", msg)

    # ── Conflict detail panel ─────────────────────────────────────────────────

    def _on_conflict_selected(self, result):
        """Update the conflict detail panel when a BOTH_CHANGED row is selected.
        `result` is a DiffResult or None."""
        if result is None:
            self._conflict_panel.setVisible(False)
            return

        local_e  = result.yours_entry  or {}
        server_e = result.server_entry or {}

        def _short_hash(entry):
            cs = entry.get("checksums", {})
            h = cs.get("sha256") or cs.get("md5") or cs.get("xxhash3_64")
            return h[:8] if h else "n/a"

        local_size  = _fmt_size(local_e.get("size"))
        server_size = _fmt_size(server_e.get("size"))
        local_mt    = _fmt_date(local_e.get("modtime", ""))
        server_mt   = _fmt_date(server_e.get("modtime", ""))
        local_hash  = _short_hash(local_e)
        server_hash = _short_hash(server_e)

        self._cp_local_size.setText(f"size:     {local_size}")
        self._cp_local_mtime.setText(f"modified: {local_mt or 'unknown'}")
        self._cp_local_hash.setText(f"sha256:   {local_hash}")

        self._cp_server_size.setText(f"size:     {server_size}")
        self._cp_server_mtime.setText(f"modified: {server_mt or 'unknown'}")
        self._cp_server_hash.setText(f"sha256:   {server_hash}")

        raw_local  = local_e.get("modtime", "")
        raw_server = server_e.get("modtime", "")
        if raw_local and raw_server:
            if raw_local > raw_server:
                verdict = "LOCAL is newer"
            elif raw_server > raw_local:
                verdict = "SERVER is newer"
            else:
                verdict = "Same modification time"
        else:
            verdict = "Modification time unknown"

        self._cp_verdict.setText(f"  {verdict}")
        self._conflict_panel.setVisible(True)

    # ── Conflict quick-action buttons (conflict detail panel) ─────────────────

    def _cp_keep_local(self):
        """Set the selected BOTH_CHANGED row's action to Push (keep local)."""
        from core.merge_ops import ACT_PUSH
        self.diff_table.set_action_for_selected(ACT_PUSH)

    def _cp_keep_server(self):
        """Set the selected BOTH_CHANGED row's action to Pull (keep server)."""
        from core.merge_ops import ACT_PULL
        self.diff_table.set_action_for_selected(ACT_PULL)

    def _on_conflict_action_changed(self, path: str, action: str):
        """Update the unresolved count label whenever a BOTH_CHANGED combo changes."""
        self._update_unresolved_count()

    def _update_unresolved_count(self):
        n = self.diff_table.unresolved_conflict_count()
        total = sum(
            1 for s in self.diff_table.get_states().values() if s == "BOTH_CHANGED"
        )
        if total == 0:
            self._unresolved_lbl.setVisible(False)
        elif n == 0:
            self._unresolved_lbl.setText(f"All {total} conflicts resolved")
            self._unresolved_lbl.setStyleSheet(
                f"color:{theme.ACCENT_GREEN}; font-size:12px; font-weight:bold;"
            )
            self._unresolved_lbl.setVisible(True)
        else:
            self._unresolved_lbl.setText(f"{n}/{total} conflicts unresolved")
            self._unresolved_lbl.setStyleSheet(
                f"color:{theme.ACCENT_CORAL}; font-size:12px; font-weight:bold;"
            )
            self._unresolved_lbl.setVisible(True)

    # ── Newer Wins batch action ───────────────────────────────────────────────

    def _on_newer_wins(self):
        """Apply the mtime-based default action to every BOTH_CHANGED row."""
        self.diff_table.apply_newer_wins()
        self._update_unresolved_count()
        conflict_rows = [
            path for path, state in self.diff_table.get_states().items()
            if state == "BOTH_CHANGED"
        ]
        if conflict_rows:
            self.log.log(
                f"Newer Wins applied to {len(conflict_rows)} conflict row(s).",
                "info",
            )
        else:
            self.log.log("Newer Wins: no conflict rows found.", "info")

    def _on_rescan_conflict(self, paths):
        end_session()
        self.apply_btn.setEnabled(False)
        self._apply_opacity.setOpacity(0.4)
        self.scan_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Scan first to enable apply")
        QMessageBox.warning(
            self, "Files Changed Since Scan",
            f"{len(paths)} file(s) changed since the initial scan.\n"
            "Apply was aborted to prevent overwriting current data.\n\n"
            'Click "Scan & Compare" again to refresh the diff.'
        )
