import xxhash
from pathlib import Path
from typing import Callable, Optional

CHUNK = 8 * 1024 * 1024

def compute_all(path: Path, include_xxh128=True, include_md5=False,
                progress_cb: Optional[Callable[[int], None]] = None) -> dict:
    """Hash a file using xxh128 (primary) and optionally md5 (for GDrive).

    Transfer-mode conventions:
      local → local  : include_xxh128=True,  include_md5=False
      local → GDrive : include_xxh128=True,  include_md5=True
      GDrive → GDrive: rclone handles natively; call with include_xxh128=False, include_md5=True
    """
    h_xx  = xxhash.xxh128() if include_xxh128 else None
    h_md5 = __import__("hashlib").md5() if include_md5 else None
    size = path.stat().st_size; done = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if h_xx:  h_xx.update(chunk)
            if h_md5: h_md5.update(chunk)
            done += len(chunk)
            if progress_cb: progress_cb(int(done / size * 100) if size else 100)
    result = {}
    if h_xx:  result["xxhash128"] = h_xx.hexdigest()
    if h_md5: result["md5"]       = h_md5.hexdigest()
    return result
