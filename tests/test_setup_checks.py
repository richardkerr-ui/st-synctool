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


# ---------------------------------------------------------------------------
# check_rclone_auth
# ---------------------------------------------------------------------------

class TestCheckRcloneAuth:
    def _run(self, returncode, stdout="", stderr=""):
        from unittest.mock import MagicMock, patch
        from core.setup_checks import check_rclone_auth
        mock = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
        with patch("subprocess.run", return_value=mock):
            return check_rclone_auth("gdrive", timeout=5)

    def test_ok_when_returncode_zero(self):
        result = self._run(0, stdout="  folder1\n  folder2\n")
        assert result.status == CheckStatus.OK

    def test_ok_message_includes_folder_count(self):
        result = self._run(0, stdout="  folder1\n  folder2\n")
        assert "2" in result.message

    def test_empty_stdout_ok_with_zero_folders(self):
        result = self._run(0, stdout="")
        assert result.status == CheckStatus.OK
        assert "0" in result.message

    def test_nonzero_returncode_gives_error(self):
        result = self._run(1, stderr="Token expired")
        assert result.status == CheckStatus.ERROR

    def test_error_message_includes_stderr(self):
        result = self._run(1, stderr="Token expired")
        assert "Token expired" in result.message

    def test_timeout_gives_error(self):
        import subprocess
        from unittest.mock import patch
        from core.setup_checks import check_rclone_auth
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="rclone", timeout=5)):
            result = check_rclone_auth("gdrive", timeout=5)
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()

    def test_os_error_gives_error(self):
        from unittest.mock import patch
        from core.setup_checks import check_rclone_auth
        with patch("subprocess.run", side_effect=OSError("rclone not found")):
            result = check_rclone_auth("gdrive", timeout=5)
        assert result.status == CheckStatus.ERROR

    def test_result_name_includes_remote(self):
        result = self._run(0)
        assert "gdrive" in result.name

    def test_long_stderr_truncated_to_200_chars(self):
        long_err = "x" * 500
        result = self._run(1, stderr=long_err)
        assert len(result.message) < 400  # truncated, not raw 500-char dump
