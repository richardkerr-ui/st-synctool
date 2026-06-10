"""
Offload engine — camera card / audio recorder ingest.

Design principles (Phase 5, SYNCTOOL_CONTEXT.md):
- Source is always read-only. Never written to.
- Pre-hash all sources before copying begins.
- Staging: write to temp folder, verify against ground-truth, commit atomically.
- Per-file retries with exponential backoff.
- Sequential by source (source 1 → all dests, then source 2, …).
- Independent destination handling — one dest failure does not abort others.
- Chain-of-custody text log saved to ~/Documents/STSyncTool/offload_logs/.
"""

import enum
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from core.checksum import compute_all
from core.thumbnail import (
    ffmpeg_available, pillow_available, redline_available,
    build_contact_sheet, classify_files, find_rdc_clips,
    VIDEO_EXTENSIONS, AUDIO_EXTENSIONS, BRAW_EXTENSIONS,
)

OFFLOAD_LOGS_DIR = Path.home() / "Documents" / "STSyncTool" / "offload_logs"
MAX_RETRIES_DEFAULT = 3

_RETRYABLE = (OSError, IOError, ConnectionResetError, TimeoutError)


# ---------------------------------------------------------------------------
# Filename normalisation constants (Phase 7, items 53-59)
# ---------------------------------------------------------------------------

# Known camera sequential naming patterns: (compiled regex for stem, display name)
_KNOWN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'^IMG_\d{4,5}$',   re.IGNORECASE), 'IMG_XXXX'),
    (re.compile(r'^MVI_\d{4,5}$',   re.IGNORECASE), 'MVI_XXXX'),
    (re.compile(r'^GH0\d{5}$',      re.IGNORECASE), 'GH0XXXXX'),
    (re.compile(r'^DJI_\d{4}$',     re.IGNORECASE), 'DJI_XXXX'),
    (re.compile(r'^CM[12]_\d{4}$',  re.IGNORECASE), 'CM1/2_XXXX'),
    (re.compile(r'^CLIP_\d{4,5}$',  re.IGNORECASE), 'CLIP_XXXX'),
    (re.compile(r'^VIDEO_\d{4,5}$', re.IGNORECASE), 'VIDEO_XXXX'),
]
# Generic fallback: 1–4 uppercase letters + underscore + 4–5 digits, no date component
_GENERIC_SEQUENTIAL = re.compile(r'^[A-Z]{1,4}_\d{4,5}$', re.IGNORECASE)

# R3D stems already contain date and camera identifier — never rename these
_R3D_STEM = re.compile(r'^[A-Z]\d{3}_[A-Z]\d{3}_\d{6}', re.IGNORECASE)

# Sidecar extensions that travel with a video clip (item 59)
SIDECAR_EXTENSIONS: frozenset[str] = frozenset({
    '.srt', '.thm', '.xml', '.lut', '.xmp', '.edl',
})

SEQUENTIAL_DETECTION_THRESHOLD = 0.60


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

class CellState(enum.Enum):
    PENDING    = "pending"
    HASHING    = "hashing"
    COPYING    = "copying"
    VERIFYING  = "verifying"
    COMMITTING = "committing"
    THUMBNAILS = "thumbnails"  # contact sheet generation in progress
    DONE       = "done"
    FAILED     = "failed"
    SKIPPED    = "skipped"


@dataclass
class OffloadSource:
    label: str
    path: Path
    subfolder: str = ""   # empty → use label
    enabled: bool = True

    def effective_subfolder(self) -> str:
        return self.subfolder.strip() or self.label


@dataclass
class OffloadDest:
    label: str
    path: Path
    enabled: bool = True


@dataclass
class OffloadConfig:
    max_retries: int = MAX_RETRIES_DEFAULT
    stop_on_first_failure: bool = False
    generate_thumbnails: bool = False
    thumbnail_max_frames: int = 4
    normalize_filenames: bool = False


@dataclass
class CellResult:
    source_label: str
    dest_label: str
    state: CellState = CellState.PENDING
    files_copied: int = 0
    bytes_copied: int = 0
    errors: list = field(default_factory=list)
    staging_path: Optional[Path] = None
    final_path: Optional[Path] = None
    thumbnail_result: Optional[dict] = None   # set for primary dest when thumbnails enabled


# Callback type aliases (not enforced at runtime, just for clarity)
StatusCallback   = Callable[[str, Optional[str], "CellState"], None]
LogCallback      = Callable[[str, str], None]
ProgressCallback = Callable[[str, str, int, int], None]


# ---------------------------------------------------------------------------
# Normalisation helpers (Phase 7)
# ---------------------------------------------------------------------------

def _is_r3d_stem(stem: str) -> bool:
    return bool(_R3D_STEM.match(stem))


def _sequential_pattern_name(stem: str) -> Optional[str]:
    """Return display name of the matched sequential scheme, or None."""
    if _is_r3d_stem(stem):
        return None
    for pattern, name in _KNOWN_PATTERNS:
        if pattern.match(stem):
            return name
    if _GENERIC_SEQUENTIAL.match(stem):
        return 'XXXX_NNNN'
    return None


def scan_naming_patterns(source_manifest: dict) -> dict:
    """
    Item 53. Analyse video file stems in source_manifest for generic sequential naming.

    Returns:
      {
        "detected": bool,
        "pattern_name": str,       # most common matched pattern
        "match_ratio": float,
        "example_files": list[str] # up to 3 original filenames (before normalisation)
      }
    """
    from core.thumbnail import VIDEO_EXTENSIONS
    video_entries: list[tuple[str, str]] = []   # (rel, stem)
    for rel in source_manifest:
        if rel == "generated_artifacts":
            continue
        p = Path(rel)
        if p.suffix.lower() in VIDEO_EXTENSIONS and not _is_r3d_stem(p.stem):
            video_entries.append((rel, p.stem))

    if not video_entries:
        return {"detected": False, "pattern_name": "", "match_ratio": 0.0, "example_files": []}

    pattern_hits: dict[str, list[str]] = {}
    for rel, stem in video_entries:
        pname = _sequential_pattern_name(stem)
        if pname:
            pattern_hits.setdefault(pname, []).append(rel)

    if not pattern_hits:
        return {"detected": False, "pattern_name": "", "match_ratio": 0.0, "example_files": []}

    best = max(pattern_hits, key=lambda k: len(pattern_hits[k]))
    hits = pattern_hits[best]
    ratio = len(hits) / len(video_entries)
    return {
        "detected":      ratio >= SEQUENTIAL_DETECTION_THRESHOLD,
        "pattern_name":  best,
        "match_ratio":   ratio,
        "example_files": [Path(h).name for h in hits[:3]],
    }


def detect_cross_source_duplicates(source_manifests: dict) -> set:
    """
    Item 54. Return basenames that appear in more than one source.
    An empty source_manifests or a single source always returns an empty set.
    """
    counts: Counter = Counter()
    for mfst in source_manifests.values():
        seen: set[str] = set()
        for rel in mfst:
            if rel == "generated_artifacts":
                continue
            name = Path(rel).name
            if name not in seen:
                counts[name] += 1
                seen.add(name)
    return {name for name, cnt in counts.items() if cnt > 1}


def build_normalization_plan(source_manifest: dict) -> dict:
    """
    Items 57, 59. Build {original_rel: normalized_rel} using sha256 already in source_manifest.

    Video files whose stem matches a sequential pattern get _{sha256[:8]} appended before
    the extension.  Sidecar files (.srt, .thm, .xml, .lut, .xmp, .edl) with the same stem
    carry the same hash suffix (co-rename).  Files that do not match are omitted (identity).
    R3D files are never included.
    """
    from core.thumbnail import VIDEO_EXTENSIONS

    # First pass: stem → hash suffix for video files that will be renamed
    stem_to_suffix: dict[str, str] = {}
    for rel, info in source_manifest.items():
        if rel == "generated_artifacts":
            continue
        p = Path(rel)
        if p.suffix.lower() in VIDEO_EXTENSIONS and _sequential_pattern_name(p.stem):
            stem_to_suffix[p.stem] = info["checksum"][:8]

    plan: dict[str, str] = {}
    for rel in source_manifest:
        if rel == "generated_artifacts":
            continue
        p = Path(rel)
        ext_lower = p.suffix.lower()

        if ext_lower in VIDEO_EXTENSIONS and _sequential_pattern_name(p.stem):
            suffix   = source_manifest[rel]["checksum"][:8]
            new_name = f"{p.stem}_{suffix}{p.suffix}"
            plan[rel] = str(p.parent / new_name)
        elif ext_lower in SIDECAR_EXTENSIONS:
            # Case-insensitive stem lookup
            suffix = stem_to_suffix.get(p.stem) or stem_to_suffix.get(p.stem.upper()) or \
                     stem_to_suffix.get(p.stem.lower())
            if suffix:
                new_name = f"{p.stem}_{suffix}{p.suffix}"
                plan[rel] = str(p.parent / new_name)

    return plan


def apply_normalization_in_staging(
    staging_dir: Path,
    norm_plan: dict,
    log_cb: LogCallback,
) -> None:
    """
    Item 58. Rename files inside staging_dir according to norm_plan.
    The source card is never touched.
    """
    if not norm_plan:
        return
    for original_rel, normalized_rel in norm_plan.items():
        src = staging_dir / original_rel
        dst = staging_dir / normalized_rel
        if not src.exists():
            log_cb(f"[Normalise] Not found in staging: {original_rel}", "warning")
            continue
        if src == dst:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        log_cb(f"[Normalise] {Path(original_rel).name} → {Path(normalized_rel).name}", "info")


def build_normalized_manifest(source_manifest: dict, norm_plan: dict) -> tuple:
    """
    Item 60 (data layer). Return (normalized_manifest, normalization_block).

    normalized_manifest keys are normalized paths; renamed entries carry
    'original_filename', 'filename_hash_suffix', and 'hash_method' fields.
    normalization_block is the top-level 'filename_normalization' dict.
    """
    if not norm_plan:
        return dict(source_manifest), {"applied": False}

    norm_manifest: dict = {}
    renames_list: list[dict] = []

    for rel, info in source_manifest.items():
        if rel == "generated_artifacts":
            norm_manifest[rel] = info
            continue
        normalized = norm_plan.get(rel, rel)
        entry = dict(info)
        if normalized != rel:
            entry["original_filename"]    = Path(rel).name
            entry["filename_hash_suffix"] = info.get("checksum", "")[:8]
            entry["hash_method"]          = "sha256_prefix8"
            renames_list.append({
                "original":   Path(rel).name,
                "normalized": Path(normalized).name,
            })
        norm_manifest[normalized] = entry

    pattern_scan = scan_naming_patterns(source_manifest)
    norm_block = {
        "applied":          True,
        "method":           "sha256_prefix8",
        "detected_pattern": pattern_scan.get("pattern_name", ""),
        "renames":          renames_list,
    }
    return norm_manifest, norm_block


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return compute_all(path, include_xxhash=False)["sha256"]


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, _RETRYABLE)


def _copy_with_retries(
    src_file: Path,
    dst_file: Path,
    max_retries: int,
    log_cb: LogCallback,
) -> None:
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, max_retries + 1):
        try:
            shutil.copy2(src_file, dst_file)
            return
        except Exception as exc:
            if not _retryable(exc) or attempt == max_retries:
                raise
            delay = 2 ** (attempt - 1)
            log_cb(
                f"[Offload] Retry {attempt}/{max_retries} for {src_file.name}"
                f" (waiting {delay}s): {exc}",
                "warning",
            )
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Phase steps (also callable individually for testing)
# ---------------------------------------------------------------------------

def preflight_source_readonly(source: OffloadSource) -> None:
    """Raise if the source path is missing or not a directory."""
    p = source.path
    if not p.exists():
        raise FileNotFoundError(f"Source not found: {p}")
    if not p.is_dir():
        raise NotADirectoryError(f"Source is not a directory: {p}")


def prehash_source(
    source: OffloadSource,
    log_cb: LogCallback,
    file_progress_cb: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """
    SHA-256 every file in the source directory.

    Returns ground-truth manifest:
      { relative_path_str: {"size": int, "checksum": str, "algorithm": "sha256"} }
    """
    log_cb(f"[Offload] Pre-hashing: {source.label} ({source.path})", "info")
    files = sorted(p for p in source.path.rglob("*") if p.is_file())
    manifest: dict = {}
    for i, f in enumerate(files):
        rel = str(f.relative_to(source.path))
        manifest[rel] = {
            "size":      f.stat().st_size,
            "checksum":  _sha256(f),
            "algorithm": "sha256",
        }
        if file_progress_cb:
            file_progress_cb(i + 1, len(files))
    log_cb(f"[Offload] Pre-hash done: {len(files)} files in {source.label}", "success")
    return manifest


def copy_source_to_staging(
    source: OffloadSource,
    dest: OffloadDest,
    ts: str,
    source_manifest: dict,
    max_retries: int,
    log_cb: LogCallback,
    status_cb: StatusCallback,
    progress_cb: Optional[ProgressCallback] = None,
) -> Path:
    """
    Copy all source files into a staging directory under the destination.

    Staging path: {dest.path}/{source.effective_subfolder()}/.st_staging_{ts}/
    Returns the staging directory Path.
    """
    staging_dir = dest.path / source.effective_subfolder() / f".st_staging_{ts}"
    log_cb(f"[Offload] Staging {source.label} → {dest.label}: {staging_dir}", "info")
    status_cb(source.label, dest.label, CellState.COPYING)

    files = list(source_manifest.keys())
    for i, rel in enumerate(files):
        src_file = source.path / rel
        dst_file = staging_dir / rel
        _copy_with_retries(src_file, dst_file, max_retries, log_cb)
        if progress_cb:
            progress_cb(source.label, dest.label, i + 1, len(files))

    return staging_dir


def verify_staging(
    staging_dir: Path,
    source_manifest: dict,
    log_cb: LogCallback,
    status_cb: StatusCallback,
    source_label: str,
    dest_label: str,
) -> list:
    """
    Re-hash every file in staging and compare against source ground-truth.

    Returns a list of error strings; empty list means verification passed.
    """
    status_cb(source_label, dest_label, CellState.VERIFYING)
    errors = []
    for rel, info in source_manifest.items():
        dst_file = staging_dir / rel
        if not dst_file.exists():
            errors.append(f"Missing after copy: {rel}")
            continue
        if dst_file.stat().st_size != info["size"]:
            errors.append(f"Size mismatch: {rel}")
            continue
        actual = _sha256(dst_file)
        if actual != info["checksum"]:
            errors.append(
                f"Checksum mismatch: {rel} "
                f"(expected {info['checksum'][:8]}…, got {actual[:8]}…)"
            )
    if not errors:
        log_cb(f"[Offload] Verification passed: {source_label} → {dest_label}", "success")
    return errors


def commit_staging(
    staging_dir: Path,
    dest: OffloadDest,
    source: OffloadSource,
    log_cb: LogCallback,
    status_cb: StatusCallback,
) -> Path:
    """
    Rename/move staging directory to final destination path.

    If the final directory already exists, individual files are moved into it
    to avoid clobbering existing content. Otherwise a single atomic rename is used.
    """
    final_dir = dest.path / source.effective_subfolder()
    status_cb(source.label, dest.label, CellState.COMMITTING)

    if final_dir.exists():
        for f in staging_dir.rglob("*"):
            if f.is_file():
                target = final_dir / f.relative_to(staging_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(target))
        shutil.rmtree(staging_dir, ignore_errors=True)
    else:
        shutil.move(str(staging_dir), str(final_dir))

    log_cb(f"[Offload] Committed: {source.label} → {dest.label} ({final_dir})", "success")
    return final_dir


def write_failure_report(
    staging_dir: Path,
    errors: list,
    source_label: str,
    dest_label: str,
) -> None:
    """Write a plain-text failure report alongside the staging directory."""
    report = staging_dir.parent / f".st_failure_{staging_dir.name}.txt"
    lines = [
        "Offload failure report",
        f"Source:   {source_label}",
        f"Dest:     {dest_label}",
        f"Staging:  {staging_dir}",
        f"Errors ({len(errors)}):",
    ] + [f"  - {e}" for e in errors]
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines))
    except Exception:
        pass


def write_chain_of_custody_log(
    sources: list,
    dests: list,
    results: list,
    source_manifests: dict,
    ts: str,
) -> Path:
    """
    Write a human-readable chain-of-custody log for the entire offload run.
    Saved to ~/Documents/STSyncTool/offload_logs/offload_{ts}.txt.
    """
    OFFLOAD_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OFFLOAD_LOGS_DIR / f"offload_{ts}.txt"

    lines: list[str] = [
        "=" * 72,
        "ST SyncTool — Offload Chain of Custody",
        f"Run: {ts}",
        f"Sources: {len(sources)}   Destinations: {len(dests)}",
        "=" * 72,
        "",
    ]

    for src in sources:
        manifest = source_manifests.get(src.label, {})
        lines += [
            f"SOURCE: {src.label}",
            f"  Path:      {src.path}",
            f"  Subfolder: {src.effective_subfolder()}",
            f"  Files:     {len(manifest)}",
            f"  Total:     {sum(v['size'] for v in manifest.values()):,} bytes",
            "  Pre-hash manifest:",
        ]
        for rel, info in sorted(manifest.items()):
            lines.append(f"    {info['checksum'][:16]}  {rel}")
        lines.append("")

    lines += ["RESULTS:", ""]
    for r in results:
        lines += [
            f"  {r.source_label} → {r.dest_label}",
            f"    State:  {r.state.value}",
            f"    Files:  {r.files_copied}",
            f"    Bytes:  {r.bytes_copied:,}",
        ]
        if r.final_path:
            lines.append(f"    Path:   {r.final_path}")
        if r.thumbnail_result:
            lines.append(f"    Contact sheet: {r.thumbnail_result.get('contact_sheet_path', '')}")
        if r.errors:
            lines.append(f"    Errors ({len(r.errors)}):")
            for e in r.errors:
                lines.append(f"      - {e}")
        lines.append("")

    log_path.write_text("\n".join(lines))
    return log_path


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_offload(
    sources: list,
    dests: list,
    config: OffloadConfig,
    status_cb: StatusCallback,
    log_cb: LogCallback,
    progress_cb: Optional[ProgressCallback] = None,
    cancelled_cb: Optional[Callable[[], bool]] = None,
) -> tuple:
    """
    Execute a full M×N offload.

    Execution order: source 1 → all active dests, source 2 → all active dests, …

    Returns (results: list[CellResult], source_manifests: dict, log_path: Path).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    active_sources = [s for s in sources if s.enabled]
    active_dests   = [d for d in dests   if d.enabled]

    # Initialise result grid
    cell_results: dict[tuple, CellResult] = {
        (s.label, d.label): CellResult(s.label, d.label)
        for s in active_sources
        for d in active_dests
    }

    source_manifests: dict[str, dict] = {}
    # Track which dest is primary per source (first active dest)
    primary_dest = active_dests[0] if active_dests else None

    for src in active_sources:
        if cancelled_cb and cancelled_cb():
            _mark_remaining(cell_results, src, active_dests, CellState.SKIPPED, status_cb)
            break

        # ── Preflight ────────────────────────────────────────────────────
        try:
            preflight_source_readonly(src)
        except Exception as exc:
            log_cb(f"[Offload] Preflight failed for {src.label}: {exc}", "error")
            for dst in active_dests:
                r = cell_results[(src.label, dst.label)]
                r.state = CellState.FAILED
                r.errors.append(str(exc))
                status_cb(src.label, dst.label, CellState.FAILED)
            continue

        # ── Pre-hash ─────────────────────────────────────────────────────
        # Broadcast HASHING state to all destination cells for this source
        for dst in active_dests:
            status_cb(src.label, dst.label, CellState.HASHING)

        try:
            mfst = prehash_source(src, log_cb)
            source_manifests[src.label] = mfst
        except Exception as exc:
            log_cb(f"[Offload] Pre-hash failed for {src.label}: {exc}", "error")
            for dst in active_dests:
                r = cell_results[(src.label, dst.label)]
                r.state = CellState.FAILED
                r.errors.append(str(exc))
                status_cb(src.label, dst.label, CellState.FAILED)
            continue

        # ── Filename normalisation (Phase 7, items 57-59) ────────────────
        norm_plan: dict = {}
        norm_block: dict = {"applied": False}
        if config.normalize_filenames:
            norm_plan = build_normalization_plan(mfst)
            if norm_plan:
                _, norm_block = build_normalized_manifest(mfst, norm_plan)
                log_cb(
                    f"[Offload] Normalising {len(norm_plan)} filename(s) for {src.label}",
                    "info",
                )

        # Verified manifest uses normalised keys; original mfst used for copying
        norm_mfst = build_normalized_manifest(mfst, norm_plan)[0] if norm_plan else mfst

        # ── Copy → Verify → Commit for each destination ──────────────────
        for dst in active_dests:
            if cancelled_cb and cancelled_cb():
                r = cell_results[(src.label, dst.label)]
                r.state = CellState.SKIPPED
                status_cb(src.label, dst.label, CellState.SKIPPED)
                continue

            r = cell_results[(src.label, dst.label)]
            try:
                staging = copy_source_to_staging(
                    src, dst, ts, mfst, config.max_retries,
                    log_cb, status_cb, progress_cb,
                )
                r.staging_path = staging

                # Rename files in staging to normalised names (source never touched)
                apply_normalization_in_staging(staging, norm_plan, log_cb)

                errors = verify_staging(
                    staging, norm_mfst, log_cb, status_cb, src.label, dst.label,
                )
                if errors:
                    r.state  = CellState.FAILED
                    r.errors = errors
                    write_failure_report(staging, errors, src.label, dst.label)
                    status_cb(src.label, dst.label, CellState.FAILED)
                    log_cb(
                        f"[Offload] FAILED: {src.label} → {dst.label}"
                        f" ({len(errors)} error(s))",
                        "error",
                    )
                    if config.stop_on_first_failure:
                        _mark_remaining(cell_results, src, active_dests, CellState.SKIPPED, status_cb, after=dst)
                        break
                else:
                    final = commit_staging(staging, dst, src, log_cb, status_cb)
                    r.final_path   = final
                    r.files_copied = len(norm_mfst)
                    r.bytes_copied = sum(
                        v["size"] for v in norm_mfst.values()
                        if isinstance(v, dict) and "size" in v
                    )
                    r.state = CellState.DONE
                    status_cb(src.label, dst.label, CellState.DONE)

                    # Persist normalised manifest for chain-of-custody log
                    if norm_plan:
                        nm = dict(norm_mfst)
                        nm["filename_normalization"] = norm_block
                        source_manifests[src.label] = nm

                    # ── Thumbnail generation (primary dest only) ──────────
                    if (
                        config.generate_thumbnails
                        and primary_dest
                        and dst.label == primary_dest.label
                        and pillow_available()
                    ):
                        try:
                            status_cb(src.label, dst.label, CellState.THUMBNAILS)
                            from datetime import date as _date
                            # Build orig-name lookup from normalised manifest
                            fn_originals = {
                                rel: info["original_filename"]
                                for rel, info in norm_mfst.items()
                                if isinstance(info, dict) and "original_filename" in info
                            } if norm_plan else None
                            thumb_result = build_contact_sheet(
                                source_label=src.label,
                                offload_date=_date.today().isoformat(),
                                dest_dir=final,
                                ts=ts,
                                max_frames=config.thumbnail_max_frames,
                                log_cb=log_cb,
                                filename_originals=fn_originals,
                            )
                            r.thumbnail_result = thumb_result
                            # Merge per-file thumbnail info into normalised manifest
                            for rel, ti in thumb_result.get("per_file", {}).items():
                                if rel in norm_mfst:
                                    norm_mfst[rel]["thumbnails"] = ti
                            # Add generated_artifacts to normalised manifest
                            norm_mfst.setdefault("generated_artifacts", {})[
                                thumb_result["artifact_key"]
                            ] = thumb_result["artifact_info"]
                            source_manifests[src.label] = norm_mfst
                            status_cb(src.label, dst.label, CellState.DONE)
                        except Exception as exc:
                            log_cb(
                                f"[Thumbnail] Generation failed for {src.label}: {exc}",
                                "warning",
                            )
                            status_cb(src.label, dst.label, CellState.DONE)

            except Exception as exc:
                r.state = CellState.FAILED
                r.errors.append(str(exc))
                status_cb(src.label, dst.label, CellState.FAILED)
                log_cb(f"[Offload] Error: {src.label} → {dst.label}: {exc}", "error")
                if config.stop_on_first_failure:
                    _mark_remaining(cell_results, src, active_dests, CellState.SKIPPED, status_cb, after=dst)
                    break

    flat = list(cell_results.values())
    log_path = write_chain_of_custody_log(
        active_sources, active_dests, flat, source_manifests, ts
    )
    return flat, source_manifests, log_path


def _mark_remaining(
    cell_results: dict,
    src: OffloadSource,
    dests: list,
    state: CellState,
    status_cb: StatusCallback,
    after: Optional[OffloadDest] = None,
) -> None:
    """Mark all remaining destination cells for a source as `state`."""
    past = after is None
    for dst in dests:
        if after is not None and dst.label == after.label:
            past = True
            continue
        if past:
            key = (src.label, dst.label)
            if key in cell_results:
                cell_results[key].state = state
                status_cb(src.label, dst.label, state)
