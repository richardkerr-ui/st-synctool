"""Tests for core/cold_read.py — M14.2 cold-read plumbing.

These prove the PLUMBING only. They cannot prove coldness — a tmpfs/local read in
the test environment is always warm. The coldness proof is the real-device
divergence experiment in the ROADMAP M14.2 manual-checks table.
"""

import os
import sys

import pytest

from core import cold_read


# ── cold_open returns a usable binary handle ─────────────────────────────────

def test_cold_open_reads_full_contents(tmp_path):
    p = tmp_path / "clip.mov"
    data = b"\x00\x01\x02" * 5000
    p.write_bytes(data)
    with cold_read.cold_open(p) as f:
        assert f.read() == data


def test_cold_open_is_binary(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"\xff\xfe\x00")
    with cold_read.cold_open(p) as f:
        chunk = f.read(1)
        assert isinstance(chunk, bytes)


def test_cold_open_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        cold_read.cold_open(tmp_path / "nope.mov")


def test_cold_open_best_effort_survives_fcntl_failure(tmp_path, monkeypatch):
    # A failing fcntl/fadvise must never prevent the read (best-effort bypass).
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    monkeypatch.setattr(cold_read, "_try_fadvise_dontneed",
                        lambda fd: (_ for _ in ()).throw(OSError("boom")))
    with cold_read.cold_open(p) as f:
        assert f.read() == b"hello"


# ── full_fsync write-side seam ───────────────────────────────────────────────

def test_full_fsync_on_real_fd_returns_bool(tmp_path):
    p = tmp_path / "w.bin"
    with open(p, "wb") as f:
        f.write(b"payload")
        f.flush()
        result = cold_read.full_fsync(f.fileno())
    assert isinstance(result, bool)
    # On macOS F_FULLFSYNC should succeed (True); elsewhere a plain fsync (False).
    if sys.platform == "darwin":
        assert result is True


def test_full_fsync_falls_back_to_fsync_on_oserror(tmp_path, monkeypatch):
    p = tmp_path / "w.bin"
    fsync_calls = {"n": 0}
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsync_calls.__setitem__("n", fsync_calls["n"] + 1), real_fsync(fd))[1])
    if sys.platform == "darwin":
        import fcntl
        monkeypatch.setattr(fcntl, "fcntl",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("no fullfsync")))
    with open(p, "wb") as f:
        f.write(b"x")
        f.flush()
        result = cold_read.full_fsync(f.fileno())
    assert result is False
    assert fsync_calls["n"] >= 1


# ── honest labelling ─────────────────────────────────────────────────────────

def test_eviction_unverified_by_default():
    # Must stay False until the real-device divergence experiment passes.
    assert cold_read.EVICTION_VERIFIED is False


def test_coldness_label_is_honest():
    label = cold_read.coldness_label()
    assert isinstance(label, str) and label
    if sys.platform == "darwin" and not cold_read.EVICTION_VERIFIED:
        assert "not confirmed cold" in label or "unverified" in label


def test_no_sha256_or_purge_command():
    import inspect
    src = inspect.getsource(cold_read)
    # The nuclear system-wide `purge` command is explicitly rejected.
    assert "subprocess" not in src
    assert "'purge'" not in src and '"purge"' not in src


# ── fadvise helper ───────────────────────────────────────────────────────────

def test_try_fadvise_returns_false_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "posix_fadvise", raising=False)
    p = tmp_path / "f.bin"
    p.write_bytes(b"z")
    with open(p, "rb") as f:
        assert cold_read._try_fadvise_dontneed(f.fileno()) is False


def test_try_fadvise_issues_when_available(tmp_path, monkeypatch):
    # Simulate posix_fadvise being present even on platforms that lack it (macOS).
    calls = []
    monkeypatch.setattr(os, "posix_fadvise",
                        lambda *a: calls.append(a), raising=False)
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    p = tmp_path / "f.bin"
    p.write_bytes(b"z" * 100)
    with open(p, "rb") as f:
        assert cold_read._try_fadvise_dontneed(f.fileno()) is True
    assert calls and calls[0][3] == 4


def test_try_fadvise_returns_false_on_oserror(tmp_path, monkeypatch):
    def boom(*a):
        raise OSError("fadvise refused")
    monkeypatch.setattr(os, "posix_fadvise", boom, raising=False)
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    p = tmp_path / "f.bin"
    p.write_bytes(b"z")
    with open(p, "rb") as f:
        assert cold_read._try_fadvise_dontneed(f.fileno()) is False


# ── platform-branch simulation (so every branch runs on one OS) ───────────────

def test_full_fsync_non_darwin_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "linux")
    p = tmp_path / "w.bin"
    with open(p, "wb") as f:
        f.write(b"x")
        f.flush()
        assert cold_read.full_fsync(f.fileno()) is False


def test_full_fsync_darwin_success_returns_true(tmp_path, monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "darwin")
    captured = {}
    import fcntl
    monkeypatch.setattr(fcntl, "fcntl",
                        lambda fd, cmd, *a: captured.setdefault("cmd", cmd))
    p = tmp_path / "w.bin"
    with open(p, "wb") as f:
        f.write(b"x")
        f.flush()
        assert cold_read.full_fsync(f.fileno()) is True
    assert captured["cmd"] == cold_read._F_FULLFSYNC


def test_coldness_label_linux(monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "linux")
    assert "POSIX_FADV_DONTNEED" in cold_read.coldness_label()


def test_coldness_label_other_platform(monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "win32")
    assert "not confirmed cold" in cold_read.coldness_label()


def test_coldness_label_darwin_verified(monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "darwin")
    monkeypatch.setattr(cold_read, "EVICTION_VERIFIED", True)
    assert cold_read.coldness_label() == "verified cold on macOS"


def test_cold_open_linux_branch_reads_and_advises(tmp_path, monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "linux")
    advised = []
    monkeypatch.setattr(cold_read, "_try_fadvise_dontneed",
                        lambda fd: advised.append(fd) or True)
    p = tmp_path / "f.bin"
    p.write_bytes(b"linux-bytes")
    with cold_read.cold_open(p) as f:
        assert f.read() == b"linux-bytes"
    assert advised  # fadvise path was taken on the linux branch


def test_cold_open_other_platform_plain_open(tmp_path, monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "win32")
    p = tmp_path / "f.bin"
    p.write_bytes(b"win-bytes")
    with cold_read.cold_open(p) as f:
        assert f.read() == b"win-bytes"


def test_cold_open_darwin_applies_nocache(tmp_path, monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "darwin")
    seen = []
    import fcntl
    monkeypatch.setattr(fcntl, "fcntl",
                        lambda fd, cmd, *a: seen.append(cmd))
    monkeypatch.setattr(cold_read, "_try_fadvise_dontneed", lambda fd: False)
    p = tmp_path / "f.bin"
    p.write_bytes(b"mac-bytes")
    with cold_read.cold_open(p) as f:
        assert f.read() == b"mac-bytes"
    assert cold_read._F_NOCACHE in seen


def test_cold_open_darwin_nocache_oserror_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(cold_read.sys, "platform", "darwin")
    import fcntl
    monkeypatch.setattr(fcntl, "fcntl",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no nocache")))
    monkeypatch.setattr(cold_read, "_try_fadvise_dontneed", lambda fd: False)
    p = tmp_path / "f.bin"
    p.write_bytes(b"still-readable")
    with cold_read.cold_open(p) as f:
        assert f.read() == b"still-readable"
