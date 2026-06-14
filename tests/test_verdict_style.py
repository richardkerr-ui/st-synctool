"""Tests for core.verdict_style — verdict → severity/symbol mapping."""
import pytest

from core.verdict_style import (
    verdict_severity,
    verdict_symbol,
    severity_symbol,
)


@pytest.mark.parametrize("verdict,severity", [
    ("OK", "ok"),
    ("PASS", "ok"),
    ("VERIFIED", "ok"),
    ("CLEARED", "ok"),
    ("COMPLETE", "neutral"),
    ("SKIPPED", "neutral"),
    ("NOT_CLEARED", "warn"),
    ("EXTRA", "warn"),
    ("FAIL", "error"),
    ("ERROR", "error"),
    ("MISSING", "error"),
    ("MISMATCH", "error"),
])
def test_known_verdicts(verdict, severity):
    assert verdict_severity(verdict) == severity


@pytest.mark.parametrize("raw,severity", [
    ("verified", "ok"),
    ("  Fail  ", "error"),
    ("not cleared", "warn"),
    ("Not-Cleared", "warn"),
])
def test_normalisation(raw, severity):
    """Case, surrounding whitespace and space/hyphen separators are ignored."""
    assert verdict_severity(raw) == severity


def test_unknown_falls_back_to_neutral():
    assert verdict_severity("WHATEVER") == "neutral"
    assert verdict_severity("") == "neutral"
    assert verdict_severity(None) == "neutral"


def test_symbols_match_severity():
    """Glyphs accompany colour so the verdict reads without relying on hue."""
    assert verdict_symbol("VERIFIED") == "✓"
    assert verdict_symbol("NOT_CLEARED") == "⚠"
    assert verdict_symbol("FAIL") == "✕"
    assert verdict_symbol("COMPLETE") == "·"


def test_severity_symbol_direct():
    assert severity_symbol("ok") == "✓"
    assert severity_symbol("warn") == "⚠"
    assert severity_symbol("error") == "✕"
    assert severity_symbol("neutral") == "·"
    assert severity_symbol("garbage") == "·"
