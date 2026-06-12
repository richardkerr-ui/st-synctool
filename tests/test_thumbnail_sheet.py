"""Tests for core/thumbnail.py — sheet_assembly."""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build minimal PIL stubs so tests run without Pillow installed
# ---------------------------------------------------------------------------

def _make_pil_stub():
    """Return a minimal PIL package stub that satisfies thumbnail imports."""
    pil = types.ModuleType("PIL")

    # Image stub
    image_mod = types.ModuleType("PIL.Image")

    class _FakeImage:
        def __init__(self, mode="RGB", size=(100, 50), color=(0, 0, 0)):
            self.mode   = mode
            self.width  = size[0]
            self.height = size[1]
            self._color = color
            self.paste  = MagicMock()
            self.save   = MagicMock()

        # Image.new class-method equivalent
        @classmethod
        def new(cls, mode, size, color=0):
            obj = cls(mode, size, color if isinstance(color, tuple) else (0, 0, 0))
            return obj

    image_mod.Image = _FakeImage
    image_mod.new   = _FakeImage.new
    # Resampling namespace used by make_video_tile thumbnail
    resampling = types.SimpleNamespace(LANCZOS=1)
    _FakeImage.Resampling = resampling
    image_mod.Resampling  = resampling
    pil.Image = image_mod

    # ImageDraw stub
    draw_mod = types.ModuleType("PIL.ImageDraw")

    class _FakeDraw:
        def __init__(self, img):
            self.text      = MagicMock()
            self.line      = MagicMock()
            self.rectangle = MagicMock()

    draw_mod.Draw    = _FakeDraw
    draw_mod.ImageDraw = _FakeDraw
    pil.ImageDraw    = draw_mod

    # ImageFont stub
    font_mod = types.ModuleType("PIL.ImageFont")
    _fake_font = object()
    font_mod.truetype    = MagicMock(return_value=_fake_font)
    font_mod.load_default = MagicMock(return_value=_fake_font)
    pil.ImageFont = font_mod

    return pil, image_mod, draw_mod, font_mod


def _inject_pil(monkeypatch):
    """Inject PIL stub into sys.modules and return the stub package."""
    pil, image_mod, draw_mod, font_mod = _make_pil_stub()
    monkeypatch.setitem(sys.modules, "PIL",           pil)
    monkeypatch.setitem(sys.modules, "PIL.Image",     image_mod)
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", draw_mod)
    monkeypatch.setitem(sys.modules, "PIL.ImageFont", font_mod)
    return pil, image_mod, draw_mod, font_mod


# ---------------------------------------------------------------------------
# TestMakeHeaderTile
# ---------------------------------------------------------------------------

class TestMakeHeaderTile:
    """Tests for _make_header_tile (lines 785-813)."""

    def _call(self, monkeypatch, **kwargs):
        """Inject PIL stub and call _make_header_tile."""
        _inject_pil(monkeypatch)
        from core.thumbnail import _make_header_tile
        defaults = dict(
            source_label="A001",
            offload_date="2026-06-12",
            total_clips=5,
            total_duration=300.0,
            total_size_bytes=1_073_741_824,  # 1 GB
        )
        defaults.update(kwargs)
        return _make_header_tile(**defaults)

    def test_happy_path_returns_image_with_correct_dimensions(self, monkeypatch):
        img = self._call(monkeypatch)
        # Default height is 72; width is TILE_WIDTH (1800)
        assert img.height == 72
        assert img.width  == 1800

    def test_custom_width_and_height(self, monkeypatch):
        img = self._call(monkeypatch, width=800, height=48)
        assert img.width  == 800
        assert img.height == 48

    def test_none_duration_renders_without_error(self, monkeypatch):
        # total_duration=None should not raise — _format_duration handles None
        img = self._call(monkeypatch, total_duration=None)
        assert img is not None

    def test_draw_text_called_with_source_label(self, monkeypatch):
        """Header tile must write the source label into the image."""
        _inject_pil(monkeypatch)
        from core.thumbnail import _make_header_tile

        # Capture Draw instance to inspect calls
        draw_calls = []

        class _CaptureDraw:
            def __init__(self, img):
                self.text      = MagicMock(side_effect=lambda *a, **kw: draw_calls.append(("text", a, kw)))
                self.line      = MagicMock()
                self.rectangle = MagicMock()

        import PIL.ImageDraw as _id_mod
        monkeypatch.setattr(_id_mod, "Draw", _CaptureDraw)

        _make_header_tile("MY_SOURCE", "2026-06-12", 3, 60.0, 512)

        rendered_texts = [str(a) for (kind, a, kw) in draw_calls if kind == "text"]
        assert any("MY_SOURCE" in t for t in rendered_texts)

    def test_zero_size_bytes_formats_without_error(self, monkeypatch):
        # Edge case: zero-byte total
        img = self._call(monkeypatch, total_size_bytes=0)
        assert img is not None

    def test_draw_bottom_border_line_called(self, monkeypatch):
        """The gold bottom border line must always be drawn."""
        _inject_pil(monkeypatch)
        from core.thumbnail import _make_header_tile

        line_calls = []

        class _CaptureDraw:
            def __init__(self, img):
                self.text      = MagicMock()
                self.rectangle = MagicMock()
                self.line      = MagicMock(side_effect=lambda *a, **kw: line_calls.append((a, kw)))

        import PIL.ImageDraw as _id_mod
        monkeypatch.setattr(_id_mod, "Draw", _CaptureDraw)

        _make_header_tile("X", "2026-01-01", 1, 10.0, 100)
        assert len(line_calls) >= 1, "Expected at least one line() call for the bottom border"


# ---------------------------------------------------------------------------
# TestIsArtifact
# ---------------------------------------------------------------------------

class TestIsArtifact:
    """Tests for _is_artifact (lines 1028-1038)."""

    def _call(self, path_str: str) -> bool:
        from core.thumbnail import _is_artifact
        return _is_artifact(Path(path_str))

    # Happy path — files that are NOT artifacts
    def test_normal_video_file_is_not_artifact(self):
        assert self._call("/dest/CLIP_001.mov") is False

    def test_normal_audio_file_is_not_artifact(self):
        assert self._call("/dest/A001_001.wav") is False

    def test_normal_nested_file_is_not_artifact(self):
        assert self._call("/dest/subdirectory/clip.mp4") is False

    # Failure / positive-detection paths
    def test_contact_sheet_pdf_is_artifact(self):
        assert self._call("/dest/_contact_sheet_20260612T120000.pdf") is True

    def test_st_staging_file_is_artifact(self):
        assert self._call("/dest/.st_staging_lock") is True

    def test_st_failure_file_is_artifact(self):
        assert self._call("/dest/.st_failure_report.json") is True

    def test_file_inside_thumbnails_dir_is_artifact(self):
        assert self._call("/dest/_thumbnails/clip_f1.jpg") is True

    def test_thumbnails_dir_itself_is_artifact(self):
        # A file literally named "_thumbnails" (edge case)
        assert self._call("/dest/_thumbnails") is True

    # Edge cases
    def test_contact_sheet_prefix_anywhere_in_name_triggers(self):
        # Prefix check — partial match at start only
        assert self._call("/dest/_contact_sheet_x.jpg") is True

    def test_non_artifact_with_similar_substring(self):
        # "contact_sheet" in the middle of the name should NOT trigger
        assert self._call("/dest/my_contact_sheet_backup.pdf") is False

    def test_deep_nested_thumbnails_parent(self):
        # Parent dir is _thumbnails regardless of depth
        assert self._call("/dest/project/_thumbnails/frame.jpg") is True


# ---------------------------------------------------------------------------
# TestBuildContactSheet
# ---------------------------------------------------------------------------

class TestBuildContactSheet:
    """Tests for build_contact_sheet (lines 820-1025)."""

    # ------------------------------------------------------------------
    # Shared fixture for PIL and external dependencies
    # ------------------------------------------------------------------

    def _base_patches(self, monkeypatch, tmp_path):
        """Inject PIL stub and neutral mocks for all external calls."""
        _inject_pil(monkeypatch)

        # pillow_available must return True so the function proceeds
        monkeypatch.setattr("core.thumbnail.pillow_available", lambda: True)

        # No ffmpeg by default — avoids frame-extraction branching
        monkeypatch.setattr("core.thumbnail.ffmpeg_available", lambda: False)

        # No REDline
        monkeypatch.setattr("core.thumbnail.check_redline", lambda: None)

        # find_rdc_clips returns empty list (no R3D clips)
        monkeypatch.setattr("core.thumbnail.find_rdc_clips", lambda dest: [])

        # classify_files returns everything as video by default — callers can override
        monkeypatch.setattr(
            "core.thumbnail.classify_files",
            lambda files: {"video": list(files), "audio": [], "braw": [], "other": []},
        )

        # Stub make_video_tile to return a tiny fake image
        def _fake_video_tile(clip_path, frame_paths, probe_info, **kwargs):
            from PIL.Image import Image
            return Image.new("RGB", (1800, 220), (0, 0, 0))

        monkeypatch.setattr("core.thumbnail.make_video_tile", _fake_video_tile)
        monkeypatch.setattr("core.thumbnail.make_audio_tile",
                            lambda *a, **kw: _fake_video_tile(None, [], {}, **kw))
        monkeypatch.setattr("core.thumbnail.make_braw_tile",
                            lambda *a, **kw: _fake_video_tile(None, [], {}, **kw))

        # Stub _make_header_tile
        def _fake_header(*a, **kw):
            from PIL.Image import Image
            return Image.new("RGB", (1800, 72), (0, 0, 0))

        monkeypatch.setattr("core.thumbnail._make_header_tile", _fake_header)

        # probe_clip returns minimal metadata
        monkeypatch.setattr("core.thumbnail.probe_clip", lambda p: {"duration": 10.0})
        monkeypatch.setattr("core.thumbnail.adaptive_frame_count", lambda dur, max_f: 2)
        monkeypatch.setattr("core.thumbnail.extract_frames", lambda *a, **kw: [])

        # compute_all used for checksum of the saved sheet
        import unittest.mock as _mock
        checksum_mock = _mock.MagicMock(return_value={"sha256": "abc123"})
        monkeypatch.setattr("core.checksum.compute_all", checksum_mock, raising=False)

        # shutil.copy2 — suppress archive copy
        monkeypatch.setattr("shutil.copy2", lambda *a, **kw: None)

        return tmp_path

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_returns_expected_keys(self, monkeypatch, tmp_path):
        """build_contact_sheet must return the four documented top-level keys."""
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "clip001.mov").write_bytes(b"\x00" * 128)

        self._base_patches(monkeypatch, tmp_path)

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="CAM_A",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T120000",
        )

        assert set(result.keys()) == {"contact_sheet_path", "artifact_key", "artifact_info", "per_file"}

    def test_per_file_entry_for_each_media_clip(self, monkeypatch, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        clips = ["clip001.mov", "clip002.mov"]
        for c in clips:
            (dest / c).write_bytes(b"\x00" * 128)

        self._base_patches(monkeypatch, tmp_path)

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="CAM_A",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T120001",
        )

        for c in clips:
            assert c in result["per_file"], f"Expected per_file entry for {c}"

    def test_pdf_saved_to_dest_dir(self, monkeypatch, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "clip001.mov").write_bytes(b"\x00" * 128)

        self._base_patches(monkeypatch, tmp_path)

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="CAM_A",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T130000",
        )

        assert result["contact_sheet_path"].endswith(".pdf")
        assert result["artifact_key"].startswith("_contact_sheet_")
        assert result["artifact_key"].endswith(".pdf")

    def test_artifact_info_structure(self, monkeypatch, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "clip001.mov").write_bytes(b"\x00" * 64)

        self._base_patches(monkeypatch, tmp_path)

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="SRC",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T140000",
        )

        ai = result["artifact_info"]
        assert ai["type"]         == "contact_sheet"
        assert ai["generated_by"] == "st_synctool"
        assert "checksums" in ai
        assert "source_clips" in ai

    # ------------------------------------------------------------------
    # Failure / non-fatal tile error path
    # ------------------------------------------------------------------

    def test_tile_failure_is_nonfatal_sets_error_field(self, monkeypatch, tmp_path):
        """A per-clip tile crash must be caught; per_file entry gets generated=False and error."""
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "bad_clip.mov").write_bytes(b"\x00" * 64)

        self._base_patches(monkeypatch, tmp_path)

        # Override make_video_tile to always raise
        monkeypatch.setattr(
            "core.thumbnail.make_video_tile",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("forced tile failure")),
        )

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="CAM_B",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T150000",
        )

        entry = result["per_file"]["bad_clip.mov"]
        assert entry["generated"] is False
        assert "forced tile failure" in entry["error"]

    def test_missing_pillow_raises_import_error(self, monkeypatch, tmp_path):
        """ImportError must be raised when Pillow is not available."""
        monkeypatch.setattr("core.thumbnail.pillow_available", lambda: False)

        from core.thumbnail import build_contact_sheet
        with pytest.raises(ImportError, match="Pillow"):
            build_contact_sheet(
                source_label="CAM_C",
                offload_date="2026-06-12",
                dest_dir=tmp_path,
                ts="20260612T160000",
            )

    def test_log_cb_called_per_clip(self, monkeypatch, tmp_path):
        """log_cb must be called at least once per media clip."""
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "clip001.mov").write_bytes(b"\x00" * 64)

        self._base_patches(monkeypatch, tmp_path)

        log_messages = []

        def _log(msg, level):
            log_messages.append((msg, level))

        from core.thumbnail import build_contact_sheet
        build_contact_sheet(
            source_label="CAM_D",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T170000",
            log_cb=_log,
        )

        assert any("clip001.mov" in msg for msg, _ in log_messages)

    def test_progress_cb_called_per_clip(self, monkeypatch, tmp_path):
        """progress_cb must receive (done, total) for each clip."""
        dest = tmp_path / "dest"
        dest.mkdir()
        for i in range(3):
            (dest / f"clip{i:03d}.mov").write_bytes(b"\x00" * 64)

        self._base_patches(monkeypatch, tmp_path)

        progress_calls = []

        from core.thumbnail import build_contact_sheet
        build_contact_sheet(
            source_label="CAM_E",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T180000",
            progress_cb=lambda done, total: progress_calls.append((done, total)),
        )

        assert len(progress_calls) == 3
        totals = {t for _, t in progress_calls}
        assert totals == {3}

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_dest_dir_produces_empty_per_file(self, monkeypatch, tmp_path):
        """An empty destination directory must return per_file={}."""
        dest = tmp_path / "empty_dest"
        dest.mkdir()

        self._base_patches(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "core.thumbnail.classify_files",
            lambda files: {"video": [], "audio": [], "braw": [], "other": []},
        )

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="CAM_F",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T190000",
        )

        assert result["per_file"] == {}

    def test_filename_originals_stored_in_per_file(self, monkeypatch, tmp_path):
        """filename_originals mapping must be passed through for tile rendering."""
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "A001_001.mov").write_bytes(b"\x00" * 64)

        self._base_patches(monkeypatch, tmp_path)

        captured_orig = {}

        def _spy_tile(clip_path, frame_paths, probe_info, original_filename=None, **kw):
            captured_orig[clip_path.name] = original_filename
            from PIL.Image import Image
            return Image.new("RGB", (1800, 220), (0, 0, 0))

        monkeypatch.setattr("core.thumbnail.make_video_tile", _spy_tile)

        from core.thumbnail import build_contact_sheet
        build_contact_sheet(
            source_label="CAM_G",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T200000",
            filename_originals={"A001_001.mov": "OriginalCard_001.mov"},
        )

        assert captured_orig.get("A001_001.mov") == "OriginalCard_001.mov"

    def test_artifact_files_excluded_from_media_scan(self, monkeypatch, tmp_path):
        """Contact sheet PDFs and thumbnails present in dest_dir must not appear in per_file."""
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "clip001.mov").write_bytes(b"\x00" * 64)
        (dest / "_contact_sheet_old.pdf").write_bytes(b"\x00" * 32)
        thumbs = dest / "_thumbnails"
        thumbs.mkdir()
        (thumbs / "clip001_f1.jpg").write_bytes(b"\x00" * 16)

        self._base_patches(monkeypatch, tmp_path)

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="CAM_H",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T210000",
        )

        per_file_keys = set(result["per_file"].keys())
        assert "_contact_sheet_old.pdf" not in per_file_keys
        assert not any("_thumbnails" in k for k in per_file_keys)

    def test_audio_clip_handled_correctly(self, monkeypatch, tmp_path):
        """Audio clips must produce a per_file entry with generated=True."""
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "sound001.wav").write_bytes(b"\x00" * 64)

        self._base_patches(monkeypatch, tmp_path)

        monkeypatch.setattr(
            "core.thumbnail.classify_files",
            lambda files: {"video": [], "audio": list(files), "braw": [], "other": []},
        )

        audio_tile_called = []

        def _fake_audio_tile(audio_path, probe_info, **kw):
            audio_tile_called.append(audio_path.name)
            from PIL.Image import Image
            return Image.new("RGB", (1800, 220), (0, 0, 0))

        monkeypatch.setattr("core.thumbnail.make_audio_tile", _fake_audio_tile)

        from core.thumbnail import build_contact_sheet
        result = build_contact_sheet(
            source_label="CAM_I",
            offload_date="2026-06-12",
            dest_dir=dest,
            ts="20260612T220000",
        )

        assert "sound001.wav" in audio_tile_called
        assert result["per_file"]["sound001.wav"]["generated"] is True
