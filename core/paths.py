"""Single source of truth for the on-disk layout under ``~/Documents/STSyncTool``.

Every module resolves its directories through here instead of hardcoding paths,
so the layout is defined in one place and the whole tree can be redirected for
tests by setting the ``ST_SYNC_HOME`` environment variable (the test suite points
it at a tmp dir, so tests never write into the real Documents folder).

Human-readable layout (M-cleanup 2026-06-13):

    STSyncTool/
        Offload Reports/      chain-of-custody logs, grouped by date
        Verify Reports/       verify + batch-verify + scheduled-verify reports
        Transfer Reports/     transfer logs
        Contact Sheets/       thumbnail contact-sheet PDFs
        Manifests/            per-project st_manifest archives
        projects.json         the project registry
        .app-state/           hidden machine state (not user-facing)
            activity/         per-machine activity shards (shipped to the org)
            activity-cache/   other machines' shards pulled for the History view
            upload_tally.json, log_sync_ledger.json, scheduled_verify_state.json
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "ST_SYNC_HOME"

# Human-readable subdir names (also the relpaths shipped to the org Drive folder).
OFFLOAD_REPORTS = "Offload Reports"
VERIFY_REPORTS = "Verify Reports"
TRANSFER_REPORTS = "Transfer Reports"
CONTACT_SHEETS = "Contact Sheets"
MANIFESTS = "Manifests"
APP_STATE = ".app-state"
ACTIVITY = f"{APP_STATE}/activity"
ACTIVITY_CACHE = f"{APP_STATE}/activity-cache"


def base_dir() -> Path:
    """Root of the on-disk tree. Overridable via ``ST_SYNC_HOME`` (tests/CI)."""
    override = os.environ.get(ENV_HOME)
    return Path(override) if override else Path.home() / "Documents" / "STSyncTool"


def offload_reports_dir() -> Path:
    return base_dir() / OFFLOAD_REPORTS


def verify_reports_dir() -> Path:
    return base_dir() / VERIFY_REPORTS


def transfer_reports_dir() -> Path:
    return base_dir() / TRANSFER_REPORTS


def contact_sheets_dir() -> Path:
    return base_dir() / CONTACT_SHEETS


def manifests_dir() -> Path:
    return base_dir() / MANIFESTS


def app_state_dir() -> Path:
    return base_dir() / APP_STATE


def activity_dir() -> Path:
    return base_dir() / ACTIVITY


def activity_cache_dir() -> Path:
    return base_dir() / ACTIVITY_CACHE


def projects_registry() -> Path:
    return base_dir() / "projects.json"


def upload_tally_path() -> Path:
    return app_state_dir() / "upload_tally.json"


def log_sync_ledger_path() -> Path:
    return app_state_dir() / "log_sync_ledger.json"


def scheduled_verify_state_path() -> Path:
    return app_state_dir() / "scheduled_verify_state.json"


# Subdirs shipped to the shared org folder (reports + manifests + the activity
# shard). Machine-only state under .app-state is never shipped.
SHIP_SUBDIRS = (OFFLOAD_REPORTS, VERIFY_REPORTS, TRANSFER_REPORTS, MANIFESTS, ACTIVITY)

# Subdirs a feedback bundle collects (human-readable report logs only).
FEEDBACK_SUBDIRS = (OFFLOAD_REPORTS, VERIFY_REPORTS, TRANSFER_REPORTS)
