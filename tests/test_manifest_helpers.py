"""Tests for core/manifest.py and core/transfer.py — pure helper functions.

_primary_algorithm, _project_id, and estimate_time_seconds had zero test
coverage. _project_id is used to correlate manifests across sessions; a
collision or instability would break project history lookup.
"""

import pytest
from core.manifest import _primary_algorithm, _project_id
from core.transfer import estimate_time_seconds


# ---------------------------------------------------------------------------
# _primary_algorithm
# ---------------------------------------------------------------------------

class TestPrimaryAlgorithm:
    def test_returns_md5_for_gdrive(self):
        assert _primary_algorithm(gdrive=True) == "md5"

    def test_returns_sha256_for_local(self):
        assert _primary_algorithm(gdrive=False) == "sha256"


# ---------------------------------------------------------------------------
# _project_id
# ---------------------------------------------------------------------------

class TestProjectId:
    def test_returns_12_char_hex(self):
        pid = _project_id("/local/project", "/server/project")
        assert len(pid) == 12
        assert all(c in "0123456789abcdef" for c in pid)

    def test_empty_local_path_returns_empty_string(self):
        assert _project_id("", "/server") == ""

    def test_same_paths_produce_same_id(self):
        a = _project_id("/local", "/server")
        b = _project_id("/local", "/server")
        assert a == b

    def test_different_local_path_produces_different_id(self):
        a = _project_id("/local/a", "/server")
        b = _project_id("/local/b", "/server")
        assert a != b

    def test_different_server_path_produces_different_id(self):
        a = _project_id("/local", "/server/a")
        b = _project_id("/local", "/server/b")
        assert a != b

    def test_empty_counterpart_path_is_stable(self):
        a = _project_id("/local", "")
        b = _project_id("/local", None)
        assert a == b

    def test_order_matters(self):
        # Swapping local/server should produce a different ID
        a = _project_id("/alpha", "/beta")
        b = _project_id("/beta", "/alpha")
        assert a != b


# ---------------------------------------------------------------------------
# estimate_time_seconds
# ---------------------------------------------------------------------------

class TestEstimateTimeSeconds:
    def test_zero_bytes_is_zero(self):
        assert estimate_time_seconds(0) == 0.0

    def test_known_value_at_default_speed(self):
        # 150 MB/s default: 150 * 1024^2 bytes = 1 second
        one_second_bytes = 150 * 1024 * 1024
        assert estimate_time_seconds(one_second_bytes) == pytest.approx(1.0)

    def test_custom_speed(self):
        # At 300 MB/s, same data takes half the time
        one_second_at_150 = 150 * 1024 * 1024
        result = estimate_time_seconds(one_second_at_150, speed_mbps=300.0)
        assert result == pytest.approx(0.5)

    def test_1gb_at_150_mbps(self):
        one_gb = 1024 ** 3
        secs = estimate_time_seconds(one_gb)
        # 1 GB / 150 MB/s ≈ 6.83 seconds
        assert secs == pytest.approx(1024 / 150, rel=1e-4)

    def test_result_is_float(self):
        result = estimate_time_seconds(1024 * 1024)
        assert isinstance(result, float)
