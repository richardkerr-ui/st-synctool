"""Tests for core.dnd.folder_from_dropped_paths."""
from core.dnd import folder_from_dropped_paths


def test_directory_dropped_returns_it(tmp_path):
    d = tmp_path / "A001"
    d.mkdir()
    assert folder_from_dropped_paths([str(d)]) == str(d)


def test_file_dropped_returns_parent(tmp_path):
    f = tmp_path / "clip.mov"
    f.write_text("x")
    assert folder_from_dropped_paths([str(f)]) == str(tmp_path)


def test_first_directory_wins(tmp_path):
    f = tmp_path / "clip.mov"
    f.write_text("x")
    d = tmp_path / "A001"
    d.mkdir()
    # File listed first, but the directory is preferred.
    assert folder_from_dropped_paths([str(f), str(d)]) == str(d)


def test_nonexistent_path_falls_back_to_parent(tmp_path):
    ghost = tmp_path / "sub" / "missing.mov"
    assert folder_from_dropped_paths([str(ghost)]) == str(tmp_path / "sub")


def test_empty_and_blank_return_none():
    assert folder_from_dropped_paths([]) is None
    assert folder_from_dropped_paths(["", None]) is None
