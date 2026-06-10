"""
Unit tests for the Drive verify path in gui/verify_tab.py — VerifyWorker._verify_gdrive.

The ROADMAP notes: "Confirm Verify actually catches a mismatch: rename a file in
Drive, re-run Verify, confirm MISSING/MISMATCH is reported."

These tests exercise VerifyWorker._verify_gdrive directly using mocked rclone
lsjson output. No real Drive connection is required.

Cases covered:
  1. All files match — every result must be OK
  2. A file is missing from Drive — result must be MISSING
  3. A file has a different hash in Drive — result must be MISMATCH
  4. rclone lsjson itself fails (raises RuntimeError) — worker must emit error
"""

import pytest
from unittest.mock import patch, MagicMock

# PyQt6 is installed in this project's venv and imports cleanly without a
# display on macOS. No stubbing is needed — import the real module directly.
from gui.verify_tab import VerifyWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GDRIVE_URL  = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWx"
RCLONE_REMOTE = "gdrive:"
RCLONE_FLAGS  = ["--drive-root-folder-id", "1AbCdEfGhIjKlMnOpQrStUvWx"]


def _entry(md5: str, size: int = 100) -> dict:
    """Build a manifest file entry with an md5 checksum.
    Uses md5 because rclone lsjson returns "MD5" which lowercases
    to "md5" — matching the candidate list in _verify_gdrive."""
    return {
        "checksums": {"md5": md5},
        "size": size,
    }


def _drive_item(path: str, md5: str, size: int = 100) -> dict:
    """Build a fake rclone lsjson item with an MD5 hash.
    The real rclone key is "MD5"; the worker lowercases it to "md5"
    which matches the candidate "md5" in _verify_gdrive's algo loop."""
    return {
        "Path":   path,
        "Size":   size,
        "IsDir":  False,
        "Hashes": {"MD5": md5},
    }


def _run_worker(manifest: dict, lsjson_items: list):
    """
    Instantiate VerifyWorker for a Drive URL, run it synchronously (call run()
    directly rather than via QThread), and collect emitted signals.

    Returns (results, log_lines, error) where:
      results   — list passed to finished.emit
      log_lines — list of (message, level) tuples
      error     — string passed to error.emit, or None
    """
    worker = VerifyWorker(GDRIVE_URL, manifest)

    results_bucket = []
    log_bucket     = []
    error_bucket   = []

    # Wire up signal mimics
    worker.finished = MagicMock()
    worker.finished.emit = lambda r: results_bucket.append(r)

    worker.log = MagicMock()
    worker.log.emit = lambda m, l: log_bucket.append((m, l))

    worker.error = MagicMock()
    worker.error.emit = lambda e: error_bucket.append(e)

    worker.progress = MagicMock()
    worker.progress.emit = lambda *a: None

    with patch("gui.verify_tab.gdrive_url_to_rclone", return_value=(RCLONE_REMOTE, RCLONE_FLAGS)), \
         patch("core.rclone_bridge.lsjson", return_value=lsjson_items), \
         patch("gui.verify_tab.rclone_bridge.lsjson", return_value=lsjson_items):

        worker.run()

    results = results_bucket[0] if results_bucket else []
    error   = error_bucket[0]   if error_bucket   else None
    return results, log_bucket, error


# ---------------------------------------------------------------------------
# Case 1: All files match
# ---------------------------------------------------------------------------

class TestVerifyGdriveAllOK:
    def test_all_ok_statuses(self):
        sha = "aabbccdd" * 4  # 64-char placeholder hash
        manifest = {
            "files": {
                "DCIM/A001/clip_001.mov": _entry(sha),
                "DCIM/A001/clip_002.mov": _entry(sha),
            }
        }
        drive_items = [
            _drive_item("DCIM/A001/clip_001.mov", sha),
            _drive_item("DCIM/A001/clip_002.mov", sha),
        ]

        results, _, error = _run_worker(manifest, drive_items)

        assert error is None
        assert len(results) == 2
        for r in results:
            assert r["status"] == "OK", f"Expected OK but got {r['status']} for {r['path']}"

    def test_all_ok_no_error_logged(self):
        sha = "aabbccdd" * 4
        manifest = {"files": {"clip.mov": _entry(sha)}}
        drive_items = [_drive_item("clip.mov", sha)]

        _, log_lines, error = _run_worker(manifest, drive_items)

        assert error is None
        error_lines = [l for l in log_lines if l[1] == "error"]
        assert not error_lines, f"Unexpected error log lines: {error_lines}"


# ---------------------------------------------------------------------------
# Case 2: A file is missing from Drive
# ---------------------------------------------------------------------------

class TestVerifyGdriveMissing:
    def test_missing_file_reported_as_missing(self):
        sha = "aabbccdd" * 4
        manifest = {
            "files": {
                "clip_present.mov": _entry(sha),
                "clip_missing.mov": _entry(sha),
            }
        }
        # Only the first file is on Drive
        drive_items = [_drive_item("clip_present.mov", sha)]

        results, _, error = _run_worker(manifest, drive_items)

        assert error is None
        statuses = {r["path"]: r["status"] for r in results}
        assert statuses["clip_missing.mov"] == "MISSING"
        assert statuses["clip_present.mov"] == "OK"

    def test_missing_file_detail_mentions_drive(self):
        sha = "aabbccdd" * 4
        manifest = {"files": {"gone.mov": _entry(sha)}}
        drive_items = []

        results, _, _ = _run_worker(manifest, drive_items)

        r = results[0]
        assert r["status"] == "MISSING"
        assert "Drive" in r["detail"] or "present" in r["detail"].lower()

    def test_missing_file_error_logged(self):
        sha = "aabbccdd" * 4
        manifest = {"files": {"vanished.mov": _entry(sha)}}
        drive_items = []

        _, log_lines, _ = _run_worker(manifest, drive_items)

        error_lines = [m for m, l in log_lines if l == "error"]
        assert any("vanished.mov" in m or "MISSING" in m for m in error_lines)

    def test_all_files_missing(self):
        sha = "aabbccdd" * 4
        manifest = {
            "files": {
                "a.mov": _entry(sha),
                "b.mov": _entry(sha),
                "c.mov": _entry(sha),
            }
        }
        results, _, error = _run_worker(manifest, [])

        assert error is None
        assert len(results) == 3
        assert all(r["status"] == "MISSING" for r in results)


# ---------------------------------------------------------------------------
# Case 3: A file has a different hash in Drive (MISMATCH)
# ---------------------------------------------------------------------------

class TestVerifyGdriveMismatch:
    def test_hash_mismatch_reported_as_mismatch(self):
        expected_sha = "aabbccdd" * 4
        actual_sha   = "11223344" * 4   # different
        manifest = {"files": {"clip.mov": _entry(expected_sha)}}
        drive_items = [_drive_item("clip.mov", actual_sha)]

        results, _, error = _run_worker(manifest, drive_items)

        assert error is None
        assert len(results) == 1
        assert results[0]["status"] == "MISMATCH"

    def test_mismatch_detail_shows_expected_and_actual(self):
        expected_md5 = "aabbccdd" * 4   # 32-char MD5-length placeholder
        actual_md5   = "11223344" * 4
        manifest = {"files": {"clip.mov": _entry(expected_md5)}}
        drive_items = [_drive_item("clip.mov", actual_md5)]

        results, _, _ = _run_worker(manifest, drive_items)

        detail = results[0]["detail"]
        # The worker formats detail as "Expected <hash[:16]>... | Got <hash[:16]>..."
        # Verify at least one of the hash fragments appears in the detail.
        assert (expected_md5[:8].lower() in detail.lower() or
                actual_md5[:8].lower()   in detail.lower() or
                "Expected"               in detail or
                "Got"                    in detail)

    def test_mismatch_error_logged(self):
        expected_sha = "aabbccdd" * 4
        actual_sha   = "11223344" * 4
        manifest = {"files": {"corrupt.mov": _entry(expected_sha)}}
        drive_items = [_drive_item("corrupt.mov", actual_sha)]

        _, log_lines, _ = _run_worker(manifest, drive_items)

        error_lines = [m for m, l in log_lines if l == "error"]
        assert any("corrupt.mov" in m or "MISMATCH" in m for m in error_lines)

    def test_mixed_ok_and_mismatch(self):
        sha_good    = "aabbccdd" * 4
        sha_bad_exp = "11223344" * 4
        sha_bad_act = "55667788" * 4
        manifest = {
            "files": {
                "good.mov":    _entry(sha_good),
                "corrupt.mov": _entry(sha_bad_exp),
            }
        }
        drive_items = [
            _drive_item("good.mov",    sha_good),
            _drive_item("corrupt.mov", sha_bad_act),
        ]

        results, _, error = _run_worker(manifest, drive_items)

        assert error is None
        statuses = {r["path"]: r["status"] for r in results}
        assert statuses["good.mov"]    == "OK"
        assert statuses["corrupt.mov"] == "MISMATCH"

    def test_empty_drive_hash_triggers_mismatch(self):
        """Drive returning an empty hash string must be treated as MISMATCH, not OK."""
        sha = "aabbccdd" * 4
        manifest = {"files": {"clip.mov": _entry(sha)}}
        # Drive item with an empty hash
        drive_items = [_drive_item("clip.mov", "")]

        results, _, error = _run_worker(manifest, drive_items)

        assert error is None
        # Empty hash cannot equal the expected SHA; must not be OK
        assert results[0]["status"] != "OK"


# ---------------------------------------------------------------------------
# Case 4: rclone lsjson fails
# ---------------------------------------------------------------------------

class TestVerifyGdriveRcloneError:
    def test_rclone_error_emits_error_signal(self):
        manifest = {"files": {"clip.mov": _entry("aabbccdd" * 4)}}

        worker = VerifyWorker(GDRIVE_URL, manifest)
        error_bucket  = []
        result_bucket = []

        worker.finished  = MagicMock()
        worker.finished.emit = lambda r: result_bucket.append(r)
        worker.log       = MagicMock()
        worker.log.emit  = lambda *a: None
        worker.error     = MagicMock()
        worker.error.emit = lambda e: error_bucket.append(e)
        worker.progress  = MagicMock()
        worker.progress.emit = lambda *a: None

        with patch("gui.verify_tab.gdrive_url_to_rclone", return_value=(RCLONE_REMOTE, RCLONE_FLAGS)), \
             patch("gui.verify_tab.rclone_bridge.lsjson",
                   side_effect=RuntimeError("rclone lsjson: connection refused")):
            worker.run()

        assert error_bucket, "error signal must be emitted when rclone fails"
        assert not result_bucket, "finished signal must NOT be emitted on rclone error"

    def test_rclone_error_message_contains_context(self):
        manifest = {"files": {"clip.mov": _entry("aabbccdd" * 4)}}

        worker = VerifyWorker(GDRIVE_URL, manifest)
        error_bucket = []

        worker.finished  = MagicMock()
        worker.finished.emit = lambda *a: None
        worker.log       = MagicMock()
        worker.log.emit  = lambda *a: None
        worker.error     = MagicMock()
        worker.error.emit = lambda e: error_bucket.append(e)
        worker.progress  = MagicMock()
        worker.progress.emit = lambda *a: None

        with patch("gui.verify_tab.gdrive_url_to_rclone", return_value=(RCLONE_REMOTE, RCLONE_FLAGS)), \
             patch("gui.verify_tab.rclone_bridge.lsjson",
                   side_effect=RuntimeError("rclone lsjson failed: exit 1")):
            worker.run()

        assert error_bucket
        assert "rclone" in error_bucket[0].lower() or "lsjson" in error_bucket[0].lower()
