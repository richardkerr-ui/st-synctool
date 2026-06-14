"""Semantic bucket for merge diff states — the *direction* of a change and
whether it needs a human decision. Pure logic, no Qt: the GUI maps the bucket
to a colour + glyph (see ``gui.theme.merge_pill`` / ``merge_state_glyph``).

The buckets mirror the approved redesign mockup's three-colour key:

    out      — your changes going out to the server      (gold,  ↑)
    incoming — routine changes coming from the server     (gray,  ↓)
    decision — conflicts & held deletions you must resolve (coral, ⚠)
    neutral  — nothing to do: unchanged / both-deleted /
               rename info                                 (muted, ·)

A glyph always accompanies the colour so the state is legible without relying
on hue (colour-blind accessibility).
"""

_BUCKET = {
    "LOCAL_ONLY":     "out",
    "LOCAL_CHANGED":  "out",
    "SERVER_ONLY":    "incoming",
    "SERVER_CHANGED": "incoming",
    "BOTH_CHANGED":   "decision",
    "DELETED_LOCAL":  "decision",
    "DELETED_SERVER": "decision",
    "DELETED_BOTH":   "neutral",
    "UNCHANGED":      "neutral",
    "RENAMED":        "neutral",
}

_GLYPH = {
    "out":      "↑",
    "incoming": "↓",
    "decision": "⚠",
    "neutral":  "·",
}


def _normalise(state: str) -> str:
    return (state or "").strip().upper()


def state_bucket(state: str) -> str:
    """Return the semantic bucket for a diff state. Unknown → "neutral"."""
    return _BUCKET.get(_normalise(state), "neutral")


def bucket_glyph(bucket: str) -> str:
    return _GLYPH.get(bucket, _GLYPH["neutral"])


def state_glyph(state: str) -> str:
    """Return the accessibility glyph for a diff state's bucket."""
    return _GLYPH[state_bucket(state)]
