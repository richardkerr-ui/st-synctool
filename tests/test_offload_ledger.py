"""Tests for M12.2 duplicate-card / already-offloaded guard (core/offload_ledger.py)."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.offload_ledger import (
    SourceFingerprint,
    fingerprint_from_manifest,
    fingerprint_source,
    match_prior_offloads,
    warning_text,
)


@dataclass
class FakeSource:
    label: str
    path: Path


def _card(tmp_path, name, files):
    root = tmp_path / name
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return FakeSource(label=name, path=root)


# ── fingerprint_source ───────────────────────────────────────────────────────

def test_fingerprint_counts_and_sizes(tmp_path):
    src = _card(tmp_path, "A001", {
        "DCIM/clip1.mov": b"aaaa", "DCIM/clip2.mov": b"bbbbbb", "AUDIO/s.wav": b"cc",
    })
    fp = fingerprint_source(src)
    assert fp.file_count == 3
    assert fp.total_bytes == 4 + 6 + 2
    assert fp.top_names == ("AUDIO", "DCIM")
    assert fp.label == "A001"


def test_fingerprint_skips_os_junk(tmp_path):
    src = _card(tmp_path, "A001", {"clip.mov": b"data", ".DS_Store": b"junk"})
    fp = fingerprint_source(src, skip_filenames={".DS_Store"})
    assert fp.file_count == 1
    assert fp.total_bytes == 4


def test_volume_label_from_volumes_path():
    # A card mounts at /Volumes/<NAME>; the fingerprint captures that label.
    src = FakeSource("CARD", Path("/Volumes/RED_A001/DCIM"))
    out = fingerprint_from_manifest(src, {"DCIM/a.mov": {"size": 1}})
    assert out.volume_label == "RED_A001"


# ── fingerprint_from_manifest ────────────────────────────────────────────────

def test_fingerprint_from_manifest_matches_walk(tmp_path):
    src = _card(tmp_path, "A001", {"DCIM/c1.mov": b"aaaa", "DCIM/c2.mov": b"bb"})
    manifest = {
        "DCIM/c1.mov": {"size": 4, "checksum": "x", "algorithm": "sha256"},
        "DCIM/c2.mov": {"size": 2, "checksum": "y", "algorithm": "sha256"},
        "generated_artifacts": {"contact_sheet": {}},   # non-file entry ignored
    }
    fp = fingerprint_from_manifest(src, manifest)
    assert fp.file_count == 2
    assert fp.total_bytes == 6
    assert fp.top_names == ("DCIM",)


# ── match_prior_offloads ─────────────────────────────────────────────────────

def _rec(file_count, total_bytes, top_names, dests=("NAS",)):
    return {
        "file_count": file_count, "total_bytes": total_bytes,
        "top_names": list(top_names), "dests": list(dests),
        "offloaded_at": "2026-06-14T10:00:00+00:00",
    }


def test_exact_match():
    fp = SourceFingerprint("A001", "RED", 3, 100, ("DCIM",))
    hits = match_prior_offloads(fp, [_rec(3, 100, ["DCIM"])])
    assert len(hits) == 1 and hits[0]["kind"] == "exact"


def test_partial_match_top_folder_renamed():
    fp = SourceFingerprint("A001", "RED", 3, 100, ("RENAMED",))
    hits = match_prior_offloads(fp, [_rec(3, 100, ["DCIM"])])
    assert len(hits) == 1 and hits[0]["kind"] == "partial"


def test_no_match_when_content_differs():
    fp = SourceFingerprint("A001", "RED", 3, 100, ("DCIM",))
    assert match_prior_offloads(fp, [_rec(4, 100, ["DCIM"])]) == []
    assert match_prior_offloads(fp, [_rec(3, 999, ["DCIM"])]) == []


def test_reused_label_different_content_does_not_match():
    # Same physical card reused for a new shoot: label may repeat but the
    # content fingerprint differs, so it must NOT warn.
    fp = SourceFingerprint("A001", "RED", 50, 5_000, ("DCIM",))
    history = [_rec(3, 100, ["DCIM"])]   # the card's previous, different shoot
    assert match_prior_offloads(fp, history) == []


def test_dest_filter_only_matches_that_destination():
    fp = SourceFingerprint("A001", "RED", 3, 100, ("DCIM",))
    history = [
        _rec(3, 100, ["DCIM"], dests=["NAS"]),
        _rec(3, 100, ["DCIM"], dests=["LTO"]),
    ]
    assert len(match_prior_offloads(fp, history, dest_label="NAS")) == 1
    assert match_prior_offloads(fp, history, dest_label="OTHER") == []


# ── warning_text ─────────────────────────────────────────────────────────────

def test_warning_text_mentions_label_and_prior_dest():
    fp = SourceFingerprint("A001", "RED_A001", 3, 100, ("DCIM",))
    hits = match_prior_offloads(fp, [_rec(3, 100, ["DCIM"], dests=["NAS"])])
    text = warning_text(fp, hits)
    assert "A001" in text and "NAS" in text and "again" in text.lower()


# ── round-trip through the registry ──────────────────────────────────────────

def test_record_and_list_round_trip(tmp_path, monkeypatch):
    from core import projects
    monkeypatch.setattr(projects, "PROJECTS_REGISTRY", tmp_path / "projects.json")
    fp = SourceFingerprint("A001", "RED", 3, 100, ("DCIM",))
    assert projects.list_offload_fingerprints() == []
    projects.record_offload_fingerprint(fp.to_record(["NAS"], "2026-06-14T10:00:00+00:00"))
    history = projects.list_offload_fingerprints()
    assert len(history) == 1
    # A later identical card now matches the recorded one for that destination.
    assert match_prior_offloads(fp, history, dest_label="NAS")[0]["kind"] == "exact"


def test_ledger_trims_to_cap(tmp_path, monkeypatch):
    from core import projects
    monkeypatch.setattr(projects, "PROJECTS_REGISTRY", tmp_path / "projects.json")
    monkeypatch.setattr(projects, "_LEDGER_MAX", 3)
    for i in range(5):
        projects.record_offload_fingerprint(
            SourceFingerprint(f"S{i}", "v", i, i, ()).to_record(["NAS"], "t"))
    history = projects.list_offload_fingerprints()
    assert len(history) == 3
    assert [r["label"] for r in history] == ["S2", "S3", "S4"]   # oldest dropped
