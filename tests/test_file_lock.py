"""Tests for core/file_lock.py — M14.3 atomic read-modify-write.

Includes the required concurrent-writer test (two threads, assert no lost update)
and an empirical O_APPEND concurrency test backing the append_activity assumption.
"""

import json
import threading
from pathlib import Path

import pytest

from core.file_lock import locked_json_update


# ── basic read-modify-write semantics ─────────────────────────────────────────

def test_creates_file_when_absent(tmp_path):
    p = tmp_path / "state.json"
    out = locked_json_update(p, lambda d: {**d, "k": 1})
    assert out == {"k": 1}
    assert json.loads(p.read_text()) == {"k": 1}


def test_default_used_when_absent(tmp_path):
    p = tmp_path / "state.json"
    out = locked_json_update(p, lambda d: d, default={"shipped": {}})
    assert out == {"shipped": {}}


def test_default_is_copied_not_aliased(tmp_path):
    p = tmp_path / "state.json"
    default = {"shipped": {}}
    locked_json_update(p, lambda d: d.update({"shipped": {"x": 1}}) or d, default=default)
    # The caller's default object must not have been mutated.
    assert default == {"shipped": {}}


def test_reads_existing_then_mutates(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"a": 1}))
    out = locked_json_update(p, lambda d: {**d, "b": 2})
    assert out == {"a": 1, "b": 2}


def test_unparseable_file_falls_back_to_default(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    out = locked_json_update(p, lambda d: {**d, "ok": True}, default={"seed": 1})
    assert out == {"seed": 1, "ok": True}


def test_unparseable_file_no_default_is_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    out = locked_json_update(p, lambda d: {**d, "ok": True})
    assert out == {"ok": True}


def test_creates_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "nested" / "state.json"
    locked_json_update(p, lambda d: {"y": 2})
    assert p.exists()


def test_lock_sidecar_created_and_persists(tmp_path):
    p = tmp_path / "state.json"
    locked_json_update(p, lambda d: {"k": 1})
    lock = p.with_name(p.name + ".lock")
    assert lock.exists()           # sidecar created
    # Second cycle reuses the same sidecar (stable inode); it is never deleted.
    inode_before = lock.stat().st_ino
    locked_json_update(p, lambda d: {**d, "k2": 2})
    assert lock.exists() and lock.stat().st_ino == inode_before


def test_no_tmp_left_behind(tmp_path):
    p = tmp_path / "state.json"
    locked_json_update(p, lambda d: {"k": 1})
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_locks_data_file_not_renamed_inode(tmp_path):
    # The sidecar — not the data file — is the lock target, so the data inode can
    # be replaced by rename every cycle without breaking serialisation.
    p = tmp_path / "state.json"
    locked_json_update(p, lambda d: {"n": 1})
    ino1 = p.stat().st_ino
    locked_json_update(p, lambda d: {"n": 2})
    ino2 = p.stat().st_ino
    # tmp+rename means the data file is a fresh inode each write.
    assert ino1 != ino2


# ── concurrent writers: no lost update ────────────────────────────────────────

def test_concurrent_writers_no_lost_update(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"items": []}))
    n_threads = 8
    per_thread = 25

    def worker(tid):
        for i in range(per_thread):
            def _mut(d):
                d.setdefault("items", []).append(f"{tid}-{i}")
                return d
            locked_json_update(p, _mut)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    items = json.loads(p.read_text())["items"]
    # Every single append survived — no thread clobbered another's write.
    assert len(items) == n_threads * per_thread
    expected = {f"{t}-{i}" for t in range(n_threads) for i in range(per_thread)}
    assert set(items) == expected


def test_concurrent_counter_increments_not_lost(tmp_path):
    p = tmp_path / "counter.json"
    p.write_text(json.dumps({"n": 0}))

    def worker():
        for _ in range(50):
            locked_json_update(p, lambda d: {**d, "n": d.get("n", 0) + 1})

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(p.read_text())["n"] == 6 * 50


# ── empirical O_APPEND concurrency (backs append_activity assumption b) ────────

def test_oappend_concurrent_lines_all_present_and_intact(tmp_path):
    # Two-plus writers appending newline-terminated records with open("a") must
    # produce all lines, none torn — the same mechanism append_activity relies on.
    p = tmp_path / "activity.jsonl"
    p.touch()
    n_threads = 6
    per_thread = 200

    def worker(tid):
        line = json.dumps({"tid": tid, "payload": "x" * 100})  # well under 4 KB
        for _ in range(per_thread):
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = p.read_text().splitlines()
    assert len(lines) == n_threads * per_thread
    # Every line is intact, parseable JSON (no interleaved/torn writes).
    for ln in lines:
        json.loads(ln)
