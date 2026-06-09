#!/bin/bash
# ST SyncTool — project scaffold script
# Run from wherever you want the project to live.

set -e
PROJECT="st_synctool"
mkdir -p "$PROJECT"/{core,gui,utils}

# ── Touch __init__ files ──────────────────────────────────────────────────────
touch "$PROJECT/core/__init__.py"
touch "$PROJECT/gui/__init__.py"
touch "$PROJECT/utils/__init__.py"

echo "Creating files..."

# ── requirements.txt ─────────────────────────────────────────────────────────
cat > "$PROJECT/requirements.txt" << 'EOF'
PyQt6>=6.6.0
xxhash>=3.4.1
humanize>=4.9.0
pyperclip>=1.8.2
requests>=2.31.0
EOF

# ── main.py ───────────────────────────────────────────────────────────────────
cat > "$PROJECT/main.py" << 'PYEOF'
import sys, os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from gui.main_window import MainWindow

def main():
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    app = QApplication(sys.argv)
    app.setApplicationName("ST SyncTool")
    app.setOrganizationName("Signal Theory")
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base,            QColor(22, 22, 22))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(40, 40, 40))
    palette.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button,          QColor(50, 50, 50))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(0, 122, 204))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
PYEOF

echo "✓ main.py"

# ── core/checksum.py ──────────────────────────────────────────────────────────
cat > "$PROJECT/core/checksum.py" << 'PYEOF'
import hashlib, xxhash
from pathlib import Path
from typing import Callable, Optional

CHUNK = 8 * 1024 * 1024

def compute_all(path: Path, include_xxhash=True, include_md5=False,
                progress_cb: Optional[Callable[[int], None]] = None) -> dict:
    h_sha = hashlib.sha256()
    h_xx  = xxhash.xxh3_64() if include_xxhash else None
    h_md5 = hashlib.md5()    if include_md5    else None
    size = path.stat().st_size; done = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h_sha.update(chunk)
            if h_xx:  h_xx.update(chunk)
            if h_md5: h_md5.update(chunk)
            done += len(chunk)
            if progress_cb: progress_cb(int(done / size * 100) if size else 100)
    result = {"sha256": h_sha.hexdigest()}
    if h_xx:  result["xxhash3_64"] = h_xx.hexdigest()
    if h_md5: result["md5"]        = h_md5.hexdigest()
    return result
PYEOF

echo "✓ core/checksum.py"

# ── core/manifest.py ──────────────────────────────────────────────────────────
cat > "$PROJECT/core/manifest.py" << 'PYEOF'
import json, os, socket, getpass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable
from core.checksum import compute_all

MANIFEST_FILENAME = "st_manifest.json"
LOCAL_MANIFEST_DIR = Path.home() / "Documents" / "STSyncTool" / "manifests"

def generate_manifest(folder: Path, label="source", dest_path=None,
                      gdrive=False, progress_cb=None) -> dict:
    files_list = [p for p in folder.rglob("*") if p.is_file()]
    total = len(files_list)
    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label, "root": str(folder),
        "destination": dest_path or "",
        "workstation": socket.gethostname(), "user": getpass.getuser(),
        "file_count": total, "files": {},
    }
    for i, path in enumerate(files_list):
        if progress_cb: progress_cb(int((i / total) * 100), path.name)
        rel = path.relative_to(folder).as_posix()
        stat = path.stat()
        hashes = compute_all(path, include_xxhash=not gdrive, include_md5=gdrive)
        manifest["files"][rel] = {
            "type": "file", "size": stat.st_size,
            "modtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "checksums": hashes,
        }
    manifest["total_size_bytes"] = sum(e["size"] for e in manifest["files"].values())
    return manifest

def save_manifest(manifest: dict, source_dir=None, dest_dir=None, name_hint="") -> list:
    LOCAL_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"st_manifest_{name_hint}_{ts}.json" if name_hint else f"st_manifest_{ts}.json"
    saved = []
    targets = [LOCAL_MANIFEST_DIR / fname]
    if source_dir: targets.append(Path(source_dir) / MANIFEST_FILENAME)
    if dest_dir:   targets.append(Path(dest_dir)   / MANIFEST_FILENAME)
    for p in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest, indent=2))
        saved.append(p)
    return saved

def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text())
PYEOF

echo "✓ core/manifest.py"

# ── core/comparison.py ────────────────────────────────────────────────────────
cat > "$PROJECT/core/comparison.py" << 'PYEOF'
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

class DiffState(Enum):
    UNCHANGED=auto(); LOCAL_ONLY=auto(); SERVER_ONLY=auto()
    LOCAL_CHANGED=auto(); SERVER_CHANGED=auto(); BOTH_CHANGED=auto()
    DELETED_LOCAL=auto(); DELETED_SERVER=auto(); DELETED_BOTH=auto()

STATE_LABELS = {
    DiffState.UNCHANGED:      ("Unchanged",       "#6a9955"),
    DiffState.LOCAL_ONLY:     ("Local Only",      "#569cd6"),
    DiffState.SERVER_ONLY:    ("Server Only",     "#9cdcfe"),
    DiffState.LOCAL_CHANGED:  ("Local Changed",   "#dcdcaa"),
    DiffState.SERVER_CHANGED: ("Server Changed",  "#ce9178"),
    DiffState.BOTH_CHANGED:   ("⚠ Conflict",      "#f44747"),
    DiffState.DELETED_LOCAL:  ("Deleted Locally", "#d16969"),
    DiffState.DELETED_SERVER: ("Deleted Server",  "#c586c0"),
    DiffState.DELETED_BOTH:   ("Deleted Both",    "#808080"),
}

@dataclass
class DiffResult:
    path: str; state: DiffState
    base_entry: Optional[dict]=field(default=None,repr=False)
    yours_entry: Optional[dict]=field(default=None,repr=False)
    server_entry: Optional[dict]=field(default=None,repr=False)
    @property
    def label(self): return STATE_LABELS[self.state][0]
    @property
    def color(self): return STATE_LABELS[self.state][1]

def _cs(entry):
    if not entry: return None
    c = entry.get("checksums", {})
    return c.get("sha256") or c.get("xxhash3_64") or c.get("md5")

def three_way_diff(base, yours, server) -> list:
    bf=base.get("files",{}); yf=yours.get("files",{}); sf=server.get("files",{})
    results=[]
    for path in sorted(set(bf)|set(yf)|set(sf)):
        b=bf.get(path); y=yf.get(path); s=sf.get(path)
        cb=_cs(b); cy=_cs(y); cs=_cs(s)
        if b and y and s:
            if cy==cb and cs==cb: state=DiffState.UNCHANGED
            elif cy!=cb and cs==cb: state=DiffState.LOCAL_CHANGED
            elif cs!=cb and cy==cb: state=DiffState.SERVER_CHANGED
            else: state=DiffState.BOTH_CHANGED
        elif not b and y and s: state=DiffState.UNCHANGED if cy==cs else DiffState.BOTH_CHANGED
        elif not b and y and not s: state=DiffState.LOCAL_ONLY
        elif not b and not y and s: state=DiffState.SERVER_ONLY
        elif b and not y and not s: state=DiffState.DELETED_BOTH
        elif b and not y and s: state=DiffState.DELETED_LOCAL
        elif b and y and not s: state=DiffState.DELETED_SERVER
        else: continue
        results.append(DiffResult(path=path,state=state,base_entry=b,yours_entry=y,server_entry=s))
    return results
PYEOF

echo "✓ core/comparison.py"

# ── core/transfer.py ──────────────────────────────────────────────────────────
# (writing full version)
cat > "$PROJECT/core/transfer.py" << 'PYEOF'
import os, shutil, subprocess, zipfile, getpass, socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional
from core.checksum import compute_all
from core.manifest import generate_manifest, save_manifest
from utils.file_utils import folder_size, free_space, format_bytes
from utils.gdrive_utils import is_gdrive_url

GDRIVE_DAILY_LIMIT_BYTES = 750 * 1024 ** 3

class TransferError(Exception): pass
class TransferWarning(Exception): pass

def estimate_time_seconds(size_bytes, speed_mbps=150.0):
    return size_bytes / (speed_mbps * 1024 * 1024)

def pre_flight_checks(source, destination, is_gdrive_dest=False, log_cb=None):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    summary = {}
    src_path = Path(source) if not is_gdrive_url(str(source)) else None
    if src_path:
        total = folder_size(src_path)
        summary["source_size"] = total
        secs = estimate_time_seconds(total)
        h=int(secs//3600); m2=int((secs%3600)//60); s=int(secs%60)
        summary["estimated_human"] = f"{h}h {m2}m {s}s" if h else f"{m2}m {s}s"
        log(f"Source size: {format_bytes(total)} — est. {summary['estimated_human']} @ 150 MB/s")
        if is_gdrive_dest and total > GDRIVE_DAILY_LIMIT_BYTES:
            raise TransferError(
                f"Source is {format_bytes(total)}, which exceeds the Google Drive 750 GB/day upload limit.\n"
                "Please contact a Signal Theory Productions lead to schedule a direct CloudSync on Synology instead."
            )
    dst_path = Path(destination) if not is_gdrive_url(str(destination)) else None
    if dst_path and src_path:
        dst_path.mkdir(parents=True, exist_ok=True)
        free = free_space(dst_path)
        total_disk = shutil.disk_usage(dst_path).total
        used_after = sum(f.stat().st_size for f in dst_path.rglob("*") if f.is_file()) if dst_path.exists() else 0
        pct_after = (used_after + total) / total_disk * 100
        if free < total:
            raise TransferError(f"Not enough space. Need {format_bytes(total)}, only {format_bytes(free)} free.")
        if pct_after > 90:
            raise TransferWarning(f"⚠ Destination will be {pct_after:.1f}% full after transfer.")
        log(f"Destination free: {format_bytes(free)} — OK")
    return summary

def copy_file(src, dst, log_cb=None, progress_cb=None, gdrive_mode=False):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    dst = Path(dst); dst.parent.mkdir(parents=True, exist_ok=True)
    log(f"  → Hashing source: {Path(src).name}")
    pre = compute_all(Path(src), include_xxhash=not gdrive_mode, include_md5=gdrive_mode,
                      progress_cb=lambda p: progress_cb(p//2) if progress_cb else None)
    shutil.copy2(src, dst)
    log(f"  → Verifying destination: {dst.name}")
    post = compute_all(dst, include_xxhash=not gdrive_mode, include_md5=gdrive_mode,
                       progress_cb=lambda p: progress_cb(50+p//2) if progress_cb else None)
    key = "md5" if gdrive_mode else "sha256"
    if pre.get(key) != post.get(key):
        raise TransferError(f"Checksum mismatch after copy! {key}: {pre.get(key)} vs {post.get(key)}")
    log(f"  ✓ Verified {Path(src).name}", "success")
    return {"source_checksums": pre, "dest_checksums": post, "verified": True}

def resolve_folder_conflict(src: Path, dst: Path):
    same = src.name == dst.name
    return (dst if same else dst / src.name), same

def transfer_folder(src, dst, gdrive_mode=False, log_cb=None, progress_cb=None, conflict_handler="skip"):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    src=Path(src); dst=Path(dst)
    actual_dest, same_name = resolve_folder_conflict(src, dst)
    actual_dest.mkdir(parents=True, exist_ok=True)
    files = [p for p in src.rglob("*") if p.is_file()]
    total = len(files); records = []; errors = []
    for i, fpath in enumerate(files):
        rel = fpath.relative_to(src); dest_file = actual_dest / rel
        if dest_file.exists():
            if conflict_handler=="skip": log(f"  ⊘ Skipped: {rel}","warning"); continue
            elif conflict_handler=="rename":
                dest_file = dest_file.with_name(f"{dest_file.stem}_conflict{dest_file.suffix}")
        if progress_cb: progress_cb(int(i/total*100), fpath.name)
        try:
            r = copy_file(fpath, dest_file, log_cb=log_cb, gdrive_mode=gdrive_mode)
            r.update({"source_path":str(fpath),"dest_path":str(dest_file),"filename":fpath.name,"size":fpath.stat().st_size})
            records.append(r)
        except Exception as e:
            log(f"  ✗ {fpath.name}: {e}","error"); errors.append({"file":str(fpath),"error":str(e)})
    if progress_cb: progress_cb(100, "Building manifest…")
    manifest = {
        "schema_version":"1.0","created_at":datetime.now(timezone.utc).isoformat(),
        "workstation":socket.gethostname(),"user":getpass.getuser(),
        "source_root":str(src),"dest_root":str(actual_dest),
        "same_name_merge":same_name,"gdrive_mode":gdrive_mode,
        "file_count":len(records),"error_count":len(errors),
        "files":{r["filename"]:r for r in records},"errors":errors,
    }
    saved = save_manifest(manifest, source_dir=src, dest_dir=actual_dest, name_hint=src.name)
    log(f"  ✓ Manifest saved to {len(saved)} locations")
    return {"manifest":manifest,"saved_manifest_paths":[str(p) for p in saved],
            "errors":errors,"actual_dest":str(actual_dest),"same_name":same_name}

def extract_multipart_zip(zip_dir: Path, log_cb=None):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    extracted = []
    for z in sorted(Path(zip_dir).glob("*.zip")):
        out = z.parent / z.stem
        try:
            with zipfile.ZipFile(z) as zf: zf.extractall(out)
            log(f"  ✓ Extracted {z.name} → {out.name}"); extracted.append(out)
        except zipfile.BadZipFile: log(f"  ✗ Bad zip: {z.name}","error")
    return extracted
PYEOF

echo "✓ core/transfer.py"

# ── core/rclone_bridge.py ─────────────────────────────────────────────────────
cat > "$PROJECT/core/rclone_bridge.py" << 'PYEOF'
import json, subprocess, socket, getpass
from datetime import datetime, timezone

RCLONE_BIN = "rclone"

def _run(args, timeout=300):
    return subprocess.run([RCLONE_BIN]+args, capture_output=True, text=True, timeout=timeout)

def lsjson(remote_path, with_checksum=True):
    args = ["lsjson","--recursive"]
    if with_checksum: args.append("--checksum")
    args.append(remote_path)
    r = _run(args, timeout=600)
    if r.returncode != 0: raise RuntimeError(f"rclone lsjson failed: {r.stderr}")
    return json.loads(r.stdout)

def lsjson_to_manifest(remote_path, label="server"):
    items = lsjson(remote_path, with_checksum=True)
    files = {}
    for item in items:
        if item.get("IsDir"): continue
        cs = {}
        if "Hashes" in item:
            h = item["Hashes"]
            if "SHA-256" in h: cs["sha256"]     = h["SHA-256"].lower()
            if "xxhash"  in h: cs["xxhash3_64"] = h["xxhash"].lower()
            if "MD5"     in h: cs["md5"]         = h["MD5"].lower()
        files[item["Path"]] = {"type":"file","size":item.get("Size",0),
                               "modtime":item.get("ModTime",""),"checksums":cs}
    return {"schema_version":"1.0","created_at":datetime.now(timezone.utc).isoformat(),
            "label":label,"root":remote_path,"workstation":socket.gethostname(),
            "user":getpass.getuser(),"file_count":len(files),"files":files,
            "total_size_bytes":sum(v["size"] for v in files.values())}

def sync(src, dst, dry_run=False, log_cb=None):
    args = ["sync", src, dst, "--progress"]
    if dry_run: args.append("--dry-run")
    r = _run(args, timeout=3600)
    if log_cb:
        for line in r.stdout.splitlines(): log_cb(line,"info")
        for line in r.stderr.splitlines(): log_cb(line,"warning" if r.returncode==0 else "error")
    return r.returncode == 0
PYEOF

echo "✓ core/rclone_bridge.py"

# ── core/amphetamine.py ───────────────────────────────────────────────────────
cat > "$PROJECT/core/amphetamine.py" << 'PYEOF'
import subprocess, sys

AMPHETAMINE_APP_STORE = "https://apps.apple.com/us/app/amphetamine/id937984704"

def is_macos(): return sys.platform == "darwin"

def is_installed():
    if not is_macos(): return False
    r = subprocess.run(["osascript","-e",'tell application "Finder" to exists application file id "com.if.Amphetamine"'],
                       capture_output=True,text=True)
    return "true" in r.stdout.lower()

def start_session():
    if not is_macos() or not is_installed(): return False
    script='tell application "Amphetamine" to start new session with options {duration:0, interval:minutes, displaySleepAllowed:false}'
    return subprocess.run(["osascript","-e",script],capture_output=True).returncode == 0

def end_session():
    if not is_macos() or not is_installed(): return False
    return subprocess.run(["osascript","-e",'tell application "Amphetamine" to end current session'],capture_output=True).returncode == 0

def check_and_prompt(parent_widget=None):
    if not is_macos(): return True
    if is_installed(): return True
    if parent_widget:
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(parent_widget)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Amphetamine Required")
        msg.setText("<b>Amphetamine is not installed.</b><br><br>Amphetamine prevents your Mac from sleeping during transfers.<br><br>"
                    f'<a href="{AMPHETAMINE_APP_STORE}">Download from Mac App Store</a>')
        msg.setTextFormat(1); msg.exec()
    return False
PYEOF

echo "✓ core/amphetamine.py"

# ── utils/file_utils.py ───────────────────────────────────────────────────────
cat > "$PROJECT/utils/file_utils.py" << 'PYEOF'
import shutil
from pathlib import Path

def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())

def free_space(path: Path) -> int:
    return shutil.disk_usage(path).free

def format_bytes(n: int) -> str:
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024: return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
PYEOF

echo "✓ utils/file_utils.py"

# ── utils/gdrive_utils.py ─────────────────────────────────────────────────────
cat > "$PROJECT/utils/gdrive_utils.py" << 'PYEOF'
import re
from typing import Optional

GDRIVE_PATTERNS = [
    r"https://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)",
    r"https://drive\.google\.com/drive/u/\d+/folders/([a-zA-Z0-9_-]+)",
    r"https://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)",
]

def is_gdrive_url(text: str) -> bool:
    return any(re.search(p, text) for p in GDRIVE_PATTERNS)

def parse_gdrive_id(url: str) -> Optional[str]:
    for p in GDRIVE_PATTERNS:
        m = re.search(p, url)
        if m: return m.group(1)
    return None

def get_clipboard_gdrive_url() -> Optional[str]:
    try:
        import pyperclip
        text = pyperclip.paste()
        if is_gdrive_url(text): return text.strip()
    except Exception: pass
    return None
PYEOF

echo "✓ utils/gdrive_utils.py"

# ── gui/log_widget.py ─────────────────────────────────────────────────────────
cat > "$PROJECT/gui/log_widget.py" << 'PYEOF'
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtGui import QTextCursor
from datetime import datetime

LEVEL_COLORS = {"error":"#f44747","warning":"#f4a744","success":"#6aa84f","info":"#cccccc"}

class LogWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setStyleSheet("QTextEdit{background:#1a1a1a;font-family:'SF Mono','Menlo','Consolas',monospace;font-size:11px;border:1px solid #333;border-radius:4px;padding:4px;}")

    def log(self, message: str, level: str = "info"):
        color = LEVEL_COLORS.get(level, LEVEL_COLORS["info"])
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"error":"✗","warning":"⚠","success":"✓","info":"·"}.get(level,"·")
        self.append(f'<span style="color:#555">[{ts}]</span> <span style="color:{color}">{prefix} {message}</span>')
        self.moveCursor(QTextCursor.MoveOperation.End)

    def clear_log(self): self.clear()
PYEOF

echo "✓ gui/log_widget.py"

# ── gui/path_input_widget.py ──────────────────────────────────────────────────
cat > "$PROJECT/gui/path_input_widget.py" << 'PYEOF'
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton, QComboBox, QFileDialog
from PyQt6.QtCore import pyqtSignal, QSettings
from utils.gdrive_utils import get_clipboard_gdrive_url

MAX_RECENT = 12

class PathInputWidget(QWidget):
    pathChanged = pyqtSignal(str)
    def __init__(self, label="path", parent=None):
        super().__init__(parent)
        self._label = label
        self._settings = QSettings("SignalTheory","STSyncTool")
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter path or Google Drive URL…")
        self.input.focusInEvent = self._on_focus_in
        self.input.textChanged.connect(lambda t: self.pathChanged.emit(t))
        self.recent_btn = QComboBox(); self.recent_btn.setFixedWidth(28); self.recent_btn.setToolTip("Recent")
        self.recent_btn.addItem("▾"); self._load_recent()
        self.recent_btn.currentIndexChanged.connect(self._on_recent_selected)
        self.browse_btn = QPushButton("Browse…"); self.browse_btn.setFixedWidth(70)
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.input, stretch=1); layout.addWidget(self.recent_btn); layout.addWidget(self.browse_btn)

    def _on_focus_in(self, event):
        QLineEdit.focusInEvent(self.input, event)
        if not self.input.text():
            g = get_clipboard_gdrive_url()
            if g: self.input.setText(g)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, f"Select {self._label}")
        if path: self.input.setText(path); self.add_to_recent(path)

    def _load_recent(self):
        r = self._settings.value(f"recent_{self._label}", [], type=list)
        self.recent_btn.clear(); self.recent_btn.addItem("▾")
        for item in r: self.recent_btn.addItem(item)

    def _on_recent_selected(self, idx):
        if idx > 0: self.input.setText(self.recent_btn.itemText(idx)); self.recent_btn.setCurrentIndex(0)

    def add_to_recent(self, path):
        r = self._settings.value(f"recent_{self._label}", [], type=list)
        if path in r: r.remove(path)
        r.insert(0, path); r = r[:MAX_RECENT]
        self._settings.setValue(f"recent_{self._label}", r); self._load_recent()

    def text(self): return self.input.text().strip()
    def setText(self, t): self.input.setText(t)
PYEOF

echo "✓ gui/path_input_widget.py"

# ── gui/diff_table.py ─────────────────────────────────────────────────────────
cat > "$PROJECT/gui/diff_table.py" << 'PYEOF'
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
PYEOF

echo "✓ gui/diff_table.py"

# ── gui/transfer_tab.py, merge_tab.py, verify_tab.py, main_window.py ─────────
# These are large — copying verbatim from the spec above.
# For brevity the scaffold writes placeholder stubs; replace with full code from the chat.

cat > "$PROJECT/gui/transfer_tab.py" << 'PYEOF'
# See full implementation in chat — paste TransferTab class here.
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
class TransferTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("Paste full TransferTab implementation here."))
PYEOF

cat > "$PROJECT/gui/merge_tab.py" << 'PYEOF'
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
class MergeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("Paste full MergeTab implementation here."))
PYEOF

cat > "$PROJECT/gui/verify_tab.py" << 'PYEOF'
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
class VerifyTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        QVBoxLayout(self).addWidget(QLabel("Paste full VerifyTab implementation here."))
PYEOF

cat > "$PROJECT/gui/main_window.py" << 'PYEOF'
from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget, QStatusBar, QLabel
from PyQt6.QtGui import QFont
from gui.transfer_tab import TransferTab
from gui.merge_tab    import MergeTab
from gui.verify_tab   import VerifyTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST SyncTool — Signal Theory")
        self.setMinimumSize(1100, 780)
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(12,12,12,8)
        header_label = QLabel("ST SyncTool")
        header_label.setFont(QFont("SF Pro Display",18,QFont.Weight.Bold))
        header_label.setStyleSheet("color:white")
        root.addWidget(header_label)
        self.tabs = QTabWidget()
        self.tabs.addTab(TransferTab(self), "📦  Transfer")
        self.tabs.addTab(MergeTab(self),    "🔀  Merge")
        self.tabs.addTab(VerifyTab(self),   "🔎  Verify")
        root.addWidget(self.tabs)
        self.setStatusBar(QStatusBar())
PYEOF

echo "✓ gui/ tabs + main_window.py"
echo ""
echo "════════════════════════════════════════════"
echo "✅  Project scaffolded → ./$PROJECT/"
echo ""
echo "Next steps:"
echo "  cd $PROJECT"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  # Paste full tab code from chat into gui/ files"
echo "  python main.py"
echo "════════════════════════════════════════════"
