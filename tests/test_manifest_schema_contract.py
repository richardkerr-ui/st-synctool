"""
Layer 1: Manifest schema contract tests.

Every manifest producer (generate_manifest, generate_manifest_fast,
build_offload_manifest) is verified to emit schema-1.2 compliant output.
Cross-producer/consumer compatibility is then checked:
  - load_manifest roundtrip preserves all required fields
  - three_way_diff consumes every manifest type without errors
  - The checksum format VerifyWorker._verify_local expects is always present

Also covers the SCHEMA_INTEROP_SPEC.md acceptance tests that are currently
testable (tests 1, 2-partial, 3, 4, 6 — test 5 is blocked on the offload
custody block not yet being built; see ROADMAP).
"""

import hashlib
import json
import shutil
import pytest
from pathlib import Path

from core.manifest import (
    SCHEMA_VERSION,
    generate_manifest,
    generate_manifest_fast,
    load_manifest,
    save_manifest,
)
from core.comparison import three_way_diff, DiffState
from core.offload import OffloadSource, build_offload_manifest


# ---------------------------------------------------------------------------
# Constants mirroring what VerifyWorker._verify_local and three_way_diff expect
# ---------------------------------------------------------------------------

REQUIRED_TOP_LEVEL = {
    "schema_version", "created_at", "label", "root", "destination",
    "counterpart_path", "operation", "project_id", "workstation",
    "user", "file_count", "renames", "checksum_context", "files",
    "total_size_bytes",
}

REQUIRED_FILE_FIELDS = {"type", "size", "modtime", "checksums", "hash_algorithm"}


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_dir(tmp_path):
    """Small directory with a few files across a subdirectory."""
    (tmp_path / "video.mov").write_bytes(b"fake prores content here")
    (tmp_path / "audio.wav").write_bytes(b"fake audio content here")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "clip.mp4").write_bytes(b"nested mp4 content")
    return tmp_path


@pytest.fixture
def transfer_manifest(sample_dir):
    return generate_manifest(sample_dir, label="source", operation="transfer")


@pytest.fixture
def fast_manifest(sample_dir, transfer_manifest):
    return generate_manifest_fast(
        sample_dir, base_manifest=transfer_manifest,
        label="source", operation="transfer",
    )


@pytest.fixture
def offload_source_manifest(sample_dir):
    """Prehash-style in-memory manifest as produced by offload.prehash_source."""
    result = {}
    for f in sorted(sample_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(sample_dir).as_posix()
            data = f.read_bytes()
            result[rel] = {
                "size": len(data),
                "checksum": hashlib.sha256(data).hexdigest(),
                "algorithm": "sha256",
            }
    return result


@pytest.fixture
def offload_manifest(sample_dir, offload_source_manifest):
    source = OffloadSource(label="A001", path=sample_dir)
    return build_offload_manifest(source, offload_source_manifest, sample_dir)


# ---------------------------------------------------------------------------
# 1. Schema version — every writer emits SCHEMA_VERSION
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_generate_manifest(self, transfer_manifest):
        assert transfer_manifest["schema_version"] == SCHEMA_VERSION

    def test_generate_manifest_fast(self, fast_manifest):
        assert fast_manifest["schema_version"] == SCHEMA_VERSION

    def test_build_offload_manifest(self, offload_manifest):
        assert offload_manifest["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 2. Required top-level fields
# ---------------------------------------------------------------------------

class TestRequiredTopLevelFields:
    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_all_required_top_level_fields_present(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        missing = REQUIRED_TOP_LEVEL - set(manifest)
        assert not missing, f"{fixture_name} missing top-level fields: {missing}"

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_files_is_non_empty(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        assert manifest["files"], f"{fixture_name} has no file entries"

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_file_count_matches_files_dict(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        assert manifest["file_count"] == len(manifest["files"])

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_renames_is_a_list(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        assert isinstance(manifest["renames"], list)

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_checksum_context_has_algorithm(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        ctx = manifest["checksum_context"]
        assert isinstance(ctx, dict)
        assert "algorithm" in ctx
        assert ctx["algorithm"] in ("sha256", "md5", "xxhash3_64")


# ---------------------------------------------------------------------------
# 3. File entry shape — every entry has the fields consumers depend on
# ---------------------------------------------------------------------------

class TestFileEntryShape:
    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_all_required_file_fields_present(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        for rel_path, entry in manifest["files"].items():
            missing = REQUIRED_FILE_FIELDS - set(entry)
            assert not missing, (
                f"{fixture_name}[{rel_path!r}] missing file entry fields: {missing}"
            )

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_checksums_is_a_dict(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        for rel_path, entry in manifest["files"].items():
            cs = entry.get("checksums")
            assert isinstance(cs, dict), (
                f"{fixture_name}[{rel_path!r}] checksums must be a dict, got {type(cs)}"
            )

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_hash_algorithm_matches_checksums_key(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        for rel_path, entry in manifest["files"].items():
            algo = entry["hash_algorithm"]
            assert algo in entry["checksums"], (
                f"{fixture_name}[{rel_path!r}] hash_algorithm={algo!r} "
                f"not present in checksums keys: {set(entry['checksums'])}"
            )

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_checksums_values_are_non_empty_strings(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        for rel_path, entry in manifest["files"].items():
            for algo, val in entry["checksums"].items():
                assert isinstance(val, str) and val, (
                    f"{fixture_name}[{rel_path!r}] checksums[{algo!r}] is empty"
                )

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_file_keys_are_posix_relative_paths(self, request, fixture_name):
        """Keys must be relative POSIX paths — no leading slash, no Windows separators."""
        manifest = request.getfixturevalue(fixture_name)
        for rel_path in manifest["files"]:
            assert not rel_path.startswith("/"), (
                f"{fixture_name} file key is absolute: {rel_path!r}"
            )
            assert "\\" not in rel_path, (
                f"{fixture_name} file key has Windows separator: {rel_path!r}"
            )


# ---------------------------------------------------------------------------
# 4. Save → load roundtrip — schema and content preserved after disk round-trip
# ---------------------------------------------------------------------------

class TestSaveLoadRoundtrip:
    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_schema_version_survives_roundtrip(self, request, fixture_name, tmp_path):
        manifest = request.getfixturevalue(fixture_name)
        saved = save_manifest(manifest, source_dir=tmp_path, name_hint="test")
        loaded = load_manifest(saved[0])
        assert loaded["schema_version"] == SCHEMA_VERSION

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_file_keys_survive_roundtrip(self, request, fixture_name, tmp_path):
        manifest = request.getfixturevalue(fixture_name)
        saved = save_manifest(manifest, source_dir=tmp_path, name_hint="test")
        loaded = load_manifest(saved[0])
        assert set(loaded["files"]) == set(manifest["files"])

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_file_entry_fields_survive_roundtrip(self, request, fixture_name, tmp_path):
        manifest = request.getfixturevalue(fixture_name)
        saved = save_manifest(manifest, source_dir=tmp_path, name_hint="test")
        loaded = load_manifest(saved[0])
        for rel in manifest["files"]:
            loaded_entry = loaded["files"][rel]
            for field in REQUIRED_FILE_FIELDS:
                assert field in loaded_entry, (
                    f"After roundtrip, {fixture_name}[{rel!r}] missing: {field}"
                )


# ---------------------------------------------------------------------------
# 5. VerifyWorker compatibility — checksums format matches _expected_checksums
# ---------------------------------------------------------------------------

class TestVerifyWorkerCompatibility:
    """
    VerifyWorker._verify_local does:
        expected_cs = entry.get("dest_checksums") or entry.get("source_checksums")
                      or entry.get("checksums", {})
        algo = "sha256" if "sha256" in expected_cs else
               "xxhash3_64" if "xxhash3_64" in expected_cs else "md5"
        expected_val = expected_cs.get(algo) or ""

    A manifest is verify-compatible if every entry's checksums dict has at
    least one key the algo-selection logic will find, and that value is
    non-empty.
    """

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_verify_can_resolve_algo_and_value(self, request, fixture_name):
        manifest = request.getfixturevalue(fixture_name)
        for rel_path, entry in manifest["files"].items():
            cs = (entry.get("dest_checksums")
                  or entry.get("source_checksums")
                  or entry.get("checksums", {}))
            assert isinstance(cs, dict) and cs, (
                f"{fixture_name}[{rel_path!r}] has no usable checksum block"
            )
            algo = ("sha256" if "sha256" in cs else
                    "xxhash3_64" if "xxhash3_64" in cs else "md5")
            val = (cs.get(algo) or "").lower()
            assert val, (
                f"{fixture_name}[{rel_path!r}] resolved algo={algo!r} "
                f"but value is empty"
            )

    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_sha256_checksums_are_64_hex_chars(self, request, fixture_name):
        """Full 64-char sha256 in manifest (truncation is presentation-only)."""
        manifest = request.getfixturevalue(fixture_name)
        for rel_path, entry in manifest["files"].items():
            cs = entry.get("checksums", {})
            if "sha256" in cs:
                assert len(cs["sha256"]) == 64, (
                    f"{fixture_name}[{rel_path!r}] sha256 is truncated: "
                    f"{cs['sha256']!r}"
                )


# ---------------------------------------------------------------------------
# 6. three_way_diff compatibility — every manifest type is consumable
# ---------------------------------------------------------------------------

class TestThreeWayDiffCompatibility:
    @pytest.mark.parametrize("fixture_name", [
        "transfer_manifest", "fast_manifest", "offload_manifest",
    ])
    def test_base_equals_yours_equals_theirs_all_unchanged(
        self, request, fixture_name
    ):
        manifest = request.getfixturevalue(fixture_name)
        results = three_way_diff(manifest, manifest, manifest)
        non_unchanged = [r for r in results if r.state != DiffState.UNCHANGED]
        assert not non_unchanged, (
            f"{fixture_name}: expected all UNCHANGED when base==yours==theirs, "
            f"got: {[(r.path, r.state) for r in non_unchanged]}"
        )

    def test_transfer_as_merge_base_detects_local_change(
        self, sample_dir, transfer_manifest, tmp_path
    ):
        """A modified file shows LOCAL_CHANGED when diffed against the original manifest."""
        modified_dir = tmp_path / "local_copy"
        shutil.copytree(sample_dir, modified_dir)
        (modified_dir / "video.mov").write_bytes(b"different content after edit")

        yours = generate_manifest(modified_dir, label="yours")
        server = generate_manifest(sample_dir, label="server")
        results = three_way_diff(transfer_manifest, yours, server)
        states_by_path = {r.path: r.state for r in results}
        assert states_by_path.get("video.mov") == DiffState.LOCAL_CHANGED

    def test_offload_manifest_as_merge_base_no_spurious_diffs(
        self, sample_dir, offload_manifest
    ):
        """
        SCHEMA_INTEROP_SPEC acceptance test 3: using the persisted (normalized-key)
        offload manifest as a merge base and scanning the same destination should
        produce all UNCHANGED — no false LOCAL_ONLY or DELETED entries.
        """
        # Scan the same committed destination used to build the offload manifest
        scanned = generate_manifest(sample_dir, label="current")
        results = three_way_diff(offload_manifest, scanned, scanned)
        non_unchanged = [r for r in results if r.state != DiffState.UNCHANGED]
        assert not non_unchanged, (
            "Offload manifest as merge base produced unexpected diff states: "
            + str([(r.path, r.state) for r in non_unchanged])
        )


# ---------------------------------------------------------------------------
# 7. generate_manifest_fast is schema-compatible with generate_manifest
# ---------------------------------------------------------------------------

class TestFastManifestCompatibility:
    def test_file_keys_match_full_manifest(self, sample_dir, transfer_manifest):
        fast = generate_manifest_fast(sample_dir, base_manifest=transfer_manifest)
        assert set(fast["files"]) == set(transfer_manifest["files"])

    def test_checksums_match_full_manifest(self, sample_dir, transfer_manifest):
        fast = generate_manifest_fast(sample_dir, base_manifest=transfer_manifest)
        for rel in transfer_manifest["files"]:
            full_cs = transfer_manifest["files"][rel]["checksums"]
            fast_cs = fast["files"][rel]["checksums"]
            assert full_cs == fast_cs, (
                f"Checksum mismatch for {rel!r}: full={full_cs} fast={fast_cs}"
            )

    def test_scan_stats_present(self, sample_dir, transfer_manifest):
        fast = generate_manifest_fast(sample_dir, base_manifest=transfer_manifest)
        assert "scan_stats" in fast
        stats = fast["scan_stats"]
        assert "reused_from_base" in stats
        assert "rehashed" in stats

    def test_unchanged_files_reuse_base_hashes(self, sample_dir, transfer_manifest):
        fast = generate_manifest_fast(sample_dir, base_manifest=transfer_manifest)
        total = len(fast["files"])
        assert fast["scan_stats"]["reused_from_base"] == total
        assert fast["scan_stats"]["rehashed"] == 0


# ---------------------------------------------------------------------------
# 8. Offload manifest specifics (SCHEMA_INTEROP_SPEC acceptance tests 1 and 6)
# ---------------------------------------------------------------------------

class TestOffloadManifestContract:
    def test_operation_label_is_offload(self, offload_manifest):
        """Acceptance test 1: operation field must be 'offload'."""
        assert offload_manifest["operation"] == "offload"

    def test_all_entries_have_full_sha256(self, offload_manifest):
        """Acceptance test 6: persisted manifest carries 64-char sha256."""
        for rel_path, entry in offload_manifest["files"].items():
            cs = entry.get("checksums", {})
            assert "sha256" in cs, f"{rel_path!r} missing sha256 in checksums"
            assert len(cs["sha256"]) == 64, (
                f"{rel_path!r} sha256 is truncated: {cs['sha256']!r}"
            )

    def test_checksum_context_algorithm_is_sha256(self, offload_manifest):
        assert offload_manifest["checksum_context"]["algorithm"] == "sha256"

    def test_all_entries_have_modtime(self, offload_manifest, sample_dir):
        """
        modtime is populated from the committed destination file.
        With sample_dir as dest_root the files exist, so modtime must be set.
        """
        for rel_path, entry in offload_manifest["files"].items():
            assert entry.get("modtime"), (
                f"{rel_path!r} has empty modtime — stat of committed file failed"
            )

    def test_load_manifest_accepts_offload_manifest(self, offload_manifest, tmp_path):
        """Acceptance test 1: offload manifest is loadable via load_manifest."""
        saved = save_manifest(offload_manifest, source_dir=tmp_path, name_hint="A001")
        loaded = load_manifest(saved[0])
        assert loaded["schema_version"] == SCHEMA_VERSION
        assert loaded["operation"] == "offload"
        assert set(loaded["files"]) == set(offload_manifest["files"])

    def test_normalized_entries_carry_original_filename(
        self, sample_dir, tmp_path
    ):
        """
        When normalization renames a file, the manifest entry preserves
        original_filename so the chain-of-custody record is complete.
        """
        source_manifest = {}
        norm_renames = []
        for f in sorted(sample_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(sample_dir).as_posix()
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            suffix = sha[:8]
            stem, ext = rel.rsplit(".", 1) if "." in rel else (rel, "")
            normalized = f"{stem}_{suffix}.{ext}" if ext else f"{stem}_{suffix}"
            source_manifest[normalized] = {
                "size": len(data),
                "checksum": sha,
                "algorithm": "sha256",
                "original_filename": f.name,
                "filename_hash_suffix": suffix,
                "hash_method": "sha256_prefix8",
            }
            norm_renames.append({"original": f.name, "normalized": normalized})

        norm_block = {
            "applied": True,
            "method": "sha256_prefix8",
            "renames": norm_renames,
        }
        source = OffloadSource(label="A001", path=sample_dir)
        manifest = build_offload_manifest(source, source_manifest, sample_dir, norm_block)

        for rel_path, entry in manifest["files"].items():
            assert "original_filename" in entry, (
                f"{rel_path!r} missing original_filename after normalization"
            )
