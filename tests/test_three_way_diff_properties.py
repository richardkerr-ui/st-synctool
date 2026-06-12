"""
Layer 3: Property-based tests for three_way_diff rename collapse logic.

Uses hypothesis to generate random manifest triples and assert structural
invariants that should hold for all valid inputs:

  - No duplicate output paths
  - Every path in the output was a key in at least one of the three manifests
  - Total output count never exceeds the total union of input keys
  - UNCHANGED when base == yours == server
  - Rename collapse: a path in rename_map with a collapsible state becomes RENAMED
  - Suppressed originals never appear in output
  - No RENAMED entry is also in collapsed_paths (no double-entry for same rename event)
"""

import string
from hypothesis import given, assume, settings, HealthCheck
from hypothesis import strategies as st
import pytest

from core.comparison import three_way_diff, DiffState


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_SAFE_CHARS = string.ascii_lowercase + string.digits + "_"

@st.composite
def rel_path(draw):
    """Generate a simple relative posix path like 'foo/bar_1.mov'."""
    name = draw(st.text(alphabet=_SAFE_CHARS, min_size=1, max_size=12))
    # Occasional subdirectory prefix
    use_subdir = draw(st.booleans())
    if use_subdir:
        subdir = draw(st.text(alphabet=_SAFE_CHARS, min_size=1, max_size=6))
        return f"{subdir}/{name}"
    return name


@st.composite
def checksum(draw):
    """Generate a hex string of length 64 (sha256-like)."""
    return draw(st.text(alphabet="0123456789abcdef", min_size=64, max_size=64))


@st.composite
def file_entry(draw):
    cs = draw(checksum())
    return {
        "type": "file",
        "size": draw(st.integers(min_value=1, max_value=10_000_000)),
        "checksums": {"sha256": cs},
        "hash_algorithm": "sha256",
        "modtime": "2026-01-01T00:00:00Z",
    }


@st.composite
def manifest_files(draw, paths):
    """For each path in `paths`, optionally include a file entry (simulating presence/absence)."""
    return {
        p: draw(file_entry())
        for p in paths
        if draw(st.booleans())
    }


@st.composite
def rename_entry(draw, from_path, to_path):
    return {"from": from_path, "to": to_path, "reason": draw(st.sampled_from(["preserve", "normalize"]))}


@st.composite
def manifest_triple(draw):
    """
    Generate (base, yours, server) manifests with optional renames[] in base.

    Ensures:
    - All three manifests share the same path universe
    - renames[].from and .to are drawn from that universe
    - from != to for each rename entry
    """
    paths = draw(st.frozensets(rel_path(), min_size=1, max_size=10))
    paths = sorted(paths)

    base_files   = draw(manifest_files(paths))
    yours_files  = draw(manifest_files(paths))
    server_files = draw(manifest_files(paths))

    # Optional rename entries — from and to must both be in the path universe
    renames = []
    if len(paths) >= 2:
        num_renames = draw(st.integers(min_value=0, max_value=min(3, len(paths) // 2)))
        path_pairs = draw(
            st.lists(
                st.tuples(
                    st.sampled_from(paths),
                    st.sampled_from(paths),
                ).filter(lambda pair: pair[0] != pair[1]),
                min_size=num_renames,
                max_size=num_renames,
                unique_by=lambda pair: (pair[0], pair[1]),
            )
        )
        for from_p, to_p in path_pairs:
            renames.append(draw(rename_entry(from_p, to_p)))

    base   = {"files": base_files,   "renames": renames}
    yours  = {"files": yours_files,  "renames": []}
    server = {"files": server_files, "renames": []}
    return base, yours, server


# ---------------------------------------------------------------------------
# Invariant helpers
# ---------------------------------------------------------------------------

_COLLAPSIBLE = frozenset({
    DiffState.SERVER_ONLY, DiffState.DELETED_SERVER,
    DiffState.LOCAL_ONLY,  DiffState.DELETED_LOCAL,
})


def _rename_map(base):
    return {
        r["to"]: r
        for r in base.get("renames", [])
        if r.get("to") and r.get("from")
    }


def _expected_collapsed_paths(base, results_before_collapse):
    rm = _rename_map(base)
    collapsed = set()
    for r in results_before_collapse:
        if r.path in rm and r.state in _COLLAPSIBLE:
            collapsed.add(rm[r.path]["from"])
    return collapsed


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_no_duplicate_output_paths(triple):
    base, yours, server = triple
    results = three_way_diff(base, yours, server)
    paths = [r.path for r in results]
    assert len(paths) == len(set(paths)), (
        f"Duplicate paths in output: {[p for p in paths if paths.count(p) > 1]}"
    )


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_output_paths_are_subset_of_input_union(triple):
    base, yours, server = triple
    all_input = set(base["files"]) | set(yours["files"]) | set(server["files"])
    results = three_way_diff(base, yours, server)
    for r in results:
        assert r.path in all_input, (
            f"Output path {r.path!r} not in any input manifest"
        )


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_output_count_never_exceeds_input_union(triple):
    base, yours, server = triple
    all_input = set(base["files"]) | set(yours["files"]) | set(server["files"])
    results = three_way_diff(base, yours, server)
    # Ignore ignored paths (st_manifest.json etc.) from the bound
    from core.comparison import _is_ignored
    countable = sum(1 for p in all_input if not _is_ignored(p))
    assert len(results) <= countable, (
        f"Output ({len(results)}) exceeds input union ({countable})"
    )


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_renamed_paths_not_in_raw_collapsible_states(triple):
    """Any path that ends up as RENAMED must not also appear as SERVER_ONLY etc."""
    base, yours, server = triple
    results = three_way_diff(base, yours, server)
    state_by_path = {r.path: r.state for r in results}
    for r in results:
        if r.state == DiffState.RENAMED:
            assert state_by_path[r.path] not in _COLLAPSIBLE, (
                f"RENAMED path {r.path!r} still appears as {state_by_path[r.path]}"
            )


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_suppressed_originals_absent_from_output(triple):
    """Paths collapsed as the 'from' side of a rename must not appear in output."""
    base, yours, server = triple
    rm = _rename_map(base)
    results = three_way_diff(base, yours, server)

    # Determine which 'from' paths were actually collapsed
    renamed_paths = {r.path for r in results if r.state == DiffState.RENAMED}
    collapsed_froms = {rm[p]["from"] for p in renamed_paths if p in rm}
    output_paths = {r.path for r in results}

    for orig in collapsed_froms:
        assert orig not in output_paths, (
            f"Collapsed original {orig!r} still in output (should be suppressed)"
        )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_renamed_entry_has_renamed_from_set(triple):
    """Every RENAMED result must have renamed_from populated."""
    base, yours, server = triple
    results = three_way_diff(base, yours, server)
    for r in results:
        if r.state == DiffState.RENAMED:
            assert r.renamed_from is not None, (
                f"RENAMED result for {r.path!r} has renamed_from=None"
            )
            assert r.renamed_from != r.path, (
                f"RENAMED result for {r.path!r} has renamed_from == path (self-rename)"
            )


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.frozensets(rel_path(), min_size=1, max_size=8).flatmap(
        lambda paths: st.fixed_dictionaries({
            "files": st.fixed_dictionaries(
                {p: file_entry() for p in sorted(paths)}
            )
        })
    )
)
def test_identical_manifests_all_unchanged(manifest):
    """When base == yours == server, every result must be UNCHANGED."""
    results = three_way_diff(manifest, manifest, manifest)
    non_unchanged = [r for r in results if r.state != DiffState.UNCHANGED]
    assert not non_unchanged, (
        "Expected all UNCHANGED when base==yours==server, got: "
        + str([(r.path, r.state) for r in non_unchanged])
    )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_state_coverage_complete(triple):
    """Every result state is a known DiffState (no None, no unexpected values)."""
    base, yours, server = triple
    results = three_way_diff(base, yours, server)
    valid_states = set(DiffState)
    for r in results:
        assert r.state in valid_states, (
            f"Unknown state {r.state!r} for path {r.path!r}"
        )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(manifest_triple())
def test_rename_collapse_idempotent(triple):
    """Calling three_way_diff twice on the same input gives the same result."""
    base, yours, server = triple
    first  = three_way_diff(base, yours, server)
    second = three_way_diff(base, yours, server)
    assert [(r.path, r.state) for r in first] == [(r.path, r.state) for r in second]


# ---------------------------------------------------------------------------
# Targeted rename-collapse scenario (non-random, ensures the happy path)
# ---------------------------------------------------------------------------

def _entry(cs: str) -> dict:
    return {
        "type": "file", "size": 10,
        "checksums": {"sha256": cs * 64},
        "hash_algorithm": "sha256",
        "modtime": "2026-01-01T00:00:00Z",
    }


def test_rename_collapse_server_side():
    """
    Base has 'old.mov'. Server has 'new.mov' (rename to new.mov happened server-side).
    base.renames = [{from: old.mov, to: new.mov}].
    Expected: new.mov -> RENAMED (renamed_from=old.mov), old.mov suppressed.
    """
    cs = "a" * 64
    base   = {"files": {"old.mov": _entry(cs)},   "renames": [{"from": "old.mov", "to": "new.mov"}]}
    yours  = {"files": {},                          "renames": []}
    server = {"files": {"new.mov": _entry(cs)},    "renames": []}

    results = three_way_diff(base, yours, server)
    state_by_path = {r.path: r for r in results}

    assert "new.mov" in state_by_path
    assert state_by_path["new.mov"].state == DiffState.RENAMED
    assert state_by_path["new.mov"].renamed_from == "old.mov"
    assert "old.mov" not in state_by_path


def test_rename_collapse_local_side():
    """
    Base has 'edit.prproj'. Local has 'edit_2026-06-10-rk.prproj' (preserve rename during push).
    base.renames = [{from: edit.prproj, to: edit_2026-06-10-rk.prproj}].
    Expected: renamed path -> RENAMED, original suppressed.
    """
    cs = "b" * 64
    orig = "edit.prproj"
    renamed = "edit_2026-06-10-rk.prproj"
    base   = {"files": {orig: _entry(cs)},     "renames": [{"from": orig, "to": renamed}]}
    yours  = {"files": {renamed: _entry(cs)},  "renames": []}
    server = {"files": {},                      "renames": []}

    results = three_way_diff(base, yours, server)
    state_by_path = {r.path: r for r in results}

    assert renamed in state_by_path
    assert state_by_path[renamed].state == DiffState.RENAMED
    assert state_by_path[renamed].renamed_from == orig
    assert orig not in state_by_path


def test_no_rename_entry_keeps_raw_state():
    """A file present only on server with no rename entry stays SERVER_ONLY."""
    cs = "c" * 64
    base   = {"files": {},                      "renames": []}
    yours  = {"files": {},                      "renames": []}
    server = {"files": {"clip.mp4": _entry(cs)},"renames": []}

    results = three_way_diff(base, yours, server)
    state_by_path = {r.path: r.state for r in results}
    assert state_by_path.get("clip.mp4") == DiffState.SERVER_ONLY


def test_rename_map_does_not_collapse_unchanged_state():
    """
    If the 'to' path is UNCHANGED (exists in all three), it must NOT become RENAMED.
    The rename-collapse only fires on SERVER_ONLY, LOCAL_ONLY, DELETED_SERVER, DELETED_LOCAL.
    """
    cs = "d" * 64
    orig = "a.mov"
    new  = "b.mov"
    # new.mov exists in all three — it is UNCHANGED, not a rename target
    base   = {"files": {orig: _entry(cs), new: _entry(cs)}, "renames": [{"from": orig, "to": new}]}
    yours  = {"files": {orig: _entry(cs), new: _entry(cs)}, "renames": []}
    server = {"files": {orig: _entry(cs), new: _entry(cs)}, "renames": []}

    results = three_way_diff(base, yours, server)
    state_by_path = {r.path: r.state for r in results}

    # new.mov is UNCHANGED; rename_map entry for it should not convert it to RENAMED
    assert state_by_path.get("b.mov") == DiffState.UNCHANGED
