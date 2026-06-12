from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

class DiffState(Enum):
    UNCHANGED=auto(); LOCAL_ONLY=auto(); SERVER_ONLY=auto()
    LOCAL_CHANGED=auto(); SERVER_CHANGED=auto(); BOTH_CHANGED=auto()
    DELETED_LOCAL=auto(); DELETED_SERVER=auto(); DELETED_BOTH=auto()
    RENAMED=auto()

STATE_LABELS = {
    DiffState.UNCHANGED:      ("Unchanged",       "#6a9955"),
    DiffState.LOCAL_ONLY:     ("Local Only",      "#569cd6"),
    DiffState.SERVER_ONLY:    ("Server Only",     "#9cdcfe"),
    DiffState.LOCAL_CHANGED:  ("Local Changed",   "#dcdcaa"),
    DiffState.SERVER_CHANGED: ("Server Changed",  "#ce9178"),
    DiffState.BOTH_CHANGED:   ("Conflict",        "#f44747"),
    DiffState.DELETED_LOCAL:  ("Deleted Locally", "#d16969"),
    DiffState.DELETED_SERVER: ("Deleted Server",  "#c586c0"),
    DiffState.DELETED_BOTH:   ("Deleted Both",    "#808080"),
    DiffState.RENAMED:        ("Renamed",         "#c586c0"),
}

@dataclass
class DiffResult:
    path: str; state: DiffState
    base_entry:   Optional[dict] = field(default=None, repr=False)
    yours_entry:  Optional[dict] = field(default=None, repr=False)
    server_entry: Optional[dict] = field(default=None, repr=False)
    renamed_from: Optional[str]  = field(default=None)
    @property
    def label(self): return STATE_LABELS[self.state][0]
    @property
    def color(self): return STATE_LABELS[self.state][1]


# Files that should never appear in merge diffs (internal bookkeeping / OS junk)
IGNORED_FILES = {
    "st_manifest.json",   # our own manifest file
    ".DS_Store",          # macOS finder metadata
    "Thumbs.db",          # Windows thumbnail cache
    "desktop.ini",
}

# Path segments that mark internally generated artifacts (item 52c)
_IGNORED_PREFIXES = (
    "_contact_sheet_",  # offload contact sheet PDFs/JPEGs
    ".st_staging_",     # in-progress staging directories
    ".st_failure_",     # failure reports left alongside staging
    ".st_offload_",     # offload metadata files
)
_IGNORED_DIRS = {"_thumbnails"}  # thumbnail frame cache


def _is_ignored(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name in IGNORED_FILES:
        return True
    if any(name.startswith(p) for p in _IGNORED_PREFIXES):
        return True
    # Check every path segment for ignored directory names
    parts = path.replace("\\", "/").split("/")
    return any(part in _IGNORED_DIRS or any(part.startswith(p) for p in _IGNORED_PREFIXES) for part in parts)


# Public alias: this is the single source of truth for "files that should never
# appear in a diff or in a generated manifest" (OS junk, our own manifest file,
# staging/failure/thumbnail artifacts). Manifest generation imports this so the
# ignore list is unified across comparison and manifest generation.
def is_ignored_path(path: str) -> bool:
    return _is_ignored(path)


def conflict_suggested_action(result: "DiffResult") -> str:
    """Return the mtime-based suggested action for a BOTH_CHANGED DiffResult.

    Compares yours_entry.modtime vs server_entry.modtime:
      - local newer  -> "Push to Server"
      - server newer -> "Pull from Server"
      - tie / unknown -> "Skip"

    Safe to call on non-BOTH_CHANGED rows; always returns "Skip" for those.
    """
    # Import here to avoid circular dependency (merge_ops imports comparison)
    from core.merge_ops import ACT_PUSH, ACT_PULL, ACT_SKIP

    if result.state.name != "BOTH_CHANGED":
        return ACT_SKIP

    local_mt  = (result.yours_entry  or {}).get("modtime")
    server_mt = (result.server_entry or {}).get("modtime")

    if not local_mt or not server_mt:
        return ACT_SKIP

    try:
        if local_mt > server_mt:
            return ACT_PUSH
        if server_mt > local_mt:
            return ACT_PULL
    except Exception:
        pass

    return ACT_SKIP

def _cs(entry):
    if not entry: return None
    c = entry.get("checksums", {})
    return c.get("sha256") or c.get("xxhash3_64") or c.get("md5")

def three_way_diff(base, yours, server) -> list:
    bf=base.get("files",{}); yf=yours.get("files",{}); sf=server.get("files",{})
    results=[]
    for path in sorted(set(bf)|set(yf)|set(sf)):
        if _is_ignored(path):
            continue
        b=bf.get(path); y=yf.get(path); s=sf.get(path)
        cb=_cs(b); cy=_cs(y); cs=_cs(s)
        if b and y and s:
            if cy==cb and cs==cb: state=DiffState.UNCHANGED
            elif cy!=cb and cs==cb: state=DiffState.LOCAL_CHANGED
            elif cs!=cb and cy==cb: state=DiffState.SERVER_CHANGED
            else: state=DiffState.BOTH_CHANGED
        elif not b and y and s: state=DiffState.UNCHANGED if cy==cs else DiffState.BOTH_CHANGED
        elif not b and y and not s: state=DiffState.LOCAL_ONLY
        elif not b and not y and s: state=DiffState.SERVER_ONLY
        elif b and not y and not s: state=DiffState.DELETED_BOTH
        elif b and not y and s: state=DiffState.DELETED_LOCAL
        elif b and y and not s: state=DiffState.DELETED_SERVER
        else: continue
        results.append(DiffResult(path=path,state=state,base_entry=b,yours_entry=y,server_entry=s))

    # Collapse intentional renames recorded in base manifest (item 14).
    # A rename entry {from: X, to: Y} means Y was created by a preserve_rename during apply.
    # Y appearing as SERVER_ONLY/DELETED_SERVER (pull rename) or LOCAL_ONLY/DELETED_LOCAL
    # (push rename) is expected and should be marked RENAMED, not flagged as a conflict.
    rename_map = {r["to"]: r for r in base.get("renames", []) if r.get("to") and r.get("from")}
    if not rename_map:
        return results

    # Two-pass collapse: first identify which original paths will be suppressed,
    # then build the final list.  A single-pass approach fails when the renamed-to
    # path sorts after the renamed-from path and the original has already been
    # added to the output before the collapse fires.
    collapsed_paths: set[str] = set()
    for result in results:
        if result.path in rename_map:
            entry = rename_map[result.path]
            if result.state in (DiffState.SERVER_ONLY, DiffState.DELETED_SERVER,
                                DiffState.LOCAL_ONLY,  DiffState.DELETED_LOCAL):
                collapsed_paths.add(entry["from"])

    final = []
    for result in results:
        # collapsed_paths wins: if this path is the 'from' of a rename whose 'to'
        # is already being shown as RENAMED, suppress it even if it is itself a
        # rename target (chained-rename case).
        if result.path in collapsed_paths:
            continue
        if result.path in rename_map:
            entry = rename_map[result.path]
            orig = entry["from"]
            if result.state in (DiffState.SERVER_ONLY, DiffState.DELETED_SERVER,
                                DiffState.LOCAL_ONLY,  DiffState.DELETED_LOCAL):
                final.append(DiffResult(
                    path=result.path, state=DiffState.RENAMED,
                    base_entry=result.base_entry,
                    yours_entry=result.yours_entry,
                    server_entry=result.server_entry,
                    renamed_from=orig,
                ))
                continue
        final.append(result)
    return final
