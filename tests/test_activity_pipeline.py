"""End-to-end integration of the org-activity pipeline through the rclone seam.

Runs the entire real call chain — ship_logs -> rclone_bridge.copyto -> the
swappable runner -> fake remote -> find_activity_shards -> fetch_remote_shards
-> load_org_records -> history.rows_for — with only the rclone *binary* faked
(tests/fakes.FakeRclone). This exercises the wiring the M9.1 log-shipping and
M9.3 org-refresh manual e2e checks were standing in for; only the real-Google
smoke remains genuinely manual.
"""

import json

import pytest

from core import log_sync, activity_index, history, rclone_bridge
from tests.fakes import FakeRclone


@pytest.fixture
def fake_remote():
    fake = FakeRclone()
    prev = rclone_bridge.set_rclone_runner(fake)
    try:
        yield fake
    finally:
        rclone_bridge.set_rclone_runner(prev)


def _write_shard(base_dir, ws, lines):
    d = base_dir / "activity"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"activity_{ws}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in lines))
    return p


def test_ship_then_fetch_then_render_round_trip(fake_remote, tmp_path):
    remote_base = "gdrive:STActivity"

    # ── Machine A: has a local shard, ships it to the fake remote ────────────
    machine_a = tmp_path / "machineA"
    _write_shard(machine_a, "CartA", [
        {"operation": "offload", "timestamp": "2026-06-12T10:00:00",
         "workstation": "CartA", "user": "dit", "project": "ProjX",
         "source": "A001", "dests": ["NAS"], "file_count": 12, "bytes": 2048,
         "verdict": "VERIFIED", "log_filename": "a.txt"},
    ])
    result = log_sync.ship_logs(remote_base, base_dir=machine_a,
                                ledger_path=machine_a / "ledger.json",
                                workstation="CartA", user="dit")
    assert result.shipped == 1
    # The shard now lives in the fake remote under {base}/{ws}/{user}/activity/.
    assert any("activity_CartA.jsonl" in k for k in fake_remote.store)

    # ── Machine B: pulls org shards and merges with its own local history ────
    machine_b = tmp_path / "machineB"
    _write_shard(machine_b, "CartB", [
        {"operation": "verify", "timestamp": "2026-06-13T09:00:00",
         "workstation": "CartB", "user": "ed", "project": "ProjY",
         "file_count": 50, "bytes": 0, "verdict": "FAIL"},
    ])
    cache = machine_b / "cache"
    fetched = activity_index.fetch_remote_shards(remote_base, cache)
    assert "activity_CartA.jsonl" in fetched

    merged = activity_index.load_org_records(local_dir=machine_b / "activity",
                                             cache_dir=cache)
    workstations = {r["workstation"] for r in merged}
    assert workstations == {"CartA", "CartB"}  # own + pulled

    # ── Render through the History presentation layer ────────────────────────
    rows = history.rows_for(merged)
    assert len(rows) == 2
    assert rows[0].workstation == "CartB"     # 06-13 newest first
    assert "VERIFIED" in {r.verdict for r in rows}


def test_ship_is_idempotent_across_runs(fake_remote, tmp_path):
    remote_base = "gdrive:STActivity"
    machine = tmp_path / "m"
    ledger = machine / "ledger.json"
    _write_shard(machine, "Cart9", [
        {"operation": "transfer", "timestamp": "2026-06-12T10:00:00",
         "workstation": "Cart9", "user": "x"},
    ])
    r1 = log_sync.ship_logs(remote_base, base_dir=machine, ledger_path=ledger,
                            workstation="Cart9", user="x")
    r2 = log_sync.ship_logs(remote_base, base_dir=machine, ledger_path=ledger,
                            workstation="Cart9", user="x")
    assert r1.shipped == 1
    assert r2.shipped == 0  # ledger remembers — no re-upload


def test_fetch_skips_own_unchanged_and_returns_only_shards(fake_remote, tmp_path):
    remote_base = "gdrive:STActivity"
    # Seed the remote with a shard plus a raw log that must NOT be fetched.
    fake_remote.store[f"{remote_base}/CartA/dit/activity/activity_CartA.jsonl"] = b'{}\n'
    fake_remote.store[f"{remote_base}/CartA/dit/logs/custody.txt"] = b"log"
    cache = tmp_path / "cache"
    fetched = activity_index.fetch_remote_shards(remote_base, cache)
    assert fetched == ["activity_CartA.jsonl"]  # the .txt log is never pulled
