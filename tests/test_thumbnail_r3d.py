"""Tests for core/thumbnail.py — r3d_pipeline."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from core.thumbnail import (
    extract_frames_r3d,
    find_rdc_clips,
    r3d_clip_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rdc(tmp_path: Path, name: str = "A001_C001.RDC") -> Path:
    """Create a fake .RDC directory with one .R3D segment."""
    rdc = tmp_path / name
    rdc.mkdir(parents=True)
    (rdc / f"{name[:-4]}_001.R3D").touch()
    return rdc


# ---------------------------------------------------------------------------
# TestFindRdcClips
# ---------------------------------------------------------------------------

class TestFindRdcClips:
    def test_finds_rdc_dirs_at_top_level(self, tmp_path):
        rdc = _make_rdc(tmp_path, "A001_C001.RDC")
        result = find_rdc_clips(tmp_path)
        assert result == [rdc]

    def test_finds_rdc_dirs_nested(self, tmp_path):
        sub = tmp_path / "card1" / "footage"
        sub.mkdir(parents=True)
        rdc_a = sub / "A001_C001.RDC"
        rdc_b = sub / "A001_C002.RDC"
        rdc_a.mkdir()
        rdc_b.mkdir()
        result = find_rdc_clips(tmp_path)
        assert result == sorted([rdc_a, rdc_b])

    def test_ignores_rdc_files_that_are_not_dirs(self, tmp_path):
        # A file named *.RDC should not be returned
        fake_file = tmp_path / "not_a_dir.RDC"
        fake_file.touch()
        result = find_rdc_clips(tmp_path)
        assert result == []

    def test_empty_directory_returns_empty_list(self, tmp_path):
        result = find_rdc_clips(tmp_path)
        assert result == []

    def test_result_is_sorted(self, tmp_path):
        names = ["C003.RDC", "A001.RDC", "B002.RDC"]
        for n in names:
            (tmp_path / n).mkdir()
        result = find_rdc_clips(tmp_path)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# TestR3dClipMetadata
# ---------------------------------------------------------------------------

class TestR3dClipMetadata:
    def _rmd_meta(self):
        return {
            "fps": "23.976",
            "resolution": "4096x2160",
            "camera_model": "DSMC2 MONSTRO 8K VV",
            "timecode_start": "01:00:00:00",
            "redcode_ratio": "5:1",
            "frame_count": "2400",
        }

    def test_happy_path_with_rmd(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        rmd = rdc / "A001_C001.RMD"
        rmd.touch()

        with patch("core.thumbnail.parse_rmd_sidecar", return_value=self._rmd_meta()) as mock_parse:
            meta = r3d_clip_metadata(rdc)

        mock_parse.assert_called_once_with(rmd)
        assert meta["codec"] == "R3D"
        assert meta["frame_rate"] == "23.976"
        assert meta["resolution"] == "4096x2160"
        assert meta["camera_model"] == "DSMC2 MONSTRO 8K VV"
        assert meta["timecode_start"] == "01:00:00:00"
        assert meta["profile"] == "5:1"
        assert meta["bit_depth"] is None
        assert meta["segment_count"] == 1
        assert meta["clip_path"] == rdc
        assert "_rmd_raw" in meta

    def test_duration_calculated_from_fps_and_frame_count(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        (rdc / "A001_C001.RMD").touch()

        with patch("core.thumbnail.parse_rmd_sidecar", return_value=self._rmd_meta()):
            meta = r3d_clip_metadata(rdc)

        expected = 2400 / 23.976
        assert abs(meta["duration"] - expected) < 0.001

    def test_no_rmd_returns_segment_count_and_clip_path_only(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        # No .RMD files present
        meta = r3d_clip_metadata(rdc)
        assert meta["segment_count"] == 1
        assert meta["clip_path"] == rdc
        # Keys set by RMD block should be absent
        assert "codec" not in meta
        assert "frame_rate" not in meta

    def test_invalid_fps_skips_duration(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        (rdc / "A001_C001.RMD").touch()
        bad = self._rmd_meta()
        bad["fps"] = "not-a-number"

        with patch("core.thumbnail.parse_rmd_sidecar", return_value=bad):
            meta = r3d_clip_metadata(rdc)

        assert "duration" not in meta

    def test_zero_fps_skips_duration(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        (rdc / "A001_C001.RMD").touch()
        bad = self._rmd_meta()
        bad["fps"] = "0"

        with patch("core.thumbnail.parse_rmd_sidecar", return_value=bad):
            meta = r3d_clip_metadata(rdc)

        assert "duration" not in meta

    def test_empty_fps_and_frame_count_skips_duration(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        (rdc / "A001_C001.RMD").touch()
        sparse = {"fps": "", "frame_count": ""}

        with patch("core.thumbnail.parse_rmd_sidecar", return_value=sparse):
            meta = r3d_clip_metadata(rdc)

        assert "duration" not in meta

    def test_multiple_r3d_segments_counted(self, tmp_path):
        rdc = tmp_path / "B001_C001.RDC"
        rdc.mkdir()
        for i in range(3):
            (rdc / f"B001_C001_{i:03d}.R3D").touch()

        meta = r3d_clip_metadata(rdc)
        assert meta["segment_count"] == 3


# ---------------------------------------------------------------------------
# TestExtractFramesR3d
# ---------------------------------------------------------------------------

class TestExtractFramesR3d:
    def _fake_run_success(self, out_dir: Path, out_file: Path):
        """Return a side_effect function that creates out_file on subprocess.run."""
        def _run(cmd, **kwargs):
            out_file.touch()
            return MagicMock(returncode=0)
        return _run

    def test_happy_path_single_frame(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")
        expected_frame = out_dir / f"{rdc.stem}_f1.jpg"

        def _run(cmd, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            expected_frame.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run):
            result = extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=1,
                redline_path=redline,
                fps=24.0,
            )

        assert len(result) == 1
        assert result[0] == expected_frame

    def test_multiple_frames_extracted(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")

        call_idx = [0]

        def _run(cmd, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            frame_num = call_idx[0] + 1
            out_file = out_dir / f"{rdc.stem}_f{frame_num}.jpg"
            out_file.touch()
            call_idx[0] += 1
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run):
            result = extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=100.0,
                frame_count=4,
                redline_path=redline,
                fps=24.0,
            )

        assert len(result) == 4

    def test_no_r3d_files_returns_empty_list(self, tmp_path):
        rdc = tmp_path / "Empty.RDC"
        rdc.mkdir()
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")

        with patch("subprocess.run") as mock_run:
            result = extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=2,
                redline_path=redline,
                fps=24.0,
            )

        mock_run.assert_not_called()
        assert result == []

    def test_subprocess_failure_calls_log_cb(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "REDline")):
            log_messages = []
            def _log(msg, level):
                log_messages.append((msg, level))

            result = extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=1,
                redline_path=redline,
                fps=24.0,
                log_cb=_log,
            )

        assert result == []
        assert len(log_messages) == 1
        assert "warning" in log_messages[0][1]

    def test_subprocess_failure_without_log_cb_does_not_raise(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("REDline", 120)):
            result = extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=1,
                redline_path=redline,
                fps=24.0,
                log_cb=None,
            )

        assert result == []

    def test_frame_num_uses_fps_when_provided(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")
        captured_cmds = []

        def _run(cmd, **kwargs):
            captured_cmds.append(cmd)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{rdc.stem}_f1.jpg").touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run):
            extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=1,
                redline_path=redline,
                fps=24.0,
            )

        # FRAME_POSITIONS[0] = 0.15; t_secs = 10.0 * 0.15 = 1.5; frame_num = int(1.5 * 24) = 36
        assert "--frameNum" in captured_cmds[0]
        idx = captured_cmds[0].index("--frameNum")
        assert captured_cmds[0][idx + 1] == "36"

    def test_frame_num_fallback_when_fps_none(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")
        captured_cmds = []

        def _run(cmd, **kwargs):
            captured_cmds.append(cmd)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{rdc.stem}_f1.jpg").touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run):
            extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=1,
                redline_path=redline,
                fps=None,
            )

        # Without fps, fallback: frame_num = int(pos * 1000) = int(0.15 * 1000) = 150
        idx = captured_cmds[0].index("--frameNum")
        assert captured_cmds[0][idx + 1] == "150"

    def test_redline_output_renamed_when_name_differs(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "frames"
        redline = Path("/fake/REDline")
        expected_frame = out_dir / f"{rdc.stem}_f1.jpg"

        def _run(cmd, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            # REDline produces a differently named file
            (out_dir / "REDline_output_0001.jpg").touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run):
            result = extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=1,
                redline_path=redline,
                fps=24.0,
            )

        assert len(result) == 1
        assert result[0] == expected_frame
        assert expected_frame.exists()

    def test_out_dir_created_if_not_exists(self, tmp_path):
        rdc = _make_rdc(tmp_path)
        out_dir = tmp_path / "deep" / "nested" / "frames"
        redline = Path("/fake/REDline")

        def _run(cmd, **kwargs):
            (out_dir / f"{rdc.stem}_f1.jpg").touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run):
            extract_frames_r3d(
                rdc_path=rdc,
                out_dir=out_dir,
                duration=10.0,
                frame_count=1,
                redline_path=redline,
                fps=24.0,
            )

        assert out_dir.exists()
