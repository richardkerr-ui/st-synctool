# ST SyncTool — Offload Manifest + Rename Contract Spec

> **Status:** Fully implemented. Schema 1.2, `counterpart_path`, `build_offload_manifest`, `checksums` dict, `modtime`, `renames[]`, the `offload` custody block (including `overall_result`, per-destination `verified_files`, and all 6 acceptance tests), and the `reason` field on merge preserve-rename entries (Part 2) are all done. **M13 (landed):** xxh128 is the sole file-integrity algorithm; the `checksum_context.algorithm` field and MHL mapping (Part 4) are updated accordingly.

Target: next implementation session. Goal is to make offload output consumable by Verify and Merge without a re-scan, and to give offload and merge one shared rename contract so a folder that crosses the offload to merge boundary does not trip the rename-divergence path.

Grounded in the current code, not invented:
- Merge/transfer manifest: `core/manifest.py` `generate_manifest` (v1.2).
- Offload in-memory manifest: `core/offload.py` `prehash_source` and `build_normalized_manifest`.
- Rename consumer: `core/comparison.py:96` reads top-level `renames[]` keyed `{from, to}` on relative posix paths.

Hash algorithm (M13, landed): **xxh128 is the sole file-integrity algorithm** for every path where local bytes exist — local transfers, offload prehash (`offload.py`, `algorithm: "xxh128"`), and merge copy-verify. **md5** is carried alongside xxh128 only on local-to-Drive transfers (rclone's transport-verification key) and is the *only* hash on Drive-to-Drive paths (no local bytes exist to compute xxh128). sha256 was removed entirely as a writer key. The `checksum_context.algorithm` field and Part 4's MHL mapping reflect this.

**Activity-log trust model (stated honestly):** the org activity log is **not tamper-evident and not append-only**. There is no record hashing or hash chaining. Its trust rests on the team plus Google Drive access controls plus Drive version history — not a technical guarantee. Drive files are mutable and rclone only guarantees copy-time correctness. Do not describe the log as tamper-evident anywhere.

> **Note (June 14, 2026):** the diff no longer depends on algorithm overlap. `three_way_diff` compares on the strongest **shared** algorithm and, when two sides share none (e.g. a Drive md5-only manifest against a local xxh128 scan after M13), reports `DiffState.INDETERMINATE` ("Unknown") rather than a false change. The indeterminate path is the honest fallback for the genuine no-shared-algorithm runtime case (Drive md5 vs local xxh128). No migration burden: beta ships clean with no sha256 field archives.

---

## Part 1 — Offload must persist a JSON manifest

### Where it lands

One manifest per source (the source is the ground truth, each source writes to its own `{dest}/{label}/` subfolder at every destination). For each source, write:

1. `{dest}/{source_label}/st_manifest.json` at every destination, so each destination is self-describing and independently verifiable. This mirrors how merge/transfer drop `st_manifest.json` into the folder.
2. One archive copy at `~/Documents/STSyncTool/manifests/{project_id}/st_manifest_{label}_offload_{ts}.json`, reusing `save_manifest`'s naming and per-project subdir convention.

### Keying decision (the part that prevents divergence)

Persist the **normalized** manifest, the one already built at `offload.py:662` (`norm_mfst`). Its keys are the normalized paths, which match what is actually on disk at the destination. A later merge that scans the destination against this manifest sees the files exactly as they are and reports UNCHANGED. No rename collapse is even needed in the common case.

The `renames[]` contract in Part 2 is the defense for the uncommon case: a merge run against a base manifest that still holds the original card names (for example a manifest generated off the source card). Then the diff needs the rename map to collapse correctly.

### Envelope

Reuse the merge/transfer envelope verbatim so consumers do not branch on producer. Dispatch, where needed, on the existing `operation` field (`"offload"` vs `"merge"` vs `"transfer"`). Do not add a new discriminator.

```json
{
  "schema_version": "1.2",
  "created_at": "<ISO8601 UTC>",
  "label": "<source.label>",
  "root": "<str(source.path)>",
  "destination": "<final committed path at this destination>",
  "counterpart_path": "",
  "operation": "offload",
  "project_id": "<see note>",
  "workstation": "<hostname>",
  "user": "<username>",
  "file_count": 0,
  "total_size_bytes": 0,
  "checksum_context": { "algorithm": "xxh128", "gdrive_mode": false },
  "renames": [],
  "filename_normalization": { "applied": false },
  "files": { },
  "offload": { }
}
```

Notes:
- `project_id`: reuse `manifest._project_id(local_path, counterpart_path)`. For offload pass `(str(source.path), str(destination))` so the archive subdir is stable per source-to-dest pairing.
- `destination` is the committed `{dest}/{source_label}` path for the destination this copy of the manifest lives in. The archive copy may set it to the primary destination.

### File entry shape (must match merge entries)

Current offload in-memory entry is lean: `{size, checksum, algorithm}`. Do not change the in-memory shape (internal code reads `info["checksum"]` and `info["size"]` at `offload.py:242`, in verify and in the COC writer). Instead add a serializer that transforms lean to canonical at persist time:

```json
"DCIM/IMG_0001_a3f9b2c1.MOV": {
  "type": "file",
  "size": 12345,
  "modtime": "<ISO8601 UTC>",
  "checksums": { "xxh128": "<full 32-char>" },
  "hash_algorithm": "xxh128",
  "original_filename": "IMG_0001.MOV",
  "filename_hash_suffix": "a3f9b2c1",
  "hash_method": "xxh128_prefix8"
}
```

- `checksums` is a dict, not the bare `checksum` string. Carries the **full** 32-char xxh128, never truncated. The 16-char truncation is presentation only and stays confined to the COC text log (`offload.py:555`).
- `original_filename`, `filename_hash_suffix`, `hash_method` appear only on entries that were normalized. `build_normalized_manifest` already sets these (`offload.py:301-303`); the serializer just carries them through.
- `modtime` is new for offload. Add it in `prehash_source` from `f.stat().st_mtime` as ISO8601 UTC, the same encoding `manifest.py:68` uses. The copy step must preserve mtime (use `shutil.copy2`, verify the current copy call does) so a later `generate_manifest_fast` can reuse hashes via the modtime+size fast path.

### Path key encoding

`prehash_source` keys on `str(f.relative_to(source.path))` (`offload.py:383`). Merge keys on `.as_posix()` (`manifest.py:63`). Standardize offload on `.as_posix()` so keys match cross-platform and string-compare cleanly.

### offload block (offload-specific, machine-readable)

This is what makes the COC log consumable. Replace reliance on the prose `.txt` for programmatic state.

```json
"offload": {
  "overall_result": "COMPLETE",
  "destinations": [
    {
      "label": "Backup A",
      "final_path": "/Volumes/Backup A/A001",
      "primary": true,
      "files_verified": 10,
      "bytes_verified": 123456,
      "result": "COMPLETE",
      "verified_files": {
        "DCIM/IMG_0001_a3f9b2c1.MOV": { "verified": true, "xxh128": "<full>" }
      },
      "errors": []
    }
  ]
}
```

- `overall_result`: `"COMPLETE"` if every destination for this source verified, else `"PARTIAL_FAILURE"`. This is the verdict the Phase 6 audit found missing.
- `verified_files`: per-file post-copy verification result, the destination re-hash compared against source ground truth. The audit flagged that this exists only as cell state today. Surface it here.
- The prose `.txt` COC log stays as a human artifact. The JSON manifest is the machine record.

### New code

- `core/offload.py`: `build_offload_manifest(source, norm_mfst, norm_block, renames_full, cell_results_for_source, ts) -> dict`, producing the envelope above.
- Persist inside `run_offload` after each source's destinations finish, writing to each committed `{dest}/{label}/st_manifest.json` plus one archive copy via `save_manifest` (or a thin offload wrapper).
- Reuse `manifest.SCHEMA_VERSION`, `manifest._project_id`.

---

## Part 2 — One rename contract for both subsystems

### The canonical machine contract

Top-level `renames[]`, the shape `comparison.py:96` already reads, extended with an optional `reason`:

```json
"renames": [
  { "from": "DCIM/IMG_0001.MOV", "to": "DCIM/IMG_0001_a3f9b2c1.MOV", "reason": "normalize" },
  { "from": "edit.prproj",       "to": "edit_2026-06-10-rk.prproj",   "reason": "preserve" }
]
```

Rules:
- `from` and `to` are full relative posix paths, not basenames.
- `from` is the path in the prior/base state, `to` is the path on disk now.
- `reason` is `"normalize"` (offload) or `"preserve"` (merge apply). The diff ignores `reason`; it is for logging and UI only. Adding it is non-breaking because `comparison.py:96` only reads `from` and `to`.

Both subsystems write this same top-level `renames[]`. `comparison.py` collapses any `to` path that appears as LOCAL_ONLY, SERVER_ONLY, DELETED_LOCAL, or DELETED_SERVER into RENAMED and suppresses the matching `from`.

**Duplicate-target guard (June 14, 2026):** producers must keep each `to` unique within a single `renames[]` list. If two entries claim the same `to` (e.g. a merge same-day collision before `preserve_rename` started incrementing), `three_way_diff` no longer collapses either one. A `{to: from}` map would silently keep only the last entry and the dropped original would surface as a phantom `DELETED_LOCAL`. Instead the diff flags every path involved in the collision (both `from`s and the shared `to`) as `BOTH_CHANGED` for the user to resolve.

### What changes in offload

`build_normalized_manifest` (`offload.py:280-317`) currently emits renames as **basenames** under `filename_normalization.renames` with keys `{original, normalized}` (`offload.py:304-307`). That block stays as human-readable provenance, unchanged. Additionally emit the canonical top-level list using the full rel paths it already has in scope (`rel` and `normalized` at `offload.py:298`):

```python
renames_full.append({"from": rel, "to": normalized, "reason": "normalize"})
```

Return `renames_full` alongside `norm_block`, and write it to the manifest's top-level `renames[]`.

### What changes in merge

When `preserve_rename` fires during apply, write the same top-level shape with `reason: "preserve"` and full rel paths. Confirm the current preserve-rename path already populates top-level `renames[]` in `{from, to}` form (the Phase 3 rename-collapse fix relies on it). If so, only add `reason`. If it writes basenames or a different block, align it to this contract.

### Why this is the right cut

There are two different rename events (normalize at offload, preserve at merge) but the diff needs the identical outcome from both: the new path is expected, the old path is gone, collapse to RENAMED. One top-level list keyed on full rel paths satisfies both. `filename_normalization` and any preserve-specific metadata remain as provenance blocks that the diff does not read.

---

## Acceptance tests

Write these against real fixtures, in `tests/` (these are durable, unlike the overnight temp tests):

1. **Offload manifest is loadable and v1.2.** Run an offload with normalization. Load the persisted `{dest}/{label}/st_manifest.json` via `manifest.load_manifest`. Assert `schema_version == "1.2"`, `operation == "offload"`, every file entry has `checksums.xxh128` (full 32 char), `hash_algorithm`, `size`, `modtime`.

2. **Verify consumes an offload manifest.** Point the Verify flow at the committed destination using the offload-produced manifest. Assert all files report OK, no MISSING, no MISMATCH.

3. **Same-name merge base is clean.** Use the persisted (normalized-key) offload manifest as a merge base, scan the same destination. Assert every file is UNCHANGED, no RENAMED, no LOCAL_ONLY, no SERVER_ONLY.

4. **Original-name merge base collapses via renames[].** Build a base manifest keyed on the original card names (pre-normalization). Run `three_way_diff` against the normalized destination. Assert the normalized paths come back RENAMED, not LOCAL_ONLY, and the original paths are suppressed, not DELETED. This is the cross-boundary case the contract exists for.

5. **overall_result and per-file verification present.** Force one destination to fail. Assert `offload.overall_result == "PARTIAL_FAILURE"`, the failing destination's `result` reflects it, and `verified_files` carries a per-file boolean for the passing destination.

6. **Full hash in manifest, truncation only in log.** Assert the persisted manifest carries 32-char xxh128 while the `.txt` COC log still shows 16-char (log-only truncation is intended).

---

## Part 3 — Persisted format-verification results (M5.4)

The Verify tab runs format-aware media checks (BRAW/R3D/audio structural checks)
alongside hash verification, but their outcome used to vanish when the window
closed. M5.4 persists it in two places. Both are produced by `core/verify.py`
and round-trip through `json`.

### media_verify block on a manifest file entry

Where a manifest is present on disk, `persist_media_verify_to_manifest()` writes
a `media_verify` block onto each file entry that actually ran a format check:

```json
"DCIM/A001_C001.braw": {
  "type": "file",
  "size": 12345,
  "checksums": { "xxh128": "<full 32-char>" },
  "hash_algorithm": "xxh128",
  "media_verify": {
    "status": "OK",                       // OK | ADVISORY | FAILED
    "detail": "BRAW structure OK (gen 5)",
    "verified_at": "<ISO8601 UTC>"
  }
}
```

- The block appears **only** on entries whose result carried `format_status`
  (i.e. media files the checker recognised). Non-media entries are left
  untouched, so adding the block is non-breaking and sparse.
- `status` mirrors the in-memory `format_status`; `detail` mirrors
  `format_detail`. `verified_at` is the persist time, ISO8601 UTC.
- Readers that don't know the field ignore it (additive, like `offload`).

### Standalone verify report

`write_verify_report()` persists one report per run to
`~/Documents/STSyncTool/logs/verify_report_{label_}{ts}.json`:

```json
{
  "schema": "verify_report",
  "schema_version": 1,
  "generated_at": "<ISO8601 UTC>",
  "folder": "/Volumes/Archive/A001",
  "label": "A001",
  "deep": false,
  "summary": { "total": 312, "ok": 310, "missing": 0, "mismatch": 0, "format_fail": 2 },
  "verdict": "FAIL",
  "files": [
    { "path": "DCIM/A001_C001.braw", "status": "OK", "detail": "xxh128: ...",
      "format_status": "OK", "format_detail": "BRAW structure OK (gen 5)" }
  ]
}
```

- `files[]` carries each per-file result verbatim, including `format_status` /
  `format_detail`, so the media-verify evidence is the record, not transient
  cell state.
- `verdict` is `OK` when nothing is missing/mismatched/format-failed, else `FAIL`
  (same rule as the batch `ProjectVerifySummary`).

---

## Part 4 — ASC MHL v2.0 export (M10.3)

ST SyncTool can export an ASC Media Hash List sidecar (`.mhl`) next to `st_manifest.json` so post houses verify deliveries with their own tools (Silverstack, YoYotta and similar) without trusting our app. It is a translation of existing manifest hash data, no rehashing. Code: `core/asc_mhl.py`.

- **Format:** ASC MHL v2.0, namespace `urn:ASC:MHL:v2.0`, single `<hashlist version="2.0">` with required `<creatorinfo>` (creationdate, hostname, tool[@version]), `<processinfo>` (`<process>transfer</process>`) and a `<hashes>` block of `<hash>` entries. Validated against the published schema `xsd/ASCMHL.xsd` (github.com/ascmitc/mhl), bundled at `tests/fixtures/ASCMHL.xsd`.
- **Per file:** `<path size=".." lastmodificationdate="..">rel/posix/path</path>` plus hash elements in schema order (c4, md5, sha1, xxh128, xxh3, xxh64), each carrying `action="original"` and `hashdate`.
- **Hash mapping:** manifest `md5` to `<md5>`, manifest `xxh128` to `<xxh128>`. The ASC MHL v2.0 schema defines no sha256 element, and M13 removed sha256 as a writer key anyway. Every local manifest carries xxh128 and every Drive manifest carries md5 (with xxh128 where local bytes were available), so each file gets a verifiable hash element. A foreign manifest entry whose only hash has no MHL element is written with its path but no hash element and reported in `MhlExportResult.unhashed`. The old `xxhash3_64` → `<xxh3>` mapping was removed in M13 (the key is no longer written).
- **Trigger:** off by default. An "Export ASC MHL (.mhl)" checkbox in the Transfer and Offload tabs sets `export_mhl`, threaded through `route_transfer`/`transfer_folder`/`transfer_folder_rclone` and `OffloadConfig`/`save_offload_manifest`. A `.mhl` (named from the manifest label) is written next to each saved manifest; an export failure is logged and swallowed so it never affects the copy.

## Out of scope for this change, tracked separately

- `root` vs `counterpart_path` path vocabulary across producers (renamed from `server_path` in schema 1.2; `_migrate()` backfills `counterpart_path` from `server_path` for schema versions below 1.2).

*Previously listed here, now resolved:*
- `.DS_Store` ingest asymmetry: fixed. Offload now skips it via `SKIP_FILENAMES` frozenset (`offload.py:43`); merge filters it via `comparison._is_ignored`. Both sides aligned.
- Same-second COC log filename collision: fixed. `write_chain_of_custody_log` appends a 4-char random hex suffix via `secrets.token_hex(2)` (`offload.py:905`).
