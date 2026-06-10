"""
System and rclone configuration checks for ST SyncTool.

Pure logic — no Qt imports. UI layer consumes CheckResult objects and
renders them however it wants.
"""

from __future__ import annotations

import os
import shutil
import sys
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from utils.gdrive_utils import RCLONE_REMOTE
from core.oauth_config import get_oauth_credentials


MIN_RCLONE_VERSION = (1, 60, 0)

REQUIRED_PYTHON_PACKAGES = [
    ("PyQt6", "PyQt6"),
    ("xxhash", "xxhash"),
    ("humanize", "humanize"),
    ("pyperclip", "pyperclip"),
    ("requests", "requests"),
]


class CheckStatus(Enum):
    OK = "ok"
    MISSING = "missing"
    ERROR = "error"
    WARNING = "warning"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    fix_hint: Optional[str] = None
    can_auto_fix: bool = False
    fix_command: Optional[List[str]] = field(default=None)

    @property
    def ok(self) -> bool:
        return self.status == CheckStatus.OK


def check_homebrew() -> CheckResult:
    if shutil.which("brew") is None:
        return CheckResult(
            name="Homebrew",
            status=CheckStatus.MISSING,
            message="Homebrew is not installed.",
            fix_hint=(
                "Homebrew can't be auto-installed safely from inside the app. "
                "Open Terminal and run the command at https://brew.sh, then "
                "re-launch ST SyncTool."
            ),
            can_auto_fix=False,
        )
    try:
        out = subprocess.run(
            ["brew", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        version_line = out.stdout.splitlines()[0] if out.stdout else "unknown"
        return CheckResult(
            name="Homebrew",
            status=CheckStatus.OK,
            message=version_line,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(
            name="Homebrew",
            status=CheckStatus.ERROR,
            message=f"brew is on PATH but failed to run: {e}",
        )


def check_rclone() -> CheckResult:
    if shutil.which("rclone") is None:
        return CheckResult(
            name="rclone",
            status=CheckStatus.MISSING,
            message="rclone is not installed.",
            fix_hint="Click Install to run `brew install rclone`.",
            can_auto_fix=True,
            fix_command=["brew", "install", "rclone"],
        )
    try:
        out = subprocess.run(
            ["rclone", "version"],
            capture_output=True, text=True, timeout=5,
        )
        first_line = out.stdout.splitlines()[0] if out.stdout else ""
        version = _parse_rclone_version(first_line)
        if version is None:
            return CheckResult(
                name="rclone",
                status=CheckStatus.WARNING,
                message=f"Installed but version could not be parsed: {first_line}",
            )
        if version < MIN_RCLONE_VERSION:
            v_str = ".".join(str(n) for n in version)
            min_str = ".".join(str(n) for n in MIN_RCLONE_VERSION)
            return CheckResult(
                name="rclone",
                status=CheckStatus.WARNING,
                message=f"Installed v{v_str}, but v{min_str}+ recommended.",
                fix_hint="Click Update to run `brew upgrade rclone`.",
                can_auto_fix=True,
                fix_command=["brew", "upgrade", "rclone"],
            )
        return CheckResult(
            name="rclone",
            status=CheckStatus.OK,
            message=first_line,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(
            name="rclone",
            status=CheckStatus.ERROR,
            message=f"rclone is on PATH but failed to run: {e}",
        )


def _parse_rclone_version(line: str) -> Optional[tuple]:
    import re
    m = re.search(r"v(\d+)\.(\d+)\.(\d+)", line)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def check_python_packages() -> CheckResult:
    missing = []
    for display_name, import_name in REQUIRED_PYTHON_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(display_name)
    if not missing:
        return CheckResult(
            name="Python packages",
            status=CheckStatus.OK,
            message=f"All {len(REQUIRED_PYTHON_PACKAGES)} required packages installed.",
        )
    return CheckResult(
        name="Python packages",
        status=CheckStatus.MISSING,
        message=f"Missing: {', '.join(missing)}",
        fix_hint="Click Install to run `pip3 install -r requirements.txt`.",
        can_auto_fix=True,
        fix_command=[sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
    )


def check_rclone_remote(name: str = RCLONE_REMOTE) -> CheckResult:
    try:
        out = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(
            name=f"rclone remote '{name}'",
            status=CheckStatus.ERROR,
            message=f"Could not list remotes: {e}",
        )
    remotes = [line.strip().rstrip(":") for line in out.stdout.splitlines() if line.strip()]
    if name in remotes:
        others = [r for r in remotes if r != name]
        return CheckResult(
            name=f"rclone remote '{name}'",
            status=CheckStatus.OK,
            message=f"Configured. Other remotes: {', '.join(others) or 'none'}",
        )
    return CheckResult(
        name=f"rclone remote '{name}'",
        status=CheckStatus.MISSING,
        message=f"No rclone remote named '{name}'. Configured: {', '.join(remotes) or 'none'}",
        fix_hint="The setup wizard will create this for you.",
        can_auto_fix=False,
    )


def check_rclone_auth(name: str = RCLONE_REMOTE, timeout: int = 15) -> CheckResult:
    try:
        result = subprocess.run(
            ["rclone", "lsd", f"{name}:", "--max-depth", "1"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name=f"'{name}' authentication",
            status=CheckStatus.ERROR,
            message=f"Auth check timed out after {timeout}s. Network or auth issue.",
            fix_hint="Re-run the setup wizard to re-authenticate.",
        )
    except OSError as e:
        return CheckResult(
            name=f"'{name}' authentication",
            status=CheckStatus.ERROR,
            message=f"Could not run rclone: {e}",
        )

    if result.returncode == 0:
        folder_count = len([l for l in result.stdout.splitlines() if l.strip()])
        return CheckResult(
            name=f"'{name}' authentication",
            status=CheckStatus.OK,
            message=f"Authenticated. {folder_count} top-level folders visible.",
        )

    stderr = result.stderr.strip()
    return CheckResult(
        name=f"'{name}' authentication",
        status=CheckStatus.ERROR,
        message=f"Auth failed: {stderr[:200] if stderr else 'unknown error'}",
        fix_hint="OAuth token has likely expired. Re-run the setup wizard.",
    )


def run_all_checks(remote_name: str = RCLONE_REMOTE) -> List[CheckResult]:
    results = [
        check_homebrew(),
        check_rclone(),
        check_python_packages(),
    ]
    rclone_ok = results[1].status in (CheckStatus.OK, CheckStatus.WARNING)
    if rclone_ok:
        remote_result = check_rclone_remote(remote_name)
        results.append(remote_result)
        if remote_result.ok:
            results.append(check_rclone_auth(remote_name))
    return results


def create_gdrive_remote(
    name: str = RCLONE_REMOTE,
    shared_drive: bool = False,
    timeout: int = 300,
) -> CheckResult:
    _cid, _csec = get_oauth_credentials()
    cmd = [
        "rclone", "config", "create", name, "drive",
        f"client_id={_cid}",
        f"client_secret={_csec}",
        "scope=drive",
        "config_is_local=true",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="Create remote",
            status=CheckStatus.ERROR,
            message="Timed out waiting for OAuth. Did you complete sign-in in the browser?",
        )
    except OSError as e:
        return CheckResult(
            name="Create remote",
            status=CheckStatus.ERROR,
            message=f"Failed to run rclone: {e}",
        )

    if result.returncode == 0:
        return CheckResult(
            name="Create remote",
            status=CheckStatus.OK,
            message=f"Remote '{name}' created successfully.",
        )
    return CheckResult(
        name="Create remote",
        status=CheckStatus.ERROR,
        message=f"rclone returned {result.returncode}: {result.stderr.strip()[:300]}",
    )
