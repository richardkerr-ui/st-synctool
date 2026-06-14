"""Tests for core.merge_state — diff state → bucket/glyph mapping."""
import pytest

from core.merge_state import state_bucket, state_glyph, bucket_glyph


@pytest.mark.parametrize("state,bucket", [
    ("LOCAL_ONLY", "out"),
    ("LOCAL_CHANGED", "out"),
    ("SERVER_ONLY", "incoming"),
    ("SERVER_CHANGED", "incoming"),
    ("BOTH_CHANGED", "decision"),
    ("DELETED_LOCAL", "decision"),
    ("DELETED_SERVER", "decision"),
    ("DELETED_BOTH", "neutral"),
    ("UNCHANGED", "neutral"),
    ("RENAMED", "neutral"),
])
def test_buckets(state, bucket):
    assert state_bucket(state) == bucket


def test_normalisation_and_unknown():
    assert state_bucket("local_only") == "out"
    assert state_bucket("  Both_Changed ") == "decision"
    assert state_bucket("WAT") == "neutral"
    assert state_bucket("") == "neutral"
    assert state_bucket(None) == "neutral"


def test_glyphs_pair_with_direction():
    assert state_glyph("LOCAL_CHANGED") == "↑"   # going out
    assert state_glyph("SERVER_ONLY") == "↓"     # coming in
    assert state_glyph("BOTH_CHANGED") == "⚠"    # needs a decision
    assert state_glyph("UNCHANGED") == "·"       # nothing to do


def test_bucket_glyph_direct():
    assert bucket_glyph("out") == "↑"
    assert bucket_glyph("incoming") == "↓"
    assert bucket_glyph("decision") == "⚠"
    assert bucket_glyph("neutral") == "·"
    assert bucket_glyph("garbage") == "·"
