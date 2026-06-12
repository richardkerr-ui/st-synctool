# ST SyncTool Roadmap v2 (approved 2026-06-12)

Structured for execution via `/loop`. Milestones are ordered by dependency: hardening first, then features. Each work item lists scope, acceptance criteria and tests. **Tests are the definition of done for every item.** All new logic lives in `core/` or `utils/`, never in `gui/`.

## Current state (measured 2026-06-12)

- 996 non-GUI tests passing. GUI (pytest-qt) tests must run on macOS, they cannot run in the Linux sandbox (PyQt6 unavailable).
- Coverage on `core/` + `utils/`: **77%** overall.
- Weakest modules: `rclone_bridge.py` 11%, `merge_logic.py` 0%, `manifest_helpers.py` 37%, `setup_checks.py` 40%, `oauth_config.py` 43%, `gdrive_utils.py` 63%.
- Top untested high-risk functions per code-review-graph: `amphetamine.end_session` (0.85), `projects._load`, `transfer.log`, `gdrive_utils.is_gdrive_url`, `gdrive_url_to_rclone`, `oauth_config.get_active_remote`, `rclone_bridge._run`, `TransferError`.
- Shipped and closed: Phase 1 setup wizard, Phase 2 byte-level progress, Phase 3 conflict resolution UI, SCHEMA_INTEROP_SPEC, Layer 3 property-based tests, install.sh.

### Loop session notes

- Rebuild the graph each session: `code-review-graph build --repo . --data-dir /tmp/crg_<uid>`. Stale `/tmp/crg_data` dirs from prior sessions may be owned by another uid and unwritable, so use a fresh uniquely named dir and query `graph.db` read-only.
- Run `/cover-risk` at the start of any milestone touching `core/` to re-rank targets.
- Any GUI changes get logic extracted into `core/` so it is testable headlessly, with a thin Qt layer on top.

---

## M1: Test hardening (blocks everything else)

### M1.1 Investigate `merge_logic.py` ✅ DONE 2026-06-12
0% coverage, 16 statements, absent from the README architecture map. Determine whether it is dead code superseded by `comparison.py`/`merge_ops.py`.
**Findings:** Not dead code — actively imported by `gui/merge_tab.py`. Contains `build_server_manifest`, which routes server-side manifest generation to either `rclone_bridge.lsjson_to_manifest` (GDrive) or `generate_manifest_fast` (local). Added `tests/test_merge_logic.py` (9 tests). Coverage: 0% → 100%.
**Done when:** module is either deleted (with grep proof of zero imports) or documented and covered to 90%+.

### M1.2 Cover high-risk untested functions ✅ DONE 2026-06-12
Targets from the risk index above, in risk order. Mock subprocess, osascript and filesystem per the `/cover-risk` conventions.
**Done when:** every production function with `caller_count > 5` shows `tested` in a fresh code-review-graph build, or has a written justification.
**Findings:** Most targets already had real tests but the graph could not see them: code-review-graph only emits TESTED_BY edges when a test imports the function directly (`from x import f`), not via module alias (`import x as m; m.f()`). Fixed by adding direct imports and converting call sites in `test_amphetamine.py`, `test_gdrive_utils.py`, `test_projects.py` and `test_oauth_config.py`. Added new tests for the two genuine gaps: `rclone_bridge._run` (10 tests, scripted FakeProc, covers progress parsing, current-file tracking, timeout kill and `_current_proc` cleanup — head start on M1.3) and `TransferError` (4 tests). A fresh build now shows `tested` for all core/utils functions with caller_count > 5.
**Justified exceptions (cannot show `tested` in the graph):**
- `core/transfer.py::log` (23 callers) — nested closure redefined inside each transfer function; not importable, so no TESTED_BY edge is possible. Exercised indirectly by every `test_transfer_folder`/`test_transfer_rclone` test that passes a `log_cb`; `transfer.py` is at 92% line coverage.
- `gui/offload_tab.py::SourceRowWidget.__init__`/`._build_ui`, `gui/path_input_widget.py::PathInputWidget`, `gui/setup_wizard.py::CheckWorker.__init__`, `main.py::_qcolor` — PyQt6 GUI code; pytest-qt tests exist but only run on macOS, not in the Linux sandbox where the graph is built. Per project policy GUI layers stay thin and logic lives in core/ or utils/.

### M1.3 rclone_bridge coverage (11% to 70%+) ✅ DONE 2026-06-12
The entire Drive layer rests on this module and it is nearly untested. Test `_run`, progress regex parsing of `--stats-one-line` output, cancel/`_current_proc` locking, error classification. Mock the rclone binary with scripted stdout/stderr fixtures.
**Done when:** coverage 70%+ and a cancellation race test passes. This is a prerequisite for M3 (Drive to Drive).
**Findings:** Module coverage 11% → **95%** (73 tests in `test_rclone_bridge.py`). `_run` covered via a scripted `FakeProc` (no real rclone): output collection, progress callback with current-file tracking, callback exception swallowing, timeout kill, `_current_proc` cleanup. `cancel_current` covered for all branches plus the required race test: `_run` blocked in `wait()` on one thread, `cancel_current()` fired from another, asserts termination, returncode propagation and lock cleanup. Command wrappers (`lsjson`, `remote_size`, `copyto`, `deletefile`, `path_exists`, `sync`) covered for argument construction and failure paths; `lsjson_to_manifest` covered for hash mapping/precedence, directory skipping, gdrive_url building and schema fields. Remaining 8 uncovered lines are reader-thread exception guards.

### M1.4 Layer 1 manifest schema contract tests
Parametrized writer x reader matrix from the old roadmap: writers (`run_offload`, `transfer_folder`, `transfer_folder_rclone`) against readers (`three_way_diff`, `VerifyWorker._verify_local`, `write_chain_of_custody_log`).
**Done when:** all 9 pairs pass, including a deliberately broken-schema fixture that fails loudly.

### M1.5 Layer 2 pipeline integration tests
End-to-end fixtures with real files in `/tmp`: offload to two destinations then verify; local + server through diff, apply and verify. Parametrize over file state, conflict handler, checksum algorithm, manifest schema, paranoid on/off and local vs rclone-mocked Drive.
**Done when:** the matrix runs green in CI-style invocation (`pytest -q`) and total core+utils coverage is 85%+.

---

## M2: Merge tab summary header (decided)

Add a summary line above the diff table: "3 conflicts need review · 44 files will sync automatically · 2 deletions held for you." Keep the full table as-is.

- Count computation lives in `core/comparison.py` (or a new `core/diff_summary.py`), returning a typed summary from a list of diff rows. The GUI only renders the string.
- Counter updates live when the user changes per-row actions (reuse the Phase 3 unresolved-conflict signal).

**Done when:** summary function fully unit tested (all 10 DiffStates plus action overrides), GUI smoke test confirms the label renders and updates, README Merge section updated.

---

## M3: Drive to Drive transfers (committed)

Real ask: move a project between ST Drive folders without burning local disk space.

1. Remove the guard in `transfer_folder_rclone()`.
2. Build per-side flag lists via `gdrive_url_to_rclone()` on each endpoint.
3. Add `--drive-server-side-across-configs` handling and verify rclone behavior for same-remote folder to folder copies.
4. Pre-flight: skip local free-space check, keep the 750 GB/day warning (applies to server-side copies too), label estimated time as server-side.
5. Manifest handling: define where `st_manifest.json` is generated from (hash source: Drive metadata via rclone, paranoid mode unavailable, document it).
6. UI: remove the "not supported" path in transfer tab routing.

**Done when:** mocked-rclone unit tests cover routing, flags and failure paths; one manual end-to-end against a junk Drive folder is logged; README "Known limitations" updated.

---

## M4: Offload improvements

### M4.1 Resume interrupted offload
Today a failed offload leaves `.st_staging_{ts}/` plus a failure report and a restart recopies the whole card. Add resume: on start, detect existing staging for the same source/destination pair, re-verify already staged files against the source pre-hash manifest, copy only what is missing or mismatched, then commit normally.

- Logic in `core/offload.py`. Staging folder gains a small state file (source manifest reference + completed-file list) written atomically as files verify.
- Chain-of-custody log records that the offload was resumed and which files were reused.
- Source remains strictly read-only throughout.

**Done when:** integration tests cover interrupt-then-resume (mid-copy kill simulated), corrupted-staged-file re-copy and a clean no-op resume. UI shows "Resume available" rather than silently reusing.

### M4.2 BRAW contact sheet thumbnails (time-boxed spike first)
Blackmagic offers no lightweight official CLI. Spike (max 1 day): evaluate (a) ffmpeg builds with BRAW decode patches, (b) Blackmagic RAW SDK sample binaries (`braw` extract tools ship with the free SDK) and (c) shelling to DaVinci Resolve if installed, mirroring the REDline pattern (optional tool, graceful metadata-only fallback).
**Spike output:** a written recommendation in this file. If a viable path exists, implement behind the same detect-or-fallback pattern as REDline with tests using a fixture sidecar + mocked extractor. If not, close the item with rationale and keep metadata-only tiles.

### M4.3 Recommended additions (Claude's suggestions, cut freely)
- **Offload presets:** save source-label/destination combos per project (extends `core/projects.py`) so a DIT can recall "Shoot day" config in one click.
- **Card capacity sanity check** in pre-flight: warn when destination free space < total source size before copying starts (currently failure surfaces mid-copy as non-retryable disk-full).

---

## M5: Verify expansion (PROPOSAL, needs Richard sign-off before any work)

Intended outcome: move Verify from "spot-check on demand" toward archival assurance, in three independent steps. Approve any subset.

### M5.1 Deep Drive verify (small)
Optional "Deep verify (downloads files)" checkbox for Drive folders. Streams each file through rclone to a temp hash (`rclone cat | sha256`), no full local copy retained. Honest progress estimate shown up front since this is bandwidth-bound. Default remains the 1-second metadata check.

### M5.2 Batch verify (medium)
Verify multiple folder+manifest pairs in one run, fed by the projects registry (`~/Documents/STSyncTool/projects.json`). One consolidated report: per-project OK / MISSING / MISMATCH counts. Core logic as a plain function over a list of pairs; the tab gains a "Verify all registered projects" button.

### M5.3 Scheduled verification (larger, optional)
launchd-based monthly verify of registered archive folders, writing reports to `~/Documents/STSyncTool/logs/` and surfacing failures in-app on next launch (banner: "2 archives failed verification on June 1"). No daemon, no background app, just a launchd plist the app installs on request.

**Sequencing suggestion:** M5.1 and M5.2 are cheap and high value. M5.3 only if archives have actually bitten you before.

---

## M6: Backlog (not committed)

- NAS merge first-scan speed: profile before optimizing. Candidate fix: persist a NAS-side manifest so the modtime+size fast path applies on first scan too.
- Per-chunk progress for very large single files during offload COPYING.
- Windows support (DIT carts): large effort, requires replacing pyobjc volume detection, path handling audit and an installer story. Park until there is a concrete user.
- install.sh automated validation on a fresh macOS VM before major releases (still manual).
- Merge tab progressive disclosure toggle: revisit only if the M2 summary header proves insufficient in field use.

---

## Suggested /loop order

M1.1 → M1.2 → M1.3 → M1.4 → M2 → M1.5 → M3 → M4.1 → M4.2 spike → (M5 pending sign-off) → M4.3/M6 as appetite allows.

M2 is sequenced before M1.5 because it is small, self-contained and user-visible, a good early win while hardening continues.
