"""M10.3 — ASC Media Hash List (ASC MHL v2.0) export.

Translates an `st_manifest.json` into an ASC MHL v2.0 `.mhl` sidecar so post
houses can verify Signal Theory deliveries with their own tools (Silverstack,
YoYotta and similar) without trusting our app. All hash data already lives in
the manifest, so this is a format translation with no rehashing.

The output conforms to the published ASC MHL v2.0 schema (namespace
`urn:ASC:MHL:v2.0`, schema file `xsd/ASCMHL.xsd` in github.com/ascmitc/mhl):
a single `<hashlist version="2.0">` with `<creatorinfo>`, `<processinfo>` and a
`<hashes>` block of `<hash>` entries.

Hash mapping (manifest key to MHL element). The ASC MHL schema defines exactly
six hash elements: c4, md5, sha1, xxh128, xxh3, xxh64. There is **no sha256
element** (and M13 removed sha256 as a writer key anyway); every manifest we
write carries an MHL-compatible hash — xxh128 for local entries, md5 for Drive,
plus xxh128 on Drive entries where local bytes were available — so each file
still gets a verifiable hash element.

Pure logic, no PyQt6. The current time, hostname and tool version are injectable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.version import __version__ as APP_VERSION

MHL_NAMESPACE = "urn:ASC:MHL:v2.0"
MHL_VERSION = "2.0"
TOOL_NAME = "ST SyncTool"

# Manifest checksum key -> MHL element name, in the schema's element order.
# sha256 is deliberately absent: the ASC MHL v2.0 schema has no sha256 element.
MANIFEST_TO_MHL = (
    ("c4", "c4"),
    ("md5", "md5"),
    ("sha1", "sha1"),
    ("xxh128", "xxh128"),
    ("xxh64", "xxh64"),
)

ET.register_namespace("", MHL_NAMESPACE)


@dataclass(frozen=True)
class MhlExportResult:
    """Outcome of an MHL export."""
    path: Path
    hashed_count: int       # files that got at least one MHL hash element
    unhashed: list          # rel paths with no MHL-compatible hash element


def _q(tag: str) -> str:
    return f"{{{MHL_NAMESPACE}}}{tag}"


def _mhl_hashes_for(checksums: dict) -> list:
    """Return ``[(mhl_element, hexdigest), ...]`` in schema order for an entry."""
    out = []
    for manifest_key, mhl_el in MANIFEST_TO_MHL:
        digest = checksums.get(manifest_key)
        if digest:
            out.append((mhl_el, digest))
    return out


def build_mhl_tree(
    manifest: dict,
    *,
    now: Optional[datetime] = None,
    hostname: Optional[str] = None,
    tool_version: str = APP_VERSION,
    process: str = "transfer",
) -> "tuple[ET.ElementTree, MhlExportResult]":
    """Build an ASC MHL v2.0 ElementTree from a loaded manifest dict.

    Returns the tree and an :class:`MhlExportResult` (the ``path`` is unset here;
    :func:`write_mhl` fills it). ``process`` must be one of the schema's
    enumeration values: in-place, transfer or flatten.
    """
    when = (now or datetime.now()).isoformat(timespec="seconds")
    host = hostname or manifest.get("workstation") or ""
    created = manifest.get("created_at") or when

    root = ET.Element(_q("hashlist"), {"version": MHL_VERSION})

    creator = ET.SubElement(root, _q("creatorinfo"))
    ET.SubElement(creator, _q("creationdate")).text = created
    ET.SubElement(creator, _q("hostname")).text = host
    tool = ET.SubElement(creator, _q("tool"), {"version": tool_version})
    tool.text = TOOL_NAME

    proc_info = ET.SubElement(root, _q("processinfo"))
    ET.SubElement(proc_info, _q("process")).text = process

    hashes_el = ET.SubElement(root, _q("hashes"))

    files = manifest.get("files", {}) or {}
    hashed_count = 0
    unhashed = []
    for rel in sorted(files):
        entry = files[rel] or {}
        if entry.get("type") not in (None, "file"):
            continue
        pairs = _mhl_hashes_for(entry.get("checksums", {}) or {})
        hash_el = ET.SubElement(hashes_el, _q("hash"))
        path_attrs = {}
        if entry.get("size") is not None:
            path_attrs["size"] = str(entry["size"])
        if entry.get("modtime"):
            path_attrs["lastmodificationdate"] = entry["modtime"]
        path_el = ET.SubElement(hash_el, _q("path"), path_attrs)
        # M15.1 normalisation pin (external contract): the MHL path is the TRUE
        # on-disk name, written verbatim — case and Unicode form (NFC/NFD)
        # preserved exactly. Post houses match files by the real on-disk name, so
        # this MUST NOT be normalised. The lowercase+NFC normalisation in
        # core/merkle.normalise_path is for the internal folder-root fingerprint
        # ONLY and must never leak here (it would record Shot_01A.mov as
        # shot_01a.mov and the post house could not locate the file).
        path_el.text = rel
        for mhl_el, digest in pairs:
            el = ET.SubElement(hash_el, _q(mhl_el), {"action": "original", "hashdate": created})
            el.text = digest
        if pairs:
            hashed_count += 1
        else:
            unhashed.append(rel)

    ET.indent(root, space="    ")
    tree = ET.ElementTree(root)
    result = MhlExportResult(path=Path(), hashed_count=hashed_count, unhashed=unhashed)
    return tree, result


def mhl_string(manifest: dict, **kwargs) -> str:
    """Render the MHL document as a UTF-8 XML string (for tests/preview)."""
    tree, _ = build_mhl_tree(manifest, **kwargs)
    return ET.tostring(tree.getroot(), encoding="unicode", xml_declaration=True)


def write_mhl(manifest: dict, dest_path, **kwargs) -> MhlExportResult:
    """Write an ASC MHL v2.0 file to ``dest_path`` (atomic) and return the result."""
    dest = Path(dest_path)
    tree, result = build_mhl_tree(manifest, **kwargs)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tree.write(str(tmp), encoding="utf-8", xml_declaration=True)
    tmp.replace(dest)
    return MhlExportResult(path=dest, hashed_count=result.hashed_count,
                           unhashed=result.unhashed)


def default_mhl_path(manifest: dict, base_dir) -> Path:
    """Sidecar path next to a manifest: ``<base_dir>/<label>.mhl`` (label sanitised)."""
    label = (manifest.get("label") or "st_synctool").strip() or "st_synctool"
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in label)
    return Path(base_dir) / f"{safe}.mhl"


def export_mhl_alongside_manifest(manifest_path, **kwargs) -> MhlExportResult:
    """Load a manifest file and write an ``.mhl`` next to it. Convenience wrapper."""
    from core.manifest import load_manifest
    mp = Path(manifest_path)
    manifest = load_manifest(mp)
    dest = default_mhl_path(manifest, mp.parent)
    return write_mhl(manifest, dest, **kwargs)
