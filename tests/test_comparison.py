"""
Tests for core/comparison.py — three-way diff logic and rename collapse.

Each test name encodes the (base, yours, server) presence pattern and
the expected DiffState.  A file entry with checksum "A" means that
version; "B" means a different version.
"""

import pytest
from core.comparison import DiffState, DiffResult, three_way_diff, _is_ignored, is_ignored_path, conflict_suggested_action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(checksum: str, size: int = 100) -> dict:
    return {"checksums": {"sha256": checksum}, "size": size}


def _manifest(*files: tuple) -> dict:
    """Build a minimal manifest dict from (path, checksum) pairs."""
    return {"files": {path: _entry(cs) for path, cs in files}}


def _diff(base_files, yours_files, server_files) -> dict[str, DiffState]:
    base   = {"files": {p: _entry(c) for p, c in base_files.items()}}
    yours  = {"files": {p: _entry(c) for p, c in yours_files.items()}}
    server = {"files": {p: _entry(c) for p, c in server_files.items()}}
    results = three_way_diff(base, yours, server)
    return {r.path: r.state for r in results}


# ---------------------------------------------------------------------------
# All-three-present cases
# ---------------------------------------------------------------------------

class TestAllPresent:
    def test_unchanged_all_same(self):
        # three_way_diff returns UNCHANGED entries; filtering is the GUI's job
        states = _diff({"f.mov": "A"}, {"f.mov": "A"}, {"f.mov": "A"})
        assert states["f.mov"] == DiffState.UNCHANGED

    def test_unchanged_in_results(self):
        results = three_way_diff(
            {"files": {"f.mov": _entry("A")}},
            {"files": {"f.mov": _entry("A")}},
            {"files": {"f.mov": _entry("A")}},
        )
        assert any(r.state == DiffState.UNCHANGED for r in results)

    def test_local_changed_only_yours_differs(self):
        states = _diff({"f.mov": "A"}, {"f.mov": "B"}, {"f.mov": "A"})
        assert states["f.mov"] == DiffState.LOCAL_CHANGED

    def test_server_changed_only_server_differs(self):
        states = _diff({"f.mov": "A"}, {"f.mov": "A"}, {"f.mov": "B"})
        assert states["f.mov"] == DiffState.SERVER_CHANGED

    def test_both_changed_when_both_differ_from_base(self):
        states = _diff({"f.mov": "A"}, {"f.mov": "B"}, {"f.mov": "C"})
        assert states["f.mov"] == DiffState.BOTH_CHANGED

    def test_both_changed_when_both_same_but_differ_from_base(self):
        # Both sides independently changed to the same value — still BOTH_CHANGED
        # because UNCHANGED requires all three to match.  Without a base we'd
        # call this UNCHANGED (handled in no-base case below).
        states = _diff({"f.mov": "A"}, {"f.mov": "B"}, {"f.mov": "B"})
        assert states["f.mov"] == DiffState.BOTH_CHANGED


# ---------------------------------------------------------------------------
# Missing-base cases (new files, no prior manifest entry)
# ---------------------------------------------------------------------------

class TestNoBase:
    def test_local_only(self):
        states = _diff({}, {"f.mov": "A"}, {})
        assert states["f.mov"] == DiffState.LOCAL_ONLY

    def test_server_only(self):
        states = _diff({}, {}, {"f.mov": "A"})
        assert states["f.mov"] == DiffState.SERVER_ONLY

    def test_no_base_both_same_is_unchanged(self):
        # File exists on both sides with same checksum, never in base — treat as synced
        states = _diff({}, {"f.mov": "A"}, {"f.mov": "A"})
        assert states["f.mov"] == DiffState.UNCHANGED

    def test_no_base_both_different_is_conflict(self):
        states = _diff({}, {"f.mov": "A"}, {"f.mov": "B"})
        assert states["f.mov"] == DiffState.BOTH_CHANGED


# ---------------------------------------------------------------------------
# Deletion cases
# ---------------------------------------------------------------------------

class TestDeletions:
    def test_deleted_both(self):
        states = _diff({"f.mov": "A"}, {}, {})
        assert states["f.mov"] == DiffState.DELETED_BOTH

    def test_deleted_local(self):
        states = _diff({"f.mov": "A"}, {}, {"f.mov": "A"})
        assert states["f.mov"] == DiffState.DELETED_LOCAL

    def test_deleted_server(self):
        states = _diff({"f.mov": "A"}, {"f.mov": "A"}, {})
        assert states["f.mov"] == DiffState.DELETED_SERVER


# ---------------------------------------------------------------------------
# Rename collapse (item 14)
# ---------------------------------------------------------------------------

class TestRenameCollapse:
    def _base_with_rename(self, from_path: str, to_path: str, cs: str = "A") -> dict:
        return {
            "files": {from_path: _entry(cs)},
            "renames": [{"from": from_path, "to": to_path}],
        }

    def test_server_only_renamed_file_becomes_renamed(self):
        # After a push-rename: server has the new name, local has deleted the old
        base   = self._base_with_rename("orig.mov", "orig_2026-01-01-rk.mov")
        yours  = {"files": {}}  # orig.mov was on local, now gone post-push
        server = {"files": {"orig_2026-01-01-rk.mov": _entry("A")}}
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["orig_2026-01-01-rk.mov"].state == DiffState.RENAMED
        assert results["orig_2026-01-01-rk.mov"].renamed_from == "orig.mov"
        assert "orig.mov" not in results

    def test_local_only_renamed_file_becomes_renamed(self):
        base   = self._base_with_rename("orig.mov", "orig_2026-01-01-rk.mov")
        yours  = {"files": {"orig_2026-01-01-rk.mov": _entry("A")}}
        server = {"files": {}}
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["orig_2026-01-01-rk.mov"].state == DiffState.RENAMED

    def test_non_rename_server_only_stays_server_only(self):
        # A SERVER_ONLY file that is NOT in renames[] must not be collapsed
        base   = self._base_with_rename("other.mov", "other_renamed.mov")
        yours  = {"files": {}}
        server = {"files": {"new_unrelated.mov": _entry("Z")}}
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["new_unrelated.mov"].state == DiffState.SERVER_ONLY

    def test_both_changed_rename_target_not_collapsed(self):
        # If the rename target has a conflict it should NOT be silently collapsed
        base   = self._base_with_rename("orig.mov", "orig_rk.mov")
        yours  = {"files": {"orig_rk.mov": _entry("B")}}   # different checksum
        server = {"files": {"orig_rk.mov": _entry("A")}}   # original checksum
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        # BOTH_CHANGED (B vs A) is not SERVER_ONLY/LOCAL_ONLY/DELETED — must not collapse
        assert results["orig_rk.mov"].state != DiffState.RENAMED

    def test_deleted_server_renamed_file_becomes_renamed(self):
        # Rename target present locally but deleted on server → DELETED_SERVER → RENAMED
        base   = self._base_with_rename("orig.mov", "orig_rk.mov")
        yours  = {"files": {"orig.mov": _entry("A"), "orig_rk.mov": _entry("A")}}
        server = {"files": {"orig.mov": _entry("A")}}  # orig_rk.mov absent on server
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["orig_rk.mov"].state == DiffState.RENAMED
        assert results["orig_rk.mov"].renamed_from == "orig.mov"

    def test_deleted_local_renamed_file_becomes_renamed(self):
        # Rename target present on server but deleted locally → DELETED_LOCAL → RENAMED
        base   = self._base_with_rename("orig.mov", "orig_rk.mov")
        yours  = {"files": {"orig.mov": _entry("A")}}  # orig_rk.mov absent locally
        server = {"files": {"orig.mov": _entry("A"), "orig_rk.mov": _entry("A")}}
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["orig_rk.mov"].state == DiffState.RENAMED
        assert results["orig_rk.mov"].renamed_from == "orig.mov"

    def test_unchanged_rename_target_not_collapsed(self):
        # UNCHANGED is not one of the four collapsible states; must stay UNCHANGED
        base   = self._base_with_rename("orig.mov", "orig_rk.mov")
        yours  = {"files": {"orig.mov": _entry("A"), "orig_rk.mov": _entry("A")}}
        server = {"files": {"orig.mov": _entry("A"), "orig_rk.mov": _entry("A")}}
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["orig_rk.mov"].state == DiffState.UNCHANGED

    def test_two_pass_rename_to_sorts_before_rename_from(self):
        # "aaa_rk.mov" (renamed-to) sorts before "zzz.mov" (renamed-from).
        # The two-pass logic must still suppress "zzz.mov" even though it is
        # encountered after "aaa_rk.mov" in sorted order.
        base = {
            "files": {"zzz.mov": _entry("A")},
            "renames": [{"from": "zzz.mov", "to": "aaa_rk.mov"}],
        }
        yours  = {"files": {"aaa_rk.mov": _entry("A")}}
        server = {"files": {}}
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["aaa_rk.mov"].state == DiffState.RENAMED
        assert "zzz.mov" not in results

    def test_empty_rename_map_returns_early_no_collapse(self):
        # No renames key in base — the collapse pass is skipped entirely.
        # A SERVER_ONLY file must remain SERVER_ONLY.
        base   = {"files": {}}
        yours  = {"files": {}}
        server = {"files": {"new.mov": _entry("A")}}
        results = {r.path: r for r in three_way_diff(base, yours, server)}
        assert results["new.mov"].state == DiffState.SERVER_ONLY


# ---------------------------------------------------------------------------
# Ignored files
# ---------------------------------------------------------------------------

class TestIgnoredFiles:
    @pytest.mark.parametrize("path", [
        "st_manifest.json",
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        "_contact_sheet_20260609.pdf",
        "subdir/_contact_sheet_abc.pdf",
        ".st_staging_20260609/file.mov",
        "_thumbnails/frame1.jpg",
        "proj/_thumbnails/frame2.jpg",
    ])
    def test_ignored_files_not_in_diff(self, path):
        base   = {"files": {path: _entry("A")}}
        yours  = {"files": {path: _entry("B")}}
        server = {"files": {path: _entry("C")}}
        results = three_way_diff(base, yours, server)
        assert results == []

    def test_real_files_not_ignored(self):
        states = _diff({"clip.mov": "A"}, {"clip.mov": "B"}, {"clip.mov": "A"})
        assert "clip.mov" in states


# ---------------------------------------------------------------------------
# Checksum algorithm fallback in _cs
# ---------------------------------------------------------------------------

class TestChecksumFallback:
    def test_sha256_preferred_over_md5(self):
        # When both sha256 and md5 are present, sha256 governs the diff outcome
        def _dual_entry(sha, md5):
            return {"checksums": {"sha256": sha, "md5": md5}, "size": 1}

        base   = {"files": {"f.mov": _dual_entry("A", "X")}}
        yours  = {"files": {"f.mov": _dual_entry("A", "Y")}}  # md5 differs, sha256 same
        server = {"files": {"f.mov": _dual_entry("A", "Z")}}
        results = {r.path: r.state for r in three_way_diff(base, yours, server)}
        # sha256 matches on all three → UNCHANGED (not LOCAL_CHANGED from md5 drift)
        assert results["f.mov"] == DiffState.UNCHANGED

    def test_md5_used_when_no_sha256(self):
        def _md5_entry(cs):
            return {"checksums": {"md5": cs}, "size": 1}

        base   = {"files": {"f.mov": _md5_entry("A")}}
        yours  = {"files": {"f.mov": _md5_entry("B")}}
        server = {"files": {"f.mov": _md5_entry("A")}}
        results = {r.path: r.state for r in three_way_diff(base, yours, server)}
        assert results["f.mov"] == DiffState.LOCAL_CHANGED

    def test_missing_checksums_are_equal_to_each_other(self):
        # An entry with no checksums: _cs returns None.
        # None == None in Python, so three None values → UNCHANGED.
        # This documents the current behaviour — callers should ensure entries
        # always carry at least one checksum algorithm.
        base   = {"files": {"f.mov": {"size": 1}}}
        yours  = {"files": {"f.mov": {"size": 1}}}
        server = {"files": {"f.mov": {"size": 1}}}
        results = {r.path: r.state for r in three_way_diff(base, yours, server)}
        assert results["f.mov"] == DiffState.UNCHANGED


# ---------------------------------------------------------------------------
# Edge cases — empty inputs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_all_empty_manifests_produce_empty_result(self):
        # All three sides have no files — result must be an empty list.
        results = three_way_diff({"files": {}}, {"files": {}}, {"files": {}})
        assert results == []

    def test_missing_files_key_produces_empty_result(self):
        # Manifests without a "files" key at all should also yield nothing.
        results = three_way_diff({}, {}, {})
        assert results == []


# ---------------------------------------------------------------------------
# is_ignored_path — public alias for _is_ignored
# ---------------------------------------------------------------------------

class TestIsIgnoredPath:
    # Happy path: well-known ignored filenames at the root
    def test_manifest_file_is_ignored(self):
        assert is_ignored_path("st_manifest.json") is True

    def test_ds_store_is_ignored(self):
        assert is_ignored_path(".DS_Store") is True

    def test_thumbs_db_is_ignored(self):
        assert is_ignored_path("Thumbs.db") is True

    def test_desktop_ini_is_ignored(self):
        assert is_ignored_path("desktop.ini") is True

    # Happy path: ignored prefixes
    def test_contact_sheet_prefix_is_ignored(self):
        assert is_ignored_path("_contact_sheet_20260101.pdf") is True

    def test_st_staging_prefix_is_ignored(self):
        assert is_ignored_path(".st_staging_20260101/some_file.mov") is True

    def test_st_failure_prefix_is_ignored(self):
        assert is_ignored_path(".st_failure_report.txt") is True

    def test_st_offload_prefix_is_ignored(self):
        assert is_ignored_path(".st_offload_metadata.json") is True

    # Happy path: ignored directory segments
    def test_thumbnails_dir_segment_is_ignored(self):
        assert is_ignored_path("project/_thumbnails/frame001.jpg") is True

    def test_thumbnails_at_root_is_ignored(self):
        assert is_ignored_path("_thumbnails/frame001.jpg") is True

    # Ignored prefix appearing as a mid-path directory segment
    def test_contact_sheet_dir_segment_is_ignored(self):
        assert is_ignored_path("some/dir/_contact_sheet_abc/page1.jpg") is True

    # Failure path: normal media files must NOT be ignored
    def test_regular_mov_not_ignored(self):
        assert is_ignored_path("project/clip.mov") is False

    def test_regular_jpg_not_ignored(self):
        assert is_ignored_path("photos/hero.jpg") is False

    def test_nested_real_file_not_ignored(self):
        assert is_ignored_path("a/b/c/document.pdf") is False

    # Edge cases
    def test_empty_string_not_ignored(self):
        # An empty path has no name match and no prefix match
        assert is_ignored_path("") is False

    def test_windows_separator_thumbnails_is_ignored(self):
        # Backslash paths (e.g. from a Windows manifest) must still be handled
        assert is_ignored_path("project\\_thumbnails\\frame001.jpg") is True

    def test_partial_prefix_match_not_ignored(self):
        # "contact_sheet_" without the leading underscore is NOT a match
        assert is_ignored_path("contact_sheet_summary.pdf") is False

    def test_name_containing_ignored_word_not_at_start_not_ignored(self):
        # "my_thumbnails" is not the exact directory name "_thumbnails"
        assert is_ignored_path("my_thumbnails/frame.jpg") is False


# ---------------------------------------------------------------------------
# conflict_suggested_action
# ---------------------------------------------------------------------------

class TestConflictSuggestedAction:
    # Helper: build a minimal DiffResult for BOTH_CHANGED with given modtimes
    @staticmethod
    def _conflict(local_mt, server_mt):
        return DiffResult(
            path="clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry={"modtime": local_mt} if local_mt is not None else {},
            server_entry={"modtime": server_mt} if server_mt is not None else {},
        )

    # Happy path: local is newer
    def test_local_newer_suggests_push(self):
        result = self._conflict("2026-06-12T10:00:00Z", "2026-06-10T08:00:00Z")
        assert conflict_suggested_action(result) == "Push to Server"

    # Happy path: server is newer
    def test_server_newer_suggests_pull(self):
        result = self._conflict("2026-06-10T08:00:00Z", "2026-06-12T10:00:00Z")
        assert conflict_suggested_action(result) == "Pull from Server"

    # Happy path: identical modtimes -> Skip
    def test_equal_modtimes_suggests_skip(self):
        result = self._conflict("2026-06-10T08:00:00Z", "2026-06-10T08:00:00Z")
        assert conflict_suggested_action(result) == "Skip"

    # Failure path: missing local modtime
    def test_missing_local_modtime_suggests_skip(self):
        result = DiffResult(
            path="clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry={},
            server_entry={"modtime": "2026-06-10T08:00:00Z"},
        )
        assert conflict_suggested_action(result) == "Skip"

    # Failure path: missing server modtime
    def test_missing_server_modtime_suggests_skip(self):
        result = DiffResult(
            path="clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry={"modtime": "2026-06-10T08:00:00Z"},
            server_entry={},
        )
        assert conflict_suggested_action(result) == "Skip"

    # Failure path: both entries are None
    def test_none_entries_suggests_skip(self):
        result = DiffResult(
            path="clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry=None,
            server_entry=None,
        )
        assert conflict_suggested_action(result) == "Skip"

    # Failure path: uncomparable modtime types raise internally -> Skip
    def test_incomparable_modtime_types_suggests_skip(self):
        result = DiffResult(
            path="clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry={"modtime": 12345},
            server_entry={"modtime": "2026-06-10T08:00:00Z"},
        )
        # int vs str comparison raises TypeError; the except block returns Skip
        assert conflict_suggested_action(result) == "Skip"

    # Edge case: called on a non-BOTH_CHANGED row
    def test_non_conflict_state_always_returns_skip(self):
        for state in (
            DiffState.UNCHANGED,
            DiffState.LOCAL_ONLY,
            DiffState.SERVER_ONLY,
            DiffState.LOCAL_CHANGED,
            DiffState.SERVER_CHANGED,
            DiffState.DELETED_LOCAL,
            DiffState.DELETED_SERVER,
            DiffState.DELETED_BOTH,
            DiffState.RENAMED,
        ):
            result = DiffResult(
                path="clip.mov",
                state=state,
                yours_entry={"modtime": "2026-06-12T10:00:00Z"},
                server_entry={"modtime": "2026-01-01T00:00:00Z"},
            )
            assert conflict_suggested_action(result) == "Skip", f"expected Skip for {state}"

    # Edge case: integer modtimes (unix timestamps) work correctly
    def test_integer_modtimes_local_newer(self):
        result = self._conflict(1_700_000_100, 1_700_000_000)
        assert conflict_suggested_action(result) == "Push to Server"

    def test_integer_modtimes_server_newer(self):
        result = self._conflict(1_700_000_000, 1_700_000_100)
        assert conflict_suggested_action(result) == "Pull from Server"
