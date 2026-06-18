"""Tests for core/manifest.py and core/transfer.py — pure helper functions.

_primary_algorithm, _project_id, and estimate_time_seconds had zero test
coverage. _project_id is used to correlate manifests across sessions; a
collision or instability would break project history lookup.
"""

import pytest
from core.manifest import _primary_algorithm, _project_id, preferred_algorithm
from core.transfer import estimate_time_seconds


# ---------------------------------------------------------------------------
# _primary_algorithm  (M13.3: xxh128 is the content-identity key on both paths;
# the gdrive path stores md5 alongside as rclone's transport key)
# ---------------------------------------------------------------------------

class TestPrimaryAlgorithm:
    def test_returns_xxh128_for_gdrive(self):
        assert _primary_algorithm(gdrive=True) == "xxh128"

    def test_returns_xxh128_for_local(self):
        assert _primary_algorithm(gdrive=False) == "xxh128"


# ---------------------------------------------------------------------------
# preferred_algorithm — xxh128 > md5 > "" (M13.3)
# ---------------------------------------------------------------------------

class TestPreferredAlgorithm:
    def test_prefers_xxh128(self):
        assert preferred_algorithm({"xxh128": "a", "md5": "b"}) == "xxh128"

    def test_falls_back_to_md5(self):
        assert preferred_algorithm({"md5": "b"}) == "md5"

    def test_empty_when_neither_present(self):
        assert preferred_algorithm({}) == ""
        assert preferred_algorithm(None) == ""


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


# ---------------------------------------------------------------------------
# M1.5 coverage top-up: full coverage of all four helpers
# ---------------------------------------------------------------------------

import json as _json
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

from core.manifest_helpers import (
    fmt_date, fmt_size, manifest_age_days, manifest_age_days_from_iso,
)


class TestManifestAgeDaysFromIso:
    def test_empty_string_is_zero(self):
        assert manifest_age_days_from_iso("") == 0

    def test_garbage_is_zero(self):
        assert manifest_age_days_from_iso("not-a-date") == 0

    def test_ten_days_ago(self):
        iso = (_dt.now(_tz.utc) - _td(days=10)).isoformat()
        assert manifest_age_days_from_iso(iso) == 10

    def test_future_date_clamped_to_zero(self):
        iso = (_dt.now(_tz.utc) + _td(days=5)).isoformat()
        assert manifest_age_days_from_iso(iso) == 0


class TestManifestAgeDays:
    def test_reads_created_at_field(self, tmp_path):
        iso = (_dt.now(_tz.utc) - _td(days=3)).isoformat()
        p = tmp_path / "m.json"
        p.write_text(_json.dumps({"created_at": iso}))
        assert manifest_age_days(str(p)) == 3

    def test_falls_back_to_mtime_on_bad_json(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text("{not json")
        # freshly written file -> 0 days via mtime fallback
        assert manifest_age_days(str(p)) == 0

    def test_missing_file_is_zero(self, tmp_path):
        assert manifest_age_days(str(tmp_path / "nope.json")) == 0


class TestFmtDate:
    def test_empty_is_empty(self):
        assert fmt_date("") == ""

    def test_valid_iso_renders_ymd_hm(self):
        out = fmt_date("2026-06-12T10:30:00+00:00")
        assert out.startswith("2026-06-1")  # local tz may shift the day
        assert ":" in out

    def test_garbage_truncated_to_16(self):
        assert fmt_date("x" * 40) == "x" * 16


class TestFmtSize:
    def test_none_is_unknown(self):
        assert fmt_size(None) == "unknown"

    def test_non_numeric_passthrough(self):
        assert fmt_size("lots") == "lots"

    def test_bytes(self):
        assert fmt_size(512) == "512 B"

    def test_kilobytes(self):
        assert fmt_size(4096) == "4.0 KB"

    def test_gigabytes(self):
        assert fmt_size(int(1.2 * 1024**3)) == "1.2 GB"

    def test_petabytes(self):
        assert fmt_size(3 * 1024**5).endswith("PB")
