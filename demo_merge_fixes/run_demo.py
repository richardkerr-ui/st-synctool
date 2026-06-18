#!/usr/bin/env python3
"""Demo / manual test for the merge-diff fixes in commit 3fb2e28.

Run from the repo root:   .venv/bin/python demo_merge_fixes/run_demo.py

It builds the dummy manifests under demo_merge_fixes/manifests/, runs the REAL
core.comparison.three_way_diff and core.merge_ops.preserve_rename against them,
and prints the resulting state for each scenario so you can eyeball the fixes.

Scenarios:
  1. Cross-algo, base present     -> INDETERMINATE (was false SERVER_CHANGED)
  2. Cross-algo, no base          -> INDETERMINATE (was false BOTH_CHANGED)
  3. Matching SHA-256 + md5 drift -> UNCHANGED (regression guard)
  4. Shared md5 only, real change -> LOCAL_CHANGED (md5 still used when shared)
  5. Duplicate rename target      -> all involved paths flagged BOTH_CHANGED
  6. preserve_rename collision    -> increments _2, _3 instead of overwriting
"""
import json
import sys
from pathlib import Path

# Make the repo importable when run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.comparison import three_way_diff, DiffState          # noqa: E402
from core.merge_ops import preserve_rename                     # noqa: E402

OUT = Path(__file__).resolve().parent / "manifests"
OUT.mkdir(exist_ok=True)


def _sha(cs, size=100):
    return {"type": "file", "size": size, "checksums": {"sha256": cs}}


def _md5(cs, size=100):
    return {"type": "file", "size": size, "checksums": {"md5": cs}}


def _dual(sha, md5, size=100):
    return {"type": "file", "size": size, "checksums": {"sha256": sha, "md5": md5}}


def _manifest(files, renames=None):
    m = {"schema_version": "1.2", "files": files}
    if renames is not None:
        m["renames"] = renames
    return m


def _dump(name, manifest):
    """Write a manifest to disk as a tangible dummy file and return it."""
    (OUT / name).write_text(json.dumps(manifest, indent=2))
    return manifest


def _state_of(base, yours, server, path):
    results = {r.path: r.state for r in three_way_diff(base, yours, server)}
    return results.get(path)


def _check(label, got, expected):
    ok = "PASS" if got == expected else "FAIL"
    arrow = "" if got == expected else f"   (expected {expected.name})"
    print(f"  [{ok}] {label}: {got.name if got else None}{arrow}")
    return got == expected


def main():
    print(f"Writing dummy manifests to: {OUT}\n")
    all_ok = True

    # --- 1. Cross-algo, base present --------------------------------------
    print("Scenario 1 — cross-algo, base present (identical file, server md5-only)")
    base   = _dump("01_base.json",   _manifest({"project.prproj": _sha("abc123")}))
    yours  = _dump("01_yours.json",  _manifest({"project.prproj": _sha("abc123")}))
    server = _dump("01_server.json", _manifest({"project.prproj": _md5("def456")}))
    all_ok &= _check("project.prproj", _state_of(base, yours, server, "project.prproj"),
                     DiffState.INDETERMINATE)

    # --- 2. Cross-algo, no base ------------------------------------------
    print("\nScenario 2 — cross-algo, no base (local sha256 vs Drive md5)")
    base   = _dump("02_base.json",   _manifest({}))
    yours  = _dump("02_yours.json",  _manifest({"project.prproj": _sha("abc123")}))
    server = _dump("02_server.json", _manifest({"project.prproj": _md5("def456")}))
    all_ok &= _check("project.prproj", _state_of(base, yours, server, "project.prproj"),
                     DiffState.INDETERMINATE)

    # --- 3. Matching SHA-256 wins over md5 drift -------------------------
    print("\nScenario 3 — matching SHA-256, differing md5 (md5 is stale metadata)")
    base   = _dump("03_base.json",   _manifest({"f.mov": _dual("A", "X")}))
    yours  = _dump("03_yours.json",  _manifest({"f.mov": _dual("A", "Y")}))
    server = _dump("03_server.json", _manifest({"f.mov": _dual("A", "Z")}))
    all_ok &= _check("f.mov", _state_of(base, yours, server, "f.mov"),
                     DiffState.UNCHANGED)

    # --- 4. Shared md5 only, a real change -------------------------------
    print("\nScenario 4 — md5 the only shared algorithm, a genuine change")
    base   = _dump("04_base.json",   _manifest({"f.mov": _md5("A")}))
    yours  = _dump("04_yours.json",  _manifest({"f.mov": _md5("B")}))
    server = _dump("04_server.json", _manifest({"f.mov": _md5("A")}))
    all_ok &= _check("f.mov", _state_of(base, yours, server, "f.mov"),
                     DiffState.LOCAL_CHANGED)

    # --- 5. Duplicate rename target --------------------------------------
    print("\nScenario 5 — two renames share one target (same-day collision)")
    base = _dump("05_base.json", _manifest(
        {"old_a.mov": _sha("A"), "old_b.mov": _sha("B")},
        renames=[
            {"from": "old_a.mov", "to": "new.mov"},
            {"from": "old_b.mov", "to": "new.mov"},
        ],
    ))
    yours  = _dump("05_yours.json",  _manifest({"new.mov": _sha("C")}))
    server = _dump("05_server.json", _manifest({"old_a.mov": _sha("A"),
                                                "old_b.mov": _sha("B")}))
    for p in ("new.mov", "old_a.mov", "old_b.mov"):
        all_ok &= _check(p, _state_of(base, yours, server, p), DiffState.BOTH_CHANGED)

    # --- 6. preserve_rename collision increment --------------------------
    print("\nScenario 6 — preserve_rename increments on same-day collision")
    import getpass
    from unittest.mock import patch
    with patch("core.merge_ops.getpass.getuser", return_value="richard.kerr"):
        first = preserve_rename("clip.mov")
        taken = {first}
        second = preserve_rename("clip.mov", exists_fn=lambda r: r in taken)
        taken.add(second)
        third = preserve_rename("clip.mov", exists_fn=lambda r: r in taken)
    print(f"  first : {first}")
    print(f"  second: {second}")
    print(f"  third : {third}")
    ok = (first != second != third) and second.endswith("_2.mov") and third.endswith("_3.mov")
    print(f"  [{'PASS' if ok else 'FAIL'}] three distinct names, _2 then _3")
    all_ok &= ok

    print("\n" + ("ALL SCENARIOS PASS" if all_ok else "SOME SCENARIOS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
