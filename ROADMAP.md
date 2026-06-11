# Roadmap

## Completed milestones

### Phase 1 — SHIPPED
- Setup wizard (`gui/setup_wizard.py`) replaces manual rclone setup.
- README documents brew install rclone, rclone config and the
  `ST_SYNC_RCLONE_REMOTE` override.
- Default exclude filter for `.DS_Store`, `Thumbs.db`, `desktop.ini`
  (see `core/offload.py` `SKIP_FILENAMES`).

### v1 carry-forward tests — CLOSED
- Drive-as-server Merge end-to-end validated.
- Verify drift detection confirmed (rename in Drive web UI triggers
  MISSING/MISMATCH).
- `.app` bundle confirmed working after refactor (`build_st_synctool.sh`).

## v2 work in progress

- Phase 2: Live file-level progress + ETA. Actively next.
- Phase 3: Conflict resolution UI for BOTH_CHANGED Merge rows. Planned.

## Testing roadmap

### Layer 1 — Manifest schema contract tests
Parametrized tests that run every writer against every reader to catch schema mismatches (e.g. `checksum` vs `checksums`, missing fields).

| Writers | Readers |
|---------|---------|
| `run_offload` | `three_way_diff` |
| `transfer_folder` | `VerifyWorker._verify_local` |
| `transfer_folder_rclone` | `write_chain_of_custody_log` |

Estimated effort: ~1 day. Highest signal-to-effort ratio.

### Layer 2 — Pipeline integration tests
End-to-end pytest fixtures that run the full chain with real files:

```
src/  →  [offload]  →  dst1/, dst2/  →  [verify]
local/ + server/   →  [three_way_diff]  →  [apply]  →  [verify]
```

Parametrize over the key axes:

| Axis | Values |
|------|--------|
| File state | new / modified / deleted / renamed / conflicting |
| Conflict handler | skip / rename / overwrite |
| Checksum algorithm | sha256 / md5 / xxhash |
| Manifest schema | prehash (offload) / schema-1.1 (merge) |
| Paranoid verify | on / off |
| Drive mode | local / gdrive (rclone-mocked) |

Estimated effort: ~2-3 days.

### Layer 3 — Property-based tests (hypothesis)
For `three_way_diff` rename collapse logic: generate random manifest triples and assert invariants hold (every renamed file appears exactly once in output, no duplicate output paths). Catches whole classes of sort-order and edge-case bugs automatically.

Estimated effort: ~half day for `three_way_diff`; more for `run_offload`.

---

## Offload manifest custody block (SCHEMA_INTEROP_SPEC.md)

The `offload` block specified in `SCHEMA_INTEROP_SPEC.md` is not yet implemented. The rest of that spec (schema 1.2, `counterpart_path`, `build_offload_manifest`, `modtime`, `checksums` dict, `renames[]`) is done. Outstanding:

- Add `"offload": { "overall_result", "destinations": [...], "verified_files": {...} }` block to the JSON manifest produced by `build_offload_manifest` — makes the custody record machine-readable without parsing the prose `.txt` log
- Add `"reason": "normalize"` to offload rename entries and `"reason": "preserve"` to merge preserve-rename entries
- Write the 6 acceptance tests defined in `SCHEMA_INTEROP_SPEC.md`

---

## v2 candidates (not yet committed)

- Drive to Drive transfers. Plumbing mostly exists. Real ask: moving a
  project between ST Drive folders without burning local disk space.
- Local NAS server merge speed. Full hash walk on first scan; pre-filter
  helps but still slow. Worth profiling.
