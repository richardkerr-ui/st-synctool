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


class TestQuotaClassificationSurfacing:
    """M10.2: sync/copyto must surface a plain-language quota message on failure."""

    def _patch(self, fake):
        return patch("core.rclone_bridge.subprocess.Popen", return_value=fake)

    def test_sync_surfaces_rate_limit_message(self):
        fake = FakeProc(stderr="ERROR : googleapi: Error 403: userRateLimitExceeded\n",
                        returncode=1)
        logged = []
        with self._patch(fake):
            ok = rb.sync("a", "b", log_cb=lambda m, l: logged.append((m, l)))
        assert ok is False
        assert any("daily upload limit" in m and l == "error" for m, l in logged)

    def test_copyto_surfaces_storage_full_message(self):
        fake = FakeProc(stderr="ERROR : storageQuotaExceeded\n", returncode=1)
        logged = []
        with self._patch(fake):
            ok = rb.copyto("a", "b", log_cb=lambda m, l: logged.append((m, l)))
        assert ok is False
        assert any("out of storage space" in m for m, l in logged)

    def test_sync_no_quota_message_on_unrelated_failure(self):
        fake = FakeProc(stderr="ERROR : connection refused\n", returncode=1)
        logged = []
        with self._patch(fake):
            rb.sync("a", "b", log_cb=lambda m, l: logged.append((m, l)))
        assert not any("daily upload limit" in m or "storage space" in m for m, l in logged)


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


# ---------------------------------------------------------------------------
# cancel_current — _current_proc locking and the cancel/run race (M1.3)
# ---------------------------------------------------------------------------

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.rclone_bridge import (
    cancel_current, is_rclone_installed, lsjson, remote_size,
    lsjson_to_manifest, sync, copyto, deletefile, path_exists,
)


class TestCancelCurrent:
    def teardown_method(self):
        with rb._current_proc_lock:
            rb._current_proc = None

    def test_returns_false_when_no_proc(self):
        with rb._current_proc_lock:
            rb._current_proc = None
        assert cancel_current() is False

    def test_returns_false_when_proc_already_finished(self):
        proc = MagicMock()
        proc.poll.return_value = 0          # already exited
        with rb._current_proc_lock:
            rb._current_proc = proc
        assert cancel_current() is False
        proc.terminate.assert_not_called()

    def test_terminates_running_proc(self):
        proc = MagicMock()
        proc.poll.return_value = None       # still running
        with rb._current_proc_lock:
            rb._current_proc = proc
        assert cancel_current() is True
        proc.terminate.assert_called_once()

    def test_kills_when_terminate_times_out(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired("rclone", 5)
        with rb._current_proc_lock:
            rb._current_proc = proc
        assert cancel_current() is True
        proc.kill.assert_called_once()

    def test_returns_false_when_terminate_raises(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.terminate.side_effect = OSError("gone")
        with rb._current_proc_lock:
            rb._current_proc = proc
        assert cancel_current() is False

    def test_cancel_during_run_race(self):
        """cancel_current() fired from another thread while _run is blocked in
        wait() must terminate the transfer and leave _current_proc cleared."""

        class BlockingProc(FakeProc):
            def __init__(self):
                super().__init__()
                self.returncode = None
                self._done = threading.Event()

            def wait(self, timeout=None):
                if not self._done.wait(timeout=timeout if timeout else 60):
                    raise subprocess.TimeoutExpired("rclone", timeout)
                return self.returncode

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15
                self._done.set()

        proc = BlockingProc()
        result = {}

        def run():
            with patch("core.rclone_bridge.subprocess.Popen", return_value=proc):
                result["r"] = _run(["copy", "a", "b"], timeout=30)

        t = threading.Thread(target=run)
        t.start()
        # Wait for _run to register the proc before cancelling
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with rb._current_proc_lock:
                if rb._current_proc is proc:
                    break
            time.sleep(0.01)
        else:
            t.join(timeout=1)
            pytest.fail("_run never registered _current_proc")

        assert cancel_current() is True
        t.join(timeout=5)
        assert not t.is_alive()
        assert result["r"].returncode == -15
        assert rb._current_proc is None


# ---------------------------------------------------------------------------
# cat_sha256 — streaming hash of a remote file (M5.1 deep Drive verify)
# ---------------------------------------------------------------------------

class FakeCatProc:
    """Binary-stream stand-in for `rclone cat` Popen."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, hang=False):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
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


class TestCatSha256:
    def _patch(self, fake):
        return patch("core.rclone_bridge.subprocess.Popen", return_value=fake)

    def test_hashes_streamed_bytes(self):
        import hashlib
        data = b"alpha bravo charlie" * 1000
        fake = FakeCatProc(stdout=data, returncode=0)
        with self._patch(fake):
            digest = rb.cat_sha256("gdrive:a.mov")
        assert digest == hashlib.sha256(data).hexdigest()

    def test_chunked_read_matches_full(self):
        import hashlib
        data = bytes(range(256)) * 5000
        fake = FakeCatProc(stdout=data, returncode=0)
        with self._patch(fake):
            digest = rb.cat_sha256("gdrive:a.mov", chunk_size=64)
        assert digest == hashlib.sha256(data).hexdigest()

    def test_nonzero_exit_raises(self):
        fake = FakeCatProc(stdout=b"", stderr=b"directory not found", returncode=3)
        with self._patch(fake):
            with pytest.raises(RuntimeError, match="rclone cat failed"):
                rb.cat_sha256("gdrive:missing.mov")

    def test_timeout_raises_and_kills(self):
        fake = FakeCatProc(stdout=b"x", hang=True)
        with self._patch(fake):
            with pytest.raises(TimeoutError):
                rb.cat_sha256("gdrive:a.mov", timeout=1)
        assert fake.killed is True

    def test_current_proc_cleared(self):
        fake = FakeCatProc(stdout=b"data", returncode=0)
        with self._patch(fake):
            rb.cat_sha256("gdrive:a.mov")
        assert rb._current_proc is None

    def test_extra_flags_passed(self):
        fake = FakeCatProc(stdout=b"data", returncode=0)
        with patch("core.rclone_bridge.subprocess.Popen", return_value=fake) as popen:
            rb.cat_sha256("gdrive:a.mov", extra_flags=["--drive-root-folder-id", "X"])
        args = popen.call_args[0][0]
        assert "cat" in args and "--drive-root-folder-id" in args
        assert args[-1] == "gdrive:a.mov"


# ---------------------------------------------------------------------------
# is_rclone_installed
# ---------------------------------------------------------------------------

class TestIsRcloneInstalled:
    def test_true_when_on_path(self):
        with patch("core.rclone_bridge.shutil.which", return_value="/usr/local/bin/rclone"):
            assert is_rclone_installed() is True

    def test_false_when_missing(self):
        with patch("core.rclone_bridge.shutil.which", return_value=None):
            assert is_rclone_installed() is False


# ---------------------------------------------------------------------------
# Command wrappers — lsjson, remote_size, copyto, deletefile, path_exists
# All _run calls mocked; we assert argument construction and error paths.
# ---------------------------------------------------------------------------

def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestLsjson:
    def test_parses_json_on_success(self):
        with patch("core.rclone_bridge._run", return_value=_result(stdout='[{"Path": "a"}]')) as m:
            assert lsjson("remote:folder") == [{"Path": "a"}]

    def test_includes_hash_flag_by_default(self):
        with patch("core.rclone_bridge._run", return_value=_result(stdout="[]")) as m:
            lsjson("remote:folder")
        args = m.call_args[0][0]
        assert "--hash" in args and "--recursive" in args
        assert args[-1] == "remote:folder"

    def test_no_hash_flag_when_disabled(self):
        with patch("core.rclone_bridge._run", return_value=_result(stdout="[]")) as m:
            lsjson("remote:folder", with_checksum=False)
        assert "--hash" not in m.call_args[0][0]

    def test_extra_flags_appended(self):
        with patch("core.rclone_bridge._run", return_value=_result(stdout="[]")) as m:
            lsjson("remote:", extra_flags=["--drive-root-folder-id", "X"])
        args = m.call_args[0][0]
        assert "--drive-root-folder-id" in args and "X" in args

    def test_raises_runtime_error_on_failure(self):
        with patch("core.rclone_bridge._run", return_value=_result(returncode=1, stderr="bad remote")):
            with pytest.raises(RuntimeError, match="bad remote"):
                lsjson("remote:folder")


class TestRemoteSize:
    def test_returns_bytes_and_count(self):
        out = '{"bytes": 1048576, "count": 12}'
        with patch("core.rclone_bridge._run", return_value=_result(stdout=out)):
            assert remote_size("remote:f") == (1048576, 12)

    def test_missing_keys_default_to_zero(self):
        with patch("core.rclone_bridge._run", return_value=_result(stdout="{}")):
            assert remote_size("remote:f") == (0, 0)

    def test_raises_on_failure(self):
        with patch("core.rclone_bridge._run", return_value=_result(returncode=1, stderr="denied")):
            with pytest.raises(RuntimeError, match="denied"):
                remote_size("remote:f")


class TestCopyto:
    def test_true_on_success_and_checksum_flag(self):
        with patch("core.rclone_bridge._run", return_value=_result()) as m:
            assert copyto("remote:a", "remote:b") is True
        args = m.call_args[0][0]
        assert args[:3] == ["copyto", "remote:a", "remote:b"]
        assert "--checksum" in args

    def test_false_on_failure(self):
        with patch("core.rclone_bridge._run", return_value=_result(returncode=1)):
            assert copyto("a", "b") is False

    def test_side_flags_appended(self):
        with patch("core.rclone_bridge._run", return_value=_result()) as m:
            copyto("a", "b", src_flags=["--s"], dst_flags=["--d"])
        args = m.call_args[0][0]
        assert "--s" in args and "--d" in args


class TestDeletefile:
    def test_true_on_success(self):
        with patch("core.rclone_bridge._run", return_value=_result()) as m:
            assert deletefile("remote:x") is True
        assert m.call_args[0][0][:2] == ["deletefile", "remote:x"]

    def test_false_on_failure(self):
        with patch("core.rclone_bridge._run", return_value=_result(returncode=1)):
            assert deletefile("remote:x") is False


class TestPathExists:
    def test_true_when_size_succeeds(self):
        with patch("core.rclone_bridge._run", return_value=_result()):
            assert path_exists("remote:x") is True

    def test_false_when_size_fails(self):
        with patch("core.rclone_bridge._run", return_value=_result(returncode=3)):
            assert path_exists("remote:x") is False


# ---------------------------------------------------------------------------
# lsjson_to_manifest — Drive listing to manifest conversion
# ---------------------------------------------------------------------------

def _lsjson_items():
    return [
        {"Path": "clips", "IsDir": True},
        {"Path": "clips/a.mov", "Size": 100, "ModTime": "2026-06-01T00:00:00Z",
         "ID": "abc123", "Hashes": {"SHA256": "AA11", "MD5": "BB22"}},
        {"Path": "b.wav", "Size": 50, "ModTime": "2026-06-02T00:00:00Z",
         "Hashes": {"xxHash": "CC33"}},
        {"Path": "c.txt", "Size": 5, "ModTime": "", "ID": ""},
    ]


class TestLsjsonToManifest:
    def _manifest(self, items=None):
        with patch("core.rclone_bridge.lsjson", return_value=items if items is not None else _lsjson_items()):
            return lsjson_to_manifest("remote:folder", label="server")

    def test_directories_skipped(self):
        m = self._manifest()
        assert "clips" not in m["files"]
        assert m["file_count"] == 3

    def test_hashes_lowercased_and_mapped(self):
        cs = self._manifest()["files"]["clips/a.mov"]["checksums"]
        assert cs["sha256"] == "aa11"
        assert cs["md5"] == "bb22"

    def test_xxhash_mapped_to_xxhash3_64(self):
        entry = self._manifest()["files"]["b.wav"]
        assert entry["checksums"]["xxhash3_64"] == "cc33"
        assert entry["hash_algorithm"] == "xxhash3_64"

    def test_hash_algorithm_prefers_sha256(self):
        assert self._manifest()["files"]["clips/a.mov"]["hash_algorithm"] == "sha256"

    def test_no_hashes_falls_back_to_rclone_lsjson(self):
        entry = self._manifest()["files"]["c.txt"]
        assert entry["checksums"] == {}
        assert entry["hash_algorithm"] == "rclone-lsjson"

    def test_gdrive_url_built_from_id(self):
        files = self._manifest()["files"]
        assert files["clips/a.mov"]["gdrive_url"] == "https://drive.google.com/file/d/abc123/view"
        assert files["c.txt"]["gdrive_url"] == ""

    def test_top_level_fields(self):
        m = self._manifest()
        assert m["label"] == "server"
        assert m["root"] == "remote:folder"
        assert m["total_size_bytes"] == 155
        assert m["checksum_context"]["gdrive_mode"] is True
        assert m["checksum_context"]["method"] == "rclone"

    def test_empty_listing_yields_empty_manifest(self):
        m = self._manifest(items=[])
        assert m["files"] == {} and m["file_count"] == 0 and m["total_size_bytes"] == 0


# ---------------------------------------------------------------------------
# sync — mode/conflict flag construction and failure logging
# ---------------------------------------------------------------------------

class TestSync:
    def _call(self, run_result=None, **kwargs):
        with patch("core.rclone_bridge._run", return_value=run_result or _result()) as m:
            ok = sync("src:", "dst:", **kwargs)
        return ok, m

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid rclone mode"):
            sync("a", "b", mode="move")

    def test_copy_mode_uses_copy_command(self):
        ok, m = self._call(mode="copy")
        assert ok is True
        assert m.call_args[0][0][0] == "copy"

    def test_sync_mode_uses_sync_command(self):
        ok, m = self._call(mode="sync")
        assert m.call_args[0][0][0] == "sync"

    def test_conflict_skip_adds_ignore_existing(self):
        ok, m = self._call(conflict="skip")
        assert "--ignore-existing" in m.call_args[0][0]

    def test_conflict_update_adds_update(self):
        ok, m = self._call(conflict="update")
        assert "--update" in m.call_args[0][0]

    def test_conflict_rename_warns_and_falls_back(self):
        logged = []
        ok, m = self._call(conflict="rename", log_cb=lambda msg, lvl: logged.append((msg, lvl)))
        args = m.call_args[0][0]
        assert "--ignore-existing" not in args and "--update" not in args
        assert any(lvl == "warning" and "Rename copy" in msg for msg, lvl in logged)

    def test_dry_run_flag(self):
        ok, m = self._call(dry_run=True)
        assert "--dry-run" in m.call_args[0][0]

    def test_side_flags_appended(self):
        ok, m = self._call(src_flags=["--sf"], dst_flags=["--df"])
        args = m.call_args[0][0]
        assert "--sf" in args and "--df" in args

    def test_failure_returns_false_and_logs_error(self):
        logged = []
        ok, m = self._call(run_result=_result(returncode=5),
                           log_cb=lambda msg, lvl: logged.append((msg, lvl)))
        assert ok is False
        assert any(lvl == "error" and "exited with code 5" in msg for msg, lvl in logged)

    def test_checksum_always_present(self):
        ok, m = self._call()
        assert "--checksum" in m.call_args[0][0]
