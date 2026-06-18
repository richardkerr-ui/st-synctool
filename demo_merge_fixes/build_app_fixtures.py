#!/usr/bin/env python3
"""Generate real on-disk folders + base manifests to exercise the merge fixes
inside the actual app (Merge tab).

Run from the repo root:
    .venv/bin/python demo_merge_fixes/build_app_fixtures.py

It creates demo_merge_fixes/app_fixtures/ with two scenarios, each providing the
three things the Merge tab asks for: a Base Manifest (.json), a Local Folder
(Yours) and a Server (Theirs). Checksums in the base manifests are computed from
the real files so the diff is genuine, not faked.
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.checksum import compute_all  # noqa: E402

BASE = Path(__file__).resolve().parent / "app_fixtures"


def _reset():
    if BASE.exists():
        shutil.rmtree(BASE)
    BASE.mkdir(parents=True)


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _entry_from_file(path: Path, *, keep="sha256"):
    """Build a manifest file-entry from a real file, keeping only one algorithm
    so we can stage the cross-algorithm case honestly."""
    full = compute_all(path, include_xxhash=False, include_md5=True)
    checksums = {keep: full[keep]}
    stat = path.stat()
    return {
        "type": "file",
        "size": stat.st_size,
        "modtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "checksums": checksums,
        "hash_algorithm": keep,
        "gdrive_url": "",
    }


def _manifest(files: dict, renames=None) -> dict:
    m = {"schema_version": "1.2", "files": files, "renames": renames or []}
    return m


# ---------------------------------------------------------------------------
# Scenario A — INDETERMINATE (cross-algorithm)
# Base manifest carries md5-only (a realistic prior Drive-based state). The app
# scans Local and Server to SHA-256. No shared algorithm with the base -> the
# rows show "Unknown" instead of a false "Server Changed".
# ---------------------------------------------------------------------------
def build_indeterminate():
    root = BASE / "A_indeterminate"
    local = root / "local"
    server = root / "server"
    for d in (local, server):
        d.mkdir(parents=True)

    # Identical content on both sides (so the ONLY reason a row is not Unchanged
    # is the un-comparable base manifest).
    content = b"SHOOT_DAY_01 master clip bytes, identical on both sides\n"
    for d in (local, server):
        (d / "scene_01.mov").write_bytes(content)
        (d / "scene_02.mov").write_bytes(b"second clip, also identical\n")

    # Base manifest: md5-only entries for the same files.
    base_files = {
        "scene_01.mov": _entry_from_file(local / "scene_01.mov", keep="md5"),
        "scene_02.mov": _entry_from_file(local / "scene_02.mov", keep="md5"),
    }
    _write(root / "base_manifest.json", json.dumps(_manifest(base_files), indent=2))
    return root, local, server


# ---------------------------------------------------------------------------
# Scenario B — duplicate rename target (phantom-deletion fix)
# Base manifest records two files (old_a, old_b) that were both preserve-renamed
# to the SAME target "new.mov". Local now has only new.mov; server still has the
# two originals. Every involved path is flagged "Conflict" for review instead of
# one silently showing as "Deleted Locally".
# ---------------------------------------------------------------------------
def build_rename_collision():
    root = BASE / "B_rename_collision"
    local = root / "local"
    server = root / "server"
    for d in (local, server):
        d.mkdir(parents=True)

    (server / "old_a.mov").write_bytes(b"original A footage\n")
    (server / "old_b.mov").write_bytes(b"original B footage\n")
    (local / "new.mov").write_bytes(b"the merged-into name\n")

    # Base: the two originals (sha256 matching the server copies) + a renames[]
    # map where both collapse to the same target.
    base_files = {
        "old_a.mov": _entry_from_file(server / "old_a.mov", keep="sha256"),
        "old_b.mov": _entry_from_file(server / "old_b.mov", keep="sha256"),
    }
    renames = [
        {"from": "old_a.mov", "to": "new.mov"},
        {"from": "old_b.mov", "to": "new.mov"},
    ]
    _write(root / "base_manifest.json",
           json.dumps(_manifest(base_files, renames), indent=2))
    return root, local, server


# ---------------------------------------------------------------------------
# Scenario C — genuine BOTH_CHANGED (both sides present and modified)
# A control case: the file exists on local AND server, both diverged from the
# base, both carry SHA-256 (so it is a real conflict, not Unknown). The conflict
# panel should populate BOTH the LOCAL and SERVER columns, including modtimes.
# ---------------------------------------------------------------------------
def build_true_conflict():
    root = BASE / "C_true_conflict"
    local = root / "local"
    server = root / "server"
    for d in (local, server):
        d.mkdir(parents=True)

    # Same filename, different content on each side, both differ from the base.
    (local / "edit.prproj").write_bytes(b"local edit of the timeline\n")
    (server / "edit.prproj").write_bytes(b"server edit of the timeline, different\n")

    # Distinct, obvious modtimes so the panel shows two different dates and the
    # mtime-based suggestion ("local newer" -> Push) is visible. local is newer.
    os.utime(server / "edit.prproj", (1_717_200_000, 1_717_200_000))  # 2024-06-01
    os.utime(local / "edit.prproj",  (1_749_600_000, 1_749_600_000))  # 2025-06-11

    # Base records the original (a third content), so both sides read as changed.
    base_file = root / ".base_src"
    base_file.write_bytes(b"the original agreed-on timeline\n")
    base_files = {"edit.prproj": _entry_from_file(base_file, keep="sha256")}
    base_file.unlink()
    _write(root / "base_manifest.json", json.dumps(_manifest(base_files), indent=2))
    return root, local, server


def main():
    _reset()
    a_root, a_local, a_server = build_indeterminate()
    b_root, b_local, b_server = build_rename_collision()
    c_root, c_local, c_server = build_true_conflict()

    readme = f"""HOW TO CHECK THE MERGE FIXES IN THE APP
=======================================

Open the app, go to the Merge tab, and for each scenario paste these three paths
into the three inputs, then click "Scan & Compare".

--------------------------------------------------------------------
SCENARIO A — "Unknown" state (cross-algorithm fix)
--------------------------------------------------------------------
Base Manifest (.json):  {a_root / 'base_manifest.json'}
Local Folder (Yours):   {a_local}
Server (Theirs):        {a_server}

EXPECT: scene_01.mov and scene_02.mov show the state "Unknown" (glyph warn),
not "Server Changed". The files are byte-identical on both sides; the rows are
flagged only because the base manifest shares no checksum algorithm with the
freshly scanned SHA-256, so equality is unprovable. Before the fix these showed
a false "Server Changed".

--------------------------------------------------------------------
SCENARIO B — duplicate rename target (phantom-deletion fix)
--------------------------------------------------------------------
Base Manifest (.json):  {b_root / 'base_manifest.json'}
Local Folder (Yours):   {b_local}
Server (Theirs):        {b_server}

EXPECT: new.mov, old_a.mov and old_b.mov all show "Conflict" (flagged for
review). Before the fix, one of old_a/old_b would silently show "Deleted
Locally" (a phantom deletion) while the other collapsed into a rename.

--------------------------------------------------------------------
SCENARIO C — genuine conflict (both modtimes populated)
--------------------------------------------------------------------
Base Manifest (.json):  {c_root / 'base_manifest.json'}
Local Folder (Yours):   {c_local}
Server (Theirs):        {c_server}

EXPECT: edit.prproj shows "Both Changed". Click the row: the conflict panel
fills BOTH the LOCAL and SERVER columns, including two different modtimes (local
2025-06-11 newer, server 2024-06-01), and the suggested action is Push (local is
newer). This is the control case proving the server column populates when the
file genuinely exists on both sides. In Scenario B the flagged files exist on
only one side, which is why one column was blank there.

--------------------------------------------------------------------
NOTE
--------------------------------------------------------------------
The cross-algorithm case cannot be produced with two plain local folders,
because the app always hashes both sides with SHA-256. The md5-only base
manifest stands in for a prior Drive-based state, which is where this actually
happens in production.
"""
    _write(BASE / "README.txt", readme)
    print(readme)
    print(f"Fixtures written under: {BASE}")


if __name__ == "__main__":
    main()
