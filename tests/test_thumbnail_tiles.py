"""Tests for core/thumbnail.py — tiles_video_audio."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_mock_image(width: int = 1800, height: int = 220) -> MagicMock:
    """Return a minimal MagicMock that quacks like a PIL Image."""
    img = MagicMock()
    img.width = width
    img.height = height
    return img


def _make_mock_draw() -> MagicMock:
    return MagicMock()


def _font_triple() -> tuple:
    """Three dummy font objects (bold, md, sm)."""
    return MagicMock(), MagicMock(), MagicMock()


@contextmanager
def _pil_ctx(mock_image_cls, mock_imagedraw_cls):
    """
    Inject mock PIL submodules so that 'from PIL import Image, ImageDraw'
    inside the target functions picks up our mocks regardless of whether PIL
    has already been imported into sys.modules.

    CPython resolves 'from pkg import attr' by first looking up
    sys.modules['pkg.attr'], but if that module is already cached in the
    package's __init__ namespace the attribute lookup wins. We therefore
    patch both sys.modules AND the PIL package object's attributes.
    """
    import importlib
    import PIL as _pil_pkg

    mock_image_cls.__name__ = "Image"

    orig_image_mod    = sys.modules.get("PIL.Image")
    orig_imagedraw_mod = sys.modules.get("PIL.ImageDraw")
    orig_image_attr    = getattr(_pil_pkg, "Image", None)
    orig_imagedraw_attr = getattr(_pil_pkg, "ImageDraw", None)

    # Replace both sys.modules entry and package attribute so that
    # 'from PIL import Image' always returns our mock.
    sys.modules["PIL.Image"]    = mock_image_cls       # type: ignore[assignment]
    sys.modules["PIL.ImageDraw"] = mock_imagedraw_cls  # type: ignore[assignment]
    _pil_pkg.Image    = mock_image_cls                 # type: ignore[assignment]
    _pil_pkg.ImageDraw = mock_imagedraw_cls            # type: ignore[assignment]
    try:
        yield
    finally:
        if orig_image_mod is None:
            sys.modules.pop("PIL.Image", None)
        else:
            sys.modules["PIL.Image"] = orig_image_mod

        if orig_imagedraw_mod is None:
            sys.modules.pop("PIL.ImageDraw", None)
        else:
            sys.modules["PIL.ImageDraw"] = orig_imagedraw_mod

        if orig_image_attr is None:
            try:
                delattr(_pil_pkg, "Image")
            except AttributeError:
                pass
        else:
            _pil_pkg.Image = orig_image_attr           # type: ignore[assignment]

        if orig_imagedraw_attr is None:
            try:
                delattr(_pil_pkg, "ImageDraw")
            except AttributeError:
                pass
        else:
            _pil_pkg.ImageDraw = orig_imagedraw_attr   # type: ignore[assignment]


_LOAD_FONTS_PATH  = "core.thumbnail._load_fonts"
_FORMAT_SIZE_PATH = "core.thumbnail._format_size"


# ---------------------------------------------------------------------------
# TestMakeVideoTile
# ---------------------------------------------------------------------------

class TestMakeVideoTile:
    """Tests for make_video_tile."""

    def _setup_image_mocks(self):
        """Return (mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance)."""
        img_instance = _make_mock_image()

        mock_image_cls = MagicMock(name="Image_module")
        mock_image_cls.new.return_value = img_instance
        mock_image_cls.Resampling = MagicMock()
        mock_image_cls.Resampling.LANCZOS = MagicMock()

        draw_instance = _make_mock_draw()
        mock_imagedraw_cls = MagicMock(name="ImageDraw_module")
        mock_imagedraw_cls.Draw.return_value = draw_instance

        return mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance

    # ------------------------------------------------------------------
    # Happy path — with frames and full probe_info
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="500.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_happy_path_returns_image(self, mock_load_fonts, _mock_size, tmp_path):
        """Returns the PIL image object created by Image.new."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        frame_file = tmp_path / "frame0.jpg"
        frame_file.write_bytes(b"fake")
        thumb_mock = _make_mock_image(200, 150)
        thumb_mock.convert.return_value = thumb_mock
        mock_image_cls.open.return_value = thumb_mock

        probe = {
            "codec": "ProRes",
            "profile": "HQ",
            "resolution": "3840x2160",
            "frame_rate": "23.976",
            "bit_depth": 10,
            "duration": 125.0,
            "timecode_start": "01:00:00:00",
            "date_recorded": "2024-03-15",
            "camera_make": "ARRI",
            "camera_model": "ALEXA 35",
        }
        clip = tmp_path / "A001C001_240315.mov"
        clip.write_bytes(b"fake clip")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_video_tile
            result = make_video_tile(clip, [frame_file], probe)

        assert result is img_instance

    # ------------------------------------------------------------------
    # Happy path — filename drawn in title line
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="100.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_metadata_text_drawn_for_filename(self, mock_load_fonts, _mock_size, tmp_path):
        """Clip filename is drawn as the title line."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "CLIP_0042.mov"
        clip.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_video_tile
            make_video_tile(clip, [], {"codec": "H.264", "duration": 30.0})

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("CLIP_0042.mov" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Failure path — broken frame file falls back to placeholder
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_broken_frame_draws_placeholder(self, mock_load_fonts, _mock_size, tmp_path):
        """When Image.open raises, a rectangle placeholder is drawn instead."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()
        mock_image_cls.open.side_effect = OSError("corrupted frame")

        clip = tmp_path / "broken.mov"
        clip.write_bytes(b"data")
        frame = tmp_path / "bad_frame.jpg"
        frame.write_bytes(b"bad")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_video_tile
            result = make_video_tile(clip, [frame], {})

        assert result is img_instance
        draw_instance.rectangle.assert_called()

    # ------------------------------------------------------------------
    # Edge case — empty frame list draws "no preview" placeholder
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_no_frames_draws_no_preview_placeholder(self, mock_load_fonts, _mock_size, tmp_path):
        """Empty frame_paths triggers the 'no preview' placeholder branch."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "no_frames.mxf"
        clip.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_video_tile
            result = make_video_tile(clip, [], {})

        assert result is img_instance
        draw_instance.rectangle.assert_called()
        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("no preview" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Edge case — original_filename different from clip name
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="200.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_original_filename_different_is_drawn(self, mock_load_fonts, _mock_size, tmp_path):
        """When original_filename differs from clip_path.name it is drawn as 'orig: ...'."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "renamed_clip.mov"
        clip.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_video_tile
            make_video_tile(clip, [], {}, original_filename="ORIGINAL_A001C001.mov")

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("orig: ORIGINAL_A001C001.mov" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Edge case — original_filename same as clip name (no extra line)
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="50.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_original_filename_same_not_drawn(self, mock_load_fonts, _mock_size, tmp_path):
        """When original_filename equals clip_path.name the orig line is suppressed."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "same_name.mov"
        clip.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_video_tile
            make_video_tile(clip, [], {}, original_filename="same_name.mov")

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert not any("orig:" in tc for tc in text_calls)


# ---------------------------------------------------------------------------
# TestMakeAudioTile
# ---------------------------------------------------------------------------

class TestMakeAudioTile:
    """Tests for make_audio_tile."""

    def _setup_image_mocks(self):
        img_instance = _make_mock_image()
        mock_image_cls = MagicMock(name="Image_module")
        mock_image_cls.new.return_value = img_instance

        draw_instance = _make_mock_draw()
        mock_imagedraw_cls = MagicMock(name="ImageDraw_module")
        mock_imagedraw_cls.Draw.return_value = draw_instance

        return mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance

    # ------------------------------------------------------------------
    # Happy path — full probe_info
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="48.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_happy_path_returns_image(self, mock_load_fonts, _mock_size, tmp_path):
        """Returns the PIL image and draws the filename in the title line."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        audio = tmp_path / "SFX_RAIN.wav"
        audio.write_bytes(b"riff")

        probe = {
            "format_name": "wav",
            "sample_rate": "48000",
            "channels": 2,
            "bit_depth": 24,
            "duration": 62.5,
            "date_recorded": "2024-01-10",
        }

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_audio_tile
            result = make_audio_tile(audio, probe)

        assert result is img_instance
        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("SFX_RAIN.wav" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Happy path — audio_codec fallback when format_name is absent
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_audio_codec_fallback_when_no_format_name(self, mock_load_fonts, _mock_size, tmp_path):
        """Uses audio_codec when format_name is absent."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        audio = tmp_path / "ambience.aif"
        audio.write_bytes(b"aiff")

        probe = {"audio_codec": "pcm_s24le", "sample_rate": "44100", "channels": 1}

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_audio_tile
            make_audio_tile(audio, probe)

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("pcm_s24le" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Failure path — completely empty probe_info
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_empty_probe_does_not_raise(self, mock_load_fonts, _mock_size, tmp_path):
        """An empty probe dict must not raise; unknown fields default to '?'."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        audio = tmp_path / "mystery.wav"
        audio.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_audio_tile
            result = make_audio_tile(audio, {})

        assert result is img_instance

    # ------------------------------------------------------------------
    # Edge case — original_filename differs
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="10.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_original_filename_different_is_drawn(self, mock_load_fonts, _mock_size, tmp_path):
        """When original_filename differs from audio_path.name it is drawn."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        audio = tmp_path / "renamed.wav"
        audio.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_audio_tile
            make_audio_tile(audio, {}, original_filename="ORIGINAL_SCENE.wav")

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("orig: ORIGINAL_SCENE.wav" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Edge case — bottom separator line is always drawn
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="5.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_bottom_separator_line_drawn(self, mock_load_fonts, _mock_size, tmp_path):
        """A bottom separator draw.line call is always issued."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        audio = tmp_path / "tone.wav"
        audio.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_audio_tile
            make_audio_tile(audio, {"duration": 5.0})

        draw_instance.line.assert_called()

    # ------------------------------------------------------------------
    # Edge case — date_recorded drawn when present
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="2.0 MB")
    @patch(_LOAD_FONTS_PATH)
    def test_date_recorded_drawn_when_present(self, mock_load_fonts, _mock_size, tmp_path):
        """date_recorded appears in draw.text when provided."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        audio = tmp_path / "dated.flac"
        audio.write_bytes(b"data")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_audio_tile
            make_audio_tile(audio, {"date_recorded": "2023-12-25"})

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("2023-12-25" in tc for tc in text_calls)


# ---------------------------------------------------------------------------
# TestMakeBrawTile
# ---------------------------------------------------------------------------

class TestMakeBrawTile:
    """Tests for make_braw_tile."""

    def _setup_image_mocks(self):
        img_instance = _make_mock_image()
        mock_image_cls = MagicMock(name="Image_module")
        mock_image_cls.new.return_value = img_instance

        draw_instance = _make_mock_draw()
        mock_imagedraw_cls = MagicMock(name="ImageDraw_module")
        mock_imagedraw_cls.Draw.return_value = draw_instance

        return mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance

    # ------------------------------------------------------------------
    # Happy path — sidecar with all recognised keys
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="4.2 GB")
    @patch(_LOAD_FONTS_PATH)
    def test_happy_path_returns_image(self, mock_load_fonts, _mock_size, tmp_path):
        """Returns the PIL image created by Image.new."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "A001_C001.braw"
        clip.write_bytes(b"braw")

        sidecar = {
            "VideoFrameRate": "23.976",
            "Resolution": "6K",
            "ISO": "800",
            "WhiteBalance": "5600",
            "Camera": "Blackmagic URSA Mini Pro 12K",
            "Duration": "00:01:23:15",
        }

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_braw_tile
            result = make_braw_tile(clip, sidecar)

        assert result is img_instance

    # ------------------------------------------------------------------
    # Happy path — sidecar keys appear as "Key: Value" draw.text lines
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_sidecar_keys_drawn(self, mock_load_fonts, _mock_size, tmp_path):
        """Known sidecar keys (ISO, Resolution, etc.) appear in draw.text calls."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "meta_clip.braw"
        clip.write_bytes(b"braw")

        sidecar = {"ISO": "3200", "Resolution": "4K DCI"}

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_braw_tile
            make_braw_tile(clip, sidecar)

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("ISO: 3200" in tc for tc in text_calls)
        assert any("Resolution: 4K DCI" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Failure path — empty sidecar
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_empty_sidecar_does_not_raise(self, mock_load_fonts, _mock_size, tmp_path):
        """An empty sidecar dict must not raise."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "no_meta.braw"
        clip.write_bytes(b"braw")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_braw_tile
            result = make_braw_tile(clip, {})

        assert result is img_instance

    # ------------------------------------------------------------------
    # Edge case — unsupported-preview notice is always drawn
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_unsupported_preview_notice_drawn(self, mock_load_fonts, _mock_size, tmp_path):
        """The 'BRAW thumbnail preview not yet supported' notice is always rendered."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "clip.braw"
        clip.write_bytes(b"braw")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_braw_tile
            make_braw_tile(clip, {})

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("not yet supported" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Edge case — filename drawn as title
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_clip_filename_drawn_as_title(self, mock_load_fonts, _mock_size, tmp_path):
        """clip_path.name is drawn in gold as the tile title."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "SCENE_042_A.braw"
        clip.write_bytes(b"braw")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_braw_tile
            make_braw_tile(clip, {})

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert any("SCENE_042_A.braw" in tc for tc in text_calls)

    # ------------------------------------------------------------------
    # Edge case — bottom separator line always drawn
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_bottom_separator_line_drawn(self, mock_load_fonts, _mock_size, tmp_path):
        """A bottom separator draw.line call is always issued."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "clip.braw"
        clip.write_bytes(b"braw")

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_braw_tile
            make_braw_tile(clip, {"VideoFrameRate": "25"})

        draw_instance.line.assert_called()

    # ------------------------------------------------------------------
    # Edge case — None sidecar values are silently skipped
    # ------------------------------------------------------------------

    @patch(_FORMAT_SIZE_PATH, return_value="?")
    @patch(_LOAD_FONTS_PATH)
    def test_none_sidecar_values_skipped(self, mock_load_fonts, _mock_size, tmp_path):
        """Sidecar entries with None values must not produce draw.text calls."""
        mock_load_fonts.return_value = _font_triple()
        mock_image_cls, img_instance, mock_imagedraw_cls, draw_instance = self._setup_image_mocks()

        clip = tmp_path / "partial.braw"
        clip.write_bytes(b"braw")

        sidecar = {
            "VideoFrameRate": None,
            "Resolution": None,
            "ISO": "400",
        }

        with _pil_ctx(mock_image_cls, mock_imagedraw_cls):
            from core.thumbnail import make_braw_tile
            make_braw_tile(clip, sidecar)

        text_calls = [str(c) for c in draw_instance.text.call_args_list]
        assert not any("VideoFrameRate: None" in tc for tc in text_calls)
        assert not any("Resolution: None" in tc for tc in text_calls)
        assert any("ISO: 400" in tc for tc in text_calls)
