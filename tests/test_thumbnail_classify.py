"""Tests for core/thumbnail.py — classify."""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.thumbnail import (
    check_redline,
    classify_files,
    redline_available,
    _REDLINE_BUNDLE_PATH,
)


class TestClassifyFiles:
    def test_happy_path_mixed_extensions(self, tmp_path):
        paths = [
            tmp_path / "clip.mov",
            tmp_path / "audio.wav",
            tmp_path / "raw.braw",
            tmp_path / "doc.pdf",
        ]
        result = classify_files(paths)
        assert result["video"] == [tmp_path / "clip.mov"]
        assert result["audio"] == [tmp_path / "audio.wav"]
        assert result["braw"] == [tmp_path / "raw.braw"]
        assert result["other"] == [tmp_path / "doc.pdf"]

    def test_empty_list_returns_empty_buckets(self):
        result = classify_files([])
        assert result == {"video": [], "audio": [], "braw": [], "other": []}

    def test_extension_matching_is_case_insensitive(self, tmp_path):
        paths = [
            tmp_path / "CLIP.MOV",
            tmp_path / "audio.WAV",
            tmp_path / "raw.BRAW",
        ]
        result = classify_files(paths)
        assert len(result["video"]) == 1
        assert len(result["audio"]) == 1
        assert len(result["braw"]) == 1
        assert result["other"] == []

    def test_all_video_extensions_classified(self, tmp_path):
        video_exts = [".mxf", ".mov", ".mp4", ".m4v", ".mts", ".m2ts",
                      ".ari", ".crm", ".movi", ".avi", ".mkv", ".r3d"]
        paths = [tmp_path / f"file{ext}" for ext in video_exts]
        result = classify_files(paths)
        assert len(result["video"]) == len(video_exts)
        assert result["audio"] == []
        assert result["braw"] == []
        assert result["other"] == []

    def test_all_audio_extensions_classified(self, tmp_path):
        audio_exts = [".wav", ".bwf", ".aif", ".aiff", ".flac",
                      ".mp3", ".m4a", ".ogg", ".opus"]
        paths = [tmp_path / f"file{ext}" for ext in audio_exts]
        result = classify_files(paths)
        assert len(result["audio"]) == len(audio_exts)
        assert result["video"] == []

    def test_unknown_extensions_go_to_other(self, tmp_path):
        paths = [
            tmp_path / "report.pdf",
            tmp_path / "data.csv",
            tmp_path / "archive.zip",
            tmp_path / "noext",
        ]
        result = classify_files(paths)
        assert len(result["other"]) == 4
        assert result["video"] == []
        assert result["audio"] == []
        assert result["braw"] == []

    def test_braw_not_classified_as_video(self, tmp_path):
        # .braw must land in braw bucket, not video
        path = tmp_path / "footage.braw"
        result = classify_files([path])
        assert result["braw"] == [path]
        assert result["video"] == []

    def test_order_of_input_paths_preserved_within_buckets(self, tmp_path):
        paths = [
            tmp_path / "b.mov",
            tmp_path / "a.mov",
            tmp_path / "c.mov",
        ]
        result = classify_files(paths)
        assert result["video"] == paths


class TestCheckRedline:
    def test_happy_path_bundle_exists(self):
        with patch.object(Path, "exists", return_value=True):
            result = check_redline()
        assert result == _REDLINE_BUNDLE_PATH

    def test_falls_back_to_which_when_bundle_absent(self):
        with patch.object(Path, "exists", return_value=False):
            with patch("shutil.which", return_value="/usr/local/bin/REDline") as mock_which:
                result = check_redline()
        mock_which.assert_called_once_with("REDline")
        assert result == Path("/usr/local/bin/REDline")

    def test_returns_none_when_both_bundle_and_which_absent(self):
        with patch.object(Path, "exists", return_value=False):
            with patch("shutil.which", return_value=None):
                result = check_redline()
        assert result is None

    def test_bundle_path_takes_priority_over_which(self):
        # Even when which would return a result, the bundle path should win
        with patch.object(Path, "exists", return_value=True):
            with patch("shutil.which", return_value="/usr/local/bin/REDline") as mock_which:
                result = check_redline()
        mock_which.assert_not_called()
        assert result == _REDLINE_BUNDLE_PATH

    def test_returns_path_object_from_which(self):
        with patch.object(Path, "exists", return_value=False):
            with patch("shutil.which", return_value="/opt/redline/REDline"):
                result = check_redline()
        assert isinstance(result, Path)
        assert str(result) == "/opt/redline/REDline"


class TestRedlineAvailable:
    def test_true_when_check_redline_returns_path(self):
        fake_path = Path("/Applications/REDCINE-X PRO.app/Contents/MacOS/REDline")
        with patch("core.thumbnail.check_redline", return_value=fake_path):
            assert redline_available() is True

    def test_false_when_check_redline_returns_none(self):
        with patch("core.thumbnail.check_redline", return_value=None):
            assert redline_available() is False

    def test_delegates_entirely_to_check_redline(self):
        # Verify the function is a thin wrapper — it should call check_redline once
        with patch("core.thumbnail.check_redline", return_value=None) as mock_check:
            redline_available()
        mock_check.assert_called_once_with()
