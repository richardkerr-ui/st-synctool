"""Tests for M15.2 — rclone version pinning + backend checksum capability."""

import pytest

from core import rclone_bridge as rb


# ── version helpers ───────────────────────────────────────────────────────────

def test_required_version_is_pinned_string():
    assert isinstance(rb.RCLONE_REQUIRED_VERSION, str)
    assert rb._version_tuple(rb.RCLONE_REQUIRED_VERSION) is not None


def test_version_tuple_parses_and_rejects():
    assert rb._version_tuple("1.74.3") == (1, 74, 3)
    assert rb._version_tuple("rclone v1.66.0") == (1, 66, 0)
    assert rb._version_tuple("garbage") is None
    assert rb._version_tuple(None) is None


def test_meets_required_version_floor():
    pin = rb.RCLONE_REQUIRED_VERSION
    assert rb.meets_required_version(pin) is True            # exact match
    assert rb.meets_required_version("99.0.0") is True       # newer
    assert rb.meets_required_version("1.0.0") is False       # older
    assert rb.meets_required_version(None) is False          # unparseable


def test_rclone_version_parses_subprocess_output(monkeypatch):
    class _R:
        stdout = "rclone v1.74.3\n- os/version: darwin\n"
    monkeypatch.setattr(rb.subprocess, "run", lambda *a, **k: _R())
    assert rb.rclone_version() == "1.74.3"


def test_rclone_version_none_when_absent(monkeypatch):
    def boom(*a, **k):
        raise OSError("no rclone")
    monkeypatch.setattr(rb.subprocess, "run", boom)
    assert rb.rclone_version() is None


# ── backend checksum capability ───────────────────────────────────────────────

def test_drive_url_supports_checksum():
    assert rb.backend_supports_checksum(
        "https://drive.google.com/drive/folders/abc") is True


def test_drive_connstring_supports_checksum():
    assert rb.backend_supports_checksum("gdrive,root_folder_id=abc:") is True


def test_local_path_supports_checksum(tmp_path):
    assert rb.backend_supports_checksum(str(tmp_path)) is True


def test_unknown_remote_not_confirmed():
    # An arbitrary named remote (e.g. an SMB/NAS remote) is not confirmed.
    assert rb.backend_supports_checksum("nas:share/footage") is False


# ── preflight enforcement ─────────────────────────────────────────────────────

def test_preflight_pin_refuses_old_version(monkeypatch):
    from core import preflight
    monkeypatch.setattr(rb, "rclone_version", lambda: "1.0.0")
    with pytest.raises(SystemExit):
        preflight.check_rclone_pinned_version()


def test_preflight_pin_passes_current_version(monkeypatch):
    from core import preflight
    monkeypatch.setattr(rb, "rclone_version", lambda: rb.RCLONE_REQUIRED_VERSION)
    preflight.check_rclone_pinned_version()   # must not raise


def test_preflight_pin_skips_when_rclone_absent(monkeypatch):
    # rclone missing entirely is handled by check_rclone; the pin check no-ops.
    from core import preflight
    monkeypatch.setattr(rb, "rclone_version", lambda: None)
    preflight.check_rclone_pinned_version()   # must not raise
