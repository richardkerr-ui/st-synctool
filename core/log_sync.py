"""M9.1 — Ship local activity logs to a shared Drive folder.

Every offload / transfer / merge / verify writes its custody log and manifest
locally first; the local copy is the source of truth and never depends on the
network. This module copies those files (append-only, never deleting anything
remotely) to a shared Drive folder so the org gets a single view of all
production activity. Offline is the normal case: a "shipped" ledger records what
is confirmed uploaded, and anything not in the ledger is retried at the next
trigger (after each operation and on every app launch).

Layout on the remote mirrors the local folders, namespaced per machine + user:
    {remote_base}/{workstation}/{user}/{relpath}

Pure logic, no PyQt6. The rclone copy is injected (`copy_fn`) so the whole flow
is testable without a real rclone or network. The current time is injectable so
the 7-day pending threshold can be tested across day boundaries.

INVARIANT (M9.1): this module must NEVER delete files, locally or remotely. The
activity log is append-only and shipping is copy-only — the only rclone verb it
issues is a single-file copy. Any delete call added here is a data-loss bug.
"""

from __future__ import annotations

import getpass
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from core import rclone_bridge

from core import paths as _paths
STSYNC_DIR = _paths.base_dir()
LEDGER_PATH = _paths.log_sync_ledger_path()

# Local subdirectories whose files are shipped (logs + manifest archive +
# the per-machine activity shard written by core/activity_index.py).
SHIP_SUBDIRS = _paths.SHIP_SUBDIRS

# A file pending this many days escalates from the passive status line to a
# gentle banner (still never a popup).
PENDING_BANNER_DAYS = 7


def _workstation() -> str:
    return socket.gethostname()


def _user() -> str:
    return getpass.getuser()


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #

def _read_ledger(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        if isinstance(data, dict):
            data.setdefault("shipped", {})
            data.setdefault("pending_since", {})
            return data
    except (OSError, ValueError):
        pass
    return {"shipped": {}, "pending_since": {}}


def _write_ledger(path: Path, ledger: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ledger, indent=2))
    tmp.replace(path)


def _file_key(rel_path: str, size: int) -> str:
    """A file is 'shipped' identity = its rel path + size. Filenames here are
    timestamped and unique (custody logs carry a random suffix, manifests carry
    a timestamp), so a path collision with a different size means a new file."""
    return f"{rel_path}|{size}"


# --------------------------------------------------------------------------- #
# enumeration
# --------------------------------------------------------------------------- #

def enumerate_shippable(base_dir=STSYNC_DIR, subdirs=SHIP_SUBDIRS) -> list:
    """Return [(rel_path, abs_path, size)] for every file under the ship subdirs.

    rel_path is POSIX, relative to base_dir, so it maps cleanly onto the remote.
    """
    base = Path(base_dir)
    out: list = []
    for sub in subdirs:
        root = base / sub
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out.append((p.relative_to(base).as_posix(), p, p.stat().st_size))
    return out


def pending_files(base_dir=STSYNC_DIR, ledger: Optional[dict] = None,
                  subdirs=SHIP_SUBDIRS) -> list:
    """Files present locally but not yet confirmed in the ledger."""
    ledger = ledger if ledger is not None else _read_ledger(LEDGER_PATH)
    shipped = ledger.get("shipped", {})
    return [
        (rel, abs_path, size)
        for rel, abs_path, size in enumerate_shippable(base_dir, subdirs)
        if _file_key(rel, size) not in shipped
    ]


# --------------------------------------------------------------------------- #
# shipping
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ShipResult:
    shipped: int
    failed: int
    pending: int   # still pending after this run (== failed)

    @property
    def all_clear(self) -> bool:
        return self.pending == 0


def _remote_dst(remote_base: str, rel_path: str,
                workstation: str, user: str) -> str:
    base = remote_base.rstrip("/")
    return f"{base}/{workstation}/{user}/{rel_path}"


def ship_logs(
    remote_base: str,
    *,
    base_dir=STSYNC_DIR,
    ledger_path=LEDGER_PATH,
    subdirs=SHIP_SUBDIRS,
    copy_fn: Optional[Callable] = None,
    now: Optional[datetime] = None,
    workstation: Optional[str] = None,
    user: Optional[str] = None,
    log_cb: Optional[Callable] = None,
) -> ShipResult:
    """Copy every pending file to the remote, recording successes in the ledger.

    Append-only: this function never deletes anything, locally or remotely — the
    only rclone verb it issues is a single-file copy. A file that fails to copy
    stays pending (its first-seen time recorded) and is retried next trigger.
    Fully silent-safe: a copy raising is caught and counted as failed, never
    propagated, so shipping can never break the operation that triggered it.

    copy_fn(local_abs_path: str, remote_dst: str) -> None is injected for tests;
    it defaults to rclone_bridge.copyto with a raise-on-failure wrapper.
    """
    now = now or datetime.now()
    ws = workstation or _workstation()
    usr = user or _user()
    copy = copy_fn or _default_copy
    log = log_cb or (lambda m, l="info": None)

    # Snapshot read (unlocked) only to decide what to attempt. The authoritative
    # ledger update happens under a lock below, merging deltas rather than writing
    # back this snapshot — so two concurrent shippers never lose each other's
    # entries (M14.3). A file attempted twice just copies twice (idempotent).
    ledger = _read_ledger(ledger_path)
    todo = pending_files(base_dir, ledger, subdirs)
    n_shipped = n_failed = 0
    now_iso = now.isoformat()
    config_error_seen = False

    shipped_updates: dict = {}     # key -> shipped record (delta)
    pending_additions: dict = {}   # key -> first-seen iso for files that failed

    for rel, abs_path, size in todo:
        key = _file_key(rel, size)
        try:
            copy(str(abs_path), _remote_dst(remote_base, rel, ws, usr))
        except Exception as e:
            n_failed += 1
            pending_additions.setdefault(key, now_iso)  # remember when it first failed
            log(f"  Log shipping: deferred {rel} ({e})", "warning")
            if not _is_network_error(str(e)):
                config_error_seen = True
            continue
        shipped_updates[key] = {"rel": rel, "size": size, "shipped_at": now_iso}
        n_shipped += 1

    live_keys = {_file_key(r, s) for r, _, s in enumerate_shippable(base_dir, subdirs)}

    def _apply(current: dict) -> dict:
        current.setdefault("shipped", {})
        current.setdefault("pending_since", {})
        shipped = current["shipped"]
        pending_since = current["pending_since"]
        shipped.update(shipped_updates)
        for key in shipped_updates:
            pending_since.pop(key, None)
        for key, ts in pending_additions.items():
            pending_since.setdefault(key, ts)
        # Drop pending_since entries for files that no longer exist or are shipped.
        for k in list(pending_since):
            if k in shipped or k not in live_keys:
                pending_since.pop(k, None)
        # Only write last_attempt when the outcome is conclusive:
        # - Shipped something or had no failures: ok=True
        # - A non-network failure (bad remote name, auth, permissions): ok=False
        # - Pure network failures (offline): leave last_attempt untouched so a
        #   transient offline run never fires the "check remote config" hint.
        if n_shipped > 0 or n_failed == 0:
            current["last_attempt"] = {
                "at": now_iso, "ok": True, "shipped": n_shipped, "failed": n_failed,
            }
        elif config_error_seen:
            current["last_attempt"] = {
                "at": now_iso, "ok": False, "shipped": n_shipped, "failed": n_failed,
            }
        return current

    from core.file_lock import locked_json_update
    locked_json_update(ledger_path, _apply,
                       default={"shipped": {}, "pending_since": {}})
    if n_shipped:
        log(f"  Log shipping: uploaded {n_shipped} file(s)", "info")
    return ShipResult(shipped=n_shipped, failed=n_failed, pending=n_failed)


def ship_if_configured(
    *,
    base_dir=STSYNC_DIR,
    ledger_path=LEDGER_PATH,
    copy_fn: Optional[Callable] = None,
    now: Optional[datetime] = None,
    log_cb: Optional[Callable] = None,
    remote_base: Optional[str] = None,
    configured: Optional[bool] = None,
) -> Optional[ShipResult]:
    """M9.1 trigger: ship logs only when the org activity log is configured.

    Reads ``core.settings`` (a remote base is set and shipping is not opted out)
    unless ``configured``/``remote_base`` are passed directly (for tests). Returns
    the :class:`ShipResult`, or None when shipping is not configured (a no-op).
    Never raises — safe to fire on launch and after every operation.
    """
    from core import settings
    if configured is None:
        configured = settings.activity_log_configured()
    if not configured:
        return None
    base = remote_base if remote_base is not None else settings.activity_remote_base()
    if not base:
        return None
    try:
        return ship_logs(base, base_dir=base_dir, ledger_path=ledger_path,
                         copy_fn=copy_fn, now=now, log_cb=log_cb)
    except Exception as e:  # pragma: no cover - ship_logs is already silent-safe
        if log_cb:
            log_cb(f"  Log shipping skipped: {e}", "warning")
        return None


_NETWORK_ERROR_PATTERNS = (
    "dial tcp", "connection refused", "no such host", "i/o timeout",
    "connection reset", "context deadline exceeded",
    "temporary failure in name resolution", "network is unreachable",
    "tls handshake timeout", "eof",
)


def _is_network_error(msg: str) -> bool:
    m = msg.lower()
    return any(p in m for p in _NETWORK_ERROR_PATTERNS)


def _default_copy(local_abs: str, remote_dst: str) -> None:
    ok, stderr = rclone_bridge.copyto_result(local_abs, remote_dst)
    if not ok:
        raise RuntimeError(stderr or f"rclone copyto failed: {remote_dst}")


# --------------------------------------------------------------------------- #
# pending status (status line + 7-day banner)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PendingStatus:
    count: int
    oldest_age_days: int
    escalate: bool          # True when something has waited >= PENDING_BANNER_DAYS
    last_ok: Optional[bool] = None   # None = no attempt recorded yet
    last_at: Optional[str] = None    # ISO timestamp of last attempt

    def status_line(self) -> Optional[str]:
        if self.count == 0:
            return None
        noun = "report" if self.count == 1 else "reports"
        base = f"Activity log: {self.count} {noun} waiting to upload"
        if self.last_ok is False:
            return base + " — last upload failed, check remote config"
        return base

    def banner(self) -> Optional[str]:
        if not self.escalate:
            return None
        return (f"{self.count} activity report(s) have been waiting "
                f"{self.oldest_age_days}+ days to upload. Connect to the internet "
                f"so they can ship.")


def pending_status(
    base_dir=STSYNC_DIR,
    *,
    ledger_path=LEDGER_PATH,
    subdirs=SHIP_SUBDIRS,
    now: Optional[datetime] = None,
) -> PendingStatus:
    """Compute the passive status line + 7-day escalation from the ledger."""
    now = now or datetime.now()
    ledger = _read_ledger(ledger_path)
    todo = pending_files(base_dir, ledger, subdirs)
    pending_since = ledger.get("pending_since", {})

    oldest_days = 0
    for rel, _abs, size in todo:
        key = _file_key(rel, size)
        since_iso = pending_since.get(key)
        if not since_iso:
            continue
        try:
            since = datetime.fromisoformat(since_iso)
        except ValueError:
            continue
        age = (now - since).days
        oldest_days = max(oldest_days, age)

    last = ledger.get("last_attempt")
    last_ok = last.get("ok") if isinstance(last, dict) else None
    last_at = last.get("at") if isinstance(last, dict) else None

    return PendingStatus(
        count=len(todo),
        oldest_age_days=oldest_days,
        escalate=oldest_days >= PENDING_BANNER_DAYS,
        last_ok=last_ok,
        last_at=last_at,
    )
