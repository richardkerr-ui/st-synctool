"""Tests for core/thumbnail.py — probe_clip."""

import json
import subprocess
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest

from core.thumbnail import probe_clip


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ffprobe_output(
    format_data: Optional[dict] = None,
    streams: Optional[List[dict]] = None,
) -> bytes:
    """Return UTF-8 encoded JSON mimicking ffprobe -print_format json output."""
    payload = {
        "format": format_data if format_data is not None else {},
        "streams": streams if streams is not None else [],
    }
    return json.dumps(payload).encode()


_VIDEO_STREAM = {
    "codec_type": "video",
    "codec_long_name": "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10",
    "codec_name": "h264",
    "profile": "High",
    "width": 1920,
    "height": 1080,
    "bits_per_raw_sample": "8",
    "r_frame_rate": "24000/1001",
    "tags": {},
}

_AUDIO_STREAM = {
    "codec_type": "audio",
    "codec_name": "aac",
    "sample_rate": "48000",
    "channels": 2,
    "bits_per_raw_sample": None,
    "bits_per_sample": None,
}


# ---------------------------------------------------------------------------
# TestProbeClip
# ---------------------------------------------------------------------------

class TestProbeClip:
    """Tests for probe_clip — happy paths, failure paths and edge cases."""

    # -- Happy path: full video + audio --

    def test_happy_path_video_and_audio(self, tmp_path):
        """Returns correctly normalised metadata for a clip with video and audio."""
        clip = tmp_path / "sample.mov"
        clip.touch()

        format_data = {
            "format_long_name": "QuickTime / MOV",
            "duration": "300.0",
            "tags": {
                "creation_time": "2024-03-15T10:00:00Z",
                "make": "ARRI",
                "model": "ALEXA Mini",
            },
        }
        raw = _ffprobe_output(format_data=format_data, streams=[_VIDEO_STREAM, _AUDIO_STREAM])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["format_name"] == "QuickTime / MOV"
        assert result["duration"] == pytest.approx(300.0)
        assert result["date_recorded"] == "2024-03-15"
        assert result["camera_make"] == "ARRI"
        assert result["camera_model"] == "ALEXA Mini"
        assert result["codec"] == "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10"
        assert result["profile"] == "High"
        assert result["resolution"] == "1920×1080"
        assert result["bit_depth"] == "8"
        assert result["frame_rate"] == "23.976"
        assert result["sample_rate"] == "48000"
        assert result["channels"] == "2"
        assert result["audio_codec"] == "aac"

    # -- Happy path: video-only, no audio stream --

    def test_video_only_no_audio_keys_absent(self, tmp_path):
        """Audio keys are absent when there is no audio stream."""
        clip = tmp_path / "video_only.mp4"
        clip.touch()

        raw = _ffprobe_output(streams=[_VIDEO_STREAM])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert "codec" in result
        assert "sample_rate" not in result
        assert "channels" not in result
        assert "audio_codec" not in result

    # -- Happy path: audio-only file --

    def test_audio_only_file(self, tmp_path):
        """Audio-only clip populates audio keys and leaves video keys absent."""
        clip = tmp_path / "sync.wav"
        clip.touch()

        audio_stream = {
            "codec_type": "audio",
            "codec_name": "pcm_s24le",
            "sample_rate": "96000",
            "channels": 4,
            "bits_per_raw_sample": "24",
        }
        format_data = {"format_long_name": "WAV / WAVE (Waveform Audio)"}
        raw = _ffprobe_output(format_data=format_data, streams=[audio_stream])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["sample_rate"] == "96000"
        assert result["channels"] == "4"
        assert result["audio_codec"] == "pcm_s24le"
        assert result["bit_depth"] == "24"
        assert "codec" not in result

    # -- Failure path: ffprobe subprocess raises CalledProcessError --

    def test_subprocess_error_returns_empty_dict(self, tmp_path):
        """Returns {} when ffprobe exits with a non-zero code."""
        clip = tmp_path / "bad.mov"
        clip.touch()

        with patch(
            "subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "ffprobe"),
        ):
            result = probe_clip(clip)

        assert result == {}

    # -- Failure path: ffprobe times out --

    def test_subprocess_timeout_returns_empty_dict(self, tmp_path):
        """Returns {} when ffprobe times out."""
        clip = tmp_path / "huge.r3d"
        clip.touch()

        with patch(
            "subprocess.check_output",
            side_effect=subprocess.TimeoutExpired("ffprobe", 30),
        ):
            result = probe_clip(clip)

        assert result == {}

    # -- Failure path: ffprobe returns invalid JSON --

    def test_invalid_json_returns_empty_dict(self, tmp_path):
        """Returns {} when ffprobe output cannot be parsed as JSON."""
        clip = tmp_path / "corrupt.mxf"
        clip.touch()

        with patch("subprocess.check_output", return_value=b"not json at all"):
            result = probe_clip(clip)

        assert result == {}

    # -- Edge case: duration missing (no 'duration' key in format) --

    def test_missing_duration_is_none(self, tmp_path):
        """duration is None when the format block omits the duration key."""
        clip = tmp_path / "nodur.mov"
        clip.touch()

        raw = _ffprobe_output(format_data={}, streams=[_VIDEO_STREAM])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["duration"] is None

    # -- Edge case: only first video stream is used --

    def test_only_first_video_stream_used(self, tmp_path):
        """When two video streams are present, only the first populates codec keys."""
        clip = tmp_path / "dual.mov"
        clip.touch()

        second_video = dict(
            _VIDEO_STREAM,
            codec_name="hevc",
            codec_long_name="HEVC",
            width=3840,
            height=2160,
        )
        raw = _ffprobe_output(streams=[_VIDEO_STREAM, second_video])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["codec"] == "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10"
        assert result["resolution"] == "1920×1080"

    # -- Edge case: frame_rate expressed as integer fraction '25/1' → '25' --

    def test_integer_frame_rate_strips_decimal(self, tmp_path):
        """'25/1' frame rate is simplified to '25' (no trailing decimal)."""
        clip = tmp_path / "pal.mov"
        clip.touch()

        stream = dict(_VIDEO_STREAM, r_frame_rate="25/1")
        raw = _ffprobe_output(streams=[stream])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["frame_rate"] == "25"

    # -- Edge case: camera make/model resolved from stream tags when absent
    #    from format-level tags --

    def test_camera_make_model_from_stream_tags(self, tmp_path):
        """Camera make/model resolved from stream tags when missing from format tags."""
        clip = tmp_path / "clip.mov"
        clip.touch()

        stream = dict(_VIDEO_STREAM, tags={"make": "Sony", "model": "VENICE 2"})
        raw = _ffprobe_output(format_data={"tags": {}}, streams=[stream])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["camera_make"] == "Sony"
        assert result["camera_model"] == "VENICE 2"

    # -- Edge case: format-level camera tags take priority over stream tags --

    def test_format_camera_tags_not_overwritten_by_stream_tags(self, tmp_path):
        """Camera make/model from format tags are not replaced by stream-level tags."""
        clip = tmp_path / "priority.mov"
        clip.touch()

        format_data = {
            "tags": {
                "com.apple.quicktime.make": "ARRI",
                "com.apple.quicktime.model": "ALEXA Mini LF",
            }
        }
        stream = dict(_VIDEO_STREAM, tags={"make": "ShouldBeIgnored", "model": "AlsoIgnored"})
        raw = _ffprobe_output(format_data=format_data, streams=[stream])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["camera_make"] == "ARRI"
        assert result["camera_model"] == "ALEXA Mini LF"

    # -- Edge case: timecode populated from stream tags --

    def test_timecode_from_stream_tags(self, tmp_path):
        """timecode_start is extracted from the video stream's tags."""
        clip = tmp_path / "tc.mov"
        clip.touch()

        stream = dict(_VIDEO_STREAM, tags={"timecode": "01:00:00:00"})
        raw = _ffprobe_output(streams=[stream])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["timecode_start"] == "01:00:00:00"

    # -- Edge case: date_recorded falls through to stream tags --

    def test_date_recorded_falls_through_to_stream_tags(self, tmp_path):
        """date_recorded is resolved from video stream tags as a fallback."""
        clip = tmp_path / "clip2.mov"
        clip.touch()

        stream = dict(_VIDEO_STREAM, tags={"creation_time": "2023-07-04T08:30:00.000000Z"})
        raw = _ffprobe_output(format_data={"tags": {}}, streams=[stream])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["date_recorded"] == "2023-07-04"

    # -- Edge case: bit_depth falls back to audio stream when video bit depth absent --

    def test_bit_depth_fallback_to_audio_stream(self, tmp_path):
        """bit_depth falls back to the audio stream when the video stream has none."""
        clip = tmp_path / "audio_bd.wav"
        clip.touch()

        audio_stream = {
            "codec_type": "audio",
            "codec_name": "pcm_s16le",
            "sample_rate": "44100",
            "channels": 2,
            "bits_per_raw_sample": None,
            "bits_per_sample": 16,
        }
        raw = _ffprobe_output(streams=[audio_stream])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["bit_depth"] == "16"

    # -- Edge case: empty streams list leaves video/audio keys absent --

    def test_no_streams_returns_format_metadata_only(self, tmp_path):
        """An ffprobe result with no streams still returns format-level metadata."""
        clip = tmp_path / "empty_streams.mov"
        clip.touch()

        format_data = {
            "format_long_name": "QuickTime / MOV",
            "duration": "10.0",
            "tags": {"creation_time": "2022-01-01T00:00:00Z"},
        }
        raw = _ffprobe_output(format_data=format_data, streams=[])

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["format_name"] == "QuickTime / MOV"
        assert result["duration"] == pytest.approx(10.0)
        assert result["date_recorded"] == "2022-01-01"
        assert "codec" not in result
        assert "sample_rate" not in result

    # -- Edge case: format_long_name absent, falls back to format_name --

    def test_format_long_name_fallback(self, tmp_path):
        """format_name is used when format_long_name is absent."""
        clip = tmp_path / "fallback.mp4"
        clip.touch()

        raw = _ffprobe_output(format_data={"format_name": "mp4"})

        with patch("subprocess.check_output", return_value=raw):
            result = probe_clip(clip)

        assert result["format_name"] == "mp4"

    # -- Edge case: path passed to ffprobe as string --

    def test_path_passed_as_string_to_ffprobe(self, tmp_path):
        """probe_clip converts the Path argument to a string for the subprocess call."""
        clip = tmp_path / "check_cmd.mov"
        clip.touch()

        captured: dict = {}

        def _mock(cmd, **kwargs):
            captured["cmd"] = cmd
            return _ffprobe_output()

        with patch("subprocess.check_output", side_effect=_mock):
            probe_clip(clip)

        assert isinstance(captured["cmd"][-1], str)
        assert captured["cmd"][-1] == str(clip)
