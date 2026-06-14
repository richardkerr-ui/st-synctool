# ST SyncTool

[![CI](https://github.com/richardkerr-ui/st-synctool/actions/workflows/ci.yml/badge.svg)](https://github.com/richardkerr-ui/st-synctool/actions/workflows/ci.yml)

A desktop sync tool for reconciling production files between local SSDs and Google Drive (or a NAS), built for Signal Theory's video production workflow.

Handles multi-GB video / audio / PSD / After Effects assets without re-uploading the world every time someone tweaks a comp.

## What it does

Five tabs:

- **Transfer** — push or pull a folder one-way, with integrity verification (optional ASC MHL sidecar for delivery)
- **Merge** — reconcile diverged copies using a three-way diff and apply only the changes
- **Offload** — ingest from camera cards and audio recorders with pre-hash verification, staging, contact sheet generation, chain-of-custody logging and a "safe to format" clearance
- **Verify** — confirm a folder still matches a saved manifest (with deep Drive verify and batch verify across every registered project)
- **History** — every offload, transfer, merge and verify across all machines in one filterable list, with org-health staleness flags

Header tools sit top-right: a guided **? Tour**, **Report a Problem** (bundle logs to email), and **Settings**. A one-page walkthrough lives in [docs/QUICKSTART.md](docs/QUICKSTART.md).

---

## Installation (macOS)

### One-command install (recommended)

Run this in Terminal — it handles Xcode CLT, Homebrew, Python, rclone, cloning the repo, and Google Drive auth in one shot:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/richardkerr-ui/st-synctool/main/install.sh)
```

After it completes, launch the app with:

```bash
~/Applications/st-synctool/run.sh
```

Re-running the command on a machine where the app is already installed does a `git pull` and skips any steps that are already complete.

---

### Manual install (fallback)

If you prefer to clone manually:

**Step 1** — Clone the repo. GitHub no longer accepts passwords for Git operations. If this is a private repo, create a [Personal Access Token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) with `repo` scope and use it in place of your password, or embed it in the URL: `https://<token>@github.com/...`

**Step 2** — From inside the cloned directory, run the setup script (installs dependencies only — does not run rclone config):

```bash
bash setup.sh
```

**Step 3** — Follow the rclone setup section below, then launch:

```bash
./run.sh
```

---

### What the install script installs

| Tool | Purpose |
|------|---------|
| Homebrew | macOS package manager |
| Python 3 | Runtime |
| rclone | Google Drive communication |
| PyQt6 and other pip packages | App dependencies (from `requirements.txt`) |

### Amphetamine (optional but recommended)

Prevents your Mac from sleeping mid-transfer. Install from the [Mac App Store](https://apps.apple.com/us/app/amphetamine/id937984704).

---

## First-time rclone setup for Google Drive

Do this once per workstation.

```bash
rclone config
```

Walk through the prompts:

1. `n` — new remote
2. Name it `gdrive` — this is what the app looks for by default
3. Choose Google Drive — typically option 18, but the number varies by rclone version. Look for `drive`.
4. Press Enter through `client_id` and `client_secret` unless your team has provided custom values
5. **Scope: type just `1` and press Enter.** This is the most common mistake. Type only the single digit `1` — nothing else. This selects full Drive access.
6. Press Enter on `root_folder_id`, `service_account_file`, and "Edit advanced config? n"
7. "Use auto config?" → `y` (a browser opens)
8. Sign in with your Signal Theory Google account and grant access
9. "Configure this as a Shared Drive (Team Drive)?" → `n` unless ST has a team drive you specifically need
10. `y` to keep the remote, then `q` to quit

Verify:

```bash
rclone listremotes
```

Should print `gdrive:`.

```bash
rclone lsd gdrive:
```

Should list your Google Drive folders.

---

## Launching the app

```bash
./run.sh
```

Or directly:

```bash
cd /path/to/st_synctool
source .venv/bin/activate
python3 main.py
```

Five tabs across the top. Each has its own purpose.

---

## Transfer tab

**Use when:** you are doing a one-way move — initial download from Drive to a new project folder, or pushing a fresh project up to Drive.

### Inputs

- **Source** — where the files are coming from. Either a local path (e.g., `/Volumes/Extreme SSD/projects/60318`) or a Google Drive folder URL (e.g., `https://drive.google.com/drive/folders/14cwfEbsoYOuiU_...`)
- **Destination** — same format options

The app auto-detects which side is local and which is Drive. **Drive → Drive is supported** and runs server-side: paste Drive URLs in both fields and rclone copies directly between folders without touching local disk.

### Options

- **On conflict:**
  - *Skip existing* — leave existing files alone, only copy new ones
  - *Overwrite* — replace existing files if source differs (default)
  - *Rename copy* — only for local-to-local; renames the incoming file with a `_conflict` suffix. Automatically disabled for Drive transfers.
- **Auto-extract multipart .zips after transfer** — extracts any `.zip` files that landed at the destination
- **Mirror mode** — destructive. Uses `rclone sync` instead of `rclone copy`. **Any file at the destination that is not in the source will be deleted.** Always shows an extra confirmation dialog.
- **Paranoid verification** — only matters for Drive transfers. Computes SHA-256 on the local side independently, so verification does not depend on trusting rclone. Slower (~1 GB/min added) but independent integrity check. Automatically disabled for local-to-local transfers — those already do independent verification.

### Pre-flight check

Before starting, the app shows total source size, estimated transfer time at 150 MB/s, destination free space (if local), and a warning if you are about to upload more than 750 GB to Drive in a day (Google's hard limit).

### During the transfer

- Progress bar shows percentage (byte-based for Drive transfers)
- Cancel button kills the rclone subprocess immediately
- Files are individually verified before being marked complete

### After the transfer

Two artifacts get written:

1. **Manifest** (`st_manifest.json`) — saved into source and destination folders, plus a timestamped copy in `~/Documents/STSyncTool/Manifests/`
2. **Text log** — `~/Documents/STSyncTool/Transfer Reports/transfer_<timestamp>.txt` with per-file hashes and status

---

## Merge tab

**Use when:** you downloaded a project from server, edited locally for days, and now want to push only the changes back. Or the reverse — you want to pull collaborator changes without re-downloading everything.

### How it works

A three-way diff between:

- **Base manifest** — the snapshot of what both sides agreed on at last sync
- **Yours** — what is currently on your local SSD
- **Theirs (server)** — what is currently on the server

For each file, the tool determines its state and proposes a default action:

| State | Meaning | Default action |
| --- | --- | --- |
| Unchanged | Same on all three | *(filtered out)* |
| Local Only | New file you created locally | **Push to Server** |
| Server Only | New file someone added on server | **Pull from Server** |
| Local Changed | You modified it, server has the old version | **Push to Server** |
| Server Changed | Someone modified it on server, yours is old | **Pull from Server** |
| Both Changed | Both sides modified independently | **Skip** *(conflict — your decision)* |
| Deleted Local | Used to exist, you deleted it | **Skip** *(no auto-delete on server)* |
| Deleted Server | Used to exist, deleted on server | **Skip** *(no auto-delete locally)* |
| Deleted Both | Gone everywhere | *(nothing to do)* |

### Summary header

After a scan, a one-line summary appears above the Changes table, for example:

> 3 conflicts need review · 44 files will sync automatically · 2 deletions held for you

It updates live as you change per-row actions: resolving a conflict moves it from "need review" into "will sync automatically", choosing a Delete action shows "N deletions will be applied" and rows set to Skip are counted as skipped. "Deletions held for you" means a file was deleted on one side and the tool is defaulting to Skip rather than propagating the deletion without your say-so. When a scan finds no differences the header reads "Everything in sync, N files unchanged".

### Inputs

- **Base Manifest** — optional. Auto-detects `st_manifest.json` in the local folder. Only override if you specifically want to compare against a different snapshot from `~/Documents/STSyncTool/Manifests/`.
- **Local Folder** — your working copy
- **Server** — the source of truth. Local path or Drive URL.

### Options

- **Preserve existing files on overwrite (rename incoming with date-initials suffix)** — default ON. When a Push or Pull would overwrite an existing file, the incoming version is renamed with `_<YYYY-MM-DD>-<initials>` before the extension. Example: pushing `project.prproj` becomes `project_2026-06-08-rk.prproj` on server, and server's original `project.prproj` is left alone. Turn this off when you are confident your version should genuinely replace the destination.
- **Re-scan before apply (catches drift since initial scan)** — default ON. Before executing any action, the tool re-runs the scan and aborts if any actionable file changed since you reviewed it.

### Workflow

1. Set up paths and click **Scan & Compare**. The diff table populates.
2. Review the proposed actions. Defaults are conservative — adjust dropdowns as needed.
3. Click **Apply Selected Actions**.
4. Confirm the dialog.
5. The tool re-scans (if enabled), executes the actions, and regenerates the manifest on both sides.

### Edge cases worth understanding

- **First merge after a Transfer download** — the base manifest is the one created by Transfer. Everything you edited will show as `LOCAL_CHANGED`.
- **Preserve mode produces persistent divergence by design.** Server keeps its original; you push as a backup-suffixed file. The next merge shows the suffix file as `SERVER_ONLY`. Decide later whether to pull it down or leave it as history.
- **Internal files are filtered.** `st_manifest.json`, `.DS_Store`, `Thumbs.db`, and `desktop.ini` never appear in the diff.
- **Fast-scan optimization.** Files whose size and modtime exactly match the base manifest skip hashing. On a 100 GB project with 5 files modified, this turns a 100-second scan into a 5-second scan. If you suspect false negatives, clear the Base Manifest field to force a full re-hash.

---

## Verify tab

**Use when:** you want to confirm a folder still matches what its manifest says. Useful before archiving, after a long-running sync, or when investigating suspected corruption.

### Inputs

- **Folder to Verify** — local path or Drive URL
- **Manifest File** — the `.json` to compare against. For local folders, auto-loads `st_manifest.json` from the folder if present. For Drive folders, you must explicitly provide one.

### What it does

For each file in the manifest, checks the corresponding file in the folder:

- **OK** — file present, hash matches the manifest
- **MISSING** — file in the manifest but not in the folder
- **MISMATCH** — file present but hash does not match

For Drive folders, hash comparison uses rclone's metadata-based lookup (no file downloads). A 9 GB folder verifies in about one second.

### Deep verify (Drive only)

The default Drive check trusts Google's stored hashes. For higher assurance, tick **Deep verify (downloads files)** — only enabled for Drive folders. It streams every file through rclone to compute its SHA-256 locally and compares that to the manifest, so corruption that Google's metadata would not reveal is caught. No local copy is kept (each file is hashed as it streams, then discarded). Because it is bandwidth-bound, an honest size and time estimate is logged when you start. The default 1-second metadata check is unchanged.

Any files present in the folder but not in the manifest are noted as a warning (not failures).

### Verify all projects (batch)

The **Verify All Projects** button runs every project in the registry (`~/Documents/STSyncTool/projects.json`) against its latest manifest in one pass and produces a single consolidated report with per-project OK / MISSING / MISMATCH counts and an overall OK / FAIL / ERROR tally. Projects with no manifest on record, no folder, or an unreadable manifest are listed in a "Skipped" section with the reason. A project that fails to verify (unreadable folder, rclone error) is reported as ERROR without aborting the rest of the run.

### Scheduled monthly verification

The app can install a macOS launchd agent that wakes once a month, verifies every registered project against its latest manifest, writes a consolidated report to `~/Documents/STSyncTool/Verify Reports/scheduled_verify_<timestamp>.txt`, and records the outcome. On the next normal launch, if the last scheduled run found problems, a dismissible banner appears ("2 archives failed verification on June 1"). No daemon and no background app — just a launchd agent the app installs on request and a headless run triggered with `--scheduled-verify`. (The install toggle ships with the signed app build, since the agent must point at the installed application.)

### Output

A verification report at `~/Documents/STSyncTool/Verify Reports/verify_<timestamp>.txt` with per-file status.

Every run also persists a machine-readable JSON report at `~/Documents/STSyncTool/Verify Reports/verify_report_<label>_<timestamp>.json` carrying each per-file result, including the format-aware media-verify outcome (`OK` / `ADVISORY` / `FAILED`), so the evidence survives the window closing. Where a manifest is present on disk, the same media-verify outcome is written into a `media_verify` block on each media file's manifest entry. Both formats are documented in `SCHEMA_INTEROP_SPEC.md`.

---

## Offload tab

**Use when:** cards come off camera or audio recorder and need to be copied to one or more destinations with verified integrity and a chain-of-custody record.

### Design principles

- **Source is read-only.** The source card is never written to or modified in any way — not even for filename normalisation.
- **Checksum is mandatory.** Every file is hashed on the source before copying. After the copy, every destination file is re-hashed and compared against the source ground-truth. In-flight hash is not sufficient.
- **Staging before commit.** Files land in `{dest}/{label}/.st_staging_{ts}/` first. Only after full verification is the staging folder renamed to its final path. On failure, staging is left in place with a failure report alongside it.
- **Per-source eject signal.** A source is flagged safe to eject as soon as all its destinations verify — no need to wait for other sources.
- **Free-space pre-flight.** Before any byte is copied, the app sums the source size and checks every local/NAS destination has room (plus a small safety headroom so a disk is never filled to the very last byte). If a destination cannot fit, the offload is refused up front with a clear shortfall ("need 482 GB, this drive has 210 GB free") rather than failing mid-copy. Drive destinations are not local-disk-checked.
- **Duplicate-card guard.** When you start an offload, each source is fingerprinted (file count, total bytes, top-level folder names, volume label) and compared against prior offloads to the same destination. If it looks already offloaded, you get a warn-and-confirm before re-copying — you can always continue. A reused card carrying new content does not trigger it, because the match is on content, not just the card's name.

### Inputs

- **Sources** — each source has a label (used as the destination subfolder name to prevent collisions), a path, and an enable toggle. Add as many as needed.
- **Destinations** — same pattern. Files land at `{dest}/{source_label}/{files}`.

### Volume auto-detection

When a removable media card is plugged in, the Offload tab detects it and shows a non-modal banner:

> **New volume 'A001' detected** (64.0 GB, exFAT, contains DCIM) — Add as source?  `[Add]` `[Dismiss]`

Clicking **Add** appends a pre-populated source row with the volume name as the label and the mount path already filled in. Clicking **Dismiss** silences that card for its current mount session (ejecting and replugging it will offer it again).

**This is detect-and-suggest, not detect-and-start.** Nothing copies until the user clicks "Start Offload." The source card is never written to during detection.

The feature filters aggressively: only volumes that are both removable+ejectable (OS flags) AND contain a recognisable media structure at or near the root (`DCIM/`, `PRIVATE/`, `CLIP/`, `MEDIA/`, `AUDIO/`, `SOUND/`, `.RDM`/`.RDC`) are surfaced. Plain external drives, Time Machine volumes, and network mounts are ignored.

Toggle **Auto-detect media cards** in the options bar to enable or disable. The preference is saved.

Requires pyobjc (`pip install pyobjc-framework-AppKit`). If pyobjc is not installed the toggle is disabled and detection is silently skipped.

### Options

- **Filename normalisation** — detects sequential generic naming schemes (`IMG_XXXX`, `GH0XXXXX`, `DJI_XXXX`, etc.) where ≥60% of video files match the pattern, or where two sources share overlapping filenames. If detected, offers to append the first 8 characters of the file's SHA-256 hash to the stem (`IMG_1205.mov` → `IMG_1205_a3f9b2c1.mov`). The rename is deterministic — the same file always gets the same suffix. Source card is never touched; rename happens at the destination during staging.
- **Generate contact sheet** — requires ffmpeg and Pillow (see dependencies below). Runs after the primary destination commits. See "Contact sheets" below.
- **Continue on failure** — if a destination fails, continue with the remaining destinations for that source rather than aborting.

### Execution order

Sources are processed sequentially (source 1 → all destinations, then source 2, etc.). Within a source, all destinations copy in parallel. Per-file retries are 3 attempts with exponential backoff. Retryable errors (IO timeout, connection reset) are distinguished from non-retryable ones (disk full, permission denied).

### Status matrix

A live grid shows each source/destination pair as: pending / copying / verifying / done / failed.

### Output

For each offload, a chain-of-custody log is saved to `~/Documents/STSyncTool/Offload Reports/`. It records source manifests, per-destination verification results, any filename renames, and contact sheet artifacts.

### Safe to format

For every source the log carries an explicit `CLEARANCE:` verdict. A card is marked **SAFE TO FORMAT** only when at least two destinations verified clean, so a single drive failure can never cost the only copy. Otherwise it is **NOT CLEARED** with the reason (a verification failure, an unverified destination, or only one clean copy). The verdict is computed in `core/clearance.py` and recorded in the chain-of-custody log; never format a card until its source reads SAFE TO FORMAT.

---

## Contact sheets

After the primary destination commits and verifies, a contact sheet is generated for the source label. One row per clip. Saved to:

- `{primary_dest}/{source_label}/_contact_sheet_{ts}.pdf`
- `~/Documents/STSyncTool/Contact Sheets/_contact_sheet_{ts}.pdf`

**Thumbnail count (adaptive, 1–4 max):**

| Duration | Frames |
|----------|--------|
| Under 5 s | 1 |
| 5–30 s | 2 |
| 30 s – 2 min | 3 |
| Over 2 min | 4 |

Frame positions: 15%, 38%, 62%, 85% of runtime (avoids head/tail black frames).

**Metadata baked into each tile (from ffprobe):** filename, camera make/model, codec and profile, resolution, frame rate, bit depth, duration, timecode at frame, file size, date recorded.

**Tile types:**

- **Video** — thumbnail strip + metadata column
- **R3D** — thumbnail strip via REDline (if installed) or rich metadata-only tile from the `.RMD` sidecar when REDline is absent. See "R3D support" below.
- **BRAW** — metadata-only tile (thumbnail preview pending a clean CLI path); parses Blackmagic `.sidecar` XML for metadata
- **Audio** — metadata-only card (format, sample rate, bit depth, channels, duration)

If filename normalisation was applied, each tile shows the normalised destination name as primary and the original card filename as secondary.

### Dependencies

| Tool | Required for | Install |
|------|-------------|---------|
| ffmpeg + ffprobe | Video frame extraction and metadata probing | `brew install ffmpeg` |
| Pillow | Tile compositor and PDF output | `pip install Pillow` |
| REDline | R3D frame extraction (optional) | Install REDCINE-X PRO (free) from red.com |

If ffmpeg or Pillow is absent, the contact sheet option is grayed out with an install hint in the tooltip. If REDline is absent, R3D clips still get a tile — just without thumbnail frames.

---

## R3D support

RED camera footage has a folder-based clip structure. The `.RDC` folder is the logical clip unit — not the individual `.R3D` segment files inside it.

```
REEL001.RDM/
  A001_C001_0101AB.RDC/       ← one clip
    A001_C001_0101AB_001.R3D  ← segment 1
    A001_C001_0101AB_001.RMD  ← metadata sidecar
    A001_C001_0101AB_002.R3D  ← segment 2 (clips over ~4 GB)
    A001_C001_0101AB_002.RMD
```

The offload and contact sheet logic treats each `.RDC` folder as a single clip. Multi-segment clips are handled transparently.

**Metadata** is read from the `.RMD` sidecar XML (no SDK required): frame count, fps, resolution, ISO, white balance, aperture, focal length, timecode, camera model and serial, REDCODE compression ratio, color science version.

**Frame extraction** uses REDline (ships free with REDCINE-X PRO from red.com). When REDline is absent, the contact sheet still generates a rich metadata tile with "Install REDCINE-X PRO (free) for R3D previews" in the thumbnail area.

**Filename normalisation never applies to R3D.** RED naming already includes a date component and camera identifier (`A001_C001_210601_ABCD`), so it is unconditionally excluded from generic pattern detection.

---

## Key concepts

### Manifests (`st_manifest.json`)

The manifest is a JSON file that lists every file in a folder along with:

- File size
- Modification time
- SHA-256, SHA-1, and MD5 hashes
- Status info (verified, when, by whom)

**Where they live:**

- In the folder itself (`<folder>/st_manifest.json`) — what the tool auto-detects
- Timestamped copies in `~/Documents/STSyncTool/Manifests/` — historical record

**When they get created:**

- After a successful Transfer
- After a successful Merge apply
- Manually via the **Generate Manifest Only** button on the Transfer tab

The manifest is the contract between your local copy and the server. It is how Merge knows what changed and what did not.

### Verification modes

- **Default (rclone `--checksum`)** — rclone compares hashes on both sides during transfer. Fast, integrated.
- **Paranoid** — the app additionally computes SHA-256 locally and compares. Adds ~1 GB/min of overhead.

For local-to-local transfers, paranoid mode is the only mode. Every file is hashed before and after the copy.

### Preserve-on-overwrite

Naming convention: `<filename>_<YYYY-MM-DD>-<initials>.<ext>`

Examples:

- `project.prproj` → `project_2026-06-08-rk.prproj`
- `final.mov` → `final_2026-06-08-rk.mov`

Initials come from your system username (e.g., `richard.kerr` → `rk`). If today's date + initials would collide with an existing backup, a numeric suffix is added (`_2`, `_3`, etc.).

---

## Power-user features

### `ST_SYNC_RCLONE_REMOTE` environment variable

By default, the app looks for an rclone remote named `gdrive`. To use a different remote name:

```bash
ST_SYNC_RCLONE_REMOTE=my_other_drive python3 main.py
```

### Generate Manifest Only button

On the Transfer tab. Generates a manifest for a folder without copying anything. Use cases:

- You have files that arrived via another tool (Dropbox, USB drive) and want a manifest to verify them later
- You want to snapshot the current state before making changes

### Historical manifests

Every successful Transfer or Merge writes a timestamped manifest to `~/Documents/STSyncTool/Manifests/`. Filename format:

```
st_manifest_<folder_name>_<YYYYMMDD>_<HHMMSS>.json
```

These accumulate over time. Safe to delete if you do not need the history.

### Logs

Human-readable reports live under `~/Documents/STSyncTool/`, split by type:

```
Offload Reports/<date>/   chain-of-custody logs
Verify Reports/           verify, batch-verify and scheduled-verify reports
Transfer Reports/         transfer logs
```

(See **Where your files go** above for the full layout.)

---

## Troubleshooting

### "Amphetamine could not understand the message received from AppleScript"

Old version of Amphetamine. Update from the Mac App Store.

### "Some requested scopes were invalid" during rclone config

You typed multiple numbers at the scope prompt. Type just `1` and press Enter. Nothing else.

### "Files in destination but not in manifest" warning

Not necessarily a problem. These are files that exist in the folder but were not in the manifest you compared against — usually because they were added after the manifest was generated. Worth investigating if you did not expect them.

### Transfer "fails" but files appear to be there

Check the log widget for the actual error. The most common cause is rclone OAuth expiration. Re-run `rclone config` and re-authenticate the existing remote.

### Cancel button does not seem to stop the transfer immediately

The cancel button terminates the rclone subprocess, but rclone may take a few seconds to finish writing the in-flight file before exiting. Wait 5–10 seconds. If it still does not release:

```bash
ps aux | grep rclone
killall rclone
```

### Merge shows unexpected conflicts on `st_manifest.json` or `.DS_Store`

These should be filtered. If you are seeing them, you might be running an older version of the code. Pull latest from the repo.

### "rclone copy failed: unknown flag"

Your rclone is out of date. Update:

```bash
brew upgrade rclone
```

### Verify shows mass MISSING / MISMATCH against a Drive folder you know is correct

You probably paired the wrong manifest with the wrong Drive folder. Most common cause: the manifest in your local folder got overwritten by a subsequent Transfer run. Look in `~/Documents/STSyncTool/Manifests/` for the historical manifest from the time of the original upload, and use that instead.

---

## Resume after an interrupted offload

If an offload is interrupted (power loss, crash, yanked drive), the partial copy stays in a hidden staging folder at the destination along with a small state file. The next time you start an offload of the same card to the same destination, the app offers **Resume**: files already copied are re-verified against the original source hashes and reused, so only missing or corrupted files are copied again. Choosing **Start Fresh** discards the partial copy instead. The chain-of-custody log records that the offload was resumed and lists every reused file. The source card is never written to in either case.

---

## Update notifications

On launch the app quietly asks GitHub for the latest published release (5-second timeout, completely silent if offline or the request fails). If a newer version exists, a dismissible banner appears with a Download button linking to the release page. There is no auto-update and no background daemon — just awareness. The running version is shown in the window header.

---

## ASC MHL export

Tick **Export ASC MHL (.mhl)** in the Transfer or Offload tab to write an ASC Media Hash List sidecar (ASC MHL v2.0) next to the manifest. Post houses can then verify Signal Theory deliveries with their own tools (Silverstack, YoYotta and similar) without trusting our app. It is a translation of the hash data already in the manifest, so nothing is rehashed. Note that ASC MHL has no sha256 element, so the export uses the manifest's xxh3 hash for local files and md5 for Drive files; the sha256 stays in `st_manifest.json`.

---

## Report a problem

A **Report a Problem** button in the window header bundles recent logs into a single zip a tester can email. It collects the last 14 days of files from `Offload Reports/`, `Verify Reports/` and `Transfer Reports/`, plus a `system_info.txt` carrying the app version, macOS version and Python version. You pick where to save the zip; the app then reveals it in Finder so you can attach it to an email describing the problem. Nothing is uploaded — the bundle never leaves your machine until you send it.

---

## Settings

The **Settings** button in the header opens a small dialog for the org-activity options:

- **Activity remote base** — the shared Drive folder logs ship to. It ships with a sensible default (the Signal Theory shared folder), so most users never touch it. You can paste a Google Drive folder URL, a full rclone base (`gdrive:Folder`) or a bare folder name to override; leave it blank to use the default.
- **Ship logs to the shared folder** — the opt-out toggle. On by default once a base is set; turn it off to keep everything local.

Settings persist to `~/.config/st_synctool/config.json` (shared with the active rclone remote). An `ST_SYNC_ACTIVITY_REMOTE` environment variable overrides the base for a single run.

---

## Org-wide activity log

Every offload, transfer, merge and verify writes its custody log and manifest locally first — the local copy is the source of truth and never depends on the network. The app then ships those files (append-only, never deleting anything remotely) to a shared Google Drive folder via the rclone remote every user already has, so the org gets a single view of all production activity. The remote layout mirrors the local folders, namespaced per machine and user: `ST_SyncTool_Activity/{workstation}/{user}/...`.

The shared folder is configured once: set `core/settings.DEFAULT_ACTIVITY_FOLDER` to a folder in a Shared Drive every user can reach (e.g. `ST_SyncTool_Activity`) and every install auto-derives its remote base from its own rclone remote — no per-user setup. Individual users can override it under **Settings**. Until that constant is set (or a base is entered in Settings), shipping is a safe no-op.

Offline is treated as the normal case, not the edge case. A "shipped" ledger records exactly what is confirmed uploaded; anything not in the ledger is retried at the next trigger (after each operation and on every app launch), so an offload done off the network ships automatically the next time the app opens with internet. Alerting is quiet on the cart and loud in the office: a passive status line ("Activity log: N reports waiting to upload"), escalating to a gentle banner only when files have been pending 7+ days, never a popup.

Alongside the raw files, each completed job appends one compact summary line to a per-machine index (`activity_{workstation}.jsonl`: operation, time, user, project, source, destinations, file count, bytes, verdict, log filename). Because each machine writes only its own shard there are no write conflicts and no server. Org-wide views merge the shards (kilobytes) and fetch a raw custody log only when a human opens one. Per-workstation staleness ("Cart 3 hasn't reported since June 2") falls out of the merged shards for free.

### History

The **History** tab renders the merged index as readable rows ("Jun 12 · Cart 3 · Offload · A001 → NAS, Shuttle · 312 files · 1.2 GiB · VERIFIED") with dropdown filters for operation, workstation, user and project. Your own machine's history loads instantly from local files and works offline; **Refresh org activity** downloads only the other machines' summary shards (kilobytes), never the raw logs. Set the shared Drive folder under the **Settings** button first. The query and row-formatting logic lives in `core/history.py` (headless and unit tested); the GUI renders rows and dropdowns only.

---

## Where your files go

Everything lives under `~/Documents/STSyncTool/`, organised for humans:

| Folder | Contents |
|--------|----------|
| `Offload Reports/` | Chain-of-custody logs, grouped by date (`2026-06-13/A001 20.10.56 ….txt`) |
| `Verify Reports/` | Verify, batch-verify and scheduled-verify reports |
| `Transfer Reports/` | Transfer logs |
| `Contact Sheets/` | Thumbnail contact-sheet PDFs |
| `Manifests/` | Per-project `st_manifest.json` archives |
| `projects.json` | The project registry |
| `.app-state/` | Hidden machine state (shipping ledger, upload tally, scheduled-verify state, activity shards) — not meant to be opened |

Paths are defined in one place (`core/paths.py`) and can be redirected with the `ST_SYNC_HOME` environment variable (the test suite uses this so it never writes into your real Documents).

## Known limitations

- **Drive → Drive transfers run server-side.** Paste a Drive URL in both Source and Destination; rclone copies directly between the folders with no local disk used. Paranoid verify is unavailable for this direction (there are no local files to hash); verification relies on Drive checksums. The manifest is built from the destination's Drive metadata and saved to the central archive only. The 750 GB/day limit applies to server-side copies too and is enforced in pre-flight.
- **Local NAS merges hash every file on the first scan.** Subsequent scans use the modtime+size fast-path and are much faster.
- **Manual merge is not implemented.** For `BOTH_CHANGED` conflicts you must choose Push or Pull — there is no diff-tool integration. Production binary assets do not benefit from text-merge tooling anyway.
- **The 750 GB/day Drive upload limit** is enforced as a hard error in pre-flight check. Because uploads made outside this app (browser, Drive desktop client) are invisible to it, the app cannot know the account's true remaining quota. Instead it keeps an honest **daily upload floor** — "at least N uploaded through ST SyncTool today" — recorded per local day, and if Google itself rejects an upload with a rate-limit or storage error, the cryptic rclone failure is translated into a plain-language message ("Google's daily upload limit was hit … resets at midnight Pacific … your files are safe") shown in the transfer log. The daily floor resets at local midnight. (Offload runs copy to local/NAS destinations only and never upload to Drive, so this applies to the Transfer and Merge Drive paths.)
- **The duplicate-card guard is a heuristic, not a guarantee.** It matches a source against prior offloads by content fingerprint (file count, total bytes, top-level names) for the chosen destination. Two genuinely different cards that happen to share an identical file count and byte total could match (you can confirm and continue), and a card whose content changed since its last offload will not match. It is a safety prompt to catch the common footgun, not a substitute for the chain-of-custody log.
- **The free-space pre-flight covers local and NAS destinations only.** Drive (server-side) destinations are governed by the 750 GB/day limit above instead, since there is no local disk to measure.
- **The app currently supports only macOS.** Windows / Linux support is theoretically possible but untested.

---

## For developers

### Architecture

```
st_synctool/
  main.py                  Entry point (prepends bundled-binary dirs to PATH when frozen)
  core/
    paths.py               Single source of truth for the on-disk layout (ST_SYNC_HOME)
    version.py             __version__ — single source for the app version
    checksum.py            SHA-256 / SHA-1 / MD5 / xxhash3_64 file hashing
    manifest.py            Manifest schema, generate/save/load
    comparison.py          Three-way diff logic
    diff_summary.py        Merge summary header counts
    transfer.py            Local-to-local and rclone transfer orchestration
    rclone_bridge.py       Subprocess wrapper around rclone CLI (swappable runner seam)
    merge_ops.py           Single-file push/pull/delete operations
    merge_logic.py         Server-manifest routing for the Merge tab
    offload.py             Camera card ingest: pre-hash, staging, verify, commit, COC log
    clearance.py           "Safe to format" verdict (M10.1)
    verify.py              Verify logic: local, Drive, deep, batch, report persistence (M5.x)
    media_verify.py        Format-aware media checks (ffprobe)
    quota.py               Google rate-limit classification + daily upload floor (M10.2)
    scheduled_verify.py    launchd monthly verify + failure banner (M5.3)
    asc_mhl.py             ASC MHL v2.0 export (M10.3)
    settings.py            Typed settings store + activity-base resolution (M11.1)
    log_sync.py            Org log shipping to the shared Drive folder (M9.1)
    activity_index.py      Per-machine activity shards: write/merge/fetch/staleness (M9.2/9.3)
    history.py             History row formatting, filtering, staleness line (M9.3)
    feedback.py            "Report a Problem" log+info zip bundle (M7.3)
    update_check.py        GitHub release check (M7.5)
    thumbnail.py           Frame extraction, tile compositor, contact sheet PDF output
    projects.py            Projects registry and destination preset persistence
    amphetamine.py         AppleScript wrapper for Amphetamine.app
    demo.py                Demo data for every tab (transfer/merge/offload/verify/history)
  utils/
    file_utils.py          folder_size, free_space, format_bytes
    gdrive_utils.py        URL parsing, rclone remote detection, folder-id connstr
    resources.py           Bundled-binary discovery for the frozen .app (M7.1)
  gui/
    main_window.py         5-tab container + header (Settings/Report/Tour), banners, tour
    transfer_tab.py        Transfer tab UI + worker
    merge_tab.py           Merge tab UI + worker
    verify_tab.py          Verify tab UI + worker
    offload_tab.py         Offload tab UI + workers
    history_tab.py         History tab UI (M9.3)
    settings_dialog.py     Settings dialog (M11.2)
    setup_wizard.py        First-run rclone setup wizard
    tutorial_overlay.py    Guided onboarding tour overlay
    path_input_widget.py   Reusable path input with browse button
    log_widget.py          Reusable colored log output
    diff_table.py          Diff table with per-row dropdowns
  STSyncTool.spec          PyInstaller build spec (M7.1)
  build.sh                 Build .app + DMG (optional codesign)
  docs/                    QUICKSTART.md, release.md (build runbook)
  requirements.txt
```

### Key design decisions

- **Transfers route through `route_transfer()` in `core/transfer.py`**, which dispatches to either `transfer_folder()` (local) or `transfer_folder_rclone()` based on URL detection. Adding a new transfer backend means adding a new dispatch branch.
- **Single-file operations live in `merge_ops.py`** (push, pull, delete). They abstract local-vs-rclone so the Merge tab's `ApplyWorker` does not need to branch on every action.
- **The rclone subprocess is tracked in `_current_proc`** in `rclone_bridge.py` with a lock, so the Cancel button can terminate it from the main thread without race conditions.
- **The `DiffState` enum in `core/comparison.py` is the source of truth** for the merge state vocabulary. To add a new state, update `DiffState`, then add the state to `gui/diff_table.py:_ACTIONS_BY_STATE` and `_STATE_COLORS`.
- **Action constants live in `core/merge_ops.py`** (`ACT_PUSH`, `ACT_PULL`, etc.) and are imported everywhere.
- **rclone progress is parsed from stderr** using a regex against the `--stats-one-line` output, streamed via a reader thread.

### Adding a new feature

**For Transfer-flow changes:**

1. `core/transfer.py:route_transfer()` — top-level entry point
2. `gui/transfer_tab.py:TransferWorker` — Qt worker that calls into core
3. `gui/transfer_tab.py:TransferTab._start_transfer()` — UI plumbing

**For Merge-flow changes:**

1. `gui/merge_tab.py:ApplyWorker.run()` — orchestrator
2. `core/merge_ops.py` — single-file operations
3. `gui/diff_table.py:_ACTIONS_BY_STATE` — what shows in the dropdown

**For Verify-flow changes:**

1. `gui/verify_tab.py:VerifyWorker._verify_local()` or `_verify_gdrive()`

### Testing

Run the full test suite:

```bash
source .venv/bin/activate
pytest
```

The suite covers core logic (manifest, comparison, transfer, merge_ops), rclone bridge, apply worker, and GUI smoke tests via pytest-qt. Integration tests use `/tmp/` fixtures — no Drive connection needed.

Manual end-to-end sandboxes used during development:

- Local↔Local Transfer: `/tmp/sync_test_src` → `/tmp/test/`
- Local↔Local Merge: `/tmp/sync_test/merge_local` + `/tmp/sync_test_server/merge_local`
- Drive transfers: a junk folder created in the browser, deleted after

### Extending to Drive ↔ Drive transfers

The plumbing exists. `transfer_folder_rclone()` raises `TransferError` for the Drive↔Drive case. To enable:

1. Remove the guard in `transfer_folder_rclone()`
2. Build two separate flag lists (one per side) using `gdrive_url_to_rclone()` on each
3. Test against rclone's behavior — `rclone copy` may need `--drive-server-side-across-configs`

### Extending to Drive Shared Drives (Team Drives)

The current rclone config assumes a personal Drive. For shared drives:

1. Re-run `rclone config` and answer `y` to "Configure this as a Shared Drive (Team Drive)?"
2. Select the team drive from the list
3. Set the `ST_SYNC_RCLONE_REMOTE` env var to your new remote name, or rename it `gdrive` to use defaults

No code changes needed.

### Key paths

All on-disk paths resolve through **`core/paths.py`** (the single source of truth), with the base redirectable via the `ST_SYNC_HOME` env var (the test suite uses this so it never writes into the real Documents folder).

| Accessor (`core.paths`) | Path |
|------|------|
| `base_dir()` | `~/Documents/STSyncTool/` (or `$ST_SYNC_HOME`) |
| `manifests_dir()` | `…/Manifests/` |
| `offload_reports_dir()` | `…/Offload Reports/<date>/` |
| `verify_reports_dir()` | `…/Verify Reports/` |
| `transfer_reports_dir()` | `…/Transfer Reports/` |
| `contact_sheets_dir()` | `…/Contact Sheets/` |
| `projects_registry()` | `…/projects.json` |
| `app_state_dir()` | `…/.app-state/` (hidden: ledger, tally, scheduled-verify state, `activity/` shards) |
| `MANIFEST_FILENAME` | `st_manifest.json` — written to local + server root |
| App config | `~/.config/st_synctool/config.json` (active remote + Settings) |

### Checksum algorithm selection

| Scenario | Algorithm |
|----------|-----------|
| Local-to-local transfer | SHA-256 pre + post copy |
| rclone transfer, no paranoid | rclone `--checksum` internal |
| rclone transfer, paranoid | SHA-256 local vs Drive's reported SHA-256 |
| Merge push/pull via rclone | rclone `copyto --checksum` |
| Merge push/pull local | SHA-256 pre + post |
| Offload pre-hash | SHA-256 on source |
| Offload destination verify | SHA-256 vs source ground-truth |

GDrive does not always compute SHA-256 for all files. When it hasn't, paranoid mode falls back to rclone checksum and logs a warning via `checksum_context`.

### External dependencies (consolidated)

| Tool | Required for | Install |
|------|-------------|---------|
| rclone | GDrive transfers | `brew install rclone` |
| ffmpeg + ffprobe | Contact sheet frame extraction | `brew install ffmpeg` |
| Pillow | Contact sheet compositor | `pip install Pillow` |
| REDline | R3D frame extraction (optional) | REDCINE-X PRO (free) from red.com |
| pyobjc | Offload tab volume auto-detection (optional) | `pip install pyobjc-framework-AppKit` |
