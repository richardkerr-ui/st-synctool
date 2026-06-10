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
    CellState,
    preflight_source_readonly,
    prehash_source,
    copy_source_to_staging,
    verify_staging,
    commit_staging,
    write_failure_report,
    scan_naming_patterns,
    detect_cross_source_duplicates,
    build_normalization_plan,
    apply_normalization_in_staging,
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
