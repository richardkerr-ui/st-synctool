"""Tests for core/setup_checks.py.

Covers the CheckResult/CheckStatus value objects plus every check function:
homebrew, rclone, python packages, rclone remote, rclone auth, the run_all_checks
orchestrator and create_gdrive_remote. All external calls (subprocess, shutil.which,
__import__, oauth credentials) are mocked, so these run headless in any environment.
"""

from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# check_homebrew
# ---------------------------------------------------------------------------

class TestCheckHomebrew:
    def test_missing_when_not_on_path(self):
        from core.setup_checks import check_homebrew
        with patch("shutil.which", return_value=None):
            result = check_homebrew()
        assert result.status == CheckStatus.MISSING
        assert result.can_auto_fix is False  # brew can't be auto-installed safely

    def test_ok_reports_version_line(self):
        from core.setup_checks import check_homebrew
        mock = MagicMock(stdout="Homebrew 4.2.1\n(more)\n")
        with patch("shutil.which", return_value="/opt/homebrew/bin/brew"), \
             patch("subprocess.run", return_value=mock):
            result = check_homebrew()
        assert result.status == CheckStatus.OK
        assert result.message == "Homebrew 4.2.1"

    def test_ok_with_empty_stdout_says_unknown(self):
        from core.setup_checks import check_homebrew
        mock = MagicMock(stdout="")
        with patch("shutil.which", return_value="/usr/local/bin/brew"), \
             patch("subprocess.run", return_value=mock):
            result = check_homebrew()
        assert result.status == CheckStatus.OK
        assert result.message == "unknown"

    def test_timeout_gives_error(self):
        import subprocess
        from core.setup_checks import check_homebrew
        with patch("shutil.which", return_value="/usr/local/bin/brew"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="brew", timeout=5)):
            result = check_homebrew()
        assert result.status == CheckStatus.ERROR

    def test_os_error_gives_error(self):
        from core.setup_checks import check_homebrew
        with patch("shutil.which", return_value="/usr/local/bin/brew"), \
             patch("subprocess.run", side_effect=OSError("boom")):
            result = check_homebrew()
        assert result.status == CheckStatus.ERROR


# ---------------------------------------------------------------------------
# check_rclone
# ---------------------------------------------------------------------------

class TestCheckRclone:
    def test_missing_when_not_on_path(self):
        from core.setup_checks import check_rclone
        with patch("shutil.which", return_value=None):
            result = check_rclone()
        assert result.status == CheckStatus.MISSING
        assert result.can_auto_fix is True
        assert result.fix_command == ["brew", "install", "rclone"]

    def test_ok_when_version_meets_minimum(self):
        from core.setup_checks import check_rclone
        mock = MagicMock(stdout="rclone v1.65.0\n- os/version\n")
        with patch("shutil.which", return_value="/usr/local/bin/rclone"), \
             patch("subprocess.run", return_value=mock):
            result = check_rclone()
        assert result.status == CheckStatus.OK
        assert result.message == "rclone v1.65.0"

    def test_warning_when_version_below_minimum(self):
        from core.setup_checks import check_rclone
        mock = MagicMock(stdout="rclone v1.50.0\n")
        with patch("shutil.which", return_value="/usr/local/bin/rclone"), \
             patch("subprocess.run", return_value=mock):
            result = check_rclone()
        assert result.status == CheckStatus.WARNING
        assert result.fix_command == ["brew", "upgrade", "rclone"]

    def test_warning_when_version_unparseable(self):
        from core.setup_checks import check_rclone
        mock = MagicMock(stdout="rclone unknown-build\n")
        with patch("shutil.which", return_value="/usr/local/bin/rclone"), \
             patch("subprocess.run", return_value=mock):
            result = check_rclone()
        assert result.status == CheckStatus.WARNING
        assert "could not be parsed" in result.message

    def test_timeout_gives_error(self):
        import subprocess
        from core.setup_checks import check_rclone
        with patch("shutil.which", return_value="/usr/local/bin/rclone"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="rclone", timeout=5)):
            result = check_rclone()
        assert result.status == CheckStatus.ERROR


# ---------------------------------------------------------------------------
# _parse_rclone_version
# ---------------------------------------------------------------------------

class TestParseRcloneVersion:
    def test_parses_standard_version_string(self):
        from core.setup_checks import _parse_rclone_version
        assert _parse_rclone_version("rclone v1.65.2") == (1, 65, 2)

    def test_returns_none_when_no_version(self):
        from core.setup_checks import _parse_rclone_version
        assert _parse_rclone_version("rclone (no version here)") is None


# ---------------------------------------------------------------------------
# check_python_packages
# ---------------------------------------------------------------------------

class TestCheckPythonPackages:
    def test_ok_when_all_present(self):
        from core.setup_checks import check_python_packages
        with patch("builtins.__import__", return_value=MagicMock()):
            result = check_python_packages()
        assert result.status == CheckStatus.OK

    def test_missing_lists_absent_packages(self):
        from core.setup_checks import check_python_packages
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "xxhash":
                raise ImportError("no xxhash")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = check_python_packages()
        assert result.status == CheckStatus.MISSING
        assert "xxhash" in result.message
        assert result.can_auto_fix is True


# ---------------------------------------------------------------------------
# check_rclone_remote
# ---------------------------------------------------------------------------

class TestCheckRcloneRemote:
    def test_ok_when_remote_present(self):
        from core.setup_checks import check_rclone_remote
        mock = MagicMock(stdout="gdrive:\nother:\n")
        with patch("subprocess.run", return_value=mock):
            result = check_rclone_remote("gdrive")
        assert result.status == CheckStatus.OK
        assert "other" in result.message

    def test_missing_when_remote_absent(self):
        from core.setup_checks import check_rclone_remote
        mock = MagicMock(stdout="other:\n")
        with patch("subprocess.run", return_value=mock):
            result = check_rclone_remote("gdrive")
        assert result.status == CheckStatus.MISSING

    def test_error_when_listremotes_fails(self):
        from core.setup_checks import check_rclone_remote
        with patch("subprocess.run", side_effect=OSError("no rclone")):
            result = check_rclone_remote("gdrive")
        assert result.status == CheckStatus.ERROR


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def _result(self, status):
        return CheckResult(name="x", status=status, message="m")

    def test_stops_before_remote_when_rclone_missing(self):
        from core.setup_checks import run_all_checks
        with patch("core.setup_checks.check_homebrew", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_rclone", return_value=self._result(CheckStatus.MISSING)), \
             patch("core.setup_checks.check_python_packages", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_rclone_remote") as remote, \
             patch("core.setup_checks.check_rclone_auth") as auth:
            results = run_all_checks("gdrive")
        assert len(results) == 3
        remote.assert_not_called()
        auth.assert_not_called()

    def test_runs_remote_and_auth_when_rclone_and_remote_ok(self):
        from core.setup_checks import run_all_checks
        with patch("core.setup_checks.check_homebrew", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_rclone", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_python_packages", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_rclone_remote", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_rclone_auth", return_value=self._result(CheckStatus.OK)) as auth:
            results = run_all_checks("gdrive")
        assert len(results) == 5
        auth.assert_called_once()

    def test_skips_auth_when_remote_missing(self):
        from core.setup_checks import run_all_checks
        with patch("core.setup_checks.check_homebrew", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_rclone", return_value=self._result(CheckStatus.WARNING)), \
             patch("core.setup_checks.check_python_packages", return_value=self._result(CheckStatus.OK)), \
             patch("core.setup_checks.check_rclone_remote", return_value=self._result(CheckStatus.MISSING)), \
             patch("core.setup_checks.check_rclone_auth") as auth:
            results = run_all_checks("gdrive")
        assert len(results) == 4  # remote ran (rclone WARNING counts as usable), auth did not
        auth.assert_not_called()


# ---------------------------------------------------------------------------
# create_gdrive_remote
# ---------------------------------------------------------------------------

class TestCreateGdriveRemote:
    def _patch_creds(self):
        return patch("core.setup_checks.get_oauth_credentials", return_value=("cid", "csec"))

    def test_ok_when_create_and_verify_succeed(self):
        from core.setup_checks import create_gdrive_remote
        create = MagicMock(returncode=0, stderr="")
        verify = MagicMock(returncode=0, stdout="folder\n")
        with self._patch_creds(), patch("subprocess.run", side_effect=[create, verify]):
            result = create_gdrive_remote("gdrive")
        assert result.status == CheckStatus.OK

    def test_error_when_verify_fails(self):
        from core.setup_checks import create_gdrive_remote
        create = MagicMock(returncode=0, stderr="")
        verify = MagicMock(returncode=1, stdout="")
        with self._patch_creds(), patch("subprocess.run", side_effect=[create, verify]):
            result = create_gdrive_remote("gdrive")
        assert result.status == CheckStatus.ERROR
        assert "sign-in was not completed" in result.message

    def test_error_when_verify_raises(self):
        from core.setup_checks import create_gdrive_remote
        create = MagicMock(returncode=0, stderr="")
        with self._patch_creds(), \
             patch("subprocess.run", side_effect=[create, OSError("gone")]):
            result = create_gdrive_remote("gdrive")
        assert result.status == CheckStatus.ERROR

    def test_error_when_create_returns_nonzero(self):
        from core.setup_checks import create_gdrive_remote
        create = MagicMock(returncode=1, stderr="bad config")
        with self._patch_creds(), patch("subprocess.run", return_value=create):
            result = create_gdrive_remote("gdrive")
        assert result.status == CheckStatus.ERROR
        assert "bad config" in result.message

    def test_error_on_create_timeout(self):
        import subprocess
        from core.setup_checks import create_gdrive_remote
        with self._patch_creds(), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="rclone", timeout=300)):
            result = create_gdrive_remote("gdrive")
        assert result.status == CheckStatus.ERROR
        assert "Timed out" in result.message

    def test_error_on_create_os_error(self):
        from core.setup_checks import create_gdrive_remote
        with self._patch_creds(), patch("subprocess.run", side_effect=OSError("no rclone")):
            result = create_gdrive_remote("gdrive")
        assert result.status == CheckStatus.ERROR
