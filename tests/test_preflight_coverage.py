"""Tests for core/preflight.py — check_rclone, ensure_remote, run_preflight.

These functions are the first code that executes on startup, so failures here
are high-impact. All subprocess and oauth calls are fully mocked so the tests
run without rclone installed.
"""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

import core.preflight as preflight


# ---------------------------------------------------------------------------
# check_rclone
# ---------------------------------------------------------------------------

class TestCheckRclone:
    """check_rclone exits on missing/old rclone and passes silently otherwise."""

    def test_passes_silently_for_current_version(self):
        """A version at or above MIN_RCLONE produces no error."""
        mock_result = MagicMock()
        mock_result.stdout = "rclone v1.65.2\n  - os/arch: darwin/amd64"
        with patch("subprocess.run", return_value=mock_result):
            # Should not raise or exit
            preflight.check_rclone()

    def test_passes_for_exact_minimum_version(self):
        """A version equal to MIN_RCLONE (1.60.0) is accepted."""
        mock_result = MagicMock()
        mock_result.stdout = "rclone v1.60.0\n"
        with patch("subprocess.run", return_value=mock_result):
            preflight.check_rclone()

    def test_exits_when_rclone_not_found(self):
        """OSError (rclone not on PATH) calls sys.exit with install hint."""
        with patch("subprocess.run", side_effect=OSError("not found")):
            with pytest.raises(SystemExit) as exc_info:
                preflight.check_rclone()
        assert "brew install rclone" in str(exc_info.value)

    def test_exits_on_timeout(self):
        """TimeoutExpired is treated the same as rclone not being present."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["rclone", "version"], 5),
        ):
            with pytest.raises(SystemExit) as exc_info:
                preflight.check_rclone()
        assert "brew install rclone" in str(exc_info.value)

    def test_exits_for_old_version(self):
        """A version older than 1.60.0 triggers the upgrade message."""
        mock_result = MagicMock()
        mock_result.stdout = "rclone v1.55.1\n"
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                preflight.check_rclone()
        assert "brew upgrade rclone" in str(exc_info.value)

    def test_exits_for_version_just_below_minimum(self):
        """v1.59.999 is below the 1.60.0 floor and must be rejected."""
        mock_result = MagicMock()
        mock_result.stdout = "rclone v1.59.2\n"
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit):
                preflight.check_rclone()

    def test_no_version_line_in_output_does_not_exit(self):
        """Unrecognised output format skips the version check rather than crashing."""
        mock_result = MagicMock()
        mock_result.stdout = "unexpected output\n"
        with patch("subprocess.run", return_value=mock_result):
            # The regex match is None so the version check is skipped
            preflight.check_rclone()


# ---------------------------------------------------------------------------
# ensure_remote
# ---------------------------------------------------------------------------

class TestEnsureRemote:
    """ensure_remote returns early when the remote already exists, and
    calls rclone config create (with injected credentials) when it does not."""

    def _listremotes_result(self, remotes: str) -> MagicMock:
        m = MagicMock()
        m.stdout = remotes
        return m

    def test_returns_early_when_remote_exists(self):
        """No config create call is made when 'gdrive:' is already listed."""
        with patch("subprocess.run", return_value=self._listremotes_result("gdrive:\n")):
            with patch("core.oauth_config.get_oauth_credentials") as mock_creds:
                preflight.ensure_remote("gdrive")
        mock_creds.assert_not_called()

    def test_creates_remote_when_missing(self):
        """rclone config create is called with credentials when remote is absent."""
        list_result = self._listremotes_result("other:\n")
        config_result = MagicMock()

        with patch(
            "subprocess.run", side_effect=[list_result, config_result]
        ) as mock_run:
            with patch(
                "core.oauth_config.get_oauth_credentials",
                return_value=("my_id", "my_secret"),
            ):
                preflight.ensure_remote("gdrive")

        # Second subprocess.run call must be the config create
        config_call = mock_run.call_args_list[1]
        cmd = config_call[0][0]
        assert "config" in cmd
        assert "create" in cmd
        assert "gdrive" in cmd
        assert "my_id" in cmd
        assert "my_secret" in cmd

    def test_config_create_uses_drive_scope(self):
        """The scope passed to rclone config create must be 'drive'."""
        list_result = self._listremotes_result("")
        config_result = MagicMock()

        with patch(
            "subprocess.run", side_effect=[list_result, config_result]
        ) as mock_run:
            with patch(
                "core.oauth_config.get_oauth_credentials",
                return_value=("cid", "csec"),
            ):
                preflight.ensure_remote("gdrive")

        cmd = mock_run.call_args_list[1][0][0]
        scope_idx = cmd.index("scope")
        assert cmd[scope_idx + 1] == "drive"

    def test_custom_remote_name_is_forwarded(self):
        """A caller-specified remote name is used in both list check and create."""
        list_result = self._listremotes_result("gdrive:\n")  # 'myremote:' not present
        # Override: use a remote name not in the listing
        list_result.stdout = "gdrive:\n"
        config_result = MagicMock()

        with patch(
            "subprocess.run", side_effect=[self._listremotes_result(""), config_result]
        ) as mock_run:
            with patch(
                "core.oauth_config.get_oauth_credentials",
                return_value=("cid", "csec"),
            ):
                preflight.ensure_remote("myremote")

        cmd = mock_run.call_args_list[1][0][0]
        assert "myremote" in cmd

    def test_config_create_propagates_subprocess_error(self):
        """A CalledProcessError from rclone config create bubbles up unchanged."""
        list_result = self._listremotes_result("")

        with patch(
            "subprocess.run",
            side_effect=[
                list_result,
                subprocess.CalledProcessError(1, "rclone"),
            ],
        ):
            with patch(
                "core.oauth_config.get_oauth_credentials",
                return_value=("cid", "csec"),
            ):
                with pytest.raises(subprocess.CalledProcessError):
                    preflight.ensure_remote("gdrive")


# ---------------------------------------------------------------------------
# run_preflight
# ---------------------------------------------------------------------------

class TestRunPreflight:
    """run_preflight orchestrates check_rclone then ensure_remote."""

    def test_calls_check_rclone_then_ensure_remote(self):
        """Both sub-functions are called in order with the default remote."""
        with patch.object(preflight, "check_rclone") as mock_check:
            with patch.object(preflight, "ensure_remote") as mock_ensure:
                preflight.run_preflight()

        mock_check.assert_called_once_with()
        mock_ensure.assert_called_once_with("gdrive")

    def test_custom_remote_forwarded_to_ensure_remote(self):
        """A non-default remote name is passed through to ensure_remote."""
        with patch.object(preflight, "check_rclone"):
            with patch.object(preflight, "ensure_remote") as mock_ensure:
                preflight.run_preflight(remote="s3bucket")

        mock_ensure.assert_called_once_with("s3bucket")

    def test_exits_early_if_check_rclone_fails(self):
        """If check_rclone calls sys.exit, ensure_remote is never reached."""
        with patch.object(preflight, "check_rclone", side_effect=SystemExit("old")):
            with patch.object(preflight, "ensure_remote") as mock_ensure:
                with pytest.raises(SystemExit):
                    preflight.run_preflight()

        mock_ensure.assert_not_called()

    def test_propagates_ensure_remote_error(self):
        """An exception from ensure_remote surfaces to the caller."""
        with patch.object(preflight, "check_rclone"):
            with patch.object(
                preflight, "ensure_remote", side_effect=RuntimeError("oauth broke")
            ):
                with pytest.raises(RuntimeError, match="oauth broke"):
                    preflight.run_preflight()
