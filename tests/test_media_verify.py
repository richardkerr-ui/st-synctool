"""
Tests for core/media_verify.py — format-aware post-copy verification.

All subprocess calls for REDline, ffprobe, and ART are mocked.
File I/O uses pytest's tmp_path fixture.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.media_verify import (
    MediaVerifyResult,
    verify_r3d_clip,
    verify_prores_mxf,
    verify_image_sequence,
    verify_arriraw,
    verify_file,
    IMAGE_SEQUENCE_EXTENSIONS,
    PRORES_MXF_EXTENSIONS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_completed(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a mock CompletedProcess with the given returncode and output."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr.encode()
    return m


def _make_r3d_rdc(tmp_path: Path, segments: list[str]) -> Path:
    """Create a fake .RDC folder with .R3D segment files."""
    rdc = tmp_path / "A001_C001_210601.RDC"
    rdc.mkdir()
    for name in segments:
        (rdc / name).write_bytes(b"\x00" * 16)
    return rdc


def _make_sequence(directory: Path, stem: str, frames: list[int], ext: str = ".dpx") -> None:
    """Write image sequence files into *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    for n in frames:
        (directory / f"{stem}.{n:04d}{ext}").write_bytes(b"\x00" * 4)


# ---------------------------------------------------------------------------
# verify_r3d_clip
# ---------------------------------------------------------------------------

class TestVerifyR3dClip:
    def test_redline_absent_returns_advisory(self, tmp_path):
        rdc = _make_r3d_rdc(tmp_path, ["A001_C001_0001.R3D"])
        with patch("core.media_verify._find_redline", return_value=None):
            result = verify_r3d_clip(rdc)
        assert result.advisory is True
        assert result.ok is True
        assert "REDline not installed" in result.detail

    def test_redline_present_exit0_returns_ok(self, tmp_path):
        rdc = _make_r3d_rdc(tmp_path, ["A001_C001_0001.R3D", "A001_C001_0002.R3D"])
        fake_redline = tmp_path / "REDline"
        fake_redline.write_bytes(b"")
        with (
            patch("core.media_verify._find_redline", return_value=fake_redline),
            patch("subprocess.run", return_value=_mock_completed(0)),
        ):
            result = verify_r3d_clip(rdc)
        assert result.ok is True
        assert result.advisory is False
        assert "verified 2 segment(s)" in result.detail

    def test_redline_present_exit1_returns_fail(self, tmp_path):
        rdc = _make_r3d_rdc(tmp_path, ["A001_C001_0001.R3D"])
        fake_redline = tmp_path / "REDline"
        fake_redline.write_bytes(b"")
        with (
            patch("core.media_verify._find_redline", return_value=fake_redline),
            patch("subprocess.run", return_value=_mock_completed(1)),
        ):
            result = verify_r3d_clip(rdc)
        assert result.ok is False
        assert result.advisory is False
        assert "REDline decode failed" in result.detail

    def test_no_segments_returns_advisory(self, tmp_path):
        rdc = tmp_path / "Empty.RDC"
        rdc.mkdir()
        fake_redline = tmp_path / "REDline"
        fake_redline.write_bytes(b"")
        with patch("core.media_verify._find_redline", return_value=fake_redline):
            result = verify_r3d_clip(rdc)
        assert result.advisory is True
        assert "No .R3D segments" in result.detail

    def test_redline_only_first_failing_segment_reported(self, tmp_path):
        """All failing segments should appear in the detail, not just one."""
        rdc = _make_r3d_rdc(tmp_path, ["seg_0001.R3D", "seg_0002.R3D"])
        fake_redline = tmp_path / "REDline"
        fake_redline.write_bytes(b"")
        with (
            patch("core.media_verify._find_redline", return_value=fake_redline),
            patch("subprocess.run", return_value=_mock_completed(1)),
        ):
            result = verify_r3d_clip(rdc)
        assert result.ok is False
        # Both segment names should appear in the detail
        assert "seg_0001.R3D" in result.detail
        assert "seg_0002.R3D" in result.detail


# ---------------------------------------------------------------------------
# verify_prores_mxf
# ---------------------------------------------------------------------------

class TestVerifyProresMxf:
    def test_ffprobe_absent_returns_advisory(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify._find_ffprobe", return_value=None):
            result = verify_prores_mxf(src, dst)
        assert result.advisory is True
        assert result.ok is True
        assert "ffprobe not installed" in result.detail

    def test_counts_match_returns_ok(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        completed = _mock_completed(0, stdout="120")
        with (
            patch("core.media_verify._find_ffprobe", return_value="ffprobe"),
            patch("subprocess.run", return_value=completed),
        ):
            result = verify_prores_mxf(src, dst)
        assert result.ok is True
        assert result.advisory is False
        assert "120" in result.detail

    def test_counts_differ_returns_fail_with_detail(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        # First call → src count 120, second call → dst count 100
        side_effects = [_mock_completed(0, stdout="120"), _mock_completed(0, stdout="100")]
        with (
            patch("core.media_verify._find_ffprobe", return_value="ffprobe"),
            patch("subprocess.run", side_effect=side_effects),
        ):
            result = verify_prores_mxf(src, dst)
        assert result.ok is False
        assert result.advisory is False
        assert "src=120" in result.detail
        assert "dst=100" in result.detail

    def test_ffprobe_read_error_returns_advisory(self, tmp_path):
        src = tmp_path / "src.mxf"
        dst = tmp_path / "dst.mxf"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        # ffprobe returns non-zero → frame count cannot be read
        completed = _mock_completed(1, stdout="")
        with (
            patch("core.media_verify._find_ffprobe", return_value="ffprobe"),
            patch("subprocess.run", return_value=completed),
        ):
            result = verify_prores_mxf(src, dst)
        assert result.advisory is True
        assert result.ok is True

    def test_mxf_extension_uses_same_logic(self, tmp_path):
        """verify_prores_mxf is extension-agnostic — test with .mxf paths."""
        src = tmp_path / "src.mxf"
        dst = tmp_path / "dst.mxf"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        completed = _mock_completed(0, stdout="50")
        with (
            patch("core.media_verify._find_ffprobe", return_value="ffprobe"),
            patch("subprocess.run", return_value=completed),
        ):
            result = verify_prores_mxf(src, dst)
        assert result.ok is True


# ---------------------------------------------------------------------------
# verify_image_sequence
# ---------------------------------------------------------------------------

class TestVerifyImageSequence:
    def test_dpx_no_gaps_returns_ok_with_count(self, tmp_path):
        src = tmp_path / "src_seq"
        dst = tmp_path / "dst_seq"
        _make_sequence(src, "shot_001", list(range(1, 11)), ".dpx")
        _make_sequence(dst, "shot_001", list(range(1, 11)), ".dpx")
        result = verify_image_sequence(src, dst, (".dpx",))
        assert result.ok is True
        assert result.advisory is False
        assert "10" in result.detail

    def test_dpx_gap_detected_returns_fail(self, tmp_path):
        src = tmp_path / "src_seq"
        dst = tmp_path / "dst_seq"
        frames = [1, 2, 3, 4, 6, 7, 8]  # 5 is missing
        _make_sequence(src, "shot", frames, ".dpx")
        _make_sequence(dst, "shot", frames, ".dpx")
        result = verify_image_sequence(src, dst, (".dpx",))
        assert result.ok is False
        assert "5" in result.detail
        assert "missing frames" in result.detail

    def test_dst_count_mismatch_returns_fail(self, tmp_path):
        src = tmp_path / "src_seq"
        dst = tmp_path / "dst_seq"
        _make_sequence(src, "shot", list(range(1, 11)), ".dpx")
        _make_sequence(dst, "shot", list(range(1, 9)), ".dpx")  # 2 frames short
        result = verify_image_sequence(src, dst, (".dpx",))
        assert result.ok is False
        assert "src=10" in result.detail
        assert "dst=8" in result.detail

    def test_exr_no_gaps_returns_ok(self, tmp_path):
        src = tmp_path / "src_exr"
        dst = tmp_path / "dst_exr"
        _make_sequence(src, "comp", list(range(1001, 1025)), ".exr")
        _make_sequence(dst, "comp", list(range(1001, 1025)), ".exr")
        result = verify_image_sequence(src, dst, (".exr",))
        assert result.ok is True
        assert "24" in result.detail

    def test_exr_gap_returns_fail_listing_missing(self, tmp_path):
        src = tmp_path / "src_exr"
        dst = tmp_path / "dst_exr"
        frames = list(range(1001, 1010)) + list(range(1012, 1020))  # 1010 and 1011 missing
        _make_sequence(src, "comp", frames, ".exr")
        _make_sequence(dst, "comp", frames, ".exr")
        result = verify_image_sequence(src, dst, (".exr",))
        assert result.ok is False
        assert "1010" in result.detail
        assert "1011" in result.detail

    def test_no_sequence_files_returns_advisory(self, tmp_path):
        src = tmp_path / "empty_src"
        dst = tmp_path / "empty_dst"
        src.mkdir()
        dst.mkdir()
        result = verify_image_sequence(src, dst, (".dpx",))
        assert result.advisory is True
        assert result.ok is True

    def test_many_gaps_truncated_at_10_in_detail(self, tmp_path):
        src = tmp_path / "src_seq"
        dst = tmp_path / "dst_seq"
        # frames 1-5 then jump to 17 — gap of 11 frames (6-16)
        frames = list(range(1, 6)) + list(range(17, 22))
        _make_sequence(src, "shot", frames, ".dpx")
        _make_sequence(dst, "shot", frames, ".dpx")
        result = verify_image_sequence(src, dst, (".dpx",))
        assert result.ok is False
        assert "+1 more" in result.detail  # 11 gaps, report 10 + "+1 more"


# ---------------------------------------------------------------------------
# verify_arriraw
# ---------------------------------------------------------------------------

class TestVerifyArriraw:
    def test_art_absent_returns_advisory(self, tmp_path):
        ari = tmp_path / "A001_C001.ari"
        ari.write_bytes(b"\x00")
        with patch("core.media_verify._find_art", return_value=None):
            result = verify_arriraw(ari)
        assert result.advisory is True
        assert result.ok is True
        assert "ART not installed" in result.detail

    def test_art_present_exit0_returns_ok(self, tmp_path):
        ari = tmp_path / "A001_C001.ari"
        ari.write_bytes(b"\x00")
        with (
            patch("core.media_verify._find_art", return_value="/usr/local/bin/arri-art"),
            patch("subprocess.run", return_value=_mock_completed(0)),
        ):
            result = verify_arriraw(ari)
        assert result.ok is True
        assert result.advisory is False
        assert "ART verified" in result.detail

    def test_art_present_exit1_returns_fail(self, tmp_path):
        ari = tmp_path / "A001_C001.ari"
        ari.write_bytes(b"\x00")
        with (
            patch("core.media_verify._find_art", return_value="/usr/local/bin/arri-art"),
            patch("subprocess.run", return_value=_mock_completed(1, stderr="checksum error")),
        ):
            result = verify_arriraw(ari)
        assert result.ok is False
        assert result.advisory is False
        assert "ART verification failed" in result.detail


# ---------------------------------------------------------------------------
# verify_file dispatcher
# ---------------------------------------------------------------------------

class TestVerifyFileDispatcher:
    def test_mov_dispatches_to_prores_mxf(self, tmp_path):
        src = tmp_path / "clip.mov"
        dst = tmp_path / "clip.mov"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_prores_mxf") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            result = verify_file(src, dst)
        mock_fn.assert_called_once_with(src, dst)
        assert result is not None

    def test_mxf_dispatches_to_prores_mxf(self, tmp_path):
        src = tmp_path / "clip.mxf"
        dst = tmp_path / "clip.mxf"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_prores_mxf") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(src, dst)
        mock_fn.assert_called_once()

    def test_dpx_dispatches_to_image_sequence(self, tmp_path):
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        src = src_dir / "frame.0001.dpx"
        dst = dst_dir / "frame.0001.dpx"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_image_sequence") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(src, dst)
        mock_fn.assert_called_once_with(src_dir, dst_dir)

    def test_exr_dispatches_to_image_sequence(self, tmp_path):
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        src = src_dir / "comp.0001.exr"
        dst = dst_dir / "comp.0001.exr"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_image_sequence") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(src, dst)
        mock_fn.assert_called_once()

    def test_ari_dispatches_to_arriraw(self, tmp_path):
        src = tmp_path / "scene.ari"
        dst = tmp_path / "scene.ari"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_arriraw") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(src, dst)
        mock_fn.assert_called_once_with(dst)

    def test_unrecognised_extension_returns_none(self, tmp_path):
        src = tmp_path / "audio.wav"
        dst = tmp_path / "audio.wav"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        assert verify_file(src, dst) is None

    def test_txt_extension_returns_none(self, tmp_path):
        src = tmp_path / "notes.txt"
        dst = tmp_path / "notes.txt"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        assert verify_file(src, dst) is None

    def test_r3d_inside_rdc_dispatches_to_clip(self, tmp_path):
        """An .r3d file inside an .RDC folder triggers verify_r3d_clip on the RDC."""
        rdc_src = tmp_path / "src_root" / "A001_C001.RDC"
        rdc_dst = tmp_path / "dst_root" / "A001_C001.RDC"
        rdc_src.mkdir(parents=True)
        rdc_dst.mkdir(parents=True)
        src = rdc_src / "A001_C001_0001.R3D"
        dst = rdc_dst / "A001_C001_0001.R3D"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_r3d_clip") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(src, dst)
        # Should be called with the destination .RDC folder
        mock_fn.assert_called_once_with(rdc_dst)

    def test_r3d_not_in_rdc_dispatches_to_parent(self, tmp_path):
        """An .r3d file not inside an .RDC folder still calls verify_r3d_clip."""
        src_dir = tmp_path / "src_root"
        dst_dir = tmp_path / "dst_root"
        src_dir.mkdir()
        dst_dir.mkdir()
        src = src_dir / "clip.r3d"
        dst = dst_dir / "clip.r3d"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_r3d_clip") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(src, dst)
        mock_fn.assert_called_once_with(dst_dir)

    def test_seq_dirs_seen_deduplicates_image_sequence_check(self, tmp_path):
        """
        When multiple files share the same source directory, the image-sequence
        check must only run once.
        """
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        files = [
            (src_dir / "frame.0001.dpx", dst_dir / "frame.0001.dpx"),
            (src_dir / "frame.0002.dpx", dst_dir / "frame.0002.dpx"),
        ]
        for s, d in files:
            s.write_bytes(b"\x00")
            d.write_bytes(b"\x00")

        seen: set = set()
        with patch("core.media_verify.verify_image_sequence") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(files[0][0], files[0][1], _seq_dirs_seen=seen)
            verify_file(files[1][0], files[1][1], _seq_dirs_seen=seen)
        # verify_image_sequence must be called exactly once, not twice
        assert mock_fn.call_count == 1

    def test_case_insensitive_extension_matching(self, tmp_path):
        """Extensions should be matched case-insensitively (.MOV, .MXF, etc.)."""
        src = tmp_path / "CLIP.MOV"
        dst = tmp_path / "CLIP.MOV"
        src.write_bytes(b"\x00")
        dst.write_bytes(b"\x00")
        with patch("core.media_verify.verify_prores_mxf") as mock_fn:
            mock_fn.return_value = MediaVerifyResult(ok=True, detail="ok")
            verify_file(src, dst)
        mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# Integration: offload.py calls media_verify and writes to COC log
# ---------------------------------------------------------------------------

class TestOffloadMediaVerifyIntegration:
    """
    Drive run_offload end-to-end with mocked media_verify.verify_file to
    confirm that COC log entries and CellResult.media_verify_log are correct.
    """

    def _run(self, sources, dests, **kwargs):
        from unittest.mock import MagicMock
        from core.offload import run_offload, OffloadConfig
        cfg = OffloadConfig(**kwargs)
        return run_offload(sources, dests, cfg, MagicMock(), MagicMock())

    def test_media_verify_ok_written_to_coc(self, tmp_path, monkeypatch):
        from core.offload import OffloadSource, OffloadDest
        monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "_logs")
        monkeypatch.setattr("core.offload.save_offload_manifest", lambda *a, **k: None)

        src_dir = tmp_path / "A001"
        src_dir.mkdir()
        (src_dir / "clip.mov").write_bytes(b"prores data")
        src = OffloadSource(label="A001", path=src_dir)
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        ok_result = MediaVerifyResult(ok=True, detail="frame counts match: 120 packets")
        with patch("core.media_verify.verify_file", return_value=ok_result):
            results, _, log_path = self._run([src], [dst])

        assert results[0].media_verify_log
        content = log_path.read_text()
        assert "MEDIA VERIFY OK" in content

    def test_media_verify_advisory_written_to_coc(self, tmp_path, monkeypatch):
        from core.offload import OffloadSource, OffloadDest
        monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "_logs")
        monkeypatch.setattr("core.offload.save_offload_manifest", lambda *a, **k: None)

        src_dir = tmp_path / "A001"
        src_dir.mkdir()
        (src_dir / "take.ari").write_bytes(b"arriraw data")
        src = OffloadSource(label="A001", path=src_dir)
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        advisory_result = MediaVerifyResult(
            ok=True,
            detail="ART not installed — manual ARRIRAW verification recommended",
            advisory=True,
        )
        with patch("core.media_verify.verify_file", return_value=advisory_result):
            results, _, log_path = self._run([src], [dst])

        content = log_path.read_text()
        assert "MEDIA VERIFY ADVISORY" in content
        # Advisory must NOT fail the offload
        from core.offload import CellState
        assert results[0].state == CellState.DONE

    def test_media_verify_failed_fails_the_cell(self, tmp_path, monkeypatch):
        from core.offload import OffloadSource, OffloadDest, CellState
        monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "_logs")
        monkeypatch.setattr("core.offload.save_offload_manifest", lambda *a, **k: None)

        src_dir = tmp_path / "A001"
        src_dir.mkdir()
        (src_dir / "clip.mov").write_bytes(b"prores data")
        src = OffloadSource(label="A001", path=src_dir)
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        fail_result = MediaVerifyResult(
            ok=False,
            detail="frame count mismatch: src=120 dst=100",
            advisory=False,
        )
        with patch("core.media_verify.verify_file", return_value=fail_result):
            results, _, log_path = self._run([src], [dst])

        assert results[0].state == CellState.FAILED
        content = log_path.read_text()
        assert "MEDIA VERIFY FAILED" in content

    def test_media_verify_none_does_not_appear_in_log(self, tmp_path, monkeypatch):
        """verify_file returning None for unrecognised files must not add log entries."""
        from core.offload import OffloadSource, OffloadDest
        monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "_logs")
        monkeypatch.setattr("core.offload.save_offload_manifest", lambda *a, **k: None)

        src_dir = tmp_path / "A001"
        src_dir.mkdir()
        (src_dir / "notes.txt").write_bytes(b"notes")
        src = OffloadSource(label="A001", path=src_dir)
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        with patch("core.media_verify.verify_file", return_value=None):
            results, _, log_path = self._run([src], [dst])

        assert results[0].media_verify_log == []
        content = log_path.read_text()
        assert "MEDIA VERIFY" not in content
