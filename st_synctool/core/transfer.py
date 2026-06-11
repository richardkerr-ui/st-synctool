import os, shutil, subprocess, zipfile, getpass, socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional
from core.checksum import compute_all
from core.manifest import generate_manifest, save_manifest
from utils.file_utils import folder_size, free_space, format_bytes
from utils.gdrive_utils import is_gdrive_url

GDRIVE_DAILY_LIMIT_BYTES = 750 * 1024 ** 3

class TransferError(Exception): pass
class TransferWarning(Exception): pass

def estimate_time_seconds(size_bytes, speed_mbps=150.0):
    return size_bytes / (speed_mbps * 1024 * 1024)

def pre_flight_checks(source, destination, is_gdrive_dest=False, log_cb=None):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    summary = {}
    src_path = Path(source) if not is_gdrive_url(str(source)) else None
    if src_path:
        total = folder_size(src_path)
        summary["source_size"] = total
        secs = estimate_time_seconds(total)
        h=int(secs//3600); m2=int((secs%3600)//60); s=int(secs%60)
        summary["estimated_human"] = f"{h}h {m2}m {s}s" if h else f"{m2}m {s}s"
        log(f"Source size: {format_bytes(total)} — est. {summary['estimated_human']} @ 150 MB/s")
        if is_gdrive_dest and total > GDRIVE_DAILY_LIMIT_BYTES:
            raise TransferError(
                f"Source is {format_bytes(total)}, which exceeds the Google Drive 750 GB/day upload limit.\n"
                "Please contact a Signal Theory Productions lead to schedule a direct CloudSync on Synology instead."
            )
    dst_path = Path(destination) if not is_gdrive_url(str(destination)) else None
    if dst_path and src_path:
        dst_path.mkdir(parents=True, exist_ok=True)
        free = free_space(dst_path)
        total_disk = shutil.disk_usage(dst_path).total
        used_after = sum(f.stat().st_size for f in dst_path.rglob("*") if f.is_file()) if dst_path.exists() else 0
        pct_after = (used_after + total) / total_disk * 100
        if free < total:
            raise TransferError(f"Not enough space. Need {format_bytes(total)}, only {format_bytes(free)} free.")
        if pct_after > 90:
            raise TransferWarning(f"⚠ Destination will be {pct_after:.1f}% full after transfer.")
        log(f"Destination free: {format_bytes(free)} — OK")
    return summary

def copy_file(src, dst, log_cb=None, progress_cb=None, gdrive_mode=False):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    dst = Path(dst); dst.parent.mkdir(parents=True, exist_ok=True)
    log(f"  → Hashing source: {Path(src).name}")
    pre = compute_all(Path(src), include_xxhash=not gdrive_mode, include_md5=gdrive_mode,
                      progress_cb=lambda p: progress_cb(p//2) if progress_cb else None)
    shutil.copy2(src, dst)
    log(f"  → Verifying destination: {dst.name}")
    post = compute_all(dst, include_xxhash=not gdrive_mode, include_md5=gdrive_mode,
                       progress_cb=lambda p: progress_cb(50+p//2) if progress_cb else None)
    key = "md5" if gdrive_mode else "sha256"
    if pre.get(key) != post.get(key):
        raise TransferError(f"Checksum mismatch after copy! {key}: {pre.get(key)} vs {post.get(key)}")
    log(f"  ✓ Verified {Path(src).name}", "success")
    return {"source_checksums": pre, "dest_checksums": post, "verified": True}

def resolve_folder_conflict(src: Path, dst: Path):
    same = src.name == dst.name
    return (dst if same else dst / src.name), same

def transfer_folder(src, dst, gdrive_mode=False, log_cb=None, progress_cb=None, conflict_handler="skip"):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    src=Path(src); dst=Path(dst)
    actual_dest, same_name = resolve_folder_conflict(src, dst)
    actual_dest.mkdir(parents=True, exist_ok=True)
    files = [p for p in src.rglob("*") if p.is_file()]
    total = len(files); records = []; errors = []
    for i, fpath in enumerate(files):
        rel = fpath.relative_to(src); dest_file = actual_dest / rel
        if dest_file.exists():
            if conflict_handler=="skip": log(f"  ⊘ Skipped: {rel}","warning"); continue
            elif conflict_handler=="rename":
                dest_file = dest_file.with_name(f"{dest_file.stem}_conflict{dest_file.suffix}")
        if progress_cb: progress_cb(int(i/total*100), fpath.name)
        try:
            r = copy_file(fpath, dest_file, log_cb=log_cb, gdrive_mode=gdrive_mode)
            r.update({"source_path":str(fpath),"dest_path":str(dest_file),"filename":fpath.name,"size":fpath.stat().st_size})
            records.append(r)
        except Exception as e:
            log(f"  ✗ {fpath.name}: {e}","error"); errors.append({"file":str(fpath),"error":str(e)})
    if progress_cb: progress_cb(100, "Building manifest…")
    manifest = {
        "schema_version":"1.0","created_at":datetime.now(timezone.utc).isoformat(),
        "workstation":socket.gethostname(),"user":getpass.getuser(),
        "source_root":str(src),"dest_root":str(actual_dest),
        "same_name_merge":same_name,"gdrive_mode":gdrive_mode,
        "file_count":len(records),"error_count":len(errors),
        "files":{r["filename"]:r for r in records},"errors":errors,
    }
    saved = save_manifest(manifest, source_dir=src, dest_dir=actual_dest, name_hint=src.name)
    log(f"  ✓ Manifest saved to {len(saved)} locations")
    return {"manifest":manifest,"saved_manifest_paths":[str(p) for p in saved],
            "errors":errors,"actual_dest":str(actual_dest),"same_name":same_name}

def extract_multipart_zip(zip_dir: Path, log_cb=None):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    extracted = []
    for z in sorted(Path(zip_dir).glob("*.zip")):
        out = z.parent / z.stem
        try:
            with zipfile.ZipFile(z) as zf: zf.extractall(out)
            log(f"  ✓ Extracted {z.name} → {out.name}"); extracted.append(out)
        except zipfile.BadZipFile: log(f"  ✗ Bad zip: {z.name}","error")
    return extracted
