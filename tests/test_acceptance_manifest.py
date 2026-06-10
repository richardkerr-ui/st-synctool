"""
Acceptance tests for the cross-tab manifest contract.

These exercise the real pipeline against real fixtures on a real filesystem:
an offload writes a schema-1.1 manifest, Verify consumes it, and the merge
diff treats it correctly both as a same-name base and across the
normalisation boundary (original card names -> normalised names).

Unlike the overnight temp scripts these are durable. They are intentionally
end-to-end: they drive core.offload.run_offload, core.manifest.load_manifest,
core.comparison.three_way_diff and the real gui Verify worker rather than
re-implementing any of that logic.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.offload import (
    OffloadSource,
    OffloadDest,
    OffloadConfig,
    CellState,
    run_offload,
)
from core.manifest import load_manifest, generate_manifest
from core.comparison import three_way_diff, DiffState


# ---------------------------------------------------------------------------
# Isolation: keep run_offload's real side effects out of ~/Documents/STSyncTool/
# while STILL persisting the destination manifest (in tmp_path) that these tests
# read back. We redirect both the chain-of-custody log dir and the central
# manifest archive into tmp_path; the {dest}/{label}/st_manifest.json copy lives
# under tmp_path already because the destination is a tmp dir.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "_offload_logs")
    monkeypatch.setattr("core.manifest.LOCAL_MANIFEST_DIR", tmp_path / "_manifest_archive")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# IMG_NNNN.mov matches the sequential video pattern, so an offload with
# normalize_filenames=True renames each to IMG_NNNN_<sha256[:8]>.mov.
_NORMALIZABLE_FILES = {
    "IMG_0001.mov": b"camera clip one",
    "IMG_0002.mov": b"camera clip two",
}


def _run_normalized_offload(tmp_path, files=None, src_label="A001", dst_label="NAS"):
    files = files or _NORMALIZABLE_FILES
    src_dir = tmp_path / src_label
    src_dir.mkdir()
    for name, data in files.items():
        p = src_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    src = OffloadSource(label=src_label, path=src_dir)
    dst = OffloadDest(label=dst_label, path=tmp_path / dst_label.lower())
    dst.path.mkdir()

    cfg = OffloadConfig(normalize_filenames=True)
    results, manifests, log_path = run_offload(
        [src], [dst], cfg, MagicMock(), MagicMock()
    )

    dest_root = dst.path / src.effective_subfolder()
    manifest_path = dest_root / "st_manifest.json"
    return {
        "results": results,
        "manifests": manifests,
        "log_path": log_path,
        "dest_root": dest_root,
        "manifest_path": manifest_path,
        "src": src,
        "dst": dst,
    }


def _drive_verify(folder, manifest):
    """Run the real gui Verify worker headlessly and return its result list.

    VerifyWorker.run() is synchronous for a local folder and emits the result
    list on its `finished` signal, so we connect a slot, call run() directly and
    read the captured results. A QApplication must exist for the signal/slot
    machinery.
    """
    from PyQt6.QtWidgets import QApplication
    from gui.verify_tab import VerifyWorker

    QApplication.instance() or QApplication([])
    worker = VerifyWorker(str(folder), manifest)
    captured = {}
    worker.finished.connect(lambda r: captured.setdefault("results", r))
    worker.error.connect(lambda e: captured.setdefault("error", e))
    worker.run()
    if "error" in captured:
        pytest.fail(f"Verify worker errored: {captured['error']}")
    return captured.get("results", [])


def _real_file_entries(manifest):
    return {
        rel: e for rel, e in manifest.get("files", {}).items()
        if isinstance(e, dict) and "size" in e
    }


# ---------------------------------------------------------------------------
# 1. Offload manifest is loadable and v1.1
# ---------------------------------------------------------------------------

class TestOffloadManifestLoadable:
    def test_persisted_manifest_is_v11_and_complete(self, tmp_path):
        ctx = _run_normalized_offload(tmp_path)
        assert ctx["manifest_path"].exists(), "offload did not persist st_manifest.json"

        m = load_manifest(ctx["manifest_path"])
        assert m["schema_version"] == "1.2"
        assert m["operation"] == "offload"

        entries = _real_file_entries(m)
        assert entries, "manifest has no file entries"
        for rel, e in entries.items():
            sha = e["checksums"]["sha256"]
            assert len(sha) == 64, f"{rel}: sha256 not full length ({len(sha)})"
            int(sha, 16)  # raises if not valid hex
            assert e["hash_algorithm"], f"{rel}: missing hash_algorithm"
            assert e["size"] > 0, f"{rel}: missing/zero size"
            assert e["modtime"], f"{rel}: missing modtime"

    def test_keys_are_normalized(self, tmp_path):
        # Confirms the persisted manifest is the normalized one (the contract's
        # 'normalized-key' base used by tests 3 and 4).
        ctx = _run_normalized_offload(tmp_path)
        m = load_manifest(ctx["manifest_path"])
        keys = list(_real_file_entries(m))
        assert all(re.search(r"_[0-9a-f]{8}\.mov$", k) for k in keys), keys


# ---------------------------------------------------------------------------
# 2. Verify consumes an offload manifest
# ---------------------------------------------------------------------------

class TestVerifyConsumesOffloadManifest:
    def test_all_files_ok(self, tmp_path):
        ctx = _run_normalized_offload(tmp_path)
        m = load_manifest(ctx["manifest_path"])

        results = _drive_verify(ctx["dest_root"], m)
        statuses = [r["status"] for r in results]

        assert statuses, "verify produced no results"
        assert len(statuses) == len(_real_file_entries(m))
        assert "MISSING" not in statuses, results
        assert "MISMATCH" not in statuses, results
        assert all(s == "OK" for s in statuses), results


# ---------------------------------------------------------------------------
# 3. Same-name merge base is clean
# ---------------------------------------------------------------------------

class TestSameNameMergeBaseClean:
    def test_rescan_of_destination_is_all_unchanged(self, tmp_path):
        ctx = _run_normalized_offload(tmp_path)
        base = load_manifest(ctx["manifest_path"])
        # Fresh independent scan of the committed destination.
        scan = generate_manifest(ctx["dest_root"])

        diff = three_way_diff(base, scan, scan)
        states = {r.path: r.state for r in diff}

        assert states, "diff produced no rows"
        assert all(st == DiffState.UNCHANGED for st in states.values()), states
        assert DiffState.RENAMED not in states.values()
        assert DiffState.LOCAL_ONLY not in states.values()
        assert DiffState.SERVER_ONLY not in states.values()


# ---------------------------------------------------------------------------
# 4. Original-name merge base collapses via renames[]
#
# The cross-boundary case the rename contract exists for: a base manifest keyed
# on the pre-normalization card names, diffed against the normalized destination,
# must report the normalized paths as RENAMED (not LOCAL_ONLY) and suppress the
# original paths (not flag them DELETED).
# ---------------------------------------------------------------------------

class TestOriginalNameMergeBaseCollapses:
    def _build_original_name_base(self, norm_manifest):
        """From the normalized manifest, reconstruct a base keyed on original
        names plus the renames[] mapping (original_rel -> normalized_rel)."""
        base_files = {}
        renames = []
        for nrel, e in _real_file_entries(norm_manifest).items():
            orig_name = e.get("original_filename")
            if not orig_name:
                base_files[nrel] = e  # never-renamed file stays put
                continue
            parent = Path(nrel).parent
            orig_rel = orig_name if str(parent) == "." else (parent / orig_name).as_posix()
            base_files[orig_rel] = dict(e)
            renames.append({"from": orig_rel, "to": nrel})
        return {"schema_version": "1.1", "files": base_files, "renames": renames}, renames

    def test_normalized_paths_are_renamed_originals_suppressed(self, tmp_path):
        ctx = _run_normalized_offload(tmp_path)
        norm = load_manifest(ctx["manifest_path"])

        base, renames = self._build_original_name_base(norm)
        assert renames, "fixture produced no renames; normalization did not fire"

        # Push scenario: the normalized names exist locally (yours) but not yet
        # on the server. three_way_diff must collapse them to RENAMED.
        yours = norm
        server = {"files": {}}
        diff = three_way_diff(base, yours, server)
        by_path = {r.path: r for r in diff}

        for r in renames:
            nrel, orig = r["to"], r["from"]
            assert nrel in by_path, f"normalized path {nrel} missing from diff"
            assert by_path[nrel].state == DiffState.RENAMED, (nrel, by_path[nrel].state)
            assert by_path[nrel].renamed_from == orig
            # Original path is suppressed entirely, not surfaced as a deletion.
            assert orig not in by_path, f"original path {orig} should be suppressed"

        # Nothing should leak through as LOCAL_ONLY or any DELETED_* state.
        leaked = {
            p: r.state for p, r in by_path.items()
            if r.state in (DiffState.LOCAL_ONLY, DiffState.DELETED_LOCAL,
                           DiffState.DELETED_SERVER, DiffState.DELETED_BOTH)
        }
        assert not leaked, leaked


# ---------------------------------------------------------------------------
# 5. overall_result and per-file verification present
# ---------------------------------------------------------------------------

class TestOverallResultAndPerFileVerification:
    def test_partial_failure_and_per_file_booleans(self, tmp_path):
        src_dir = tmp_path / "A001"
        src_dir.mkdir()
        (src_dir / "clip.mov").write_bytes(b"the footage")
        src = OffloadSource(label="A001", path=src_dir)
        good = OffloadDest(label="NAS1", path=tmp_path / "nas1"); good.path.mkdir()
        bad  = OffloadDest(label="NAS2", path=tmp_path / "nas2"); bad.path.mkdir()

        from core import offload as _offload
        original_verify = _offload.verify_staging

        def fail_for_bad(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            if dst_lbl == "NAS2":
                for f in staging_dir.rglob("*"):
                    if f.is_file():
                        f.write_bytes(b"corrupted on the second NAS")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=fail_for_bad):
            results, _, log_path = run_offload(
                [src], [good, bad], OffloadConfig(), MagicMock(), MagicMock()
            )

        # Overall result is surfaced in the chain-of-custody record.
        content = log_path.read_text()
        assert "OVERALL RESULT: PARTIAL_FAILURE" in content

        by_dest = {r.dest_label: r for r in results}

        # The failing destination's result reflects the failure.
        failing = by_dest["NAS2"]
        assert failing.state == CellState.FAILED
        assert failing.verified is False
        assert failing.errors

        # The passing destination carries a per-file boolean verification map.
        passing = by_dest["NAS1"]
        assert passing.state == CellState.DONE
        assert passing.verified is True
        assert isinstance(passing.per_file_verify, dict) and passing.per_file_verify
        assert all(isinstance(v, bool) for v in passing.per_file_verify.values())
        assert all(passing.per_file_verify.values()), passing.per_file_verify


# ---------------------------------------------------------------------------
# 6. Full hash in manifest, truncation only in the log
# ---------------------------------------------------------------------------

class TestFullHashInManifestTruncationInLog:
    def test_manifest_full_log_truncated(self, tmp_path):
        ctx = _run_normalized_offload(tmp_path)
        m = load_manifest(ctx["manifest_path"])

        entry = next(iter(_real_file_entries(m).values()))
        full_sha = entry["checksums"]["sha256"]
        assert len(full_sha) == 64

        log = ctx["log_path"].read_text()
        # Log shows only the 16-char prefix; the full hash never appears in it.
        assert full_sha not in log
        assert full_sha[:16] in log
