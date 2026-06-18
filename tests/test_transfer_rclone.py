"""
Tests for core/transfer.py::transfer_folder_rclone.

All rclone_bridge calls are mocked — no rclone binary required.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from core.transfer import transfer_folder_rclone, TransferError

DRIVE_URL = "https://drive.google.com/drive/folders/abc123"
RCLONE_REMOTE = "gdrive:abc123"
RCLONE_FLAGS = ["--drive-root-folder-id", "abc123"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_entry(md5="aabbcc", size=1024):
    return {"size": size, "checksums": {"md5": md5}}


def _file_manifest(*names, md5="aabbcc", size=1024):
    return {"files": {n: _file_entry(md5, size) for n in names}, "errors": []}


def _is_drive(s):
    return "drive.google.com" in str(s)


def _to_rclone(_s):
    return RCLONE_REMOTE, RCLONE_FLAGS


def _any_warning(log_cb, substr):
    return any(
        len(c.args) >= 2 and c.args[1] == "warning" and substr in c.args[0]
        for c in log_cb.call_args_list
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def log_cb():
    return MagicMock()


@pytest.fixture
def rclone(monkeypatch):
    """Patch all rclone_bridge entry points; return the mock namespace."""
    import core.transfer as t

    m = MagicMock()
    m.is_rclone_installed.return_value = True
    m.lsjson.return_value = []
    m.sync.return_value = True
    m.lsjson_to_manifest.return_value = {"files": {}, "errors": []}
    m.save_manifest.return_value = []

    monkeypatch.setattr(t.rclone_bridge, "is_rclone_installed", m.is_rclone_installed)
    monkeypatch.setattr(t.rclone_bridge, "lsjson", m.lsjson)
    monkeypatch.setattr(t.rclone_bridge, "sync", m.sync)
    monkeypatch.setattr(t.rclone_bridge, "lsjson_to_manifest", m.lsjson_to_manifest)
    monkeypatch.setattr("core.transfer.save_manifest", m.save_manifest)
    monkeypatch.setattr("core.transfer.is_gdrive_url", _is_drive)
    monkeypatch.setattr("core.transfer.gdrive_url_to_rclone", _to_rclone)
    return m


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------

class TestGuardRails:
    def test_raises_when_rclone_not_installed(self, tmp_path, rclone, log_cb):
        rclone.is_rclone_installed.return_value = False
        with pytest.raises(TransferError, match="rclone is not installed"):
            transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)

    def test_drive_to_drive_no_longer_raises(self, rclone, log_cb, monkeypatch):
        # M3 removed the Drive-to-Drive guard; both-URL transfers now route
        # through connection-string remotes (see TestDriveToDrive below).
        monkeypatch.setattr("core.transfer.gdrive_url_to_connstr",
                            lambda u: "gdrive,root_folder_id=x:")
        other = "https://drive.google.com/drive/folders/xyz"
        result = transfer_folder_rclone(DRIVE_URL, other, log_cb=log_cb)
        assert "manifest" in result

    def test_raises_when_rclone_copy_fails(self, tmp_path, rclone, log_cb):
        rclone.sync.return_value = False
        with pytest.raises(TransferError, match="rclone copy failed"):
            transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)

    def test_raises_when_rclone_sync_fails(self, tmp_path, rclone, log_cb):
        rclone.sync.return_value = False
        with pytest.raises(TransferError, match="rclone sync failed"):
            transfer_folder_rclone(str(tmp_path), DRIVE_URL, mirror_mode=True, log_cb=log_cb)


# ---------------------------------------------------------------------------
# rclone mode routing
# ---------------------------------------------------------------------------

class TestModeRouting:
    def test_default_uses_copy_mode(self, tmp_path, rclone, log_cb):
        transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)
        assert rclone.sync.call_args.kwargs["mode"] == "copy"

    def test_mirror_mode_uses_sync(self, tmp_path, rclone, log_cb):
        transfer_folder_rclone(str(tmp_path), DRIVE_URL, mirror_mode=True, log_cb=log_cb)
        assert rclone.sync.call_args.kwargs["mode"] == "sync"

    def test_conflict_handler_forwarded_to_sync(self, tmp_path, rclone, log_cb):
        transfer_folder_rclone(str(tmp_path), DRIVE_URL, conflict_handler="skip", log_cb=log_cb)
        assert rclone.sync.call_args.kwargs["conflict"] == "skip"


# ---------------------------------------------------------------------------
# Return-value schema
# ---------------------------------------------------------------------------

class TestReturnSchema:
    def test_result_has_required_keys(self, tmp_path, rclone, log_cb):
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)
        for key in ("manifest", "errors", "actual_dest", "saved_manifest_paths"):
            assert key in result

    def test_manifest_has_schema_fields(self, tmp_path, rclone, log_cb):
        m = transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)["manifest"]
        for field in ("schema_version", "source_root", "dest_root", "operation",
                      "status_counts", "deleted_files", "verify_failures",
                      "checksum_context"):
            assert field in m, f"missing field: {field}"
        assert m["operation"] == "rclone-transfer"

    def test_local_to_drive_records_dest_url(self, tmp_path, rclone, log_cb):
        m = transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)["manifest"]
        assert m["dest_url"] == DRIVE_URL
        assert m["source_url"] == ""

    def test_drive_to_local_records_source_url(self, tmp_path, rclone, log_cb):
        m = transfer_folder_rclone(DRIVE_URL, str(tmp_path), log_cb=log_cb)["manifest"]
        assert m["source_url"] == DRIVE_URL
        assert m["dest_url"] == ""


# ---------------------------------------------------------------------------
# Progress callbacks
# ---------------------------------------------------------------------------

class TestProgressCallbacks:
    def test_progress_starts_at_zero_and_ends_at_100(self, tmp_path, rclone, log_cb):
        pcb = MagicMock()
        transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb, progress_cb=pcb)
        pcts = [c.args[0] for c in pcb.call_args_list]
        assert pcts[0] == 0
        assert pcts[-1] == 100

    def test_progress_hits_95_during_manifest_build(self, tmp_path, rclone, log_cb):
        pcb = MagicMock()
        transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb, progress_cb=pcb)
        pcts = [c.args[0] for c in pcb.call_args_list]
        assert 95 in pcts


# ---------------------------------------------------------------------------
# Status diff: uploaded / updated / unchanged / deleted
# ---------------------------------------------------------------------------

class TestStatusDiff:
    def _run(self, tmp_path, rclone, log_cb, pre_items, post_files):
        rclone.lsjson.return_value = pre_items
        rclone.lsjson_to_manifest.return_value = {"files": post_files, "errors": []}
        return transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)

    def test_new_file_status_is_uploaded(self, tmp_path, rclone, log_cb):
        result = self._run(tmp_path, rclone, log_cb,
            pre_items=[],
            post_files={"clip.mov": _file_entry()},
        )
        assert result["manifest"]["files"]["clip.mov"]["status"] == "uploaded"

    def test_unchanged_when_hash_and_size_match(self, tmp_path, rclone, log_cb):
        result = self._run(tmp_path, rclone, log_cb,
            pre_items=[{"Path": "clip.mov", "IsDir": False, "Size": 1024,
                        "Hashes": {"MD5": "AABBCC"}}],
            post_files={"clip.mov": _file_entry(md5="aabbcc", size=1024)},
        )
        assert result["manifest"]["files"]["clip.mov"]["status"] == "unchanged"

    def test_updated_when_hash_differs(self, tmp_path, rclone, log_cb):
        result = self._run(tmp_path, rclone, log_cb,
            pre_items=[{"Path": "clip.mov", "IsDir": False, "Size": 1024,
                        "Hashes": {"MD5": "OLD000"}}],
            post_files={"clip.mov": _file_entry(md5="aabbcc", size=1024)},
        )
        assert result["manifest"]["files"]["clip.mov"]["status"] == "updated"

    def test_deleted_files_tracked(self, tmp_path, rclone, log_cb):
        result = self._run(tmp_path, rclone, log_cb,
            pre_items=[{"Path": "gone.mov", "IsDir": False, "Size": 512, "Hashes": {}}],
            post_files={},
        )
        assert "gone.mov" in result["manifest"]["deleted_files"]

    def test_status_counts_aggregated(self, tmp_path, rclone, log_cb):
        result = self._run(tmp_path, rclone, log_cb,
            pre_items=[{"Path": "a.mov", "IsDir": False, "Size": 1024,
                        "Hashes": {"MD5": "AABBCC"}}],
            post_files={
                "a.mov": _file_entry(md5="aabbcc", size=1024),   # unchanged
                "b.mov": _file_entry(md5="001122", size=512),    # uploaded
            },
        )
        counts = result["manifest"]["status_counts"]
        assert counts["unchanged"] == 1
        assert counts["uploaded"] == 1

    def test_dir_entries_in_lsjson_are_ignored(self, tmp_path, rclone, log_cb):
        result = self._run(tmp_path, rclone, log_cb,
            pre_items=[{"Path": "subdir", "IsDir": True, "Size": 0, "Hashes": {}}],
            post_files={"clip.mov": _file_entry()},
        )
        # subdir should not appear in pre_state, so clip.mov should be uploaded
        assert result["manifest"]["files"]["clip.mov"]["status"] == "uploaded"


# ---------------------------------------------------------------------------
# Paranoid verification
# ---------------------------------------------------------------------------

class TestParanoidVerify:
    def _stub_hashes(self, monkeypatch, hashes):
        monkeypatch.setattr("core.transfer._compute_local_hashes",
                            lambda path, log_cb=None, use_md5=False: hashes)

    def test_local_to_drive_verified_on_md5_match(self, tmp_path, rclone, log_cb, monkeypatch):
        self._stub_hashes(monkeypatch, {"file.mov": "aabbcc"})
        rclone.lsjson_to_manifest.return_value = _file_manifest("file.mov", md5="aabbcc")
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL,
                                        paranoid_verify=True, log_cb=log_cb)
        f = result["manifest"]["files"]["file.mov"]
        assert f["verified"] is True
        assert f["verification_method"] == "paranoid"

    def test_local_to_drive_fails_on_md5_mismatch(self, tmp_path, rclone, log_cb, monkeypatch):
        self._stub_hashes(monkeypatch, {"file.mov": "aabbcc"})
        rclone.lsjson_to_manifest.return_value = _file_manifest("file.mov", md5="DIFFERENT")
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL,
                                        paranoid_verify=True, log_cb=log_cb)
        assert "file.mov" in result["manifest"]["verify_failures"]
        assert result["manifest"]["files"]["file.mov"]["verified"] is False

    def test_paranoid_fallback_when_drive_has_no_md5(self, tmp_path, rclone, log_cb, monkeypatch):
        self._stub_hashes(monkeypatch, {"file.mov": "aabbcc"})
        rclone.lsjson_to_manifest.return_value = {
            "files": {"file.mov": {"size": 512, "checksums": {}}},
            "errors": [],
        }
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL,
                                        paranoid_verify=True, log_cb=log_cb)
        f = result["manifest"]["files"]["file.mov"]
        assert f["verification_method"] == "rclone-checksum"
        assert "file.mov" in result["manifest"]["checksum_context"]["paranoid_fallback_files"]

    def test_drive_to_local_verified_when_xxh128_present(self, tmp_path, rclone, log_cb, monkeypatch):
        # Drive -> Local: verified = True when local xxh128 hash is present.
        # No cross-side comparison (Drive md5 vs local xxh128 are different algos).
        self._stub_hashes(monkeypatch, {"file.mov": "aabbcc"})
        rclone.lsjson_to_manifest.return_value = _file_manifest("file.mov", md5="aabbcc")
        result = transfer_folder_rclone(DRIVE_URL, str(tmp_path),
                                        paranoid_verify=True, log_cb=log_cb)
        f = result["manifest"]["files"]["file.mov"]
        assert f["verified"] is True
        assert f["dest_checksums"] == {"xxhash128": "aabbcc"}
        assert f["hash_algorithm"] == "xxhash128"

    def test_drive_to_local_unverified_when_hash_missing(self, tmp_path, rclone, log_cb, monkeypatch):
        # Drive -> Local: verified = False when local hash cannot be computed.
        self._stub_hashes(monkeypatch, {})  # empty — file.mov not hashed
        rclone.lsjson_to_manifest.return_value = _file_manifest("file.mov", md5="aabbcc")
        result = transfer_folder_rclone(DRIVE_URL, str(tmp_path),
                                        paranoid_verify=True, log_cb=log_cb)
        assert "file.mov" in result["manifest"]["verify_failures"]

    def test_checksum_context_paranoid_fields(self, tmp_path, rclone, log_cb, monkeypatch):
        self._stub_hashes(monkeypatch, {})
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL,
                                        paranoid_verify=True, log_cb=log_cb)
        ctx = result["manifest"]["checksum_context"]
        assert ctx["paranoid"] is True
        assert ctx["method"] == "paranoid"
        assert ctx["algorithm"] == "md5"

    def test_checksum_context_non_paranoid_fields(self, tmp_path, rclone, log_cb):
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)
        ctx = result["manifest"]["checksum_context"]
        assert ctx["paranoid"] is False
        assert ctx["method"] == "rclone"
        assert ctx["gdrive_mode"] is True


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_pre_state_failure_logs_warning_and_continues(self, tmp_path, rclone, log_cb):
        rclone.lsjson.side_effect = RuntimeError("network error")
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)
        assert _any_warning(log_cb, "Could not capture pre-sync state")
        assert "manifest" in result

    def test_manifest_generation_failure_logs_warning(self, tmp_path, rclone, log_cb):
        rclone.lsjson_to_manifest.side_effect = RuntimeError("lsjson failed")
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)
        assert _any_warning(log_cb, "Manifest generation warning")
        assert result["manifest"]["files"] == {}

    def test_save_manifest_failure_logs_warning(self, tmp_path, rclone, log_cb, monkeypatch):
        monkeypatch.setattr("core.transfer.save_manifest",
                            MagicMock(side_effect=RuntimeError("disk full")))
        result = transfer_folder_rclone(str(tmp_path), DRIVE_URL, log_cb=log_cb)
        assert _any_warning(log_cb, "Could not save JSON manifest")
        assert "manifest" in result


# ---------------------------------------------------------------------------
# M3: Drive-to-Drive transfers
# ---------------------------------------------------------------------------

SRC_URL = "https://drive.google.com/drive/folders/srcID111"
DST_URL = "https://drive.google.com/drive/folders/dstID222"


@pytest.fixture
def d2d(rclone, monkeypatch):
    """rclone fixture plus a deterministic connection-string builder."""
    monkeypatch.setattr(
        "core.transfer.gdrive_url_to_connstr",
        lambda u: f"gdrive,root_folder_id={u.rsplit('/', 1)[-1]}:",
    )
    return rclone


class TestDriveToDrive:
    def test_no_longer_guarded(self, d2d, log_cb):
        result = transfer_folder_rclone(SRC_URL, DST_URL, log_cb=log_cb)
        assert "manifest" in result

    def test_sync_called_with_connstr_both_sides(self, d2d, log_cb):
        transfer_folder_rclone(SRC_URL, DST_URL, log_cb=log_cb)
        args, kwargs = d2d.sync.call_args
        assert args[0] == "gdrive,root_folder_id=srcID111:"
        assert args[1] == "gdrive,root_folder_id=dstID222:"

    def test_server_side_flag_passed(self, d2d, log_cb):
        transfer_folder_rclone(SRC_URL, DST_URL, log_cb=log_cb)
        kwargs = d2d.sync.call_args[1]
        assert kwargs["src_flags"] == ["--drive-server-side-across-configs"]
        assert kwargs["dst_flags"] is None

    def test_announces_server_side_copy(self, d2d, log_cb):
        transfer_folder_rclone(SRC_URL, DST_URL, log_cb=log_cb)
        assert _any(log_cb, "no local disk")

    def test_paranoid_downgraded_with_warning(self, d2d, log_cb):
        result = transfer_folder_rclone(SRC_URL, DST_URL,
                                        paranoid_verify=True, log_cb=log_cb)
        assert _any_warning(log_cb, "Paranoid verify is unavailable")
        assert result["manifest"]["checksum_context"]["paranoid"] is False

    def test_manifest_saved_to_archive_only(self, d2d, log_cb):
        transfer_folder_rclone(SRC_URL, DST_URL, log_cb=log_cb)
        args, kwargs = d2d.save_manifest.call_args
        assert kwargs.get("name_hint") == "drive_to_drive"
        assert kwargs.get("source_dir") is None and kwargs.get("dest_dir") is None

    def test_manifest_urls_and_operation(self, d2d, log_cb):
        result = transfer_folder_rclone(SRC_URL, DST_URL, log_cb=log_cb)
        m = result["manifest"]
        assert m["source_url"] == SRC_URL
        assert m["dest_url"] == DST_URL
        assert m["operation"] == "rclone-transfer"
        assert m["checksum_context"]["gdrive_mode"] is True

    def test_sync_failure_raises(self, d2d, log_cb):
        d2d.sync.return_value = False
        with pytest.raises(TransferError, match="rclone copy failed"):
            transfer_folder_rclone(SRC_URL, DST_URL, log_cb=log_cb)

    def test_mirror_mode_uses_sync_command(self, d2d, log_cb):
        transfer_folder_rclone(SRC_URL, DST_URL, mirror_mode=True, log_cb=log_cb)
        assert d2d.sync.call_args[1]["mode"] == "sync"

    def test_route_transfer_dispatches_to_rclone(self, d2d, log_cb):
        from core.transfer import route_transfer
        result = route_transfer(SRC_URL, DST_URL, log_cb=log_cb)
        assert d2d.sync.called
        assert "manifest" in result


def _any(log_cb, substr):
    return any(substr in c.args[0] for c in log_cb.call_args_list if c.args)


class TestDriveToDrivePreflight:
    def test_750gb_limit_enforced_server_side(self, monkeypatch, log_cb):
        from core.transfer import pre_flight_checks, GDRIVE_DAILY_LIMIT_BYTES
        monkeypatch.setattr("core.transfer.is_gdrive_url",
                            lambda s: "drive.google.com" in str(s))
        monkeypatch.setattr("core.transfer.gdrive_url_to_rclone",
                            lambda s: ("gdrive:", []))
        monkeypatch.setattr("core.transfer.rclone_bridge.remote_size",
                            lambda *a, **k: (GDRIVE_DAILY_LIMIT_BYTES + 1, 10))
        with pytest.raises(TransferError, match="750 GB"):
            pre_flight_checks(SRC_URL, DST_URL, log_cb=log_cb)

    def test_under_limit_flags_server_side(self, monkeypatch, log_cb):
        from core.transfer import pre_flight_checks
        monkeypatch.setattr("core.transfer.is_gdrive_url",
                            lambda s: "drive.google.com" in str(s))
        monkeypatch.setattr("core.transfer.gdrive_url_to_rclone",
                            lambda s: ("gdrive:", []))
        monkeypatch.setattr("core.transfer.rclone_bridge.remote_size",
                            lambda *a, **k: (10 * 1024**3, 25))
        summary = pre_flight_checks(SRC_URL, DST_URL, log_cb=log_cb)
        assert summary["server_side"] is True
        assert summary["source_size"] == 10 * 1024**3
        assert _any(log_cb, "server-side copy")

    def test_no_local_freespace_check_for_url_dest(self, monkeypatch, log_cb):
        # A URL destination must not be mkdir'd or free-space checked
        from core.transfer import pre_flight_checks
        monkeypatch.setattr("core.transfer.is_gdrive_url",
                            lambda s: "drive.google.com" in str(s))
        monkeypatch.setattr("core.transfer.gdrive_url_to_rclone",
                            lambda s: ("gdrive:", []))
        monkeypatch.setattr("core.transfer.rclone_bridge.remote_size",
                            lambda *a, **k: (1024, 1))
        summary = pre_flight_checks(SRC_URL, DST_URL, log_cb=log_cb)
        assert "destination_free" not in summary
