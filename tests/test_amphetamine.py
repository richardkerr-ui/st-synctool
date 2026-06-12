"""Tests for core/amphetamine.py — is_macos, is_installed, start_session, end_session.

These functions had zero test coverage despite 8 callers. All osascript
and sys.platform calls are mocked so the tests run on any OS.
"""

from unittest.mock import MagicMock, patch

import core.amphetamine as amp
from core.amphetamine import is_macos, is_installed, start_session, end_session


class TestIsMacos:
    def test_returns_true_on_darwin(self):
        with patch("sys.platform", "darwin"):
            assert is_macos() is True

    def test_returns_false_on_linux(self):
        with patch("sys.platform", "linux"):
            assert is_macos() is False

    def test_returns_false_on_win32(self):
        with patch("sys.platform", "win32"):
            assert is_macos() is False


class TestIsInstalled:
    def test_returns_false_on_non_macos(self):
        with patch("core.amphetamine.is_macos", return_value=False):
            assert is_installed() is False

    def test_returns_true_when_osascript_says_true(self):
        mock_result = MagicMock(stdout="true\n")
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert is_installed() is True

    def test_returns_false_when_osascript_says_false(self):
        mock_result = MagicMock(stdout="false\n")
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert is_installed() is False

    def test_returns_false_on_empty_stdout(self):
        mock_result = MagicMock(stdout="")
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert is_installed() is False


class TestStartSession:
    def test_returns_false_on_non_macos(self):
        with patch("core.amphetamine.is_macos", return_value=False):
            assert start_session() is False

    def test_returns_false_when_not_installed(self):
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("core.amphetamine.is_installed", return_value=False):
            assert start_session() is False

    def test_returns_true_on_success(self):
        mock_result = MagicMock(returncode=0, stderr="")
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("core.amphetamine.is_installed", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert start_session() is True

    def test_returns_false_on_osascript_error(self):
        mock_result = MagicMock(returncode=1, stderr="execution error")
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("core.amphetamine.is_installed", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert start_session() is False


class TestEndSession:
    def test_returns_false_on_non_macos(self):
        with patch("core.amphetamine.is_macos", return_value=False):
            assert end_session() is False

    def test_returns_false_when_not_installed(self):
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("core.amphetamine.is_installed", return_value=False):
            assert end_session() is False

    def test_returns_true_on_success(self):
        mock_result = MagicMock(returncode=0, stderr="")
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("core.amphetamine.is_installed", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert end_session() is True

    def test_returns_false_on_osascript_error(self):
        mock_result = MagicMock(returncode=1, stderr="Amphetamine is not running")
        with patch("core.amphetamine.is_macos", return_value=True), \
             patch("core.amphetamine.is_installed", return_value=True), \
             patch("subprocess.run", return_value=mock_result):
            assert end_session() is False
