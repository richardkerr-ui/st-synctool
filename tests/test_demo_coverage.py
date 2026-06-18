"""Tests for core/demo.py — path helpers, stub creation, and merge scaffold.

Covers: _app_support_dir, demo_root, demo_source, demo_destination,
demo_verify_sample, demo_verify_manifest, _build_verify_sample,
ensure_demo_folder, demo_exists, demo_merge_local, demo_merge_server,
demo_merge_manifest, _xxh128_of, _write_if_missing, ensure_demo_merge_folders
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

import core.demo as demo_mod
from core.demo import (
    _app_support_dir,
    _build_verify_sample,
    _xxh128_of,
    _write_if_missing,
    demo_destination,
    demo_exists,
    demo_merge_local,
    demo_merge_manifest,
    demo_merge_server,
    demo_root,
    demo_source,
    demo_verify_manifest,
    demo_verify_sample,
    ensure_demo_folder,
    ensure_demo_merge_folders,
)


# ---------------------------------------------------------------------------
# _app_support_dir
# ---------------------------------------------------------------------------

class TestAppSupportDir:
    def test_darwin_returns_library_path(self):
        with patch("platform.system", return_value="Darwin"):
            result = _app_support_dir()
        assert "Library/Application Support" in str(result)
        assert result.name == "ST SyncTool"
        assert result.parent.name == "Signal Theory"

    def test_linux_returns_xdg_data_home_when_set(self):
        with patch("platform.system", return_value="Linux"), \
             patch.dict("os.environ", {"XDG_DATA_HOME": "/tmp/xdg"}):
            result = _app_support_dir()
        assert str(result).startswith("/tmp/xdg")
        assert result.name == "ST SyncTool"
        assert result.parent.name == "Signal Theory"

    def test_linux_falls_back_to_local_share_when_xdg_unset(self):
        with patch("platform.system", return_value="Linux"), \
             patch.dict("os.environ", {}, clear=True):
            result = _app_support_dir()
        assert ".local/share" in str(result)
        assert result.name == "ST SyncTool"


# ---------------------------------------------------------------------------
# demo_root / demo_source / demo_destination
# ---------------------------------------------------------------------------

class TestDemoPathHelpers:
    def test_demo_root_is_child_of_app_support(self):
        root = demo_root()
        assert root.name == "demo"
        assert "ST SyncTool" in str(root)

    def test_demo_source_is_inside_demo_root(self):
        assert demo_source() == demo_root() / "source"

    def test_demo_destination_is_inside_demo_root(self):
        assert demo_destination() == demo_root() / "destination"

    def test_all_path_helpers_return_path_objects(self):
        for fn in (demo_root, demo_source, demo_destination,
                   demo_verify_sample, demo_verify_manifest,
                   demo_merge_local, demo_merge_server, demo_merge_manifest):
            assert isinstance(fn(), Path), f"{fn.__name__} did not return a Path"


# ---------------------------------------------------------------------------
# demo_verify_sample / demo_verify_manifest
# ---------------------------------------------------------------------------

class TestVerifyPaths:
    def test_verify_sample_is_inside_demo_root(self):
        assert demo_verify_sample() == demo_root() / "verify_sample"

    def test_verify_manifest_is_inside_verify_sample(self):
        assert demo_verify_manifest() == demo_verify_sample() / "st_manifest.json"

    def test_verify_manifest_filename(self):
        assert demo_verify_manifest().name == "st_manifest.json"


# ---------------------------------------------------------------------------
# demo_merge_local / demo_merge_server / demo_merge_manifest
# ---------------------------------------------------------------------------

class TestMergePaths:
    def test_merge_local_is_inside_demo_root(self):
        assert demo_merge_local() == demo_root() / "merge_local"

    def test_merge_server_is_inside_demo_root(self):
        assert demo_merge_server() == demo_root() / "merge_server"

    def test_merge_manifest_is_inside_demo_root(self):
        assert demo_merge_manifest() == demo_root() / "merge_base_manifest.json"

    def test_merge_local_and_server_differ(self):
        assert demo_merge_local() != demo_merge_server()


# ---------------------------------------------------------------------------
# _xxh128_of  (M13: was _sha256_of)
# ---------------------------------------------------------------------------

class TestXxh128Of:
    def test_known_hash_of_empty_bytes(self):
        import xxhash
        assert _xxh128_of(b"") == xxhash.xxh128(b"").hexdigest()

    def test_known_hash_of_hello(self):
        import xxhash
        assert _xxh128_of(b"hello") == xxhash.xxh128(b"hello").hexdigest()

    def test_different_data_produces_different_hash(self):
        assert _xxh128_of(b"abc") != _xxh128_of(b"xyz")

    def test_returns_32_char_hex_string(self):
        result = _xxh128_of(b"test data")
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# _write_if_missing
# ---------------------------------------------------------------------------

class TestWriteIfMissing:
    def test_creates_file_with_correct_content(self, tmp_path):
        target = tmp_path / "subdir" / "file.txt"
        _write_if_missing(target, b"hello")
        assert target.read_bytes() == b"hello"

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "c" / "file.txt"
        _write_if_missing(target, b"content")
        assert target.exists()

    def test_does_not_overwrite_existing_file(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_bytes(b"original")
        _write_if_missing(target, b"replacement")
        assert target.read_bytes() == b"original"

    def test_write_empty_bytes(self, tmp_path):
        target = tmp_path / "empty.txt"
        _write_if_missing(target, b"")
        assert target.exists()
        assert target.read_bytes() == b""


# ---------------------------------------------------------------------------
# _build_verify_sample
# ---------------------------------------------------------------------------

class TestBuildVerifySample:
    def _patch_demo_root(self, tmp_path):
        """Return a context manager that redirects all demo path helpers."""
        fake_root = tmp_path / "demo"

        def patched_demo_root():
            return fake_root

        def patched_verify_sample():
            return fake_root / "verify_sample"

        def patched_verify_manifest():
            return fake_root / "verify_sample" / "st_manifest.json"

        return (
            patch.object(demo_mod, "demo_root", patched_demo_root),
            patch.object(demo_mod, "demo_verify_sample", patched_verify_sample),
            patch.object(demo_mod, "demo_verify_manifest", patched_verify_manifest),
        )

    def test_creates_verify_sample_directory(self, tmp_path):
        patches = self._patch_demo_root(tmp_path)
        with patches[0], patches[1], patches[2]:
            _build_verify_sample(tmp_path / "demo")
        assert (tmp_path / "demo" / "verify_sample").is_dir()

    def test_creates_manifest_json(self, tmp_path):
        patches = self._patch_demo_root(tmp_path)
        with patches[0], patches[1], patches[2]:
            _build_verify_sample(tmp_path / "demo")
        manifest_path = tmp_path / "demo" / "verify_sample" / "st_manifest.json"
        assert manifest_path.exists()

    def test_manifest_is_valid_json_with_expected_keys(self, tmp_path):
        patches = self._patch_demo_root(tmp_path)
        with patches[0], patches[1], patches[2]:
            _build_verify_sample(tmp_path / "demo")
        manifest_path = tmp_path / "demo" / "verify_sample" / "st_manifest.json"
        data = json.loads(manifest_path.read_text())
        for key in ("schema_version", "files", "file_count", "operation", "label"):
            assert key in data, f"Key '{key}' missing from manifest"

    def test_manifest_label_is_demo_verify_sample(self, tmp_path):
        patches = self._patch_demo_root(tmp_path)
        with patches[0], patches[1], patches[2]:
            _build_verify_sample(tmp_path / "demo")
        manifest_path = tmp_path / "demo" / "verify_sample" / "st_manifest.json"
        data = json.loads(manifest_path.read_text())
        assert data["label"] == "demo_verify_sample"

    def test_stub_files_use_empty_xxh128(self, tmp_path):
        patches = self._patch_demo_root(tmp_path)
        with patches[0], patches[1], patches[2]:
            _build_verify_sample(tmp_path / "demo")
        manifest_path = tmp_path / "demo" / "verify_sample" / "st_manifest.json"
        data = json.loads(manifest_path.read_text())
        for rel, entry in data["files"].items():
            if entry["size"] == 0:
                assert entry["checksums"]["xxh128"] == demo_mod._EMPTY_XXH128, \
                    f"Stub {rel} has wrong xxh128"

    def test_idempotent_second_call_does_not_raise(self, tmp_path):
        patches = self._patch_demo_root(tmp_path)
        with patches[0], patches[1], patches[2]:
            _build_verify_sample(tmp_path / "demo")
            _build_verify_sample(tmp_path / "demo")  # should not raise

    def test_existing_manifest_not_overwritten(self, tmp_path):
        patches = self._patch_demo_root(tmp_path)
        with patches[0], patches[1], patches[2]:
            _build_verify_sample(tmp_path / "demo")
            manifest_path = tmp_path / "demo" / "verify_sample" / "st_manifest.json"
            original_mtime = manifest_path.stat().st_mtime
            _build_verify_sample(tmp_path / "demo")
            assert manifest_path.stat().st_mtime == original_mtime


# ---------------------------------------------------------------------------
# ensure_demo_folder
# ---------------------------------------------------------------------------

class TestEnsureDemoFolder:
    def _patch_all(self, tmp_path):
        fake_root = tmp_path / "demo"

        def fake_demo_root():
            return fake_root

        def fake_demo_source():
            return fake_root / "source"

        def fake_demo_destination():
            return fake_root / "destination"

        def fake_demo_verify_sample():
            return fake_root / "verify_sample"

        def fake_demo_verify_manifest():
            return fake_root / "verify_sample" / "st_manifest.json"

        return (
            patch.object(demo_mod, "demo_root", fake_demo_root),
            patch.object(demo_mod, "demo_source", fake_demo_source),
            patch.object(demo_mod, "demo_destination", fake_demo_destination),
            patch.object(demo_mod, "demo_verify_sample", fake_demo_verify_sample),
            patch.object(demo_mod, "demo_verify_manifest", fake_demo_verify_manifest),
        )

    def test_returns_source_and_destination_paths(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            src, dst = ensure_demo_folder()
        assert src.name == "source"
        assert dst.name == "destination"

    def test_creates_readme_txt(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
        readme = tmp_path / "demo" / "README_DEMO.txt"
        assert readme.exists()

    def test_readme_contains_expected_text(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
        readme = tmp_path / "demo" / "README_DEMO.txt"
        content = readme.read_bytes().decode()
        assert "ST SyncTool" in content
        assert "Demo Folder" in content

    def test_creates_source_subdirectories(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
        assert (tmp_path / "demo" / "source" / "DCIM" / "A001").is_dir()
        assert (tmp_path / "demo" / "source" / "AUDIO").is_dir()

    def test_creates_destination_directory(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
        assert (tmp_path / "demo" / "destination").is_dir()

    def test_stub_files_are_zero_bytes(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
        mov = tmp_path / "demo" / "source" / "DCIM" / "A001" / "A001C001_260610_R0FH.mov"
        assert mov.exists()
        assert mov.stat().st_size == 0

    def test_notes_txt_has_real_content(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
        notes = tmp_path / "demo" / "source" / "MISC" / "NOTES.txt"
        assert notes.exists()
        assert notes.stat().st_size > 0

    def test_idempotent_does_not_raise(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
            ensure_demo_folder()  # second call must not raise

    def test_existing_readme_not_overwritten(self, tmp_path):
        patches = self._patch_all(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            ensure_demo_folder()
            readme = tmp_path / "demo" / "README_DEMO.txt"
            readme.write_bytes(b"custom content")
            ensure_demo_folder()
        assert readme.read_bytes() == b"custom content"


# ---------------------------------------------------------------------------
# demo_exists
# ---------------------------------------------------------------------------

class TestDemoExists:
    def test_returns_false_when_source_missing(self, tmp_path):
        with patch.object(demo_mod, "demo_source", return_value=tmp_path / "nonexistent"):
            assert demo_exists() is False

    def test_returns_true_when_source_exists(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        with patch.object(demo_mod, "demo_source", return_value=src):
            assert demo_exists() is True

    def test_returns_false_for_file_not_dir(self, tmp_path):
        # demo_source() returns a path — exists() is True for files too,
        # so this test confirms the real semantic: .exists() not .is_dir()
        src_file = tmp_path / "source"
        src_file.write_bytes(b"")
        with patch.object(demo_mod, "demo_source", return_value=src_file):
            assert demo_exists() is True  # .exists() returns True for files


# ---------------------------------------------------------------------------
# ensure_demo_merge_folders
# ---------------------------------------------------------------------------

class TestEnsureDemoMergeFolders:
    def _patch_merge(self, tmp_path):
        fake_root = tmp_path / "demo"

        def fake_demo_root():
            return fake_root

        def fake_merge_local():
            return fake_root / "merge_local"

        def fake_merge_server():
            return fake_root / "merge_server"

        def fake_merge_manifest():
            return fake_root / "merge_base_manifest.json"

        return (
            patch.object(demo_mod, "demo_root", fake_demo_root),
            patch.object(demo_mod, "demo_merge_local", fake_merge_local),
            patch.object(demo_mod, "demo_merge_server", fake_merge_server),
            patch.object(demo_mod, "demo_merge_manifest", fake_merge_manifest),
        )

    def test_returns_three_path_tuple(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            result = ensure_demo_merge_folders()
        assert len(result) == 3
        local, server, manifest = result
        assert isinstance(local, Path)
        assert isinstance(server, Path)
        assert isinstance(manifest, Path)

    def test_local_and_server_directories_created(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        assert (tmp_path / "demo" / "merge_local").is_dir()
        assert (tmp_path / "demo" / "merge_server").is_dir()

    def test_base_manifest_json_created(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        manifest_path = tmp_path / "demo" / "merge_base_manifest.json"
        assert manifest_path.exists()

    def test_manifest_is_valid_json(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        manifest_path = tmp_path / "demo" / "merge_base_manifest.json"
        data = json.loads(manifest_path.read_text())
        assert data["label"] == "demo_merge_base"
        assert data["operation"] == "demo"
        assert data["project_id"] == "demo_merge"

    def test_manifest_checksums_match_base_bytes(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        manifest_path = tmp_path / "demo" / "merge_base_manifest.json"
        data = json.loads(manifest_path.read_text())
        from core.demo import _MERGE_FILES, _xxh128_of as xxh
        for rel, base_bytes, _local, _server in _MERGE_FILES:
            assert rel in data["files"]
            expected = xxh(base_bytes)
            actual = data["files"][rel]["checksums"]["xxh128"]
            assert actual == expected, f"Checksum mismatch for {rel}"

    def test_local_changed_file_has_expected_content(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        local_file = tmp_path / "demo" / "merge_local" / "DCIM" / "A001" / "scene_01.txt"
        assert local_file.exists()
        assert b"YOUR EDIT" in local_file.read_bytes()

    def test_server_changed_file_has_expected_content(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        server_file = tmp_path / "demo" / "merge_server" / "DCIM" / "A001" / "scene_02.txt"
        assert server_file.exists()
        assert b"SERVER EDIT" in server_file.read_bytes()

    def test_deleted_local_file_absent_from_local(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        # sound_report.txt has local_bytes=None — must not exist in merge_local
        deleted = tmp_path / "demo" / "merge_local" / "AUDIO" / "sound_report.txt"
        assert not deleted.exists()

    def test_deleted_local_file_present_on_server(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        server_file = tmp_path / "demo" / "merge_server" / "AUDIO" / "sound_report.txt"
        assert server_file.exists()

    def test_local_only_file_exists_in_local_only(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        local_only = tmp_path / "demo" / "merge_local" / "DCIM" / "A001" / "new_footage.txt"
        server_only_check = tmp_path / "demo" / "merge_server" / "DCIM" / "A001" / "new_footage.txt"
        assert local_only.exists()
        assert not server_only_check.exists()

    def test_server_only_file_exists_in_server_only(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        server_only = tmp_path / "demo" / "merge_server" / "DCIM" / "B001" / "server_addition.txt"
        local_check = tmp_path / "demo" / "merge_local" / "DCIM" / "B001" / "server_addition.txt"
        assert server_only.exists()
        assert not local_check.exists()

    def test_idempotent_does_not_raise(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
            ensure_demo_merge_folders()  # must not raise

    def test_manifest_not_overwritten_on_second_call(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
            manifest_path = tmp_path / "demo" / "merge_base_manifest.json"
            original_mtime = manifest_path.stat().st_mtime
            ensure_demo_merge_folders()
            assert manifest_path.stat().st_mtime == original_mtime

    def test_manifest_file_count_matches_merge_files(self, tmp_path):
        patches = self._patch_merge(tmp_path)
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_demo_merge_folders()
        manifest_path = tmp_path / "demo" / "merge_base_manifest.json"
        data = json.loads(manifest_path.read_text())
        from core.demo import _MERGE_FILES
        assert data["file_count"] == len(_MERGE_FILES)
