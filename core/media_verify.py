"""
Format-aware post-copy verification for professional media files.

Returns MediaVerifyResult dataclass instances — never raises on tool absence.
Callers should treat advisory=True results as warnings, not failures.

Supported formats:
  - R3D (.r3d / .RDC clips)        — via REDline (REDCINE-X PRO)
  - ProRes / MXF (.mov, .mxf)      — frame count comparison via ffprobe
  - Image sequences (.dpx, .exr)   — file count and frame-number gap detection
  - ARRIRAW (.ari)                  — via ARRI ART tool (advisory if absent)
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# REDline ships inside REDCINE-X PRO (free download from red.com)
_REDLINE_BUNDLE_PATH = Path("/Applications/REDCINE-X PRO.app/Contents/MacOS/REDline")

# Extensions that trigger image-sequence verification
IMAGE_SEQUENCE_EXTENSIONS: tuple[str, ...] = (".dpx", ".exr")

# Extensions that trigger ProRes/MXF verification
PRORES_MXF_EXTENSIONS: tuple[str, ...] = (".mov", ".mxf")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MediaVerifyResult:
    """
    ok:       True = check passed, False = check found a problem.
    detail:   Human-readable message written to the chain-of-custody log.
    advisory: True = warning only; does not fail the offload.
              False = hard failure (same weight as a hash mismatch).
    """
    ok: bool
    detail: str
    advisory: bool = False


# ---------------------------------------------------------------------------
# Tool detection helpers
# ---------------------------------------------------------------------------

def _find_redline() -> Optional[Path]:
    """Return the Path to REDline, or None if not installed."""
    if _REDLINE_BUNDLE_PATH.exists():
        return _REDLINE_BUNDLE_PATH
    found = shutil.which("REDline")
    return Path(found) if found else None


def _find_ffprobe() -> Optional[str]:
    """Return the ffprobe executable name/path, or None if not installed."""
    return shutil.which("ffprobe")


def _find_art() -> Optional[str]:
    """Return the ARRI ART executable, or None if not installed."""
    return shutil.which("arri-art")


# ---------------------------------------------------------------------------
# R3D clip verification
# ---------------------------------------------------------------------------

def verify_r3d_clip(rdc_path: Path) -> MediaVerifyResult:
    """
    Verify all .R3D segment files inside an .RDC folder using REDline.

    REDline is run with --useMeta --decode <segment> --noOutput for each
    segment. A non-zero exit code from any segment is a hard failure.
    If REDline is not installed the result is advisory.
    """
    redline = _find_redline()
    if redline is None:
        return MediaVerifyResult(
            ok=True,
            detail="REDline not installed — R3D decode verification skipped",
            advisory=True,
        )

    segments = sorted(rdc_path.glob("*.R3D")) + sorted(rdc_path.glob("*.r3d"))
    if not segments:
        return MediaVerifyResult(
            ok=True,
            detail=f"No .R3D segments found in {rdc_path.name}",
            advisory=True,
        )

    failed: list[str] = []
    for seg in segments:
        try:
            result = subprocess.run(
                [str(redline), "--useMeta", "--decode", str(seg), "--noOutput"],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                failed.append(seg.name)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed.append(f"{seg.name} ({exc})")

    if failed:
        return MediaVerifyResult(
            ok=False,
            detail=f"REDline decode failed for: {', '.join(failed)}",
            advisory=False,
        )

    return MediaVerifyResult(
        ok=True,
        detail=f"REDline verified {len(segments)} segment(s) in {rdc_path.name}",
        advisory=False,
    )


# ---------------------------------------------------------------------------
# ProRes / MXF frame-count verification
# ---------------------------------------------------------------------------

def _ffprobe_frame_count(path: Path, ffprobe_bin: str) -> Optional[int]:
    """
    Return the video packet count for the first video stream, or None on error.

    Uses:
      ffprobe -v error -select_streams v:0 -count_packets
              -show_entries stream=nb_read_packets -of csv=p=0 <path>
    """
    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-select_streams", "v:0",
                "-count_packets",
                "-show_entries", "stream=nb_read_packets",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.strip()
        if not line:
            return None
        return int(line)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def verify_prores_mxf(src_path: Path, dst_path: Path) -> MediaVerifyResult:
    """
    Compare video packet (frame) counts at source and destination via ffprobe.

    A mismatch means the container wrote successfully but the stream is truncated.
    Advisory if ffprobe is not installed.
    """
    ffprobe = _find_ffprobe()
    if ffprobe is None:
        return MediaVerifyResult(
            ok=True,
            detail="ffprobe not installed — frame count verification skipped",
            advisory=True,
        )

    src_count = _ffprobe_frame_count(src_path, ffprobe)
    dst_count = _ffprobe_frame_count(dst_path, ffprobe)

    if src_count is None or dst_count is None:
        return MediaVerifyResult(
            ok=True,
            detail=(
                f"ffprobe could not read frame count "
                f"(src={'?' if src_count is None else src_count}, "
                f"dst={'?' if dst_count is None else dst_count}) — skipped"
            ),
            advisory=True,
        )

    if src_count != dst_count:
        return MediaVerifyResult(
            ok=False,
            detail=f"frame count mismatch: src={src_count} dst={dst_count}",
            advisory=False,
        )

    return MediaVerifyResult(
        ok=True,
        detail=f"frame counts match: {src_count} packets",
        advisory=False,
    )


# ---------------------------------------------------------------------------
# Image sequence verification (DPX / EXR)
# ---------------------------------------------------------------------------

_FRAME_NUMBER_RE = re.compile(r"(\d+)$")


def _extract_frame_number(stem: str) -> Optional[int]:
    """Extract the trailing numeric run from a filename stem, or None."""
    m = _FRAME_NUMBER_RE.search(stem)
    return int(m.group(1)) if m else None


def _collect_sequence_frames(directory: Path, extensions: tuple[str, ...]) -> dict[int, str]:
    """
    Return {frame_number: filename} for all image files in *directory*
    whose extension (lowercased) matches *extensions*.
    Files without a parseable trailing frame number are ignored.
    """
    frames: dict[int, str] = {}
    for ext in extensions:
        for f in directory.glob(f"*{ext}"):
            fn = _extract_frame_number(f.stem)
            if fn is not None:
                frames[fn] = f.name
        # Also match uppercase variants (e.g. .DPX alongside .dpx)
        for f in directory.glob(f"*{ext.upper()}"):
            fn = _extract_frame_number(f.stem)
            if fn is not None and fn not in frames:
                frames[fn] = f.name
    return frames


def verify_image_sequence(
    src_dir: Path,
    dst_dir: Path,
    extensions: tuple[str, ...] = IMAGE_SEQUENCE_EXTENSIONS,
) -> MediaVerifyResult:
    """
    Verify an image sequence directory:
      1. Frame count at destination matches source.
      2. No gaps in the frame numbering (consecutive integers from min to max).

    Returns ok=False with detail listing missing frames if gaps are found,
    or if the destination count differs from the source count.
    """
    src_frames = _collect_sequence_frames(src_dir, extensions)
    dst_frames = _collect_sequence_frames(dst_dir, extensions)

    if not src_frames:
        return MediaVerifyResult(
            ok=True,
            detail=f"No image sequence files found in {src_dir.name}",
            advisory=True,
        )

    src_count = len(src_frames)
    dst_count = len(dst_frames)

    if src_count != dst_count:
        return MediaVerifyResult(
            ok=False,
            detail=(
                f"frame count mismatch: src={src_count} dst={dst_count} "
                f"in {src_dir.name}"
            ),
            advisory=False,
        )

    # Check for gaps in the destination frame numbering
    frame_nums = sorted(dst_frames.keys())
    if len(frame_nums) >= 2:
        expected = set(range(frame_nums[0], frame_nums[-1] + 1))
        actual = set(frame_nums)
        missing = sorted(expected - actual)
        if missing:
            # Report up to 10 missing frame numbers to keep the log readable
            sample = missing[:10]
            extra = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
            return MediaVerifyResult(
                ok=False,
                detail=(
                    f"gap(s) in frame sequence in {dst_dir.name}: "
                    f"missing frames {sample}{extra}"
                ),
                advisory=False,
            )

    return MediaVerifyResult(
        ok=True,
        detail=f"image sequence OK: {dst_count} frame(s) in {dst_dir.name}",
        advisory=False,
    )


# ---------------------------------------------------------------------------
# ARRIRAW verification
# ---------------------------------------------------------------------------

def verify_arriraw(dst_path: Path) -> MediaVerifyResult:
    """
    Verify an ARRIRAW .ari file using the ARRI ART tool.

    Advisory if ART is not installed; hard failure if ART returns non-zero.
    """
    art = _find_art()
    if art is None:
        return MediaVerifyResult(
            ok=True,
            detail="ART not installed — manual ARRIRAW verification recommended",
            advisory=True,
        )

    try:
        result = subprocess.run(
            [art, str(dst_path)],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            return MediaVerifyResult(
                ok=False,
                detail=f"ART verification failed for {dst_path.name}: {stderr}",
                advisory=False,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MediaVerifyResult(
            ok=True,
            detail=f"ART could not run ({exc}) — advisory only",
            advisory=True,
        )

    return MediaVerifyResult(
        ok=True,
        detail=f"ART verified {dst_path.name}",
        advisory=False,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def verify_file(
    src_path: Path,
    dst_path: Path,
    _seq_dirs_seen: Optional[set] = None,
) -> Optional[MediaVerifyResult]:
    """
    Dispatch to the appropriate format-specific check based on file extension.

    Returns None if no format-specific check applies to this extension.

    For image sequences, the check runs once per directory: pass a shared
    set as *_seq_dirs_seen* and it will be populated to avoid re-running
    the check for every file in the same sequence directory.
    """
    ext = src_path.suffix.lower()

    # ── R3D: dispatch at the .RDC clip level ─────────────────────────────
    if ext == ".r3d":
        # Walk up to find the enclosing .RDC folder (or treat parent as clip root)
        parent = src_path.parent
        if parent.suffix.lower() == ".rdc":
            rdc_dst = dst_path.parent  # corresponding .RDC at destination
            return verify_r3d_clip(rdc_dst)
        # .r3d not inside an .rdc — verify via its parent directory
        return verify_r3d_clip(dst_path.parent)

    # ── ProRes / MXF ─────────────────────────────────────────────────────
    if ext in PRORES_MXF_EXTENSIONS:
        return verify_prores_mxf(src_path, dst_path)

    # ── DPX / EXR image sequences ─────────────────────────────────────────
    if ext in IMAGE_SEQUENCE_EXTENSIONS:
        src_dir = src_path.parent
        dst_dir = dst_path.parent
        if _seq_dirs_seen is not None:
            key = str(src_dir)
            if key in _seq_dirs_seen:
                return None  # already checked this directory
            _seq_dirs_seen.add(key)
        return verify_image_sequence(src_dir, dst_dir)

    # ── ARRIRAW ──────────────────────────────────────────────────────────
    if ext == ".ari":
        return verify_arriraw(dst_path)

    return None
