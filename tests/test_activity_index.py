"""Tests for core/activity_index.py (M9.2 per-machine activity summaries)."""

import json
from datetime import datetime, timedelta

import pytest

from core import activity_index as ai


@pytest.fixture
def activity_dir(tmp_path):
    return tmp_path / "activity"


def _rec(operation="offload", ws="cart3", ts="2026-06-12T10:00:00", **kw):
    return ai.ActivityRecord(operation=operation, timestamp=ts, workstation=ws,
                             user=kw.pop("user", "rk"), **kw)


# ── append + round-trip ──────────────────────────────────────────────────────

def test_append_writes_one_line(activity_dir):
    r = ai.record_for("offload", project="A001", dests=["NAS", "Shuttle"],
                      file_count=312, bytes=1200, verdict="VERIFIED",
                      now=datetime(2026, 6, 12, 10, 0), workstation="cart3", user="rk")
    path = ai.append_activity(r, activity_dir=activity_dir)
    assert path == activity_dir / "activity_cart3.jsonl"
    loaded = ai.load_shard(path)
    assert len(loaded) == 1
    assert loaded[0]["project"] == "A001"
    assert loaded[0]["dests"] == ["NAS", "Shuttle"]
    assert loaded[0]["file_count"] == 312


def test_append_is_additive(activity_dir):
    ai.append_activity(_rec(), activity_dir=activity_dir)
    ai.append_activity(_rec(operation="verify"), activity_dir=activity_dir)
    loaded = ai.load_shard(ai.shard_path("cart3", activity_dir=activity_dir))
    assert [r["operation"] for r in loaded] == ["offload", "verify"]


def test_record_for_stamps_host_user_time():
    r = ai.record_for("merge", now=datetime(2026, 6, 12, 8, 0),
                      workstation="ws1", user="bob")
    assert r.operation == "merge"
    assert r.workstation == "ws1" and r.user == "bob"
    assert r.timestamp == "2026-06-12T08:00:00"


# ── load: corrupt / partial lines skipped loudly ─────────────────────────────

def test_load_skips_corrupt_lines(activity_dir):
    activity_dir.mkdir(parents=True)
    path = activity_dir / "activity_x.jsonl"
    path.write_text(
        json.dumps({"operation": "offload", "timestamp": "t1", "workstation": "x"}) + "\n"
        + "{ truncated half line\n"                       # crash mid-write
        + "12345\n"                                        # non-object
        + "\n"                                             # blank
        + json.dumps({"operation": "verify", "timestamp": "t2", "workstation": "x"}) + "\n"
    )
    warnings = []
    loaded = ai.load_shard(path, log_cb=lambda m, l="warning": warnings.append(m))
    assert [r["operation"] for r in loaded] == ["offload", "verify"]
    assert len(warnings) == 2  # corrupt line + non-object line, logged loudly


def test_load_missing_file(activity_dir):
    warnings = []
    out = ai.load_shard(activity_dir / "nope.jsonl",
                        log_cb=lambda m, l="warning": warnings.append(m))
    assert out == []
    assert warnings


# ── merge + find ─────────────────────────────────────────────────────────────

def test_merge_sorts_by_timestamp(activity_dir):
    activity_dir.mkdir(parents=True)
    ai.append_activity(_rec(ws="cartA", ts="2026-06-12T12:00:00"), activity_dir=activity_dir)
    ai.append_activity(_rec(ws="cartB", ts="2026-06-12T09:00:00"), activity_dir=activity_dir)
    merged = ai.merge_shards(ai.find_shards(activity_dir))
    assert [r["timestamp"] for r in merged] == ["2026-06-12T09:00:00", "2026-06-12T12:00:00"]


def test_find_shards_only_matches_pattern(activity_dir):
    activity_dir.mkdir(parents=True)
    (activity_dir / "activity_cartA.jsonl").write_text("")
    (activity_dir / "notes.txt").write_text("x")
    shards = ai.find_shards(activity_dir)
    assert [p.name for p in shards] == ["activity_cartA.jsonl"]


# ── filter ───────────────────────────────────────────────────────────────────

def test_filter_records():
    recs = [
        {"operation": "offload", "workstation": "a", "user": "u1", "project": "P1"},
        {"operation": "verify", "workstation": "a", "user": "u2", "project": "P1"},
        {"operation": "offload", "workstation": "b", "user": "u1", "project": "P2"},
    ]
    assert len(ai.filter_records(recs, operation="offload")) == 2
    assert len(ai.filter_records(recs, workstation="a")) == 2
    assert len(ai.filter_records(recs, operation="offload", workstation="b")) == 1
    assert len(ai.filter_records(recs, project="P1", user="u1")) == 1


# ── staleness ────────────────────────────────────────────────────────────────

def test_staleness_flags_old_workstations():
    now = datetime(2026, 6, 12)
    recs = [
        {"workstation": "fresh", "timestamp": (now - timedelta(days=1)).isoformat()},
        {"workstation": "stale", "timestamp": (now - timedelta(days=10)).isoformat()},
        {"workstation": "stale", "timestamp": (now - timedelta(days=20)).isoformat()},
    ]
    result = ai.staleness(recs, now=now)
    by_ws = {s.workstation: s for s in result}
    assert by_ws["fresh"].stale is False and by_ws["fresh"].days_since == 1
    # uses the most recent record for 'stale' (10 days, not 20)
    assert by_ws["stale"].stale is True and by_ws["stale"].days_since == 10
    # sorted most-stale first
    assert result[0].workstation == "stale"


def test_staleness_ignores_bad_rows():
    now = datetime(2026, 6, 12)
    recs = [
        {"workstation": "", "timestamp": now.isoformat()},     # no ws
        {"workstation": "x"},                                   # no ts
        {"workstation": "y", "timestamp": "not-a-date"},        # bad ts
        {"workstation": "z", "timestamp": now.isoformat()},
    ]
    result = ai.staleness(recs, now=now)
    assert [s.workstation for s in result] == ["z"]
