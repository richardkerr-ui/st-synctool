#!/usr/bin/env bash
# pre_beta_clear.sh — wipe all dev/test artifacts before beta ship.
# Safe to run repeatedly. Does NOT touch rclone config or app settings.json.
#
# Usage:
#   ./scripts/pre_beta_clear.sh          # dry run (preview only)
#   ./scripts/pre_beta_clear.sh --go     # actually delete

set -euo pipefail

DRY=true
[[ "${1:-}" == "--go" ]] && DRY=false

BASE="${HOME}/Documents/STSyncTool"
APP_STATE="${BASE}/.app-state"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RESET='\033[0m'

log()  { echo -e "${GREEN}[clear]${RESET} $*"; }
warn() { echo -e "${YELLOW}[skip] ${RESET} $*"; }
dry()  { echo -e "${YELLOW}[dry]  ${RESET} would remove: $*"; }

remove() {
    local path="$1"
    if [[ ! -e "$path" ]]; then
        return
    fi
    if $DRY; then
        dry "$path"
    else
        rm -rf "$path"
        log "removed $path"
    fi
}

remove_contents() {
    # Remove contents of a dir but keep the dir itself (app expects it to exist).
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        return
    fi
    if $DRY; then
        local count
        count=$(find "$dir" -mindepth 1 | wc -l | tr -d ' ')
        dry "$dir/* ($count items)"
    else
        find "$dir" -mindepth 1 -delete
        log "cleared $dir"
    fi
}

echo ""
echo "========================================"
echo "  ST SyncTool — pre-beta clear"
$DRY && echo -e "  ${YELLOW}DRY RUN — pass --go to delete${RESET}"
echo "========================================"
echo ""

# ── Local: current paths.py layout ───────────────────────────────────────────

# Report dirs (human-readable logs)
remove_contents "${BASE}/Offload Reports"
remove_contents "${BASE}/Transfer Reports"
remove_contents "${BASE}/Verify Reports"

# Manifests (all dev/test)
remove_contents "${BASE}/Manifests"

# Contact sheets
remove_contents "${BASE}/Contact Sheets"

# App state
remove "${APP_STATE}/log_sync_ledger.json"
remove "${APP_STATE}/upload_tally.json"
remove "${APP_STATE}/scheduled_verify_state.json"
remove_contents "${APP_STATE}/activity"
remove_contents "${APP_STATE}/activity-cache"

# ── Local: legacy dirs from earlier layouts (safe to nuke entirely) ───────────
remove "${BASE}/activity"
remove "${BASE}/logs"
remove "${BASE}/offload_logs"
remove "${BASE}/contact_sheets"
remove "${BASE}/manifests"

# ── Local: project registry ───────────────────────────────────────────────────
if [[ -f "${BASE}/projects.json" ]]; then
    if $DRY; then
        dry "${BASE}/projects.json"
    else
        echo '{}' > "${BASE}/projects.json"
        log "reset projects.json to {}"
    fi
fi

# ── Org Drive (rclone) ────────────────────────────────────────────────────────
echo ""
echo "----------------------------------------"
echo "  Org Drive (manual step)"
echo "----------------------------------------"
echo ""
echo "  Run these after confirming your remote name with:"
echo "    rclone listremotes"
echo ""
echo "  Then (replace 'gdrive:STSyncTool' with your actual remote base):"
echo ""
echo "    rclone delete 'gdrive:STSyncTool/Offload Reports'  --rmdirs"
echo "    rclone delete 'gdrive:STSyncTool/Transfer Reports' --rmdirs"
echo "    rclone delete 'gdrive:STSyncTool/Verify Reports'   --rmdirs"
echo "    rclone delete 'gdrive:STSyncTool/Manifests'        --rmdirs"
echo "    rclone delete 'gdrive:STSyncTool/.app-state/activity' --rmdirs"
echo ""
echo "  Or nuke the whole STSyncTool folder and let the app recreate it:"
echo "    rclone purge 'gdrive:STSyncTool'"
echo ""

echo ""
if $DRY; then
    echo -e "${YELLOW}Dry run complete. Re-run with --go to apply.${RESET}"
else
    echo -e "${GREEN}Local clear complete.${RESET}"
fi
echo ""
