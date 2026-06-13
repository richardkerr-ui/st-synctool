"""Tests for core/update_check.py (M7.5 update checker)."""

import pytest

from core import update_check as uc


# ── version parsing / comparison ─────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("v1.2.3-beta", (1, 2, 3)),
    ("release 2.0.0 final", (2, 0, 0)),
])
def test_parse_version_ok(text, expected):
    assert uc.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", None, "vX.Y.Z", "1.2", "latest"])
def test_parse_version_rejects_malformed(text):
    assert uc.parse_version(text) is None


@pytest.mark.parametrize("latest,current,expected", [
    ("1.0.1", "1.0.0", True),
    ("v1.1.0", "1.0.9", True),
    ("2.0.0", "1.9.9", True),
    ("1.0.0", "1.0.0", False),     # same
    ("1.0.0", "1.0.1", False),     # older
    ("bad", "1.0.0", False),       # unparseable latest -> never nag
    ("1.0.1", "bad", False),       # unparseable current -> never nag
])
def test_is_newer(latest, current, expected):
    assert uc.is_newer(latest, current) is expected


# ── release parsing ──────────────────────────────────────────────────────────

def test_parse_release_ok():
    info = uc.parse_release({"tag_name": "v1.2.0",
                             "html_url": "https://github.com/x/y/releases/v1.2.0"})
    assert info.version == "v1.2.0"
    assert info.url.endswith("v1.2.0")


def test_parse_release_falls_back_to_name_and_default_url():
    info = uc.parse_release({"name": "v1.3.0"})
    assert info.version == "v1.3.0"
    assert "releases/latest" in info.url


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"tag_name": ""},
                                     {"tag_name": "   "}])
def test_parse_release_malformed_returns_none(payload):
    assert uc.parse_release(payload) is None


# ── check_for_update: newer / same / older / malformed / offline ─────────────

def test_check_returns_info_when_newer():
    fetch = lambda url, timeout: {"tag_name": "v2.0.0",
                                  "html_url": "https://example/r"}
    info = uc.check_for_update("1.0.0", fetch_fn=fetch)
    assert info is not None and info.version == "v2.0.0"


def test_check_none_when_same():
    fetch = lambda url, timeout: {"tag_name": "v1.0.0"}
    assert uc.check_for_update("1.0.0", fetch_fn=fetch) is None


def test_check_none_when_older():
    fetch = lambda url, timeout: {"tag_name": "v0.9.0"}
    assert uc.check_for_update("1.0.0", fetch_fn=fetch) is None


def test_check_none_when_malformed():
    fetch = lambda url, timeout: {"unexpected": "shape"}
    assert uc.check_for_update("1.0.0", fetch_fn=fetch) is None


def test_check_silent_when_offline():
    def boom(url, timeout):
        raise OSError("network unreachable")
    assert uc.check_for_update("1.0.0", fetch_fn=boom) is None


def test_check_passes_timeout():
    seen = {}
    def fetch(url, timeout):
        seen["timeout"] = timeout
        return {"tag_name": "v0.0.1"}
    uc.check_for_update("1.0.0", fetch_fn=fetch)
    assert seen["timeout"] == uc.REQUEST_TIMEOUT_SECONDS


def test_banner_text():
    info = uc.UpdateInfo(version="v1.2.0", url="https://x")
    text = uc.update_banner_text(info, current="1.0.0")
    assert "v1.2.0" in text and "v1.0.0" in text


def test_default_current_is_app_version():
    # check_for_update defaults to the app's own version constant.
    fetch = lambda url, timeout: {"tag_name": f"v{uc.APP_VERSION}"}
    assert uc.check_for_update(fetch_fn=fetch) is None  # same version, no update
