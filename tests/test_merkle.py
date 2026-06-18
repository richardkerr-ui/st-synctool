"""Tests for core/merkle.py — M13.5 folder root digest.

Covers leaf hashing, internal nodes, single/two/even/odd-count trees, the empty
sentinel, path normalisation, domain separation, the version tag, round-trip
identity, and the CaseFoldCollision guard. All-xxh128; no sha256/hashlib.
"""

import unicodedata

import pytest
import xxhash

from core import merkle
from core.merkle import (
    CaseFoldCollision,
    ROOT_VERSION,
    empty_root,
    file_leaf,
    internal_node,
    merkle_root,
    normalise_path,
)


# ── normalise_path ───────────────────────────────────────────────────────────

def test_normalise_forward_slashes():
    assert normalise_path("a\\b\\c.mov") == "a/b/c.mov"


def test_normalise_lowercases():
    assert normalise_path("Shot_01A.MOV") == "shot_01a.mov"


def test_normalise_nfc():
    nfd = unicodedata.normalize("NFD", "café.mov")
    assert normalise_path(nfd) == unicodedata.normalize("NFC", "café.mov").lower()


# ── leaf / internal / empty primitives ───────────────────────────────────────

def test_file_leaf_is_domain_separated_from_node():
    # A leaf and an internal node built from the same-looking strings must differ
    # because of the b"file:" vs b"node:" domain prefixes.
    leaf = file_leaf("a.mov", "deadbeef")
    node = internal_node("a.mov", "deadbeef")  # not how it's used, but proves prefix matters
    assert leaf != node


def test_file_leaf_matches_pinned_formula():
    norm, digest = "clips/a.mov", "0123abcd"
    expected = xxhash.xxh3_128(b"file:clips/a.mov:0123abcd").hexdigest()
    assert file_leaf(norm, digest) == expected


def test_internal_node_matches_pinned_formula():
    expected = xxhash.xxh3_128(b"node:LL:RR").hexdigest()
    assert internal_node("LL", "RR") == expected


def test_internal_node_order_matters():
    assert internal_node("a", "b") != internal_node("b", "a")


def test_empty_root_is_pinned_sentinel():
    assert empty_root() == xxhash.xxh3_128(b"empty").hexdigest()


# ── merkle_root: counts and structure ────────────────────────────────────────

def test_empty_folder_returns_versioned_sentinel():
    assert merkle_root({}) == f"{ROOT_VERSION}:{empty_root()}"


def test_version_tag_prefix():
    root = merkle_root({"a.mov": "11"})
    assert root.startswith("v1:")


def test_single_leaf_root_is_that_leaf():
    root = merkle_root({"a.mov": "11"})
    assert root == f"{ROOT_VERSION}:{file_leaf('a.mov', '11')}"


def test_two_leaf_root_is_internal_node_of_sorted_leaves():
    leaves = {"b.mov": "22", "a.mov": "11"}
    la = file_leaf("a.mov", "11")
    lb = file_leaf("b.mov", "22")
    expected = f"{ROOT_VERSION}:{internal_node(la, lb)}"  # sorted: a before b
    assert merkle_root(leaves) == expected


def test_even_count_four_leaves():
    leaves = {f"{c}.mov": c * 2 for c in "abcd"}
    la, lb, lc, ld = (file_leaf(f"{c}.mov", c * 2) for c in "abcd")
    top = internal_node(internal_node(la, lb), internal_node(lc, ld))
    assert merkle_root(leaves) == f"{ROOT_VERSION}:{top}"


def test_odd_count_promotes_last_node_not_duplicated():
    # Three leaves: level0 = [la, lb, lc]; level1 = [node(la,lb), lc(promoted)];
    # root = node(node(la,lb), lc). Duplicating lc would give node(node(la,lb),
    # node(lc,lc)) — a different, CVE-2012-2459-ambiguous value.
    leaves = {"a.mov": "11", "b.mov": "22", "c.mov": "33"}
    la = file_leaf("a.mov", "11")
    lb = file_leaf("b.mov", "22")
    lc = file_leaf("c.mov", "33")
    promote_root = f"{ROOT_VERSION}:{internal_node(internal_node(la, lb), lc)}"
    duplicate_root = f"{ROOT_VERSION}:{internal_node(internal_node(la, lb), internal_node(lc, lc))}"
    assert merkle_root(leaves) == promote_root
    assert merkle_root(leaves) != duplicate_root


# ── ordering, normalisation, round-trip ──────────────────────────────────────

def test_insertion_order_does_not_change_root():
    a = merkle_root({"a.mov": "11", "b.mov": "22", "c.mov": "33"})
    b = merkle_root({"c.mov": "33", "a.mov": "11", "b.mov": "22"})
    assert a == b


def test_round_trip_identity_same_fixture_same_root():
    leaves = {"DCIM/A001/clip_001.mov": "aa", "DCIM/A001/clip_002.mov": "bb"}
    assert merkle_root(dict(leaves)) == merkle_root(dict(leaves))


def test_backslash_and_forward_slash_paths_agree():
    assert merkle_root({"a\\b.mov": "11"}) == merkle_root({"a/b.mov": "11"})


def test_case_differing_digests_change_root():
    assert merkle_root({"a.mov": "11"}) != merkle_root({"a.mov": "22"})


# ── CaseFoldCollision guard ──────────────────────────────────────────────────

def test_case_collision_raises_not_silently_overwrites():
    # Two case-sensitive-distinct paths that normalise to the same key.
    with pytest.raises(CaseFoldCollision):
        merkle_root({"Photo.jpg": "11", "photo.jpg": "22"})


def test_unicode_normalisation_collision_raises():
    nfc = unicodedata.normalize("NFC", "café.mov")
    nfd = unicodedata.normalize("NFD", "café.mov")
    assert nfc != nfd  # different byte sequences
    with pytest.raises(CaseFoldCollision):
        merkle_root({nfc: "11", nfd: "22"})


def test_no_sha256_or_hashlib_used_in_module():
    # The docstring mentions "no hashlib, no sha256" in prose, so assert on actual
    # code constructs rather than bare words.
    import inspect
    src = inspect.getsource(merkle)
    assert "import hashlib" not in src
    assert "hashlib." not in src
    assert ".sha256(" not in src and "sha256(" not in src
