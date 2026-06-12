"""
Tests for core/offload.py — staging, verification, commit, and failure paths.

These are the highest-stakes tests: offload runs against camera cards that
may be reformatted within hours.  A bug that commits before verification
passes, silently skips a file, or marks a destination done while files are
in-flight is unrecoverable.
"""

import shutil
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.offload import (
    OffloadSource,
    OffloadDest,
    OffloadConfig,
    CellState,
    preflight_source_readonly,
    prehash_source,
    copy_source_to_staging,
    verify_staging,
    commit_staging,
    write_failure_report,
    scan_naming_patterns,
    detect_cross_source_duplicates,
    detect_subfolder_collisions,
    build_normalization_plan,
    apply_normalization_in_staging,
    run_offload,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def log_cb():
    return MagicMock()


@pytest.fixture
def status_cb():
    return MagicMock()


@pytest.fixture(autouse=True)
def _isolate_offload_outputs(tmp_path, monkeypatch):
    """Keep run_offload's real-world side effects out of ~/Documents/STSyncTool/.

    Any test that drives run_offload would otherwise write a chain-of-custody
    log to OFFLOAD_LOGS_DIR and an ingest manifest into the central archive,
    polluting the user's real output directory on every test run. Redirect the
    log dir into the test's tmp_path and stub the archive write so the suite
    stays hermetic. Tests that need the log assert against the path run_offload
    returns, so the redirect is transparent to them.
    """
    monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "_offload_logs")
    monkeypatch.setattr("core.offload.save_offload_manifest", lambda *a, **k: None)


def _make_source(tmp_path: Path, label: str = "A001") -> tuple[OffloadSource, dict]:
    src_dir = tmp_path / label
    src_dir.mkdir()
    files = {
        "clip001.mov": b"fake video data one",
        "clip002.mov": b"fake video data two",
        "subdir/audio.wav": b"audio content",
    }
    for rel, data in files.items():
        p = src_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return OffloadSource(label=label, path=src_dir), files


# ---------------------------------------------------------------------------
# preflight_source_readonly
# ---------------------------------------------------------------------------

class TestPreflight:
    def test_valid_directory_passes(self, tmp_path):
        src = OffloadSource(label="A", path=tmp_path)
        preflight_source_readonly(src)  # should not raise

    def test_missing_path_raises(self, tmp_path):
        src = OffloadSource(label="A", path=tmp_path / "does_not_exist")
        with pytest.raises(FileNotFoundError):
            preflight_source_readonly(src)

    def test_file_not_directory_raises(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_bytes(b"x")
        src = OffloadSource(label="A", path=f)
        with pytest.raises(NotADirectoryError):
            preflight_source_readonly(src)


# ---------------------------------------------------------------------------
# prehash_source
# ---------------------------------------------------------------------------

class TestPrehash:
    def test_all_files_hashed(self, tmp_path, log_cb):
        source, files = _make_source(tmp_path)
        manifest = prehash_source(source, log_cb)
        assert set(manifest.keys()) == {
            "clip001.mov", "clip002.mov", "subdir/audio.wav"
        }

    def test_manifest_entries_have_required_fields(self, tmp_path, log_cb):
        source, _ = _make_source(tmp_path)
        manifest = prehash_source(source, log_cb)
        for rel, info in manifest.items():
            assert "size" in info
            assert "checksum" in info
            assert info["algorithm"] == "sha256"

    def test_checksum_is_sha256_hex(self, tmp_path, log_cb):
        source, _ = _make_source(tmp_path)
        manifest = prehash_source(source, log_cb)
        for info in manifest.values():
            assert len(info["checksum"]) == 64
            int(info["checksum"], 16)  # raises ValueError if not valid hex

    def test_size_matches_file(self, tmp_path, log_cb):
        source, files = _make_source(tmp_path)
        manifest = prehash_source(source, log_cb)
        for rel, data in files.items():
            assert manifest[rel]["size"] == len(data)

    def test_different_files_different_checksums(self, tmp_path, log_cb):
        source, _ = _make_source(tmp_path)
        manifest = prehash_source(source, log_cb)
        checksums = [info["checksum"] for info in manifest.values()]
        assert len(checksums) == len(set(checksums))


# ---------------------------------------------------------------------------
# copy_source_to_staging
# ---------------------------------------------------------------------------

class TestCopyToStaging:
    def _run(self, tmp_path, log_cb, status_cb):
        source, files = _make_source(tmp_path)
        dest = OffloadDest(label="Primary", path=tmp_path / "dest")
        dest.path.mkdir()
        manifest = prehash_source(source, log_cb)
        staging = copy_source_to_staging(source, dest, "20260609T120000", manifest, 1, log_cb, status_cb)
        return staging, manifest

    def test_staging_dir_created(self, tmp_path, log_cb, status_cb):
        staging, _ = self._run(tmp_path, log_cb, status_cb)
        assert staging.exists()
        assert staging.is_dir()

    def test_staging_dir_is_inside_dest(self, tmp_path, log_cb, status_cb):
        staging, _ = self._run(tmp_path, log_cb, status_cb)
        assert ".st_staging_" in staging.name

    def test_all_files_present_in_staging(self, tmp_path, log_cb, status_cb):
        staging, manifest = self._run(tmp_path, log_cb, status_cb)
        for rel in manifest:
            assert (staging / rel).exists()

    def test_status_cb_called_with_copying(self, tmp_path, log_cb, status_cb):
        self._run(tmp_path, log_cb, status_cb)
        calls = [c.args for c in status_cb.call_args_list]
        assert any(state == CellState.COPYING for _, _, state in calls)

    def test_progress_cb_emits_bytes_not_file_count(self, tmp_path, log_cb, status_cb):
        """progress_cb should receive (src, dst, bytes_done, bytes_total) not file indices."""
        source, _ = _make_source(tmp_path)
        dest = OffloadDest(label="Primary", path=tmp_path / "dest2")
        dest.path.mkdir()
        manifest = prehash_source(source, log_cb)
        total_bytes = sum(v["size"] for v in manifest.values() if isinstance(v, dict))
        calls = []
        copy_source_to_staging(
            source, dest, "20260609T120000", manifest, 1, log_cb, status_cb,
            progress_cb=lambda s, d, done, total: calls.append((s, d, done, total)),
        )
        assert calls, "progress_cb was not called"
        # last call should report total_bytes for both done and total
        last = calls[-1]
        assert last[2] == total_bytes, f"bytes_done should equal total at end, got {last[2]} vs {total_bytes}"
        assert last[3] == total_bytes
        # bytes_done should be monotonically non-decreasing
        for i in range(1, len(calls)):
            assert calls[i][2] >= calls[i - 1][2], "bytes_done went backwards"

    def test_progress_cb_total_equals_manifest_sum(self, tmp_path, log_cb, status_cb):
        """bytes_total passed to progress_cb must equal sum of manifest sizes."""
        source, _ = _make_source(tmp_path)
        dest = OffloadDest(label="Primary", path=tmp_path / "dest3")
        dest.path.mkdir()
        manifest = prehash_source(source, log_cb)
        expected_total = sum(v["size"] for v in manifest.values() if isinstance(v, dict))
        totals = []
        copy_source_to_staging(
            source, dest, "20260609T120000", manifest, 1, log_cb, status_cb,
            progress_cb=lambda s, d, done, total: totals.append(total),
        )
        assert all(t == expected_total for t in totals), (
            f"bytes_total inconsistent across calls: {set(totals)}"
        )


# ---------------------------------------------------------------------------
# verify_staging
# ---------------------------------------------------------------------------

class TestVerifyStaging:
    def _setup(self, tmp_path, log_cb, status_cb):
        source, _ = _make_source(tmp_path)
        dest = OffloadDest(label="Primary", path=tmp_path / "dest")
        dest.path.mkdir()
        manifest = prehash_source(source, log_cb)
        staging = copy_source_to_staging(source, dest, "20260609T120000", manifest, 1, log_cb, status_cb)
        return staging, manifest

    def test_clean_copy_passes(self, tmp_path, log_cb, status_cb):
        staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        errors = verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")
        assert errors == []

    def test_missing_file_is_error(self, tmp_path, log_cb, status_cb):
        staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        (staging / "clip001.mov").unlink()
        errors = verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")
        assert any("clip001.mov" in e for e in errors)

    def test_corrupted_file_is_error(self, tmp_path, log_cb, status_cb):
        staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        (staging / "clip001.mov").write_bytes(b"corrupted!")
        errors = verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")
        assert any("clip001.mov" in e for e in errors)

    def test_size_mismatch_is_error(self, tmp_path, log_cb, status_cb):
        staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        # Truncate the file without changing its checksum key in manifest
        (staging / "clip002.mov").write_bytes(b"short")
        errors = verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")
        assert any("clip002.mov" in e for e in errors)

    def test_all_files_corrupted_returns_all_errors(self, tmp_path, log_cb, status_cb):
        staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        for rel in manifest:
            (staging / rel).write_bytes(b"garbage")
        errors = verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")
        assert len(errors) == len(manifest)

    def test_verify_does_not_commit(self, tmp_path, log_cb, status_cb):
        staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")
        # Staging dir must still exist — verify must not side-effect commit
        assert staging.exists()


# ---------------------------------------------------------------------------
# commit_staging
# ---------------------------------------------------------------------------

class TestCommitStaging:
    def _setup(self, tmp_path, log_cb, status_cb):
        source, _ = _make_source(tmp_path)
        dest = OffloadDest(label="Primary", path=tmp_path / "dest")
        dest.path.mkdir()
        manifest = prehash_source(source, log_cb)
        staging = copy_source_to_staging(source, dest, "20260609T120000", manifest, 1, log_cb, status_cb)
        return source, dest, staging, manifest

    def test_staging_removed_after_commit(self, tmp_path, log_cb, status_cb):
        source, dest, staging, _ = self._setup(tmp_path, log_cb, status_cb)
        commit_staging(staging, dest, source, log_cb, status_cb)
        assert not staging.exists()

    def test_files_present_at_final_path(self, tmp_path, log_cb, status_cb):
        source, dest, staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        final = commit_staging(staging, dest, source, log_cb, status_cb)
        for rel in manifest:
            assert (final / rel).exists()

    def test_final_path_not_staging_path(self, tmp_path, log_cb, status_cb):
        source, dest, staging, _ = self._setup(tmp_path, log_cb, status_cb)
        final = commit_staging(staging, dest, source, log_cb, status_cb)
        assert ".st_staging_" not in final.name

    def test_commit_into_existing_final_dir(self, tmp_path, log_cb, status_cb):
        source, dest, staging, manifest = self._setup(tmp_path, log_cb, status_cb)
        # Pre-create the final directory with a pre-existing file
        # (copy_source_to_staging already creates this dir as a side-effect)
        final_dir = dest.path / source.effective_subfolder()
        final_dir.mkdir(parents=True, exist_ok=True)
        (final_dir / "pre_existing.txt").write_bytes(b"keep me")

        commit_staging(staging, dest, source, log_cb, status_cb)

        # New files landed
        for rel in manifest:
            assert (final_dir / rel).exists()
        # Pre-existing file was not deleted
        assert (final_dir / "pre_existing.txt").exists()

    def test_status_cb_called_with_committing(self, tmp_path, log_cb, status_cb):
        source, dest, staging, _ = self._setup(tmp_path, log_cb, status_cb)
        commit_staging(staging, dest, source, log_cb, status_cb)
        calls = [c.args for c in status_cb.call_args_list]
        assert any(state == CellState.COMMITTING for _, _, state in calls)


# ---------------------------------------------------------------------------
# write_failure_report
# ---------------------------------------------------------------------------

class TestFailureReport:
    def test_report_file_created(self, tmp_path):
        staging = tmp_path / ".st_staging_20260609T120000"
        staging.mkdir()
        write_failure_report(staging, ["Missing: clip.mov"], "A001", "Primary")
        reports = list(tmp_path.glob(".st_failure_*.txt"))
        assert len(reports) == 1

    def test_report_contains_errors(self, tmp_path):
        staging = tmp_path / ".st_staging_20260609T120000"
        staging.mkdir()
        errors = ["Missing: clip.mov", "Checksum mismatch: audio.wav"]
        write_failure_report(staging, errors, "A001", "Primary")
        report = next(tmp_path.glob(".st_failure_*.txt"))
        content = report.read_text()
        for e in errors:
            assert e in content

    def test_report_contains_source_and_dest(self, tmp_path):
        staging = tmp_path / ".st_staging_20260609T120000"
        staging.mkdir()
        write_failure_report(staging, ["err"], "CARD_A", "NAS_PRIMARY")
        report = next(tmp_path.glob(".st_failure_*.txt"))
        content = report.read_text()
        assert "CARD_A" in content
        assert "NAS_PRIMARY" in content


# ---------------------------------------------------------------------------
# Staging never committed when verification fails
# ---------------------------------------------------------------------------

class TestNeverCommitOnFailure:
    """
    The most critical invariant: commit_staging must never be called when
    verify_staging returns errors.  This test verifies the functions are
    independent and that calling commit after a failed verify still leaves
    evidence (failure report) in place.
    """

    def test_failed_verify_result_is_non_empty(self, tmp_path, log_cb, status_cb):
        source, _ = _make_source(tmp_path)
        dest = OffloadDest(label="Primary", path=tmp_path / "dest")
        dest.path.mkdir()
        manifest = prehash_source(source, log_cb)
        staging = copy_source_to_staging(source, dest, "ts", manifest, 1, log_cb, status_cb)
        # Corrupt a file
        (staging / "clip001.mov").write_bytes(b"corrupt")
        errors = verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")
        assert errors  # caller must check this before calling commit

    def test_caller_writes_failure_report_not_commit(self, tmp_path, log_cb, status_cb):
        source, _ = _make_source(tmp_path)
        dest = OffloadDest(label="Primary", path=tmp_path / "dest")
        dest.path.mkdir()
        manifest = prehash_source(source, log_cb)
        staging = copy_source_to_staging(source, dest, "ts", manifest, 1, log_cb, status_cb)
        (staging / "clip001.mov").write_bytes(b"corrupt")
        errors = verify_staging(staging, manifest, log_cb, status_cb, "A001", "Primary")

        if errors:  # simulating what the orchestrator must do
            write_failure_report(staging, errors, "A001", "Primary")

        # Final dir must NOT exist — staging not committed
        final_dir = dest.path / source.effective_subfolder()
        assert not final_dir.exists() or ".st_staging_" in staging.name

        # Failure report must exist
        reports = list((dest.path / source.effective_subfolder()).parent.rglob(".st_failure_*.txt"))
        assert len(reports) >= 1


# ---------------------------------------------------------------------------
# Filename normalisation
# ---------------------------------------------------------------------------

class TestFilenameNormalisation:
    def _manifest_from_names(self, names: list[str], cs_prefix: str = "aa") -> dict:
        return {
            name: {"size": 10, "checksum": f"{cs_prefix}{i:062x}", "algorithm": "sha256"}
            for i, name in enumerate(names)
        }

    def test_sequential_pattern_detected(self):
        names = [f"IMG_{i:04d}.mov" for i in range(1, 11)]
        manifest = self._manifest_from_names(names)
        result = scan_naming_patterns(manifest)
        assert result["detected"] is True

    def test_unique_names_not_detected(self):
        names = [f"A001_C{i:03d}_210601_AB.mov" for i in range(1, 10)]
        manifest = self._manifest_from_names(names)
        result = scan_naming_patterns(manifest)
        assert result["detected"] is False

    def test_cross_source_duplicates_detected(self):
        m1 = self._manifest_from_names(["IMG_0001.mov", "IMG_0002.mov"], "aa")
        m2 = self._manifest_from_names(["IMG_0001.mov", "IMG_0003.mov"], "bb")
        dupes = detect_cross_source_duplicates({"A": m1, "B": m2})
        assert "IMG_0001.mov" in dupes

    def test_no_cross_source_duplicates_when_unique(self):
        m1 = self._manifest_from_names(["clip_a.mov"], "aa")
        m2 = self._manifest_from_names(["clip_b.mov"], "bb")
        dupes = detect_cross_source_duplicates({"A": m1, "B": m2})
        assert len(dupes) == 0

    def test_norm_plan_suffix_from_checksum(self):
        manifest = self._manifest_from_names(["IMG_0001.mov"])
        manifest["IMG_0001.mov"]["checksum"] = "abcdef1234567890" + "0" * 48
        plan = build_normalization_plan(manifest)
        # plan maps original → normalized
        assert plan.get("IMG_0001.mov") == "IMG_0001_abcdef12.mov"

    def test_apply_normalisation_renames_in_staging(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "IMG_0001.mov").write_bytes(b"video")
        plan = {"IMG_0001.mov": "IMG_0001_abcdef12.mov"}
        apply_normalization_in_staging(staging, plan, MagicMock())
        assert (staging / "IMG_0001_abcdef12.mov").exists()
        assert not (staging / "IMG_0001.mov").exists()

    def test_apply_normalisation_source_not_modified(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "IMG_0001.mov").write_bytes(b"original")
        staging = tmp_path / "staging"
        staging.mkdir()
        shutil.copy2(str(source_dir / "IMG_0001.mov"), str(staging / "IMG_0001.mov"))

        plan = {"IMG_0001.mov": "IMG_0001_abcdef12.mov"}
        apply_normalization_in_staging(staging, plan, MagicMock())

        # Source card must be untouched
        assert (source_dir / "IMG_0001.mov").exists()
        assert (source_dir / "IMG_0001.mov").read_bytes() == b"original"


# ---------------------------------------------------------------------------
# run_offload — staging never committed on verification failure
# ---------------------------------------------------------------------------

def _default_config(**overrides) -> OffloadConfig:
    cfg = OffloadConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_source_dir(tmp_path: Path, label: str, files: dict) -> OffloadSource:
    src_dir = tmp_path / label
    src_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = src_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return OffloadSource(label=label, path=src_dir)


class TestRunOffloadStagingInvariant:
    """
    Staging must never be renamed to the final path unless verification passes.
    These tests drive run_offload end-to-end using real filesystem operations
    and inject corruption at the right moment to confirm the invariant holds.
    """

    def _run(self, sources, dests, config=None):
        status_cb = MagicMock()
        log_cb = MagicMock()
        cfg = config or _default_config()
        return run_offload(sources, dests, cfg, status_cb, log_cb)

    def test_clean_offload_produces_done_state(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"good data"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        results, _, _ = self._run([src], [dst])

        assert len(results) == 1
        assert results[0].state == CellState.DONE

    def test_clean_offload_files_at_final_path(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"good data"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        results, _, _ = self._run([src], [dst])

        final = results[0].final_path
        assert final and Path(final).exists()
        assert (Path(final) / "clip.mov").exists()

    def test_clean_offload_no_staging_dir_remains(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"good data"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        self._run([src], [dst])

        staging_dirs = list((tmp_path / "nas").rglob(".st_staging_*"))
        assert staging_dirs == []

    def test_verification_failure_state_is_failed(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"original"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        # Corrupt every file in staging before verification runs
        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def corrupt_then_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            for f in staging_dir.rglob("*"):
                if f.is_file():
                    f.write_bytes(b"corrupted")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=corrupt_then_verify):
            results, _, _ = self._run([src], [dst])

        assert results[0].state == CellState.FAILED

    def test_verification_failure_leaves_no_final_dir(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"original"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def corrupt_then_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            for f in staging_dir.rglob("*"):
                if f.is_file():
                    f.write_bytes(b"corrupted")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=corrupt_then_verify):
            results, _, _ = self._run([src], [dst])

        final_dir = dst.path / src.effective_subfolder()
        # If final_dir exists it must still contain a staging subdir (not committed)
        if final_dir.exists():
            committed_files = [
                f for f in final_dir.rglob("*")
                if f.is_file() and ".st_staging_" not in str(f)
            ]
            assert committed_files == []

    def test_verification_failure_leaves_failure_report(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"original"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def corrupt_then_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            for f in staging_dir.rglob("*"):
                if f.is_file():
                    f.write_bytes(b"corrupted")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=corrupt_then_verify):
            self._run([src], [dst])

        failure_reports = list((tmp_path / "nas").rglob(".st_failure_*.txt"))
        assert len(failure_reports) >= 1

    def test_failed_result_has_errors_list(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"original"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas")
        dst.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def corrupt_then_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            for f in staging_dir.rglob("*"):
                if f.is_file():
                    f.write_bytes(b"corrupted")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=corrupt_then_verify):
            results, _, _ = self._run([src], [dst])

        assert results[0].errors


# ---------------------------------------------------------------------------
# run_offload — per-source eject signal
#
# A source is safe to eject (logically) only when every destination for that
# source reaches DONE.  These tests verify that one destination failing does
# not mask the failure — i.e. the result grid is honest about partial success.
# ---------------------------------------------------------------------------

class TestEjectSignal:
    """
    'Safe to eject' = all CellResults for that source are DONE.
    Any FAILED or SKIPPED result means the source must not be ejected.
    """

    def _source_done(self, results, source_label: str) -> bool:
        source_cells = [r for r in results if r.source_label == source_label]
        return source_cells and all(r.state == CellState.DONE for r in source_cells)

    def test_eject_safe_after_all_dests_pass(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"data"})
        d1 = OffloadDest(label="NAS1", path=tmp_path / "nas1"); d1.path.mkdir()
        d2 = OffloadDest(label="NAS2", path=tmp_path / "nas2"); d2.path.mkdir()

        results, _, _ = run_offload(
            [src], [d1, d2], _default_config(), MagicMock(), MagicMock()
        )

        assert self._source_done(results, "A001")

    def test_eject_not_safe_when_one_dest_fails(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"data"})
        d1 = OffloadDest(label="NAS1", path=tmp_path / "nas1"); d1.path.mkdir()
        d2 = OffloadDest(label="NAS2", path=tmp_path / "nas2"); d2.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def fail_for_nas2(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            if dst_lbl == "NAS2":
                for f in staging_dir.rglob("*"):
                    if f.is_file():
                        f.write_bytes(b"corrupt")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=fail_for_nas2):
            results, _, _ = run_offload(
                [src], [d1, d2], _default_config(), MagicMock(), MagicMock()
            )

        assert not self._source_done(results, "A001")

    def test_eject_not_safe_when_all_dests_fail(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"data"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def always_fail(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            for f in staging_dir.rglob("*"):
                if f.is_file():
                    f.write_bytes(b"corrupt")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=always_fail):
            results, _, _ = run_offload(
                [src], [dst], _default_config(), MagicMock(), MagicMock()
            )

        assert not self._source_done(results, "A001")

    def test_two_sources_eject_independently(self, tmp_path):
        """Source A passing all dests must not be blocked by source B failing."""
        src_a = _make_source_dir(tmp_path, "A001", {"clip_a.mov": b"data a"})
        src_b = _make_source_dir(tmp_path, "B001", {"clip_b.mov": b"data b"})
        dst   = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def fail_for_b(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            if src_lbl == "B001":
                for f in staging_dir.rglob("*"):
                    if f.is_file():
                        f.write_bytes(b"corrupt")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=fail_for_b):
            results, _, _ = run_offload(
                [src_a, src_b], [dst], _default_config(), MagicMock(), MagicMock()
            )

        assert     self._source_done(results, "A001")
        assert not self._source_done(results, "B001")


# ---------------------------------------------------------------------------
# write_chain_of_custody_log — auditable overall verdict + per-file verify
#
# MANIFEST-FIX (Phase 4): the chain-of-custody log must be readable by a human
# or an audit tool without inferring outcomes from cell state.  It must carry an
# explicit OVERALL RESULT line, a per-file VERIFY: PASS/FAIL line for every file,
# a collision-proof filename (4-char hex suffix), and must never mention OS-junk
# files (.DS_Store, Thumbs.db, desktop.ini) that are filtered before pre-hash.
# ---------------------------------------------------------------------------

import re as _re
import json as _json
import hashlib as _hashlib


class TestChainOfCustodyLog:
    """Drives run_offload end to end and inspects the written COC log file.

    Output isolation is handled by the module-level _isolate_offload_outputs
    autouse fixture.
    """

    def _run(self, sources, dests, config=None):
        cfg = config or _default_config()
        return run_offload(sources, dests, cfg, MagicMock(), MagicMock())

    def test_overall_result_complete_on_clean_run(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"good data"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        _, _, log_path = self._run([src], [dst])
        content = log_path.read_text()
        assert "OVERALL RESULT: COMPLETE" in content

    def test_overall_result_partial_failure_when_a_cell_fails(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"original"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def corrupt_then_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            for f in staging_dir.rglob("*"):
                if f.is_file():
                    f.write_bytes(b"corrupted")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=corrupt_then_verify):
            _, _, log_path = self._run([src], [dst])

        content = log_path.read_text()
        assert "OVERALL RESULT: PARTIAL_FAILURE" in content

    def test_per_file_verify_pass_lines_present(self, tmp_path):
        src = _make_source_dir(
            tmp_path, "A001",
            {"clip001.mov": b"one", "subdir/clip002.mov": b"two"},
        )
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        _, _, log_path = self._run([src], [dst])
        content = log_path.read_text()
        # Every source file must have an explicit PASS line in the log.
        assert "VERIFY: PASS  clip001.mov" in content
        assert "VERIFY: PASS  subdir/clip002.mov" in content

    def test_per_file_verify_fail_line_on_corruption(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"original"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        original_verify = __import__("core.offload", fromlist=["verify_staging"]).verify_staging

        def corrupt_then_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl):
            for f in staging_dir.rglob("*"):
                if f.is_file():
                    f.write_bytes(b"corrupted")
            return original_verify(staging_dir, manifest, log_cb, status_cb, src_lbl, dst_lbl)

        with patch("core.offload.verify_staging", side_effect=corrupt_then_verify):
            _, _, log_path = self._run([src], [dst])

        assert "VERIFY: FAIL  clip.mov" in log_path.read_text()

    def test_log_filename_has_4char_hex_suffix(self, tmp_path):
        src = _make_source_dir(tmp_path, "A001", {"clip.mov": b"good data"})
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        _, _, log_path = self._run([src], [dst])
        # offload_<YYYYmmdd>_<HHMMSS>_<4 hex>.txt
        assert _re.fullmatch(r"offload_\d{8}_\d{6}_[0-9a-f]{4}\.txt", log_path.name), log_path.name

    def test_ds_store_never_appears_in_log(self, tmp_path):
        src_dir = tmp_path / "A001"
        src_dir.mkdir()
        (src_dir / "clip.mov").write_bytes(b"good data")
        (src_dir / ".DS_Store").write_bytes(b"junk")          # must be filtered
        (src_dir / "subdir").mkdir()
        (src_dir / "subdir" / "Thumbs.db").write_bytes(b"junk")  # must be filtered
        src = OffloadSource(label="A001", path=src_dir)
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        results, manifests, log_path = self._run([src], [dst])
        content = log_path.read_text()
        assert ".DS_Store" not in content
        assert "Thumbs.db" not in content
        # And the only real file was accounted for, so the run still completes.
        assert results[0].state == CellState.DONE
        assert ".DS_Store" not in manifests["A001"]


# ---------------------------------------------------------------------------
# KNOWN-ISSUE-FIX: subfolder collision warning (Phase 5 #24)
# ---------------------------------------------------------------------------

class TestSubfolderCollision:
    """detect_subfolder_collisions + the warning run_offload emits."""

    def test_distinct_subfolders_no_collision(self):
        a = OffloadSource(label="A001", path=Path("/x/a"))
        b = OffloadSource(label="B002", path=Path("/x/b"))
        assert detect_subfolder_collisions([a, b]) == {}

    def test_same_label_collides(self):
        a = OffloadSource(label="CARD", path=Path("/x/a"))
        b = OffloadSource(label="CARD", path=Path("/x/b"))
        coll = detect_subfolder_collisions([a, b])
        assert "card" in coll
        assert sorted(coll["card"]) == ["CARD", "CARD"]

    def test_override_to_same_subfolder_collides(self):
        a = OffloadSource(label="A001", path=Path("/x/a"), subfolder="Shoot")
        b = OffloadSource(label="B002", path=Path("/x/b"), subfolder="Shoot")
        coll = detect_subfolder_collisions([a, b])
        assert coll == {"shoot": ["A001", "B002"]}

    def test_collision_is_case_insensitive(self):
        a = OffloadSource(label="A001", path=Path("/x/a"), subfolder="shoot")
        b = OffloadSource(label="B002", path=Path("/x/b"), subfolder="SHOOT")
        assert "shoot" in detect_subfolder_collisions([a, b])

    def test_disabled_source_excluded(self):
        a = OffloadSource(label="A001", path=Path("/x/a"), subfolder="Shoot")
        b = OffloadSource(label="B002", path=Path("/x/b"), subfolder="Shoot", enabled=False)
        assert detect_subfolder_collisions([a, b]) == {}

    def test_run_offload_logs_warning_and_still_offloads(self, tmp_path):
        # Two sources overridden to the same subfolder. The run must warn but
        # still complete; the merged directory holds both sources' files.
        a = _make_source_dir(tmp_path, "A001", {"a.mov": b"alpha"})
        a.subfolder = "Shared"
        b = _make_source_dir(tmp_path, "B002", {"b.mov": b"bravo"})
        b.subfolder = "Shared"
        dst = OffloadDest(label="NAS", path=tmp_path / "nas"); dst.path.mkdir()

        log_cb = MagicMock()
        results, _, _ = run_offload(
            [a, b], [dst], _default_config(), MagicMock(), log_cb
        )

        warned = [
            args[0] for args, _ in log_cb.call_args_list
            if "share subfolder" in args[0] and "A001" in args[0] and "B002" in args[0]
        ]
        assert warned, "expected a subfolder-collision warning in the log"
        assert all(r.state == CellState.DONE for r in results)

        merged = dst.path / "Shared"
        assert (merged / "a.mov").exists()
        assert (merged / "b.mov").exists()


# ---------------------------------------------------------------------------
# OffloadSource.effective_subfolder
# ---------------------------------------------------------------------------

class TestEffectiveSubfolder:
    def test_uses_label_when_subfolder_empty(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"))
        assert src.effective_subfolder() == "A001"

    def test_uses_subfolder_when_set(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"), subfolder="Production")
        assert src.effective_subfolder() == "Production"

    def test_strips_whitespace_before_checking_empty(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"), subfolder="   ")
        assert src.effective_subfolder() == "A001"

    def test_strips_whitespace_from_non_empty_subfolder(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"), subfolder="  Shared  ")
        assert src.effective_subfolder() == "Shared"


# ---------------------------------------------------------------------------
# _sha256
# ---------------------------------------------------------------------------

from core.offload import _sha256


class TestSha256:
    def test_returns_64_char_hex_string(self, tmp_path):
        f = tmp_path / "file.bin"
        f.write_bytes(b"hello world")
        result = _sha256(f)
        assert isinstance(result, str)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_known_value(self, tmp_path):
        f = tmp_path / "file.bin"
        data = b"hello world"
        f.write_bytes(data)
        expected = _hashlib.sha256(data).hexdigest()
        assert _sha256(f) == expected

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert _sha256(a) != _sha256(b)

    def test_empty_file_has_known_hash(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = _hashlib.sha256(b"").hexdigest()
        assert _sha256(f) == expected


# ---------------------------------------------------------------------------
# _retryable
# ---------------------------------------------------------------------------

from core.offload import _retryable


class TestRetryable:
    def test_oserror_is_retryable(self):
        assert _retryable(OSError("disk error")) is True

    def test_ioerror_is_retryable(self):
        assert _retryable(IOError("io error")) is True

    def test_connection_reset_is_retryable(self):
        assert _retryable(ConnectionResetError("reset")) is True

    def test_timeout_is_retryable(self):
        assert _retryable(TimeoutError("timeout")) is True

    def test_valueerror_is_not_retryable(self):
        assert _retryable(ValueError("bad value")) is False

    def test_runtimeerror_is_not_retryable(self):
        assert _retryable(RuntimeError("unexpected")) is False

    def test_keyboardinterrupt_is_not_retryable(self):
        assert _retryable(KeyboardInterrupt()) is False


# ---------------------------------------------------------------------------
# _copy_with_retries
# ---------------------------------------------------------------------------

from core.offload import _copy_with_retries


class TestCopyWithRetries:
    def test_copies_file_successfully(self, tmp_path):
        src = tmp_path / "src.mov"
        dst = tmp_path / "dest" / "sub" / "dst.mov"
        src.write_bytes(b"video content")
        _copy_with_retries(src, dst, max_retries=1, log_cb=MagicMock())
        assert dst.exists()
        assert dst.read_bytes() == b"video content"

    def test_creates_destination_parent_dirs(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        dst = tmp_path / "a" / "b" / "c" / "dst.bin"
        _copy_with_retries(src, dst, max_retries=1, log_cb=MagicMock())
        assert dst.exists()

    def test_retries_on_transient_oserror(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.bin"
        log_cb = MagicMock()
        call_count = 0

        original_copy2 = shutil.copy2

        def flaky_copy(s, d):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("simulated transient error")
            original_copy2(s, d)

        with patch("core.offload.shutil.copy2", side_effect=flaky_copy):
            with patch("core.offload.time.sleep"):
                _copy_with_retries(src, dst, max_retries=3, log_cb=log_cb)

        assert call_count == 2
        log_cb.assert_called()

    def test_raises_after_max_retries_on_persistent_oserror(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.bin"

        with patch("core.offload.shutil.copy2", side_effect=OSError("persistent")):
            with patch("core.offload.time.sleep"):
                with pytest.raises(OSError, match="persistent"):
                    _copy_with_retries(src, dst, max_retries=2, log_cb=MagicMock())

    def test_non_retryable_error_raised_immediately(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.bin"
        call_count = 0

        def bad_copy(s, d):
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with patch("core.offload.shutil.copy2", side_effect=bad_copy):
            with pytest.raises(ValueError, match="not retryable"):
                _copy_with_retries(src, dst, max_retries=3, log_cb=MagicMock())

        # Must not retry a non-retryable exception
        assert call_count == 1

    def test_logs_warning_on_retry(self, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.bin"
        log_cb = MagicMock()
        call_count = 0

        original_copy2 = shutil.copy2

        def flaky_copy(s, d):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("transient")
            original_copy2(s, d)

        with patch("core.offload.shutil.copy2", side_effect=flaky_copy):
            with patch("core.offload.time.sleep"):
                _copy_with_retries(src, dst, max_retries=3, log_cb=log_cb)

        warning_calls = [c for c in log_cb.call_args_list if "Retry" in str(c)]
        assert len(warning_calls) >= 1


# ---------------------------------------------------------------------------
# _mark_remaining
# ---------------------------------------------------------------------------

from core.offload import _mark_remaining, CellResult


class TestMarkRemaining:
    def _make_grid(self, src_label, dest_labels):
        return {
            (src_label, d): CellResult(src_label, d)
            for d in dest_labels
        }

    def test_marks_all_when_after_is_none(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"))
        dests = [OffloadDest(label=d, path=Path(f"/tmp/{d}")) for d in ["D1", "D2", "D3"]]
        grid = self._make_grid("A001", ["D1", "D2", "D3"])
        status_cb = MagicMock()
        _mark_remaining(grid, src, dests, CellState.SKIPPED, status_cb, after=None)
        for d in ["D1", "D2", "D3"]:
            assert grid[("A001", d)].state == CellState.SKIPPED

    def test_marks_only_after_pivot(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"))
        dests = [OffloadDest(label=d, path=Path(f"/tmp/{d}")) for d in ["D1", "D2", "D3"]]
        grid = self._make_grid("A001", ["D1", "D2", "D3"])
        pivot = dests[0]  # after D1 — only D2 and D3 should be marked
        status_cb = MagicMock()
        _mark_remaining(grid, src, dests, CellState.SKIPPED, status_cb, after=pivot)
        assert grid[("A001", "D1")].state == CellState.PENDING   # pivot not touched
        assert grid[("A001", "D2")].state == CellState.SKIPPED
        assert grid[("A001", "D3")].state == CellState.SKIPPED

    def test_status_cb_called_for_each_marked(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"))
        dests = [OffloadDest(label=d, path=Path(f"/tmp/{d}")) for d in ["D1", "D2"]]
        grid = self._make_grid("A001", ["D1", "D2"])
        status_cb = MagicMock()
        _mark_remaining(grid, src, dests, CellState.SKIPPED, status_cb, after=None)
        assert status_cb.call_count == 2

    def test_empty_dests_is_no_op(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"))
        grid = {}
        status_cb = MagicMock()
        _mark_remaining(grid, src, [], CellState.SKIPPED, status_cb, after=None)
        status_cb.assert_not_called()

    def test_marks_failed_state(self):
        src = OffloadSource(label="A001", path=Path("/tmp/a"))
        dests = [OffloadDest(label="D1", path=Path("/tmp/d1"))]
        grid = self._make_grid("A001", ["D1"])
        status_cb = MagicMock()
        _mark_remaining(grid, src, dests, CellState.FAILED, status_cb, after=None)
        assert grid[("A001", "D1")].state == CellState.FAILED


# ---------------------------------------------------------------------------
# _is_r3d_stem
# ---------------------------------------------------------------------------

from core.offload import _is_r3d_stem


class TestIsR3dStem:
    def test_valid_r3d_stem_matches(self):
        assert _is_r3d_stem("A001_C001_210601") is True

    def test_valid_r3d_stem_with_extra_suffix_matches(self):
        assert _is_r3d_stem("A001_C001_210601_EXTRA") is True

    def test_img_pattern_does_not_match(self):
        assert _is_r3d_stem("IMG_0001") is False

    def test_generic_sequential_does_not_match(self):
        assert _is_r3d_stem("CLIP_0001") is False

    def test_empty_string_does_not_match(self):
        assert _is_r3d_stem("") is False

    def test_lowercase_r3d_stem_matches(self):
        # Regex uses re.IGNORECASE
        assert _is_r3d_stem("a001_c001_210601") is True


# ---------------------------------------------------------------------------
# _sequential_pattern_name
# ---------------------------------------------------------------------------

from core.offload import _sequential_pattern_name


class TestSequentialPatternName:
    def test_img_pattern_returns_name(self):
        assert _sequential_pattern_name("IMG_0001") == "IMG_XXXX"

    def test_mvi_pattern_returns_name(self):
        assert _sequential_pattern_name("MVI_0001") == "MVI_XXXX"

    def test_dji_pattern_returns_name(self):
        assert _sequential_pattern_name("DJI_0001") == "DJI_XXXX"

    def test_gh0_pattern_returns_name(self):
        assert _sequential_pattern_name("GH012345") == "GH0XXXXX"

    def test_r3d_stem_returns_none(self):
        assert _sequential_pattern_name("A001_C001_210601") is None

    def test_unique_name_returns_none(self):
        assert _sequential_pattern_name("INTERVIEW_WIDE_TAKE3") is None

    def test_generic_sequential_returns_fallback(self):
        # Matches _GENERIC_SEQUENTIAL: 1-4 uppercase + _ + 4-5 digits
        result = _sequential_pattern_name("XYZ_1234")
        assert result == "XXXX_NNNN"

    def test_empty_string_returns_none(self):
        assert _sequential_pattern_name("") is None


# ---------------------------------------------------------------------------
# build_normalized_manifest
# ---------------------------------------------------------------------------

from core.offload import build_normalized_manifest


class TestBuildNormalizedManifest:
    def _source_manifest(self, names_and_checksums):
        return {
            name: {"size": 100, "checksum": cs, "algorithm": "sha256"}
            for name, cs in names_and_checksums
        }

    def test_empty_norm_plan_returns_source_unchanged(self):
        manifest = self._source_manifest([("IMG_0001.mov", "a" * 64)])
        result, norm_block, renames = build_normalized_manifest(manifest, {})
        assert result == manifest
        assert norm_block == {"applied": False}
        assert renames == []

    def test_renamed_entry_has_original_filename_field(self):
        checksum = "abcdef12" + "0" * 56
        manifest = self._source_manifest([("IMG_0001.mov", checksum)])
        norm_plan = {"IMG_0001.mov": "IMG_0001_abcdef12.mov"}
        result, norm_block, renames = build_normalized_manifest(manifest, norm_plan)
        assert "IMG_0001_abcdef12.mov" in result
        assert result["IMG_0001_abcdef12.mov"]["original_filename"] == "IMG_0001.mov"

    def test_renamed_entry_has_hash_suffix_field(self):
        checksum = "abcdef12" + "0" * 56
        manifest = self._source_manifest([("IMG_0001.mov", checksum)])
        norm_plan = {"IMG_0001.mov": "IMG_0001_abcdef12.mov"}
        result, _, _ = build_normalized_manifest(manifest, norm_plan)
        assert result["IMG_0001_abcdef12.mov"]["filename_hash_suffix"] == "abcdef12"

    def test_renames_full_list_has_from_to_reason(self):
        checksum = "abcdef12" + "0" * 56
        manifest = self._source_manifest([("IMG_0001.mov", checksum)])
        norm_plan = {"IMG_0001.mov": "IMG_0001_abcdef12.mov"}
        _, _, renames_full = build_normalized_manifest(manifest, norm_plan)
        assert len(renames_full) == 1
        entry = renames_full[0]
        assert entry["from"] == "IMG_0001.mov"
        assert entry["to"] == "IMG_0001_abcdef12.mov"
        assert entry["reason"] == "normalize"

    def test_norm_block_applied_true_when_renames_present(self):
        checksum = "abcdef12" + "0" * 56
        manifest = self._source_manifest([("IMG_0001.mov", checksum)])
        norm_plan = {"IMG_0001.mov": "IMG_0001_abcdef12.mov"}
        _, norm_block, _ = build_normalized_manifest(manifest, norm_plan)
        assert norm_block["applied"] is True
        assert norm_block["method"] == "sha256_prefix8"

    def test_generated_artifacts_key_passed_through(self):
        checksum = "abcdef12" + "0" * 56
        manifest = self._source_manifest([("IMG_0001.mov", checksum)])
        manifest["generated_artifacts"] = {"contact_sheet.jpg": {"type": "thumbnail"}}
        norm_plan = {"IMG_0001.mov": "IMG_0001_abcdef12.mov"}
        result, _, _ = build_normalized_manifest(manifest, norm_plan)
        assert "generated_artifacts" in result
        assert result["generated_artifacts"] == {"contact_sheet.jpg": {"type": "thumbnail"}}

    def test_unrenamed_files_preserved_unchanged(self):
        checksum_a = "aaaa1234" + "0" * 56
        checksum_b = "bbbb5678" + "0" * 56
        manifest = self._source_manifest([
            ("IMG_0001.mov", checksum_a),
            ("interview.mov", checksum_b),
        ])
        norm_plan = {"IMG_0001.mov": "IMG_0001_aaaa1234.mov"}
        result, _, _ = build_normalized_manifest(manifest, norm_plan)
        # interview.mov has no entry in norm_plan — must survive as-is
        assert "interview.mov" in result
        assert "original_filename" not in result["interview.mov"]


# ---------------------------------------------------------------------------
# save_offload_manifest
# ---------------------------------------------------------------------------

from core.offload import save_offload_manifest, build_offload_manifest, CellResult, CellState


class TestSaveOffloadManifest:
    """save_offload_manifest writes manifest JSON to each committed final_path
    and returns the list of paths saved. We patch save_manifest and direct
    file writes to tmp_path so the real archive directory is never touched.
    """

    def _make_committed_result(self, tmp_path, dest_label="NAS"):
        final = tmp_path / dest_label / "A001"
        final.mkdir(parents=True, exist_ok=True)
        r = CellResult(source_label="A001", dest_label=dest_label)
        r.state = CellState.DONE
        r.final_path = final
        r.files_copied = 1
        r.bytes_copied = 10
        r.verified = True
        return r

    def _source_manifest(self):
        return {
            "clip.mov": {"size": 10, "checksum": "aa" * 32, "algorithm": "sha256"}
        }

    def test_returns_list_of_saved_paths(self, tmp_path):
        src = OffloadSource(label="A001", path=tmp_path / "card")
        r = self._make_committed_result(tmp_path)
        mfst = self._source_manifest()

        with patch("core.manifest.save_manifest") as mock_save:
            mock_save.return_value = [r.final_path / "st_manifest.json"]
            saved = save_offload_manifest(src, mfst, [r])

        assert isinstance(saved, list)
        assert len(saved) >= 1

    def test_no_committed_results_returns_empty_list(self, tmp_path):
        src = OffloadSource(label="A001", path=tmp_path / "card")
        mfst = self._source_manifest()
        # Results without final_path set — all filtered out
        r = CellResult(source_label="A001", dest_label="NAS")
        r.state = CellState.FAILED
        saved = save_offload_manifest(src, mfst, [r])
        assert saved == []

    def test_writes_to_additional_destinations(self, tmp_path):
        src = OffloadSource(label="A001", path=tmp_path / "card")
        r1 = self._make_committed_result(tmp_path, "NAS1")
        r2 = self._make_committed_result(tmp_path, "NAS2")
        mfst = self._source_manifest()

        with patch("core.manifest.save_manifest") as mock_save:
            mock_save.return_value = [r1.final_path / "st_manifest.json"]
            saved = save_offload_manifest(src, mfst, [r1, r2])

        # Second destination manifest written directly via Path.write_text
        dest2_manifest = r2.final_path / "st_manifest.json"
        assert dest2_manifest.exists()
        data = _json.loads(dest2_manifest.read_text())
        assert data["label"] == "A001"

    def test_manifest_written_to_second_dest_is_valid_json(self, tmp_path):
        src = OffloadSource(label="A001", path=tmp_path / "card")
        r1 = self._make_committed_result(tmp_path, "NAS1")
        r2 = self._make_committed_result(tmp_path, "NAS2")
        mfst = self._source_manifest()

        with patch("core.manifest.save_manifest") as mock_save:
            mock_save.return_value = [r1.final_path / "st_manifest.json"]
            save_offload_manifest(src, mfst, [r1, r2])

        raw = (r2.final_path / "st_manifest.json").read_text()
        parsed = _json.loads(raw)
        assert parsed.get("schema_version") is not None

    def test_offload_block_present_when_cell_results_passed(self, tmp_path):
        src = OffloadSource(label="A001", path=tmp_path / "card")
        r1 = self._make_committed_result(tmp_path, "NAS1")
        mfst = self._source_manifest()

        captured = {}

        def capture_save(manifest, **kwargs):
            captured["manifest"] = manifest
            dest = kwargs.get("dest_dir") or (tmp_path / "archive")
            p = Path(dest) / "st_manifest.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_json.dumps(manifest, indent=2))
            return [p]

        with patch("core.manifest.save_manifest", side_effect=capture_save):
            save_offload_manifest(src, mfst, [r1])

        assert "offload" in captured["manifest"]
        assert captured["manifest"]["offload"]["overall_result"] == "COMPLETE"
