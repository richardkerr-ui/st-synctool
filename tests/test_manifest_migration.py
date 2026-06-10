"""
Tests for the opt-in on-disk schema migration sweep (core/manifest.py).

load_manifest backfills pre-1.1 manifests in memory but never rewrites the
file. migrate_manifests_on_disk() is the opt-in utility that rewrites them.

These tests are hermetic: every manifest is created under tmp_path and the
sweep is always pointed at an explicit tmp dir, so the user's real
~/Documents/STSyncTool/manifests/ is never read or written.
"""

import json
from pathlib import Path

from core.manifest import (
    SCHEMA_VERSION,
    needs_migration,
    migrate_manifest_file,
    migrate_manifests_on_disk,
)


def _write_v10(path: Path) -> None:
    """A minimal schema-1.0 manifest, missing 1.1 fields."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "label": "old",
        "root": "/some/old/path",
        "files": {
            "clip.mov": {
                "type": "file", "size": 10, "modtime": "",
                "checksums": {"sha256": "a" * 64},
            }
        },
    }, indent=2))


def _write_current(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "label": "new",
        "files": {},
    }, indent=2))


# ---------------------------------------------------------------------------
# needs_migration
# ---------------------------------------------------------------------------

class TestNeedsMigration:
    def test_old_manifest_needs_migration(self, tmp_path):
        p = tmp_path / "st_manifest_old.json"
        _write_v10(p)
        assert needs_migration(p) is True

    def test_current_manifest_does_not(self, tmp_path):
        p = tmp_path / "st_manifest_new.json"
        _write_current(p)
        assert needs_migration(p) is False

    def test_unparseable_file_is_false(self, tmp_path):
        p = tmp_path / "st_manifest_bad.json"
        p.write_text("{ not valid json")
        assert needs_migration(p) is False


# ---------------------------------------------------------------------------
# migrate_manifest_file
# ---------------------------------------------------------------------------

class TestMigrateManifestFile:
    def test_rewrites_and_bumps_version(self, tmp_path):
        p = tmp_path / "st_manifest_old.json"
        _write_v10(p)
        assert migrate_manifest_file(p, backup=False) is True

        data = json.loads(p.read_text())
        assert data["schema_version"] == SCHEMA_VERSION
        # Backfilled top-level + per-file fields are now present on disk.
        assert "filename_normalization" in data
        assert data["files"]["clip.mov"]["hash_algorithm"] == "sha256"
        assert data["files"]["clip.mov"]["gdrive_url"] == ""

    def test_backup_preserves_original(self, tmp_path):
        p = tmp_path / "st_manifest_old.json"
        _write_v10(p)
        original = p.read_text()
        migrate_manifest_file(p, backup=True)

        bak = p.with_suffix(p.suffix + ".bak")
        assert bak.exists()
        assert bak.read_text() == original

    def test_current_file_untouched(self, tmp_path):
        p = tmp_path / "st_manifest_new.json"
        _write_current(p)
        before = p.read_text()
        assert migrate_manifest_file(p) is False
        assert p.read_text() == before


# ---------------------------------------------------------------------------
# migrate_manifests_on_disk (the sweep)
# ---------------------------------------------------------------------------

class TestMigrateOnDisk:
    def _archive(self, tmp_path):
        archive = tmp_path / "archive"
        _write_v10(archive / "A001" / "st_manifest_A001_offload_1.json")
        _write_v10(archive / "B002" / "st_manifest_B002_merge_1.json")
        _write_current(archive / "C003" / "st_manifest_C003_transfer_1.json")
        return archive

    def test_dry_run_changes_nothing(self, tmp_path):
        archive = self._archive(tmp_path)
        report = migrate_manifests_on_disk(archive, dry_run=True)

        assert report["dry_run"] is True
        assert report["scanned"] == 3
        assert len(report["migrated"]) == 2   # the two 1.0 files
        assert report["skipped"] == 1         # the current one
        # On disk, nothing was rewritten.
        for p in archive.rglob("st_manifest*.json"):
            data = json.loads(p.read_text())
            if "A001" in str(p) or "B002" in str(p):
                assert data["schema_version"] == "1.0"

    def test_apply_migrates_only_old_files(self, tmp_path):
        archive = self._archive(tmp_path)
        report = migrate_manifests_on_disk(archive, dry_run=False, backup=False)

        assert report["dry_run"] is False
        assert len(report["migrated"]) == 2
        assert report["skipped"] == 1
        assert report["errors"] == []
        for p in archive.rglob("st_manifest*.json"):
            assert json.loads(p.read_text())["schema_version"] == SCHEMA_VERSION

    def test_rerun_is_idempotent(self, tmp_path):
        archive = self._archive(tmp_path)
        migrate_manifests_on_disk(archive, dry_run=False, backup=True)
        report = migrate_manifests_on_disk(archive, dry_run=False, backup=True)
        # Second pass finds nothing to migrate and ignores its own .bak files.
        assert report["migrated"] == []
        assert report["scanned"] == 3

    def test_missing_dir_returns_empty_report(self, tmp_path):
        report = migrate_manifests_on_disk(tmp_path / "nope", dry_run=False)
        assert report["scanned"] == 0
        assert report["migrated"] == []

    def test_bad_json_is_recorded_not_raised(self, tmp_path):
        archive = tmp_path / "archive"
        archive.mkdir()
        _write_v10(archive / "st_manifest_ok.json")
        (archive / "st_manifest_broken.json").write_text("{ broken")
        report = migrate_manifests_on_disk(archive, dry_run=False, backup=False)
        # The broken file parses as "not needing migration" (skipped), the good
        # one migrates. The sweep never raises.
        assert len(report["migrated"]) == 1
        assert report["scanned"] == 2
