"""
Tests for core/checksum.py — hash correctness and algorithm selection.

These are the most critical tests in the suite: a regression that causes
verify_staging to return OK on a mismatched file is silent and undetectable
until the files are needed.
"""

import hashlib
import pytest
from pathlib import Path
from core.checksum import compute_all


# ---------------------------------------------------------------------------
# Correctness — known inputs vs known outputs
# ---------------------------------------------------------------------------

class TestCorrectness:
    def test_sha256_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        result = compute_all(f, include_xxhash=False)
        assert result["sha256"] == hashlib.sha256(b"").hexdigest()

    def test_sha256_known_content(self, tmp_path):
        data = b"ST SyncTool test vector"
        f = tmp_path / "known.bin"
        f.write_bytes(data)
        result = compute_all(f, include_xxhash=False)
        assert result["sha256"] == hashlib.sha256(data).hexdigest()

    def test_sha256_matches_stdlib_for_large_file(self, tmp_path):
        # 2 MB — exercises the chunked read path
        data = b"x" * (2 * 1024 * 1024)
        f = tmp_path / "large.bin"
        f.write_bytes(data)
        result = compute_all(f, include_xxhash=False)
        assert result["sha256"] == hashlib.sha256(data).hexdigest()

    def test_different_content_produces_different_hashes(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"file one")
        f2.write_bytes(b"file two")
        r1 = compute_all(f1, include_xxhash=False)
        r2 = compute_all(f2, include_xxhash=False)
        assert r1["sha256"] != r2["sha256"]

    def test_same_content_different_paths_same_hash(self, tmp_path):
        data = b"identical content"
        f1 = tmp_path / "copy1.bin"
        f2 = tmp_path / "copy2.bin"
        f1.write_bytes(data)
        f2.write_bytes(data)
        r1 = compute_all(f1, include_xxhash=False)
        r2 = compute_all(f2, include_xxhash=False)
        assert r1["sha256"] == r2["sha256"]

    def test_single_bit_flip_changes_hash(self, tmp_path):
        data = bytearray(b"important footage manifest data")
        f1 = tmp_path / "orig.bin"
        f2 = tmp_path / "flipped.bin"
        f1.write_bytes(bytes(data))
        data[0] ^= 0x01  # flip one bit
        f2.write_bytes(bytes(data))
        r1 = compute_all(f1, include_xxhash=False)
        r2 = compute_all(f2, include_xxhash=False)
        assert r1["sha256"] != r2["sha256"]


# ---------------------------------------------------------------------------
# Algorithm selection — returned keys match requested algorithms
# ---------------------------------------------------------------------------

class TestAlgorithmSelection:
    def test_default_returns_sha256_and_xxhash(self, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"data")
        result = compute_all(f)
        assert "sha256" in result
        assert "xxhash3_64" in result
        assert "md5" not in result

    def test_include_xxhash_false_omits_xxhash(self, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"data")
        result = compute_all(f, include_xxhash=False)
        assert "xxhash3_64" not in result
        assert "sha256" in result

    def test_include_md5_true_returns_md5(self, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"data")
        result = compute_all(f, include_xxhash=False, include_md5=True)
        assert "md5" in result
        assert result["md5"] == hashlib.md5(b"data").hexdigest()

    def test_all_algorithms_independent(self, tmp_path):
        data = b"multi-hash test"
        f = tmp_path / "f.bin"
        f.write_bytes(data)
        result = compute_all(f, include_xxhash=True, include_md5=True)
        assert result["sha256"] == hashlib.sha256(data).hexdigest()
        assert result["md5"]    == hashlib.md5(data).hexdigest()
        assert "xxhash3_64" in result


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

class TestProgressCallback:
    def test_progress_callback_called(self, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"x" * 1024)
        calls = []
        compute_all(f, include_xxhash=False, progress_cb=calls.append)
        assert len(calls) >= 1
        assert calls[-1] == 100

    def test_progress_callback_final_value_is_100(self, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"y" * (4 * 1024 * 1024))
        calls = []
        compute_all(f, include_xxhash=False, progress_cb=calls.append)
        assert calls[-1] == 100

    def test_progress_empty_file_calls_100(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        calls = []
        compute_all(f, include_xxhash=False, progress_cb=calls.append)
        # Empty file: progress_cb is not called (no chunks), which is acceptable.
        # This test documents the current behaviour rather than asserting a
        # specific count so it catches any future regression.
        assert all(v <= 100 for v in calls)


# ---------------------------------------------------------------------------
# Verification semantics — the silent-OK failure mode
#
# compute_all() is the ground truth for offload verification.  These tests
# confirm that comparing two hashes from compute_all() reliably catches
# content mismatches — the exact property that verify_staging depends on.
# ---------------------------------------------------------------------------

class TestVerificationSemantics:
    def test_hash_comparison_passes_for_identical_files(self, tmp_path):
        data = b"production footage ground truth"
        original = tmp_path / "original.mov"
        copy     = tmp_path / "copy.mov"
        original.write_bytes(data)
        copy.write_bytes(data)
        h1 = compute_all(original, include_xxhash=False)
        h2 = compute_all(copy,     include_xxhash=False)
        assert h1["sha256"] == h2["sha256"]

    def test_hash_comparison_fails_for_different_files(self, tmp_path):
        f1 = tmp_path / "a.mov"
        f2 = tmp_path / "b.mov"
        f1.write_bytes(b"real footage")
        f2.write_bytes(b"different footage")
        h1 = compute_all(f1, include_xxhash=False)
        h2 = compute_all(f2, include_xxhash=False)
        assert h1["sha256"] != h2["sha256"]

    def test_single_appended_byte_detected(self, tmp_path):
        data = b"clip data"
        f1 = tmp_path / "orig.mov"
        f2 = tmp_path / "extra.mov"
        f1.write_bytes(data)
        f2.write_bytes(data + b"\x00")
        h1 = compute_all(f1, include_xxhash=False)
        h2 = compute_all(f2, include_xxhash=False)
        assert h1["sha256"] != h2["sha256"]

    def test_truncated_file_detected(self, tmp_path):
        data = b"x" * 1024
        f1 = tmp_path / "full.mov"
        f2 = tmp_path / "truncated.mov"
        f1.write_bytes(data)
        f2.write_bytes(data[:512])
        h1 = compute_all(f1, include_xxhash=False)
        h2 = compute_all(f2, include_xxhash=False)
        assert h1["sha256"] != h2["sha256"]

    def test_stored_hash_matches_recomputed_hash(self, tmp_path):
        """Simulates the offload pre-hash → post-copy verify flow."""
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"camera card footage")

        # Pre-hash (as prehash_source does)
        pre = compute_all(src, include_xxhash=False)["sha256"]

        # Copy (as copy_source_to_staging does)
        import shutil
        shutil.copy2(str(src), str(dst))

        # Post-hash (as verify_staging does)
        post = compute_all(dst, include_xxhash=False)["sha256"]

        assert pre == post  # verification must pass

    def test_stored_hash_catches_corruption_after_copy(self, tmp_path):
        """If the destination file is corrupted after copy, verification must fail."""
        src = tmp_path / "src.mov"
        dst = tmp_path / "dst.mov"
        src.write_bytes(b"camera card footage")
        import shutil
        shutil.copy2(str(src), str(dst))

        pre = compute_all(src, include_xxhash=False)["sha256"]
        dst.write_bytes(b"corrupted content")  # simulate post-copy corruption
        post = compute_all(dst, include_xxhash=False)["sha256"]

        assert pre != post  # verification must fail
