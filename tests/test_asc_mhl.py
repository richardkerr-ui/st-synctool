"""Tests for M10.3 ASC MHL v2.0 export (core/asc_mhl.py).

Validates the output against the published ASC MHL v2.0 schema structure:
namespace, version, required creatorinfo/processinfo/hashes elements, hash
element names and the no-sha256 rule.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from core import asc_mhl
from core.version import __version__ as APP_VERSION

NS = asc_mhl.MHL_NAMESPACE


def _manifest(**over):
    m = {
        "schema_version": "1.1",
        "created_at": "2026-06-13T10:00:00+00:00",
        "label": "ProjectX A001",
        "workstation": "Cart 3",
        "files": {
            "DCIM/A001/clip_002.mov": {
                "type": "file", "size": 2048, "modtime": "2026-06-10T09:00:00+00:00",
                "checksums": {"xxh128": "1234abcd"},
                "hash_algorithm": "xxh128",
            },
            "DCIM/A001/clip_001.mov": {
                "type": "file", "size": 1024, "modtime": "2026-06-10T08:00:00+00:00",
                "checksums": {"xxh128": "b" * 32, "md5": "deadbeef"},
                "hash_algorithm": "xxh128",
            },
        },
    }
    m.update(over)
    return m


def _root(manifest, **kw):
    tree, result = asc_mhl.build_mhl_tree(manifest, **kw)
    return tree.getroot(), result


def _q(tag):
    return f"{{{NS}}}{tag}"


def test_root_namespace_and_version():
    root, _ = _root(_manifest())
    assert root.tag == _q("hashlist")
    assert root.get("version") == "2.0"


def test_required_top_level_sequence():
    root, _ = _root(_manifest())
    children = [c.tag for c in root]
    assert children[:3] == [_q("creatorinfo"), _q("processinfo"), _q("hashes")]


def test_creatorinfo_fields():
    root, _ = _root(_manifest(), hostname=None)
    ci = root.find(_q("creatorinfo"))
    assert ci.find(_q("creationdate")).text == "2026-06-13T10:00:00+00:00"
    assert ci.find(_q("hostname")).text == "Cart 3"  # from manifest workstation
    tool = ci.find(_q("tool"))
    assert tool.text == "ST SyncTool"
    assert tool.get("version") == APP_VERSION


def test_processinfo_process_value():
    root, _ = _root(_manifest(), process="transfer")
    proc = root.find(_q("processinfo")).find(_q("process"))
    assert proc.text == "transfer"


def test_hash_entries_sorted_with_path_attrs():
    root, _ = _root(_manifest())
    hashes = root.find(_q("hashes")).findall(_q("hash"))
    assert len(hashes) == 2
    first_path = hashes[0].find(_q("path"))
    assert first_path.text == "DCIM/A001/clip_001.mov"  # sorted
    assert first_path.get("size") == "1024"
    assert first_path.get("lastmodificationdate") == "2026-06-10T08:00:00+00:00"


def test_xxh128_maps_to_xxh128_element():
    # M13: manifest xxh128 maps to MHL's native <xxh128> element (the prior
    # xxhash3_64 → <xxh3> mapping was removed with the key).
    root, _ = _root(_manifest())
    hashes = {h.find(_q("path")).text: h for h in root.find(_q("hashes")).findall(_q("hash"))}
    h = hashes["DCIM/A001/clip_002.mov"]
    xxh128 = h.find(_q("xxh128"))
    assert xxh128 is not None
    assert xxh128.text == "1234abcd"
    assert xxh128.get("action") == "original"
    assert xxh128.get("hashdate") == "2026-06-13T10:00:00+00:00"


def test_md5_maps_to_md5_element():
    root, _ = _root(_manifest())
    hashes = {h.find(_q("path")).text: h for h in root.find(_q("hashes")).findall(_q("hash"))}
    h = hashes["DCIM/A001/clip_001.mov"]
    assert h.find(_q("md5")).text == "deadbeef"


def test_sha256_is_never_exported():
    # sha256 has no ASC MHL element; it must not appear anywhere.
    s = asc_mhl.mhl_string(_manifest())
    assert "sha256" not in s
    assert "a" * 64 not in s  # the sha256 digest value is absent
    assert _q("sha256") not in s


def test_unhashed_file_reported_and_path_still_written():
    # A foreign manifest entry whose only hash has no ASC MHL element (here a
    # crc32) is reported as unhashed but its path is still written.
    m = _manifest(files={
        "only_crc.mov": {"type": "file", "size": 10, "modtime": "2026-06-10T08:00:00+00:00",
                         "checksums": {"crc32": "c" * 8}},
    })
    root, result = _root(m)
    assert result.hashed_count == 0
    assert result.unhashed == ["only_crc.mov"]
    h = root.find(_q("hashes")).find(_q("hash"))
    assert h.find(_q("path")).text == "only_crc.mov"
    # No hash sub-elements present
    assert h.find(_q("md5")) is None and h.find(_q("xxh128")) is None


def test_directory_entries_skipped():
    m = _manifest(files={
        "DCIM": {"type": "dir"},
        "f.mov": {"type": "file", "size": 1, "checksums": {"md5": "ab"}},
    })
    root, result = _root(m)
    paths = [h.find(_q("path")).text for h in root.find(_q("hashes")).findall(_q("hash"))]
    assert paths == ["f.mov"]
    assert result.hashed_count == 1


def test_hash_element_order_matches_schema():
    # c4, md5, sha1, xxh128, xxh64 — schema order; md5 before xxh128.
    m = _manifest(files={
        "f.mov": {"type": "file", "size": 1, "modtime": "",
                  "checksums": {"xxh128": "x", "md5": "m"}},
    })
    root, _ = _root(m)
    h = root.find(_q("hashes")).find(_q("hash"))
    tags = [c.tag for c in h if c.tag != _q("path")]
    assert tags == [_q("md5"), _q("xxh128")]


def test_write_mhl_atomic_and_parseable(tmp_path):
    dest = tmp_path / "out" / "ProjectX.mhl"
    result = asc_mhl.write_mhl(_manifest(), dest, now=datetime(2026, 6, 13))
    assert result.path == dest
    assert dest.exists()
    assert not (dest.parent / "ProjectX.mhl.tmp").exists()
    # Re-parse to confirm well-formed XML with the right root.
    parsed = ET.parse(str(dest)).getroot()
    assert parsed.tag == _q("hashlist")
    assert result.hashed_count == 2


def test_mhl_string_has_declaration_and_namespace():
    s = asc_mhl.mhl_string(_manifest())
    assert s.startswith("<?xml")
    assert 'xmlns="urn:ASC:MHL:v2.0"' in s or "urn:ASC:MHL:v2.0" in s


def test_default_mhl_path_sanitises_label(tmp_path):
    p = asc_mhl.default_mhl_path(_manifest(label="A001 / Day:1"), tmp_path)
    assert p.parent == tmp_path
    assert p.name == "A001___Day_1.mhl"


def test_export_alongside_manifest(tmp_path):
    import json
    mp = tmp_path / "st_manifest.json"
    mp.write_text(json.dumps(_manifest()))
    result = asc_mhl.export_mhl_alongside_manifest(mp, now=datetime(2026, 6, 13))
    assert result.path == tmp_path / "ProjectX_A001.mhl"
    assert result.path.exists()


# --------------------------------------------------------------------------- #
# Validate against the bundled published ASC MHL v2.0 schema (skips if no
# XSD validator is installed; xmlschema is not a project dependency).
# --------------------------------------------------------------------------- #

XSD_PATH = Path(__file__).parent / "fixtures" / "ASCMHL.xsd"


def test_output_validates_against_published_xsd(tmp_path):
    xmlschema = pytest.importorskip("xmlschema")
    dest = tmp_path / "v.mhl"
    asc_mhl.write_mhl(_manifest(), dest, now=datetime(2026, 6, 13))
    schema = xmlschema.XMLSchema(str(XSD_PATH))
    schema.validate(str(dest))  # raises on any schema violation


# --------------------------------------------------------------------------- #
# Flow wiring: transfer + offload emit a sidecar only when export_mhl is set.
# --------------------------------------------------------------------------- #

def test_transfer_folder_export_mhl_writes_sidecar(tmp_path):
    from core import transfer
    src = tmp_path / "src"; src.mkdir()
    (src / "a.txt").write_text("hello")
    dst = tmp_path / "dst"
    transfer.transfer_folder(src, dst, export_mhl=True)
    mhls = list(dst.rglob("*.mhl"))
    assert mhls, "expected a .mhl sidecar next to the destination manifest"
    root = ET.parse(str(mhls[0])).getroot()
    assert root.tag == _q("hashlist")


def test_transfer_folder_no_mhl_by_default(tmp_path):
    from core import transfer
    src = tmp_path / "src"; src.mkdir()
    (src / "a.txt").write_text("hello")
    dst = tmp_path / "dst"
    transfer.transfer_folder(src, dst)
    assert not list(dst.rglob("*.mhl"))


def test_save_offload_manifest_export_mhl(tmp_path, monkeypatch):
    import core.offload as off
    import core.manifest as man
    monkeypatch.setattr(man, "LOCAL_MANIFEST_DIR", tmp_path / "archive")
    # Minimal fake committed result + source.
    dest_dir = tmp_path / "dest"; dest_dir.mkdir()

    class _Src:
        label = "A001"
        def effective_subfolder(self): return "A001"

    class _Cell:
        final_path = dest_dir
        state = type("S", (), {"value": "DONE"})()

    src_manifest = {
        "label": "A001", "files": {
            "clip.mov": {"type": "file", "size": 5, "modtime": "2026-06-10T08:00:00+00:00",
                         "checksums": {"xxh128": "abcd"}}}}
    monkeypatch.setattr(off, "build_offload_manifest", lambda *a, **k: src_manifest)
    off.save_offload_manifest(_Src(), src_manifest, [_Cell()], export_mhl=True)
    assert list(dest_dir.rglob("*.mhl")), "offload should write a .mhl when export_mhl is set"


# ── M15.1 filename encoding: MHL preserves the true on-disk name ──────────────

def test_mhl_preserves_mixed_case_nfd_filename_even_when_folder_root_differs():
    """The MHL <path> must carry the exact on-disk name (case + Unicode form),
    NOT the lowercase/NFC form the internal folder-root fingerprint uses."""
    import unicodedata
    from core import merkle

    # A mixed-case, NFD-normalised name as macOS stores it on disk.
    on_disk = unicodedata.normalize("NFD", "Café_Shot_01A.mov")
    m = _manifest(files={
        on_disk: {"type": "file", "size": 1, "modtime": "2026-06-10T08:00:00+00:00",
                  "checksums": {"xxh128": "abcd"}},
    })
    root, _ = _root(m)
    paths = [h.find(_q("path")).text
             for h in root.find(_q("hashes")).findall(_q("hash"))]
    # MHL preserves the real name verbatim …
    assert paths == [on_disk]
    # … while the internal folder-root key for the same path is lowercased+NFC,
    # i.e. a different string — proving the two representations stay separate.
    folder_key = merkle.normalise_path(on_disk)
    assert folder_key != on_disk
    assert folder_key == unicodedata.normalize("NFC", "café_shot_01a.mov")
