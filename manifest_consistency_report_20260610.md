# ST SyncTool — Manifest Consistency Report
Date: 2026-06-10
Model: claude-opus-4-8
Phases run: 5 (phases 4 and 5 completed inline after the orchestration subagents hit a session limit)

## Summary

All four tabs now write and read a single consistent schema 1.1 manifest, and every writer records `hash_algorithm` per file entry. The standout finding was a real cross-tab data-loss bug in `core/transfer.py`: it keyed manifest entries by bare filename, so `subdir/FILE_C.txt` collapsed to `FILE_C.txt`, which would make comparison and verify falsely report every subdirectory file as deleted or missing. That is fixed. The full end-to-end pipeline (Transfer to Merge to Verify to Projects) passes, the chain-of-custody log now carries an explicit overall verdict plus per-file PASS/FAIL, and the test suite stands at 128 passed / 0 failed.

## Schema Compatibility Matrix

Schema version 1.1 throughout. `core/manifest.py` is the source of truth; `load_manifest()` runs `_migrate()` to backfill any older manifest on read.

| Reader (consumes) | Writer (produces) | Before this session | After this session |
|---|---|---|---|
| Transfer | manifest.py / transfer.py | Local transfer keyed entries by basename, dropping subdir files; no per-entry `hash_algorithm` | Keyed by relative POSIX path; `hash_algorithm` + `gdrive_url` + `verification_method` per entry; `root` label added |
| Merge | transfer.py / manifest.py | Read cleanly | Read cleanly; post-merge manifest now carries verified post-copy hashes |
| Merge | offload.py | Offload wrote no base manifest | Offload now persists a schema 1.1 ingest manifest at the destination plus archive |
| Verify | manifest.py / transfer.py | Subdir entries mis-keyed would fail verify | Relative-path keying fixed; verify resolves all entries |
| comparison.py | all writers | Presence-based checksum fallback (sha256 then xxhash3_64 then md5); ignores `hash_algorithm` | Unchanged by design; writers are now complete so no reader leniency needed |
| rclone_bridge (lsjson) | remote listing | No `hash_algorithm`; ad-hoc `checksum_context` | `hash_algorithm` per entry; standardised `checksum_context` |

## Cross-Tab Pipeline Test Results

Run against `/tmp/st_pipeline_test/` calling the core modules directly (no GUI). All steps passed.

- STEP 1 Transfer: source copied to server, `st_manifest.json` written, schema 1.1 with `root`, `created_at`, `hash_algorithm=sha256` per entry, all three files keyed by relative path and loaded clean.
- STEP 2 Diff: three-way diff returned FILE_A=LOCAL_CHANGED, FILE_B=UNCHANGED, subdir/FILE_C=UNCHANGED, FILE_D=LOCAL_ONLY.
- STEP 3 Merge: pushed the changed and new files, server content matched, new post-merge manifest carried all four files with correct hashes.
- STEP 4 Verify: clean source verified all OK; flipping one byte in FILE_B produced FILE_B=MISMATCH with the rest still OK.
- STEP 5 Projects: `upsert_project()` wrote `projects.json` with `project_id 5d3abe8b8198`, local and server paths, `created_at`, `display_name`. `list_projects()` and `get_project()` both returned the entry.

## Bugs Patched

All patches are marked `# MANIFEST-FIX` and are in the working tree (uncommitted).

- `core/transfer.py` ~159-201 — key transfer manifest entries by relative POSIX path instead of basename. This was the core cross-tab interoperability bug: it collapsed `subdir/FILE_C.txt` to `FILE_C.txt`, breaking comparison and verify for any nested file.
- `core/transfer.py` ~159-191, ~356-395 — add `hash_algorithm`, `verification_method`, `gdrive_url` per entry; add the `root` display label; standardise `checksum_context` (algorithm, method, gdrive_mode, paranoid_fallback_count) for both local and rclone transfers.
- `core/rclone_bridge.py` ~136-174 — add `hash_algorithm` per entry and standardise `checksum_context` for lsjson-derived manifests.
- `gui/merge_tab.py` ~263-326 — capture verified post-copy hashes from `push_file`/`pull_file` and merge `checksums`, `hash_algorithm`, `verification_method` into the regenerated post-merge manifest.
- `core/offload.py` ~512-608, ~848-860 — new `build_offload_manifest()` and `save_offload_manifest()`; `run_offload` persists a schema 1.1 offload-ingest manifest after each cell commits, non-fatal on failure.

## Chain-of-Custody Log Improvements

`core/offload.py` `write_chain_of_custody_log()` and `prehash_source()`:

- `OVERALL RESULT: COMPLETE` or `OVERALL RESULT: PARTIAL_FAILURE` near the top of every log (PARTIAL_FAILURE if any cell is not DONE).
- Per-file `VERIFY: PASS` / `VERIFY: FAIL` line for every file, derived from `verify_staging()` results captured on the new `CellResult.per_file_verify` and `CellResult.verified` fields.
- Collision-proof filename: `offload_<YYYYmmdd>_<HHMMSS>_<4 hex>.txt`, so two offloads starting in the same second no longer overwrite each other.
- `.DS_Store`, `Thumbs.db` and `desktop.ini` are filtered before pre-hash via the new `SKIP_FILENAMES` frozenset, so OS junk never enters pre-hash, copy, verify, manifest or the log.

New committed coverage in `tests/test_offload.py` (`TestChainOfCustodyLog`, 6 tests): asserts the overall verdict (both COMPLETE and PARTIAL_FAILURE), per-file PASS and FAIL lines, the 4-char hex suffix and that `.DS_Store` never appears. The tests monkeypatch `OFFLOAD_LOGS_DIR` and `save_offload_manifest` so they stay hermetic.

## Final Test Count

128 passed / 0 failed (122 baseline plus 6 new chain-of-custody tests).

## On-Disk State

Under `~/Documents/STSyncTool/`:

- `manifests/`: 53 manifests. Freshest is `A001/st_manifest_A001_offload-ingest_20260610_082500.json`, schema 1.1 with `root`, `created_at` and `hash_algorithm` on every entry. 37 older schema 1.0 manifests remain on disk; all load without error and migrate to 1.1 in memory. None were deleted.
- `projects.json`: one entry, written by the pipeline test, structure valid.
- `offload_logs/`: freshest is `offload_20260610_132500_3e71.txt`, with the hex suffix, the `OVERALL RESULT:` line, per-file `VERIFY:` lines and no `.DS_Store`. The new format is confirmed live.

## Remaining Known Issues

- generated_at vs created_at: the spec calls the timestamp `generated_at` but the codebase uniformly uses `created_at`. Left as `created_at` to avoid breaking `load_manifest` and migration. Recommend aligning the spec to the code or adding `generated_at` as an alias.
- Local-file `gdrive_url` (Phase 1 item 06): not implemented. Populating real Drive URLs for local transfers needs a live rclone lsjson roundtrip against a real remote and has no test harness. Local manifests keep `gdrive_url=""`, which is a complete valid field that `load_manifest` tolerates. rclone-derived manifests still get real URLs.
- comparison.py cross-algorithm fallback: `_cs()` still picks the first present hash (sha256 then xxhash3_64 then md5) and ignores `hash_algorithm`. Left unchanged because every writer is now complete, so a reader-side change was not needed and would risk altering diff behaviour relied on by `test_comparison.py`. Low real-world risk; worth a future hardening pass to compare like-for-like algorithms.
- Test hygiene: the existing `TestRunOffloadStagingInvariant` and `TestEjectSignal` classes call `run_offload` without isolating `OFFLOAD_LOGS_DIR` or `save_offload_manifest`, so they write real logs and manifests into `~/Documents/STSyncTool/` on every run. Recommend they adopt the same monkeypatch the new `TestChainOfCustodyLog` uses.

## Confidence Assessment

High. Every gap traced to specific source lines, writer-side fixes were preferred over reader leniency, the full pipeline passes end to end and the suite is green at 128 / 0. The transfer keying fix is the most important correctness change and should be reviewed first. Top risk to address next: the test-hygiene leak that pollutes the real output directory, since it makes future on-disk audits noisier and could mask a regression.
