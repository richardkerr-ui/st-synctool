"""
Tests for gui/merge_tab.py::ApplyWorker.run.

Exercises action dispatch, signal emissions, rescan-drift detection,
manifest upload, project registry recording, and error handling.
All external I/O is mocked; run() is called synchronously.
"""

import sys
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, ANY

import pytest
from PyQt6.QtWidgets import QApplication

from gui.merge_tab import ApplyWorker
from core.merge_ops import ACT_PUSH, ACT_PULL, ACT_DELETE_LOCAL, ACT_DELETE_SERVER, ACT_SKIP
from core.manifest import MANIFEST_FILENAME

# One QApplication for the whole module (PyQt6 signal/slot requires it)
_app = QApplication.instance() or QApplication(sys.argv[:1])

DRIVE_URL = "https://drive.google.com/drive/folders/abc123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _DR:
    """Minimal stand-in for DiffResult (path + state.name)."""
    def __init__(self, path, state_name):
        self.path = path
        self.state = SimpleNamespace(name=state_name)


def _worker(tmp_path, actions=None, rescan=False, conflict_count=0):
    local = tmp_path / "local"
    srv   = tmp_path / "server"
    local.mkdir(exist_ok=True)
    srv.mkdir(exist_ok=True)
    (local / MANIFEST_FILENAME).write_text("{}")
    return ApplyWorker(
        actions=actions if actions is not None else {},
        local_path=str(local),
        server_path=str(srv),
        base_manifest={"files": {}},
        yours_manifest={"files": {}},
        server_manifest={"files": {}},
        preserve_on_overwrite=True,
        rescan_before_apply=rescan,
        conflict_count=conflict_count,
    )


def _tap(worker):
    """Connect all signals to MagicMocks; return the mock namespace."""
    s = SimpleNamespace(
        finished=MagicMock(), error=MagicMock(),
        progress=MagicMock(), log=MagicMock(),
        rescan_conflict=MagicMock(),
    )
    worker.finished.connect(s.finished)
    worker.error.connect(s.error)
    worker.progress.connect(s.progress)
    worker.log.connect(s.log)
    worker.rescan_conflict.connect(s.rescan_conflict)
    return s


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mocks(monkeypatch, tmp_path):
    """Patch every external call made by ApplyWorker.run."""
    import gui.merge_tab as mt

    m = MagicMock()
    m.generate_manifest_fast.return_value = {"files": {}, "project_id": ""}
    m.build_server_manifest.return_value  = {"files": {}}
    m.three_way_diff.return_value         = []
    m.save_manifest.return_value          = [Path("/tmp/fake_manifest.json")]
    m.push_file.return_value              = True
    m.pull_file.return_value              = True
    m.delete_local.return_value           = True
    m.delete_server.return_value          = True

    monkeypatch.setattr(mt,               "generate_manifest_fast", m.generate_manifest_fast)
    monkeypatch.setattr(mt,               "_build_server_manifest", m.build_server_manifest)
    monkeypatch.setattr(mt,               "three_way_diff",         m.three_way_diff)
    monkeypatch.setattr(mt,               "save_manifest",          m.save_manifest)
    monkeypatch.setattr(mt,               "is_gdrive_url",          lambda s: False)
    monkeypatch.setattr(mt.merge_ops,     "push_file",              m.push_file)
    monkeypatch.setattr(mt.merge_ops,     "pull_file",              m.pull_file)
    monkeypatch.setattr(mt.merge_ops,     "delete_local",           m.delete_local)
    monkeypatch.setattr(mt.merge_ops,     "delete_server",          m.delete_server)
    monkeypatch.setattr(mt.project_registry, "record_merge",        MagicMock())
    monkeypatch.setattr(mt.project_registry, "upsert_project",      MagicMock())
    return m


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class TestSignals:
    def test_finished_emitted_on_success(self, tmp_path, mocks):
        w = _worker(tmp_path)
        s = _tap(w)
        w.run()
        s.finished.assert_called_once()
        s.error.assert_not_called()

    def test_error_emitted_on_unhandled_exception(self, tmp_path, mocks, monkeypatch):
        import gui.merge_tab as mt
        monkeypatch.setattr(mt, "generate_manifest_fast",
                            MagicMock(side_effect=RuntimeError("boom")))
        w = _worker(tmp_path)
        s = _tap(w)
        w.run()
        s.error.assert_called_once()
        s.finished.assert_not_called()

    def test_progress_reaches_100_on_success(self, tmp_path, mocks):
        w = _worker(tmp_path)
        s = _tap(w)
        w.run()
        pcts = [c.args[0] for c in s.progress.call_args_list]
        assert 100 in pcts

    def test_progress_hits_92_before_manifest_regen(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={"a.mov": ACT_PUSH})
        s = _tap(w)
        w.run()
        pcts = [c.args[0] for c in s.progress.call_args_list]
        assert 92 in pcts


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------

class TestActionDispatch:
    def test_empty_actions_emits_finished_with_empty_lists(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={})
        s = _tap(w)
        w.run()
        result = s.finished.call_args.args[0]
        assert result["success"] == []
        assert result["failed"]  == []
        assert result["skipped"] == []

    def test_skip_action_adds_to_skipped(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={"clip.mov": ACT_SKIP})
        s = _tap(w)
        w.run()
        assert "clip.mov" in s.finished.call_args.args[0]["skipped"]

    def test_push_calls_push_file_and_adds_to_success(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={"clip.mov": ACT_PUSH})
        s = _tap(w)
        w.run()
        mocks.push_file.assert_called_once_with(
            "clip.mov", w.local_path, w.server_path,
            preserve_on_overwrite=True, log_cb=ANY,
        )
        assert "clip.mov" in s.finished.call_args.args[0]["success"]

    def test_pull_calls_pull_file_and_adds_to_success(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={"clip.mov": ACT_PULL})
        s = _tap(w)
        w.run()
        mocks.pull_file.assert_called_once()
        assert "clip.mov" in s.finished.call_args.args[0]["success"]

    def test_delete_local_calls_delete_local(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={"clip.mov": ACT_DELETE_LOCAL})
        s = _tap(w)
        w.run()
        mocks.delete_local.assert_called_once()
        assert "clip.mov" in s.finished.call_args.args[0]["success"]

    def test_delete_server_calls_delete_server(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={"clip.mov": ACT_DELETE_SERVER})
        s = _tap(w)
        w.run()
        mocks.delete_server.assert_called_once()
        assert "clip.mov" in s.finished.call_args.args[0]["success"]

    def test_unknown_action_adds_to_skipped_with_warning(self, tmp_path, mocks):
        w = _worker(tmp_path, actions={"clip.mov": "DoSomethingWeird"})
        s = _tap(w)
        w.run()
        assert "clip.mov" in s.finished.call_args.args[0]["skipped"]
        warnings = [c.args for c in s.log.call_args_list if len(c.args) >= 2 and c.args[1] == "warning"]
        assert any("Unknown action" in w_[0] for w_ in warnings)

    def test_failed_op_adds_to_failed(self, tmp_path, mocks):
        mocks.push_file.return_value = False
        w = _worker(tmp_path, actions={"clip.mov": ACT_PUSH})
        s = _tap(w)
        w.run()
        assert "clip.mov" in s.finished.call_args.args[0]["failed"]


# ---------------------------------------------------------------------------
# Rename and verified-entry capture
# ---------------------------------------------------------------------------

class TestRenameAndVerify:
    def test_dict_result_with_renamed_to_records_rename(self, tmp_path, mocks):
        mocks.push_file.return_value = {"renamed_to": "clip-rk.mov", "method": "sha256"}
        w = _worker(tmp_path, actions={"clip.mov": ACT_PUSH})
        s = _tap(w)
        w.run()
        renames = s.finished.call_args.args[0]["renames"]
        assert len(renames) == 1
        assert renames[0]["from"] == "clip.mov"
        assert renames[0]["to"]   == "clip-rk.mov"

    def test_dict_result_with_post_checksums_enriches_manifest(self, tmp_path, mocks):
        mocks.push_file.return_value = {
            "renamed_to": None,
            "post": {"sha256": "aabbcc"},
            "method": "sha256",
        }
        mocks.generate_manifest_fast.return_value = {
            "files": {"clip.mov": {"size": 1024}},
            "project_id": "",
        }
        w = _worker(tmp_path, actions={"clip.mov": ACT_PUSH})
        s = _tap(w)
        w.run()
        enriched_manifest = mocks.save_manifest.call_args.args[0]
        entry = enriched_manifest["files"]["clip.mov"]
        assert entry.get("checksums", {}).get("sha256") == "aabbcc"
        assert entry.get("hash_algorithm") == "sha256"


# ---------------------------------------------------------------------------
# Rescan-before-apply
# ---------------------------------------------------------------------------

class TestRescan:
    def test_no_drift_proceeds_to_finished(self, tmp_path, mocks):
        mocks.three_way_diff.side_effect = [
            [_DR("a.mov", "YOURS_ONLY")],  # fresh scan
            [_DR("a.mov", "YOURS_ONLY")],  # original scan
        ]
        w = _worker(tmp_path, actions={"a.mov": ACT_PUSH}, rescan=True)
        s = _tap(w)
        w.run()
        s.finished.assert_called_once()
        s.rescan_conflict.assert_not_called()

    def test_drift_emits_rescan_conflict_and_aborts(self, tmp_path, mocks):
        mocks.three_way_diff.side_effect = [
            [_DR("a.mov", "CONFLICT")],    # fresh: state changed
            [_DR("a.mov", "YOURS_ONLY")],  # original
        ]
        w = _worker(tmp_path, actions={"a.mov": ACT_PUSH}, rescan=True)
        s = _tap(w)
        w.run()
        s.rescan_conflict.assert_called_once()
        conflicted = s.rescan_conflict.call_args.args[0]
        assert "a.mov" in conflicted
        s.finished.assert_not_called()


# ---------------------------------------------------------------------------
# Manifest upload
# ---------------------------------------------------------------------------

class TestManifestUpload:
    def test_local_server_copies_manifest_file(self, tmp_path, mocks, monkeypatch):
        copied = {}
        monkeypatch.setattr("gui.merge_tab.shutil.copy2",
                            lambda src, dst: copied.update({"src": src, "dst": dst}))
        w = _worker(tmp_path, actions={"a.mov": ACT_PUSH})
        s = _tap(w)
        w.run()
        assert MANIFEST_FILENAME in str(copied.get("src", ""))

    def test_gdrive_server_calls_rclone_copyto(self, tmp_path, mocks, monkeypatch):
        import gui.merge_tab as mt
        copyto = MagicMock()
        monkeypatch.setattr(mt, "is_gdrive_url", lambda s: "drive.google.com" in str(s))
        monkeypatch.setattr(mt, "gdrive_url_to_rclone", lambda s: ("gdrive:abc", ["--flag"]))
        monkeypatch.setattr(mt.rclone_bridge, "copyto", copyto)
        w = _worker(tmp_path)
        w.server_path = DRIVE_URL
        s = _tap(w)
        w.run()
        copyto.assert_called_once()

    def test_missing_manifest_file_emits_error(self, tmp_path, mocks):
        w = _worker(tmp_path)
        (w.local_path / MANIFEST_FILENAME).unlink()
        s = _tap(w)
        w.run()
        s.error.assert_called_once()
        s.finished.assert_not_called()


# ---------------------------------------------------------------------------
# Project registry
# ---------------------------------------------------------------------------

class TestProjectRegistry:
    def test_record_merge_called_when_project_id_present(self, tmp_path, mocks, monkeypatch):
        import gui.merge_tab as mt
        record = MagicMock()
        monkeypatch.setattr(mt.project_registry, "record_merge", record)
        mocks.generate_manifest_fast.return_value = {"files": {}, "project_id": "proj-42"}
        w = _worker(tmp_path, actions={"a.mov": ACT_PUSH}, conflict_count=2)
        s = _tap(w)
        w.run()
        record.assert_called_once()
        call_kwargs = record.call_args
        assert call_kwargs.args[0] == "proj-42"

    def test_record_merge_not_called_when_project_id_empty(self, tmp_path, mocks, monkeypatch):
        import gui.merge_tab as mt
        record = MagicMock()
        monkeypatch.setattr(mt.project_registry, "record_merge", record)
        mocks.generate_manifest_fast.return_value = {"files": {}, "project_id": ""}
        w = _worker(tmp_path)
        _tap(w)
        w.run()
        record.assert_not_called()

    def test_registry_failure_logs_warning_and_still_emits_finished(self, tmp_path, mocks, monkeypatch):
        import gui.merge_tab as mt
        monkeypatch.setattr(mt.project_registry, "record_merge",
                            MagicMock(side_effect=RuntimeError("db locked")))
        mocks.generate_manifest_fast.return_value = {"files": {}, "project_id": "proj-1"}
        w = _worker(tmp_path, actions={"a.mov": ACT_PUSH})
        s = _tap(w)
        w.run()
        s.finished.assert_called_once()
        warnings = [c.args[0] for c in s.log.call_args_list
                    if len(c.args) >= 2 and c.args[1] == "warning"]
        assert any("project registry" in w_.lower() for w_ in warnings)
