"""
M5.0 — Verify logic, extracted from gui/verify_tab.py so it is testable without
PyQt6 and reusable by batch / deep / scheduled verify (M5.1, M5.2, M5.3).

Two entry points, plus a dispatcher:
  • verify_local(folder, manifest, ...)  — hash files on disk against a manifest,
    with additive format-aware media checks.
  • verify_gdrive(folder, manifest, ...) — compare manifest hashes to Drive
    metadata via rclone lsjson (no downloads).
  • verify_folder(folder, manifest, ...) — picks local vs Drive by URL detection.

Each returns a list of per-file result dicts:
  {"path": str, "status": "OK"|"MISSING"|"MISMATCH"|"FORMAT_FAIL", "detail": str,
   optionally "format_status": "OK"|"ADVISORY"|"FAILED", "format_detail": str}

Progress and logging are delivered through optional callbacks so the Qt worker
stays a thin signal adapter:
  progress_cb(pct: int, path: str)
  log_cb(message: str, level: str)   level ∈ {"info","success","warning","error"}

No PyQt6 import here; the GUI layer wires the callbacks to Qt signals.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from core.checksum import compute_all
import core.media_verify as _media_verify
from core import rclone_bridge
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str, str], None]

# Files that legitimately live in a Drive folder without being in the manifest.
_IGNORED_EXTRAS = frozenset(
    {"st_manifest.json", ".DS_Store", "Thumbs.db", "desktop.ini"}
)


def _noop_progress(pct: int, path: str) -> None:  # pragma: no cover - trivial
    pass


def _noop_log(message: str, level: str) -> None:  # pragma: no cover - trivial
    pass


def expected_checksums(entry: dict) -> dict:
    """Pull the most authoritative checksum block out of a manifest entry."""
    return (
        entry.get("dest_checksums")
        or entry.get("source_checksums")
        or entry.get("checksums", {})
    )


def _select_algo(checksums: dict) -> str:
    """Local algo preference: sha256 > xxhash3_64 > md5."""
    if "sha256" in checksums:
        return "sha256"
    if "xxhash3_64" in checksums:
        return "xxhash3_64"
    return "md5"


def verify_local(
    folder,
    manifest: dict,
    progress_cb: Optional[ProgressCallback] = None,
    log_cb: Optional[LogCallback] = None,
) -> list:
    """Verify files on local disk against the manifest checksums."""
    progress = progress_cb or _noop_progress
    log = log_cb or _noop_log

    folder = Path(folder)
    files = manifest.get("files", {})
    total = max(len(files), 1)
    results: list = []
    seq_dirs_seen: set = set()

    for i, (rel_path, entry) in enumerate(files.items()):
        progress(int(i / total * 100), rel_path)
        abs_path = folder / rel_path

        if not abs_path.exists():
            results.append({"path": rel_path, "status": "MISSING",
                            "detail": "File not found on disk"})
            log(f"  MISSING: {rel_path}", "error")
            continue

        expected_cs = expected_checksums(entry)
        algo = _select_algo(expected_cs)
        actual = compute_all(
            abs_path,
            include_xxhash=(algo == "xxhash3_64"),
            include_md5=(algo == "md5"),
        )
        expected_val = (expected_cs.get(algo) or "").lower()
        actual_val = (actual.get(algo) or "").lower()

        hash_ok = expected_val == actual_val and bool(expected_val)
        if hash_ok:
            result = {"path": rel_path, "status": "OK",
                      "detail": f"{algo}: {actual_val[:16]}..."}
            log(f"  OK: {rel_path}", "success")
        else:
            result = {"path": rel_path, "status": "MISMATCH",
                      "detail": f"Expected {expected_val[:16]}... | Got {actual_val[:16]}..."}
            log(f"  MISMATCH: {rel_path}", "error")

        # ── Format-specific media verification (additive) ─────────────
        try:
            mv_result = _media_verify.verify_file(abs_path, abs_path, seq_dirs_seen)
        except Exception as mv_exc:
            logging.getLogger(__name__).warning(
                "[MediaVerify] Unexpected error for %s: %s", rel_path, mv_exc
            )
            mv_result = None

        if mv_result is not None:
            if not mv_result.ok and not mv_result.advisory:
                result["format_status"] = "FAILED"
                result["format_detail"] = mv_result.detail
                if hash_ok:
                    result["status"] = "FORMAT_FAIL"
                log(f"  FORMAT FAIL: {rel_path} — {mv_result.detail}", "error")
            elif mv_result.advisory:
                result["format_status"] = "ADVISORY"
                result["format_detail"] = mv_result.detail
            else:
                result["format_status"] = "OK"
                result["format_detail"] = mv_result.detail

        results.append(result)

    progress(100, "Complete")
    return results


def verify_gdrive(
    folder,
    manifest: dict,
    progress_cb: Optional[ProgressCallback] = None,
    log_cb: Optional[LogCallback] = None,
) -> list:
    """
    Verify a Drive folder by pulling hashes via rclone lsjson and comparing to
    the manifest. No file downloads — purely metadata-based.

    Raises RuntimeError if the rclone listing fails (the caller decides how to
    surface it; the Qt worker emits error()).
    """
    progress = progress_cb or _noop_progress
    log = log_cb or _noop_log

    log("Fetching Drive folder hashes via rclone lsjson...", "info")
    remote, flags = gdrive_url_to_rclone(str(folder))
    try:
        items = rclone_bridge.lsjson(remote, extra_flags=flags, with_checksum=True)
    except Exception as e:
        raise RuntimeError(f"rclone lsjson failed: {e}") from e

    # Build {rel_path: hashes_dict} from Drive listing
    drive_files: dict = {}
    for item in items:
        if item.get("IsDir"):
            continue
        hashes = {k.lower(): (v or "").lower()
                  for k, v in (item.get("Hashes") or {}).items()}
        drive_files[item["Path"]] = hashes

    files = manifest.get("files", {})
    total = max(len(files), 1)
    results: list = []

    for i, (rel_path, entry) in enumerate(files.items()):
        progress(int(i / total * 100), rel_path)

        if rel_path not in drive_files:
            results.append({"path": rel_path, "status": "MISSING",
                            "detail": "Not present in Drive folder"})
            log(f"  MISSING: {rel_path}", "error")
            continue

        expected_cs = expected_checksums(entry)
        drive_hashes = drive_files[rel_path]

        # Pick the strongest hash available on both sides
        algo = None
        for candidate in ("sha256", "sha1", "md5"):
            if candidate in expected_cs and candidate in drive_hashes:
                algo = candidate
                break

        if algo is None:
            results.append({"path": rel_path, "status": "MISMATCH",
                            "detail": "No common hash algorithm between manifest and Drive"})
            log(f"  MISMATCH (no common hash): {rel_path}", "error")
            continue

        expected_val = (expected_cs.get(algo) or "").lower()
        actual_val = drive_hashes.get(algo, "")

        if expected_val == actual_val and expected_val:
            results.append({"path": rel_path, "status": "OK",
                            "detail": f"{algo}: {actual_val[:16]}..."})
            log(f"  OK: {rel_path}", "success")
        else:
            results.append({"path": rel_path, "status": "MISMATCH",
                            "detail": f"Expected {expected_val[:16]}... | Got {actual_val[:16]}..."})
            log(f"  MISMATCH: {rel_path}", "error")

    # Report extras on Drive not covered by manifest (info only)
    extras = set(drive_files.keys()) - set(files.keys())
    extras = {p for p in extras if Path(p).name not in _IGNORED_EXTRAS}
    if extras:
        log(f"  Note: {len(extras)} file(s) present on Drive but not in manifest",
            "warning")

    progress(100, "Complete")
    return results


# M5.1: deep Drive verify is bandwidth-bound. Assumed sustained download for the
# up-front time estimate (deliberately conservative; the real run shows actual).
DEEP_VERIFY_ASSUMED_MBPS = 100


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _human_duration(secs: float) -> str:
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h}h {m}m" if h else (f"{m}m {s}s" if m else f"{s}s")


def estimate_deep_verify_seconds(total_bytes: int, mbps: float = DEEP_VERIFY_ASSUMED_MBPS) -> float:
    """Estimated download time for a deep verify, in seconds (bandwidth-bound)."""
    if total_bytes <= 0 or mbps <= 0:
        return 0.0
    bytes_per_sec = mbps * 1_000_000 / 8
    return total_bytes / bytes_per_sec


def _join_remote(remote: str, rel: str) -> str:
    """Join an rclone remote folder spec with a relative file path."""
    if remote.endswith(":") or remote.endswith("/"):
        return f"{remote}{rel}"
    return f"{remote}/{rel}"


def verify_gdrive_deep(
    folder,
    manifest: dict,
    progress_cb: Optional[ProgressCallback] = None,
    log_cb: Optional[LogCallback] = None,
    cat_fn: Optional[Callable] = None,
) -> list:
    """
    Deep-verify a Drive folder by streaming every file through `rclone cat` to a
    SHA-256 and comparing to the manifest (M5.1). No file is retained locally.
    Bandwidth-bound, so an honest size + time estimate is logged up front.

    cat_fn is injectable for testing; defaults to rclone_bridge.cat_sha256.
    """
    progress = progress_cb or _noop_progress
    log = log_cb or _noop_log
    cat = cat_fn or rclone_bridge.cat_sha256

    remote, flags = gdrive_url_to_rclone(str(folder))
    files = manifest.get("files", {})
    total = max(len(files), 1)

    total_bytes = sum(int(e.get("size") or 0) for e in files.values())
    est = estimate_deep_verify_seconds(total_bytes)
    log(
        f"Deep verify will download {_human_bytes(total_bytes)} across "
        f"{len(files)} file(s) — est. {_human_duration(est)} @ "
        f"{DEEP_VERIFY_ASSUMED_MBPS} Mbps (bandwidth-bound, no local copy kept)",
        "info",
    )

    results: list = []
    for i, (rel_path, entry) in enumerate(files.items()):
        progress(int(i / total * 100), rel_path)
        expected_val = (expected_checksums(entry).get("sha256") or "").lower()

        if not expected_val:
            results.append({"path": rel_path, "status": "MISMATCH",
                            "detail": "No sha256 in manifest for deep comparison"})
            log(f"  MISMATCH (no sha256 in manifest): {rel_path}", "error")
            continue

        try:
            actual_val = cat(_join_remote(remote, rel_path), extra_flags=flags).lower()
        except Exception as e:
            results.append({"path": rel_path, "status": "MISSING",
                            "detail": f"Could not read from Drive: {e}"})
            log(f"  MISSING: {rel_path} — {e}", "error")
            continue

        if actual_val == expected_val:
            results.append({"path": rel_path, "status": "OK",
                            "detail": f"sha256: {actual_val[:16]}... (downloaded)"})
            log(f"  OK: {rel_path}", "success")
        else:
            results.append({"path": rel_path, "status": "MISMATCH",
                            "detail": f"Expected {expected_val[:16]}... | Got {actual_val[:16]}..."})
            log(f"  MISMATCH: {rel_path}", "error")

    progress(100, "Complete")
    return results


def verify_folder(
    folder,
    manifest: dict,
    progress_cb: Optional[ProgressCallback] = None,
    log_cb: Optional[LogCallback] = None,
    deep: bool = False,
) -> list:
    """
    Dispatch to local, Drive-metadata, or deep-Drive verification.

    deep=True only applies to Drive folders (downloads each file to hash it);
    it is ignored for local folders, which are always hashed directly.
    """
    if is_gdrive_url(str(folder)):
        if deep:
            return verify_gdrive_deep(folder, manifest, progress_cb, log_cb)
        return verify_gdrive(folder, manifest, progress_cb, log_cb)
    return verify_local(folder, manifest, progress_cb, log_cb)
