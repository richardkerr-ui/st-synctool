"""Tests for core/thumbnail.py — frames."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.thumbnail import FRAME_POSITIONS, adaptive_frame_count, extract_frames


class TestAdaptiveFrameCount:
    # --- happy paths ---

    def test_long_clip_returns_four(self):
        # over 2 min -> full 4 frames
        assert adaptive_frame_count(180) == 4

    def test_medium_clip_returns_three(self):
        # 30 s-2 min -> 3 frames
        assert adaptive_frame_count(60) == 3

    def test_short_clip_returns_two(self):
        # 5-30 s -> 2 frames
        assert adaptive_frame_count(15) == 2

    def test_very_short_clip_returns_one(self):
        # under 5 s -> 1 frame
        assert adaptive_frame_count(3) == 1

    # --- boundary values ---

    def test_exactly_5_seconds_returns_two(self):
        assert adaptive_frame_count(5) == 2

    def test_exactly_30_seconds_returns_three(self):
        assert adaptive_frame_count(30) == 3

    def test_exactly_120_seconds_returns_four(self):
        assert adaptive_frame_count(120) == 4

    def test_just_under_5_seconds_returns_one(self):
        assert adaptive_frame_count(4.99) == 1

    def test_just_under_30_seconds_returns_two(self):
        assert adaptive_frame_count(29.99) == 2

    # --- None / missing duration ---

    def test_none_duration_returns_one(self):
        assert adaptive_frame_count(None) == 1

    def test_zero_duration_returns_one(self):
        assert adaptive_frame_count(0) == 1

    # --- user_max cap ---

    def test_user_max_caps_result(self):
        # 3 min clip would normally yield 4 but user_max=2 caps it
        assert adaptive_frame_count(300, user_max=2) == 2

    def test_user_max_one_always_returns_one(self):
        assert adaptive_frame_count(300, user_max=1) == 1

    def test_user_max_above_four_treated_as_four(self):
        # user_max is capped at 4 internally
        assert adaptive_frame_count(300, user_max=10) == 4

    def test_user_max_zero_clamped_to_one(self):
        # max(1, min(0, 4)) = 1
        assert adaptive_frame_count(300, user_max=0) == 1

    def test_user_max_negative_clamped_to_one(self):
        assert adaptive_frame_count(300, user_max=-5) == 1

    def test_user_max_caps_short_clip(self):
        # 15 s clip -> 2 but cap at 1
        assert adaptive_frame_count(15, user_max=1) == 1


class TestExtractFrames:
    # --- happy path ---

    def test_extracts_requested_frames(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"

        def _fake_run(cmd, **kwargs):
            # Simulate ffmpeg creating the output file
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            result = extract_frames(clip, out_dir, duration=60.0, frame_count=2)

        assert len(result) == 2
        assert all(p.exists() for p in result)

    def test_output_filenames_use_stem_and_index(self, tmp_path):
        clip = tmp_path / "myvideo.mp4"
        clip.touch()
        out_dir = tmp_path / "frames"

        def _fake_run(cmd, **kwargs):
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            result = extract_frames(clip, out_dir, duration=30.0, frame_count=2)

        names = [p.name for p in result]
        assert "myvideo_f1.jpg" in names
        assert "myvideo_f2.jpg" in names

    def test_creates_output_directory(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "nested" / "frames"

        def _fake_run(cmd, **kwargs):
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            extract_frames(clip, out_dir, duration=60.0, frame_count=1)

        assert out_dir.exists()

    def test_timestamps_derived_from_frame_positions(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"
        duration = 100.0
        frame_count = 2
        captured_cmds = []

        def _fake_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            extract_frames(clip, out_dir, duration=duration, frame_count=frame_count)

        expected_ts = [f"{duration * p:.3f}" for p in FRAME_POSITIONS[:frame_count]]
        actual_ts = [cmd[cmd.index("-ss") + 1] for cmd in captured_cmds]
        assert actual_ts == expected_ts

    # --- failure path ---

    def test_failed_frame_omitted_from_result(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"
        call_count = [0]

        def _fake_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise subprocess.CalledProcessError(1, cmd)
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            result = extract_frames(clip, out_dir, duration=60.0, frame_count=2)

        # Frame 1 failed, frame 2 succeeded
        assert len(result) == 1
        assert result[0].name.endswith("_f2.jpg")

    def test_all_frames_fail_returns_empty_list(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            result = extract_frames(clip, out_dir, duration=60.0, frame_count=3)

        assert result == []

    def test_ffmpeg_timeout_omits_frame(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 60)):
            result = extract_frames(clip, out_dir, duration=60.0, frame_count=2)

        assert result == []

    def test_log_cb_called_on_failure(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"
        log_messages = []

        def _log(msg, level):
            log_messages.append((msg, level))

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            extract_frames(clip, out_dir, duration=60.0, frame_count=1, log_cb=_log)

        assert len(log_messages) == 1
        msg, level = log_messages[0]
        assert "Frame 1" in msg
        assert level == "warning"

    def test_no_log_cb_does_not_raise_on_failure(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            # Must not raise even without a log callback
            result = extract_frames(clip, out_dir, duration=60.0, frame_count=2)

        assert result == []

    # --- edge cases ---

    def test_frame_count_zero_returns_empty_list(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"

        with patch("subprocess.run") as mock_run:
            result = extract_frames(clip, out_dir, duration=60.0, frame_count=0)

        mock_run.assert_not_called()
        assert result == []

    def test_ffmpeg_writes_no_file_omits_frame(self, tmp_path):
        # subprocess succeeds but output file is never written
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"

        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = extract_frames(clip, out_dir, duration=60.0, frame_count=2)

        assert result == []

    def test_four_frames_uses_all_positions(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"
        captured_cmds = []

        def _fake_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run):
            result = extract_frames(clip, out_dir, duration=200.0, frame_count=4)

        assert len(result) == 4
        assert len(captured_cmds) == 4

    def test_subprocess_run_receives_correct_flags(self, tmp_path):
        clip = tmp_path / "clip.mov"
        clip.touch()
        out_dir = tmp_path / "frames"

        def _fake_run(cmd, **kwargs):
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.touch()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_fake_run) as mock_run:
            extract_frames(clip, out_dir, duration=60.0, frame_count=1)

        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "-ss" in cmd
        assert "-vframes" in cmd
        assert kwargs.get("timeout") == 60
        assert kwargs.get("check") is True
