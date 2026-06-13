"""Tests for M5.0 extracted verify logic (core/verify.py)."""

import hashlib
from pathlib import Path

import pytest

import core.verify as verify
from core.media_verify import MediaVerifyResult


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(files: dict) -> dict:
    """files: {rel: bytes} -> manifest with sha256 checksums block."""
    return {
        "files": {
            rel: {"size": len(data), "checksums": {"sha256": _sha256(data)}}
            for rel, data in files.items()
        }
    }


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "a.mov").write_bytes(b"alpha")
    (tmp_path / "clips" / "b.mov").write_bytes(b"bravo")
    return tmp_path


# ── verify_local ─────────────────────────────────────────────────────────────

def test_local_all_ok(folder):
    manifest = _manifest({"clips/a.mov": b"alpha", "clips/b.mov": b"bravo"})
    results = verify.verify_local(folder, manifest)
    assert {r["path"]: r["status"] for r in results} == {
        "clips/a.mov": "OK", "clips/b.mov": "OK",
    }


def test_local_missing(folder):
    manifest = _manifest({"clips/a.mov": b"alpha", "clips/gone.mov": b"x"})
    results = {r["path"]: r["status"] for r in verify.verify_local(folder, manifest)}
    assert results["clips/gone.mov"] == "MISSING"
    assert results["clips/a.mov"] == "OK"


def test_local_mismatch(folder):
    # Manifest claims different bytes for a.mov than what's on disk.
    manifest = _manifest({"clips/a.mov": b"DIFFERENT", "clips/b.mov": b"bravo"})
    results = {r["path"]: r["status"] for r in verify.verify_local(folder, manifest)}
    assert results["clips/a.mov"] == "MISMATCH"
    assert results["clips/b.mov"] == "OK"


def test_local_empty_expected_value_is_mismatch(folder):
    manifest = {"files": {"clips/a.mov": {"checksums": {"sha256": ""}}}}
    results = verify.verify_local(folder, manifest)
    assert results[0]["status"] == "MISMATCH"


def test_local_uses_dest_checksums_first(folder):
    # dest_checksums should win over checksums.
    good = _sha256(b"alpha")
    manifest = {"files": {"clips/a.mov": {
        "dest_checksums": {"sha256": good},
        "checksums": {"sha256": "deadbeef"},
    }}}
    results = verify.verify_local(folder, manifest)
    assert results[0]["status"] == "OK"


def test_local_progress_and_log_callbacks(folder):
    manifest = _manifest({"clips/a.mov": b"alpha"})
    progress, logs = [], []
    verify.verify_local(
        folder, manifest,
        progress_cb=lambda p, path: progress.append((p, path)),
        log_cb=lambda m, l: logs.append((m, l)),
    )
    assert progress[-1] == (100, "Complete")
    assert any(level == "success" for _, level in logs)


def test_local_format_fail_overrides_ok_status(folder, monkeypatch):
    manifest = _manifest({"clips/a.mov": b"alpha"})
    monkeypatch.setattr(
        verify._media_verify, "verify_file",
        lambda *a, **k: MediaVerifyResult(ok=False, detail="truncated", advisory=False),
    )
    r = verify.verify_local(folder, manifest)[0]
    assert r["status"] == "FORMAT_FAIL"
    assert r["format_status"] == "FAILED"
    assert r["format_detail"] == "truncated"


def test_local_format_advisory_keeps_ok(folder, monkeypatch):
    manifest = _manifest({"clips/a.mov": b"alpha"})
    monkeypatch.setattr(
        verify._media_verify, "verify_file",
        lambda *a, **k: MediaVerifyResult(ok=True, detail="no tool", advisory=True),
    )
    r = verify.verify_local(folder, manifest)[0]
    assert r["status"] == "OK"
    assert r["format_status"] == "ADVISORY"


def test_local_media_verify_exception_is_swallowed(folder, monkeypatch):
    manifest = _manifest({"clips/a.mov": b"alpha"})
    def boom(*a, **k):
        raise RuntimeError("ffprobe blew up")
    monkeypatch.setattr(verify._media_verify, "verify_file", boom)
    r = verify.verify_local(folder, manifest)[0]
    assert r["status"] == "OK"          # hash still authoritative
    assert "format_status" not in r     # media result discarded


# ── verify_gdrive (rclone mocked) ────────────────────────────────────────────

def _drive_items(mapping):
    """mapping: {path: sha256} -> rclone lsjson-style items."""
    return [
        {"Path": p, "IsDir": False, "Hashes": {"sha256": h}}
        for p, h in mapping.items()
    ]


def test_gdrive_ok_missing_mismatch(monkeypatch):
    manifest = _manifest({"a.mov": b"alpha", "b.mov": b"bravo", "c.mov": b"charlie"})
    # Drive: a matches, b differs, c absent.
    monkeypatch.setattr(verify, "is_gdrive_url", lambda s: True)
    monkeypatch.setattr(verify, "gdrive_url_to_rclone", lambda s: ("remote:path", []))
    monkeypatch.setattr(verify.rclone_bridge, "lsjson", lambda *a, **k: _drive_items({
        "a.mov": _sha256(b"alpha"),
        "b.mov": _sha256(b"WRONG"),
    }))
    results = {r["path"]: r["status"]
               for r in verify.verify_gdrive("https://drive...", manifest)}
    assert results == {"a.mov": "OK", "b.mov": "MISMATCH", "c.mov": "MISSING"}


def test_gdrive_no_common_hash_is_mismatch(monkeypatch):
    manifest = {"files": {"a.mov": {"checksums": {"xxhash3_64": "abcd"}}}}
    monkeypatch.setattr(verify, "gdrive_url_to_rclone", lambda s: ("remote:path", []))
    monkeypatch.setattr(verify.rclone_bridge, "lsjson",
                        lambda *a, **k: _drive_items({"a.mov": _sha256(b"x")}))
    r = verify.verify_gdrive("https://drive...", manifest)[0]
    assert r["status"] == "MISMATCH"
    assert "common hash" in r["detail"]


def test_gdrive_lsjson_failure_raises(monkeypatch):
    manifest = _manifest({"a.mov": b"alpha"})
    monkeypatch.setattr(verify, "gdrive_url_to_rclone", lambda s: ("remote:path", []))
    def boom(*a, **k):
        raise OSError("rclone not found")
    monkeypatch.setattr(verify.rclone_bridge, "lsjson", boom)
    with pytest.raises(RuntimeError, match="rclone lsjson failed"):
        verify.verify_gdrive("https://drive...", manifest)


def test_gdrive_extras_logged(monkeypatch):
    manifest = _manifest({"a.mov": b"alpha"})
    monkeypatch.setattr(verify, "gdrive_url_to_rclone", lambda s: ("remote:path", []))
    monkeypatch.setattr(verify.rclone_bridge, "lsjson", lambda *a, **k: _drive_items({
        "a.mov": _sha256(b"alpha"),
        "extra.mov": _sha256(b"z"),
        "st_manifest.json": "ignored",
    }))
    logs = []
    verify.verify_gdrive("https://drive...", manifest, log_cb=lambda m, l: logs.append((m, l)))
    # extra.mov counts; st_manifest.json is filtered out.
    assert any("1 file(s) present on Drive but not in manifest" in m for m, _ in logs)


# ── verify_folder dispatcher ─────────────────────────────────────────────────

def test_dispatch_local(folder, monkeypatch):
    monkeypatch.setattr(verify, "is_gdrive_url", lambda s: False)
    called = {}
    monkeypatch.setattr(verify, "verify_local",
                        lambda *a, **k: called.setdefault("local", True) or [])
    verify.verify_folder(folder, _manifest({"clips/a.mov": b"alpha"}))
    assert called.get("local")


def test_dispatch_gdrive(monkeypatch):
    monkeypatch.setattr(verify, "is_gdrive_url", lambda s: True)
    called = {}
    monkeypatch.setattr(verify, "verify_gdrive",
                        lambda *a, **k: called.setdefault("gdrive", True) or [])
    verify.verify_folder("https://drive...", {"files": {}})
    assert called.get("gdrive")


def test_dispatch_gdrive_deep(monkeypatch):
    monkeypatch.setattr(verify, "is_gdrive_url", lambda s: True)
    called = {}
    monkeypatch.setattr(verify, "verify_gdrive_deep",
                        lambda *a, **k: called.setdefault("deep", True) or [])
    verify.verify_folder("https://drive...", {"files": {}}, deep=True)
    assert called.get("deep")


def test_dispatch_deep_ignored_for_local(folder, monkeypatch):
    # deep=True must not trigger a download path for local folders.
    monkeypatch.setattr(verify, "is_gdrive_url", lambda s: False)
    called = {}
    monkeypatch.setattr(verify, "verify_local",
                        lambda *a, **k: called.setdefault("local", True) or [])
    verify.verify_folder(folder, _manifest({"clips/a.mov": b"alpha"}), deep=True)
    assert called.get("local")


# ── M5.1 deep Drive verify ───────────────────────────────────────────────────

def test_estimate_deep_verify_seconds():
    # 100 Mbps = 12.5 MB/s. 125 MB should take ~10s.
    secs = verify.estimate_deep_verify_seconds(125_000_000, mbps=100)
    assert 9 < secs < 11
    assert verify.estimate_deep_verify_seconds(0) == 0.0


def test_join_remote():
    assert verify._join_remote("gdrive:", "a/b.mov") == "gdrive:a/b.mov"
    assert verify._join_remote("gdrive:folder", "a.mov") == "gdrive:folder/a.mov"
    assert verify._join_remote("gdrive:folder/", "a.mov") == "gdrive:folder/a.mov"


def _deep_setup(monkeypatch, cat_map):
    """cat_map: {remote_path: sha256 or Exception}. Returns a cat_fn."""
    monkeypatch.setattr(verify, "gdrive_url_to_rclone", lambda s: ("gdrive:", []))
    def cat_fn(remote_path, extra_flags=None):
        val = cat_map[remote_path]
        if isinstance(val, Exception):
            raise val
        return val
    return cat_fn


def test_deep_ok_and_mismatch(monkeypatch):
    manifest = _manifest({"a.mov": b"alpha", "b.mov": b"bravo"})
    cat_fn = _deep_setup(monkeypatch, {
        "gdrive:a.mov": _sha256(b"alpha"),       # matches
        "gdrive:b.mov": _sha256(b"WRONG"),       # mismatch
    })
    results = {r["path"]: r["status"] for r in
               verify.verify_gdrive_deep("https://drive...", manifest, cat_fn=cat_fn)}
    assert results == {"a.mov": "OK", "b.mov": "MISMATCH"}


def test_deep_missing_on_cat_error(monkeypatch):
    manifest = _manifest({"a.mov": b"alpha"})
    cat_fn = _deep_setup(monkeypatch, {"gdrive:a.mov": RuntimeError("not found")})
    r = verify.verify_gdrive_deep("https://drive...", manifest, cat_fn=cat_fn)[0]
    assert r["status"] == "MISSING"
    assert "not found" in r["detail"]


def test_deep_no_sha256_is_mismatch(monkeypatch):
    manifest = {"files": {"a.mov": {"size": 5, "checksums": {"md5": "abc"}}}}
    cat_fn = _deep_setup(monkeypatch, {})  # cat never called
    r = verify.verify_gdrive_deep("https://drive...", manifest, cat_fn=cat_fn)[0]
    assert r["status"] == "MISMATCH"
    assert "No sha256" in r["detail"]


def test_deep_logs_estimate(monkeypatch):
    manifest = _manifest({"a.mov": b"alpha"})
    cat_fn = _deep_setup(monkeypatch, {"gdrive:a.mov": _sha256(b"alpha")})
    logs = []
    verify.verify_gdrive_deep("https://drive...", manifest, cat_fn=cat_fn,
                              log_cb=lambda m, l: logs.append(m))
    assert any("Deep verify will download" in m for m in logs)


def test_deep_progress_completes(monkeypatch):
    manifest = _manifest({"a.mov": b"alpha"})
    cat_fn = _deep_setup(monkeypatch, {"gdrive:a.mov": _sha256(b"alpha")})
    prog = []
    verify.verify_gdrive_deep("https://drive...", manifest, cat_fn=cat_fn,
                              progress_cb=lambda p, path: prog.append((p, path)))
    assert prog[-1] == (100, "Complete")
