# Changelog

## Post-ship hardening — June 10–11, 2026

### Manifest schema (schema 1.2)
- Bumped `SCHEMA_VERSION` to 1.2; `load_manifest` migrates 1.0 and 1.1 files on read
- Renamed `server_path` to `counterpart_path` throughout — clearer semantics, backfilled for older manifests
- Transfer manifest entries now keyed by relative POSIX path (was basename — caused subdirectory files to collapse and break Verify/Merge)
- Added `hash_algorithm`, `modtime`, `checksums` dict, `checksum_context`, and `gdrive_url` per entry
- `build_offload_manifest` added to `core/offload.py` — persists a schema 1.2 manifest to `{dest}/{label}/st_manifest.json` and the archive after each offload commit
- On-disk migration utility added (`migrate_manifests_on_disk`, opt-in, dry-run by default)

### Chain-of-custody log
- `OVERALL RESULT: COMPLETE` / `OVERALL RESULT: PARTIAL_FAILURE` verdict line added near top of every log
- Per-file `VERIFY: PASS` / `VERIFY: FAIL` lines added (were only inferable from cell state before)
- Collision-proof filename: `offload_<YYYYmmdd>_<HHMMSS>_<4hex>.txt`
- Fixed `KeyError` crash when normalisation or thumbnails added non-file meta keys to the manifest — log writer now filters to real file entries

### Offload pipeline
- Added `SKIP_FILENAMES` frozenset (`.DS_Store`, `Thumbs.db`, `desktop.ini`) — OS junk filtered before pre-hash, copy, verify, manifest, and log
- Subfolder collision detection: warns (non-blocking) when two sources resolve to the same effective subfolder; GUI shows a confirm dialog before the run
- Sidecar→hash-suffix binding made deterministic (lexicographically first clip wins when two clips share a stem)

### Merge / comparison
- `ApplyWorker.run`: `op_result.get("renamed_to", rel_path)` replaced with `... or rel_path` — `None` value no longer drops post-copy checksums from the regenerated manifest
- `three_way_diff` rename collapse now covers `DELETED_SERVER` and `DELETED_LOCAL` target states; two-pass sort-order edge case fixed

### Transfer
- Disk-usage warning in `pre_flight_checks` now uses `shutil.disk_usage` (was summing files in subfolder, not actual used space)

### Verify tab
- `VerifyWorker._verify_local` now calls `_media_verify.verify_file` after each hash check; result dicts carry `format_status` / `format_detail`; hash-OK files that fail format check surface as `FORMAT_FAIL`

### Test suite
- Grew from 122 → 459 tests across this period
- New suites: `test_transfer_rclone.py` (29), `test_apply_worker.py` (22), `test_transfer_folder.py` (24), `test_comparison.py` additions (15), `test_gui_smoke.py` (21)
- `pytest-qt` added as dev dependency for GUI smoke tests

---

## v1 + Phase 1 of v2 — June 8, 2026

This repo was initialized after v1 was complete and Phase 1 of v2 was
shipped. Both are captured here.

### Phase 1 of v2: Setup wizard + OAuth

- core/setup_checks.py — system/rclone/auth checks with auto-fix actions
- core/oauth_config.py — ST OAuth credentials with env / file / default hierarchy
- gui/setup_wizard.py — 4-page wizard (welcome, system, drive, verify)
- gui/main_window.py — wizard launcher + coral auth-health banner
- Uses sys.executable for pip installs to avoid python/pip interpreter mismatch
- Detects and migrates existing remotes off rclone's shared OAuth client
  (full ST API quota instead of throttled shared client)

### v1 bug fixes

1. Amphetamine — duration:0 was invalid syntax; corrected to indefinite
   sessions and fixed "end current session" -> "end session"
2. rclone wasn't installed — installed via Homebrew, configured gdrive
   remote with proper OAuth scope
3. Routing layer — added route_transfer() dispatcher so URLs go through
   rclone and local paths stay on the direct code path; killed the ghost
   https:/... directory
4. GUI safety — stopped wrapping URLs in Path(), disabled "Rename copy"
   for Drive transfers, added Mirror Mode checkbox with extra confirmation
5. Conflict handling — wired the dropdown to --ignore-existing /
   --update / default-overwrite in rclone
6. Manifest schema — fixed lowercase hash key lookup (md5/sha256 not
   MD5/SHA-256) and normalized the rclone manifest to match the local
   schema so logging works
7. JSON manifest save — added save path for rclone transfers

### v1 feature status

Transfer tab
- Local <-> Local with per-file SHA-256 verification
- Local <-> Drive via rclone with --checksum
- Mirror mode (rclone sync) with extra confirmation
- Streaming progress bar, working Cancel button
- Pre-flight size check both sides
- Paranoid verification mode (independent SHA-256)
- Auto-detect rclone remote, env-var override
- Accurate post-transfer audit log with status diff

Merge tab
- Three-way diff against baseline manifest
- Safe defaults (Skip for deletes and conflicts)
- Preserve-on-overwrite with date-initials backup naming
- Paranoid pre-apply re-scan that actually aborts on drift
- Modtime+size fast-scan pre-filter
- Post-apply manifest regeneration synced to both sides
- Internal files (st_manifest.json, .DS_Store) filtered from diffs

Verify tab
- Local folder verification (re-hash and compare)
- Drive folder verification via lsjson metadata (no downloads)
- Auto-load manifest for local, explicit for Drive
- Report extras present in Drive but not in manifest

### Known limitations

- Drive -> Drive transfers explicitly unsupported
- "Rename copy" silently falls back to "Overwrite" for any Drive transfer
- .DS_Store files get synced (default exclude filter is a v2 candidate)
