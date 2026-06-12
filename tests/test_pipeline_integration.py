"""Layer 2 (M1.5): end-to-end pipeline integration tests with real files.

Pipeline A: offload to one or two destinations, then verify the committed
files against the persisted manifest (including corruption detection).

Pipeline B: local + server through three_way_diff, merge_ops apply and
re-scan, parametrized over every actionable DiffState, both conflict
resolutions, preserve-on-overwrite and a v1.0 base manifest (schema
migration). A mocked-rclone variant covers the Drive merge path.

Pipeline C: transfer_folder over conflict handlers with a pre-existing
destination, asserting the on-disk outcome and manifest verification.

All filesystem work happens in tmp_path. No rclone binary, no network.
"""

import json
import shutil
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from core.checksum import compute_all
from core.comparison import DiffState, three_way_diff
from core.manifest import MANIFEST_FILENAME, SCHEMA_VERSION, generate_manifest, load_manifest
from core.merge_ops import (
    ACT_PUSH, ACT_PULL, delete_local, delete_server, overwrite_suffix,
    pull_file, push_file,
)
from core.offload import CellState, OffloadConfig, OffloadDest, OffloadSource, run_offload


@pytest.fixture(autouse=True)
def _isolate_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr("core.manifest.LOCAL_MANIFEST_DIR", tmp_path / "_archive")
    monkeypatch.setattr("core.offload.OFFLOAD_LOGS_DIR", tmp_path / "_coc")


FILES = {"clips/a.mov": b"alpha-v1", "audio/b.wav": b"bravo-v1", "notes.txt": b"notes-v1"}


def _populate(root: Path, files=None):
    for rel, data in (files or FILES).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _hashes_equal(a: Path, b: Path) -> bool:
    return compute_all(a)["sha256"] == compute_all(b)["sha256"]


def _verify_against_manifest(manifest: dict, folder: Path) -> dict:
    """Hash-verify every manifest entry against disk. Returns {rel: status}."""
    out = {}
    for rel, entry in manifest["files"].items():
        p = folder / rel
        if not p.exists():
            out[rel] = "MISSING"
            continue
        cs = (entry.get("dest_checksums") or entry.get("source_checksums")
              or entry.get("checksums", {}))
        algo = ("sha256" if "sha256" in cs else
                "xxhash3_64" if "xxhash3_64" in cs else "md5")
        actual = compute_all(p, include_xxhash=(algo == "xxhash3_64"),
                             include_md5=(algo == "md5"))
        expected = (cs.get(algo) or "").lower()
        out[rel] = "OK" if expected and expected == (actual.get(algo) or "").lower() else "MISMATCH"
    return out


# ---------------------------------------------------------------------------
# Pipeline A: offload -> commit -> verify
# ---------------------------------------------------------------------------

class TestOffloadThenVerify:
    def _offload(self, tmp_path, n_dests):
        src = _populate(tmp_path / "CARD_A")
        dests = []
        for i in range(n_dests):
            d = OffloadDest(label=f"D{i}", path=tmp_path / f"dest{i}")
            d.path.mkdir()
            dests.append(d)
        results, source_manifests, log_path = run_offload(
            [OffloadSource(label="A001", path=src)], dests,
            OffloadConfig(), MagicMock(), MagicMock(),
        )
        return src, dests, results, source_manifests, log_path

    @pytest.mark.parametrize("n_dests", [1, 2])
    def test_all_cells_done_and_every_dest_verifies(self, tmp_path, n_dests):
        src, dests, results, _, _ = self._offload(tmp_path, n_dests)
        assert len(results) == n_dests
        assert all(r.state == CellState.DONE for r in results)
        for d in dests:
            final = d.path / "A001"
            manifest = load_manifest(final / MANIFEST_FILENAME)
            statuses = _verify_against_manifest(manifest, final)
            assert set(statuses.values()) == {"OK"}, statuses
            # bytes really match the source
            for rel in FILES:
                assert _hashes_equal(src / rel, final / rel)

    def test_corrupted_dest_file_detected_by_verify(self, tmp_path):
        _, dests, _, _, _ = self._offload(tmp_path, 1)
        final = dests[0].path / "A001"
        (final / "notes.txt").write_bytes(b"bitrot")
        manifest = load_manifest(final / MANIFEST_FILENAME)
        statuses = _verify_against_manifest(manifest, final)
        assert statuses["notes.txt"] == "MISMATCH"
        assert statuses["clips/a.mov"] == "OK"

    def test_custody_log_covers_both_dests(self, tmp_path):
        _, dests, results, _, log_path = self._offload(tmp_path, 2)
        text = Path(log_path).read_text()
        assert "OVERALL RESULT: COMPLETE" in text
        assert "A001 → D0" in text and "A001 → D1" in text

    def test_source_untouched_after_offload(self, tmp_path):
        src, _, _, _, _ = self._offload(tmp_path, 1)
        for rel, data in FILES.items():
            assert (src / rel).read_bytes() == data


# ---------------------------------------------------------------------------
# Pipeline B: diff -> apply -> re-scan over every actionable state
# ---------------------------------------------------------------------------

def _make_synced_pair(tmp_path):
    """local and server with identical content plus a base manifest of it."""
    local = _populate(tmp_path / "local")
    server = _populate(tmp_path / "server")
    base = generate_manifest(local, label="base")
    return local, server, base


def _diff(local, server, base):
    yours = generate_manifest(local, label="local")
    theirs = generate_manifest(server, label="server")
    return three_way_diff(base, yours, theirs)


def _rescan_all_unchanged(local, server):
    """After an apply both sides must scan clean against a fresh base."""
    fresh_base = generate_manifest(local, label="base")
    rows = _diff(local, server, fresh_base)
    return [r for r in rows if r.state != DiffState.UNCHANGED]


class TestMergeRoundTrip:
    TARGET = "clips/a.mov"

    def _mutate(self, local, server, state):
        t = self.TARGET
        if state == DiffState.LOCAL_ONLY:
            (local / "new_local.txt").write_bytes(b"fresh local")
            return "new_local.txt"
        if state == DiffState.SERVER_ONLY:
            (server / "new_server.txt").write_bytes(b"fresh server")
            return "new_server.txt"
        if state == DiffState.LOCAL_CHANGED:
            (local / t).write_bytes(b"alpha-v2-local")
            return t
        if state == DiffState.SERVER_CHANGED:
            (server / t).write_bytes(b"alpha-v2-server")
            return t
        if state == DiffState.BOTH_CHANGED:
            (local / t).write_bytes(b"alpha-v2-local")
            (server / t).write_bytes(b"alpha-v3-server")
            return t
        if state == DiffState.DELETED_LOCAL:
            (local / t).unlink()
            return t
        if state == DiffState.DELETED_SERVER:
            (server / t).unlink()
            return t
        raise AssertionError(state)

    @pytest.mark.parametrize("state,action", [
        (DiffState.LOCAL_ONLY,     ACT_PUSH),
        (DiffState.SERVER_ONLY,    ACT_PULL),
        (DiffState.LOCAL_CHANGED,  ACT_PUSH),
        (DiffState.SERVER_CHANGED, ACT_PULL),
        (DiffState.BOTH_CHANGED,   ACT_PUSH),   # keep local
        (DiffState.BOTH_CHANGED,   ACT_PULL),   # keep server
    ])
    def test_state_detected_applied_and_rescan_clean(self, tmp_path, state, action):
        local, server, base = _make_synced_pair(tmp_path)
        rel = self._mutate(local, server, state)

        rows = {r.path: r for r in _diff(local, server, base)}
        assert rows[rel].state == state

        if action == ACT_PUSH:
            assert push_file(rel, local, str(server), preserve_on_overwrite=False)
        else:
            assert pull_file(rel, local, str(server), preserve_on_overwrite=False)

        assert _hashes_equal(local / rel, server / rel)
        assert _rescan_all_unchanged(local, server) == []

    def test_deleted_local_propagates_to_server(self, tmp_path):
        local, server, base = _make_synced_pair(tmp_path)
        rel = self._mutate(local, server, DiffState.DELETED_LOCAL)
        rows = {r.path: r for r in _diff(local, server, base)}
        assert rows[rel].state == DiffState.DELETED_LOCAL

        assert delete_server(rel, str(server)) is True
        assert not (server / rel).exists()
        assert _rescan_all_unchanged(local, server) == []

    def test_deleted_server_propagates_to_local(self, tmp_path):
        local, server, base = _make_synced_pair(tmp_path)
        rel = self._mutate(local, server, DiffState.DELETED_SERVER)
        rows = {r.path: r for r in _diff(local, server, base)}
        assert rows[rel].state == DiffState.DELETED_SERVER

        assert delete_local(rel, local) is True
        assert not (local / rel).exists()
        assert _rescan_all_unchanged(local, server) == []

    def test_preserve_on_overwrite_keeps_server_backup(self, tmp_path):
        local, server, base = _make_synced_pair(tmp_path)
        rel = self._mutate(local, server, DiffState.BOTH_CHANGED)

        result = push_file(rel, local, str(server), preserve_on_overwrite=True)
        assert result
        # Documented preserve semantics: server keeps its original at rel and
        # the local version lands as a backup-suffixed sibling (persistent
        # divergence by design, see README "Preserve mode").
        assert (server / rel).read_bytes() == b"alpha-v3-server"
        backups = list((server / "clips").glob(f"a*{overwrite_suffix()}*"))
        assert backups, "expected the pushed copy under a suffixed name"
        assert backups[0].read_bytes() == b"alpha-v2-local"
        # Next scan: rel still differs (SERVER_CHANGED vs the fresh local base)
        # and the suffixed copy appears as SERVER_ONLY
        leftover = _rescan_all_unchanged(local, server)
        assert {r.state for r in leftover} == {DiffState.SERVER_CHANGED, DiffState.SERVER_ONLY}

    def test_v10_base_manifest_migrates_and_diffs(self, tmp_path):
        local, server, _ = _make_synced_pair(tmp_path)
        fresh = generate_manifest(local, label="base")
        # Strip to a v1.0 shape: old key names, no 1.1/1.2 fields
        v10 = {
            "schema_version": "1.0",
            "created_at": fresh["created_at"],
            "label": "base",
            "root": fresh["root"],
            "server_path": "/old/server",
            "files": {
                rel: {"type": "file", "size": e["size"], "modtime": e["modtime"],
                      "checksums": e["checksums"]}
                for rel, e in fresh["files"].items()
            },
        }
        p = tmp_path / "old_manifest.json"
        p.write_text(json.dumps(v10))
        migrated = load_manifest(p)
        assert migrated["schema_version"] == SCHEMA_VERSION
        assert migrated["counterpart_path"] == "/old/server"
        assert migrated["renames"] == []

        (local / self.TARGET).write_bytes(b"alpha-v2-local")
        rows = {r.path: r for r in _diff(local, server, migrated)}
        assert rows[self.TARGET].state == DiffState.LOCAL_CHANGED

    @pytest.mark.parametrize("keep", ["sha256", "xxhash3_64", "md5"])
    def test_verify_contract_per_checksum_algorithm(self, tmp_path, keep):
        folder = _populate(tmp_path / "data")
        manifest = generate_manifest(folder, label="x")
        for entry in manifest["files"].values():
            cs = entry["checksums"]
            if keep == "md5":
                # generate_manifest skips md5 by default; compute it
                for rel in manifest["files"]:
                    manifest["files"][rel]["checksums"] = {
                        "md5": compute_all(folder / rel, include_md5=True)["md5"]
                    }
                break
            entry["checksums"] = {keep: cs[keep]}
        statuses = _verify_against_manifest(manifest, folder)
        assert set(statuses.values()) == {"OK"}


class TestMergeDrivePath:
    """Merge apply against a Drive server with rclone mocked at the bridge."""

    DRIVE_URL = "https://drive.google.com/drive/folders/abc123"

    @pytest.fixture
    def drive(self, monkeypatch):
        import core.merge_ops as mo
        m = MagicMock()
        m.copyto.return_value = True
        m.deletefile.return_value = True
        m.path_exists.return_value = False
        monkeypatch.setattr(mo, "rclone_bridge", m)
        monkeypatch.setattr(mo, "gdrive_url_to_rclone",
                            lambda s: ("gdrive:", ["--drive-root-folder-id", "abc123"]))
        monkeypatch.setattr(mo, "is_gdrive_url",
                            lambda s: "drive.google.com" in str(s))
        return m

    def test_push_routes_through_copyto(self, tmp_path, drive):
        local = _populate(tmp_path / "local")
        result = push_file("clips/a.mov", local, self.DRIVE_URL,
                           preserve_on_overwrite=False)
        assert result
        args = drive.copyto.call_args
        assert str(local / "clips/a.mov") in args[0]
        assert "gdrive:clips/a.mov" in args[0]

    def test_delete_server_routes_through_deletefile(self, tmp_path, drive):
        assert delete_server("clips/a.mov", self.DRIVE_URL) is True
        assert "gdrive:clips/a.mov" in drive.deletefile.call_args[0]


# ---------------------------------------------------------------------------
# Pipeline C: transfer_folder conflict handlers with a pre-existing dest
# ---------------------------------------------------------------------------

class TestTransferConflictHandlers:
    def _setup(self, tmp_path):
        src = _populate(tmp_path / "src", {"x.txt": b"source-version"})
        dst = tmp_path / "dst"
        existing = dst / "src" / "x.txt"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"dest-version")
        return src, dst, existing

    def test_skip_keeps_existing_dest_file(self, tmp_path):
        from core.transfer import transfer_folder
        src, dst, existing = self._setup(tmp_path)
        result = transfer_folder(src, dst, conflict_handler="skip")
        assert not result["errors"]
        assert existing.read_bytes() == b"dest-version"

    def test_overwrite_replaces_dest_file(self, tmp_path):
        from core.transfer import transfer_folder
        src, dst, existing = self._setup(tmp_path)
        result = transfer_folder(src, dst, conflict_handler="overwrite")
        assert not result["errors"]
        assert existing.read_bytes() == b"source-version"

    def test_rename_keeps_both_versions(self, tmp_path):
        from core.transfer import transfer_folder
        src, dst, existing = self._setup(tmp_path)
        result = transfer_folder(src, dst, conflict_handler="rename")
        assert not result["errors"]
        assert existing.read_bytes() == b"dest-version"
        siblings = list(existing.parent.glob("x*.txt"))
        assert len(siblings) == 2
        assert b"source-version" in [p.read_bytes() for p in siblings]

    def test_transferred_folder_verifies_against_manifest(self, tmp_path):
        from core.transfer import transfer_folder
        src = _populate(tmp_path / "ProjectY")
        dst = tmp_path / "out"
        dst.mkdir()
        result = transfer_folder(src, dst)
        assert not result["errors"]
        actual = Path(result["actual_dest"])
        manifest = load_manifest(actual / MANIFEST_FILENAME)
        statuses = _verify_against_manifest(manifest, actual)
        assert set(statuses.values()) == {"OK"}


# ---------------------------------------------------------------------------
# M4.1: resume interrupted offload
# ---------------------------------------------------------------------------

import core.offload as offload_mod
from core.offload import (
    STATE_FILENAME, discard_stale_staging, find_resumable_staging,
    write_chain_of_custody_log,
)


def _interrupt_after(monkeypatch, n_files):
    """Make _copy_with_retries die after n successful per-file copies."""
    real = offload_mod._copy_with_retries
    calls = {"n": 0}

    def wrapper(src, dst, retries, log_cb):
        if calls["n"] >= n_files:
            raise OSError("simulated mid-copy interrupt")
        calls["n"] += 1
        return real(src, dst, retries, log_cb)

    monkeypatch.setattr(offload_mod, "_copy_with_retries", wrapper)
    return calls


class TestResumeInterruptedOffload:
    def _run(self, tmp_path, resume=False):
        src = tmp_path / "CARD_A"
        if not src.exists():
            _populate(src)
        dest = OffloadDest(label="NAS", path=tmp_path / "nas")
        dest.path.mkdir(exist_ok=True)
        cfg = OffloadConfig(resume_staging=resume)
        results, _, log_path = run_offload(
            [OffloadSource(label="A001", path=src)], [dest],
            cfg, MagicMock(), MagicMock(),
        )
        return src, dest, results[0], log_path

    def test_interrupt_then_resume_copies_only_missing(self, tmp_path, monkeypatch):
        calls = _interrupt_after(monkeypatch, 1)  # first file copies, second dies
        src, dest, r1, _ = self._run(tmp_path)
        assert r1.state == CellState.FAILED

        # staging and its state file survive the failure
        hit = find_resumable_staging(
            OffloadSource(label="A001", path=src), dest)
        assert hit is not None
        staging, state = hit
        assert len(state["completed"]) == 1

        # resume: remaining files copy, staged one is reused
        monkeypatch.setattr(offload_mod, "_copy_with_retries",
                            offload_mod._copy_with_retries)  # restore via attr below
        monkeypatch.undo()
        calls2 = _count_copies(monkeypatch)
        src, dest, r2, _ = self._run(tmp_path, resume=True)
        assert r2.state == CellState.DONE
        assert r2.resumed is True
        assert len(r2.reused_files) == 1
        assert calls2["n"] == len(FILES) - 1  # only the missing files copied

        final = dest.path / "A001"
        for rel, data in FILES.items():
            assert (final / rel).read_bytes() == data
        assert not (final / STATE_FILENAME).exists()

    def test_corrupted_staged_file_recopied_on_resume(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, 2)  # two files staged, third dies
        src, dest, r1, _ = self._run(tmp_path)
        assert r1.state == CellState.FAILED
        staging, state = find_resumable_staging(
            OffloadSource(label="A001", path=src), dest)
        victim = state["completed"][0]
        (staging / victim).write_bytes(b"bitrot in staging")

        monkeypatch.undo()
        calls2 = _count_copies(monkeypatch)
        src, dest, r2, _ = self._run(tmp_path, resume=True)
        assert r2.state == CellState.DONE
        assert victim not in r2.reused_files          # corrupted -> recopied
        assert calls2["n"] == len(FILES) - 1          # 1 clean reuse, rest copied
        final = dest.path / "A001"
        assert (final / victim).read_bytes() == FILES[victim]

    def test_clean_noop_resume_reuses_everything(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, len(FILES))  # all copied, dies after
        # interrupt fires inside the next cell's first copy; with one dest and
        # all files staged, the run actually completes copy. Force failure by
        # corrupting verify instead: simpler — interrupt during commit is out
        # of scope, so simulate by hand-building a complete staging run that
        # failed before commit.
        monkeypatch.undo()
        src = _populate(tmp_path / "CARD_A")
        dest = OffloadDest(label="NAS", path=tmp_path / "nas")
        dest.path.mkdir()
        source = OffloadSource(label="A001", path=src)
        from core.offload import prehash_source, copy_source_to_staging
        mfst = prehash_source(source, MagicMock())
        staging = copy_source_to_staging(
            source, dest, "20260612_000000", mfst, 1, MagicMock(), MagicMock())
        assert (staging / STATE_FILENAME).exists()

        calls = _count_copies(monkeypatch)
        _, dest, r, _ = self._run(tmp_path, resume=True)
        assert r.state == CellState.DONE
        assert r.resumed is True
        assert calls["n"] == 0                        # nothing recopied
        assert sorted(r.reused_files) == sorted(FILES)

    def test_stale_staged_file_never_committed(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, 1)
        src, dest, r1, _ = self._run(tmp_path)
        staging, _ = find_resumable_staging(
            OffloadSource(label="A001", path=src), dest)
        (staging / "junk_leftover.tmp").write_bytes(b"should not survive")

        monkeypatch.undo()
        src, dest, r2, _ = self._run(tmp_path, resume=True)
        assert r2.state == CellState.DONE
        assert not (dest.path / "A001" / "junk_leftover.tmp").exists()

    def test_custody_log_records_resume_and_reused_files(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, 1)
        src, dest, r1, _ = self._run(tmp_path)
        monkeypatch.undo()
        src, dest, r2, log_path = self._run(tmp_path, resume=True)
        text = Path(log_path).read_text()
        assert "Resumed: YES" in text
        assert "REUSED:" in text
        for rel in r2.reused_files:
            assert f"REUSED: {rel}" in text

    def test_no_resume_when_config_off(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, 1)
        src, dest, r1, _ = self._run(tmp_path)
        monkeypatch.undo()
        src, dest, r2, _ = self._run(tmp_path, resume=False)
        assert r2.state == CellState.DONE
        assert r2.resumed is False and r2.reused_files == []

    def test_source_remains_untouched_through_resume(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, 1)
        src, dest, _, _ = self._run(tmp_path)
        monkeypatch.undo()
        self._run(tmp_path, resume=True)
        for rel, data in FILES.items():
            assert (src / rel).read_bytes() == data


def _count_copies(monkeypatch):
    real = offload_mod._copy_with_retries
    calls = {"n": 0}

    def wrapper(src, dst, retries, log_cb):
        calls["n"] += 1
        return real(src, dst, retries, log_cb)

    monkeypatch.setattr(offload_mod, "_copy_with_retries", wrapper)
    return calls


class TestFindResumableStaging:
    def test_none_when_no_staging(self, tmp_path):
        s = OffloadSource(label="A001", path=tmp_path / "card")
        d = OffloadDest(label="NAS", path=tmp_path / "nas")
        assert find_resumable_staging(s, d) is None

    def test_none_for_different_source(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, 1)
        src = _populate(tmp_path / "CARD_A")
        dest = OffloadDest(label="NAS", path=tmp_path / "nas")
        dest.path.mkdir()
        run_offload([OffloadSource(label="A001", path=src)], [dest],
                    OffloadConfig(), MagicMock(), MagicMock())
        other = OffloadSource(label="A001", path=tmp_path / "OTHER_CARD")
        assert find_resumable_staging(other, dest) is None

    def test_discard_stale_staging_removes_dir(self, tmp_path, monkeypatch):
        _interrupt_after(monkeypatch, 1)
        src = _populate(tmp_path / "CARD_A")
        dest = OffloadDest(label="NAS", path=tmp_path / "nas")
        dest.path.mkdir()
        run_offload([OffloadSource(label="A001", path=src)], [dest],
                    OffloadConfig(), MagicMock(), MagicMock())
        source = OffloadSource(label="A001", path=src)
        staging, _ = find_resumable_staging(source, dest)
        discard_stale_staging(staging)
        assert not staging.exists()
        assert find_resumable_staging(source, dest) is None
