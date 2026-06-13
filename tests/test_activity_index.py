"""Tests for core/activity_index.py (M9.2 per-machine activity summaries)."""

import json
from datetime import datetime, timedelta
from pathlib import Path

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


# --------------------------------------------------------------------------- #
# M9.2 wiring helpers: record_from_manifest + safe_append_activity
# --------------------------------------------------------------------------- #

def _manifest(**over):
    m = {
        "label": "ProjectX", "project_id": "projx", "workstation": "Cart 9",
        "user": "dit", "file_count": 3, "total_size_bytes": 9000,
        "files": {"a": {"size": 1000}, "b": {"size": 3000}, "c": {"size": 5000}},
    }
    m.update(over)
    return m


def test_record_from_manifest_derives_fields():
    r = ai.record_from_manifest(_manifest(), operation="offload", source="A001",
                                dests=["NAS", "Shuttle"], verdict="VERIFIED",
                                log_filename="cust.txt",
                                now=datetime(2026, 6, 13, 9, 0, 0))
    assert r.operation == "offload"
    assert r.project == "projx"
    assert r.workstation == "Cart 9"
    assert r.user == "dit"
    assert r.file_count == 3
    assert r.bytes == 9000
    assert r.dests == ["NAS", "Shuttle"]
    assert r.verdict == "VERIFIED"
    assert r.log_filename == "cust.txt"
    assert r.timestamp == "2026-06-13T09:00:00"


def test_record_from_manifest_falls_back_to_file_sums():
    m = _manifest()
    del m["file_count"]; del m["total_size_bytes"]
    r = ai.record_from_manifest(m, operation="transfer")
    assert r.file_count == 3
    assert r.bytes == 9000  # summed from files


def test_safe_append_activity_writes(activity_dir):
    r = ai.record_from_manifest(_manifest(), operation="offload")
    path = ai.safe_append_activity(r, activity_dir=activity_dir)
    assert path.exists()
    recs = ai.load_shard(path)
    assert recs[0]["operation"] == "offload"


def test_safe_append_activity_swallows_errors(monkeypatch):
    msgs = []
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ai, "append_activity", boom)
    out = ai.safe_append_activity(_rec(), log_cb=lambda m, l: msgs.append(m))
    assert out is None
    assert msgs and "not recorded" in msgs[0]


def test_transfer_folder_appends_activity(tmp_path, monkeypatch):
    from core import transfer
    monkeypatch.setattr(ai, "ACTIVITY_DIR", tmp_path / "activity")
    src = tmp_path / "src"; src.mkdir()
    (src / "clip.txt").write_text("data")
    dst = tmp_path / "dst"
    transfer.transfer_folder(src, dst)
    shards = ai.find_shards(tmp_path / "activity")
    assert shards, "transfer should write an activity shard"
    recs = ai.merge_shards(shards)
    assert any(r["operation"] == "transfer" for r in recs)


# --------------------------------------------------------------------------- #
# M9.3 org shards: fetch_remote_shards + load_org_records
# --------------------------------------------------------------------------- #

def test_fetch_remote_shards_copies_only_shards(tmp_path):
    cache = tmp_path / "cache"
    remote = [
        "gdrive:Acts/Cart1/dit/activity/activity_Cart1.jsonl",
        "gdrive:Acts/Cart2/dit/activity/activity_Cart2.jsonl",
        "gdrive:Acts/Cart1/dit/logs/custody_x.txt",  # not a shard, ignored
    ]
    copied = {}
    def copy_fn(src, dst):
        copied[src] = dst
        Path(dst).write_text('{"operation":"offload","timestamp":"2026-06-12T10:00:00","workstation":"Cart1","user":"dit"}\n')
    fetched = ai.fetch_remote_shards("gdrive:Acts", cache, list_fn=lambda b: remote,
                                     copy_fn=copy_fn)
    assert set(fetched) == {"activity_Cart1.jsonl", "activity_Cart2.jsonl"}
    assert len(copied) == 2


def test_fetch_remote_shards_empty_base_noop(tmp_path):
    assert ai.fetch_remote_shards("", tmp_path / "c", list_fn=lambda b: [],
                                  copy_fn=lambda s, d: None) == []


def test_fetch_remote_shards_skips_failed_copy(tmp_path):
    def copy_fn(src, dst):
        if "Cart2" in src:
            raise RuntimeError("network")
        Path(dst).write_text("{}\n")
    fetched = ai.fetch_remote_shards(
        "gdrive:Acts", tmp_path / "c",
        list_fn=lambda b: ["x/activity_Cart1.jsonl", "x/activity_Cart2.jsonl"],
        copy_fn=copy_fn)
    assert fetched == ["activity_Cart1.jsonl"]


def test_fetch_remote_shards_list_failure_returns_empty(tmp_path):
    def boom(b):
        raise RuntimeError("offline")
    assert ai.fetch_remote_shards("gdrive:Acts", tmp_path / "c", list_fn=boom,
                                  copy_fn=lambda s, d: None) == []


def test_load_org_records_merges_local_and_cache(tmp_path):
    local = tmp_path / "local"; local.mkdir()
    cache = tmp_path / "cache"; cache.mkdir()
    (local / "activity_Cart1.jsonl").write_text(
        '{"operation":"offload","timestamp":"2026-06-12T10:00:00","workstation":"Cart1","user":"a"}\n')
    (cache / "activity_Cart2.jsonl").write_text(
        '{"operation":"merge","timestamp":"2026-06-11T10:00:00","workstation":"Cart2","user":"b"}\n')
    recs = ai.load_org_records(local_dir=local, cache_dir=cache)
    assert [r["workstation"] for r in recs] == ["Cart2", "Cart1"]  # timestamp-sorted


def test_load_org_records_local_overrides_cache_same_filename(tmp_path):
    local = tmp_path / "local"; local.mkdir()
    cache = tmp_path / "cache"; cache.mkdir()
    # Stale cached copy of our own shard vs the fresh local one.
    (cache / "activity_Cart1.jsonl").write_text(
        '{"operation":"offload","timestamp":"2026-06-01T10:00:00","workstation":"Cart1","user":"a"}\n')
    (local / "activity_Cart1.jsonl").write_text(
        '{"operation":"offload","timestamp":"2026-06-12T10:00:00","workstation":"Cart1","user":"a"}\n'
        '{"operation":"verify","timestamp":"2026-06-13T10:00:00","workstation":"Cart1","user":"a"}\n')
    recs = ai.load_org_records(local_dir=local, cache_dir=cache)
    assert len(recs) == 2  # local shard used, not the 1-line stale cache
    assert recs[-1]["operation"] == "verify"


# --------------------------------------------------------------------------- #
# M9.3: find_local_log (open custody log)
# --------------------------------------------------------------------------- #

def test_find_local_log_in_logs_dir(tmp_path):
    (tmp_path / "logs").mkdir()
    f = tmp_path / "logs" / "custody_a001.txt"
    f.write_text("x")
    assert ai.find_local_log("custody_a001.txt", base_dir=tmp_path) == f


def test_find_local_log_in_offload_logs(tmp_path):
    (tmp_path / "offload_logs").mkdir()
    f = tmp_path / "offload_logs" / "offload_x.txt"
    f.write_text("x")
    assert ai.find_local_log("offload_x.txt", base_dir=tmp_path) == f


def test_find_local_log_missing_or_empty(tmp_path):
    assert ai.find_local_log("nope.txt", base_dir=tmp_path) is None
    assert ai.find_local_log("", base_dir=tmp_path) is None
