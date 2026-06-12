"""
Tests for core/rclone_bridge.py

Covers:
- _PROGRESS_RE parsing of rclone --stats-one-line output lines
- _CURRENT_FILE_RE parsing of rclone INFO file-activity lines
- Graceful degradation when optional fields (speed, ETA, xfr count) are absent
"""

import re
import pytest

# Import the compiled regexes directly so we can unit-test them in isolation
# without starting any subprocesses.
from core.rclone_bridge import _PROGRESS_RE, _CURRENT_FILE_RE


# ---------------------------------------------------------------------------
# _PROGRESS_RE — stats line parsing
# ---------------------------------------------------------------------------

class TestProgressRegex:
    """_PROGRESS_RE must extract percent, speed, ETA and file counts."""

    def _match(self, line):
        """Return the regex match object or None."""
        return _PROGRESS_RE.search(line)

    # ── full stats line with all fields ────────────────────────────────────

    def test_full_line_with_xfr_counts(self):
        line = (
            "2026/06/08 15:38:36 NOTICE: 19.996 MiB / 2.421 GiB, 1%, "
            "2.5 MB/s, ETA 14m30s (xfr#12/47)"
        )
        m = self._match(line)
        assert m is not None
        assert m.group(1) == "1"            # percent
        assert m.group(2) == "2.5 MB/s"    # speed
        assert m.group(3) == "14m30s"       # ETA
        assert m.group(4) == "12"           # files done
        assert m.group(5) == "47"           # files total

    def test_full_line_with_xfr_and_chk_counts(self):
        """rclone sometimes emits '(xfr#5/47, chk#3/47)' -- we only need xfr."""
        line = "NOTICE: 45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47, chk#3/47)"
        m = self._match(line)
        assert m is not None
        assert m.group(1) == "9"
        assert m.group(2) == "12.3 MB/s"
        assert m.group(3) == "1m2s"
        assert m.group(4) == "5"
        assert m.group(5) == "47"

    def test_100_percent_line(self):
        """Completion line observed in real rclone output (local copy finished)."""
        line = "2026/06/10 17:01:57 NOTICE:       150 MiB / 150 MiB, 100%, 0 B/s, ETA -"
        m = self._match(line)
        assert m is not None
        assert m.group(1) == "100"
        assert m.group(2) == "0 B/s"
        assert m.group(3) == "-"
        # No xfr counts in this line
        assert m.group(4) is None
        assert m.group(5) is None

    def test_zero_xfr_count(self):
        """xfr#0/N is a valid state at the start of a transfer."""
        line = (
            "2026/06/08 15:38:36 NOTICE: 19.996 MiB / 2.421 GiB, 1%, "
            "0 B/s, ETA - (xfr#0/20)"
        )
        m = self._match(line)
        assert m is not None
        assert m.group(4) == "0"
        assert m.group(5) == "20"

    # ── missing optional fields: graceful degradation ──────────────────────

    def test_missing_speed_and_eta(self):
        """A minimal stats line with only bytes and percent must still match."""
        line = "19.996 MiB / 2.421 GiB, 1%"
        m = self._match(line)
        assert m is not None
        assert m.group(1) == "1"
        assert m.group(2) is None   # no speed
        assert m.group(3) is None   # no ETA
        assert m.group(4) is None   # no files done
        assert m.group(5) is None   # no files total

    def test_missing_xfr_counts_only(self):
        """Line with speed and ETA but no xfr counts."""
        line = "45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s"
        m = self._match(line)
        assert m is not None
        assert m.group(1) == "9"
        assert m.group(2) == "12.3 MB/s"
        assert m.group(3) == "1m2s"
        assert m.group(4) is None
        assert m.group(5) is None

    def test_speed_with_gib_unit(self):
        """Speed values can use GiB/s for very fast transfers."""
        line = "1.500 GiB / 10 GiB, 15%, 1.2 GiB/s, ETA 7s (xfr#3/10)"
        m = self._match(line)
        assert m is not None
        assert m.group(2) == "1.2 GiB/s"

    def test_speed_with_kib_unit(self):
        line = "512 KiB / 100 MiB, 0%, 512 KiB/s, ETA 3m20s"
        m = self._match(line)
        assert m is not None
        assert m.group(2) == "512 KiB/s"

    # ── non-stats lines must not match ─────────────────────────────────────

    def test_non_stats_line_does_not_match(self):
        line = "2026/06/10 17:01:51 INFO  : file3.dat: Copied (server-side copy)"
        assert self._match(line) is None

    def test_empty_line_does_not_match(self):
        assert self._match("") is None

    def test_plain_text_does_not_match(self):
        assert self._match("rclone copy finished successfully") is None


# ---------------------------------------------------------------------------
# _CURRENT_FILE_RE — per-file INFO line parsing
# ---------------------------------------------------------------------------

class TestCurrentFileRegex:
    """_CURRENT_FILE_RE must extract the filename from rclone INFO copy lines."""

    def _match(self, line):
        return _CURRENT_FILE_RE.search(line)

    def test_copied_with_note(self):
        line = "2026/06/10 17:01:51 INFO  : file3.dat: Copied (server-side copy)"
        m = self._match(line)
        assert m is not None
        assert m.group(1).strip() == "file3.dat"

    def test_copying_in_progress(self):
        line = "2026/06/10 17:01:51 INFO  : video/project.mov: Copying"
        m = self._match(line)
        assert m is not None
        assert m.group(1).strip() == "video/project.mov"

    def test_deep_path(self):
        line = "INFO  : subdir/nested/photo.jpg: Copied"
        m = self._match(line)
        assert m is not None
        assert m.group(1).strip() == "subdir/nested/photo.jpg"

    def test_copied_bare(self):
        line = "INFO  : path/to/file.r3d: Copied"
        m = self._match(line)
        assert m is not None
        assert "file.r3d" in m.group(1)

    def test_non_copy_info_line_does_not_match(self):
        """Lines about checking or transferring should not match."""
        line = "INFO  : bigfile.dat: Checking"
        assert self._match(line) is None

    def test_notice_stats_line_does_not_match(self):
        line = (
            "2026/06/08 15:38:36 NOTICE: 19.996 MiB / 2.421 GiB, 1%, "
            "0 B/s, ETA - (xfr#0/20)"
        )
        assert self._match(line) is None

    def test_empty_line_does_not_match(self):
        assert self._match("") is None


# ---------------------------------------------------------------------------
# Integration: simulate what _run() does with both regexes together
# ---------------------------------------------------------------------------

class TestProgressInfoBuilding:
    """Simulate how _run() converts a stats line into a progress info dict."""

    def _parse(self, line):
        """Return the info dict as _run() would build it, or None."""
        m = _PROGRESS_RE.search(line)
        if not m:
            return None
        return {
            "line": line,
            "speed": m.group(2),
            "eta": m.group(3),
            "files_done": int(m.group(4)) if m.group(4) is not None else None,
            "files_total": int(m.group(5)) if m.group(5) is not None else None,
        }

    def test_full_info_dict(self):
        line = "NOTICE: 45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47)"
        info = self._parse(line)
        assert info is not None
        assert info["speed"] == "12.3 MB/s"
        assert info["eta"] == "1m2s"
        assert info["files_done"] == 5
        assert info["files_total"] == 47

    def test_partial_info_dict_no_xfr(self):
        """None fields must not raise when accessed."""
        line = "45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s"
        info = self._parse(line)
        assert info is not None
        assert info["files_done"] is None
        assert info["files_total"] is None
        # Accessing None is safe -- caller guards with 'if files_done is not None'
        count = info["files_done"]
        assert count is None

    def test_eta_dash_preserved(self):
        """ETA value '-' (not yet calculable) must be passed through as-is."""
        line = "100 MiB / 100 MiB, 100%, 0 B/s, ETA -"
        info = self._parse(line)
        assert info is not None
        assert info["eta"] == "-"

    def test_files_done_zero_is_falsy_but_valid(self):
        """files_done == 0 is a legitimate value; must not be treated as missing."""
        line = (
            "19.996 MiB / 2.421 GiB, 1%, 0 B/s, ETA - (xfr#0/20)"
        )
        info = self._parse(line)
        assert info is not None
        assert info["files_done"] == 0      # falsy but not None
        assert info["files_total"] == 20


# ---------------------------------------------------------------------------
# _run — subprocess orchestration (Popen fully mocked, no rclone needed)
# ---------------------------------------------------------------------------

import io
import subprocess
from unittest.mock import patch

import core.rclone_bridge as rb
from core.rclone_bridge import _run


class FakeProc:
    """Scripted stand-in for subprocess.Popen running rclone.

    stdout/stderr are StringIO streams so _run's reader threads consume
    them exactly as they would real pipes.
    """

    def __init__(self, stdout="", stderr="", returncode=0, hang=False):
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    def wait(self, timeout=None):
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired(cmd="rclone", timeout=timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        self._hang = False
        self.returncode = -9

    def poll(self):
        return self.returncode


class TestRunSubprocess:
    """_run must collect output, surface progress and always clear _current_proc."""

    def _patch(self, fake):
        return patch("core.rclone_bridge.subprocess.Popen", return_value=fake)

    def test_returns_returncode_stdout_stderr(self):
        fake = FakeProc(stdout="line1\nline2\n", stderr="err1\n", returncode=0)
        with self._patch(fake):
            r = _run(["lsjson", "remote:"])
        assert r.returncode == 0
        assert r.stdout == "line1\nline2\n"
        assert r.stderr == "err1\n"

    def test_nonzero_returncode_propagated(self):
        fake = FakeProc(returncode=3)
        with self._patch(fake):
            r = _run(["size", "remote:"])
        assert r.returncode == 3

    def test_command_echoed_to_log_cb(self):
        fake = FakeProc()
        logged = []
        with self._patch(fake):
            _run(["copy", "a", "b"], log_cb=lambda m, l: logged.append((m, l)))
        assert logged[0] == ("  rclone copy a b", "info")

    def test_progress_cb_receives_pct_and_info(self):
        stats = "NOTICE: 45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47)\n"
        fake = FakeProc(stderr=stats)
        seen = []
        with self._patch(fake):
            _run(["copy", "a", "b"], progress_cb=lambda pct, info: seen.append((pct, info)))
        assert len(seen) == 1
        pct, info = seen[0]
        assert pct == 9
        assert info["speed"] == "12.3 MB/s"
        assert info["eta"] == "1m2s"
        assert info["files_done"] == 5
        assert info["files_total"] == 47

    def test_current_file_attached_to_progress_info(self):
        stderr = (
            "INFO  : A001_C002.mov: Copying\n"
            "NOTICE: 19.996 MiB / 2.421 GiB, 1%, 0 B/s, ETA - (xfr#0/20)\n"
        )
        fake = FakeProc(stderr=stderr)
        seen = []
        with self._patch(fake):
            _run(["copy", "a", "b"], progress_cb=lambda pct, info: seen.append(info))
        assert seen[0]["current_file"] == "A001_C002.mov"

    def test_progress_cb_exception_swallowed(self):
        stats = "NOTICE: 45.2 MiB / 500 MiB, 9%, 12.3 MB/s, ETA 1m2s (xfr#5/47)\n"
        fake = FakeProc(stderr=stats)

        def boom(pct, info):
            raise RuntimeError("ui died")

        with self._patch(fake):
            r = _run(["copy", "a", "b"], progress_cb=boom)
        assert r.returncode == 0

    def test_non_stats_stderr_forwarded_to_log_cb(self):
        fake = FakeProc(stderr="ERROR : something broke\n")
        logged = []
        with self._patch(fake):
            _run(["copy", "a", "b"], log_cb=lambda m, l: logged.append(m))
        assert "ERROR : something broke" in logged

    def test_timeout_kills_process(self):
        fake = FakeProc(hang=True)
        with self._patch(fake):
            r = _run(["copy", "a", "b"], timeout=1)
        assert fake.killed is True
        assert r.returncode == -9

    def test_current_proc_cleared_after_run(self):
        fake = FakeProc()
        with self._patch(fake):
            _run(["copy", "a", "b"])
        assert rb._current_proc is None

    def test_current_proc_cleared_even_after_timeout(self):
        fake = FakeProc(hang=True)
        with self._patch(fake):
            _run(["copy", "a", "b"], timeout=1)
        assert rb._current_proc is None
