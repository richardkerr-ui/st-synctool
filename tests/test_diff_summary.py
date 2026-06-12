"""Tests for core/diff_summary.py (M2 merge summary header).

Covers every DiffState with its default action, action overrides from the
GUI combos and the rendered header text.
"""

import pytest

from core.comparison import DiffResult, DiffState
from core.diff_summary import (
    ACTION_OPTIONS_BY_STATE, DiffSummary, default_action, summarize_diff,
)
from core.merge_ops import (
    ACT_PUSH, ACT_PULL, ACT_DELETE_LOCAL, ACT_DELETE_SERVER, ACT_SKIP,
)


def _row(state, path="f.mov", local_mt=None, server_mt=None):
    yours = {"modtime": local_mt} if local_mt else None
    server = {"modtime": server_mt} if server_mt else None
    return DiffResult(path=path, state=state, yours_entry=yours, server_entry=server)


# ---------------------------------------------------------------------------
# Default actions per state
# ---------------------------------------------------------------------------

class TestDefaultAction:
    @pytest.mark.parametrize("state,expected", [
        (DiffState.LOCAL_ONLY,     ACT_PUSH),
        (DiffState.SERVER_ONLY,    ACT_PULL),
        (DiffState.LOCAL_CHANGED,  ACT_PUSH),
        (DiffState.SERVER_CHANGED, ACT_PULL),
        (DiffState.DELETED_LOCAL,  ACT_SKIP),
        (DiffState.DELETED_SERVER, ACT_SKIP),
        (DiffState.DELETED_BOTH,   ACT_SKIP),
        (DiffState.RENAMED,        ACT_SKIP),
    ])
    def test_static_defaults_match_first_option(self, state, expected):
        assert default_action(_row(state)) == expected
        assert ACTION_OPTIONS_BY_STATE[state.name][0] == expected

    def test_conflict_default_is_skip_without_modtimes(self):
        assert default_action(_row(DiffState.BOTH_CHANGED)) == ACT_SKIP

    def test_conflict_default_push_when_local_newer(self):
        r = _row(DiffState.BOTH_CHANGED, local_mt="2026-06-02", server_mt="2026-06-01")
        assert default_action(r) == ACT_PUSH

    def test_conflict_default_pull_when_server_newer(self):
        r = _row(DiffState.BOTH_CHANGED, local_mt="2026-06-01", server_mt="2026-06-02")
        assert default_action(r) == ACT_PULL

    def test_unknown_state_falls_back_to_skip(self):
        assert default_action(_row(DiffState.UNCHANGED)) == ACT_SKIP


# ---------------------------------------------------------------------------
# Counting: every DiffState with default actions
# ---------------------------------------------------------------------------

class TestSummarizeDefaults:
    def test_unchanged_counted_separately(self):
        s = summarize_diff([_row(DiffState.UNCHANGED)])
        assert s.unchanged == 1 and s.total == 1
        assert s.syncing == 0 and s.skipped == 0

    @pytest.mark.parametrize("state", [
        DiffState.LOCAL_ONLY, DiffState.SERVER_ONLY,
        DiffState.LOCAL_CHANGED, DiffState.SERVER_CHANGED,
    ])
    def test_default_push_pull_states_count_as_syncing(self, state):
        s = summarize_diff([_row(state)])
        assert s.syncing == 1

    def test_unresolved_conflict_counts_as_needing_review(self):
        s = summarize_diff([_row(DiffState.BOTH_CHANGED)])
        assert s.conflicts_total == 1 and s.conflicts_unresolved == 1
        assert s.syncing == 0

    def test_mtime_resolved_conflict_counts_as_syncing(self):
        r = _row(DiffState.BOTH_CHANGED, local_mt="2026-06-02", server_mt="2026-06-01")
        s = summarize_diff([r])
        assert s.conflicts_total == 1 and s.conflicts_unresolved == 0
        assert s.syncing == 1

    @pytest.mark.parametrize("state", [DiffState.DELETED_LOCAL, DiffState.DELETED_SERVER])
    def test_one_sided_deletions_held_by_default(self, state):
        s = summarize_diff([_row(state)])
        assert s.deletions_held == 1 and s.deletions_to_apply == 0

    def test_deleted_both_is_skipped_not_held(self):
        s = summarize_diff([_row(DiffState.DELETED_BOTH)])
        assert s.deletions_held == 0 and s.skipped == 1

    def test_renamed_default_is_skipped(self):
        s = summarize_diff([_row(DiffState.RENAMED)])
        assert s.skipped == 1

    def test_mixed_population(self):
        rows = [
            _row(DiffState.UNCHANGED, path="u1"),
            _row(DiffState.UNCHANGED, path="u2"),
            _row(DiffState.LOCAL_ONLY, path="a"),
            _row(DiffState.SERVER_CHANGED, path="b"),
            _row(DiffState.BOTH_CHANGED, path="c"),
            _row(DiffState.DELETED_LOCAL, path="d"),
        ]
        s = summarize_diff(rows)
        assert s.total == 6 and s.unchanged == 2
        assert s.syncing == 2
        assert s.conflicts_unresolved == 1
        assert s.deletions_held == 1


# ---------------------------------------------------------------------------
# Action overrides (what the GUI combos send back)
# ---------------------------------------------------------------------------

class TestActionOverrides:
    def test_resolving_conflict_moves_it_to_syncing(self):
        rows = [_row(DiffState.BOTH_CHANGED, path="c")]
        s = summarize_diff(rows, {"c": ACT_PUSH})
        assert s.conflicts_unresolved == 0 and s.syncing == 1

    def test_unresolving_conflict_back_to_review(self):
        r = _row(DiffState.BOTH_CHANGED, path="c",
                 local_mt="2026-06-02", server_mt="2026-06-01")
        s = summarize_diff([r], {"c": ACT_SKIP})
        assert s.conflicts_unresolved == 1 and s.syncing == 0

    def test_delete_override_counts_as_deletion_to_apply(self):
        rows = [_row(DiffState.LOCAL_ONLY, path="a")]
        s = summarize_diff(rows, {"a": ACT_DELETE_LOCAL})
        assert s.deletions_to_apply == 1 and s.syncing == 0

    def test_held_deletion_becomes_applied_when_chosen(self):
        rows = [_row(DiffState.DELETED_LOCAL, path="d")]
        s = summarize_diff(rows, {"d": ACT_DELETE_SERVER})
        assert s.deletions_to_apply == 1 and s.deletions_held == 0

    def test_skip_override_moves_sync_row_to_skipped(self):
        rows = [_row(DiffState.LOCAL_ONLY, path="a")]
        s = summarize_diff(rows, {"a": ACT_SKIP})
        assert s.syncing == 0 and s.skipped == 1

    def test_rows_missing_from_actions_use_defaults(self):
        rows = [_row(DiffState.LOCAL_ONLY, path="a"), _row(DiffState.SERVER_ONLY, path="b")]
        s = summarize_diff(rows, {"a": ACT_SKIP})  # b absent -> default Pull
        assert s.skipped == 1 and s.syncing == 1


# ---------------------------------------------------------------------------
# Header text rendering
# ---------------------------------------------------------------------------

class TestToText:
    def _summary(self, **kw):
        base = dict(total=0, unchanged=0, conflicts_total=0, conflicts_unresolved=0,
                    syncing=0, deletions_held=0, deletions_to_apply=0, skipped=0)
        base.update(kw)
        return DiffSummary(**base)

    def test_roadmap_example_shape(self):
        s = self._summary(conflicts_unresolved=3, syncing=44, deletions_held=2)
        assert s.to_text() == (
            "3 conflicts need review · 44 files will sync automatically · "
            "2 deletions held for you"
        )

    def test_singular_forms(self):
        s = self._summary(conflicts_unresolved=1, syncing=1, deletions_held=1)
        assert s.to_text() == (
            "1 conflict needs review · 1 file will sync automatically · "
            "1 deletion held for you"
        )

    def test_zero_segments_omitted(self):
        s = self._summary(syncing=5)
        assert s.to_text() == "5 files will sync automatically"

    def test_deletions_to_apply_segment(self):
        s = self._summary(deletions_to_apply=2)
        assert s.to_text() == "2 deletions will be applied"

    def test_skipped_segment(self):
        s = self._summary(syncing=1, skipped=3)
        assert s.to_text() == "1 file will sync automatically · 3 files skipped"

    def test_all_unchanged(self):
        s = self._summary(total=10, unchanged=10)
        assert s.to_text() == "Everything in sync, 10 files unchanged"

    def test_single_unchanged(self):
        s = self._summary(total=1, unchanged=1)
        assert s.to_text() == "Everything in sync, 1 file unchanged"

    def test_empty_diff(self):
        assert self._summary().to_text() == "Nothing to compare"

    def test_end_to_end_text_from_rows(self):
        rows = [
            _row(DiffState.BOTH_CHANGED, path="c1"),
            _row(DiffState.LOCAL_ONLY, path="a"),
            _row(DiffState.SERVER_ONLY, path="b"),
            _row(DiffState.DELETED_LOCAL, path="d"),
        ]
        text = summarize_diff(rows).to_text()
        assert text == (
            "1 conflict needs review · 2 files will sync automatically · "
            "1 deletion held for you"
        )
