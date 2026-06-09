import os
import re
import subprocess
from typing import Optional, List, Tuple

GDRIVE_PATTERNS = [
    r"https://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)",
    r"https://drive\.google\.com/drive/u/\d+/folders/([a-zA-Z0-9_-]+)",
    r"https://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)",
]

_DEFAULT_REMOTE_NAME = "gdrive"
_ENV_OVERRIDE = "ST_SYNC_RCLONE_REMOTE"


def _detect_rclone_remote() -> str:
    """
    Resolve which rclone remote to use, in order of priority:
      1. ST_SYNC_RCLONE_REMOTE environment variable
      2. A remote literally named "gdrive"
      3. The first remote returned by `rclone listremotes`
      4. Fallback to "gdrive" (will surface a clear error if it doesn't exist)
    """
    override = os.environ.get(_ENV_OVERRIDE, "").strip().rstrip(":")
    if override:
        return override
    try:
        r = subprocess.run(
            ["rclone", "listremotes"],
            capture_output=True, text=True, timeout=5,
        )
        remotes = [
            line.strip().rstrip(":")
            for line in r.stdout.splitlines()
            if line.strip()
        ]
        if _DEFAULT_REMOTE_NAME in remotes:
            return _DEFAULT_REMOTE_NAME
        if remotes:
            return remotes[0]
    except Exception:
        pass
    return _DEFAULT_REMOTE_NAME


RCLONE_REMOTE = _detect_rclone_remote()


def is_gdrive_url(text: str) -> bool:
    if not text:
        return False
    return any(re.search(pat, text) for pat in GDRIVE_PATTERNS)


def parse_gdrive_id(url: str) -> Optional[str]:
    for pat in GDRIVE_PATTERNS:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def gdrive_url_to_rclone(url: str) -> Tuple[str, List[str]]:
    fid = parse_gdrive_id(url)
    if not fid:
        raise ValueError(f"Not a recognizable Google Drive folder URL: {url}")
    return f"{RCLONE_REMOTE}:", ["--drive-root-folder-id", fid]


def get_clipboard_gdrive_url() -> Optional[str]:
    try:
        import pyperclip
        text = pyperclip.paste()
        if is_gdrive_url(text):
            return text.strip()
    except Exception:
        pass
    return None
