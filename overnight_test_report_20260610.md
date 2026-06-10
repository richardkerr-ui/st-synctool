# ST SyncTool — Overnight Test Report
Date: 2026-06-10
Model: claude-opus-4-8
Phases run: 6 (sequential)

## Summary

ST SyncTool is in good shape. The 122-test baseline was green from the start and stayed green after every patch. Six phases ran sequentially; four real source patches were applied (one cosmetic, three genuine bugs), each followed by a full-suite re-run confirming no regressions. The most consequential find was a crash in the chain-of-custody log writer that made any offload-with-normalisation report failure *after* the footage had already committed safely. All four phases that touched code finished at High confidence. The Phase 6 audit surfaced real but lower-severity gaps in log/manifest content — none are regressions, and the two scariest-looking "Critical" flags turned out to be stale historical files, not live defects.

## Test Suite Results

- Baseline (Phase 1): **122 passed / 0 failed**, clean, no skips or warnings (Python 3.11.15, pytest 9.0.3).
- After all four patches (final integrated run): **122 passed / 0 failed** in 0.12s.
- Regression status: every patching phase re-ran the full suite and stayed at 122/122. The last patch (Phase 5, `core/manifest.py`) was followed by a green suite, and a final orchestrator-level integrated run confirmed all four patches coexist cleanly.
- Cross-check satisfied: Phase 1 required no patch, so there was no Phase-1 fix for later phases to re-verify. Phases 2–5 each re-ran the suite post-patch; Phase 6 was read-only.

## Integration Test Results

**Offload pipeline (Phase 4)** — every stage exercised individually and end-to-end against a realistic mixed fixture (DCIM video + SRT sidecars, AUDIO WAV, R3D + RMD, .RDC folder):
- preflight read-only ✓ (source byte-identical before/after, write attempt raises PermissionError)
- prehash ✓ (all 10 files hashed, none skipped)
- copy-to-staging ✓ (`.st_staging_<ts>` created, correct relative paths)
- verify-staging ✓ (passes clean, catches a deliberately corrupted file)
- commit-staging ✓ (moved to `<dest>/<label>/`, staging removed)
- write_failure_report ✓ (structured report on synthetic failure)
- run_offload end-to-end ✓ after the COC-log fix below.

**R3D + normalisation (Phases 3 & 4)** — confirmed per spec:
- Valid R3D stems (A001_C001 / A001_C002) and RMD/WAV kept original names.
- IMG_XXXX sequential clips renamed with the sha8 suffix; `.SRT` sidecars co-renamed with the *same* suffix as their parent.
- Generic non-sequential names (PROJECT_A.MOV, INTERVIEW_WIDE.MOV) not renamed.
- `find_rdc_clips` treats `A001.RDC/` as one clip unit, not per-segment.

**Volume detection (Phase 2)** — `scan_existing`, `_looks_like_media_card`, `_sanitise_label`, and the `volume_mounted` signal all behave per spec, including DCIM-at-depth-1 (Canon), RED `.RDM` layouts, and the removable+ejectable gate that excludes plain drives / Time Machine / network mounts. PyQt6 was available so the live-signal test ran rather than being skipped.

**Comparison / merge (Phase 5)** — existing tests/test_comparison.py (30) and tests/test_merge_ops.py (27) pass in isolation. Added edge-case coverage for DELETED_BOTH, BOTH_CHANGED, the preserve-rename round-trip (today's rename-collapse fix holds — the renamed path does not re-flag as LOCAL_ONLY/SERVER_ONLY on the next scan), schema-version presence, and hash_algorithm recording.

## Log & Manifest Audit (Phase 6, read-only)

What is correct on disk:
- All 18 offload logs parse and contain source label, source path, file count, total bytes, per-file pre-hash records, per-destination result blocks, and a run timestamp.
- All destination paths referenced in logs exist and hold the expected files; no stray `.st_staging_*` / `.st_offload_*` dirs left behind.
- AUDIT_NORM run shows normalisation applied at destination on disk (e.g. `IMG_0001_11cca198.MOV` with co-renamed `.SRT`).
- All 37 archived manifests are valid JSON with full 64-char sha256 values; no null/empty hashes.

Findings reconciled against live code (important — the audit inspected files already on disk):
- **Manifests at schema 1.0 / no `hash_algorithm` (audit "Critical") — STALE, not a defect.** Every archived manifest is dated Jun 8–9, written by the pre-patch app. Live code is now `SCHEMA_VERSION = "1.1"` and writes `hash_algorithm`; `load_manifest` backfills both for older files in-memory on read. Tonight's run exercised offload + unit tests only, so no fresh merge/transfer manifest was generated to land a 1.1 file on disk.
- **No `projects.json`, no `contact_sheets/` — flows not invoked, not failures.** The projects registry is written on a successful merge scan (none ran tonight); contact sheets need ffmpeg/REDline + real video and were not run end-to-end.

Genuine live findings (carried to Known Issues below):
- COC log lacks an explicit overall **COMPLETE / PARTIAL_FAILURE** verdict line and explicit **per-file post-copy verification PASS/FAIL** records — result is only implied by per-cell state. (Phase 4 read cell-state as sufficient; the stricter audit checklist flags the missing explicit fields. Real gap, design-level.)
- Offload pre-hash manifests use xxhash3_64 (16-char), so the chain-of-custody log carries a non-cryptographic hash, weaker as custody evidence than the sha256 in the merge manifests.
- `.DS_Store` is treated as offload payload (pre-hashed, copied, verified) — macOS metadata captured as footage.
- Same-named subfolder override defeats collision protection (Phase 5 #24): pointing two sources at the same custom subfolder merges both cards into one dir with no warning.
- 10 of 18 offload logs are test-fixture/pytest leftovers written into the production `offload_logs/` dir (pytest tmp paths + `/tmp/test_card_A001`). No same-second filename collision actually occurred this run, but the writer derives its filename from the current second and *would* clobber on two runs in one second.

## Bugs Patched

All patches are in source, each tagged `# OVERNIGHT-FIX:`, each followed by a green 122-test suite. Verified present in the tree at report time.

1. **`core/offload.py` ~L221, `build_normalization_plan`** (real bug, Phase 3) — sidecar→hash-suffix binding was non-deterministic when two clips share a stem (e.g. `IMG_0001.MOV` + `IMG_0001.R3D`): a co-named `IMG_0001.SRT` inherited whichever clip's hash came last in dict/filesystem order, risking sidecar orphaning across runs/platforms. Fix: iterate `sorted(...)` with `setdefault` so the lexicographically-first clip wins deterministically.

2. **`core/offload.py` ~L537, `write_chain_of_custody_log`** (serious bug, Phase 4) — when normalisation or thumbnails ran, the manifest carries non-file meta keys (`filename_normalization`, `generated_artifacts`) whose values have no `size`/`checksum`. The log writer did `v['size']` over every value and raised `KeyError`, aborting `run_offload` *at the very end* — footage was already safely committed on disk, but the run reported failure and no custody log was written. Fix: filter to real file entries (`isinstance(info, dict) and "size" in info`) and use `info.get('checksum','')`.

3. **`core/manifest.py` ~L25/L70/L114/L190** (real schema gap, Phase 5) — `hash_algorithm` per-entry field, required by spec, did not exist anywhere. Added a `_primary_algorithm(gdrive)` helper; emit `hash_algorithm` in both `generate_manifest` and `generate_manifest_fast`; backfill it in `_migrate` for pre-1.1 files (preferring `checksum_context.algorithm`, else inferring from present checksum keys sha256 > md5 > xxhash3_64).

4. **`utils/volume_watcher.py` ~L36** (cosmetic, Phase 2) — module docstring still claimed pre-mounted cards are not auto-detected, contradicting `scan_existing()` (added in commit fbd3d58) and the README. Docstring corrected, no logic change.

## Known Issues Not Fixed

Left for a human decision — out of scope for an autonomous overnight pass, or design-level:

- **COC log completeness.** Add an explicit overall result verdict (COMPLETE / PARTIAL_FAILURE) and explicit per-file post-copy verification PASS/FAIL to `write_chain_of_custody_log`. Currently the verification outcome is only inferable from per-cell state. This is the most worthwhile follow-up — it is the chain-of-custody record's core job.
- **COC hash strength.** Consider recording sha256 (not just xxhash3_64) in the custody log for evidentiary strength, or document that xxhash3_64 is intentional.
- **`.DS_Store` (and similar OS cruft) ingested as offload payload.** Merge filters these; offload does not. Decide whether offload should apply the same ignore list.
- **Subfolder collision warning.** When a user overrides two sources to the same custom subfolder name, files merge silently. A warning (or per-source guard) would preserve the Phase 5 #24 intent.
- **Same-second COC log filename collision.** `offload_<YYYYmmdd_HHMMSS>.txt` can clobber on two runs in one second. Add a uniquifier (counter or ms). Low real-world likelihood.
- **Test-fixture logs in production dir.** 10 leftover logs sit in `~/Documents/STSyncTool/offload_logs/`. Not deleted (audit was read-only; nothing in that dir is mine to remove without confirmation). Safe to clear manually.
- **Historical manifests not migrated on disk.** Old 1.0 manifests are handled correctly by `load_manifest`'s in-memory backfill, but are never rewritten. Optional one-time re-save sweep if you want on-disk consistency.
- **GoPro pattern vs fixture naming (note, not a bug).** `GH0_00001.MP4` (the literal name in the test brief) matches no pattern because real GoPro names are `GH010001.MP4` (no underscore) and the source regex correctly targets the real form. Fixture-vs-real mismatch only.

## Confidence Assessment

**Overall: ready, with one clear follow-up.** Core data-integrity paths — read-only source enforcement, pre-hash → stage → verify → commit, deterministic normalisation, three-way diff including the rename-collapse fix — are exercised and correct, and the full suite is green with all patches in place. Every phase reported High confidence.

Risks to flag for the next session:
1. The chain-of-custody log is functionally thin — it now writes (the KeyError is fixed) but omits an explicit overall verdict and per-file verification results. For a tool whose selling point is verified custody, close that gap first.
2. There is no committed test for `core/offload.py`'s `write_chain_of_custody_log` despite it having just hosted a crash-on-the-happy-path bug; add one to `tests/`.
3. No project venv exists — tests only run under `/opt/homebrew/bin/python3.11` (the Xcode `python3` lacks pytest/PyQt6). Pin an interpreter before any CI.
4. The Phase 1/2 schema and registry features could not be confirmed *on disk* tonight because no merge/transfer/contact-sheet flow was invoked; the code is correct in unit tests, but an end-to-end merge run would be worth doing to land a fresh 1.1 manifest and a `projects.json` for direct inspection.
