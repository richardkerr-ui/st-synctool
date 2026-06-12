"""Layer 1 (M1.4): writer x reader manifest contract matrix.

Writers are the three real pipeline functions, run end-to-end on tmp files:
  - run_offload            (manifest loaded from the committed st_manifest.json)
  - transfer_folder        (manifest loaded from the persisted st_manifest.json)
  - transfer_folder_rclone (rclone faked at the lsjson layer with real hashes,
                            so the real lsjson_to_manifest code path runs)

Readers:
  - three_way_diff
  - VerifyWorker._verify_local's checksum contract. The worker itself lives in
    gui/verify_tab.py (PyQt6) so the hash-verification loop is mirrored here
    headlessly, same approach as test_manifest_schema_contract.py. The real
    worker is exercised by test_drive_verify.py on macOS.
  - write_chain_of_custody_log

Every writer x reader pair must pass, and a deliberately broken-schema
manifest must fail loudly in every reader.
"""

import hashlib
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.checksum import compute_all
from core.comparison import three_way_diff, DiffState
from core.manifest import load_manifest, MANIFEST_FILENAME
from core.offload import (
    CellResult, CellState, OffloadConfig, OffloadDest, OffloadSource,
    run_offload, write_chain_of_custody_log,
)
from core.transfer import transfer_folder, transfer_folder_rclone
import core.rclone_bridge as rb

FILES = {
    "clips/a.mov": b"alpha-payload-bytes",
    "audio/b.wav": b"bravo-payload-bytes",
}
DRIVE_URL = "https://drive.google.com/drive/folders/abc123"


def _make_source(tmp_path: Path, name: str) -> Path:
    src = tmp_path / name
    for rel, data in FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return src


def _isolate_dirs(monkeypatch, tmp_path):
    """Keep manifest archive and custody logs out of the real home directory."""
    monkeypatch.setattr("core.manifest.LOCAL_MANIFEST_DIR", tmp_path / "archive")
    monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "coc_logs")


# ---------------------------------------------------------------------------
# Writer fixtures — each returns the persisted manifest, the local folder the
# files actually live in (for hash verification) and a flat {rel: info} dict
# for the chain-of-custody reader.
# ---------------------------------------------------------------------------

@pytest.fixture
def offload_artifacts(tmp_path, monkeypatch):
    _isolate_dirs(monkeypatch, tmp_path)
    src = _make_source(tmp_path, "A001")
    dest = OffloadDest(label="NAS", path=tmp_path / "nas")
    dest.path.mkdir()

    results, source_manifests, _ = run_offload(
        [OffloadSource(label="A001", path=src)], [dest],
        OffloadConfig(), MagicMock(), MagicMock(),
    )
    assert all(r.state == CellState.DONE for r in results)

    final = dest.path / "A001"
    manifest = load_manifest(final / MANIFEST_FILENAME)
    return {
        "manifest": manifest,
        "verify_folder": final,
        "flat_files": source_manifests["A001"],
    }


@pytest.fixture
def transfer_artifacts(tmp_path, monkeypatch):
    _isolate_dirs(monkeypatch, tmp_path)
    src = _make_source(tmp_path, "ProjectX")
    dst = tmp_path / "dst"
    dst.mkdir()

    result = transfer_folder(src, dst)
    assert not result["errors"]

    actual_dest = Path(result["actual_dest"])
    manifest = load_manifest(actual_dest / MANIFEST_FILENAME)
    return {
        "manifest": manifest,
        "verify_folder": actual_dest,
        "flat_files": manifest["files"],
    }


@pytest.fixture
def rclone_artifacts(tmp_path, monkeypatch):
    _isolate_dirs(monkeypatch, tmp_path)
    src = _make_source(tmp_path, "LocalSrc")

    # Fake the rclone binary at the lsjson layer with the REAL hashes of the
    # fixture files, then let the real lsjson_to_manifest build the manifest.
    items = []
    for i, (rel, data) in enumerate(FILES.items()):
        items.append({
            "Path": rel,
            "Size": len(data),
            "ModTime": "2026-06-12T00:00:00Z",
            "ID": f"driveid{i}",
            "Hashes": {"SHA256": hashlib.sha256(data).hexdigest()},
        })
    with patch.object(rb, "_run") as fake_run:
        fake_run.return_value = type(
            "R", (), {"returncode": 0, "stdout": json.dumps(items), "stderr": ""}
        )()
        real_manifest = rb.lsjson_to_manifest("gdrive:", label="rclone-copy")

    import core.transfer as t
    monkeypatch.setattr(t.rclone_bridge, "is_rclone_installed", lambda: True)
    monkeypatch.setattr(t.rclone_bridge, "lsjson", lambda *a, **k: items)
    monkeypatch.setattr(t.rclone_bridge, "sync", lambda *a, **k: True)
    monkeypatch.setattr(
        t.rclone_bridge, "lsjson_to_manifest", lambda *a, **k: real_manifest
    )
    monkeypatch.setattr("core.transfer.is_gdrive_url",
                        lambda s: "drive.google.com" in str(s))
    monkeypatch.setattr("core.transfer.gdrive_url_to_rclone",
                        lambda s: ("gdrive:", ["--drive-root-folder-id", "abc123"]))

    result = transfer_folder_rclone(src, DRIVE_URL)
    return {
        "manifest": result["manifest"],
        "verify_folder": src,           # local side holds the identical bytes
        "flat_files": result["manifest"]["files"],
    }


WRITERS = ["offload_artifacts", "transfer_artifacts", "rclone_artifacts"]


# ---------------------------------------------------------------------------
# Reader 1: three_way_diff
# ---------------------------------------------------------------------------

class TestReaderThreeWayDiff:
    @pytest.mark.parametrize("writer", WRITERS)
    def test_manifest_consumed_all_unchanged(self, request, writer):
        art = request.getfixturevalue(writer)
        m = art["manifest"]
        rows = three_way_diff(m, m, m)
        assert {r.path for r in rows} == set(FILES)
        assert all(r.state == DiffState.UNCHANGED for r in rows), (
            f"{writer}: {[(r.path, r.state) for r in rows]}"
        )


# ---------------------------------------------------------------------------
# Reader 2: VerifyWorker._verify_local checksum contract (mirrored headlessly)
# ---------------------------------------------------------------------------

def _verify_local_contract(manifest: dict, folder: Path) -> list:
    """Mirror of gui/verify_tab.py VerifyWorker._verify_local's core loop."""
    statuses = []
    for rel_path, entry in manifest["files"].items():
        abs_path = folder / rel_path
        if not abs_path.exists():
            statuses.append((rel_path, "MISSING"))
            continue
        expected_cs = (entry.get("dest_checksums")
                       or entry.get("source_checksums")
                       or entry.get("checksums", {}))
        algo = ("sha256" if "sha256" in expected_cs else
                "xxhash3_64" if "xxhash3_64" in expected_cs else "md5")
        actual = compute_all(
            abs_path,
            include_xxhash=(algo == "xxhash3_64"),
            include_md5=(algo == "md5"),
        )
        expected_val = (expected_cs.get(algo) or "").lower()
        actual_val = (actual.get(algo) or "").lower()
        ok = expected_val == actual_val and bool(expected_val)
        statuses.append((rel_path, "OK" if ok else "MISMATCH"))
    return statuses


class TestReaderVerifyLocal:
    @pytest.mark.parametrize("writer", WRITERS)
    def test_every_file_verifies_ok(self, request, writer):
        art = request.getfixturevalue(writer)
        statuses = _verify_local_contract(art["manifest"], art["verify_folder"])
        assert len(statuses) == len(FILES)
        assert all(s == "OK" for _, s in statuses), f"{writer}: {statuses}"

    @pytest.mark.parametrize("writer", WRITERS)
    def test_corrupted_file_detected_as_mismatch(self, request, writer, tmp_path):
        art = request.getfixturevalue(writer)
        folder = art["verify_folder"]
        victim = folder / "clips/a.mov"
        original = victim.read_bytes()
        try:
            victim.write_bytes(b"corrupted!")
            statuses = dict(_verify_local_contract(art["manifest"], folder))
            assert statuses["clips/a.mov"] == "MISMATCH"
        finally:
            victim.write_bytes(original)


# ---------------------------------------------------------------------------
# Reader 3: write_chain_of_custody_log
# ---------------------------------------------------------------------------

class TestReaderChainOfCustody:
    def _write(self, flat_files, tmp_path, monkeypatch):
        monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "coc")
        src = OffloadSource(label="SRC", path=tmp_path / "card")
        dst = OffloadDest(label="DST", path=tmp_path / "nas")
        result = CellResult(source_label="SRC", dest_label="DST",
                            state=CellState.DONE,
                            files_copied=len(FILES),
                            bytes_copied=sum(len(d) for d in FILES.values()))
        return write_chain_of_custody_log(
            [src], [dst], [result], {"SRC": flat_files}, "20260612_000000",
        )

    @pytest.mark.parametrize("writer", WRITERS)
    def test_log_written_with_all_files_accounted(
        self, request, writer, tmp_path, monkeypatch
    ):
        art = request.getfixturevalue(writer)
        log_path = self._write(art["flat_files"], tmp_path, monkeypatch)
        text = log_path.read_text()
        assert "OVERALL RESULT: COMPLETE" in text
        assert f"Files:     {len(FILES)}" in text
        for rel in FILES:
            assert rel in text

    def test_full_schema_manifest_tolerated_without_crash(
        self, tmp_path, monkeypatch, transfer_artifacts
    ):
        # Regression for the OVERNIGHT-FIX: a full manifest dict (with meta keys
        # like schema_version) passed as a source manifest must not raise; meta
        # keys are filtered out by the size-bearing-entry guard.
        log_path = self._write(transfer_artifacts["manifest"], tmp_path, monkeypatch)
        assert "OVERALL RESULT: COMPLETE" in log_path.read_text()


# ---------------------------------------------------------------------------
# Broken-schema fixture — every reader must fail loudly, never silently
# ---------------------------------------------------------------------------

BROKEN_MANIFEST = {
    "schema_version": "9.9",
    "files": ["this", "is", "not", "a", "mapping"],
}

BROKEN_FLAT = {
    "clip.mov": {"size": "not-an-int", "checksum": 12345},
}


class TestBrokenSchemaFailsLoudly:
    def test_three_way_diff_raises(self):
        with pytest.raises((AttributeError, TypeError, KeyError)):
            three_way_diff(BROKEN_MANIFEST, BROKEN_MANIFEST, BROKEN_MANIFEST)

    def test_verify_local_contract_raises(self, tmp_path):
        with pytest.raises((AttributeError, TypeError, KeyError)):
            _verify_local_contract(BROKEN_MANIFEST, tmp_path)

    def test_chain_of_custody_raises_on_garbage_sizes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "coc")
        src = OffloadSource(label="SRC", path=tmp_path)
        dst = OffloadDest(label="DST", path=tmp_path)
        result = CellResult(source_label="SRC", dest_label="DST",
                            state=CellState.DONE)
        with pytest.raises(TypeError):
            write_chain_of_custody_log(
                [src], [dst], [result], {"SRC": BROKEN_FLAT}, "20260612_000000",
            )
