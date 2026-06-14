"""Semantic styling for operation verdicts and states.

Single source of truth for *what a verdict means* (its severity) and *which
glyph represents it*. Pure logic, no Qt: the GUI maps the severity to a brand
colour via ``gui.theme.verdict_color`` and renders the glyph alongside it.
Keeping the semantics here lets them be unit-tested headlessly while the Qt
layer stays a thin renderer.

Glyphs are shown in addition to colour so the verdict is legible to colour-blind
users (colour is never the sole signal), which is why we diverge from the
colour-only redesign mockup here.

Severities:
    "ok"      — clean / verified / passed           (green)
    "warn"    — needs attention but not a failure    (gold)
    "error"   — failed / missing / mismatched        (coral)
    "neutral" — completed, nothing to flag           (muted gray)
"""

# Map a normalised verdict/status token to a severity bucket.
_SEVERITY = {
    "OK":          "ok",
    "PASS":        "ok",
    "PASSED":      "ok",
    "VERIFIED":    "ok",
    "CLEARED":     "ok",
    "SAFE":        "ok",
    "COMPLETE":    "neutral",
    "COMPLETED":   "neutral",
    "DONE":        "neutral",
    "SKIPPED":     "neutral",
    "NOT_CLEARED": "warn",
    "EXTRA":       "warn",
    "WARNING":     "warn",
    "WARN":        "warn",
    "FAIL":        "error",
    "FAILED":      "error",
    "ERROR":       "error",
    "MISSING":     "error",
    "MISMATCH":    "error",
}

_SYMBOL = {
    "ok":      "✓",
    "warn":    "⚠",
    "error":   "✗",
    "neutral": "·",
}


def _normalise(verdict: str) -> str:
    return (verdict or "").strip().upper().replace(" ", "_").replace("-", "_")


def verdict_severity(verdict: str) -> str:
    """Return the severity bucket for a verdict token.

    Unknown verdicts fall back to "neutral" so the UI never crashes on a token
    it has not seen.
    """
    return _SEVERITY.get(_normalise(verdict), "neutral")


def verdict_symbol(verdict: str) -> str:
    """Return the accessibility glyph for a verdict's severity."""
    return _SYMBOL[verdict_severity(verdict)]


def severity_symbol(severity: str) -> str:
    """Return the accessibility glyph for a severity bucket directly."""
    return _SYMBOL.get(severity, _SYMBOL["neutral"])
