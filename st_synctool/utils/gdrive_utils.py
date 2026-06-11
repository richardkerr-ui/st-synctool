import re
from typing import Optional

GDRIVE_PATTERNS = [
    r"https://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)",
    r"https://drive\.google\.com/drive/u/\d+/folders/([a-zA-Z0-9_-]+)",
    r"https://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)",
]

def is_gdrive_url(text: str) -> bool:
    return any(re.search(p, text) for p in GDRIVE_PATTERNS)

def parse_gdrive_id(url: str) -> Optional[str]:
    for p in GDRIVE_PATTERNS:
        m = re.search(p, url)
        if m: return m.group(1)
    return None

def get_clipboard_gdrive_url() -> Optional[str]:
    try:
        import pyperclip
        text = pyperclip.paste()
        if is_gdrive_url(text): return text.strip()
    except Exception: pass
    return None
