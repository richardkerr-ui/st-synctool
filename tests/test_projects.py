"""Tests for core/projects.py — _load, _save, and public registry API.

_load and _save have 12 callers each and had zero test coverage.
All tests redirect PROJECTS_REGISTRY to a tmp path to avoid touching real state.
"""

import json
import pytest
from pathlib import Path

import core.projects as proj
from core.projects import _load, _save


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Redirect every test to a fresh temporary registry file."""
    registry_path = tmp_path / "projects.json"
    monkeypatch.setattr(proj, "PROJECTS_REGISTRY", registry_path)
    return registry_path


# ── _load ─────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_returns_empty_dict_when_file_missing(self):
        result = _load()
        assert result == {}

    def test_returns_parsed_dict_when_file_exists(self, isolated_registry):
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(json.dumps({"key": "value"}))
        assert _load() == {"key": "value"}

    def test_returns_empty_dict_on_corrupt_json(self, isolated_registry):
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text("not valid json {{{{")
        assert _load() == {}

    def test_preserves_nested_structure(self, isolated_registry):
        data = {"p1": {"project_id": "p1", "history": [{"merged_at": "2026-01-01"}]}}
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(json.dumps(data))
        assert _load()["p1"]["history"][0]["merged_at"] == "2026-01-01"


# ── _save ─────────────────────────────────────────────────────────────────────

class TestSave:
    def test_creates_file_if_missing(self, isolated_registry):
        _save({"x": 1})
        assert isolated_registry.exists()

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        deep = tmp_path / "a" / "b" / "c" / "projects.json"
        monkeypatch.setattr(proj, "PROJECTS_REGISTRY", deep)
        _save({"y": 2})
        assert deep.exists()

    def test_roundtrip_preserves_data(self, isolated_registry):
        data = {"abc": {"name": "test"}}
        _save(data)
        assert json.loads(isolated_registry.read_text()) == data

    def test_overwrites_existing_file(self, isolated_registry):
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(json.dumps({"old": True}))
        _save({"new": True})
        assert json.loads(isolated_registry.read_text()) == {"new": True}

    def test_output_is_valid_json(self):
        _save({"nested": {"list": [1, 2, 3]}})
        # If _load doesn't raise, the JSON written is valid
        assert _load() == {"nested": {"list": [1, 2, 3]}}


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


# ── Destination presets ────────────────────────────────────────────────────────

class TestDestPresets:
    def test_list_presets_empty_initially(self):
        assert proj.list_dest_presets() == []

    def test_save_and_list_preset(self):
        dests = [{"label": "NAS", "path": "/Volumes/NAS"}]
        proj.save_dest_preset("Shoot Day", dests)
        assert "Shoot Day" in proj.list_dest_presets()

    def test_get_preset_returns_saved_dests(self):
        dests = [{"label": "A", "path": "/a"}, {"label": "B", "path": "/b"}]
        proj.save_dest_preset("Two Drives", dests)
        assert proj.get_dest_preset("Two Drives") == dests

    def test_get_missing_preset_returns_empty_list(self):
        assert proj.get_dest_preset("Ghost") == []

    def test_save_preset_overwrites_existing(self):
        proj.save_dest_preset("P", [{"label": "old", "path": "/old"}])
        proj.save_dest_preset("P", [{"label": "new", "path": "/new"}])
        assert proj.get_dest_preset("P") == [{"label": "new", "path": "/new"}]

    def test_delete_preset_removes_it(self):
        proj.save_dest_preset("ToDelete", [{"label": "x", "path": "/x"}])
        proj.delete_dest_preset("ToDelete")
        assert "ToDelete" not in proj.list_dest_presets()
        assert proj.get_dest_preset("ToDelete") == []

    def test_delete_missing_preset_is_noop(self):
        proj.delete_dest_preset("NeverExisted")

    def test_list_presets_sorted_alphabetically(self):
        proj.save_dest_preset("Zebra", [])
        proj.save_dest_preset("Alpha", [])
        proj.save_dest_preset("Mango", [])
        assert proj.list_dest_presets() == ["Alpha", "Mango", "Zebra"]

    def test_presets_coexist_with_projects(self):
        proj.upsert_project("p1", "/local", "/server")
        proj.save_dest_preset("Drive Set", [{"label": "D", "path": "/d"}])
        assert proj.get_project("p1") is not None
        assert proj.get_dest_preset("Drive Set") == [{"label": "D", "path": "/d"}]


# ── Naming preferences ─────────────────────────────────────────────────────────

class TestNamingPreferences:
    def test_returns_none_when_not_set(self):
        assert proj.get_naming_preference("A001_*") is None

    def test_save_and_get_preference(self):
        proj.save_naming_preference("A001_*", "normalize")
        assert proj.get_naming_preference("A001_*") == "normalize"

    def test_overwrite_preference(self):
        proj.save_naming_preference("B002_*", "skip")
        proj.save_naming_preference("B002_*", "ask")
        assert proj.get_naming_preference("B002_*") == "ask"


# ── App settings ───────────────────────────────────────────────────────────────

class TestAppSettings:
    def test_returns_default_when_not_set(self):
        assert proj.get_app_setting("theme", "dark") == "dark"

    def test_returns_none_default_when_not_set(self):
        assert proj.get_app_setting("missing") is None

    def test_save_and_get_setting(self):
        proj.save_app_setting("last_tab", 2)
        assert proj.get_app_setting("last_tab") == 2

    def test_overwrite_setting(self):
        proj.save_app_setting("flag", True)
        proj.save_app_setting("flag", False)
        assert proj.get_app_setting("flag") is False
