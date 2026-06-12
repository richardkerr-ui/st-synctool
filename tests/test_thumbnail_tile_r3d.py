"""Tests for core/thumbnail.py — tile_r3d."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_fonts():
    """Return three distinct MagicMock font objects."""
    return MagicMock(name="font_bold"), MagicMock(name="font_md"), MagicMock(name="font_sm")


def _make_draw_image_mocks():
    """Return (mock_img, mock_draw) pre-wired so ImageDraw.Draw returns mock_draw."""
    mock_img = MagicMock(name="Image_instance")
    mock_draw = MagicMock(name="Draw_instance")
    return mock_img, mock_draw


def _minimal_meta():
    return {
        "resolution": "4096x2160",
        "frame_rate": "23.976",
        "duration": 10.0,
        "timecode_start": "01:00:00:00",
        "segment_count": 1,
        "camera_model": "DSMC2 MONSTRO 8K VV",
        "_rmd_raw": {
            "camera_serial": "SN12345",
            "redcode_ratio": "8",
            "iso": "800",
            "white_balance": "5600",
            "focal_length": "35mm",
        },
    }


# ---------------------------------------------------------------------------
# TestMakeR3dTile
# ---------------------------------------------------------------------------

class TestMakeR3dTile:
    """Tests for make_r3d_tile (lines 471-559)."""

    # ------------------------------------------------------------------
    # Happy path — with frame_paths provided
    # ------------------------------------------------------------------

    def test_happy_path_with_frames_pastes_thumbnails(self, tmp_path):
        """Returns an Image and pastes one thumbnail per frame when frames exist."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        frame1 = tmp_path / "frame1.jpg"
        frame2 = tmp_path / "frame2.jpg"
        frame1.write_bytes(b"x")
        frame2.write_bytes(b"x")

        mock_img, mock_draw = _make_draw_image_mocks()
        mock_thumb1 = MagicMock(name="thumb1")
        mock_thumb1.width = 200
        mock_thumb1.height = 112
        mock_thumb2 = MagicMock(name="thumb2")
        mock_thumb2.width = 200
        mock_thumb2.height = 112

        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.Image.open", side_effect=[mock_thumb1, mock_thumb2]) as mock_open,
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            mock_thumb1.convert.return_value = mock_thumb1
            mock_thumb2.convert.return_value = mock_thumb2

            result = make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[frame1, frame2],
                meta=_minimal_meta(),
                redline_present=True,
            )

        assert result is mock_img
        assert mock_open.call_count == 2
        assert mock_img.paste.call_count == 2

    def test_happy_path_metadata_written_to_draw(self, tmp_path):
        """Metadata fields (clip name, camera, resolution) are rendered via draw.text."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "B002_C003.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta=_minimal_meta(),
                redline_present=True,
            )

        # Collect all text strings passed to draw.text
        drawn_texts = [c.args[1] for c in mock_draw.text.call_args_list]

        assert any("B002_C003.RDC" in t for t in drawn_texts), "clip name not rendered"
        assert any("DSMC2 MONSTRO 8K VV" in t for t in drawn_texts), "camera model not rendered"
        assert any("4096x2160" in t for t in drawn_texts), "resolution not rendered"
        assert any("REDCODE" in t for t in drawn_texts), "REDCODE ratio not rendered"

    def test_happy_path_returns_pil_image(self, tmp_path):
        """Return value is the Image object created by Image.new."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "C003_C005.RDC"
        rdc.mkdir()

        sentinel = MagicMock(name="sentinel_image")
        mock_draw = MagicMock()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=sentinel),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            result = make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta={},
                redline_present=False,
            )

        assert result is sentinel

    # ------------------------------------------------------------------
    # Failure paths
    # ------------------------------------------------------------------

    def test_broken_frame_file_draws_placeholder_rect(self, tmp_path):
        """When Image.open raises, a placeholder rectangle is drawn instead of crashing."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()
        bad_frame = tmp_path / "bad.jpg"
        bad_frame.write_bytes(b"not an image")

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.Image.open", side_effect=OSError("corrupt")),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            result = make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[bad_frame],
                meta=_minimal_meta(),
                redline_present=True,
            )

        assert result is mock_img
        # paste must NOT have been called (the error path draws a rectangle instead)
        mock_img.paste.assert_not_called()
        # rectangle should have been called at least once for the broken-frame slot
        assert mock_draw.rectangle.call_count >= 1

    def test_no_frames_and_redline_absent_shows_install_prompt(self, tmp_path):
        """When no frames and REDline is absent, the install-prompt text is rendered."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta={},
                redline_present=False,
            )

        drawn_texts = [c.args[1] for c in mock_draw.text.call_args_list]
        assert any("REDCINE-X PRO" in t for t in drawn_texts), "install prompt not rendered"

    def test_no_frames_and_redline_present_omits_install_prompt(self, tmp_path):
        """When REDline is present but no frames available, the install prompt is suppressed."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta={},
                redline_present=True,
            )

        drawn_texts = [c.args[1] for c in mock_draw.text.call_args_list]
        assert not any("REDCINE-X PRO" in t for t in drawn_texts), "install prompt should be absent"

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_meta_does_not_raise(self, tmp_path):
        """make_r3d_tile must not raise even when meta is an empty dict."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            result = make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta={},
                redline_present=False,
            )

        assert result is mock_img

    def test_single_frame_fills_full_thumb_area(self, tmp_path):
        """With exactly one frame, Image.open is called once and paste is called once."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()
        frame = tmp_path / "frame0.jpg"
        frame.write_bytes(b"x")

        mock_img, mock_draw = _make_draw_image_mocks()
        mock_thumb = MagicMock(name="thumb")
        mock_thumb.width = 600
        mock_thumb.height = 200
        mock_thumb.convert.return_value = mock_thumb

        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.Image.open", return_value=mock_thumb) as mock_open,
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            result = make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[frame],
                meta=_minimal_meta(),
                redline_present=True,
            )

        assert result is mock_img
        mock_open.assert_called_once_with(frame)
        mock_img.paste.assert_called_once()

    def test_segment_count_plural_label(self, tmp_path):
        """Multiple segments renders the plural 'segments' label."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        meta = _minimal_meta()
        meta["segment_count"] = 3

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta=meta,
                redline_present=True,
            )

        drawn_texts = [c.args[1] for c in mock_draw.text.call_args_list]
        assert any("segments" in t for t in drawn_texts), "plural 'segments' not rendered"

    def test_segment_count_singular_label(self, tmp_path):
        """A single segment must NOT render the segment count label."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        meta = _minimal_meta()
        meta["segment_count"] = 1

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta=meta,
                redline_present=True,
            )

        drawn_texts = [c.args[1] for c in mock_draw.text.call_args_list]
        assert not any("segment" in t for t in drawn_texts), "segment label must be absent for count=1"

    def test_rmd_fields_iso_wb_focal_rendered(self, tmp_path):
        """ISO, WB and focal length from _rmd_raw are rendered when they fit."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta=_minimal_meta(),
                redline_present=True,
            )

        drawn_texts = [c.args[1] for c in mock_draw.text.call_args_list]
        assert any("ISO" in t for t in drawn_texts), "ISO not rendered"
        assert any("WB" in t for t in drawn_texts), "WB not rendered"
        assert any("Focal" in t for t in drawn_texts), "Focal length not rendered"

    def test_divider_line_drawn_at_thumb_area_boundary(self, tmp_path):
        """A vertical divider line is drawn at the thumbnail/metadata boundary."""
        from core.thumbnail import make_r3d_tile, TILE_WIDTH, THUMB_AREA_FRAC

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta=_minimal_meta(),
                redline_present=True,
            )

        expected_x = int(TILE_WIDTH * THUMB_AREA_FRAC)
        line_calls = mock_draw.line.call_args_list
        divider_xs = []
        for c in line_calls:
            coords = c.args[0]
            if isinstance(coords, list) and len(coords) == 2:
                x_val = coords[0][0]
                divider_xs.append(x_val)

        assert expected_x in divider_xs, (
            f"expected divider at x={expected_x}, got line x-coords: {divider_xs}"
        )

    def test_custom_width_height_passed_to_image_new(self, tmp_path):
        """Custom width/height override defaults and are forwarded to Image.new."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img) as mock_new,
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta={},
                redline_present=False,
                width=900,
                height=110,
            )

        mock_new.assert_called_once()
        call_args = mock_new.call_args
        assert call_args.args[1] == (900, 110) or call_args.args[1] == (900, 110)

    def test_no_camera_serial_omits_sn_prefix(self, tmp_path):
        """When camera_serial is absent, the 'SN:' prefix must not appear."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        meta = {
            "camera_model": "DSMC2 GEMINI 5K S35",
            "_rmd_raw": {},
        }

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            make_r3d_tile(
                rdc_path=rdc,
                frame_paths=[],
                meta=meta,
                redline_present=True,
            )

        drawn_texts = [c.args[1] for c in mock_draw.text.call_args_list]
        assert not any("SN:" in t for t in drawn_texts), "SN: should be absent when serial is empty"

    def test_multiple_broken_frames_all_draw_placeholder_rects(self, tmp_path):
        """Each broken frame slot draws its own placeholder rectangle."""
        from core.thumbnail import make_r3d_tile

        rdc = tmp_path / "A001_C001.RDC"
        rdc.mkdir()

        frames = [tmp_path / f"f{i}.jpg" for i in range(3)]
        for f in frames:
            f.write_bytes(b"bad")

        mock_img, mock_draw = _make_draw_image_mocks()
        font_bold, font_md, font_sm = _make_fonts()

        with (
            patch("core.thumbnail._load_fonts", return_value=(font_bold, font_md, font_sm)),
            patch("PIL.Image.new", return_value=mock_img),
            patch("PIL.Image.open", side_effect=OSError("corrupt")),
            patch("PIL.ImageDraw.Draw", return_value=mock_draw),
        ):
            result = make_r3d_tile(
                rdc_path=rdc,
                frame_paths=frames,
                meta=_minimal_meta(),
                redline_present=True,
            )

        assert result is mock_img
        mock_img.paste.assert_not_called()
        # One rectangle per broken frame slot (3 broken frames)
        rect_calls = [
            c for c in mock_draw.rectangle.call_args_list
            if c.kwargs.get("outline") is not None or (len(c.args) > 1 and "outline" in str(c))
        ]
        # At minimum 3 rectangle calls (one per slot) or more (placeholder box)
        assert mock_draw.rectangle.call_count >= 3
