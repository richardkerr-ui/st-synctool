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

def _is_ignored(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in IGNORED_FILES

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

    collapsed_paths = set()
    final = []
    for result in results:
        if result.path in rename_map:
            entry = rename_map[result.path]
            orig = entry["from"]
            # Only collapse if the original path is also being resolved (deleted or exists)
            if result.state in (DiffState.SERVER_ONLY, DiffState.DELETED_SERVER,
                                DiffState.LOCAL_ONLY,  DiffState.DELETED_LOCAL):
                final.append(DiffResult(
                    path=result.path, state=DiffState.RENAMED,
                    base_entry=result.base_entry,
                    yours_entry=result.yours_entry,
                    server_entry=result.server_entry,
                    renamed_from=orig,
                ))
                collapsed_paths.add(orig)
                continue
        if result.path not in collapsed_paths:
            final.append(result)
    return final
