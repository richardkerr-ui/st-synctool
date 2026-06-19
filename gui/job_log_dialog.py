"""In-app viewer for per-job custody logs and activity records.

Opens as a non-modal window. For jobs that have a report file on disk (.txt
or .json) it renders the content in a scrollable read-only pane. For jobs
without a file (e.g. merge) it renders the activity record fields directly.
"""

import json
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QDialogButtonBox, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from gui import theme


def _fmt_json(path: Path) -> str:
    """Render a JSON report file as readable plain text."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return f"Could not parse JSON: {e}\n\n{path.read_text()}"

    lines = []
    schema = data.get("schema", "")

    if data.get("operation") in ("post-merge", "merge") or data.get("label") == "post-merge":
        ctx = data.get("checksum_context", {}) or {}
        stats = data.get("scan_stats", {}) or {}
        renames = data.get("renames") or []
        files = data.get("files") or {}
        lines += [
            f"Operation  : Merge",
            f"Date/Time  : {data.get('created_at', '')}",
            f"Workstation: {data.get('workstation', '')}",
            f"User       : {data.get('user', '')}",
            f"Project    : {data.get('project_id', '')}",
            f"Local      : {data.get('root', '')}",
            f"Server     : {data.get('counterpart_path', '')}",
            f"Files      : {data.get('file_count', len(files))}",
            f"Size       : {data.get('total_size_bytes', 0):,} bytes",
            f"Algorithm  : {ctx.get('algorithm', 'xxh128').upper()}",
            f"Reused     : {stats.get('reused_from_base', 0)}  Rehashed: {stats.get('rehashed', 0)}",
        ]
        if renames:
            lines += ["", f"RENAMES ({len(renames)}):"]
            for r in renames:
                lines.append(f"  {r.get('from', '')}  →  {r.get('to', '')}  [{r.get('reason', '')}]")
        if files:
            lines += ["", f"FILES ({len(files)}):"]
            for rel, fdata in files.items():
                cs = (fdata or {}).get("checksums", {}) or {}
                h = cs.get("xxh128") or cs.get("xxhash128") or cs.get("md5") or "—"
                status = (fdata or {}).get("verification_method", "")
                lines.append(f"  {rel}  [{status.upper() if status else '—'}]  {h}")
    elif schema == "verify_report":
        summary = data.get("summary", {})
        lines += [
            f"Operation  : Verify",
            f"Label      : {data.get('label', '')}",
            f"Folder     : {data.get('folder', '')}",
            f"Date/Time  : {data.get('timestamp', '')}",
            f"Workstation: {data.get('workstation', '')}",
            f"User       : {data.get('user', '')}",
            f"Deep       : {data.get('deep', False)}",
            "",
            "SUMMARY:",
            f"  Total    : {summary.get('total', 0)}",
            f"  OK       : {summary.get('ok', 0)}",
            f"  Missing  : {summary.get('missing', 0)}",
            f"  Mismatch : {summary.get('mismatch', 0)}",
            f"  Verdict  : {summary.get('verdict', '')}",
            "",
            "FILES:",
        ]
        for rel, fdata in (data.get("files") or {}).items():
            status = fdata.get("status", "")
            cs = fdata.get("checksums", {}) or {}
            h = cs.get("xxhash128") or cs.get("xxh128") or cs.get("md5") or "—"
            algo = "XXH128" if (cs.get("xxhash128") or cs.get("xxh128")) else ("MD5" if cs.get("md5") else "—")
            lines.append(f"  {rel}  [{status.upper()}]")
            lines.append(f"    {algo}: {h}")
    else:
        # Generic JSON: pretty-print with selective flattening of the files block.
        top = {k: v for k, v in data.items() if k != "files"}
        lines.append(json.dumps(top, indent=2, default=str))
        files = data.get("files", {})
        if files:
            lines += ["", f"FILES ({len(files)}):"]
            for rel, fdata in files.items():
                cs = (fdata.get("checksums") or fdata.get("dest_checksums") or {})
                h = cs.get("xxh128") or cs.get("xxhash128") or cs.get("md5") or "—"
                status = fdata.get("status", fdata.get("verification_method", ""))
                lines.append(f"  {rel}  [{status.upper() if status else '—'}]  {h}")

    return "\n".join(lines)


def _fmt_txt(path: Path) -> str:
    try:
        return path.read_text()
    except Exception as e:
        return f"Could not read file: {e}"


def _fmt_record(record: dict) -> str:
    """Render an activity record dict when no file is available."""
    op = record.get("operation", "").capitalize()
    lines = [
        f"Operation  : {op}",
        f"Date/Time  : {record.get('timestamp', '')}",
        f"Workstation: {record.get('workstation', '')}",
        f"User       : {record.get('user', '')}",
        f"Project    : {record.get('project', '')}",
        f"Source     : {record.get('source', '')}",
        f"Destination: {', '.join(record.get('dests') or [])}",
        f"Files      : {record.get('file_count', 0)}",
        f"Size       : {record.get('bytes', 0):,} bytes",
        f"Verdict    : {record.get('verdict', '')}",
        "",
        "(No report file is written for this operation type.)",
    ]
    return "\n".join(lines)


class JobLogDialog(QDialog):
    def __init__(self, path: Optional[Path], record: Optional[dict], parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(820, 620)

        op = (record or {}).get("operation", "job").capitalize()
        proj = (record or {}).get("project", "") or (path.stem if path else "")
        self.setWindowTitle(f"{op} Log — {proj}" if proj else f"{op} Log")
        self.setStyleSheet(f"background:{theme.CHARCOAL}; color:{theme.CREAM};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)

        if path:
            info = QLabel(str(path))
            info.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:11px;")
            info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(info)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        font = QFont("Menlo, Monaco, Courier New")
        font.setPointSize(12)
        self._text.setFont(font)
        self._text.setStyleSheet(
            f"background:{theme.CHARCOAL_LIGHT}; color:{theme.CREAM};"
            "border:none; padding:10px;"
        )
        layout.addWidget(self._text)

        btns = QHBoxLayout()
        btns.addStretch()
        if path and path.exists():
            reveal_btn = QPushButton("Reveal in Finder")
            reveal_btn.clicked.connect(lambda: self._reveal(path))
            reveal_btn.setStyleSheet(theme.primary_button_style())
            btns.addWidget(reveal_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(theme.primary_button_style())
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        if path and path.exists():
            if path.suffix == ".json":
                content = _fmt_json(path)
            else:
                content = _fmt_txt(path)
        elif record:
            content = _fmt_record(record)
        else:
            content = "No log data available for this job."

        self._text.setPlainText(content)

    def _reveal(self, path: Path):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
