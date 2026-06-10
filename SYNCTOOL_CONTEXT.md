# ST SyncTool — Development Context & Roadmap

Use this file to orient Claude Code at the start of any session. It captures the full design analysis and implementation plan agreed on in Cowork.

## Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Manifest Schema Foundations (items 01–09) | ✅ Complete |
| 2 | Projects Registry (items 10–12) | ✅ Complete |
| 3 | Rename Tracking + Diff Logic (items 13–14) | ✅ Complete |
| 4 | Merge Tab UI (items 15–21) | ✅ Complete |
| 5 | Offload Tab (items 22–40) | ✅ Complete |
| 6 | Thumbnail Extraction + Contact Sheets (items 41–52) | ✅ Complete |
| 7 | Filename Normalisation (items 53–61) | ✅ Complete |
| 8 | R3D Support (items 62–68) | 🔲 Pending |

**Next session start:** Phase 8, item 62 — R3D support: REDline detection + `.RDC` clip unit handling (`core/offload.py`, `core/thumbnail.py`).

---

## Repo

https://github.com/richardkerr-ui/st-synctool

---

## What the app does

ST SyncTool is a PyQt6 desktop app for syncing and merging project folders between a local workstation and a server (local path or Google Drive via rclone). Current tabs:

- **Transfer tab** — copies a folder from source to destination with checksum verification
- **Merge tab** — three-way diff (base manifest vs local vs server) with per-file action selection (push / pull / delete / skip)
- **Verify tab** — standalone checksum verification

A new **Offload tab** is planned (see Phase 5+) for camera card and audio recorder ingest with multi-source, multi-destination, staging, verification, contact sheet generation, and chain-of-custody logging.

---

## Key files

| File | Purpose |
|------|---------|
| `core/manifest.py` | Generates and saves manifests |
| `core/comparison.py` | Three-way diff logic |
| `core/merge_ops.py` | Push / pull / delete per file, preserve-rename |
| `core/checksum.py` | SHA-256, xxhash3_64, MD5 hashing |
| `core/rclone_bridge.py` | rclone subprocess wrapper, `lsjson_to_manifest` |
| `core/transfer.py` | Folder transfer with manifest generation |
| `gui/merge_tab.py` | ScanWorker, ApplyWorker, MergeTab UI |
| `gui/transfer_tab.py` | TransferWorker, TransferTab UI |
| `utils/gdrive_utils.py` | GDrive URL parsing, rclone remote detection |

---

## Key constants and paths

```
MANIFEST_FILENAME   = "st_manifest.json"                    # written to local + server root
LOCAL_MANIFEST_DIR  = ~/Documents/STSyncTool/manifests/     # archive (to be per-project after Phase 1 #03)
APP_CONFIG          = ~/.config/st_synctool/config.json     # rclone remote config
PROJECTS_REGISTRY   = ~/Documents/STSyncTool/projects.json  # Phase 2 (new)
CONTACT_SHEETS_DIR  = ~/Documents/STSyncTool/contact_sheets/ # Phase 6 (new)
OFFLOAD_LOGS_DIR    = ~/Documents/STSyncTool/offload_logs/  # Phase 5 (new)
```

---

## Current manifest workflow (as-built)

1. **Base manifest** — `st_manifest.json` written to both local root and server root after each merge apply. Also archived to `~/Documents/STSyncTool/manifests/st_manifest_{foldername}_{ts}.json`.
2. **Scan** — `generate_manifest_fast` scans local, reusing checksums from base where modtime + size match. Server scanned via rclone `lsjson` (GDrive) or same fast scan (local path).
3. **Three-way diff** — compares base, yours, server by checksum. States: UNCHANGED, LOCAL_ONLY, SERVER_ONLY, LOCAL_CHANGED, SERVER_CHANGED, BOTH_CHANGED, DELETED_LOCAL, DELETED_SERVER, DELETED_BOTH.
4. **Apply** — optional pre-apply rescan to catch drift. Executes push / pull / delete actions. Regenerates manifest from new local state and pushes to server.
5. **Preserve-rename** — if "preserve on overwrite" is on and destination file exists, incoming file is renamed `{stem}_{YYYY-MM-DD-initials}{ext}` before writing.

---

## Known issues in current codebase

**Rename divergence (most critical):** When `preserve_rename` fires, the renamed file is not in the base manifest. On the next scan the original path appears as DELETED and the renamed path appears as LOCAL_ONLY or SERVER_ONLY — flagged as conflicts even though the rename was intentional. Compounds each merge cycle.

**Post-merge manifest is local-only:** After apply, manifest is generated from local state and pushed to server. If preserve-rename created files only on the server, the server's manifest is wrong.

**Server path not stored in manifests:** Merge manifests record `root` (local path) but not the server path or URL. Cannot audit a manifest and know what it synced with.

**`_local_copy_verify` discards hashes:** Computes pre/post SHA-256 for local copies but returns only `True/False`. Hash values are lost and never make it into the manifest.

**Checksum algorithm not recorded:** Manifest stores hash values but not which algorithm was chosen or why. `gdrive_mode` switches sha256→md5 silently. Paranoid mode has a silent fallback when Drive hasn't computed sha256 for a file.

**GDrive per-file links not captured:** rclone `lsjson` returns an `ID` field for Drive items but `lsjson_to_manifest` ignores it. Individual file links are never stored.

**Archive is a flat pile:** All manifests go to one directory with no project organisation. No way to find the latest manifest for a given project without guessing by folder name.

**No project memory:** App starts from scratch every session. Paths must be re-entered. No concept of "projects."

**Schema versioning unused:** `schema_version: "1.0"` is written but `load_manifest` never reads it — no migration path exists.

---

## Checksum algorithm selection (current behaviour)

| Scenario | Algorithm |
|----------|-----------|
| Local-to-local transfer | SHA-256 pre + post copy |
| Local-to-local in gdrive_mode | MD5 pre + post copy |
| rclone transfer, no paranoid | rclone `--checksum` internal (algorithm varies by remote) |
| rclone transfer, paranoid, local → Drive | SHA-256 local hash vs Drive's reported SHA-256 |
| rclone transfer, paranoid, Drive → local | Drive's reported SHA-256 vs SHA-256 local hash |
| Merge push/pull via rclone | rclone `copyto --checksum` (no hash values stored) |
| Merge push/pull local | SHA-256 pre + post (currently discarded — fixed in Phase 1 #08) |

GDrive does not always compute SHA-256 for all files. When it hasn't, paranoid mode silently falls back to "rclone-checksum" — this should be explicitly logged (Phase 1 #07).

---

## Implementation plan

### Phase 1 — Manifest Schema Foundations
*Pure additions or small refactors. Nothing breaks. Enables everything downstream.*

| # | Item | Files |
|---|------|-------|
| 01 | Schema versioning + `load_manifest` migration — bump to `"1.1"`, backfill missing fields for old manifests with safe defaults | `core/manifest.py` |
| 02 | Add `project_id` field — stable hash of (local_path + server_path) pair | `core/manifest.py` |
| 03 | Add `operation` label to archive filenames + per-project subdirectories in `~/STSyncTool/manifests/{project_id}/` | `core/manifest.py` |
| 04 | Store `server_path` / `server_url` in merge manifests (currently lost entirely) | `gui/merge_tab.py`, `core/manifest.py` |
| 05 | Store original GDrive URL (not rclone remote string) in rclone transfer manifests | `core/transfer.py` |
| 06 | Capture `ID` field from rclone `lsjson` → store `gdrive_url` per file in manifest | `core/rclone_bridge.py` |
| 07 | Add `checksum_context` block — algorithm used, verification method per file, chunk size, paranoid fallback warnings | `core/manifest.py`, `core/checksum.py`, `core/transfer.py` |
| 08 | `_local_copy_verify` returns hash dict instead of bool → stored in post-merge manifest | `core/merge_ops.py`, `gui/merge_tab.py` |
| 09 | Mark `root` field as display label only — remove from any logic or comparisons | `core/manifest.py`, `core/comparison.py` |

---

### Phase 2 — Projects Registry
*Backend only. Gives the app memory across sessions. Unlocks all Phase 4 UI features.*

| # | Item | Files |
|---|------|-------|
| 10 | `projects.json` CRUD — create, read, update, list. Keys: `project_id`, `display_name`, `local_path`, `server_path`, `last_merged_at`, `latest_manifest` | `core/projects.py` (new) |
| 11 | Auto-register project on first successful scan — write to registry silently | `gui/merge_tab.py` |
| 12 | Merge history log per project — append on each successful apply (date, files changed, conflicts, preserve-renames that occurred) | `core/projects.py`, `gui/merge_tab.py` |

---

### Phase 3 — Rename Tracking + Diff Logic
*Fixes the divergent branches problem. Isolated change to manifest schema and diff algorithm.*

| # | Item | Files |
|---|------|-------|
| 13 | Track renames in manifest `renames[]` list when `preserve_rename` fires during apply | `core/merge_ops.py`, `gui/merge_tab.py` |
| 14 | Teach `three_way_diff` to check `renames[]` — collapse old + new path into `RENAMED` state instead of flagging as DELETED + LOCAL_ONLY | `core/comparison.py` |

---

### Phase 4 — Merge Tab UI
*Builds on Phases 1–3. Straightforward once the data exists.*

| # | Item | Files |
|---|------|-------|
| 15 | Quick Project Loader dropdown — auto-fills all 3 path inputs from registry in one click | `gui/merge_tab.py` |
| 16 | Manifest browser dialog — scoped to current project, sorted newest-first, one-click select; replaces generic file browser on base manifest input | `gui/merge_tab.py` (new dialog) |
| 17 | Auto-detect project on local path entry — match against registry, offer to fill remaining paths | `gui/merge_tab.py` |
| 18 | Stale manifest badge on base manifest input — "6 days old" in yellow, "14+ days" in red | `gui/merge_tab.py` |
| 19 | Merge history log panel — readable list of past sessions per project | `gui/merge_tab.py` or new tab |
| 20 | Server manifest health check button — quick compare of server `st_manifest.json` vs local without full rescan | `gui/merge_tab.py`, `core/manifest.py` |
| 21 | Right-click "Open in Drive" in diff table and transfer log — uses `gdrive_url` from Phase 1 #06 | `gui/diff_table.py`, `gui/transfer_tab.py` |

---

### Phase 5 — Offload Tab
*New tab for camera card and audio recorder ingest. Separate from Transfer tab.*

**Design principles:**
- Source is always read-only. Never write to or modify the source.
- Mirror mode and all delete actions are completely absent.
- Checksum is non-negotiable — pre-hash all sources before copying, re-hash all destinations after copying, compare against source ground-truth manifest.
- Staging: files are written to a temp folder first, verified, then committed atomically. The final destination either exists completely or not at all.
- Per-file retries (3 attempts default, configurable) with exponential backoff. Distinguish retryable errors (IO timeout, connection reset) from non-retryable ones (disk full, permission denied).
- Sequential execution: source 1 → all destinations, then source 2, etc.
- Sources and destinations are both dynamic lists — minimum one row each, supports unlimited entries.

| # | Item | Files |
|---|------|-------|
| 22 | New Offload tab — separate from Transfer tab | `gui/offload_tab.py` (new) |
| 23 | Source list widget — label + path input + enable toggle + status indicator per row, "+" / remove controls | `gui/offload_tab.py` |
| 24 | Source label used as subfolder name at each destination to prevent collisions — `{dest}/{source_label}/{files}` | `core/offload.py` (new) |
| 25 | Configurable subfolder name per source — defaults to source label, user can override | `gui/offload_tab.py` |
| 26 | Destination list widget — same pattern as source list | `gui/offload_tab.py` |
| 27 | Destination preset system — save / load named destination sets, stored in projects registry | `core/projects.py`, `gui/offload_tab.py` |
| 28 | Source read-only enforcement — lock indicator on source inputs, preflight check that never allows writes to source | `core/offload.py` |
| 29 | Mirror mode and all delete actions completely absent from offload tab | `gui/offload_tab.py` |
| 30 | Pre-hash phase — hash all files on each source sequentially before any copying begins. One ground-truth manifest per source. | `core/offload.py` |
| 31 | Staging copy — write to `{dest}/{source_label}/.st_staging_{ts}/` per source/destination pair, not final path | `core/offload.py` |
| 32 | Per-file retries — 3 attempts default (configurable), retryable vs non-retryable error distinction, exponential backoff | `core/offload.py` |
| 33 | Destination verification — re-hash all destination files after copy, compare against source ground-truth manifest (not just in-flight hash) | `core/offload.py` |
| 34 | Staging commit — rename staging to `{dest}/{source_label}/` only on full verification pass. On failure leave staging in place with failure report written alongside. | `core/offload.py` |
| 35 | M×N status matrix — sources as rows, destinations as columns, each cell shows live state (pending / copying / verifying / done / failed) | `gui/offload_tab.py` |
| 36 | Execution order — sequential by source (source 1 → all destinations, then source 2, etc.) | `core/offload.py` |
| 37 | Independent destination handling — failure on one destination does not abort others. "Continue on failure" vs "Stop on first failure" option. | `core/offload.py`, `gui/offload_tab.py` |
| 38 | Per-source eject confirmation — source flagged individually safe to eject as soon as all its destinations verify. No need to wait for other sources. | `gui/offload_tab.py` |
| 39 | Post-offload summary dialog — full M×N result matrix with per-cell checksum status and safe-to-eject indicators | `gui/offload_tab.py` |
| 40 | Chain-of-custody text log — source manifests + per-destination verification results per source, saved to `~/Documents/STSyncTool/offload_logs/` | `core/offload.py` |

---

### Phase 6 — Thumbnail Extraction + Contact Sheets
*Runs after each source commits to its primary destination. Non-blocking background thread.*

**Dependencies:** ffmpeg + ffprobe (check at startup, same pattern as rclone). If not found, thumbnail feature is disabled with tooltip: "Requires ffmpeg — install with `brew install ffmpeg`."

**When to run:** After staging commits and verifies against the primary (first) destination. Runs on destination files — never the source card.

**Thumbnail count (adaptive, user sets maximum of 1–4):**
- Under 5 seconds → 1 frame (midpoint)
- 5–30 seconds → 2 frames
- 30 seconds – 2 minutes → 3 frames
- Over 2 minutes → 4 frames

**Frame positions:** 15%, 38%, 62%, 85% of runtime (avoids head/tail black frames).

**Metadata to bake into each tile (from ffprobe):** filename, camera make and model, codec and profile, resolution, frame rate, bit depth, duration, timecode at frame, file size, date recorded.

**Contact sheet layout:** One row per clip. Thumbnail strip on the left (1–4 frames). Metadata column on the right. Header row: source label, offload date, total clips, total runtime, total size.

**Output:** PDF primary (vector text, page-based) + optional JPEG. Saved to `{primary_dest}/{source_label}/_contact_sheet_{ts}.pdf` and `~/Documents/STSyncTool/contact_sheets/`.

| # | Item | Files |
|---|------|-------|
| 41 | ffmpeg / ffprobe dependency check — grayed-out thumbnail option if absent, install hint in tooltip | `core/offload.py`, `gui/offload_tab.py` |
| 42 | Video file detection — filter clips from card by extension (.mxf, .mov, .mp4, .mts, .ari, .crm, etc.), separate audio files | `core/offload.py` |
| 43 | ffprobe metadata extraction per clip — codec, resolution, frame rate, bit depth, duration, timecode, camera make/model from container tags | `core/thumbnail.py` (new) |
| 44 | Adaptive frame count by clip duration, user-set maximum (1–4) | `core/thumbnail.py` |
| 45 | Frame extraction via ffmpeg — positions at 15/38/62/85% of runtime | `core/thumbnail.py` |
| 46 | Tile compositor — thumbnail image + metadata strip baked in using Pillow | `core/thumbnail.py` |
| 47 | BRAW handling — metadata-only tile, parse Blackmagic sidecar XML where present, "BRAW thumbnail preview not yet supported" placeholder | `core/thumbnail.py` |
| 48 | Audio file tile — metadata-only card (filename, format, duration, sample rate, bit depth, channels), no thumbnail | `core/thumbnail.py` |
| 49 | Contact sheet layout compositor — one row per clip, header row with source summary | `core/thumbnail.py` |
| 50 | PDF output (primary) + JPEG option | `core/thumbnail.py` |
| 51 | Output to `{primary_dest}/{source_label}/_contact_sheet_{ts}.pdf` + `~/Documents/STSyncTool/contact_sheets/` | `core/thumbnail.py` |
| 52 | Background thread thumbnail generation — progress label "Generating thumbnails — clip N of M", non-blocking | `gui/offload_tab.py` |
| 52a | Per-file `thumbnails` block in offload manifest — links each clip to its generated frame paths and contact sheet filename | `core/offload.py`, `core/thumbnail.py` |
| 52b | Top-level `generated_artifacts` block in offload manifest — lists contact sheets with their own SHA-256 checksums and source clip list | `core/manifest.py`, `core/thumbnail.py` |
| 52c | Pattern-based exclusions in `comparison.py` `_is_ignored` — `_contact_sheet_*`, `_thumbnails/`, `.st_staging_*`, `.st_offload_*` excluded from all diff logic | `core/comparison.py` |
| 52d | Thumbnails generated from primary destination only — explicitly recorded in offload manifest; secondary destinations get footage only by design | `core/offload.py` |
| 52e | Thumbnail failure is non-fatal — logs warning in offload summary, sets `"generated": false` + `"error"` field in manifest `thumbnails` block, footage result unaffected | `core/thumbnail.py`, `core/offload.py` |

**Manifest shape for thumbnail tracking:**

Per-file `thumbnails` block:
```json
"IMG_1205_a3f9b2c1.mov": {
  "size": ...,
  "checksums": {...},
  "thumbnails": {
    "generated": true,
    "frames": ["_thumbnails/IMG_1205_a3f9b2c1_f1.jpg", "_thumbnails/IMG_1205_a3f9b2c1_f2.jpg"],
    "contact_sheet": "_contact_sheet_20260609_143201.pdf"
  }
}
```

Top-level `generated_artifacts` block:
```json
"generated_artifacts": {
  "_contact_sheet_20260609_143201.pdf": {
    "type": "contact_sheet",
    "generated_by": "st_synctool",
    "source_clips": ["IMG_1205_a3f9b2c1.mov", "IMG_1206_a3f9b2c1.mov"],
    "checksums": {"sha256": "..."}
  }
}
```

---

### Phase 7 — Filename Normalisation
*Runs during the staging phase, before files are written. Source card is never modified.*

**Problem:** Consumer and prosumer cameras use sequential naming schemes with no date or camera identifier — `IMG_XXXX`, `MVI_XXXX`, `GH0XXXXX`, `DJI_XXXX`, `CM1_XXXX`, `CM2_XXXX`, `CLIP_XXXX`, `VIDEO_XXXX`. Files from different cards share identical names, causing false relinking in NLEs (Premiere, DaVinci Resolve) when files from different shoots end up in the same project.

**Hash method:** First 8 characters of the SHA-256 already computed during pre-hash phase. No extra I/O. Deterministic — same file always gets same suffix. `IMG_1205.mov` → `IMG_1205_a3f9b2c1.mov`.

**Detection:** Scan filenames before offload. Flag if ≥60% of video files match pattern: short prefix (1–4 chars) + 4–5 digit sequential number + video extension, with no date component. Also trigger unconditionally if two or more sources share overlapping filenames — unambiguous collision.

**Sidecar files:** Detect sidecars by base name match (.srt, .thm, .xml, .lut) and apply same hash suffix. Prevents sidecar orphaning after rename.

**Manifest tracking:**

Per-file entry:
```json
"IMG_1205_a3f9b2c1.mov": {
  "original_filename": "IMG_1205.mov",
  "filename_hash_suffix": "a3f9b2c1",
  "hash_method": "sha256_prefix8",
  "size": ...,
  "checksums": {...}
}
```

Top-level manifest block:
```json
"filename_normalization": {
  "applied": true,
  "method": "sha256_prefix8",
  "detected_pattern": "IMG_XXXX",
  "renames": [
    {"original": "IMG_1205.mov", "normalized": "IMG_1205_a3f9b2c1.mov"},
    {"original": "IMG_1205.SRT", "normalized": "IMG_1205_a3f9b2c1.SRT"}
  ]
}
```

| # | Item | Files |
|---|------|-------|
| 53 | Pre-offload filename pattern scan — detect sequential generic schemes with ≥60% threshold | `core/offload.py` |
| 54 | Cross-source duplicate detection — unconditional prompt if two sources share overlapping filenames | `core/offload.py` |
| 55 | User prompt with concrete example transformation using actual files from the card | `gui/offload_tab.py` |
| 56 | Per-pattern preference memory — remember choice per naming scheme, stored in preferences | `core/projects.py` |
| 57 | Hash suffix derived from SHA-256 precomputed in pre-hash phase — no extra I/O | `core/offload.py` |
| 58 | Rename applied at destination in staging phase — source card never touched | `core/offload.py` |
| 59 | Sidecar detection and co-rename — match by base name, carry same hash suffix | `core/offload.py` |
| 60 | Manifest `filename_normalization` block + `original_filename` per file entry | `core/manifest.py` |
| 61 | Contact sheet shows both normalised filename (primary) and original card filename (secondary) | `core/thumbnail.py` |

---

### Phase 8 — R3D Support (RED Camera)
*Builds on Phase 6 thumbnail infrastructure. Requires REDCINE-X PRO (free) for frame extraction.*

**R3D file structure — important:** R3D footage is organised in a folder hierarchy. The `.RDC` folder is the logical clip unit — not individual `.R3D` segment files. A long clip may have multiple `.R3D` segments (split at ~4GB). Offload and thumbnail logic must treat the `.RDC` folder as one clip.

```
REEL001.RDM/
  A001_C001_0101AB.RDC/         ← treat this as the clip
    A001_C001_0101AB_001.R3D    ← segment 1
    A001_C001_0101AB_001.RMD    ← metadata sidecar (XML)
    A001_C001_0101AB_002.R3D    ← segment 2 (clips > ~4GB)
    A001_C001_0101AB_002.RMD
```

**REDline CLI (ships with REDCINE-X PRO — free download from red.com):**
```bash
# Frame extraction
/Applications/REDCINE-X PRO.app/Contents/MacOS/REDline \
  --i A001_C001_0101AB_001.R3D \
  --outDir /output/ \
  --frameNum 120 \
  --exportPreset JPG

# Metadata dump
REDline --i A001_C001_0101AB_001.R3D --printMeta
```

**RMD sidecar (pure Python XML — no SDK needed):** Frame count, fps, resolution (WEAPON 8K / MONSTRO 8K VV / HELIUM 8K S35 / GEMINI 5K / V-RAPTOR etc.), ISO, white balance, aperture, focal length, timecode start, camera model and serial, REDCODE compression ratio, color science version.

**R3D naming is already unique** — includes date component and camera identifier (`A001_C001_210601_ABCD.R3D`). Exclude from generic filename pattern detection in Phase 7.

| # | Item | Files |
|---|------|-------|
| 62 | REDline detection — check `/Applications/REDCINE-X PRO.app/Contents/MacOS/REDline` and PATH; surface install prompt if absent | `core/thumbnail.py` |
| 63 | R3D frame extraction via REDline — export JPEG frames at calculated positions; multi-segment handled transparently by REDline | `core/thumbnail.py` |
| 64 | RMD sidecar parser — pure Python XML; extracts frame count, fps, resolution, ISO, WB, aperture, focal length, camera model + serial, timecode, REDCODE ratio | `core/thumbnail.py` |
| 65 | R3D clip detection — treat `.RDC` folder as clip unit; one contact sheet row per `.RDC`, not per `.R3D` segment | `core/offload.py`, `core/thumbnail.py` |
| 66 | R3D filenames excluded from generic naming pattern detection (already contain date + camera identifier) | `core/offload.py` |
| 67 | R3D metadata-only tile fallback when REDline absent — rich metadata from RMD, "Install REDCINE-X PRO (free) for R3D previews" message in tile | `core/thumbnail.py` |
| 68 | BRAW — metadata-only tile for now; parse Blackmagic sidecar XML where present; noted as future addition pending a clean CLI path | `core/thumbnail.py` |

---

## Where to start

**Phase 1, item 01** — `core/manifest.py`:
- Add `SCHEMA_VERSION = "1.1"` constant
- Update `generate_manifest` and `generate_manifest_fast` to write `schema_version: "1.1"`
- Update `load_manifest` to check version and backfill missing fields (`renames`, `checksum_context`, `server_path`, `gdrive_url` per file, `operation`, `filename_normalization`) with safe defaults for v1.0 files

Work through Phase 1 items 01–09 as a batch before touching any UI — they are all low-risk schema additions that set the foundation for everything above.

---

## External dependencies

| Tool | Used for | Install | Check |
|------|----------|---------|-------|
| rclone | GDrive transfers and lsjson | `brew install rclone` | `shutil.which("rclone")` |
| ffmpeg | Video frame extraction (Phase 6) | `brew install ffmpeg` | `shutil.which("ffmpeg")` |
| ffprobe | Video metadata extraction (Phase 6) | ships with ffmpeg | `shutil.which("ffprobe")` |
| REDline | R3D frame extraction (Phase 8) | Install REDCINE-X PRO (free, red.com) | check app bundle path |
| Pillow | Tile and contact sheet compositor (Phase 6) | `pip install Pillow` | `import PIL` |
