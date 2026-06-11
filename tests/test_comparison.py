"""
Tests for core/comparison.py — three-way diff logic and rename collapse.

Each test name encodes the (base, yours, server) presence pattern and
the expected DiffState.  A file entry with checksum "A" means that
version; "B" means a different version.
"""

import pytest
from core.comparison import DiffState, DiffResult, three_way_diff, _is_ignored


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
