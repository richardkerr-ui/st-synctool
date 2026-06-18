"""
core/merkle.py — M13.5 folder root digest (a corruption fingerprint).

Builds a single xxh128 Merkle root over a folder's per-file xxh128 digests. The
root detects accidental bit-rot and enables incremental diffing (only descend
where roots differ). It is **not** tamper-evident: the tree is all-xxh128 and its
adversarial strength is bounded by xxh128 collision resistance, the same as the
leaf hashes. The structural guards below (domain separation, odd-node
promote-not-duplicate, CaseFoldCollision) are correctness properties that prevent
*accidental* root ambiguity — they are not security properties. The label
"folder root" is intentional (not "merkle root") to avoid implying a guarantee
the construction does not deliver. Do not describe it as tamper-evident in code,
docs or UI.

Tree format (pinned — changing ANY of these changes every root):
  • Leaves:      xxh3_128(b"file:" + path_norm + b":" + xxh128_digest).hexdigest()
                 domain-separated so a leaf can never collide with an internal node.
  • Path norm:   forward slashes, Unicode NFC, lowercase (cross-platform agreement
                 on case-insensitive volumes: APFS default, exFAT cards, NTFS).
  • Sort:        leaves sorted by normalised rel_path, ascending.
  • Internal:    xxh3_128(b"node:" + left + b":" + right).hexdigest()
  • Odd node:    promoted unchanged (NOT duplicated — duplicating the last node
                 introduces CVE-2012-2459-style ambiguity).
  • Empty:       defined sentinel xxh3_128(b"empty").hexdigest()
  • Version tag: root output is "v1:" + root_hex so a format change is detectable.

All-xxh128. No hashlib, no sha256.
"""

from __future__ import annotations

import unicodedata

import xxhash

ROOT_VERSION = "v1"


class CaseFoldCollision(Exception):
    """Two distinct source paths normalise to the same key.

    A dict keyed on the normalised path would silently drop one file, producing a
    clean-looking root for an incomplete folder. Raising converts that silent
    wrong answer into a loud, fixable error. Camera media is almost always
    case-insensitive, but a case-sensitive APFS scratch disk or Linux source can
    hit this (e.g. ``Photo.jpg`` and ``photo.jpg`` in the same folder)."""


def normalise_path(rel_path: str) -> str:
    """Normalise a relative path for the folder-root fingerprint ONLY.

    Forward slashes + Unicode NFC + lowercase. This is an internal comparison key
    and may mangle the original name freely. It must NOT leak into MHL or manifest
    filenames, which carry the true on-disk name (case and Unicode preserved).
    """
    fwd = rel_path.replace("\\", "/")
    nfc = unicodedata.normalize("NFC", fwd)
    return nfc.lower()


def file_leaf(norm_path: str, xxh128_digest: str) -> str:
    """Domain-separated leaf hash for a single file."""
    payload = b"file:" + norm_path.encode("utf-8") + b":" + xxh128_digest.encode("ascii")
    return xxhash.xxh3_128(payload).hexdigest()


def internal_node(left: str, right: str) -> str:
    """Domain-separated internal node hash combining two child hashes."""
    payload = b"node:" + left.encode("ascii") + b":" + right.encode("ascii")
    return xxhash.xxh3_128(payload).hexdigest()


def empty_root() -> str:
    """The pinned sentinel digest for an empty folder (no files)."""
    return xxhash.xxh3_128(b"empty").hexdigest()


def merkle_root(leaves: dict) -> str:
    """Compute the versioned folder root over ``{rel_path: xxh128_digest}``.

    Accepts raw (un-normalised) rel_paths and normalises internally. Raises
    CaseFoldCollision if any two raw paths normalise to the same key. Returns
    ``"v1:" + root_hex``; an empty mapping returns ``"v1:" + empty_root()``.
    """
    # Normalise + collision guard: two raw paths must never share a normalised key.
    norm_to_digest: dict = {}
    for rel_path, digest in leaves.items():
        key = normalise_path(rel_path)
        if key in norm_to_digest:
            raise CaseFoldCollision(
                f"Two paths normalise to {key!r}; refusing to drop a file from the "
                f"folder root (a silent overwrite would fingerprint an incomplete folder)."
            )
        norm_to_digest[key] = digest

    if not norm_to_digest:
        return f"{ROOT_VERSION}:{empty_root()}"

    # Leaves sorted by normalised rel_path, ascending.
    level = [file_leaf(key, norm_to_digest[key]) for key in sorted(norm_to_digest)]

    # Combine pairwise up to a single root; odd node promotes unchanged.
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(internal_node(level[i], level[i + 1]))
            else:
                nxt.append(level[i])  # promote, do not duplicate
        level = nxt

    return f"{ROOT_VERSION}:{level[0]}"
