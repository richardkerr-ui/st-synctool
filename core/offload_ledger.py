"""M12.2 duplicate-card / already-offloaded guard.

The classic DIT footgun is re-offloading a card, or formatting one whose
offload silently no-op'd. This module builds a cheap fingerprint of a source
and matches it against prior offloads, so the GUI can warn (never block —
cards get reused and labels collide legitimately) before copying starts.

Pure and headless. The fingerprint deliberately keys on *content* (file count,
total bytes, top-level entry names), not just the volume label: a reused card
with a new shoot must NOT trigger a false "already offloaded" warning.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SourceFingerprint:
    label: str
    volume_label: str
    file_count: int
    total_bytes: int
    top_names: tuple   # sorted top-level entry names under the source

    def to_record(self, dests: list, now_iso: str) -> dict:
        return {
            "label": self.label,
            "volume_label": self.volume_label,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "top_names": list(self.top_names),
            "dests": list(dests),
            "offloaded_at": now_iso,
        }


def _volume_label(path: Path) -> str:
    """Best-effort human label for the volume a path lives on. On macOS a card
    mounts at /Volumes/<NAME>; otherwise fall back to the source folder name."""
    parts = path.resolve().parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return parts[2]
    return path.name


def fingerprint_source(source, skip_filenames: Optional[set] = None) -> SourceFingerprint:
    """Cheap, stat-only fingerprint of a source directory (no hashing)."""
    if skip_filenames is None:
        from core.offload import SKIP_FILENAMES
        skip_filenames = SKIP_FILENAMES
    skip_lower = {n.lower() for n in skip_filenames}
    path = source.path
    count = 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and p.name.lower() not in skip_lower:
            count += 1
            total += p.stat().st_size
    top_names = tuple(sorted(c.name for c in path.iterdir()))
    return SourceFingerprint(
        label=source.label,
        volume_label=_volume_label(path),
        file_count=count,
        total_bytes=total,
        top_names=top_names,
    )


def fingerprint_from_manifest(source, manifest: dict) -> SourceFingerprint:
    """Fingerprint from an already-computed pre-hash manifest, so recording a
    completed offload re-walks nothing."""
    files = [
        (rel, info) for rel, info in manifest.items()
        if isinstance(info, dict) and "size" in info
    ]
    total = sum(info["size"] for _, info in files)
    top_names = tuple(sorted({rel.split("/", 1)[0] for rel, _ in files}))
    return SourceFingerprint(
        label=source.label,
        volume_label=_volume_label(source.path),
        file_count=len(files),
        total_bytes=total,
        top_names=top_names,
    )


def match_prior_offloads(
    fp: SourceFingerprint,
    history: list,
    dest_label: Optional[str] = None,
) -> list:
    """Return prior offload records that look like the same card content.

    A match requires identical file_count AND total_bytes (so a reused card
    with different content never matches). Kind is "exact" when the top-level
    names also match, else "partial" (same payload, top folder renamed). When
    ``dest_label`` is given, only prior offloads to that destination count.
    """
    matches = []
    for rec in history:
        if dest_label is not None and dest_label not in rec.get("dests", []):
            continue
        if (rec.get("file_count") == fp.file_count
                and rec.get("total_bytes") == fp.total_bytes):
            same_names = tuple(rec.get("top_names", [])) == fp.top_names
            matches.append({"record": rec, "kind": "exact" if same_names else "partial"})
    return matches


def warning_text(fp: SourceFingerprint, matches: list) -> str:
    """Operator-facing warning summarising the prior offload(s)."""
    n = fp.file_count
    lines = [
        f"'{fp.label}' ({n} files, on volume '{fp.volume_label}') looks like it "
        f"was already offloaded:",
        "",
    ]
    for m in matches:
        rec = m["record"]
        when = rec.get("offloaded_at", "?")
        dests = ", ".join(rec.get("dests", [])) or "?"
        lines.append(f"  • {when} → {dests} ({m['kind']} match)")
    lines += ["", "Offload it again anyway?"]
    return "\n".join(lines)
