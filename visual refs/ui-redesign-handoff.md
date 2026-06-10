# UI Redesign Handoff — ST SyncTool

All four tabs need layout and hierarchy improvements. No functional changes — same features, same logic, better presentation. Work tab by tab. The mockups were designed and approved in Cowork; this doc is the implementation spec.

---

## Global rules (apply everywhere)

- Primary action buttons always appear **above** the log/output area, never below it
- One `Clear` button per log panel — inside the log panel header, not duplicated below
- Section groupings use a consistent panel style: white bg, 0.5px border, rounded corners, uppercase 11px section label
- The log area is a fixed-height panel at the bottom of each tab with a header row containing the label and Clear button

---

## Transfer tab (`gui/transfer_tab.py`)

### Pre-flight summary
Add an inline summary row between the path inputs and the options section. Populate it when both source and destination are set (reuse the existing pre-flight check logic). Show:
- Source size (formatted bytes)
- Destination free space (if local)
- Estimated time at 150 MB/s

Display `—` in each field until paths are entered. Replace the current "Enter source and destination to see transfer summary." text label with this row.

### Options section
Reorganise into two rows:

**Row 1 (safe options, inline):**
- On conflict dropdown (existing)
- Auto-extract multipart .zips checkbox (existing)
- Paranoid verification checkbox (existing)

**Row 2 (danger zone — separate styled container):**
- Mirror mode checkbox
- Style: red-tinted background (`#FCEBEB`), red border (`#F7C1C1`), red text (`#A32D2D`), warning icon before the label
- Label: "Mirror mode — deletes files at destination not present in source"
- Keep the existing confirmation dialog on enable — just move it into this styled container

### Progress
Move the progress bar inside the log panel header area (below the `Transfer log` label row, above the log text body). Remove the standalone Progress section.

### Actions row
- Keep: `Start Transfer` (yellow), `Cancel`
- Demote `Generate Manifest Only` to a ghost/secondary button, right-aligned in the same row. Use an icon prefix (document icon). It should not visually compete with the primary CTA.
- Status text (`Ready`, etc.) stays between Cancel and Generate Manifest Only

### Log panel
Single panel at the bottom:
- Header row: "Transfer log" label (left) + "Clear" button (right)
- Progress bar immediately below the header row (zero height / hidden until transfer starts)
- Log text body below that

---

## Merge tab (`gui/merge_tab.py`)

### Apply button state
`Apply Selected Actions` must be **disabled and visually dimmed** (reduced opacity) until a scan has been completed and returned results. Add a short status label next to it when dimmed: "Scan first to enable apply". Enable it only after `three_way_diff` returns a non-empty result set.

### Diff table
The diff table currently appears below two empty log areas and feels orphaned. Move it directly below the action buttons — it should be the dominant element of the tab after a scan. Show it in a panel with the section header "Changes".

State badge colours for the `State` column:
- `LOCAL_CHANGED` → blue pill (`#E6F1FB` bg, `#185FA5` text)
- `SERVER_CHANGED` → green pill (`#EAF3DE` bg, `#3B6D11` text)
- `BOTH_CHANGED` → red pill (`#FCEBEB` bg, `#A32D2D` text)
- `LOCAL_ONLY` / `SERVER_ONLY` → neutral grey pill
- `DELETED_*` → neutral grey pill

### Merge History section
Remove from the tab UI. The merge history is already written to `~/Documents/STSyncTool/logs/`. Replace with a small "Open logs folder" link below the log panel if you want to preserve discoverability.

### Log panel
Single panel at the bottom (same pattern as Transfer):
- Header: "Merge log" + Clear button
- Log text body

---

## Offload tab (`gui/offload_tab.py`)

### Source row — remove initials field
Delete the separate initials input (`S...` field). Initials are already derived from the system username in `preserve_filename()` — this derivation is sufficient. No manual entry needed.

### Source row — remove lock icon
Remove the subfolder lock icon. Replace the behaviour with: subfolder field defaults to `(same as label)` and is always editable. No locking mechanic needed.

### Source row layout (simplified, left to right)
`[checkbox] [label field] [path field] [Browse button] [delete button]`

That's 5 elements instead of 8. The subfolder field moves into an expandable section or becomes a secondary row that appears on focus/hover if you want to keep the feature — but it should not be in the primary row.

### Presets — scope clarification
The preset Load/Save/Delete controls currently sit inside the Destinations panel header, implying they only save destinations. They should save the full configuration (sources + destinations). Move the preset controls to a row above both panels (or rename the section header to make scope clear). Remove the `Delete` preset button — replace it with overwrite-on-Save behaviour (save dialog confirms overwrite if name exists).

### Options bar
Reorder by importance:
1. Generate contact sheets checkbox + Max frames spinner (primary workflow choice)
2. Divider
3. Stop on first destination failure checkbox
4. Divider
5. Retries per file spinner (advanced, set-and-forget)

### Primary action
`Start Offload` and `Cancel` move above the log panel. Status text right-aligned in the same row.

### Two log areas
Currently there are two separate dark areas below the action buttons. Collapse into one log panel using the standard pattern (header + Clear button + log body).

---

## Verify tab (`gui/verify_tab.py`)

### Duplicate Clear Log button — bug fix
There are currently two `Clear Log` buttons. Remove the lower one. Only one should exist, in the log panel header.

### Remove `--` placeholder
The `--` text that appears between the Run button and the Results Log label serves no purpose. Remove it.

### Add summary cards
After verification completes, show four metric cards in a row above the log panel:
- **OK** — count of files with matching hashes (green)
- **Extra files** — files present but not in manifest (amber)
- **Missing** — files in manifest but absent from folder (red)
- **Mismatch** — files present but hash doesn't match (red)

Show `—` in all four until a verification has run. Use the existing return values from `_verify_local()` / `_verify_gdrive()` — the data is already there, just not surfaced in the UI.

Card style: secondary background, no border, rounded corners, 20px/500 number, 11px muted label below.

### Action row
`Run Verification` (yellow, shield-check icon prefix) + `Cancel` + status text — above the summary cards and log panel.

---

## File reference

```
gui/
  transfer_tab.py
  merge_tab.py
  offload_tab.py
  verify_tab.py
  log_widget.py       ← shared log panel component, update if needed
```

All core logic stays in `core/`. These changes are UI layer only.
