import hashlib, xxhash
from pathlib import Path
from typing import Callable, Optional

CHUNK = 8 * 1024 * 1024

def compute_all(path: Path, include_xxhash=True, include_md5=False,
                progress_cb: Optional[Callable[[int], None]] = None) -> dict:
    h_sha = hashlib.sha256()
    h_xx  = xxhash.xxh3_64() if include_xxhash else None
    h_md5 = hashlib.md5()    if include_md5    else None
    size = path.stat().st_size; done = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h_sha.update(chunk)
            if h_xx:  h_xx.update(chunk)
            if h_md5: h_md5.update(chunk)
            done += len(chunk)
            if progress_cb: progress_cb(int(done / size * 100) if size else 100)
    result = {"sha256": h_sha.hexdigest()}
    if h_xx:  result["xxhash3_64"] = h_xx.hexdigest()
    if h_md5: result["md5"]        = h_md5.hexdigest()
    return result
