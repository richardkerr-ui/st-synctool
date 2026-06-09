# Roadmap

## v1 carry-forward tests

1. Real-world Drive merge test. Drive Transfer + Drive Verify and
   Local<->Local Merge are validated. Drive-as-server Merge code path
   exists but hasn't actually been exercised end-to-end. 10-minute test.
2. Verify drift detection. Verify against a healthy Drive folder passes.
   Confirm Verify actually catches a mismatch: rename a file in Drive
   via the web UI, re-run Verify, confirm MISSING/MISMATCH is reported.
   2-minute test.
3. Build / distribute. App currently runs via python3 main.py from a
   checkout. build_st_synctool.sh exists — confirm it still produces a
   working .app bundle after today's refactor.

## v2 work in progress

- Phase 1: Setup wizard. SHIPPED.
- Phase 2: Live file-level progress + ETA. In progress.
- Phase 3: Conflict resolution UI for BOTH_CHANGED Merge rows. Planned.

## v2 candidates (not yet committed)

- Drive -> Drive transfers. Plumbing mostly exists. Real ask: moving a
  project between ST Drive folders without burning local disk space.
- Local NAS server merge speed. Full hash walk on first scan; pre-filter
  helps but still slow. Worth profiling.
- Manual merge for text files. Only if someone actually asks. Production
  binary assets don't benefit from text-merge tooling.
- Default exclude filter for .DS_Store, Thumbs.db, desktop.ini.
- README documenting brew install rclone + rclone config + the
  ST_SYNC_RCLONE_REMOTE override.
