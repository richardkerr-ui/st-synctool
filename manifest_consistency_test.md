# ST SyncTool — Manifest Consistency & Cross-Tab Integration Test
# Run with: claude --model claude-opus-4-8
# From: /Users/richard.kerr/Claude/Projects/ST SyncTool

---

## Context

An overnight smoke test (2026-06-10) left the 122-test suite green with four patches
applied. Three known issues were flagged for a human decision. This session targets the
next critical gap: **manifest schema consistency across all four tabs**, plus the
highest-priority known issues from the overnight report.

The core question: when Offload writes a manifest, can Transfer read it cleanly? When
Transfer writes one, can Merge use it as the base? When Merge regenerates one, can
Verify load and validate against it? Every tab must speak the same schema or data is
silently lost at handoff points.

---

## Your role

You are the orchestrator. Use the Task tool to spawn subagents for each phase below.
Pass each subagent a complete, self-contained brief. Do not read source files yourself —
delegate that to subagents. Your context is reserved for coordination and final synthesis.

Work autonomously. Do not stop to ask questions. Make the conservative choice and note it.

All patches must include: # MANIFEST-FIX: <short description>
Fix bugs in source, not tests (unless a test assertion is provably wrong per spec).
After any patch, re-run `pytest -v` from repo root and confirm 122 passed / 0 failed.

---

## Repo & environment facts (share with every subagent)

- Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
- Spec: SYNCTOOL_CONTEXT.md (authoritative — read it before doing anything)
- Python: /opt/homebrew/bin/python3.11
- Test runner: pytest -v (from repo root using /opt/homebrew/bin/python3.11 -m pytest -v)
- Log/manifest output: ~/Documents/STSyncTool/
  - manifests/       archived manifests (schema 1.0 files exist; new ones should be 1.1)
  - offload_logs/    chain-of-custody logs (10 test fixtures in here — ignore those)
- Current schema version: 1.1 (SCHEMA_VERSION constant in core/manifest.py)
- Known patches already applied (do not re-apply or undo):
  - core/offload.py — sidecar hash binding determinism (# OVERNIGHT-FIX)
  - core/offload.py — COC log KeyError on meta keys (# OVERNIGHT-FIX)
  - core/manifest.py — hash_algorithm field + _migrate backfill (# OVERNIGHT-FIX)
  - utils/volume_watcher.py — docstring correction (# OVERNIGHT-FIX)

---

## Subagent phases

Each subagent returns a structured summary:
  PASSED: (list)
  FAILED: (list)
  PATCHED: (file, approx line, description)
  ANOMALIES: (anything unexpected)
  CONFIDENCE: High / Medium / Low + one-sentence rationale

---

### Phase 1 — Schema map: what each module actually writes and reads

Spawn a subagent with this brief:

> You are mapping the manifest schema as actually written and read by each module in
> ST SyncTool. Do not assume the spec — read the code.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Read first: SYNCTOOL_CONTEXT.md (full file)
>
> Tasks:
> 1. Read these files in full: core/manifest.py, core/transfer.py, core/merge_ops.py,
>    core/comparison.py, core/offload.py, core/checksum.py
> 2. For each module, document exactly:
>    a. What manifest fields it WRITES (key name, type, always/optional)
>    b. What manifest fields it READS or expects to exist (key name, how it fails if missing)
>    c. What schema_version it writes, and whether it validates on read
>    d. What hash algorithm it uses and whether it records hash_algorithm per entry
> 3. Build a cross-tab compatibility matrix: for every reader→writer pair
>    (Offload→Transfer, Offload→Merge, Transfer→Merge, Merge→Verify, Transfer→Verify),
>    list every field the reader expects that the writer does or does not provide.
> 4. Identify every field mismatch, missing field, or silent default that could cause
>    data loss, incorrect diff results, or a crash at a handoff point.
> 5. Do NOT patch anything in this phase. Document only.
> 6. Return the full compatibility matrix plus a prioritised list of gaps, from most
>    to least likely to cause a real failure in production use.
>    Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 2 — Fix schema inconsistencies

Spawn a subagent with this brief:

> You are fixing manifest schema inconsistencies in ST SyncTool identified in a prior
> analysis phase. Read that analysis first (it will be provided as context).
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Read first: SYNCTOOL_CONTEXT.md, then core/manifest.py, core/transfer.py,
>             core/merge_ops.py, core/comparison.py, core/offload.py
>
> [ORCHESTRATOR: paste the Phase 1 gap list here before spawning this subagent]
>
> Tasks — work through every gap identified, highest priority first:
> 1. For each field mismatch: patch the writer to emit the field, or patch the reader
>    to tolerate its absence gracefully (with a sensible default), or both.
>    Prefer patching the writer to be complete over patching readers to be lenient.
> 2. Ensure every manifest written anywhere in the app includes:
>    - schema_version: "1.1"
>    - root: the absolute local path the manifest describes
>    - generated_at: ISO timestamp
>    - entries: list of file records, each with:
>        path (relative), size, mtime, hash, hash_algorithm, file_type (if classifiable)
>    - checksum_context: { algorithm, gdrive_mode }
> 3. Ensure load_manifest() can read any manifest written by any tab without
>    KeyError, AttributeError, or silent data loss. Use _migrate() for schema backfill.
> 4. After each patch, run /opt/homebrew/bin/python3.11 -m pytest -v from repo root.
>    Must stay at 122 passed / 0 failed.
> 5. Write a short integration test (temp file, not committed) that:
>    a. Calls generate_manifest_fast() on a temp directory
>    b. Saves it via save_manifest()
>    c. Loads it via load_manifest()
>    d. Asserts all required fields are present in every entry
>    e. Asserts schema_version == "1.1"
>    Run this test and confirm it passes.
> 6. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 3 — End-to-end pipeline: Offload → Transfer → Merge → Verify

Spawn a subagent with this brief:

> You are running a full end-to-end cross-tab pipeline test for ST SyncTool to confirm
> manifests written by one tab can be consumed cleanly by the next.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Read first: SYNCTOOL_CONTEXT.md
> Python: /opt/homebrew/bin/python3.11
>
> Do not run the GUI. Test all logic by calling core modules directly.
>
> Setup: create this directory structure under /tmp/st_pipeline_test/:
>   source/         — the "camera card" / working folder
>     FILE_A.txt    (content: "hello world A", ~13 bytes)
>     FILE_B.txt    (content: "hello world B", ~13 bytes)
>     subdir/
>       FILE_C.txt  (content: "hello world C")
>   server/         — simulates the Google Drive / NAS (local path for this test)
>   archive/        — where manifests land (~/Documents/STSyncTool/manifests/ equivalent)
>
> Run this sequence, asserting correct behaviour at each step:
>
> STEP 1 — Transfer (source → server):
>   Call core/transfer.py logic to copy source/ to server/.
>   Assert: server/ contains FILE_A.txt, FILE_B.txt, subdir/FILE_C.txt.
>   Assert: a manifest is written to server/ as st_manifest.json.
>   Assert: manifest has schema_version 1.1, root, generated_at, hash_algorithm per entry.
>   Load the manifest with load_manifest() — assert no errors, all 3 files present.
>
> STEP 2 — Modify locally, scan for merge:
>   Modify source/FILE_A.txt (append a byte).
>   Add source/FILE_D.txt (new file, content "new file D").
>   Call comparison.py three-way diff against: base=server manifest, local=source/, server=server/.
>   Assert diff states:
>     FILE_A.txt → LOCAL_CHANGED
>     FILE_B.txt → UNCHANGED
>     FILE_C.txt (via subdir/) → UNCHANGED
>     FILE_D.txt → LOCAL_ONLY
>
> STEP 3 — Apply merge (push local changes):
>   Call merge_ops.py to push LOCAL_CHANGED and LOCAL_ONLY files to server.
>   Assert: server/FILE_A.txt content matches modified source version.
>   Assert: server/FILE_D.txt exists.
>   Assert: a new st_manifest.json is written to server/ after merge.
>   Load the new manifest — assert schema_version 1.1, all 4 files present, correct hashes.
>
> STEP 4 — Verify:
>   Call core/manifest.py or equivalent to verify source/ against the post-merge manifest.
>   Assert: FILE_A, FILE_B, FILE_C, FILE_D all verify clean.
>   Corrupt source/FILE_B.txt (flip one byte).
>   Re-run verify. Assert: FILE_B.txt fails verification, others still pass.
>
> STEP 5 — Projects registry:
>   Call core/projects.py upsert_project() with the source/ and server/ paths.
>   Assert: ~/Documents/STSyncTool/projects.json is created/updated.
>   Assert: entry contains project_id, local_path, server_path, created_at, display_name.
>   Call list_projects() — assert the entry appears.
>   Call get_project() — assert it returns the correct entry.
>
> Fix any failures encountered. Re-run full pytest suite after each patch.
> Confirm ~/Documents/STSyncTool/projects.json exists on disk after step 5.
> Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 4 — Chain-of-custody log completeness

Spawn a subagent with this brief:

> You are fixing the chain-of-custody log in ST SyncTool's offload module.
> This is the highest-priority known issue from the overnight report.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Read first: SYNCTOOL_CONTEXT.md, then core/offload.py (full file)
>
> The current gap: write_chain_of_custody_log() writes per-file pre-hash records and
> per-cell state, but omits:
>   a. An explicit overall verdict line: COMPLETE or PARTIAL_FAILURE
>   b. Explicit per-file post-copy verification PASS/FAIL (currently only inferable
>      from cell state, not directly readable by a human or audit tool)
>
> Tasks:
> 1. Read write_chain_of_custody_log() in full. Understand the current structure.
> 2. Patch it to add:
>    a. A clearly labelled "OVERALL RESULT: COMPLETE" or "OVERALL RESULT: PARTIAL_FAILURE"
>       line near the top of each log file (after the header, before per-file records)
>    b. Per-file post-copy verification: for each file entry, add a "VERIFY: PASS" or
>       "VERIFY: FAIL" line that reflects whether verify_staging() confirmed the hash.
>       If verification data is not available in the current data structure, add it to
>       the OffloadDest or CellState so it flows through to the log writer.
> 3. While in this function, also fix:
>    a. Same-second filename collision: log filename is offload_<YYYYmmdd_HHMMSS>.txt —
>       add a 4-char random hex suffix (e.g. offload_20260610_143022_a3f1.txt)
>    b. .DS_Store filter: offload currently ingests .DS_Store as payload. Add a
>       SKIP_FILENAMES frozenset (at minimum: {'.DS_Store', 'Thumbs.db', 'desktop.ini'})
>       and filter these from prehash_source() so they never enter the pipeline.
> 4. Write a committed test in tests/test_offload.py covering write_chain_of_custody_log:
>    a. Run a full offload with the test fixture from the overnight run
>       (/tmp/test_card_A001/ or re-create it)
>    b. Read the written log file
>    c. Assert: "OVERALL RESULT:" line present
>    d. Assert: every file has a "VERIFY: PASS" or "VERIFY: FAIL" line
>    e. Assert: log filename contains the 4-char hex suffix
>    f. Assert: .DS_Store is not mentioned anywhere in the log
> 5. Run /opt/homebrew/bin/python3.11 -m pytest -v — must stay at 122+ passed / 0 failed
>    (count will increase as new tests are added).
> 6. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 5 — Manifest audit: verify on-disk state after all patches

Spawn a subagent with this brief:

> You are performing a final audit of all manifests and logs under ~/Documents/STSyncTool
> after a full patch and integration test session for ST SyncTool.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Read first: SYNCTOOL_CONTEXT.md (key constants and paths section only)
>
> Tasks:
> 1. List all files under ~/Documents/STSyncTool recursively.
> 2. Identify the freshest manifest in manifests/ — assert it is schema 1.1 with
>    hash_algorithm per entry, root path, generated_at, and schema_version fields.
> 3. Identify any schema 1.0 manifests still on disk. Do NOT delete them.
>    Confirm load_manifest() can read each without error (call it directly).
> 4. Load projects.json — confirm it was written by the pipeline test (Phase 3).
>    Assert structure: project_id, local_path, server_path, created_at, display_name.
>    Assert latest_manifest path (if set) actually exists on disk.
> 5. Read the freshest offload log in offload_logs/. Confirm it now contains:
>    - OVERALL RESULT: line
>    - Per-file VERIFY: PASS / FAIL lines
>    - 4-char hex suffix in filename
>    - No .DS_Store entry
>    If none of these are present, flag it (the Phase 4 patch may not have generated
>    a new log yet — note that rather than treating it as a failure).
> 6. Run /opt/homebrew/bin/python3.11 -m pytest -v as final regression check.
>    Report exact count: N passed / 0 failed.
> 7. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE
>    Include the final pytest count and the list of every file now present under
>    ~/Documents/STSyncTool/.

---

## After all subagents complete

1. Collect all five structured summaries.
2. If any phase has CONFIDENCE: Low, spawn a follow-up subagent to re-investigate
   that specific issue before writing the report.
3. Verify the final pytest count from Phase 5 is >= 122 passed / 0 failed.
4. Write the final report to:
   ~/Documents/STSyncTool/manifest_consistency_report_YYYYMMDD.md
   (replace YYYYMMDD with today's actual date)

Report structure:

---
# ST SyncTool — Manifest Consistency Report
Date: <date>
Model: claude-opus-4-8
Phases run: 5

## Summary
<2-3 sentence overall assessment>

## Schema Compatibility Matrix
<from Phase 1 — reader/writer pairs and whether each handoff is clean>

## Cross-Tab Pipeline Test Results
<step-by-step results from Phase 3: Transfer → Merge → Verify → Projects>

## Bugs Patched
<file, approx line, description, fix summary for each patch>

## Chain-of-Custody Log Improvements
<what was added, new log structure summary>

## Final Test Count
<N passed / 0 failed — from Phase 5 final run>

## On-Disk State
<what now exists in ~/Documents/STSyncTool/ and its validity>

## Remaining Known Issues
<anything not addressed, with a one-line rationale for deferring>

## Confidence Assessment
<overall readiness and top risk to address next session>
---

Do not pad the report. Prioritise actionable findings.
