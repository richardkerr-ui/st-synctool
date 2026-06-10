"""
Manifest generation must never record ignored paths (OS junk, our own
st_manifest.json, staging/failure/thumbnail artifacts).

Regression test for the .DS_Store ingest asymmetry: the merge diff ignored
these files but `generate_manifest`/`generate_manifest_fast` did not, so a
phantom .DS_Store could land in a post-merge manifest and then show up as a
spurious MISSING on Verify. Generation now reuses comparison.is_ignored_path.
"""

from pathlib import Path

import pytest

from core.manifest import (
    generate_manifest,
    generate_manifest_fast,
    MANIFEST_FILENAME,
)


JUNK = [".DS_Store", "Thumbs.db", "desktop.ini"]


def _populate(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "clipA.mov").write_bytes(b"real footage A")
    (folder / "sub").mkdir()
    (folder / "sub" / "clipB.mov").write_bytes(b"real footage B")
    # junk that must be excluded
    for name in JUNK:
        (folder / name).write_bytes(b"junk")
    (folder / "sub" / ".DS_Store").write_bytes(b"nested junk")
    (folder / MANIFEST_FILENAME).write_text("{}")            # our own manifest
    (folder / "_thumbnails").mkdir()
    (folder / "_thumbnails" / "frame.jpg").write_bytes(b"thumb")
    (folder / ".st_staging_20260610").mkdir()
    (folder / ".st_staging_20260610" / "x.mov").write_bytes(b"in-flight")


def _assert_clean(manifest):
    keys = list(manifest["files"])
    assert set(keys) == {"clipA.mov", "sub/clipB.mov"}, keys
    assert manifest["file_count"] == 2
    # nothing junk-y leaked in, at any depth
    for k in keys:
        name = Path(k).name
        assert name not in JUNK
        assert name != MANIFEST_FILENAME
        assert "_thumbnails" not in k
        assert ".st_staging_" not in k


def test_generate_manifest_excludes_ignored(tmp_path):
    folder = tmp_path / "card"
    _populate(folder)
    _assert_clean(generate_manifest(folder))


def test_generate_manifest_fast_excludes_ignored(tmp_path):
    folder = tmp_path / "card"
    _populate(folder)
    _assert_clean(generate_manifest_fast(folder))


def test_generated_manifest_verifies_clean_against_junkfree_copy(tmp_path):
    """The exact failure mode: a manifest built from a junk-containing folder,
    used to verify a copy where the junk was (correctly) not carried, must not
    flag the junk as MISSING."""
    from PyQt6.QtWidgets import QApplication
    from gui.verify_tab import VerifyWorker

    src = tmp_path / "with_junk"
    _populate(src)
    manifest = generate_manifest(src)

    # Simulate what offload/merge-apply do: copy only the real files, skip junk.
    copy = tmp_path / "junk_free_copy"
    (copy / "sub").mkdir(parents=True)
    (copy / "clipA.mov").write_bytes((src / "clipA.mov").read_bytes())
    (copy / "sub" / "clipB.mov").write_bytes((src / "sub" / "clipB.mov").read_bytes())

    QApplication.instance() or QApplication([])
    worker = VerifyWorker(str(copy), manifest)
    cap = {}
    worker.finished.connect(lambda r: cap.setdefault("results", r))
    worker.error.connect(lambda e: cap.setdefault("error", e))
    worker.run()
    if "error" in cap:
        pytest.fail(cap["error"])
    results = cap.get("results", [])

    statuses = [r["status"] for r in results]
    assert statuses, "verify produced no results"
    assert "MISSING" not in statuses, results   # no phantom junk MISSING
    assert all(s == "OK" for s in statuses), results
