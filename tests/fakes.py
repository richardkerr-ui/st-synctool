"""Test doubles for the rclone I/O seam (core.rclone_bridge.set_rclone_runner).

FakeRclone is an in-memory stand-in for the rclone binary. Install it with
``rclone_bridge.set_rclone_runner(FakeRclone())`` and the whole app's Drive
surface (copyto, lsjson, find_activity_shards, size/path_exists …) runs against
a dict-backed remote — no network, no real rclone. Lets the log-shipping,
org-refresh and verify paths be integration-tested end to end.
"""

import json
from pathlib import Path


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRclone:
    """A minimal, faithful rclone backend over an in-memory remote filesystem.

    Remote paths are anything containing ':' (e.g. ``gdrive:Acts/...``); local
    paths are real filesystem paths. ``copyto`` moves bytes in either direction.
    """

    def __init__(self):
        self.store = {}        # remote_path -> bytes
        self.commands = []     # log of arg lists, for assertions

    # rclone runner protocol: (args, timeout, log_cb, progress_cb) -> result
    def __call__(self, args, timeout=None, log_cb=None, progress_cb=None):
        self.commands.append(list(args))
        cmd = args[0]
        if cmd == "copyto":
            return self._copyto(args[1], args[2])
        if cmd == "lsjson":
            return self._lsjson(self._last_positional(args))
        if cmd == "size":
            return self._size(self._last_positional(args))
        if cmd == "deletefile":
            self.store.pop(args[1], None)
            return _Result(0)
        return _Result(0)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _is_remote(p: str) -> bool:
        return ":" in p and not p.startswith("/")

    @staticmethod
    def _last_positional(args):
        for a in reversed(args):
            if not a.startswith("-"):
                return a
        return ""

    def _copyto(self, src, dst):
        if self._is_remote(src):
            data = self.store.get(src)
            if data is None:
                return _Result(1, stderr=f"not found: {src}")
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(data)
        else:
            self.store[dst] = Path(src).read_bytes()
        return _Result(0)

    def _lsjson(self, base):
        prefix = base.rstrip("/") + "/"
        entries = [{"Path": k[len(prefix):], "IsDir": False, "Size": len(v)}
                   for k, v in self.store.items() if k.startswith(prefix)]
        return _Result(0, stdout=json.dumps(entries))

    def _size(self, path):
        if path in self.store or any(k.startswith(path.rstrip("/") + "/")
                                     for k in self.store):
            return _Result(0, stdout=json.dumps({"count": 1, "bytes": 0}))
        return _Result(1, stderr="directory not found")
