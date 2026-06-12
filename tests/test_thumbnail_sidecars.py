"""Tests for core/thumbnail.py — sidecars."""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.thumbnail import (
    _extract_date,
    _first_tag,
    _safe_fraction,
    parse_braw_sidecar,
    parse_rmd_sidecar,
)


# ---------------------------------------------------------------------------
# TestFirstTag
# ---------------------------------------------------------------------------

class TestFirstTag:
    def test_returns_first_matching_key(self):
        tags = {"make": "Canon", "model": "R5"}
        assert _first_tag(tags, ["make", "model"]) == "Canon"

    def test_skips_falsy_values_and_returns_next(self):
        tags = {"make": "", "model": "R5"}
        assert _first_tag(tags, ["make", "model"]) == "R5"

    def test_returns_none_when_no_key_matches(self):
        tags = {"codec": "h264"}
        assert _first_tag(tags, ["make", "model"]) is None

    def test_returns_none_for_empty_dict(self):
        assert _first_tag({}, ["make", "model"]) is None

    def test_coerces_non_string_value_to_str(self):
        tags = {"iso": 800}
        result = _first_tag(tags, ["iso"])
        assert result == "800"
        assert isinstance(result, str)

    def test_empty_key_list_returns_none(self):
        tags = {"make": "Sony"}
        assert _first_tag(tags, []) is None

    def test_zero_value_is_falsy_skipped(self):
        # 0 is falsy, so it should be skipped in favour of the next key
        tags = {"track": 0, "title": "MyClip"}
        assert _first_tag(tags, ["track", "title"]) == "MyClip"


# ---------------------------------------------------------------------------
# TestExtractDate
# ---------------------------------------------------------------------------

class TestExtractDate:
    def test_creation_time_iso_format(self):
        tags = {"creation_time": "2024-05-15T10:30:00Z"}
        assert _extract_date(tags) == "2024-05-15"

    def test_date_key_fallback(self):
        tags = {"date": "2023-11-01T08:00:00"}
        assert _extract_date(tags) == "2023-11-01"

    def test_creation_time_preferred_over_date(self):
        tags = {"creation_time": "2024-01-01T00:00:00", "date": "2020-06-06T00:00:00"}
        assert _extract_date(tags) == "2024-01-01"

    def test_returns_none_when_both_keys_absent(self):
        assert _extract_date({}) is None

    def test_returns_none_when_both_keys_empty_string(self):
        assert _extract_date({"creation_time": "", "date": ""}) is None

    def test_date_without_time_component(self):
        # No "T" in the string — split returns the whole string as the first element
        tags = {"creation_time": "2024-05-15"}
        assert _extract_date(tags) == "2024-05-15"

    def test_irrelevant_tags_only_returns_none(self):
        assert _extract_date({"codec": "h264", "bit_depth": "10"}) is None


# ---------------------------------------------------------------------------
# TestSafeFraction
# ---------------------------------------------------------------------------

class TestSafeFraction:
    def test_ntsc_fraction(self):
        assert _safe_fraction("24000/1001") == "23.976"

    def test_integer_fraction_strips_trailing_zeros(self):
        # 25/1 -> "25.000" -> strip -> "25"
        assert _safe_fraction("25/1") == "25"

    def test_plain_string_passthrough(self):
        assert _safe_fraction("29.97") == "29.97"

    def test_empty_string_returns_none(self):
        assert _safe_fraction("") is None

    def test_none_returns_none(self):
        assert _safe_fraction(None) is None

    def test_malformed_fraction_returns_original(self):
        # Division by zero or unparseable — should return the original string
        result = _safe_fraction("abc/xyz")
        assert result == "abc/xyz"

    def test_30_fps_ntsc(self):
        assert _safe_fraction("30000/1001") == "29.97"

    def test_whole_number_string_passthrough(self):
        assert _safe_fraction("24") == "24"

    def test_60_fps_fraction(self):
        assert _safe_fraction("60/1") == "60"


# ---------------------------------------------------------------------------
# TestParseBrawSidecar
# ---------------------------------------------------------------------------

class TestParseBrawSidecar:
    def _write_xml(self, tmp_path: Path, suffix: str, content: str) -> Path:
        clip = tmp_path / "A001_C001.braw"
        clip.touch()
        sidecar = tmp_path / f"A001_C001{suffix}"
        sidecar.write_text(content, encoding="utf-8")
        return clip

    def test_reads_dot_sidecar_file(self, tmp_path):
        xml_content = "<Meta><ISO>800</ISO><WhiteBalance>5600</WhiteBalance></Meta>"
        clip = self._write_xml(tmp_path, ".sidecar", xml_content)
        result = parse_braw_sidecar(clip)
        assert result["ISO"] == "800"
        assert result["WhiteBalance"] == "5600"

    def test_falls_back_to_dot_xml(self, tmp_path):
        xml_content = "<Meta><Framerate>24</Framerate></Meta>"
        clip = self._write_xml(tmp_path, ".xml", xml_content)
        result = parse_braw_sidecar(clip)
        assert result["Framerate"] == "24"

    def test_dot_sidecar_preferred_over_dot_xml(self, tmp_path):
        clip = tmp_path / "clip.braw"
        clip.touch()
        (tmp_path / "clip.sidecar").write_text(
            "<Meta><Source>sidecar</Source></Meta>", encoding="utf-8"
        )
        (tmp_path / "clip.xml").write_text(
            "<Meta><Source>xml</Source></Meta>", encoding="utf-8"
        )
        result = parse_braw_sidecar(clip)
        assert result["Source"] == "sidecar"

    def test_returns_empty_dict_when_no_sidecar(self, tmp_path):
        clip = tmp_path / "A001_C001.braw"
        clip.touch()
        assert parse_braw_sidecar(clip) == {}

    def test_strips_xml_namespace_from_tag(self, tmp_path):
        xml_content = (
            '<Meta xmlns:bmd="http://example.com/bmd">'
            "<bmd:ISO>3200</bmd:ISO>"
            "</Meta>"
        )
        clip = self._write_xml(tmp_path, ".sidecar", xml_content)
        result = parse_braw_sidecar(clip)
        assert "ISO" in result
        assert result["ISO"] == "3200"

    def test_malformed_xml_returns_empty_dict(self, tmp_path):
        clip = tmp_path / "bad.braw"
        clip.touch()
        (tmp_path / "bad.sidecar").write_text("<<NOT XML>>", encoding="utf-8")
        assert parse_braw_sidecar(clip) == {}

    def test_ignores_elements_with_whitespace_only_text(self, tmp_path):
        xml_content = "<Meta><ISO>800</ISO><Empty>   </Empty></Meta>"
        clip = self._write_xml(tmp_path, ".sidecar", xml_content)
        result = parse_braw_sidecar(clip)
        assert "Empty" not in result
        assert result["ISO"] == "800"

    def test_multiple_fields_all_extracted(self, tmp_path):
        xml_content = (
            "<BlackmagicDesign>"
            "<ISO>3200</ISO>"
            "<ShutterAngle>180.0</ShutterAngle>"
            "<WhiteBalance>4500</WhiteBalance>"
            "<FrameRate>25</FrameRate>"
            "</BlackmagicDesign>"
        )
        clip = self._write_xml(tmp_path, ".sidecar", xml_content)
        result = parse_braw_sidecar(clip)
        assert len(result) >= 4
        assert result["FrameRate"] == "25"


# ---------------------------------------------------------------------------
# TestParseRmdSidecar
# ---------------------------------------------------------------------------

class TestParseRmdSidecar:
    def _make_rmd(self, tmp_path: Path, content: str, name: str = "clip.RMD") -> Path:
        rmd = tmp_path / name
        rmd.write_text(content, encoding="utf-8")
        return rmd

    def _basic_rmd(self, **fields) -> str:
        inner = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
        return f"<REDMetadata>{inner}</REDMetadata>"

    def test_extracts_fps_from_FrameRate(self, tmp_path):
        rmd = self._make_rmd(tmp_path, self._basic_rmd(FrameRate="23.976"))
        result = parse_rmd_sidecar(rmd)
        assert result["fps"] == "23.976"

    def test_extracts_iso(self, tmp_path):
        rmd = self._make_rmd(tmp_path, self._basic_rmd(ISO="800"))
        result = parse_rmd_sidecar(rmd)
        assert result["iso"] == "800"

    def test_extracts_camera_model_primary_key(self, tmp_path):
        rmd = self._make_rmd(tmp_path, self._basic_rmd(CameraModel="DSMC2 MONSTRO 8K VV"))
        result = parse_rmd_sidecar(rmd)
        assert result["camera_model"] == "DSMC2 MONSTRO 8K VV"

    def test_camera_model_fallback_to_Camera(self, tmp_path):
        rmd = self._make_rmd(tmp_path, self._basic_rmd(Camera="EPIC-W"))
        result = parse_rmd_sidecar(rmd)
        assert result["camera_model"] == "EPIC-W"

    def test_fps_fallback_to_VideoFrameRate(self, tmp_path):
        # Neither FrameRate nor FPS present — falls through to VideoFrameRate
        rmd = self._make_rmd(tmp_path, self._basic_rmd(VideoFrameRate="29.97"))
        result = parse_rmd_sidecar(rmd)
        assert result["fps"] == "29.97"

    def test_strips_namespace_from_tags(self, tmp_path):
        content = (
            '<REDMetadata xmlns:red="http://red.com/schema">'
            "<red:FrameRate>24</red:FrameRate>"
            "</REDMetadata>"
        )
        rmd = self._make_rmd(tmp_path, content)
        result = parse_rmd_sidecar(rmd)
        assert result.get("fps") == "24"

    def test_returns_empty_dict_for_malformed_xml(self, tmp_path):
        rmd = self._make_rmd(tmp_path, "NOT XML AT ALL")
        assert parse_rmd_sidecar(rmd) == {}

    def test_returns_empty_dict_for_missing_file(self, tmp_path):
        missing = tmp_path / "ghost.RMD"
        assert parse_rmd_sidecar(missing) == {}

    def test_absent_keys_not_in_result(self, tmp_path):
        # Only FrameRate provided; all other mapped keys should be absent
        rmd = self._make_rmd(tmp_path, self._basic_rmd(FrameRate="24"))
        result = parse_rmd_sidecar(rmd)
        for absent_key in ("iso", "white_balance", "aperture", "focal_length"):
            assert absent_key not in result

    def test_whitespace_only_text_nodes_ignored(self, tmp_path):
        content = "<REDMetadata><FrameRate>24</FrameRate><ISO>   </ISO></REDMetadata>"
        rmd = self._make_rmd(tmp_path, content)
        result = parse_rmd_sidecar(rmd)
        assert result.get("fps") == "24"
        assert "iso" not in result

    def test_full_metadata_round_trip(self, tmp_path):
        content = self._basic_rmd(
            FrameRate="23.976",
            Resolution="8192x4320",
            ISO="1600",
            WhiteBalance="5500",
            Aperture="T2.8",
            FocalLength="35mm",
            TimecodeStart="01:00:00:00",
            CameraModel="DSMC2 HELIUM 8K S35",
            CameraSerial="123456",
            REDCODERatio="8:1",
            ColorScience="IPP2",
        )
        rmd = self._make_rmd(tmp_path, content)
        result = parse_rmd_sidecar(rmd)
        assert result["fps"] == "23.976"
        assert result["resolution"] == "8192x4320"
        assert result["iso"] == "1600"
        assert result["white_balance"] == "5500"
        assert result["aperture"] == "T2.8"
        assert result["focal_length"] == "35mm"
        assert result["timecode_start"] == "01:00:00:00"
        assert result["camera_model"] == "DSMC2 HELIUM 8K S35"
        assert result["camera_serial"] == "123456"
        assert result["redcode_ratio"] == "8:1"
        assert result["color_science"] == "IPP2"

    def test_first_candidate_key_wins(self, tmp_path):
        # FrameCount is the primary candidate for frame_count; TotalFrameCount is fallback
        rmd = self._make_rmd(
            tmp_path, self._basic_rmd(FrameCount="2400", TotalFrameCount="9999")
        )
        result = parse_rmd_sidecar(rmd)
        assert result["frame_count"] == "2400"
