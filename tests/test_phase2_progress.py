"""
Phase 2 end-to-end progress tests.

Covers the full pipeline:
  rclone stderr output
    -> core/rclone_bridge._run() (regex parse + info dict)
      -> core/transfer._rclone_progress (pct mapping)
        -> TransferWorker.progress signal
          -> TransferTab._on_progress
            -> gui/log_widget.LogWidget.set_progress / hide_progress

Also covers:
  - Local-to-local transfer (transfer_folder) with real temp files
  - LogWidget.set_progress edge cases (plain int, missing fields)
  - LogWidget.hide_progress clears all three labels
"""

import io
import os
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure Qt runs in offscreen mode before any PyQt6 import
# ---------------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# One global QApplication for the whole module (must exist before any QWidget)
_app = QApplication.instance() or QApplication([])

from gui.log_widget import LogWidget
from core.rclone_bridge import _run as rclone_run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeStream:
    """In-memory text stream that mimics a subprocess pipe for readline()."""

    def __init__(self, lines):
        self._buf = io.StringIO("".join(lines))

    def readline(self):
        return self._buf.readline()

    def close(self):
        pass


class _FakeProc:
    """Minimal subprocess.Popen stand-in."""

    def __init__(self, stderr_lines, stdout_lines=None, returncode=0):
        self.returncode = returncode
        self.stdout = _FakeStream(stdout_lines or [])
        self.stderr = _FakeStream(stderr_lines)

    def wait(self, timeout=None):
        pass

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    def terminate(self):
        pass


def _make_log_widget():
    """Return a fresh LogWidget with progress enabled."""
    return LogWidget("test", with_progress=True)


def _run_with_fake_stderr(stderr_lines, stdout_lines=None):
    """Patch subprocess.Popen and call rclone_run, collecting progress callbacks."""
    calls = []

    def progress_cb(pct, info):
        calls.append((pct, info))

    proc = _FakeProc(stderr_lines, stdout_lines)
    with patch("subprocess.Popen", return_value=proc):
        result = rclone_run(["copy", "src", "dst"], progress_cb=progress_cb)

    return result, calls


# ===========================================================================
# 1. Integration: rclone output -> log_widget labels
# ===========================================================================

class TestRcloneOutputToLogWidget:
    """Simulate realistic rclone stderr and assert the correct label texts."""

    # -- full stats line: speed + ETA + xfr counts --

    def test_full_stats_line_sets_all_labels(self):
        """A complete stats line must populate all three label areas."""
        stderr = [
            "INFO  : project/footage/scene01.mov: Copying\n",
            (
                "2026/06/08 15:38:36 NOTICE: 45.2 MiB / 500 MiB, 9%, "
                "12.3 MB/s, ETA 1m2s (xfr#5/47)\n"
            ),
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls, "progress_cb must be called at least once"
        pct, info = calls[-1]

        # Feed the info dict through _on_progress logic (same as TransferTab)
        widget = _make_log_widget()
        if isinstance(info, dict):
            widget.set_progress(
                pct,
                current_file=info.get("current_file") or "",
                speed=info.get("speed") or "",
                eta=info.get("eta") or "",
                files_done=info.get("files_done"),
                files_total=info.get("files_total"),
            )
        else:
            widget.set_progress(pct, current_file=str(info) if info else "")

        assert pct == 9
        assert "5 / 47 files" in widget._file_count_label.text()
        assert "1m2s remaining" in widget._file_count_label.text()
        assert widget._speed_label.text() == "12.3 MB/s"
        # current_file should show last 2 path parts of "project/footage/scene01.mov"
        assert "footage/scene01.mov" in widget.current_file_label.text()

    # -- stats line with missing optional fields --

    def test_stats_line_missing_speed_and_eta_does_not_crash(self):
        """A minimal stats line (bytes + percent only) must not crash the widget."""
        stderr = [
            "19.996 MiB / 2.421 GiB, 1%\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls, "progress_cb must fire for a minimal stats line"
        pct, info = calls[-1]

        widget = _make_log_widget()
        if isinstance(info, dict):
            widget.set_progress(
                pct,
                current_file=info.get("current_file") or "",
                speed=info.get("speed") or "",
                eta=info.get("eta") or "",
                files_done=info.get("files_done"),
                files_total=info.get("files_total"),
            )
        else:
            widget.set_progress(pct, current_file=str(info) if info else "")

        assert pct == 1
        assert widget._speed_label.text() == ""
        assert widget._file_count_label.text() == ""
        # No crash is the key assertion -- reaching here means success.

    # -- Copying INFO line -> current_file_label --

    def test_copying_info_line_updates_current_file_label(self):
        """A 'Copying' INFO line must cause the current_file_label to update."""
        stderr = [
            "INFO  : subdir/nested/big_photo.jpg: Copying\n",
            "45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47)\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls
        pct, info = calls[-1]

        assert isinstance(info, dict)
        assert info["current_file"] is not None
        assert "big_photo.jpg" in info["current_file"]

        widget = _make_log_widget()
        widget.set_progress(
            pct,
            current_file=info.get("current_file") or "",
            speed=info.get("speed") or "",
            eta=info.get("eta") or "",
            files_done=info.get("files_done"),
            files_total=info.get("files_total"),
        )

        # Should be truncated to last 2 path components
        label = widget.current_file_label.text()
        assert "nested/big_photo.jpg" in label

    # -- Copied INFO line -> current_file still correct --

    def test_copied_info_line_current_file_still_set(self):
        """A 'Copied' INFO line (past-tense) must also update current_file."""
        stderr = [
            "INFO  : deliverables/exports/final_v2.mov: Copied (server-side copy)\n",
            "100 MiB / 100 MiB, 50%, 5.0 MB/s, ETA 30s (xfr#10/20)\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls
        pct, info = calls[-1]

        assert isinstance(info, dict)
        assert info["current_file"] is not None
        assert "final_v2.mov" in info["current_file"]

        widget = _make_log_widget()
        widget.set_progress(
            pct,
            current_file=info.get("current_file") or "",
        )
        label = widget.current_file_label.text()
        assert "final_v2.mov" in label

    # -- 100% completion -> hide_progress clears labels --

    def test_progress_100_percent_hide_clears_labels(self):
        """After hide_progress, all three labels must be empty strings."""
        widget = _make_log_widget()
        widget.set_progress(
            50,
            current_file="folder/file.r3d",
            speed="200 MB/s",
            eta="2m0s",
            files_done=10,
            files_total=20,
        )
        # Simulate _reset_controls() which calls hide_progress on completion
        widget.hide_progress()

        assert widget._file_count_label.text() == ""
        assert widget._speed_label.text() == ""
        assert widget.current_file_label.text() == ""
        assert widget._progress_container.isHidden()

    # -- current_file label path truncation --

    def test_deep_path_truncated_to_two_components(self):
        """Paths deeper than 2 components must be truncated to the last 2."""
        widget = _make_log_widget()
        widget.set_progress(10, current_file="a/b/c/d/file.mov")
        assert widget.current_file_label.text() == "d/file.mov"

    def test_two_component_path_unchanged(self):
        widget = _make_log_widget()
        widget.set_progress(10, current_file="folder/file.mov")
        assert widget.current_file_label.text() == "folder/file.mov"

    def test_bare_filename_unchanged(self):
        widget = _make_log_widget()
        widget.set_progress(10, current_file="file.mov")
        assert widget.current_file_label.text() == "file.mov"

    # -- ETA = "-" is handled gracefully --

    def test_eta_dash_not_shown_in_label(self):
        """ETA value '-' (not yet calculable) must NOT appear as 'remaining'."""
        widget = _make_log_widget()
        widget.set_progress(
            1,
            eta="-",
            files_done=0,
            files_total=20,
        )
        # When ETA is "-", only the file count should appear (no "remaining")
        label = widget._file_count_label.text()
        assert "remaining" not in label
        assert "0 / 20 files" in label

    # -- stats row: file count + ETA formatting --

    def test_file_count_without_eta_shows_count_only(self):
        widget = _make_log_widget()
        widget.set_progress(50, files_done=7, files_total=15)
        # No ETA provided -> only file count
        assert widget._file_count_label.text() == "7 / 15 files"

    def test_eta_only_no_file_count(self):
        widget = _make_log_widget()
        widget.set_progress(50, eta="45s")
        assert widget._file_count_label.text() == "45s remaining"

    def test_no_optional_args_no_labels(self):
        widget = _make_log_widget()
        widget.set_progress(50)
        assert widget._file_count_label.text() == ""
        assert widget._speed_label.text() == ""


# ===========================================================================
# 2. Local-to-local transfer: real temp files
# ===========================================================================

class TestLocalTransferWithTempFiles:
    """Real local->local transfer using transfer_folder() with temp files."""

    def test_progress_callback_fires_with_real_files(self, tmp_path):
        """transfer_folder must call progress_cb at least once with nonzero values."""
        # Create a small source folder with 4 files
        src = tmp_path / "source_card"
        src.mkdir()
        for i in range(4):
            content = f"test content for file {i} " * 200  # ~5 KB each
            (src / f"clip_{i:02d}.mov").write_text(content)

        dst = tmp_path / "destination"
        dst.mkdir()

        progress_calls = []

        def progress_cb(pct, info=None):
            progress_calls.append((pct, info))

        from core.transfer import transfer_folder
        result = transfer_folder(
            src, dst,
            log_cb=lambda m, l: None,
            progress_cb=progress_cb,
        )

        # Must have fired progress_cb at least once
        assert len(progress_calls) > 0, "progress_cb must be called at least once"

        # At least one call must have a non-zero percentage
        pcts = [p for p, _ in progress_calls]
        assert any(p > 0 for p in pcts), "At least one progress call must have pct > 0"

        # Final call should be 100%
        assert progress_calls[-1][0] == 100

        # Files must actually have been transferred
        assert result["errors"] == []
        actual_dest = Path(result["actual_dest"])
        transferred = list(actual_dest.rglob("*.mov"))
        assert len(transferred) == 4

    def test_progress_pct_increases_monotonically(self, tmp_path):
        """Progress percentages must not decrease during a local transfer."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(5):
            (src / f"file_{i}.dat").write_bytes(b"x" * (10 * 1024))  # 10 KB each

        dst = tmp_path / "dst"
        dst.mkdir()

        pcts = []

        def progress_cb(pct, info=None):
            pcts.append(pct)

        from core.transfer import transfer_folder
        transfer_folder(src, dst, log_cb=lambda m, l: None, progress_cb=progress_cb)

        numeric_pcts = [p for p in pcts if isinstance(p, int)]
        for i in range(1, len(numeric_pcts)):
            assert numeric_pcts[i] >= numeric_pcts[i - 1], (
                f"Progress went backwards: {numeric_pcts[i - 1]} -> {numeric_pcts[i]}"
            )

    def test_manifest_file_count_matches_source(self, tmp_path):
        """Manifest file_count must match the number of source files."""
        src = tmp_path / "cam_card"
        src.mkdir()
        file_count = 3
        for i in range(file_count):
            (src / f"A00{i}.R3D").write_bytes(b"R3D" * 500)  # ~1.5 KB each

        dst = tmp_path / "offload"

        from core.transfer import transfer_folder
        result = transfer_folder(
            src, dst,
            log_cb=lambda m, l: None,
            progress_cb=lambda pct, info=None: None,
        )

        manifest = result["manifest"]
        assert manifest["file_count"] == file_count
        assert all(
            fdata.get("size", 0) > 0
            for fdata in manifest["files"].values()
        ), "Every transferred file must have a non-zero size in the manifest"


# ===========================================================================
# 3. Edge cases
# ===========================================================================

class TestSetProgressEdgeCases:
    """Edge cases for LogWidget.set_progress and hide_progress."""

    def test_set_progress_plain_int_does_not_crash(self):
        """set_progress(42) with no other args must not raise."""
        widget = _make_log_widget()
        widget.set_progress(42)
        assert widget.progress_bar.value() == 42

    def test_set_progress_zero_does_not_crash(self):
        widget = _make_log_widget()
        widget.set_progress(0)
        assert widget.progress_bar.value() == 0

    def test_set_progress_100_does_not_crash(self):
        widget = _make_log_widget()
        widget.set_progress(100)
        assert widget.progress_bar.value() == 100

    def test_set_progress_makes_container_visible(self):
        # isVisible() requires the top-level window to be shown; in headless tests
        # the reliable check is isHidden() -- setVisible(True) clears the hidden flag.
        widget = _make_log_widget()
        assert widget._progress_container.isHidden()
        widget.set_progress(50)
        assert not widget._progress_container.isHidden()

    def test_hide_progress_clears_all_labels(self):
        """hide_progress must clear file_count_label, speed_label and current_file_label."""
        widget = _make_log_widget()
        widget.set_progress(
            75,
            current_file="a/b/video.mov",
            speed="500 MB/s",
            eta="10s",
            files_done=15,
            files_total=20,
        )
        # Verify labels are populated before hide
        assert widget._file_count_label.text() != ""
        assert widget._speed_label.text() != ""
        assert widget.current_file_label.text() != ""

        widget.hide_progress()

        assert widget._file_count_label.text() == "", (
            "hide_progress must clear _file_count_label"
        )
        assert widget._speed_label.text() == "", (
            "hide_progress must clear _speed_label"
        )
        assert widget.current_file_label.text() == "", (
            "hide_progress must clear current_file_label"
        )

    def test_hide_progress_hides_container(self):
        # Use isHidden() rather than isVisible() -- the latter requires the top-level
        # window to be shown, which we skip in headless tests.
        widget = _make_log_widget()
        widget.set_progress(50)
        assert not widget._progress_container.isHidden()
        widget.hide_progress()
        assert widget._progress_container.isHidden()

    def test_set_progress_empty_current_file_does_not_clear_label(self):
        """Passing current_file='' (the default) must NOT overwrite the previous value."""
        widget = _make_log_widget()
        widget.set_progress(10, current_file="folder/file.mov")
        assert widget.current_file_label.text() == "folder/file.mov"
        # Update percentage only, leaving current_file at default ""
        widget.set_progress(20)
        # Label should be unchanged (the code explicitly does `pass` for empty string)
        assert widget.current_file_label.text() == "folder/file.mov"

    def test_set_progress_without_progress_mode_does_not_crash(self):
        """LogWidget with with_progress=False must silently ignore set_progress."""
        widget = LogWidget("no-progress", with_progress=False)
        widget.set_progress(50, current_file="file.mov", speed="10 MB/s")
        # No crash is the assertion

    def test_hide_progress_without_progress_mode_does_not_crash(self):
        """hide_progress on a non-progress widget must be a no-op."""
        widget = LogWidget("no-progress", with_progress=False)
        widget.hide_progress()
        # No crash is the assertion

    # -- _on_progress logic mirrored from TransferTab --

    def test_on_progress_with_dict_info_routes_correctly(self):
        """The dict-info branch of TransferTab._on_progress must set all fields."""
        widget = _make_log_widget()
        info = {
            "current_file": "footage/shot01/A001_C001.mov",
            "speed": "250 MB/s",
            "eta": "3m15s",
            "files_done": 12,
            "files_total": 30,
        }
        pct = 40
        # Mirror _on_progress logic
        if isinstance(info, dict):
            widget.set_progress(
                pct,
                current_file=info.get("current_file") or "",
                speed=info.get("speed") or "",
                eta=info.get("eta") or "",
                files_done=info.get("files_done"),
                files_total=info.get("files_total"),
            )
        else:
            widget.set_progress(pct, current_file=str(info) if info else "")

        assert widget.progress_bar.value() == 40
        assert "12 / 30 files" in widget._file_count_label.text()
        assert "3m15s remaining" in widget._file_count_label.text()
        assert widget._speed_label.text() == "250 MB/s"
        assert "shot01/A001_C001.mov" in widget.current_file_label.text()

    def test_on_progress_with_string_info_routes_correctly(self):
        """The plain-string branch of TransferTab._on_progress must set current_file."""
        widget = _make_log_widget()
        info = "Copying files..."
        pct = 25
        if isinstance(info, dict):
            widget.set_progress(pct, current_file=info.get("current_file") or "")
        else:
            widget.set_progress(pct, current_file=str(info) if info else "")

        assert widget.progress_bar.value() == 25
        assert widget.current_file_label.text() == "Copying files..."

    def test_on_progress_with_none_info_does_not_crash(self):
        """progress_cb(pct, None) from legacy callers must not crash."""
        widget = _make_log_widget()
        info = None
        pct = 50
        if isinstance(info, dict):
            widget.set_progress(pct, current_file=info.get("current_file") or "")
        else:
            widget.set_progress(pct, current_file=str(info) if info else "")
        assert widget.progress_bar.value() == 50


# ===========================================================================
# 4. rclone _run() integration: subprocess mock
# ===========================================================================

class TestRcloneRunPipeline:
    """Verify _run() correctly parses mixed stderr lines via mocked Popen."""

    def test_current_file_available_in_info_dict(self):
        """INFO Copying line before a stats line must populate info['current_file']."""
        stderr = [
            "INFO  : project/footage/scene01.mov: Copying\n",
            "NOTICE: 45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47)\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls
        pct, info = calls[-1]
        assert pct == 9
        assert isinstance(info, dict)
        assert info["current_file"] == "project/footage/scene01.mov"

    def test_info_dict_fields_are_correct_types(self):
        """files_done and files_total must be int, speed and eta must be str."""
        stderr = [
            "NOTICE: 45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47)\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls
        _, info = calls[-1]
        assert isinstance(info["files_done"], int)
        assert isinstance(info["files_total"], int)
        assert isinstance(info["speed"], str)
        assert isinstance(info["eta"], str)

    def test_no_info_line_current_file_is_none(self):
        """Without a prior INFO line, current_file must be None."""
        stderr = [
            "45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47)\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls
        _, info = calls[-1]
        assert info["current_file"] is None

    def test_copied_line_also_updates_current_file(self):
        """'Copied' (past-tense) INFO line must also set current_file."""
        stderr = [
            "INFO  : deliverables/final.mov: Copied (server-side copy)\n",
            "100 MiB / 100 MiB, 50%, 5.0 MB/s, ETA 30s (xfr#10/20)\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls
        _, info = calls[-1]
        assert info["current_file"] is not None
        assert "final.mov" in info["current_file"]

    def test_multiple_stats_lines_fire_multiple_callbacks(self):
        """Each stats NOTICE line must trigger a separate progress_cb call."""
        stderr = [
            "NOTICE: 10 MiB / 100 MiB, 10%, 5 MB/s, ETA 18s (xfr#1/10)\n",
            "NOTICE: 20 MiB / 100 MiB, 20%, 5 MB/s, ETA 16s (xfr#2/10)\n",
            "NOTICE: 30 MiB / 100 MiB, 30%, 5 MB/s, ETA 14s (xfr#3/10)\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert len(calls) == 3
        assert calls[0][0] == 10
        assert calls[1][0] == 20
        assert calls[2][0] == 30

    def test_non_matching_lines_do_not_fire_callback(self):
        """Log lines that are not stats lines must not trigger progress_cb."""
        stderr = [
            "INFO  : Starting transfer\n",
            "DEBUG : some internal state\n",
        ]
        _, calls = _run_with_fake_stderr(stderr)
        assert calls == []

    def test_returncode_0_on_success(self):
        """_run() must reflect returncode=0 from a successful fake process."""
        result, _ = _run_with_fake_stderr([])
        assert result.returncode == 0
