import shutil
from pathlib import Path

def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())

def free_space(path: Path) -> int:
    return shutil.disk_usage(path).free

def format_bytes(n: int) -> str:
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024: return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
