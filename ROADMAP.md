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

## v2 candidates (not yet committed)

- Drive to Drive transfers. Plumbing mostly exists. Real ask: moving a
  project between ST Drive folders without burning local disk space.
- Local NAS server merge speed. Full hash walk on first scan; pre-filter
  helps but still slow. Worth profiling.
