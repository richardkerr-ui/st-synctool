import shutil, zipfile, getpass, socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional
from core.checksum import compute_all
from core.manifest import generate_manifest, save_manifest, SCHEMA_VERSION
from utils.file_utils import folder_size, free_space, format_bytes
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone, gdrive_url_to_connstr
from core import rclone_bridge, quota

GDRIVE_DAILY_LIMIT_BYTES = 750 * 1024 ** 3


class TransferError(Exception): pass
class TransferWarning(Exception): pass


SPEED_MBPS_LOCAL = 150.0   # NAS / local disk
SPEED_MBPS_DRIVE = 15.0    # WFH upload to Google Drive


def estimate_time_seconds(size_bytes, speed_mbps=SPEED_MBPS_LOCAL):
    return size_bytes / (speed_mbps * 1024 * 1024)


def pre_flight_checks(source, destination, is_gdrive_dest=False, log_cb=None):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)

    summary = {}
    src_is_url = is_gdrive_url(str(source))
    dst_is_url = is_gdrive_url(str(destination))

    total = None
    if src_is_url:
        # Ask rclone to size the remote folder. Slow but accurate.
        try:
            remote_path, flags = gdrive_url_to_rclone(str(source))
            log("Querying remote source size (this may take a moment)…")
            total, count = rclone_bridge.remote_size(remote_path, extra_flags=flags)
            summary["source_size"] = total
            summary["source_file_count"] = count
            secs = estimate_time_seconds(total)
            h = int(secs // 3600); m2 = int((secs % 3600) // 60); s = int(secs % 60)
            summary["estimated_human"] = f"{h}h {m2}m {s}s" if h else f"{m2}m {s}s"
            if dst_is_url:
                summary["server_side"] = True
                log(f"Remote source: {format_bytes(total)} across {count} file(s) "
                    f"— server-side copy (no local disk)")
                if total is not None and total > GDRIVE_DAILY_LIMIT_BYTES:
                    raise TransferError(
                        f"Source is {format_bytes(total)}, which exceeds the Google Drive "
                        "750 GB/day limit (it applies to server-side copies too).\n"
                        "Please contact a Signal Theory Productions lead to schedule "
                        "a direct CloudSync on Synology instead."
                    )
            else:
                log(f"Remote source: {format_bytes(total)} across {count} file(s) "
                    f"— est. {summary['estimated_human']} @ {SPEED_MBPS_LOCAL:.0f} MB/s (download)")
        except TransferError:
            raise
        except Exception as e:
            log(f"Could not determine remote source size: {e}", "warning")
    if not src_is_url:
        src_path = Path(source)
        if not src_path.exists():
            raise TransferError(f"Source does not exist: {src_path}")
        total = folder_size(src_path)
        summary["source_size"] = total
        speed_mbps = SPEED_MBPS_DRIVE if is_gdrive_dest else SPEED_MBPS_LOCAL
        secs = estimate_time_seconds(total, speed_mbps=speed_mbps)
        h = int(secs // 3600); m2 = int((secs % 3600) // 60); s = int(secs % 60)
        summary["estimated_human"] = f"{h}h {m2}m {s}s" if h else f"{m2}m {s}s"
        dest_label = "Drive upload" if is_gdrive_dest else "local"
        log(f"Source size: {format_bytes(total)} — est. {summary['estimated_human']} @ {speed_mbps:.0f} MB/s ({dest_label})")

        if is_gdrive_dest and total > GDRIVE_DAILY_LIMIT_BYTES:
            raise TransferError(
                f"Source is {format_bytes(total)}, which exceeds the Google Drive "
                "750 GB/day upload limit.\nPlease contact a Signal Theory Productions "
                "lead to schedule a direct CloudSync on Synology instead."
            )

    if not dst_is_url:
        dst_path = Path(destination)
        dst_path.mkdir(parents=True, exist_ok=True)
        free = free_space(dst_path)
        if total is not None:
            if free < total:
                raise TransferError(
                    f"Not enough space. Need {format_bytes(total)}, only {format_bytes(free)} free."
                )
            total_disk = shutil.disk_usage(dst_path).total
            if total_disk > 0:
                used_after = shutil.disk_usage(dst_path).used
                pct_after = (used_after + total) / total_disk * 100
            else:
                pct_after = 0
            if pct_after > 90:
                raise TransferWarning(f"Destination will be {pct_after:.1f}% full after transfer.")
        log(f"Destination free: {format_bytes(free)} — OK")

    return summary


def copy_file(src, dst, log_cb=None, progress_cb=None, gdrive_mode=False):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    dst = Path(dst); dst.parent.mkdir(parents=True, exist_ok=True)
    log(f"  Hashing source: {Path(src).name}")
    pre = compute_all(Path(src), include_xxhash=not gdrive_mode, include_md5=gdrive_mode,
                      progress_cb=lambda p: progress_cb(p // 2) if progress_cb else None)
    shutil.copy2(src, dst)
    log(f"  Verifying destination: {dst.name}")
    post = compute_all(dst, include_xxhash=not gdrive_mode, include_md5=gdrive_mode,
                       progress_cb=lambda p: progress_cb(50 + p // 2) if progress_cb else None)
    key = "md5" if gdrive_mode else "sha256"
    if pre.get(key) != post.get(key):
        raise TransferError(f"Checksum mismatch! {key}: {pre.get(key)} vs {post.get(key)}")
    log(f"  Verified {Path(src).name}", "success")
    return {"source_checksums": pre, "dest_checksums": post, "verified": True}




def _compute_local_hashes(local_root, log_cb=None):
    """Walk a local directory and compute SHA-256 for every file.
    Returns {relpath: sha256_lowercase}. Used in paranoid verification mode."""
    result = {}
    if not local_root.exists() or not local_root.is_dir():
        return result
    files = [f for f in local_root.rglob("*") if f.is_file()]
    if log_cb:
        log_cb(f"  [Paranoid] Hashing {len(files)} local file(s)...", "info")
    total_bytes = 0
    for f in files:
        rel = str(f.relative_to(local_root))
        try:
            total_bytes += f.stat().st_size
            cs = compute_all(f, include_xxhash=False, include_md5=False)
            sha = cs.get("sha256", "")
            if sha:
                result[rel] = sha.lower()
        except Exception as e:
            if log_cb:
                log_cb(f"  Hash failed for {rel}: {e}", "warning")
    if log_cb:
        # Format bytes inline to avoid importing format_bytes here
        gb = total_bytes / (1024 ** 3)
        log_cb(f"  [Paranoid] Hashed {len(result)} file(s), {gb:.2f} GiB total", "info")
    return result

def resolve_folder_conflict(src: Path, dst: Path):
    same = src.name == dst.name
    return (dst if same else dst / src.name), same


def _maybe_export_mhl(manifest, saved_paths, export_mhl, log):
    """M10.3: write an ASC MHL v2.0 .mhl next to each saved manifest, additively.

    A failure here is logged and swallowed so it can never fail the transfer."""
    if not export_mhl:
        return
    from core.asc_mhl import write_mhl, default_mhl_path
    for p in saved_paths:
        try:
            write_mhl(manifest, default_mhl_path(manifest, Path(p).parent))
        except Exception as exc:
            log(f"  MHL export skipped for {Path(p).parent}: {exc}", "warning")


def transfer_folder(src, dst, gdrive_mode=False, log_cb=None, progress_cb=None,
                    conflict_handler="skip", export_mhl=False, job_name=""):
    """Local-to-local transfer with per-file verification + manifest."""
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    src = Path(src); dst = Path(dst)
    actual_dest, same_name = resolve_folder_conflict(src, dst)
    actual_dest.mkdir(parents=True, exist_ok=True)
    files = [p for p in src.rglob("*") if p.is_file()]
    total = len(files); records = []; errors = []
    for i, fpath in enumerate(files):
        rel = fpath.relative_to(src); dest_file = actual_dest / rel
        if dest_file.exists():
            if conflict_handler == "skip":
                log(f"  Skipped: {rel}", "warning"); continue
            elif conflict_handler == "rename":
                dest_file = dest_file.with_name(f"{dest_file.stem}_conflict{dest_file.suffix}")
        if progress_cb: progress_cb(int(i / total * 100), fpath.name)
        try:
            r = copy_file(fpath, dest_file, log_cb=log_cb, gdrive_mode=gdrive_mode)
            fstat = fpath.stat()
            # MANIFEST-FIX: key manifest entries by relative POSIX path, not bare
            # filename. Every other writer (generate_manifest[_fast]) keys by
            # rel-path; keying by basename collapsed subdir/FILE_C.txt -> FILE_C.txt,
            # breaking comparison.three_way_diff and verify (both resolve folder/rel).
            rel_key = fpath.relative_to(src).as_posix()
            r.update({
                "source_path": str(fpath), "dest_path": str(dest_file),
                "filename": fpath.name, "rel_path": rel_key, "size": fstat.st_size,
                "modtime": datetime.fromtimestamp(fstat.st_mtime, tz=timezone.utc).isoformat(),
                "checksums": r.get("dest_checksums", {}),
                # MANIFEST-FIX: record the primary algorithm per file so a transfer
                # manifest used as a merge base does not rely on presence-based inference.
                "hash_algorithm": "md5" if gdrive_mode else "sha256",
                "verification_method": "local-copy",
                "gdrive_url": "",
            })
            records.append(r)
        except Exception as e:
            log(f"  Error {fpath.name}: {e}", "error")
            errors.append({"file": str(fpath), "error": str(e)})
    if progress_cb: progress_cb(100, "Building manifest...")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workstation": socket.gethostname(), "user": getpass.getuser(),
        # MANIFEST-FIX: include `root` display label like generate_manifest does,
        # so consumers reading a transfer manifest find the same top-level fields.
        "root": str(src),
        "source_root": str(src), "dest_root": str(actual_dest),
        "counterpart_path": str(actual_dest),
        "operation": "transfer",
        "project_id": "",
        "renames": [],
        "same_name_merge": same_name, "gdrive_mode": gdrive_mode,
        # MANIFEST-FIX: standardise checksum_context shape (algorithm, gdrive_mode,
        # method, paranoid_fallback_count) so every writer is interoperable.
        "checksum_context": {
            "algorithm": "md5" if gdrive_mode else "sha256",
            "gdrive_mode": gdrive_mode,
            "method": "local",
            "verification": "pre-post-copy",
            "paranoid_fallback_count": 0,
        },
        "file_count": len(records), "error_count": len(errors),
        # MANIFEST-FIX: key by rel_path (relative POSIX) so subdir files survive
        # round-trip into comparison/verify which both key by relative path.
        "files": {r["rel_path"]: r for r in records}, "errors": errors,
    }
    saved = save_manifest(manifest, source_dir=src, dest_dir=actual_dest, name_hint=src.name)
    log(f"  Manifest saved to {len(saved)} locations")
    _maybe_export_mhl(manifest, saved, export_mhl, log)

    # M9.2: record this job in the local per-machine activity index (local-only,
    # never raises). Shipping to the org folder is a separate step (M9.1).
    from core.activity_index import record_from_manifest, safe_append_activity
    _manifest_for_log = dict(manifest, label=job_name) if job_name else manifest
    safe_append_activity(
        record_from_manifest(
            _manifest_for_log, operation="transfer", source=src.name,
            dests=[actual_dest.name], verdict="COMPLETE" if not errors else "PARTIAL",
        ), log_cb=log_cb)

    return {
        "manifest": manifest, "saved_manifest_paths": [str(p) for p in saved],
        "errors": errors, "actual_dest": str(actual_dest), "same_name": same_name,
    }


def transfer_folder_rclone(src, dst, mirror_mode=False, conflict_handler="overwrite",
                           paranoid_verify=False, log_cb=None, progress_cb=None,
                           export_mhl=False, job_name=""):
    """Routes a transfer through rclone when either side is a Google Drive URL."""
    def log(m, l="info"):
        if log_cb: log_cb(m, l)

    if not rclone_bridge.is_rclone_installed():
        raise TransferError(
            "rclone is not installed. Install it with:\n  brew install rclone\n"
            "Then configure your Drive remote with:\n  rclone config"
        )

    src_is_url = is_gdrive_url(str(src))
    dst_is_url = is_gdrive_url(str(dst))

    src_flags = dst_flags = None
    if src_is_url and dst_is_url:
        # M3: Drive-to-Drive — rclone copies server-side, no local disk used.
        # Per-side root folders cannot be expressed with the global
        # --drive-root-folder-id flag, so each side gets a connection-string
        # remote instead ("gdrive,root_folder_id=<id>:").
        src_str = gdrive_url_to_connstr(str(src))
        dst_str = gdrive_url_to_connstr(str(dst))
        src_flags = ["--drive-server-side-across-configs"]
        label_root = dst_str
        log("Drive-to-Drive transfer: copying server-side between Drive folders; "
            "no local disk is used.", "info")
        if paranoid_verify:
            log("Paranoid verify is unavailable for Drive-to-Drive (no local files "
                "to hash); relying on Drive checksums instead.", "warning")
            paranoid_verify = False
    elif src_is_url:
        src_str, src_flags = gdrive_url_to_rclone(str(src))
        dst_str = str(Path(dst))
        Path(dst).mkdir(parents=True, exist_ok=True)
        label_root = src_str
    else:
        dst_str, dst_flags = gdrive_url_to_rclone(str(dst))
        src_str = str(Path(src))
        label_root = dst_str

    mode = "sync" if mirror_mode else "copy"
    log(f"Starting rclone {mode}: {src_str} -> {dst_str}  "
        f"(conflict: {conflict_handler})"
        + (" [PARANOID]" if paranoid_verify else ""), "info")

    # Paranoid mode: compute local source hashes BEFORE sync (Local -> Drive case)
    local_source_hashes = {}
    if paranoid_verify and not src_is_url:
        local_source_hashes = _compute_local_hashes(Path(src), log_cb=log)

    # Capture pre-sync destination state so we can label each file accurately
    pre_state = {}
    try:
        pre_items = rclone_bridge.lsjson(
            dst_str,
            extra_flags=(dst_flags if dst_is_url else None),
            with_checksum=True,
        )
        for item in pre_items:
            if not item.get("IsDir"):
                pre_state[item["Path"]] = {
                    "size": item.get("Size", 0),
                    "hashes": {k.lower(): v.lower() for k, v in item.get("Hashes", {}).items()},
                }
        log(f"  Pre-sync destination: {len(pre_state)} file(s) already present")
    except Exception as e:
        log(f"  Could not capture pre-sync state (treating dest as empty): {e}", "warning")

    if progress_cb: progress_cb(0, f"Starting rclone {mode}...")

    def _rclone_progress(pct, info):
        if progress_cb:
            mapped = min(90, int(pct * 0.9))
            # Pass the rich info dict through unchanged so the UI can display
            # speed, ETA, file counts and current filename.
            # Callers that only inspect the percent arg are unaffected.
            progress_cb(mapped, info)

    ok = rclone_bridge.sync(
        src_str, dst_str,
        mode=mode, conflict=conflict_handler,
        src_flags=src_flags, dst_flags=dst_flags,
        log_cb=log_cb, progress_cb=_rclone_progress,
    )
    if not ok:
        raise TransferError(f"rclone {mode} failed. See log for details.")

    # Paranoid mode: compute local destination hashes AFTER sync (Drive -> Local case)
    local_dest_hashes = {}
    if paranoid_verify and src_is_url:
        local_dest_hashes = _compute_local_hashes(Path(dst), log_cb=log)

    if progress_cb: progress_cb(95, "Building manifest from remote listing...")
    try:
        manifest = rclone_bridge.lsjson_to_manifest(
            label_root,
            extra_flags=(None if (src_is_url and dst_is_url)
                         else (src_flags if src_is_url else dst_flags)),
            label=f"rclone-{mode}",
        )
    except Exception as e:
        log(f"  Manifest generation warning: {e}", "warning")
        manifest = {"files": {}, "errors": []}

    # Normalize rclone manifest to match local-transfer schema for logging
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["source_root"] = str(src)
    manifest["dest_root"] = dst_str
    manifest["file_count"] = len(manifest.get("files", {}))
    # Store the original human-readable URLs (before rclone path conversion) — item 05
    manifest["source_url"] = str(src) if src_is_url else ""
    manifest["dest_url"]   = str(dst) if dst_is_url else ""
    manifest["counterpart_path"] = str(src) if src_is_url else str(dst) if dst_is_url else ""
    manifest["operation"] = "rclone-transfer"
    manifest["renames"] = []

    # Per-file status by diffing pre/post destination state, plus verification per mode
    status_counts = {"uploaded": 0, "updated": 0, "unchanged": 0}
    verify_failures = []
    paranoid_fallback_files = []
    normalized = {}
    for fpath, fdata in manifest.get("files", {}).items():
        drive_cs = fdata.get("checksums", {})

        # Verification source/dest hashes depend on direction and paranoid mode
        if paranoid_verify and not src_is_url:
            # Local -> Drive: src from local hash, dst from Drive lsjson
            src_sha = local_source_hashes.get(fpath, "")
            dst_sha = drive_cs.get("sha256", "")
            if src_sha and dst_sha:
                v_ok = (src_sha == dst_sha.lower())
                v_method = "paranoid"
            else:
                # Drive hasn't computed SHA-256 for this file yet
                v_ok = True
                v_method = "rclone-checksum"
                paranoid_fallback_files.append(fpath)
                src_sha = src_sha or dst_sha
                dst_sha = dst_sha or src_sha
            fdata["source_checksums"] = {"sha256": src_sha}
            fdata["dest_checksums"] = {"sha256": dst_sha}
        elif paranoid_verify and src_is_url:
            # Drive -> Local: src from Drive lsjson, dst from local hash
            src_sha = drive_cs.get("sha256", "")
            dst_sha = local_dest_hashes.get(fpath, "")
            if src_sha and dst_sha:
                v_ok = (src_sha.lower() == dst_sha)
                v_method = "paranoid"
            else:
                # Drive hasn't computed SHA-256 for this file yet
                v_ok = True
                v_method = "rclone-checksum"
                paranoid_fallback_files.append(fpath)
                src_sha = src_sha or dst_sha
                dst_sha = dst_sha or src_sha
            fdata["source_checksums"] = {"sha256": src_sha}
            fdata["dest_checksums"] = {"sha256": dst_sha}
        else:
            # Default: rclone --checksum verified; same hash on both sides
            fdata["source_checksums"] = drive_cs
            fdata["dest_checksums"] = drive_cs
            v_ok = True
            v_method = "rclone-checksum"

        fdata["verified"] = v_ok
        fdata["verification_method"] = v_method
        # MANIFEST-FIX: record the primary algorithm per file. Paranoid transfers
        # verify on SHA-256; non-paranoid relies on rclone's internal --checksum.
        if paranoid_verify and v_method == "paranoid":
            fdata["hash_algorithm"] = "sha256"
        elif "sha256" in drive_cs:
            fdata["hash_algorithm"] = "sha256"
        elif "md5" in drive_cs:
            fdata["hash_algorithm"] = "md5"
        elif "xxhash3_64" in drive_cs:
            fdata["hash_algorithm"] = "xxhash3_64"
        else:
            fdata["hash_algorithm"] = "rclone-checksum"
        fdata.setdefault("gdrive_url", "")
        fdata["filename"] = fpath
        if not v_ok:
            verify_failures.append(fpath)

        # Status diff vs pre-sync destination state
        if fpath not in pre_state:
            fdata["status"] = "uploaded"
        else:
            pre = pre_state[fpath]
            same = False
            for hkey in ("sha256", "md5", "sha1"):
                if hkey in drive_cs and hkey in pre["hashes"]:
                    same = (drive_cs[hkey].lower() == pre["hashes"][hkey])
                    break
            if same and fdata.get("size", 0) == pre["size"]:
                fdata["status"] = "unchanged"
            else:
                fdata["status"] = "updated"
        status_counts[fdata["status"]] += 1
        normalized[fpath] = fdata
    manifest["files"] = normalized
    manifest["verification_method"] = "paranoid" if paranoid_verify else "rclone-checksum"
    manifest["verify_failures"] = verify_failures
    # MANIFEST-FIX: standardise checksum_context shape — always expose `method`
    # and `gdrive_mode` alongside the rclone-specific paranoid fields.
    manifest["checksum_context"] = {
        "algorithm": "sha256" if paranoid_verify else "rclone-checksum",
        "method": "paranoid" if paranoid_verify else "rclone",
        "gdrive_mode": bool(src_is_url or dst_is_url),
        "paranoid": paranoid_verify,
        "paranoid_fallback_files": paranoid_fallback_files,
        "paranoid_fallback_count": len(paranoid_fallback_files),
    }
    if paranoid_fallback_files:
        log(f"  [Paranoid] {len(paranoid_fallback_files)} file(s) fell back to rclone-checksum "
            f"(Drive SHA-256 not yet computed)", "warning")
    if verify_failures:
        log(f"  [Paranoid] VERIFICATION FAILED for {len(verify_failures)} file(s)", "error")

    # Track deletions (mirror mode)
    post_paths = set(manifest["files"].keys())
    deleted = [p for p in pre_state if p not in post_paths]
    manifest["deleted_files"] = deleted
    manifest["status_counts"] = {
        "uploaded": status_counts["uploaded"],
        "updated":  status_counts["updated"],
        "unchanged": status_counts["unchanged"],
        "deleted":  len(deleted),
    }
    log(f"  Result: {status_counts['uploaded']} uploaded, "
        f"{status_counts['updated']} updated, "
        f"{status_counts['unchanged']} unchanged, "
        f"{len(deleted)} deleted")

    # M10.2: record an upload floor for the daily tally. Only counts bytes this
    # app actually pushed to Drive (uploaded/updated files to a Drive dest),
    # presented strictly as a floor since outside-app uploads are invisible.
    if dst_is_url:
        uploaded_bytes = sum(
            f.get("size", 0) for f in normalized.values()
            if f.get("status") in ("uploaded", "updated")
        )
        if uploaded_bytes > 0:
            quota.record_upload(uploaded_bytes)
            floor = quota.tally_floor_text()
            if floor:
                log(f"  {floor}")

    # Save manifest JSON next to local side (and central log dir)
    saved_paths = []
    try:
        if src_is_url and dst_is_url:
            # No local side: keep the central archive copy only
            saved_paths = save_manifest(manifest, name_hint="drive_to_drive")
            log(f"  Manifest saved to {len(saved_paths)} location(s) (archive)", "info")
            raise StopIteration  # skip the local-side save below
        local_side = Path(dst) if not dst_is_url else Path(src)
        if local_side.exists():
            name_hint = local_side.name if local_side.is_dir() else "rclone_transfer"
            saved_paths = save_manifest(
                manifest,
                source_dir=local_side if not src_is_url else None,
                dest_dir=local_side if not dst_is_url else None,
                name_hint=name_hint,
            )
            log(f"  Manifest saved to {len(saved_paths)} location(s)", "info")
    except StopIteration:
        pass
    except Exception as e:
        log(f"  Could not save JSON manifest: {e}", "warning")

    _maybe_export_mhl(manifest, saved_paths, export_mhl, log)

    if progress_cb: progress_cb(100, "Done")

    # M9.2: record this rclone job in the local activity index (never raises).
    try:
        from core.activity_index import record_from_manifest, safe_append_activity
        _src_label = str(src).rstrip("/").rsplit("/", 1)[-1]
        _dst_label = str(dst).rstrip("/").rsplit("/", 1)[-1]
        _verdict = "FAIL" if manifest.get("verify_failures") else "COMPLETE"
        _manifest_for_log = dict(manifest, label=job_name) if job_name else manifest
        safe_append_activity(
            record_from_manifest(
                _manifest_for_log, operation="transfer",
                source=_src_label, dests=[_dst_label], verdict=_verdict,
            ), log_cb=log_cb)
    except Exception:
        pass

    return {
        "manifest": manifest, "saved_manifest_paths": [str(p) for p in saved_paths],
        "errors": manifest.get("errors", []),
        "actual_dest": dst_str, "same_name": False,
    }


def route_transfer(src, dst, gdrive_mode=False, mirror_mode=False,
                   paranoid_verify=False,
                   log_cb=None, progress_cb=None, conflict_handler="skip",
                   export_mhl=False, job_name=""):
    """Top-level dispatcher: picks rclone vs. local based on URL detection."""
    if is_gdrive_url(str(src)) or is_gdrive_url(str(dst)):
        return transfer_folder_rclone(
            src, dst,
            mirror_mode=mirror_mode,
            conflict_handler=conflict_handler,
            paranoid_verify=paranoid_verify,
            log_cb=log_cb, progress_cb=progress_cb,
            export_mhl=export_mhl,
            job_name=job_name,
        )
    return transfer_folder(
        src, dst, gdrive_mode=gdrive_mode,
        log_cb=log_cb, progress_cb=progress_cb,
        conflict_handler=conflict_handler,
        export_mhl=export_mhl,
        job_name=job_name,
    )


def extract_multipart_zip(zip_dir, log_cb=None):
    def log(m, l="info"):
        if log_cb: log_cb(m, l)
    extracted = []
    zip_dir = Path(zip_dir)
    if not zip_dir.exists():
        log(f"  Zip extract skipped (path not local): {zip_dir}", "warning")
        return extracted
    for z in sorted(zip_dir.glob("*.zip")):
        out = z.parent / z.stem
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(out)
            log(f"  Extracted {z.name}"); extracted.append(out)
        except zipfile.BadZipFile:
            log(f"  Bad zip: {z.name}", "error")
    return extracted
