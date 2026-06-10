# ST SyncTool — Known-Issues Follow-up

Date: 2026-06-10
Model: claude-opus-4-8
Branch: `known-issues-followup` (off latest `main`, in an isolated worktree, NOT pushed)
Baseline: 135 passed / 0 failed. Final: 152 passed / 0 failed.

Every item below was reconciled against current `main` before any action. Line
numbers are as-found in this worktree.

---

## Stale items confirmed ALREADY RESOLVED

These predate the manifest-consistency session that already landed on `main`.
Verified in code, no change needed.

- **COC log completeness (overall verdict + per-file PASS/FAIL).** RESOLVED.
  `core/offload.py` `write_chain_of_custody_log`: overall verdict computed at
  `offload.py:648-651` and written as `OVERALL RESULT: COMPLETE|PARTIAL_FAILURE`
  at `offload.py:658`; per-file `Verified: PASS/FAIL` at `offload.py:698-699`
  and per-file `VERIFY: PASS|FAIL <rel>` lines at `offload.py:700-704`. Covered
  by `tests/test_offload.py::TestChainOfCustodyLog`.

- **`.DS_Store` / OS junk ingested by offload.** RESOLVED. `SKIP_FILENAMES`
  frozenset (`.DS_Store`, `Thumbs.db`, `desktop.ini`) at `offload.py:36-38`,
  applied case-insensitively in `prehash_source` at `offload.py:393-396`, so
  junk never enters pre-hash/copy/verify/manifest/log. Covered by
  `TestChainOfCustodyLog::test_ds_store_never_appears_in_log`.

- **Same-second COC log filename collision.** RESOLVED. `write_chain_of_custody_log`
  appends a 4-char hex suffix (`secrets.token_hex(2)`) at `offload.py:642-644`,
  giving `offload_<YYYYmmdd>_<HHMMSS>_<4hex>.txt`. Two runs in the same second no
  longer clobber. Covered by `test_log_filename_has_4char_hex_suffix`.

- **Test-fixture logs leaking into the production dir.** RESOLVED. Both offload
  test modules redirect outputs to `tmp_path` via autouse fixtures:
  `tests/test_offload.py:48-61` (`OFFLOAD_LOGS_DIR` + stubbed
  `save_offload_manifest`) and `tests/test_acceptance_manifest.py:40-44`
  (`OFFLOAD_LOGS_DIR` + `LOCAL_MANIFEST_DIR`). No test writes into
  `~/Documents/STSyncTool/`. (The 10 leftover logs noted in the overnight report
  are pre-existing files on the user's disk, not produced by the current suite;
  safe for the user to delete manually.)

---

## Item 1 — COC hash strength (sha256 vs xxhash3_64)

State: **RESOLVED in reality; the "xxhash3_64" claim was STALE. Documenting intent.**

The overnight report claimed "offload pre-hash manifests use xxhash3_64 (16-char),
so the chain-of-custody log carries a non-cryptographic hash." That is not what the
code does.

Evidence:
- Offload pre-hash computes **sha256 only**: `core/offload.py:361-362`
  `_sha256()` calls `compute_all(path, include_xxhash=False)["sha256"]`.
  `prehash_source` records `"algorithm": "sha256"` (`offload.py:404`). There is
  no xxhash call anywhere in `core/offload.py` (only reference to xxhash in the
  codebase is `core/checksum.py`, which offload explicitly disables via
  `include_xxhash=False`).
- The persisted offload manifest carries the **full 64-char sha256** in
  `checksums.sha256` (`build_offload_manifest`, `offload.py:570`). Asserted by
  `tests/test_acceptance_manifest.py::TestFullHashInManifestTruncationInLog`.
- The 16-char value the audit saw is **presentation-only truncation in the prose
  `.txt` COC log**: `offload.py:683` writes `info.get('checksum','')[:16]`. The
  machine record (the JSON manifest) keeps the full hash. This is intentional and
  the spec confirms it (`SCHEMA_INTEROP_SPEC.md` lines 76, 174-175: "Carries the
  full 64-char sha256, never truncated. The 16-char truncation is presentation
  only and stays confined to the COC text log").

Recommendation: **No code change.** sha256 already provides evidentiary strength
and the full hash is persisted in the manifest. The only residual is cosmetic: the
prose `.txt` log shows a 16-char prefix. If a future audit wants the full hash
human-readable in the `.txt` too, change `offload.py:683` from `[:16]` to the full
string (one-line, no schema impact) — but that is a presentation preference, not a
custody-strength gap. This proposal recommends leaving it truncated (keeps the log
scannable) and treating the JSON manifest as the authoritative custody record.

---

## Item 2 — Subfolder collision warning (Phase 5 #24)

State: **FIXED NOW (mechanical).** Commit `Warn on offload subfolder collision`.

Problem: when two sources resolve to the same effective subfolder
(`OffloadSource.effective_subfolder()`, `offload.py:94-95`), they write into the
same `{dest}/{subfolder}/` at every destination and `commit_staging`
(`offload.py:492-498`) moves the second source's files in alongside the first —
silent merge, defeating the Phase 5 #24 per-source separation.

What changed:
- `core/offload.py`: new `detect_subfolder_collisions(sources) -> {folder: [labels]}`
  (case-insensitive via `casefold()`, because destinations are commonly on
  case-insensitive filesystems; excludes disabled sources).
- `core/offload.py` `run_offload`: emits a non-blocking `log_cb(..., "warning")`
  for each colliding subfolder. The offload still proceeds (never blocks an
  in-flight ingest).
- `gui/offload_tab.py` `_start_offload`: a Yes/No confirm dialog listing the
  colliding subfolders and their sources before the run starts; default is No
  (cancel).

Tests added (`tests/test_offload.py::TestSubfolderCollision`, 6):
distinct labels → no collision; identical labels collide; override-to-same
collides; case-insensitive match; disabled source excluded; and an end-to-end
`run_offload` assertion that the warning fires AND both sources' files land in
the merged directory (behaviour preserved, only a warning added).

---

## Item 3 — On-disk migration of old schema-1.0 manifests

State: **FIXED NOW (opt-in utility).** Commit `Add opt-in on-disk manifest schema migration sweep`.

Problem: `load_manifest` runs `_migrate` to backfill pre-1.1 manifests in memory
(`core/manifest.py:101-133`) but never rewrites the file, so the archive keeps
stale 1.0 JSON indefinitely (37 such files noted on the user's disk).

What changed — three helpers in `core/manifest.py`, none auto-invoked:
- `needs_migration(path) -> bool` — true if the file's `schema_version` is below
  `SCHEMA_VERSION`; unparseable files return False.
- `migrate_manifest_file(path, backup=True) -> bool` — migrates and rewrites a
  single file, preserving the original as `<name>.json.bak` when `backup=True`.
- `migrate_manifests_on_disk(archive_dir=None, dry_run=True, backup=True) -> dict`
  — recursive sweep over the archive (covers per-project subdirs). **dry-run by
  default**; ignores its own `.bak` files so re-runs are idempotent; never raises
  on a bad file (records it in `errors`). Returns a
  `{scanned, migrated, skipped, errors, dry_run}` report.

It is opt-in: nothing in the app or test suite calls it. A human runs e.g.
`python3 -c "from core.manifest import migrate_manifests_on_disk as m; print(m(dry_run=False))"`.

Tests added (`tests/test_manifest_migration.py`, 11): all pointed at `tmp_path`,
never the real archive. Cover needs_migration true/false/unparseable; single-file
rewrite + version bump + backfilled fields on disk; backup preservation;
current-file untouched; dry-run changes nothing; apply migrates only old files;
re-run idempotency; missing dir → empty report; bad JSON recorded not raised.

Note (non-blocking): `needs_migration` uses string comparison `< SCHEMA_VERSION`,
matching the existing `_migrate` convention (`manifest.py:110`). Fine for "1.0" vs
"1.1"; if the schema ever reaches "1.10" the string compare would misorder. Worth a
shared `version_tuple()` helper when that day comes — out of scope here to avoid
touching `_migrate`'s comparison.

---

## Item 4 — `root` vs `server_path` path vocabulary (DESIGN — proposal only)

State: **PROPOSED. No code change** (renaming risks breaking `load_manifest`
migration consumers and `comparison.py`, which the brief forbids touching
unilaterally).

### What each producer puts in `root` and `server_path` today

| Producer | `root` | `server_path` | `destination` / other |
|---|---|---|---|
| `core/manifest.py` `generate_manifest` / `_fast` (`manifest.py:47,51,150,154`) | `str(folder)` — the **local scanned folder** (commented "display label only") | the `server_path` arg passed in (the remote/server side); `""` if not supplied | `destination = dest_path or ""` |
| `core/transfer.py` local transfer (`transfer.py:187-189`) | `str(src)` — the **source** of the copy | `str(actual_dest)` — the **destination** of the copy | also `source_root=str(src)`, `dest_root=str(actual_dest)` |
| `core/transfer.py` rclone transfer (`transfer.py:306-313`) | (inherited from `lsjson_to_manifest`, see below) | `str(src) if src_is_url else str(dst)` — the **Drive URL side**, whichever end is the remote | also `source_root`, `dest_root`, `source_url`, `dest_url` |
| `core/rclone_bridge.py` `lsjson_to_manifest` (`rclone_bridge.py:162-163`) | `remote_path` — the **listed remote path** | `remote_path` — the same remote path | — |
| `core/offload.py` `build_offload_manifest` (`offload.py:585-586`) | `str(source.path)` — the **source card** | `str(dest_root)` — the **committed destination subfolder** | `destination = str(dest_root)` (same as server_path) |

### The inconsistency

`root` means three different things depending on producer:
- manifest.py / transfer-local / offload: `root` = the **scanned/source** side.
- rclone_bridge: `root` = the **remote** side (because lsjson lists the remote).
- transfer-rclone: `root` is whatever lsjson set (remote), while its own
  `source_root`/`dest_root` carry the real local/remote pair.

`server_path` is closer to consistent ("the side that is the server/remote"), but:
- manifest.py: only set if a caller passes it; merge callers do, plain scans do not.
- transfer-local: `server_path` = the copy **destination** (a plain local dir, not
  a server at all).
- offload: `server_path` = the **committed destination** (also not a server — it is
  the local/NAS folder the card was offloaded to). Note offload sets
  `server_path == destination`, which is redundant.
- rclone_bridge: `server_path == root == remote_path` (redundant).

So a consumer that reads `server_path` cannot assume it is a *remote*; for local
transfer and offload it is just "the other end of the copy." And `root` cannot be
assumed to be the local side.

### Proposed convention (single coherent meaning)

Adopt these definitions, documented in the manifest envelope and applied by every
producer:

- `root` — **display label only**, always the path the manifest's `files[]` keys
  are relative to (the scanned tree). This already matches manifest.py and offload;
  it is the natural anchor for the file keys. It must NOT be read by any logic
  (the codebase already comments it "display label only" in three places and
  `comparison.py` does not read it).
- `server_path` — **the counterpart location this manifest was synced against**,
  regardless of whether that counterpart is a remote (Drive URL) or a local/NAS
  dir. Rename intent: think of it as `counterpart_path`. For a plain local scan
  with no counterpart, `""`.
- `destination` — keep as the literal output path of the operation where one
  exists (transfer dest, offload committed dir). Offload should stop duplicating it
  into `server_path`; instead set `server_path = ""` for offload (a card offload has
  no "server counterpart" — the destination IS the artifact) OR keep
  `server_path == destination` but document that offload treats the destination as
  its counterpart. Recommended: set offload `server_path = ""` and rely on
  `destination`, to stop overloading `server_path` with a non-server value.

### Concrete fixes this would touch (NOT done here)

1. `core/rclone_bridge.py:162-163` — stop setting `root = remote_path`. Keep
   `root` as the relative-key anchor (the listed remote root is the anchor here, so
   this one is arguably already correct, but document it). Leave `server_path =
   remote_path`. This producer is internally consistent; the doc just needs to say
   "for a pure remote listing, root and server_path coincide."
2. `core/transfer.py:187-189` (local) — `root = str(src)` is the scanned side: OK.
   `server_path = str(actual_dest)` overloads server_path with a local dest. Either
   accept the broadened "counterpart" definition above (no code change, doc only)
   or move the dest to `destination`/`dest_root` (already present) and set
   `server_path=""` for non-remote local transfers.
3. `core/transfer.py:306-313` (rclone) — `server_path` correctly points at the
   Drive URL side; OK under the broadened definition. `root` is inherited from
   lsjson; document that.
4. `core/offload.py:585-586` — drop the redundant `server_path = str(dest_root)`
   (set `""`) and rely on `destination`. One-line change, but it is a semantic
   change to a persisted field, so it belongs in a deliberate schema pass with a
   migration note, not a drive-by edit.

### Why proposal, not patch

`load_manifest`/`_migrate` and existing on-disk manifests (37+ files) read these
fields; `gui/merge_tab.py` and `transfer_tab.py` may display `server_path`.
Changing the meaning of a persisted field is a coordinated change across producers,
the migration backfill, the GUI, and the SCHEMA_INTEROP_SPEC envelope. The
lowest-risk path is to **adopt the broadened "counterpart" definition by
documentation only** (no field renames, no value changes) and, separately, do the
one offload redundancy cleanup (#4 above) in a dedicated schema PR with a test that
loads pre- and post-change manifests. Human decision required on whether to
broaden-by-doc (zero risk) or rename to `counterpart_path` (cleaner, but a real
migration).

---

## Item 5 — GoPro pattern vs fixture naming (note, not a bug)

State: **DOCUMENTED. Not a bug.**

Real GoPro filenames are `GH010001.MP4` (chapter `GH` + 2-digit chapter + 4-digit
clip, no underscore). The detector targets the real form:
`core/offload.py:51` `re.compile(r'^GH0\d{5}$', re.IGNORECASE)` matches
`GH0XXXXX` (e.g. `GH010001`). The brief's `GH0_00001.MP4` (with an underscore) is a
fixture typo that matches no GoPro pattern by design — and correctly is NOT picked
up by the generic `^[A-Z]{1,4}_\d{4,5}$` either, because `GH0` ends in a digit so
the `[A-Z]{1,4}_` prefix does not apply. No code change; do not re-report. If a test
fixture wants to exercise GoPro detection, name it `GH010001.MP4`, not
`GH0_00001.MP4`.

---

## Summary

| Item | Verdict | Action |
|---|---|---|
| COC overall verdict + per-file PASS/FAIL | already resolved | none |
| `.DS_Store` ingest | already resolved | none |
| Same-second COC filename collision | already resolved | none |
| Test logs leaking to prod dir | already resolved | none |
| 1. COC hash strength (sha256 vs xxhash) | resolved/stale | documented; no change recommended |
| 2. Subfolder collision warning | fixed now | core warning + GUI dialog + 6 tests |
| 3. On-disk schema-1.0 migration | fixed now | opt-in sweep utility + 11 tests |
| 4. `root` vs `server_path` vocabulary | design | proposal; human decision required |
| 5. GoPro pattern vs fixture | not a bug | documented |

Tests: 135 → 152 passed, 0 failed throughout. Branch `known-issues-followup`,
3 commits (item 2, item 3, this doc). Not pushed.

### Needs a human decision
- Item 4: broaden `server_path` meaning by documentation only (zero risk) vs
  rename to `counterpart_path` + migration. Plus the offload `server_path`
  redundancy cleanup (`offload.py:585-586`).
- Item 1: optional cosmetic — show full 64-char sha256 in the prose `.txt` COC log
  (currently 16-char prefix). Recommended to leave as-is.
