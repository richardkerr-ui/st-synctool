"""
Thumbnail extraction and contact sheet generation (Phases 6 and 8).

Dependencies:
  - ffmpeg + ffprobe: frame extraction and metadata probing
  - Pillow (PIL): tile compositor and PDF/JPEG output
  - REDline (REDCINE-X PRO, free): R3D frame extraction (optional)

All entry points degrade gracefully: missing dependencies produce metadata-only
tiles or raise ImportError with an install hint that the GUI surfaces as a tooltip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional

CONTACT_SHEETS_DIR = Path.home() / "Documents" / "STSyncTool" / "contact_sheets"

# REDline ships inside REDCINE-X PRO (free download from red.com)
_REDLINE_BUNDLE_PATH = Path("/Applications/REDCINE-X PRO.app/Contents/MacOS/REDline")

# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------

def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

def check_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None

def ffmpeg_available() -> bool:
    return check_ffmpeg() and check_ffprobe()

def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def check_redline() -> Optional[Path]:
    """
    Return the Path to the REDline executable, or None if not found.

    Checks the REDCINE-X PRO app bundle first, then PATH.
    Item 62: surface None to callers so they can show an install prompt.
    """
    if _REDLINE_BUNDLE_PATH.exists():
        return _REDLINE_BUNDLE_PATH
    found = shutil.which("REDline")
    return Path(found) if found else None


def redline_available() -> bool:
    return check_redline() is not None

# ---------------------------------------------------------------------------
# File classification (item 42)
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mxf", ".mov", ".mp4", ".m4v", ".mts", ".m2ts", ".ari", ".crm",
    ".movi", ".avi", ".mkv", ".r3d",
})
AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".wav", ".bwf", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".ogg", ".opus",
})
BRAW_EXTENSIONS: frozenset[str] = frozenset({".braw"})


def classify_files(paths: list[Path]) -> dict[str, list[Path]]:
    """
    Separate a list of paths into video, audio, braw, and other.

    Returns {"video": [...], "audio": [...], "braw": [...], "other": [...]}.
    """
    result: dict[str, list[Path]] = {"video": [], "audio": [], "braw": [], "other": []}
    for p in paths:
        ext = p.suffix.lower()
        if ext in BRAW_EXTENSIONS:
            result["braw"].append(p)
        elif ext in VIDEO_EXTENSIONS:
            result["video"].append(p)
        elif ext in AUDIO_EXTENSIONS:
            result["audio"].append(p)
        else:
            result["other"].append(p)
    return result


# ---------------------------------------------------------------------------
# ffprobe metadata extraction (item 43)
# ---------------------------------------------------------------------------

def probe_clip(path: Path) -> dict:
    """
    Run ffprobe on a media file and return normalised metadata.

    Returned keys (all Optional[str] unless noted):
      duration (float|None), format_name, date_recorded, camera_make,
      camera_model, codec, profile, resolution, frame_rate, bit_depth,
      timecode_start, sample_rate, channels, audio_codec.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    try:
        raw = subprocess.check_output(cmd, timeout=30, stderr=subprocess.DEVNULL)
        data = json.loads(raw)
    except Exception:
        return {}

    info: dict = {}

    fmt  = data.get("format", {})
    tags = fmt.get("tags", {})

    info["format_name"]  = fmt.get("format_long_name") or fmt.get("format_name")
    info["date_recorded"] = _extract_date(tags)
    info["camera_make"]   = _first_tag(tags, [
        "com.apple.quicktime.make", "make", "artist",
    ])
    info["camera_model"]  = _first_tag(tags, [
        "com.apple.quicktime.model", "model",
    ])

    raw_dur = fmt.get("duration")
    info["duration"] = float(raw_dur) if raw_dur else None

    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type", "")
        stags = stream.get("tags", {})

        if codec_type == "video" and "codec" not in info:
            info["codec"]      = stream.get("codec_long_name") or stream.get("codec_name")
            info["profile"]    = stream.get("profile")
            info["resolution"] = f"{stream.get('width', '?')}×{stream.get('height', '?')}"
            info["bit_depth"]  = str(
                stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or ""
            )
            info["frame_rate"] = _safe_fraction(stream.get("r_frame_rate", ""))
            info["timecode_start"] = stags.get("timecode") or stags.get("time_code") or None
            if not info.get("camera_make"):
                info["camera_make"]  = stags.get("make")
            if not info.get("camera_model"):
                info["camera_model"] = stags.get("model")
            if not info.get("date_recorded"):
                info["date_recorded"] = _extract_date(stags)

        elif codec_type == "audio" and "sample_rate" not in info:
            info["sample_rate"] = stream.get("sample_rate")
            info["channels"]    = str(stream.get("channels", ""))
            info["audio_codec"] = stream.get("codec_name")
            if not info.get("bit_depth"):
                info["bit_depth"] = str(
                    stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or ""
                )

    return info


def _first_tag(tags: dict, keys: list[str]) -> Optional[str]:
    for k in keys:
        v = tags.get(k)
        if v:
            return str(v)
    return None


def _extract_date(tags: dict) -> Optional[str]:
    raw = tags.get("creation_time") or tags.get("date") or ""
    return raw.split("T")[0] if raw else None


def _safe_fraction(s: str) -> Optional[str]:
    """'24000/1001' → '23.976', '25/1' → '25'."""
    if not s:
        return None
    try:
        if "/" in s:
            n, d = s.split("/")
            val = float(n) / float(d)
            return f"{val:.3f}".rstrip("0").rstrip(".")
        return s
    except Exception:
        return s


# ---------------------------------------------------------------------------
# Adaptive frame count (item 44)
# ---------------------------------------------------------------------------

FRAME_POSITIONS: tuple[float, ...] = (0.15, 0.38, 0.62, 0.85)


def adaptive_frame_count(duration_secs: Optional[float], user_max: int = 4) -> int:
    """
    Returns 1–4 based on clip duration, capped at user_max.

    Under 5 s → 1 frame, 5–30 s → 2, 30 s–2 min → 3, over 2 min → 4.
    """
    cap = max(1, min(user_max, 4))
    if duration_secs is None or duration_secs < 5:
        return min(1, cap)
    if duration_secs < 30:
        return min(2, cap)
    if duration_secs < 120:
        return min(3, cap)
    return cap


# ---------------------------------------------------------------------------
# Frame extraction (item 45)
# ---------------------------------------------------------------------------

def extract_frames(
    clip_path: Path,
    out_dir: Path,
    duration: float,
    frame_count: int,
    log_cb: Optional[Callable[[str, str], None]] = None,
) -> list[Path]:
    """
    Extract JPEG frames at FRAME_POSITIONS[:frame_count] of runtime.

    Returns list of Paths for frames that were successfully written.
    Missing frames are silently omitted (caller gets fewer than requested).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    positions = FRAME_POSITIONS[:frame_count]
    frames: list[Path] = []

    for i, pos in enumerate(positions):
        t = duration * pos
        out_file = out_dir / f"{clip_path.stem}_f{i + 1}.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t:.3f}",
            "-i", str(clip_path),
            "-vframes", "1",
            "-q:v", "3",
            str(out_file),
        ]
        try:
            subprocess.run(
                cmd, timeout=60,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=True,
            )
            if out_file.exists():
                frames.append(out_file)
        except Exception as exc:
            if log_cb:
                log_cb(
                    f"[Thumbnail] Frame {i + 1} failed for {clip_path.name}: {exc}",
                    "warning",
                )

    return frames


# ---------------------------------------------------------------------------
# BRAW sidecar parser (item 47)
# ---------------------------------------------------------------------------

def parse_braw_sidecar(clip_path: Path) -> dict:
    """
    Look for a Blackmagic .sidecar (or .xml) file alongside the .braw clip.
    Returns a flat dict of tag→value, or {} if not found.
    """
    for suffix in (".sidecar", ".xml"):
        sidecar = clip_path.with_suffix(suffix)
        if sidecar.exists():
            try:
                tree = ET.parse(sidecar)
                root = tree.getroot()
                result: dict = {}
                for child in root.iter():
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if child.text and child.text.strip():
                        result[tag] = child.text.strip()
                return result
            except Exception:
                pass
    return {}


# ---------------------------------------------------------------------------
# Pillow tile compositor (items 46–48)
# ---------------------------------------------------------------------------

TILE_WIDTH       = 1800
TILE_HEIGHT      = 220
THUMB_AREA_FRAC  = 0.60   # left fraction for thumbnail strip
META_X_OFFSET    = 16     # pixels right of the divider line

_BG       = (30, 30, 30)
_CHARCOAL = (25, 28, 30)
_CREAM    = (250, 247, 240)
_MUTED    = (136, 139, 142)
_GOLD     = (246, 190, 0)
_DIVIDER  = (50, 50, 50)
_BORDER   = (63, 67, 71)


def _load_fonts() -> tuple:
    """Return (font_bold_lg, font_md, font_sm) as PIL fonts."""
    from PIL import ImageFont
    candidates_bold = [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    candidates_reg = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def _try_load(paths: list, size: int):
        for p in paths:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
        return ImageFont.load_default()

    return (
        _try_load(candidates_bold, 16),
        _try_load(candidates_reg,  14),
        _try_load(candidates_reg,  12),
    )


def _format_duration(secs: Optional[float]) -> str:
    if secs is None:
        return "?"
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_size(path: Path) -> str:
    try:
        n: float = path.stat().st_size
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"
    except Exception:
        return "?"


def _format_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def make_video_tile(
    clip_path: Path,
    frame_paths: list[Path],
    probe_info: dict,
    width: int = TILE_WIDTH,
    height: int = TILE_HEIGHT,
    original_filename: Optional[str] = None,
) -> "Image":
    """One row tile for a video clip: thumbnail strip left, metadata right."""
    from PIL import Image, ImageDraw
    font_bold, font_md, font_sm = _load_fonts()

    img  = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    thumb_area_w = int(width * THUMB_AREA_FRAC)
    pad          = 6

    # ── Thumbnail strip ────────────────────────────────────────────
    if frame_paths:
        slot_w = thumb_area_w // len(frame_paths)
        for i, fp in enumerate(frame_paths):
            x0 = i * slot_w
            try:
                thumb = Image.open(fp).convert("RGB")
                inner_w = slot_w - pad * 2
                inner_h = height - pad * 2
                thumb.thumbnail((inner_w, inner_h), Image.Resampling.LANCZOS)
                px = x0 + pad + (inner_w - thumb.width) // 2
                py = pad + (inner_h - thumb.height) // 2
                img.paste(thumb, (px, py))
            except Exception:
                draw.rectangle([x0 + pad, pad, x0 + slot_w - pad, height - pad], outline=_BORDER)
                draw.text((x0 + pad + 4, height // 2 - 7), "?", font=font_sm, fill=_MUTED)
    else:
        # No frames — draw a placeholder
        draw.rectangle([pad, pad, thumb_area_w - pad, height - pad], outline=_BORDER)
        draw.text((thumb_area_w // 2 - 12, height // 2 - 7), "no preview", font=font_sm, fill=_MUTED)

    # Divider
    draw.line([(thumb_area_w, 0), (thumb_area_w, height)], fill=_DIVIDER, width=1)

    # ── Metadata ───────────────────────────────────────────────────
    mx = thumb_area_w + META_X_OFFSET
    y  = 10

    draw.text((mx, y), clip_path.name, font=font_bold, fill=_GOLD)
    y += 22
    if original_filename and original_filename != clip_path.name:
        draw.text((mx, y), f"orig: {original_filename}", font=font_sm, fill=_MUTED)
        y += 16

    camera = " ".join(filter(None, [probe_info.get("camera_make"), probe_info.get("camera_model")])) or ""
    codec  = probe_info.get("codec") or "?"
    profile = probe_info.get("profile") or ""
    codec_str = f"{codec} {profile}".strip()
    line2 = " — ".join(filter(None, [camera, codec_str]))
    draw.text((mx, y), line2, font=font_md, fill=_CREAM)
    y += 20

    res   = probe_info.get("resolution") or "?"
    fps   = probe_info.get("frame_rate") or "?"
    depth = probe_info.get("bit_depth") or ""
    depth_str = f"  {depth}-bit" if depth else ""
    draw.text((mx, y), f"{res}  {fps} fps{depth_str}", font=font_sm, fill=_CREAM)
    y += 18

    dur = _format_duration(probe_info.get("duration"))
    tc  = probe_info.get("timecode_start") or ""
    sz  = _format_size(clip_path)
    tc_str = f"  TC: {tc}" if tc else ""
    draw.text((mx, y), f"{dur}{tc_str}  {sz}", font=font_sm, fill=_MUTED)
    y += 18

    date = probe_info.get("date_recorded") or ""
    if date:
        draw.text((mx, y), date, font=font_sm, fill=_MUTED)

    draw.line([(0, height - 1), (width, height - 1)], fill=_DIVIDER, width=1)
    return img


def make_audio_tile(
    audio_path: Path,
    probe_info: dict,
    width: int = TILE_WIDTH,
    height: int = TILE_HEIGHT,
    original_filename: Optional[str] = None,
) -> "Image":
    """Metadata-only tile for audio files (item 48)."""
    from PIL import Image, ImageDraw
    font_bold, font_md, font_sm = _load_fonts()

    img  = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    icon_x2 = 80
    draw.rectangle([12, 12, icon_x2, height - 12], fill=(40, 40, 40), outline=_BORDER)
    draw.text((28, height // 2 - 9), "♫", font=font_bold, fill=_MUTED)

    mx = icon_x2 + META_X_OFFSET
    draw.text((mx, 10), audio_path.name, font=font_bold, fill=_GOLD)
    y_audio = 28
    if original_filename and original_filename != audio_path.name:
        draw.text((mx, y_audio), f"orig: {original_filename}", font=font_sm, fill=_MUTED)
        y_audio += 16

    fmt   = probe_info.get("format_name") or probe_info.get("audio_codec") or "?"
    sr    = probe_info.get("sample_rate") or "?"
    ch    = probe_info.get("channels") or "?"
    depth = probe_info.get("bit_depth") or ""
    depth_str = f"  {depth}-bit" if depth else ""
    draw.text((mx, y_audio), f"{fmt}  {sr} Hz  {ch} ch{depth_str}", font=font_sm, fill=_CREAM)
    y_audio += 20

    dur = _format_duration(probe_info.get("duration"))
    sz  = _format_size(audio_path)
    draw.text((mx, y_audio), f"{dur}  {sz}", font=font_sm, fill=_MUTED)
    y_audio += 20

    date = probe_info.get("date_recorded") or ""
    if date:
        draw.text((mx, y_audio), date, font=font_sm, fill=_MUTED)

    draw.line([(0, height - 1), (width, height - 1)], fill=_DIVIDER, width=1)
    return img


def make_braw_tile(
    clip_path: Path,
    sidecar_info: dict,
    width: int = TILE_WIDTH,
    height: int = TILE_HEIGHT,
) -> "Image":
    """Metadata-only tile for BRAW files; thumbnail preview not yet supported (item 47)."""
    from PIL import Image, ImageDraw
    font_bold, font_md, font_sm = _load_fonts()

    img  = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    icon_x2 = 80
    draw.rectangle([12, 12, icon_x2, height - 12], fill=(40, 40, 40), outline=_BORDER)
    draw.text((16, height // 2 - 9), "BRAW", font=font_sm, fill=_MUTED)

    mx = icon_x2 + META_X_OFFSET
    draw.text((mx, 10), clip_path.name, font=font_bold, fill=_GOLD)
    draw.text((mx, 32), "BRAW thumbnail preview not yet supported", font=font_sm, fill=_MUTED)

    y = 52
    for key in ("VideoFrameRate", "Resolution", "ISO", "WhiteBalance", "Camera", "Duration"):
        val = sidecar_info.get(key)
        if val and y < height - 14:
            draw.text((mx, y), f"{key}: {val}", font=font_sm, fill=_CREAM)
            y += 18

    draw.line([(0, height - 1), (width, height - 1)], fill=_DIVIDER, width=1)
    return img


def _make_header_tile(
    source_label: str,
    offload_date: str,
    total_clips: int,
    total_duration: Optional[float],
    total_size_bytes: int,
    width: int = TILE_WIDTH,
    height: int = 72,
) -> "Image":
    """Top header band for the contact sheet."""
    from PIL import Image, ImageDraw
    font_bold, _font_md, font_sm = _load_fonts()

    img  = Image.new("RGB", (width, height), _CHARCOAL)
    draw = ImageDraw.Draw(img)

    draw.text((16, 8), f"ST SyncTool — Contact Sheet: {source_label}", font=font_bold, fill=_GOLD)

    sz_str  = _format_bytes(float(total_size_bytes))
    dur_str = _format_duration(total_duration)
    info    = (
        f"Date: {offload_date}   Clips: {total_clips}"
        f"   Runtime: {dur_str}   Total: {sz_str}"
    )
    draw.text((16, 40), info, font=font_sm, fill=_CREAM)

    # Gold bottom border
    draw.line([(0, height - 2), (width, height - 2)], fill=_GOLD, width=2)
    return img


# ---------------------------------------------------------------------------
# Contact sheet assembly (items 49–51)
# ---------------------------------------------------------------------------

def build_contact_sheet(
    source_label: str,
    offload_date: str,
    dest_dir: Path,
    ts: str,
    max_frames: int = 4,
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    filename_originals: Optional[dict] = None,
) -> dict:
    """
    filename_originals — optional mapping of {relative_path: original_filename} built
    from the offload manifest's per-file 'original_filename' fields (Phase 7, item 61).
    When present, tiles show the normalised name as primary and the card name as secondary.
    """
    """
    Generate a contact sheet for one source from its committed destination directory.

    Scans dest_dir for media files, probes and tiles each clip, saves:
      {dest_dir}/_contact_sheet_{ts}.pdf
      ~/Documents/STSyncTool/contact_sheets/_contact_sheet_{ts}.pdf

    Returns:
      {
        "contact_sheet_path": str,
        "artifact_key": str,
        "artifact_info": {...},    # top-level generated_artifacts entry
        "per_file": {              # per-file thumbnails block
          "rel/path.mov": {"generated": bool, "frames": [...], "contact_sheet": str,
                           "error": str (optional)},
          ...
        },
      }

    Raises ImportError if Pillow is not installed.
    Thumbnail failure per clip is non-fatal: sets generated=False + error field.
    """
    if not pillow_available():
        raise ImportError(
            "Pillow is required for contact sheets — install with: pip install Pillow"
        )
    from PIL import Image
    from core.checksum import compute_all

    thumbnails_dir = dest_dir / "_thumbnails"
    thumbnails_dir.mkdir(parents=True, exist_ok=True)

    all_files  = sorted(p for p in dest_dir.rglob("*") if p.is_file() and not _is_artifact(p))
    classified = classify_files(all_files)
    media      = classified["video"] + classified["braw"] + classified["audio"]
    media.sort(key=lambda p: p.name)

    sheet_name   = f"_contact_sheet_{ts}.pdf"
    sheet_path   = dest_dir / sheet_name
    archive_path = CONTACT_SHEETS_DIR / sheet_name
    CONTACT_SHEETS_DIR.mkdir(parents=True, exist_ok=True)

    total_size     = sum(p.stat().st_size for p in media if p.exists())
    total_duration = 0.0
    per_file: dict = {}
    tiles: list    = []

    # Placeholder header — rebuilt with real duration after probing
    tiles.append(
        _make_header_tile(source_label, offload_date, len(media), None, total_size)
    )

    for done, clip in enumerate(media, 1):
        if progress_cb:
            progress_cb(done, len(media))
        if log_cb:
            log_cb(
                f"[Thumbnail] Generating thumbnails — clip {done} of {len(media)}: {clip.name}",
                "info",
            )

        try:
            rel = str(clip.relative_to(dest_dir))
        except ValueError:
            rel = clip.name

        tile_info: dict = {"generated": False, "frames": [], "contact_sheet": sheet_name}

        orig_name = (filename_originals or {}).get(rel)

        try:
            ext = clip.suffix.lower()
            if ext in BRAW_EXTENSIONS:
                sidecar = parse_braw_sidecar(clip)
                tiles.append(make_braw_tile(clip, sidecar))
                tile_info["generated"] = True

            elif ext in AUDIO_EXTENSIONS:
                probe = probe_clip(clip) if ffmpeg_available() else {}
                if probe.get("duration"):
                    total_duration += probe["duration"]
                tiles.append(make_audio_tile(clip, probe, original_filename=orig_name))
                tile_info["generated"] = True

            else:
                # Video
                probe  = probe_clip(clip) if ffmpeg_available() else {}
                dur    = probe.get("duration")
                if dur:
                    total_duration += dur
                n      = adaptive_frame_count(dur, max_frames)
                frames = extract_frames(clip, thumbnails_dir, dur or 0, n, log_cb) if (ffmpeg_available() and dur) else []
                tiles.append(make_video_tile(clip, frames, probe, original_filename=orig_name))
                tile_info["generated"] = True
                tile_info["frames"]    = [str(f.relative_to(dest_dir)) for f in frames]

        except Exception as exc:
            if log_cb:
                log_cb(f"[Thumbnail] Tile failed for {clip.name}: {exc}", "warning")
            tile_info["error"] = str(exc)

        per_file[rel] = tile_info

    # Rebuild header with real total duration
    tiles[0] = _make_header_tile(
        source_label, offload_date, len(media),
        total_duration if total_duration else None, total_size,
    )

    # ── Assemble and save ─────────────────────────────────────────────────
    if tiles:
        page_h = sum(t.height for t in tiles)
        page   = Image.new("RGB", (TILE_WIDTH, page_h), _BG)
        y = 0
        for tile in tiles:
            page.paste(tile, (0, y))
            y += tile.height
        page.save(str(sheet_path), format="PDF")
        try:
            shutil.copy2(str(sheet_path), str(archive_path))
        except Exception:
            pass

    # SHA-256 of the contact sheet for generated_artifacts block
    sheet_checksum = ""
    if sheet_path.exists():
        try:
            sheet_checksum = compute_all(sheet_path, include_xxhash=False)["sha256"]
        except Exception:
            pass

    artifact_info = {
        "type": "contact_sheet",
        "generated_by": "st_synctool",
        "source_clips": [str(c.relative_to(dest_dir)) for c in media if c.exists()],
        "checksums": {"sha256": sheet_checksum},
    }

    return {
        "contact_sheet_path": str(sheet_path),
        "artifact_key":       sheet_name,
        "artifact_info":      artifact_info,
        "per_file":           per_file,
    }


def _is_artifact(path: Path) -> bool:
    """True for internally generated files that should not appear in contact sheets."""
    name = path.name
    parent_name = path.parent.name
    return (
        name.startswith("_contact_sheet_")
        or name.startswith(".st_staging_")
        or name.startswith(".st_failure_")
        or parent_name == "_thumbnails"
        or name == "_thumbnails"
    )
