"""M7.5 — Update checker.

On launch the app asks GitHub for the latest published release and, if it is
newer than the running version, surfaces a dismissible banner with a download
link. No auto-update, no background daemon — just awareness. The check is best
effort: a 5-second timeout and total silence on any failure (offline, rate
limit, malformed response), so it can never delay or disrupt startup.

Pure logic, no PyQt6. The network fetch is injected (`fetch_fn`) so the whole
flow is unit-testable with mocked responses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from core.version import __version__ as APP_VERSION

GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/richardkerr-ui/st-synctool/releases/latest"
)
REQUEST_TIMEOUT_SECONDS = 5

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text) -> Optional[tuple]:
    """Parse a semver-ish string ('v1.2.3', '1.2.3', '1.2.3-beta') to (1, 2, 3).

    Returns None when no `major.minor.patch` triple is present, so malformed
    inputs are rejected rather than silently treated as 0.0.0.
    """
    if not text:
        return None
    m = _VERSION_RE.search(str(text))
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


def is_newer(latest, current) -> bool:
    """True iff `latest` is a strictly higher version than `current`.

    Unparseable inputs make this return False — we never nag on a version we
    can't understand.
    """
    lv = parse_version(latest)
    cv = parse_version(current)
    if lv is None or cv is None:
        return False
    return lv > cv


@dataclass(frozen=True)
class UpdateInfo:
    version: str        # the latest tag, e.g. "v1.2.0"
    url: str            # release page to open


def parse_release(payload) -> Optional[UpdateInfo]:
    """Extract version + download URL from a GitHub release JSON object.

    Returns None for anything malformed (not a dict, missing/blank tag), so a
    bad response is indistinguishable from 'no update' to the caller.
    """
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name") or payload.get("name")
    if not tag or not str(tag).strip():
        return None
    url = (payload.get("html_url")
           or "https://github.com/richardkerr-ui/st-synctool/releases/latest")
    return UpdateInfo(version=str(tag).strip(), url=str(url))


def _default_fetch(url: str, timeout: int):
    import requests
    resp = requests.get(url, timeout=timeout,
                        headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    return resp.json()


def check_for_update(
    current: str = APP_VERSION,
    *,
    fetch_fn: Optional[Callable] = None,
    url: str = GITHUB_LATEST_RELEASE_URL,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Optional[UpdateInfo]:
    """Return UpdateInfo when a newer release exists, else None.

    Silent on every failure mode (network error, timeout, non-200, malformed
    JSON, unparseable version): returns None so startup is never disrupted.
    `fetch_fn(url, timeout) -> parsed JSON` is injected for tests.
    """
    fetch = fetch_fn or _default_fetch
    try:
        payload = fetch(url, timeout)
    except Exception:
        return None
    info = parse_release(payload)
    if info is None:
        return None
    if is_newer(info.version, current):
        return info
    return None


def update_banner_text(info: UpdateInfo, current: str = APP_VERSION) -> str:
    """Human-readable banner string for an available update."""
    return (f"Update available: {info.version} (you have v{current}). "
            f"Click to download.")
