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


def _md5(data: bytes) -> str:
    import hashlib
    return hashlib.md5(data).hexdigest()


def test_deep_md5_fallback_ok(monkeypatch):
    """MD5-only manifest entry (Drive-origin) should verify OK via cat_md5."""
    data = b"frame"
    manifest = {"files": {"a.mov": {"size": len(data), "checksums": {"md5": _md5(data)}}}}
    cat_fn = _deep_setup(monkeypatch, {})   # sha256 path never called
    md5_fn = lambda remote_path, extra_flags=None: _md5(data)
    r = verify.verify_gdrive_deep("https://drive...", manifest, cat_fn=cat_fn,
                                  cat_md5_fn=md5_fn)[0]
    assert r["status"] == "OK"
    assert "md5" in r["detail"]


def test_deep_no_hash_is_mismatch(monkeypatch):
    """Entry with no sha256 or md5 should be MISMATCH, not a silent skip."""
    manifest = {"files": {"a.mov": {"size": 5, "checksums": {}}}}
    cat_fn = _deep_setup(monkeypatch, {})
    r = verify.verify_gdrive_deep("https://drive...", manifest, cat_fn=cat_fn)[0]
    assert r["status"] == "MISMATCH"
    assert "No sha256 or md5" in r["detail"]


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


# ── M5.2 batch verify ────────────────────────────────────────────────────────

def test_summarize_results_counts():
    results = [
        {"path": "a", "status": "OK"},
        {"path": "b", "status": "MISSING"},
        {"path": "c", "status": "MISMATCH"},
        {"path": "d", "status": "FORMAT_FAIL"},
        {"path": "e", "status": "OK"},
    ]
    s = verify.summarize_results("Proj", "/tmp/p", results)
    assert (s.total, s.ok, s.missing, s.mismatch, s.format_fail) == (5, 2, 1, 1, 1)
    assert s.passed is False
    assert s.verdict == "FAIL"


def test_summary_passed_when_all_ok():
    s = verify.summarize_results("P", "/f", [{"path": "a", "status": "OK"}])
    assert s.passed is True
    assert s.verdict == "OK"


def test_batch_verify_mixed(monkeypatch):
    # Project results scripted via an injected verify_fn keyed on folder.
    scripted = {
        "/good": [{"path": "a", "status": "OK"}, {"path": "b", "status": "OK"}],
        "/bad":  [{"path": "a", "status": "OK"}, {"path": "b", "status": "MISMATCH"}],
    }
    def vfn(folder, manifest, log_cb=None, deep=False):
        if folder == "/boom":
            raise RuntimeError("folder gone")
        return scripted[folder]
    pairs = [
        {"label": "Good", "folder": "/good", "manifest": {"files": {}}},
        {"label": "Bad", "folder": "/bad", "manifest": {"files": {}}},
        {"label": "Boom", "folder": "/boom", "manifest": {"files": {}}},
    ]
    summaries = verify.batch_verify(pairs, verify_fn=vfn)
    by_label = {s.label: s for s in summaries}
    assert by_label["Good"].verdict == "OK"
    assert by_label["Bad"].verdict == "FAIL"
    assert by_label["Boom"].verdict == "ERROR"
    assert "folder gone" in by_label["Boom"].error


def test_batch_verify_progress_completes():
    pairs = [{"label": "P", "folder": "/f", "manifest": {"files": {}}}]
    prog = []
    verify.batch_verify(pairs, verify_fn=lambda *a, **k: [],
                        progress_cb=lambda p, label: prog.append((p, label)))
    assert prog[-1] == (100, "Complete")


def test_batch_verify_passes_deep_flag():
    seen = {}
    def vfn(folder, manifest, log_cb=None, deep=False):
        seen["deep"] = deep
        return []
    verify.batch_verify([{"label": "P", "folder": "/f", "manifest": {}}],
                        verify_fn=vfn, deep=True)
    assert seen["deep"] is True


def test_pairs_from_registry(monkeypatch, tmp_path):
    mpath = tmp_path / "m.json"
    mpath.write_text("{}")
    monkeypatch.setattr("core.manifest.load_manifest", lambda p: {"files": {"a": {}}})
    projects = [
        {"display_name": "Has manifest", "local_path": "/f1", "latest_manifest": str(mpath)},
        {"display_name": "No manifest", "local_path": "/f2", "latest_manifest": ""},
        {"display_name": "Missing file", "local_path": "/f3",
         "latest_manifest": str(tmp_path / "nope.json")},
        {"display_name": "No folder", "local_path": "", "latest_manifest": str(mpath)},
    ]
    pairs, skipped = verify.pairs_from_registry(projects=projects)
    assert len(pairs) == 1 and pairs[0]["label"] == "Has manifest"
    skip_labels = {label: reason for label, reason in skipped}
    assert "No manifest" in skip_labels and "Missing file" in skip_labels
    assert "No folder" in skip_labels


def test_pairs_from_registry_manifest_load_failure(monkeypatch, tmp_path):
    mpath = tmp_path / "m.json"
    mpath.write_text("garbage")
    def boom(p):
        raise ValueError("bad json")
    monkeypatch.setattr("core.manifest.load_manifest", boom)
    projects = [{"display_name": "Broken", "local_path": "/f", "latest_manifest": str(mpath)}]
    pairs, skipped = verify.pairs_from_registry(projects=projects)
    assert pairs == []
    assert "manifest load failed" in skipped[0][1]


def test_format_batch_report():
    summaries = [
        verify.summarize_results("Good", "/g", [{"path": "a", "status": "OK"}]),
        verify.summarize_results("Bad", "/b", [{"path": "a", "status": "MISMATCH"}]),
        verify.ProjectVerifySummary("Err", "/e", 0, 0, 0, 0, 0, error="no access"),
    ]
    report = verify.format_batch_report(summaries, skipped=[("Skip", "no manifest")])
    assert "Projects verified: 3" in report
    assert "OK: 1" in report and "FAIL: 1" in report and "ERROR: 1" in report
    assert "[OK] Good" in report and "[FAIL] Bad" in report and "[ERROR] Err" in report
    assert "no access" in report
    assert "Skip: no manifest" in report


# ── M5.4: persist format-verification results ─────────────────────────────────

import json
from datetime import datetime, timezone


def _results_with_format():
    return [
        {"path": "clips/a.braw", "status": "OK", "detail": "sha256: abc...",
         "format_status": "OK", "format_detail": "BRAW structure OK"},
        {"path": "clips/b.braw", "status": "FORMAT_FAIL", "detail": "sha256: def...",
         "format_status": "FAILED", "format_detail": "truncated stream"},
        {"path": "docs/notes.txt", "status": "OK", "detail": "sha256: 123..."},  # no format check
    ]


def test_media_verify_block_only_for_format_results():
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    res = _results_with_format()
    assert verify.media_verify_block(res[0], now=now)["status"] == "OK"
    assert verify.media_verify_block(res[1], now=now)["status"] == "FAILED"
    assert verify.media_verify_block(res[1], now=now)["detail"] == "truncated stream"
    assert verify.media_verify_block(res[2], now=now) is None  # no format_status


def test_persist_media_verify_to_manifest_roundtrip(tmp_path):
    mpath = tmp_path / "st_manifest.json"
    manifest = {
        "schema_version": "1.2",
        "files": {
            "clips/a.braw": {"size": 1, "checksums": {"sha256": "x"}},
            "clips/b.braw": {"size": 2, "checksums": {"sha256": "y"}},
            "docs/notes.txt": {"size": 3, "checksums": {"sha256": "z"}},
        },
    }
    mpath.write_text(json.dumps(manifest))
    now = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)

    returned = verify.persist_media_verify_to_manifest(mpath, _results_with_format(), now=now)

    # Reload from disk to prove it round-trips.
    reloaded = json.loads(mpath.read_text())
    a = reloaded["files"]["clips/a.braw"]["media_verify"]
    b = reloaded["files"]["clips/b.braw"]["media_verify"]
    assert a["status"] == "OK" and b["status"] == "FAILED"
    assert a["verified_at"] == now.isoformat()
    # Non-media entry untouched; original checksum data preserved.
    assert "media_verify" not in reloaded["files"]["docs/notes.txt"]
    assert reloaded["files"]["clips/a.braw"]["checksums"] == {"sha256": "x"}
    assert returned == reloaded
    assert not mpath.with_suffix(".json.tmp").exists()


def test_persist_skips_paths_absent_from_manifest(tmp_path):
    mpath = tmp_path / "st_manifest.json"
    mpath.write_text(json.dumps({"files": {"clips/a.braw": {"size": 1}}}))
    # b.braw not in manifest -> skipped silently, no crash.
    verify.persist_media_verify_to_manifest(mpath, _results_with_format())
    reloaded = json.loads(mpath.read_text())
    assert "media_verify" in reloaded["files"]["clips/a.braw"]
    assert "clips/b.braw" not in reloaded["files"]


def test_persist_missing_manifest_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify.persist_media_verify_to_manifest(tmp_path / "nope.json", [])


def test_build_verify_report_shape():
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    report = verify.build_verify_report(
        "/Volumes/A001", _results_with_format(), label="A001", deep=False, now=now)
    assert report["schema"] == "verify_report"
    assert report["folder"] == "/Volumes/A001"
    assert report["label"] == "A001"
    assert report["generated_at"] == now.isoformat()
    assert report["summary"] == {"total": 3, "ok": 2, "missing": 0,
                                 "mismatch": 0, "format_fail": 1}
    assert report["verdict"] == "FAIL"   # one FORMAT_FAIL
    # Per-file format evidence preserved verbatim.
    assert report["files"][0]["format_status"] == "OK"
    assert report["files"][1]["format_detail"] == "truncated stream"


def test_write_verify_report_roundtrip(tmp_path):
    now = datetime(2026, 6, 12, 14, 30, 0)
    path = verify.write_verify_report(
        "/Volumes/A001", _results_with_format(), label="A 001",
        log_dir=tmp_path, now=now)
    assert path.exists()
    assert path.parent == tmp_path
    assert "A_001" in path.name          # label sanitised for filename
    assert path.name.endswith("20260612_143000.json")
    reloaded = json.loads(path.read_text())
    assert reloaded["schema"] == "verify_report"
    assert reloaded["summary"]["format_fail"] == 1
    assert not path.with_suffix(".json.tmp").exists()


def test_write_verify_report_no_label(tmp_path):
    now = datetime(2026, 6, 12, 14, 30, 0)
    path = verify.write_verify_report("/x", [], label="", log_dir=tmp_path, now=now)
    assert path.name == "verify_report_20260612_143000.json"


# --------------------------------------------------------------------------- #
# M9.2: verify_folder records a 'verify' activity line
# --------------------------------------------------------------------------- #

def test_verify_folder_logs_activity(tmp_path, monkeypatch):
    import hashlib
    from core import verify as v
    from core import activity_index as ai
    monkeypatch.setattr(ai, "ACTIVITY_DIR", tmp_path / "activity")

    folder = tmp_path / "A001"; folder.mkdir()
    data = b"clip-bytes"
    (folder / "clip.mov").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "label": "ProjX", "workstation": "Cart 1", "user": "dit",
        "file_count": 1, "total_size_bytes": len(data),
        "files": {"clip.mov": {"type": "file", "size": len(data),
                               "checksums": {"sha256": sha}}},
    }
    results = v.verify_folder(folder, manifest)
    assert all(r["status"] == "OK" for r in results)

    shards = ai.find_shards(tmp_path / "activity")
    recs = ai.merge_shards(shards)
    assert any(r["operation"] == "verify" and r["verdict"] == "OK"
               and r["project"] == "ProjX" for r in recs)
