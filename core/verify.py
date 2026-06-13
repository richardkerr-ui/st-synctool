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

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from core.checksum import compute_all
import core.media_verify as _media_verify
from core import rclone_bridge
from utils.gdrive_utils import is_gdrive_url, gdrive_url_to_rclone

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str, str], None]

# M5.4 — where persisted verify reports land.
VERIFY_LOGS_DIR = Path.home() / "Documents" / "STSyncTool" / "logs"

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


# ---------------------------------------------------------------------------
# M5.2 — Batch verify across the projects registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectVerifySummary:
    """Consolidated per-project outcome from a batch verify run."""

    label: str
    folder: str
    total: int
    ok: int
    missing: int
    mismatch: int
    format_fail: int
    error: str = ""  # set when the project could not be verified at all

    @property
    def passed(self) -> bool:
        return (not self.error and self.missing == 0
                and self.mismatch == 0 and self.format_fail == 0)

    @property
    def verdict(self) -> str:
        if self.error:
            return "ERROR"
        return "OK" if self.passed else "FAIL"


def summarize_results(label: str, folder, results: list) -> ProjectVerifySummary:
    """Reduce a per-file results list to a ProjectVerifySummary."""
    def _count(status: str) -> int:
        return sum(1 for r in results if r.get("status") == status)
    return ProjectVerifySummary(
        label=label,
        folder=str(folder),
        total=len(results),
        ok=_count("OK"),
        missing=_count("MISSING"),
        mismatch=_count("MISMATCH"),
        format_fail=_count("FORMAT_FAIL"),
    )


def pairs_from_registry(projects: Optional[list] = None) -> tuple:
    """
    Build (pairs, skipped) from the projects registry.

    pairs   — list of {"label", "folder", "manifest"} ready for batch_verify.
    skipped — list of (label, reason) for projects that can't be verified
              (no manifest on record, no folder, or manifest fails to load).

    projects is injectable for testing; defaults to core.projects.list_projects().
    """
    from core.manifest import load_manifest
    if projects is None:
        from core import projects as _projects
        projects = _projects.list_projects()

    pairs: list = []
    skipped: list = []
    for p in projects:
        label = p.get("display_name") or p.get("project_id") or "(unnamed)"
        folder = p.get("local_path") or p.get("server_path")
        mpath = p.get("latest_manifest")
        if not mpath or not Path(mpath).exists():
            skipped.append((label, "no manifest on record"))
            continue
        if not folder:
            skipped.append((label, "no folder on record"))
            continue
        try:
            manifest = load_manifest(Path(mpath))
        except Exception as e:
            skipped.append((label, f"manifest load failed: {e}"))
            continue
        pairs.append({"label": label, "folder": folder, "manifest": manifest})
    return pairs, skipped


def batch_verify(
    pairs: list,
    progress_cb: Optional[ProgressCallback] = None,
    log_cb: Optional[LogCallback] = None,
    deep: bool = False,
    verify_fn: Optional[Callable] = None,
) -> list:
    """
    Verify a list of {"label", "folder", "manifest"} pairs and return one
    ProjectVerifySummary per pair. A project that raises (unreadable folder,
    rclone failure) becomes an ERROR summary rather than aborting the batch.

    Progress is reported at project granularity. verify_fn is injectable for
    testing; defaults to verify_folder.
    """
    progress = progress_cb or _noop_progress
    log = log_cb or _noop_log
    vfn = verify_fn or verify_folder

    summaries: list = []
    total = max(len(pairs), 1)
    for i, pair in enumerate(pairs):
        label = pair["label"]
        folder = pair["folder"]
        manifest = pair["manifest"]
        progress(int(i / total * 100), label)
        log(f"Verifying project: {label} ({folder})", "info")
        try:
            results = vfn(folder, manifest, log_cb=log, deep=deep)
        except Exception as e:
            summaries.append(ProjectVerifySummary(
                label, str(folder), 0, 0, 0, 0, 0, error=str(e)))
            log(f"  ERROR verifying {label}: {e}", "error")
            continue
        summary = summarize_results(label, folder, results)
        summaries.append(summary)
        log(f"  {label}: {summary.verdict} — {summary.ok} OK, "
            f"{summary.missing} missing, {summary.mismatch} mismatch", "info")

    progress(100, "Complete")
    return summaries


def format_batch_report(summaries: list, skipped: Optional[list] = None) -> str:
    """Render a consolidated plain-text batch verify report."""
    lines = [
        "ST SyncTool — Batch Verification Report",
        "=" * 60,
        f"Projects verified: {len(summaries)}",
        "",
    ]
    n_fail = sum(1 for s in summaries if s.verdict == "FAIL")
    n_err = sum(1 for s in summaries if s.verdict == "ERROR")
    n_ok = sum(1 for s in summaries if s.verdict == "OK")
    lines.append(f"OK: {n_ok}   FAIL: {n_fail}   ERROR: {n_err}")
    lines.append("")
    for s in summaries:
        lines.append(f"[{s.verdict}] {s.label}")
        lines.append(f"        {s.folder}")
        if s.error:
            lines.append(f"        error: {s.error}")
        else:
            lines.append(
                f"        {s.total} files — {s.ok} OK, {s.missing} missing, "
                f"{s.mismatch} mismatch, {s.format_fail} format-fail")
    if skipped:
        lines += ["", "Skipped (not verifiable):"]
        for label, reason in skipped:
            lines.append(f"  - {label}: {reason}")
    lines += ["", "END OF REPORT"]
    return "\n".join(lines)


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
            results = verify_gdrive_deep(folder, manifest, progress_cb, log_cb)
        else:
            results = verify_gdrive(folder, manifest, progress_cb, log_cb)
    else:
        results = verify_local(folder, manifest, progress_cb, log_cb)
    _log_verify_activity(folder, manifest, results)
    return results


def _log_verify_activity(folder, manifest: dict, results: list) -> None:
    """M9.2: record one 'verify' line in the local activity index. Never raises —
    activity logging must not affect the verify it describes."""
    try:
        from core.activity_index import record_from_manifest, safe_append_activity
        label = manifest.get("label") or str(folder).rstrip("/").rsplit("/", 1)[-1]
        summary = summarize_results(label, folder, results)
        safe_append_activity(record_from_manifest(
            manifest, operation="verify",
            source=str(folder).rstrip("/").rsplit("/", 1)[-1],
            verdict=summary.verdict,
        ))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# M5.4 — Persist format-verification results
# ---------------------------------------------------------------------------
#
# The format-aware media checks already run inside verify_local (format_status /
# format_detail on each result dict), but until now the evidence was lost when
# the window closed. These two helpers persist it: into a standalone JSON verify
# report, and — where a manifest is present on disk — into a `media_verify` block
# on each file entry. Both round-trip through json. The schema is documented in
# SCHEMA_INTEROP_SPEC.md.

def _utc_now_iso(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def media_verify_block(result: dict, *, now: Optional[datetime] = None) -> Optional[dict]:
    """Extract the persistable media-verify block from a per-file result, or None.

    Only results that actually ran a format check (i.e. carry `format_status`)
    yield a block; plain hash-only entries return None so non-media files stay
    untouched in the manifest.
    """
    status = result.get("format_status")
    if not status:
        return None
    return {
        "status": status,                              # OK | ADVISORY | FAILED
        "detail": result.get("format_detail", ""),
        "verified_at": _utc_now_iso(now),
    }


def persist_media_verify_to_manifest(
    manifest_path,
    results: list,
    *,
    now: Optional[datetime] = None,
    log_cb: Optional[LogCallback] = None,
) -> dict:
    """Write per-file media-verify outcomes into a manifest's file entries.

    Loads the manifest JSON at `manifest_path`, sets
    `files[path]["media_verify"] = {status, detail, verified_at}` for every
    result that ran a format check, writes the manifest back atomically and
    returns the updated dict. Results whose path is absent from the manifest
    (or which ran no format check) are skipped. Raises FileNotFoundError if the
    manifest does not exist.
    """
    log = log_cb or _noop_log
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    files = manifest.get("files", {})

    written = 0
    for r in results:
        block = media_verify_block(r, now=now)
        if block is None:
            continue
        entry = files.get(r.get("path"))
        if not isinstance(entry, dict):
            continue
        entry["media_verify"] = block
        written += 1

    _atomic_write_json(path, manifest)
    log(f"  Persisted media-verify results for {written} file(s) into manifest", "info")
    return manifest


def build_verify_report(
    folder,
    results: list,
    *,
    label: str = "",
    deep: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    """Build the JSON-serialisable verify report dict (per-file + summary)."""
    summary = summarize_results(label, folder, results)
    return {
        "schema": "verify_report",
        "schema_version": 1,
        "generated_at": _utc_now_iso(now),
        "folder": str(folder),
        "label": label,
        "deep": bool(deep),
        "summary": {
            "total": summary.total,
            "ok": summary.ok,
            "missing": summary.missing,
            "mismatch": summary.mismatch,
            "format_fail": summary.format_fail,
        },
        "verdict": summary.verdict,
        # Per-file rows carry the format_status / format_detail fields verbatim,
        # so the media-verify evidence survives the window closing.
        "files": [dict(r) for r in results],
    }


def write_verify_report(
    folder,
    results: list,
    *,
    label: str = "",
    deep: bool = False,
    log_dir=VERIFY_LOGS_DIR,
    now: Optional[datetime] = None,
    log_cb: Optional[LogCallback] = None,
) -> Path:
    """Persist a JSON verify report to `log_dir` and return its path.

    The report includes every per-file result (hash status plus any media-verify
    outcome) and a summary, so format-verification evidence is no longer lost
    when the window closes. Round-trippable via json.loads.
    """
    log = log_cb or _noop_log
    report = build_verify_report(folder, results, label=label, deep=deep, now=now)
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label).strip("_")
    fname = f"verify_report_{safe_label + '_' if safe_label else ''}{ts}.json"
    path = Path(log_dir) / fname
    _atomic_write_json(path, report)
    log(f"  Verify report written to {path}", "info")
    return path
