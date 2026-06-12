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

### M1.4 Layer 1 manifest schema contract tests ✅ DONE 2026-06-12
Parametrized writer x reader matrix from the old roadmap: writers (`run_offload`, `transfer_folder`, `transfer_folder_rclone`) against readers (`three_way_diff`, `VerifyWorker._verify_local`, `write_chain_of_custody_log`).
**Done when:** all 9 pairs pass, including a deliberately broken-schema fixture that fails loudly.
**Findings:** New `tests/test_manifest_writer_reader_matrix.py` (16 tests). All three writers run their real pipelines end-to-end on tmp files (rclone faked at the lsjson layer with real sha256 hashes so the real `lsjson_to_manifest` path runs); manifests are loaded back from the persisted `st_manifest.json`, not taken from return values. All 9 pairs pass plus corruption-detection per writer and the broken-schema fixtures (non-mapping `files`, garbage sizes) which raise in every reader. Notes: (1) `VerifyWorker._verify_local` lives in `gui/verify_tab.py` (PyQt6) so its hash loop is mirrored headlessly, same approach as `test_manifest_schema_contract.py`; the real worker runs in `test_drive_verify.py` on macOS. (2) `write_chain_of_custody_log` consumes flat `{rel: {size, checksum}}` pre-hash manifests, not full schema manifests; full manifests are tolerated (meta keys filtered) — regression-tested.

### M1.5 Layer 2 pipeline integration tests ✅ DONE 2026-06-12
End-to-end fixtures with real files in `/tmp`: offload to two destinations then verify; local + server through diff, apply and verify. Parametrize over file state, conflict handler, checksum algorithm, manifest schema, paranoid on/off and local vs rclone-mocked Drive.
**Done when:** the matrix runs green in CI-style invocation (`pytest -q`) and total core+utils coverage is 85%+.
**Findings:** New `tests/test_pipeline_integration.py` (24 tests). Pipeline A: offload to 1 and 2 destinations with real files, every dest hash-verified against its persisted manifest, corruption detected, custody log covers both dests, source untouched. Pipeline B: diff → merge_ops apply → clean re-scan for every actionable DiffState including both conflict resolutions, preserve-on-overwrite (asserting the documented persistent-divergence semantics), deletion propagation both directions, a v1.0 base manifest migrating through `load_manifest` and per-algorithm verify (sha256/xxhash3_64/md5); Drive merge path covered with rclone mocked at the bridge. Pipeline C: `transfer_folder` over all three conflict handlers asserting on-disk outcomes plus manifest verification. Coverage top-ups for the weakest modules: `manifest_helpers` 37% → 100%, `oauth_config` 43% → 100%, `gdrive_utils` 61% → 100%. Suite 1115 → 1183 tests, core+utils coverage 83% → **87%**. Note: `utils/volume_watcher.py` (0%) is pyobjc/macOS-callback code exercised only by the excluded `test_volume_watcher.py` GUI file; it will be covered by M7.2 CI on a macOS runner.

---

## M2: Merge tab summary header ✅ DONE 2026-06-12 (GUI smoke test needs one manual Mac run)

Add a summary line above the diff table: "3 conflicts need review · 44 files will sync automatically · 2 deletions held for you." Keep the full table as-is.

- Count computation lives in `core/comparison.py` (or a new `core/diff_summary.py`), returning a typed summary from a list of diff rows. The GUI only renders the string.
- Counter updates live when the user changes per-row actions (reuse the Phase 3 unresolved-conflict signal).

**Done when:** summary function fully unit tested (all 10 DiffStates plus action overrides), GUI smoke test confirms the label renders and updates, README Merge section updated.
**Findings:** New `core/diff_summary.py`: `summarize_diff(results, actions)` returns a frozen `DiffSummary` dataclass; `to_text()` renders the header with pluralization and zero-segment omission. The action-options-per-state table moved from `gui/diff_table.py` into core as `ACTION_OPTIONS_BY_STATE` (single source of truth; the GUI imports it). `conflict_action_changed` now fires for every row's combo, not just conflicts, so the header stays live on any action change. 39 unit tests in `test_diff_summary.py` (all 10 states, defaults incl. mtime-based conflict suggestion, overrides, text rendering). Three GUI smoke tests added to `test_gui_smoke.py` (hidden before scan, renders counts, updates on combo change) — **pending one manual run on the Mac** since PyQt6 is unavailable in the sandbox; will also be covered automatically once M7.2 CI lands. README Merge section documents the header. Suite 1076 → 1115 tests, coverage 82% → 83%.

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

## M5: Verify expansion (M5.1 + M5.2 APPROVED by Richard 2026-06-12, M5.3 parked)

Intended outcome: move Verify from "spot-check on demand" toward archival assurance, in three independent steps. Richard approved M5.1 and M5.2 on 2026-06-12; M5.3 stays parked unless archives fail in the field.

### M5.0 Extract verify logic to core/ (prep, small)
`VerifyWorker._verify_local` currently lives in `gui/verify_tab.py` (PyQt6), so its hash loop is only testable on macOS and is mirrored by hand in the contract tests. Move the verification loop into `core/` (e.g. `core/verify.py`), leave a thin Qt worker that emits signals. Prerequisite for M5.1 and M5.2 so their logic lands headlessly testable.
**Done when:** verify logic imports cleanly without PyQt6, existing verify behavior unchanged, contract tests use the real function instead of the mirror and GUI tests still pass on the Mac.

### M5.1 Deep Drive verify (small)
Optional "Deep verify (downloads files)" checkbox for Drive folders. Streams each file through rclone to a temp hash (`rclone cat | sha256`), no full local copy retained. Honest progress estimate shown up front since this is bandwidth-bound. Default remains the 1-second metadata check.

### M5.2 Batch verify (medium)
Verify multiple folder+manifest pairs in one run, fed by the projects registry (`~/Documents/STSyncTool/projects.json`). One consolidated report: per-project OK / MISSING / MISMATCH counts. Core logic as a plain function over a list of pairs; the tab gains a "Verify all registered projects" button.

### M5.3 Scheduled verification (larger, optional)
launchd-based monthly verify of registered archive folders, writing reports to `~/Documents/STSyncTool/logs/` and surfacing failures in-app on next launch (banner: "2 archives failed verification on June 1"). No daemon, no background app, just a launchd plist the app installs on request.

**Sequencing:** M5.0 first (it unblocks headless testing), then M5.1 and M5.2 in either order. M5.3 only if archives have actually bitten you before; not approved, do not start.

---

## M6: Backlog (not committed)

- NAS merge first-scan speed: profile before optimizing. Candidate fix: persist a NAS-side manifest so the modtime+size fast path applies on first scan too.
- Per-chunk progress for very large single files during offload COPYING.
- Windows support (DIT carts): large effort, requires replacing pyobjc volume detection, path handling audit and an installer story. Park until there is a concrete user.
- install.sh automated validation on a fresh macOS VM before major releases (still manual).
- Merge tab progressive disclosure toggle: revisit only if the M2 summary header proves insufficient in field use.

---

## M7: Beta distribution (approved 2026-06-12)

End goal: a finished app ready to hand to beta testers who are not developers. These items are required before recruiting any testers.

### M7.1 One-click install (signed DMG)
Package the app as a normal macOS .app inside a DMG (PyInstaller or Briefcase; evaluate both, pick the one that handles PyQt6 + bundled binaries with least friction). Code-sign and notarize so Gatekeeper shows no warnings. Requires an Apple Developer account ($99/year, Richard to set up; the build scripts can be written and tested unsigned before the account exists). Document the build in a `release.md` runbook.
**Done when:** a fresh Mac can download the DMG, drag to Applications and launch with no security warnings and no terminal use. ffmpeg/rclone dependency handling decided and documented (bundle vs first-run install prompt).

### M7.2 CI on GitHub Actions
macOS runner executing the full pytest suite, including the pytest-qt GUI tests that cannot run in the local sandbox, on every push. Coverage report in the job output.
**Done when:** a failing test fails the workflow on push to main and the README shows a status badge.

### M7.3 Feedback and crash loop
"Report a problem" menu item that zips recent logs from `~/Documents/STSyncTool/` plus the app version into one file the tester can email. Visible version number in the UI (About dialog or window title). Zip/collect logic in `core/` or `utils/`, thin GUI on top.
**Done when:** the zip contains logs + version + OS info, core logic unit tested, manual GUI check on the Mac.

### M7.4 Quick start guide
One page with screenshots covering the three core flows: offload a card, merge a project, verify an archive. Lives in the repo (`docs/QUICKSTART.md`) and ships with the DMG.
**Done when:** a non-developer can complete each flow following only the guide.

---

## M8: AI assist features (approved 2026-06-12, post-beta)

Optional Claude API integrations. Approved by Richard with the note that every Signal Theory user will have a Claude account. Token cost is negligible (cents per use with claude-haiku-4-5); the design decision is auth, settled as follows: **one Signal Theory workspace API key** configured centrally (env var `ST_SYNC_ANTHROPIC_KEY` or the existing `~/.config/st_synctool/config.json`), so end users never handle billing. A regular Claude.ai subscription does not include API access, so per-user keys would mean per-user developer accounts; central key avoids that entirely.

Shared constraints for all three items:
- All API logic in `core/ai_assist.py` via the official `anthropic` Python SDK. GUI stays thin.
- Graceful degradation: if no key is configured the features are hidden, never an error. The app must work fully without them.
- Privacy: send only metadata (filenames, sizes, hashes, log text), never file contents.
- Default model `claude-haiku-4-5` (fast, cheapest, ample for summarization); model string in config, not hardcoded.
- Tests mock the Anthropic client; no live API calls in the suite. One manual live check on the Mac per feature.

### M8.1 Plain-language verify reports
After a verify run, send the per-file OK/MISSING/MISMATCH results and get back a one-paragraph summary a producer can read ("All 312 files in the June archive match their checksums except two RED clips that are missing"). Rendered above the results table with a "AI summary" label.
**Done when:** summary function unit tested with mocked client, truncation strategy for very large result sets defined and tested, GUI renders or hides cleanly based on key presence.

### M8.2 Smart error explanations
When a transfer or offload fails, pass the raw error text to Claude and show a human-readable explanation with a suggested fix next to the technical message. Cached per unique error string per session so repeats cost nothing.
**Done when:** explanation function unit tested with mocked client and the raw error always remains visible (the AI text supplements, never replaces).

### M8.3 Chain-of-custody log Q&A
Ask questions like "when was this card offloaded and to where?" against `~/Documents/STSyncTool/offload_logs/`. Picks relevant log files by date/name, sends their text as context, shows the answer with the source log filename cited.
**Done when:** log selection and prompt assembly unit tested, answers cite which log they came from, token use per question stays under a defined budget (~50K input).

**Sequencing:** post-beta. M8.2 first (smallest, highest everyday value), then M8.1, then M8.3.

---

## M9: Org-wide activity log (proposed 2026-06-12, design recommended below, Richard to confirm)

Goal: a full view of all production activity (offloads, transfers, merges, verifies) across every Signal Theory user. Raised by Richard 2026-06-12: logs currently live only in each user's `~/Documents/STSyncTool/`.

**Recommended design: ship logs to a shared Google Drive folder via the rclone remote every user already has.** Local stays the source of truth (custody logs must exist even offline); a background "log shipping" step uploads new files after each operation and on app launch. Layout: `ST_SyncTool_Activity/{workstation}/{user}/...` mirroring the local folders. Because every log and manifest filename is timestamped and unique (offload logs already carry a random suffix), shipping is append-only `rclone copy` with no conflicts and no reconciliation logic at all.

Why Drive over Synology-direct: field DIT carts are not on the office network, but they already have Drive configured in this app; shipping works from anywhere with internet and queues quietly when offline. The Synology still gets its copy for free: point Synology **Cloud Sync** (built-in) at the same Drive folder and the NAS mirrors it with zero code in our app. Office tools read the NAS; remote tools read Drive; both see the same corpus.

### M9.1 Log shipping (small, high value during beta)
`core/log_sync.py`: enumerate new files under `~/Documents/STSyncTool/` (logs + manifest archive), `rclone copy` them to the shared folder, remember what shipped (small state file), never delete anything remotely. Runs after each operation and on launch; fully silent on failure (retry next time), opt-out toggle in settings.
**Done when:** unit tests with mocked rclone cover new-file detection, state tracking, retry after offline and the never-delete invariant; one manual end-to-end against a junk Drive folder; README updated.

### M9.2 Activity index (later)
A small builder that walks the aggregated corpus into one SQLite file (operation, user, workstation, source, dests, file counts, bytes, verdict, timestamps) for dashboards and fast queries. Also becomes the retrieval layer for M8.3 log Q&A so answers can span the whole org, not just one machine.
**Done when:** index builds idempotently from the corpus, query helpers tested, M8.3 reads from it when present.

**Sequencing suggestion:** M9.1 right after M7.2 (CI) so beta testers' activity flows centrally from day one of the beta; M9.2 alongside or after M8.

---

## Suggested /loop order (sequencing approved by Richard 2026-06-12)

M1.1 ✅ → M1.2 ✅ → M1.3 ✅ → M1.4 ✅ → M2 ✅ → M1.5 ✅ → M3 → M4.1 → M4.2 spike → M5.0 → M5.1 → M5.2 → M7.1 → M7.2 → M7.3 → M7.4 → recruit beta testers.

M4.3 and M6 as appetite allows; they do not block beta. M8 (AI assist) is approved but post-beta: M8.2 → M8.1 → M8.3 after testers have builds in hand. M5.3 parked, not approved. M2 is sequenced before M1.5 because it is small, self-contained and user-visible, a good early win while hardening continues.
