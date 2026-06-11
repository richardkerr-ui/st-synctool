"""Tests for core/setup_checks.py — CheckResult and CheckStatus.

CheckResult has 23 callers and had zero test coverage.
"""

import pytest
from core.setup_checks import CheckResult, CheckStatus


class TestCheckResultOkProperty:
    def test_ok_status_returns_true(self):
        r = CheckResult(name="test", status=CheckStatus.OK, message="all good")
        assert r.ok is True

    def test_missing_status_returns_false(self):
        r = CheckResult(name="test", status=CheckStatus.MISSING, message="gone")
        assert r.ok is False

    def test_error_status_returns_false(self):
        r = CheckResult(name="test", status=CheckStatus.ERROR, message="oops")
        assert r.ok is False

    def test_warning_status_returns_false(self):
        r = CheckResult(name="test", status=CheckStatus.WARNING, message="hmm")
        assert r.ok is False


class TestCheckResultDefaults:
    def test_fix_hint_defaults_to_none(self):
        r = CheckResult(name="n", status=CheckStatus.OK, message="m")
        assert r.fix_hint is None

    def test_can_auto_fix_defaults_to_false(self):
        r = CheckResult(name="n", status=CheckStatus.OK, message="m")
        assert r.can_auto_fix is False

    def test_fix_command_defaults_to_none(self):
        r = CheckResult(name="n", status=CheckStatus.OK, message="m")
        assert r.fix_command is None


class TestCheckResultFields:
    def test_name_stored(self):
        r = CheckResult(name="rclone", status=CheckStatus.OK, message="v1.60")
        assert r.name == "rclone"

    def test_message_stored(self):
        r = CheckResult(name="n", status=CheckStatus.ERROR, message="rclone not found")
        assert r.message == "rclone not found"

    def test_fix_hint_stored(self):
        r = CheckResult(
            name="n", status=CheckStatus.MISSING, message="m",
            fix_hint="brew install rclone"
        )
        assert r.fix_hint == "brew install rclone"

    def test_fix_command_stored(self):
        cmd = ["brew", "install", "rclone"]
        r = CheckResult(
            name="n", status=CheckStatus.MISSING, message="m",
            fix_command=cmd
        )
        assert r.fix_command == cmd

    def test_can_auto_fix_stored(self):
        r = CheckResult(
            name="n", status=CheckStatus.MISSING, message="m",
            can_auto_fix=True
        )
        assert r.can_auto_fix is True


class TestCheckStatus:
    def test_all_statuses_present(self):
        statuses = {s.value for s in CheckStatus}
        assert statuses == {"ok", "missing", "error", "warning"}

    def test_ok_value(self):
        assert CheckStatus.OK.value == "ok"

    def test_missing_value(self):
        assert CheckStatus.MISSING.value == "missing"

    def test_error_value(self):
        assert CheckStatus.ERROR.value == "error"

    def test_warning_value(self):
        assert CheckStatus.WARNING.value == "warning"
