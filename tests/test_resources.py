"""Tests for M7.1 binary discovery (utils/resources.py)."""

import sys
from pathlib import Path

from utils import resources


def test_not_frozen_uses_path(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert resources.is_frozen() is False
    assert resources.bundle_bin_dirs() == []
    # Falls back to PATH: a binary that exists everywhere.
    assert resources.find_binary("sh") == __import__("shutil").which("sh")


def test_find_binary_missing_returns_none(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert resources.find_binary("definitely-not-a-real-binary-xyz") is None


def test_frozen_prefers_bundled_macos_dir(monkeypatch, tmp_path):
    macos = tmp_path / "ST SyncTool.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    fake_exe = macos / "STSyncTool"
    fake_exe.write_text("#!/bin/sh\n")
    rclone = macos / "rclone"
    rclone.write_text("binary")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert resources.is_frozen() is True
    assert macos in resources.bundle_bin_dirs()
    assert resources.find_binary("rclone") == str(rclone)


def test_frozen_finds_in_resources_dir(monkeypatch, tmp_path):
    contents = tmp_path / "ST SyncTool.app" / "Contents"
    macos = contents / "MacOS"; macos.mkdir(parents=True)
    resources_dir = contents / "Resources"; resources_dir.mkdir()
    (macos / "STSyncTool").write_text("#!/bin/sh\n")
    ffprobe = resources_dir / "ffprobe"
    ffprobe.write_text("binary")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos / "STSyncTool"))

    assert resources.find_binary("ffprobe") == str(ffprobe)


def test_prepend_bundle_to_path_noop_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = {"PATH": "/usr/bin"}
    resources.prepend_bundle_to_path(env)
    assert env["PATH"] == "/usr/bin"


def test_prepend_bundle_to_path_frozen(monkeypatch, tmp_path):
    macos = tmp_path / "ST SyncTool.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "STSyncTool").write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos / "STSyncTool"))
    env = {"PATH": "/usr/bin"}
    resources.prepend_bundle_to_path(env)
    assert env["PATH"].startswith(str(macos))
    assert env["PATH"].endswith("/usr/bin")


def test_frozen_falls_back_to_path_when_not_bundled(monkeypatch, tmp_path):
    macos = tmp_path / "app" / "Contents" / "MacOS"; macos.mkdir(parents=True)
    (macos / "STSyncTool").write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos / "STSyncTool"))
    # rclone not bundled -> PATH lookup (same as shutil.which)
    assert resources.find_binary("sh") == __import__("shutil").which("sh")
