"""
Tests for Phase 3 conflict resolution logic.

Covers:
  1. mtime comparison: local newer -> suggested action is Push
  2. mtime comparison: server newer -> suggested action is Pull
  3. mtime tie -> suggested action is Skip
  4. Missing mtime on one side -> graceful fallback to Skip
  5. "Newer wins" batch logic via DiffTable.apply_newer_wins()
"""

import pytest

from core.comparison import DiffResult, DiffState, conflict_suggested_action
from core.merge_ops import ACT_PUSH, ACT_PULL, ACT_SKIP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(modtime=None, size=100, sha256="abc123"):
    entry = {"size": size, "checksums": {"sha256": sha256}}
    if modtime is not None:
        entry["modtime"] = modtime
    return entry


def _conflict(local_mt=None, server_mt=None, local_size=100, server_size=100):
    """Build a minimal BOTH_CHANGED DiffResult for testing."""
    return DiffResult(
        path="test/clip.mov",
        state=DiffState.BOTH_CHANGED,
        yours_entry=_entry(modtime=local_mt,  size=local_size,  sha256="aabbccdd"),
        server_entry=_entry(modtime=server_mt, size=server_size, sha256="11223344"),
    )


# ---------------------------------------------------------------------------
# 1. Local newer -> Push
# ---------------------------------------------------------------------------

class TestLocalNewer:
    def test_local_newer_suggests_push(self):
        r = _conflict(
            local_mt="2026-06-08T14:32:00+00:00",
            server_mt="2026-06-07T09:11:00+00:00",
        )
        assert conflict_suggested_action(r) == ACT_PUSH

    def test_local_newer_by_one_second(self):
        r = _conflict(
            local_mt="2026-06-08T10:00:01+00:00",
            server_mt="2026-06-08T10:00:00+00:00",
        )
        assert conflict_suggested_action(r) == ACT_PUSH

    def test_local_newer_different_dates(self):
        r = _conflict(
            local_mt="2026-06-10T00:00:00+00:00",
            server_mt="2026-01-01T00:00:00+00:00",
        )
        assert conflict_suggested_action(r) == ACT_PUSH


# ---------------------------------------------------------------------------
# 2. Server newer -> Pull
# ---------------------------------------------------------------------------

class TestServerNewer:
    def test_server_newer_suggests_pull(self):
        r = _conflict(
            local_mt="2026-06-07T09:11:00+00:00",
            server_mt="2026-06-08T14:32:00+00:00",
        )
        assert conflict_suggested_action(r) == ACT_PULL

    def test_server_newer_by_one_second(self):
        r = _conflict(
            local_mt="2026-06-08T10:00:00+00:00",
            server_mt="2026-06-08T10:00:01+00:00",
        )
        assert conflict_suggested_action(r) == ACT_PULL

    def test_server_newer_by_many_days(self):
        r = _conflict(
            local_mt="2026-01-01T00:00:00+00:00",
            server_mt="2026-06-10T00:00:00+00:00",
        )
        assert conflict_suggested_action(r) == ACT_PULL


# ---------------------------------------------------------------------------
# 3. Same mtime -> Skip
# ---------------------------------------------------------------------------

class TestMtimeTie:
    def test_identical_modtime_suggests_skip(self):
        r = _conflict(
            local_mt="2026-06-08T14:32:00+00:00",
            server_mt="2026-06-08T14:32:00+00:00",
        )
        assert conflict_suggested_action(r) == ACT_SKIP

    def test_identical_modtime_millisecond_precision(self):
        r = _conflict(
            local_mt="2026-06-08T14:32:00.000000+00:00",
            server_mt="2026-06-08T14:32:00.000000+00:00",
        )
        assert conflict_suggested_action(r) == ACT_SKIP


# ---------------------------------------------------------------------------
# 4. Missing mtime -> Skip (graceful fallback)
# ---------------------------------------------------------------------------

class TestMissingMtime:
    def test_local_mtime_missing_falls_back_to_skip(self):
        r = _conflict(local_mt=None, server_mt="2026-06-08T14:32:00+00:00")
        assert conflict_suggested_action(r) == ACT_SKIP

    def test_server_mtime_missing_falls_back_to_skip(self):
        r = _conflict(local_mt="2026-06-08T14:32:00+00:00", server_mt=None)
        assert conflict_suggested_action(r) == ACT_SKIP

    def test_both_mtime_missing_falls_back_to_skip(self):
        r = _conflict(local_mt=None, server_mt=None)
        assert conflict_suggested_action(r) == ACT_SKIP

    def test_no_yours_entry_falls_back_to_skip(self):
        r = DiffResult(
            path="test/clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry=None,
            server_entry=_entry(modtime="2026-06-08T14:32:00+00:00"),
        )
        assert conflict_suggested_action(r) == ACT_SKIP

    def test_no_server_entry_falls_back_to_skip(self):
        r = DiffResult(
            path="test/clip.mov",
            state=DiffState.BOTH_CHANGED,
            yours_entry=_entry(modtime="2026-06-08T14:32:00+00:00"),
            server_entry=None,
        )
        assert conflict_suggested_action(r) == ACT_SKIP

    def test_non_conflict_state_always_returns_skip(self):
        r = DiffResult(
            path="test/clip.mov",
            state=DiffState.LOCAL_CHANGED,
            yours_entry=_entry(modtime="2026-06-08T14:32:00+00:00"),
            server_entry=_entry(modtime="2026-06-07T00:00:00+00:00"),
        )
        assert conflict_suggested_action(r) == ACT_SKIP


# ---------------------------------------------------------------------------
# 5. "Newer wins" batch logic via DiffTable.apply_newer_wins()
#    (headless — uses QApplication fixture to satisfy PyQt6 widget requirements)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication. Reused across all widget tests."""
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class TestNewerWinsBatch:
    def _make_table(self, qapp):
        from gui.diff_table import DiffTable
        table = DiffTable()
        return table

    def _result(self, path, state, local_mt=None, server_mt=None):
        return DiffResult(
            path=path,
            state=state,
            yours_entry=_entry(modtime=local_mt)  if local_mt  else _entry(),
            server_entry=_entry(modtime=server_mt) if server_mt else _entry(),
        )

    def test_newer_wins_sets_push_for_local_newer(self, qapp):
        table = self._make_table(qapp)
        rows = [
            self._result(
                "a/clip.mov", DiffState.BOTH_CHANGED,
                local_mt="2026-06-08T10:00:00+00:00",
                server_mt="2026-06-07T10:00:00+00:00",
            ),
        ]
        table.load_results(rows)
        table.apply_newer_wins()
        actions = table.get_actions()
        assert actions["a/clip.mov"] == ACT_PUSH

    def test_newer_wins_sets_pull_for_server_newer(self, qapp):
        table = self._make_table(qapp)
        rows = [
            self._result(
                "b/clip.mov", DiffState.BOTH_CHANGED,
                local_mt="2026-06-07T10:00:00+00:00",
                server_mt="2026-06-08T10:00:00+00:00",
            ),
        ]
        table.load_results(rows)
        table.apply_newer_wins()
        actions = table.get_actions()
        assert actions["b/clip.mov"] == ACT_PULL

    def test_newer_wins_leaves_tie_as_skip(self, qapp):
        table = self._make_table(qapp)
        rows = [
            self._result(
                "c/clip.mov", DiffState.BOTH_CHANGED,
                local_mt="2026-06-08T10:00:00+00:00",
                server_mt="2026-06-08T10:00:00+00:00",
            ),
        ]
        table.load_results(rows)
        table.apply_newer_wins()
        actions = table.get_actions()
        assert actions["c/clip.mov"] == ACT_SKIP

    def test_newer_wins_mixed_batch(self, qapp):
        """Multiple BOTH_CHANGED rows each get the correct action."""
        table = self._make_table(qapp)
        rows = [
            self._result(
                "push_me.mov", DiffState.BOTH_CHANGED,
                local_mt="2026-06-09T10:00:00+00:00",
                server_mt="2026-06-08T10:00:00+00:00",
            ),
            self._result(
                "pull_me.mov", DiffState.BOTH_CHANGED,
                local_mt="2026-06-07T10:00:00+00:00",
                server_mt="2026-06-09T10:00:00+00:00",
            ),
            self._result(
                "skip_me.mov", DiffState.BOTH_CHANGED,
                local_mt="2026-06-08T10:00:00+00:00",
                server_mt="2026-06-08T10:00:00+00:00",
            ),
        ]
        table.load_results(rows)
        table.apply_newer_wins()
        actions = table.get_actions()
        assert actions["push_me.mov"] == ACT_PUSH
        assert actions["pull_me.mov"] == ACT_PULL
        assert actions["skip_me.mov"] == ACT_SKIP

    def test_newer_wins_does_not_touch_non_conflict_rows(self, qapp):
        """LOCAL_ONLY and SERVER_ONLY rows retain their original default actions."""
        table = self._make_table(qapp)
        rows = [
            DiffResult(
                path="local_only.mov",
                state=DiffState.LOCAL_ONLY,
                yours_entry=_entry(modtime="2026-06-09T10:00:00+00:00"),
                server_entry=None,
            ),
            DiffResult(
                path="server_only.mov",
                state=DiffState.SERVER_ONLY,
                yours_entry=None,
                server_entry=_entry(modtime="2026-06-09T10:00:00+00:00"),
            ),
            self._result(
                "conflict.mov", DiffState.BOTH_CHANGED,
                local_mt="2026-06-09T10:00:00+00:00",
                server_mt="2026-06-08T10:00:00+00:00",
            ),
        ]
        table.load_results(rows)

        # Record pre-newer-wins defaults for non-conflict rows
        pre_actions = table.get_actions()
        local_only_default  = pre_actions["local_only.mov"]
        server_only_default = pre_actions["server_only.mov"]

        table.apply_newer_wins()
        post_actions = table.get_actions()

        # Non-conflict rows must be unchanged
        assert post_actions["local_only.mov"]  == local_only_default
        assert post_actions["server_only.mov"] == server_only_default
        # Conflict row must have been updated
        assert post_actions["conflict.mov"] == ACT_PUSH
