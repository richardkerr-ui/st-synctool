"""Tests for core/projects.py — _load, _save, and public registry API.

_load and _save have 12 callers each and had zero test coverage.
All tests redirect PROJECTS_REGISTRY to a tmp path to avoid touching real state.
"""

import json
import pytest
from pathlib import Path

import core.projects as proj


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Redirect every test to a fresh temporary registry file."""
    registry_path = tmp_path / "projects.json"
    monkeypatch.setattr(proj, "PROJECTS_REGISTRY", registry_path)
    return registry_path


# ── _load ─────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_returns_empty_dict_when_file_missing(self):
        result = proj._load()
        assert result == {}

    def test_returns_parsed_dict_when_file_exists(self, isolated_registry):
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(json.dumps({"key": "value"}))
        assert proj._load() == {"key": "value"}

    def test_returns_empty_dict_on_corrupt_json(self, isolated_registry):
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text("not valid json {{{{")
        assert proj._load() == {}

    def test_preserves_nested_structure(self, isolated_registry):
        data = {"p1": {"project_id": "p1", "history": [{"merged_at": "2026-01-01"}]}}
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(json.dumps(data))
        assert proj._load()["p1"]["history"][0]["merged_at"] == "2026-01-01"


# ── _save ─────────────────────────────────────────────────────────────────────

class TestSave:
    def test_creates_file_if_missing(self, isolated_registry):
        proj._save({"x": 1})
        assert isolated_registry.exists()

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        deep = tmp_path / "a" / "b" / "c" / "projects.json"
        monkeypatch.setattr(proj, "PROJECTS_REGISTRY", deep)
        proj._save({"y": 2})
        assert deep.exists()

    def test_roundtrip_preserves_data(self, isolated_registry):
        data = {"abc": {"name": "test"}}
        proj._save(data)
        assert json.loads(isolated_registry.read_text()) == data

    def test_overwrites_existing_file(self, isolated_registry):
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(json.dumps({"old": True}))
        proj._save({"new": True})
        assert json.loads(isolated_registry.read_text()) == {"new": True}

    def test_output_is_valid_json(self):
        proj._save({"nested": {"list": [1, 2, 3]}})
        # If _load doesn't raise, the JSON written is valid
        assert proj._load() == {"nested": {"list": [1, 2, 3]}}


# ── Public API (exercises _load/_save integration) ───────────────────────────

class TestUpsertAndGet:
    def test_upsert_creates_project(self):
        entry = proj.upsert_project("id1", "/local/path", "/server/path")
        assert entry["project_id"] == "id1"
        assert proj.get_project("id1") is not None

    def test_upsert_preserves_created_at_on_update(self):
        first = proj.upsert_project("id2", "/a", "/b")
        second = proj.upsert_project("id2", "/a", "/b")
        assert first["created_at"] == second["created_at"]

    def test_upsert_display_name_defaults_to_folder_name(self):
        entry = proj.upsert_project("id3", "/some/project_folder", "/s")
        assert entry["display_name"] == "project_folder"

    def test_get_missing_project_returns_none(self):
        assert proj.get_project("nonexistent") is None

    def test_list_projects_sorted_by_name(self):
        proj.upsert_project("z", "/z", "/z", display_name="Zebra")
        proj.upsert_project("a", "/a", "/a", display_name="Alpha")
        names = [p["display_name"] for p in proj.list_projects()]
        assert names == sorted(names, key=str.lower)


class TestRecordMerge:
    def test_record_merge_appends_history(self):
        proj.upsert_project("m1", "/l", "/s")
        proj.record_merge("m1", files_changed=5, conflicts=2, preserve_renames=1)
        p = proj.get_project("m1")
        assert len(p["history"]) == 1
        assert p["history"][0]["files_changed"] == 5

    def test_record_merge_updates_last_merged_at(self):
        proj.upsert_project("m2", "/l", "/s")
        proj.record_merge("m2", files_changed=0, conflicts=0, preserve_renames=0)
        p = proj.get_project("m2")
        assert p["last_merged_at"] != ""

    def test_record_merge_on_unknown_project_is_noop(self):
        # Should not raise
        proj.record_merge("ghost_id", files_changed=1, conflicts=0, preserve_renames=0)


class TestFindByLocalPath:
    def test_finds_exact_match(self):
        proj.upsert_project("fp1", "/exact/path", "/s")
        result = proj.find_by_local_path("/exact/path")
        assert result is not None
        assert result["project_id"] == "fp1"

    def test_returns_none_for_no_match(self):
        assert proj.find_by_local_path("/no/such/path") is None

    def test_does_not_match_substring(self):
        proj.upsert_project("fp2", "/my/project", "/s")
        assert proj.find_by_local_path("/my") is None
