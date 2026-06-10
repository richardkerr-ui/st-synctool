# ST SyncTool — Overnight Smoke & Sanity Test
# Run with: claude --model claude-opus-4-8
# From: /Users/richard.kerr/Claude/Projects/ST SyncTool

---

## Your role

You are the orchestrator for an overnight smoke and sanity test of ST SyncTool after a
significant day of commits. You will spawn subagents using the Task tool to handle each
test phase in isolation. You do NOT read full source files yourself. Your context is
reserved for coordination and final synthesis.

Work autonomously overnight. Do not stop to ask questions. If something is ambiguous,
make the most conservative reasonable choice and note it in your report.

---

## Before spawning subagents

Read these two files in full — they are the authoritative spec and you need them to
evaluate subagent summaries accurately:

- /Users/richard.kerr/Claude/Projects/ST SyncTool/SYNCTOOL_CONTEXT.md
- /Users/richard.kerr/Claude/Projects/ST SyncTool/README.md

Do not read any other files yourself. Delegate everything else.

---

## Repo & environment facts (share these with every subagent)

- Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
- Python entry point: main.py (PyQt6 GUI — do NOT run interactively)
- Test runner: pytest -v (from repo root)
- Log/manifest output root: ~/Documents/STSyncTool
  - offload_logs/      chain-of-custody logs
  - manifests/         archived manifests
  - contact_sheets/    generated contact sheets
  - projects.json      projects registry
- All patches must include a comment: # OVERNIGHT-FIX: <short description>
- Fix bugs in source, not in tests (unless a test assertion is provably wrong per spec)
- After any patch, re-run the full test suite to confirm no regressions

---

## Subagent phases — spawn these in order

Each subagent must return a structured summary with these sections:
  PASSED: (list)
  FAILED: (list)
  PATCHED: (file, line range, description)
  ANOMALIES: (anything unexpected)
  CONFIDENCE: (High / Medium / Low — with one sentence rationale)

### Phase 1 — Full test suite baseline

Spawn a subagent with this brief:

> You are running the ST SyncTool test suite as a baseline before integration testing.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
>
> Tasks:
> 1. Run `pytest -v` from repo root. Capture full output.
> 2. For every failure: read the relevant source file and test file, diagnose root cause,
>    patch the source (not the test unless the assertion is provably wrong per
>    SYNCTOOL_CONTEXT.md spec), add a comment `# OVERNIGHT-FIX: <description>` above
>    the change.
> 3. Re-run pytest after each patch until all tests pass.
> 4. Report: total tests, pass count, fail count, list of patches applied.
>
> Return a structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 2 — scan_existing() and VolumeWatcher unit tests

Spawn a subagent with this brief:

> You are testing the volume detection logic in ST SyncTool.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Key file: utils/volume_watcher.py
>
> Tasks:
> 1. Read utils/volume_watcher.py in full.
> 2. Write and run pytest tests (in a temp file, not committed) covering:
>    a. scan_existing() — monkey-patch Path("/Volumes").iterdir() to return temp dirs
>       under /tmp that mimic: DCIM/ volume, PRIVATE/ volume, .rdm file volume, a plain
>       external drive (no markers), a Time Machine volume. Assert only the media-card
>       volumes are returned.
>    b. _sanitise_label() — test inputs: "NO NAME", "UNTITLED", "EOS_DIGITAL", a normal
>       name, a name with filesystem-unsafe characters. Assert correct output.
>    c. _looks_like_media_card() — test a root with DCIM at depth 0, DCIM at depth 1
>       (Canon-style), no markers, .rdm file at root.
>    d. VolumeWatcher.volume_mounted signal — instantiate in a QApplication, emit with
>       a mock volume dict, confirm the signal fires with the correct payload.
>    e. Volume already mounted before app start — confirm scan_existing() finds it
>       without needing a mount event.
> 3. Fix any bugs found. Re-run until all pass.
> 4. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 3 — R3D support and filename normalisation

Spawn a subagent with this brief:

> You are testing R3D support (Phase 8) and filename normalisation (Phase 7) in
> ST SyncTool.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Key files: core/offload.py, core/thumbnail.py
>
> Tasks:
> 1. Read core/offload.py and core/thumbnail.py in full.
> 2. Create fixture files under /tmp/st_r3d_test/:
>    - Valid R3D stems: A001_C001_230615.R3D, B002_C003_230616.R3D
>    - Invalid/generic stems: CLIP_0001.R3D, IMG_0001.R3D (should these be renamed?
>      Check _R3D_STEM regex — stems NOT matching it are NOT protected)
>    - RDC directory: A001.RDC/ containing A001_C002_230615.R3D
>    - RMD sidecars: A001_C001_230615.RMD alongside the R3D
>    - Sidecar files: IMG_0001.SRT, IMG_0001.THM, DJI_0001.XML
>    - Known sequential: IMG_0001.MOV, MVI_0002.MP4, GH0_00001.MP4, DJI_0003.MOV
>    - Generic non-sequential: PROJECT_A.MOV, INTERVIEW_WIDE.MOV (must NOT be renamed)
> 3. Run build_normalization_plan() against this fixture. Assert:
>    - Valid R3D stems (matching _R3D_STEM) are excluded from renaming
>    - Known sequential patterns are flagged for renaming
>    - Sidecar files travel with their parent clip in the rename plan
>    - Generic non-sequential names are NOT renamed
> 4. Run find_rdc_clips() and confirm it identifies the .RDC directory correctly.
> 5. Run apply_normalization_in_staging() and verify the output matches the plan.
> 6. Fix any bugs. Re-run pytest -v from repo root after patches to check regressions.
> 7. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 4 — Full offload pipeline integration test

Spawn a subagent with this brief:

> You are running a full end-to-end integration test of the ST SyncTool offload pipeline.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Key file: core/offload.py
> Log output dir: ~/Documents/STSyncTool/offload_logs/
>
> Tasks:
> 1. Read core/offload.py in full.
> 2. Create a realistic camera card fixture at /tmp/test_card_A001/:
>    - DCIM/IMG_0001.MOV, IMG_0002.MOV, IMG_0003.MOV (~2KB binary files)
>    - DCIM/IMG_0001.SRT, IMG_0002.SRT (sidecar text files)
>    - AUDIO/SOUND001.WAV, SOUND002.WAV (~1KB binary files)
>    - A001_C001_230615.R3D, A001_C001_230615.RMD (~1KB binary files)
>    - A001.RDC/A001_C002_230615.R3D (~1KB binary file)
>    Destination: /tmp/test_offload_dest/
> 3. Run each pipeline stage individually with mock log_cb and status_cb, asserting
>    correct behaviour at each step:
>    a. preflight_source_readonly() — confirm source is never written to
>    b. prehash_source() — confirm all files get hash entries, no files skipped
>    c. copy_source_to_staging() — confirm staging dir created, all files present with
>       correct relative paths
>    d. verify_staging() — confirm hash comparison passes for all files
>    e. commit_staging() — confirm files moved to dest, staging dir cleaned up
>    f. write_failure_report() — force a synthetic failure, confirm report is written
>       with correct structure
> 4. Run run_offload() end-to-end. Confirm it completes without errors.
> 5. Read the chain-of-custody log written to ~/Documents/STSyncTool/offload_logs/.
>    Verify it contains: source label, dest path, per-file hash records, timestamps,
>    pass/fail per file, overall result. Flag any missing or malformed fields.
> 6. Confirm R3D files (valid stems) kept original names. Confirm IMG_XXXX files were
>    renamed. Confirm sidecar .SRT files were renamed alongside parent clips.
> 7. Fix any bugs. Re-run pytest -v from repo root after patches.
> 8. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 5 — Comparison and merge logic edge cases

Spawn a subagent with this brief:

> You are testing the three-way diff (comparison) and merge operations in ST SyncTool.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Key files: core/comparison.py, core/merge_ops.py, core/manifest.py
> Spec: SYNCTOOL_CONTEXT.md (read the "Current manifest workflow" and "Known issues"
> sections — they define expected behaviour and known edge cases)
>
> Tasks:
> 1. Read core/comparison.py, core/merge_ops.py, and core/manifest.py.
> 2. Read the relevant sections of SYNCTOOL_CONTEXT.md.
> 3. Run tests/test_comparison.py and tests/test_merge_ops.py in isolation. Fix any
>    failures before proceeding.
> 4. Write and run additional edge-case tests covering:
>    a. DELETED_BOTH — file in base manifest only, gone from both local and server
>    b. BOTH_CHANGED — file modified on both sides simultaneously
>    c. Preserve-rename path — after preserve_rename fires, the renamed file's new path
>       must appear correctly in the regenerated manifest so the NEXT scan does not
>       flag it as LOCAL_ONLY or SERVER_ONLY conflict (this is the rename collapse bug
>       fixed today — verify the fix holds)
>    d. Schema version — manifest written by generate_manifest_fast must include
>       schema_version field; load_manifest must not crash on a v1.1 manifest
>    e. Hash algorithm recorded — manifest entries must include hash_algorithm field,
>       not just hash value
> 5. Fix any bugs found. Re-run full pytest suite after patches.
> 6. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE


### Phase 6 — Log and manifest audit

Spawn a subagent with this brief:

> You are auditing all log and manifest files written to ~/Documents/STSyncTool by
> ST SyncTool's overnight test run.
> Repo root: /Users/richard.kerr/Claude/Projects/ST SyncTool
> Spec: SYNCTOOL_CONTEXT.md (key constants and paths section)
>
> Tasks:
> 1. List all files under ~/Documents/STSyncTool recursively.
> 2. For each file in offload_logs/:
>    - Confirm presence of: source label, mount path, total file count, total bytes,
>      per-file records (original name, hash algorithm, hash value, dest path,
>      verification result), overall result (COMPLETE or PARTIAL_FAILURE), timestamp
>    - Flag: missing hashes, empty dest paths, unresolved staging paths, files listed
>      as PASS where hash is empty or "None", duplicate file entries
> 3. For each file in manifests/:
>    - Confirm presence of: schema_version, root path, entries array
>    - Each entry must have: path, size, mtime, hash, hash_algorithm
>    - Flag: entries with null/empty hash, root path that doesn't exist on disk,
>      schema_version missing or not "1.1"
> 4. For projects.json (if it exists):
>    - Confirm each entry has: project_id, local_path, server_path, created_at,
>      display_name
>    - Flag: duplicate project_ids, latest_manifest paths that are set but don't exist
>      on disk, entries with empty local_path or server_path
> 5. For contact_sheets/ (if any files exist):
>    - Confirm each sheet references files that exist in the corresponding offload dest
>    - Flag missing or broken references
> 6. Return structured summary: PASSED / FAILED / PATCHED / ANOMALIES / CONFIDENCE
>    Include a flat list of every anomaly found, even minor ones.

---

## After all subagents complete

1. Collect all six structured summaries.
2. Cross-check: if Phase 1 found a patch, confirm Phase 4-6 re-ran tests after it.
   If any phase has CONFIDENCE: Low, flag it prominently in the report.
3. Write the final report to:
   ~/Documents/STSyncTool/overnight_test_report_YYYYMMDD.md
   (replace YYYYMMDD with today's actual date)

Report structure:

```
# ST SyncTool — Overnight Test Report
Date: <date>
Model: claude-opus-4-8
Phases run: 6

## Summary
<2-3 sentence overall assessment>

## Test Suite Results
<pass/fail counts, regression status after patches>

## Integration Test Results
<offload pipeline, R3D, normalisation — what ran, what passed, what failed>

## Log & Manifest Audit
<anomalies found, whether corrected or flagged for manual review>

## Bugs Patched
<for each patch: file, approx line range, description, fix summary>

## Known Issues Not Fixed
<things found but not patched — too risky, out of scope, or needs human decision>

## Confidence Assessment
<overall readiness, risks to flag for next session>
```

Do not pad the report. Prioritise actionable findings.
