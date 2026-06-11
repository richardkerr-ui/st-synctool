from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

class DiffState(Enum):
    UNCHANGED=auto(); LOCAL_ONLY=auto(); SERVER_ONLY=auto()
    LOCAL_CHANGED=auto(); SERVER_CHANGED=auto(); BOTH_CHANGED=auto()
    DELETED_LOCAL=auto(); DELETED_SERVER=auto(); DELETED_BOTH=auto()

STATE_LABELS = {
    DiffState.UNCHANGED:      ("Unchanged",       "#6a9955"),
    DiffState.LOCAL_ONLY:     ("Local Only",      "#569cd6"),
    DiffState.SERVER_ONLY:    ("Server Only",     "#9cdcfe"),
    DiffState.LOCAL_CHANGED:  ("Local Changed",   "#dcdcaa"),
    DiffState.SERVER_CHANGED: ("Server Changed",  "#ce9178"),
    DiffState.BOTH_CHANGED:   ("⚠ Conflict",      "#f44747"),
    DiffState.DELETED_LOCAL:  ("Deleted Locally", "#d16969"),
    DiffState.DELETED_SERVER: ("Deleted Server",  "#c586c0"),
    DiffState.DELETED_BOTH:   ("Deleted Both",    "#808080"),
}

@dataclass
class DiffResult:
    path: str; state: DiffState
    base_entry: Optional[dict]=field(default=None,repr=False)
    yours_entry: Optional[dict]=field(default=None,repr=False)
    server_entry: Optional[dict]=field(default=None,repr=False)
    @property
    def label(self): return STATE_LABELS[self.state][0]
    @property
    def color(self): return STATE_LABELS[self.state][1]

def _cs(entry):
    if not entry: return None
    c = entry.get("checksums", {})
    return c.get("sha256") or c.get("xxhash3_64") or c.get("md5")

def three_way_diff(base, yours, server) -> list:
    bf=base.get("files",{}); yf=yours.get("files",{}); sf=server.get("files",{})
    results=[]
    for path in sorted(set(bf)|set(yf)|set(sf)):
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
    return results
