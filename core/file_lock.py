"""
core/file_lock.py — M14.3 atomic read-modify-write for small JSON state files.

Several state files (the log-sync ledger, the projects registry) are updated with
a read-modify-write cycle. tmp+rename gives a torn-write-free *write*, but it does
NOT prevent a **lost update**: worker A reads, worker B reads, A writes, B writes —
B silently drops A's changes. ``locked_json_update`` closes that window.

Concurrency model — **same-host multiprocess only.** The lock is ``flock`` on a
dedicated ``.lock`` sidecar. ``flock`` is reliable for multiple processes on one
machine. It is advisory on most NAS/SMB/NFS implementations and may silently fail
to coordinate across hosts — so the files this guards must live on the local
``~/Documents/STSyncTool`` tree, never on a shared volume. (The multi-DIT
shared-NAS case is handled by the per-host shard model M9.2 already uses for the
activity index, not by this lock.)

Why the sidecar and not the data file: ``flock(data_file); …; rename(tmp, data_file)``
locks the inode that ``rename`` unlinks. The next writer opens the new inode and
locks it immediately — both proceed. The ``.lock`` sidecar is never renamed or
replaced, so its inode is stable and locking it actually serialises writers.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Callable, Optional


def locked_json_update(path, fn: Callable[[dict], dict], *,
                       default: Optional[dict] = None) -> dict:
    """Atomically read-modify-write the JSON dict at ``path`` under a sidecar lock.

    Steps: open ``path + ".lock"``, ``flock(LOCK_EX)``, read ``path`` (or a copy of
    ``default`` / ``{}`` when absent or unparseable), apply ``fn(data) -> data``,
    write the result atomically via tmp+rename, then release the lock. Returns the
    written data.

    The ``.lock`` file is created if absent and is **never deleted** — its inode
    must stay stable so it keeps serialising writers across cycles.

    SAME-HOST-MULTIPROCESS ONLY: this does not protect against concurrent writes
    from separate machines to a shared filesystem (flock is advisory on NAS). Use
    only for files on the local STSyncTool tree.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")

    # "a+" creates the sidecar if absent without truncating an existing one.
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    data = json.loads(json.dumps(default)) if default is not None else {}
            else:
                data = json.loads(json.dumps(default)) if default is not None else {}

            new_data = fn(data)

            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(new_data, indent=2))
            tmp.replace(path)
            return new_data
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
