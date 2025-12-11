#!/usr/bin/env python3
"""Tests for the consolidated CI monitor script."""

import os

# Import the script functions
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest

from scripts.ci_monitor import (
    format_issue_body,
    get_args,
    get_github_headers,
    get_repository_info,
    main,
    set_github_output,
)


class TestCIMonitor:
    """Test cases for CI monitor functionality."""

    def test_get_github_headers_success(self) -> None:
        """Test GitHub headers are created correctly with token."""
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            headers = get_github_headers()
            assert headers["Authorization"] == "Bearer test-token"
            assert headers["Accept"] == "application/vnd.github.v3+json"
            assert headers["User-Agent"] == "CI-Monitor-Script/1.0"

    def test_get_github_headers_missing_token(self) -> None:
        """Test error when GITHUB_TOKEN is missing."""
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            pytest.raises(
                ValueError,
                match="GITHUB_TOKEN environment variable is required",
            ),
        ):
            get_github_headers()

    def test_get_repository_info_success(self) -> None:
        """Test repository info extraction from environment."""
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"}):
            owner, repo = get_repository_info()
            assert owner == "owner"
            assert repo == "repo"

    def test_get_repository_info_missing(self) -> None:
        """Test error when GITHUB_REPOSITORY is missing."""
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            pytest.raises(
                ValueError,
                match="GITHUB_REPOSITORY environment variable is required",
            ),
        ):
            get_repository_info()

    def test_format_issue_body(self) -> None:
        """Test issue body formatting."""
        failed_checks = [
            {"name": "Lint", "type": "workflow", "url": "https://github.com/test/repo/actions/runs/123"},
            {"name": "Tests", "type": "workflow", "url": "https://github.com/test/repo/actions/runs/456"},
        ]
        commit_sha = "abc123def456"

        body = format_issue_body(commit_sha, failed_checks)

        assert "CI Failure Detected" in body
        assert commit_sha in body
        assert "Lint" in body
        assert "Tests" in body
        assert "https://github.com/test/repo/actions/runs/123" in body
        assert "https://github.com/test/repo/actions/runs/456" in body
        assert "python -m ruff check --fix --unsafe-fixes" in body
        assert "python -m pytest -vvv" in body

    def test_set_github_output_with_file(self) -> None:
        """Test setting GitHub Actions output when GITHUB_OUTPUT is set."""
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            output_file = f.name

        try:
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": output_file}):
                set_github_output("test_key", "test_value")

            with Path(output_file).open() as f:
                content = f.read()
                assert "test_key=test_value\n" in content
        finally:
            Path(output_file).unlink()

    def test_set_github_output_without_file(self) -> None:
        """Test setting GitHub Actions output when GITHUB_OUTPUT is not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            # Should not raise an error
            set_github_output("test_key", "test_value")

    def test_args_parsing_check_only(self) -> None:
        """Test argument parsing for --check-only."""
        with mock.patch("sys.argv", ["ci_monitor.py", "--check-only"]):
            args = get_args()
            assert args.check_only is True
            assert args.check_and_create_issue is False
            assert args.create_issue is False

    def test_args_parsing_check_and_create_issue(self) -> None:
        """Test argument parsing for --check-and-create-issue."""
        with mock.patch("sys.argv", ["ci_monitor.py", "--check-and-create-issue"]):
            args = get_args()
            assert args.check_only is False
            assert args.check_and_create_issue is True
            assert args.create_issue is False

    def test_args_parsing_create_issue(self) -> None:
        """Test argument parsing for --create-issue."""
        test_argv = [
            "ci_monitor.py",
            "--create-issue",
            "--failed-checks",
            '[{"name": "test"}]',
            "--commit-sha",
            "abc123",
        ]
        with mock.patch("sys.argv", test_argv):
            args = get_args()
            assert args.check_only is False
            assert args.check_and_create_issue is False
            assert args.create_issue is True
            assert args.failed_checks == '[{"name": "test"}]'
            assert args.commit_sha == "abc123"

    @mock.patch("scripts.ci_monitor.get_repository_info")
    @mock.patch("scripts.ci_monitor.check_ci_status")
    @mock.patch("scripts.ci_monitor.set_github_output")
    def test_main_check_only(
        self,
        mock_set_output: MagicMock,
        mock_check_ci: MagicMock,
        mock_get_repo: MagicMock,
    ) -> None:
        """Test main function with --check-only option."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_check_ci.return_value = (True, [{"name": "test"}], "abc123")

        test_argv = ["ci_monitor.py", "--check-only"]
        with mock.patch("sys.argv", test_argv), mock.patch("builtins.print"):  # Suppress output during test
            main()

        mock_check_ci.assert_called_once_with("owner", "repo")
        mock_set_output.assert_any_call("has_failed", "true")
        mock_set_output.assert_any_call("failed_checks", '[{"name": "test"}]')
        mock_set_output.assert_any_call("commit_sha", "abc123")

    @mock.patch("scripts.ci_monitor.get_repository_info")
    @mock.patch("scripts.ci_monitor.create_ci_issue_if_needed")
    @mock.patch("scripts.ci_monitor.check_ci_status")
    @mock.patch("scripts.ci_monitor.set_github_output")
    def test_main_check_and_create_issue_with_failure(
        self,
        mock_set_output: MagicMock,  # noqa: ARG002
        mock_check_ci: MagicMock,
        mock_create_issue: MagicMock,
        mock_get_repo: MagicMock,
    ) -> None:
        """Test main function with --check-and-create-issue when CI fails."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_check_ci.return_value = (True, [{"name": "test"}], "abc123")
        mock_create_issue.return_value = True

        test_argv = ["ci_monitor.py", "--check-and-create-issue"]
        with mock.patch("sys.argv", test_argv), mock.patch("builtins.print"):  # Suppress output during test
            main()

        mock_check_ci.assert_called_once_with("owner", "repo")
        mock_create_issue.assert_called_once_with("owner", "repo", [{"name": "test"}], "abc123")

    @mock.patch("scripts.ci_monitor.get_repository_info")
    @mock.patch("scripts.ci_monitor.create_ci_issue_if_needed")
    def test_main_create_issue_direct(
        self,
        mock_create_issue: MagicMock,
        mock_get_repo: MagicMock,
    ) -> None:
        """Test main function with --create-issue option."""
        mock_get_repo.return_value = ("owner", "repo")
        mock_create_issue.return_value = True

        test_argv = [
            "ci_monitor.py",
            "--create-issue",
            "--failed-checks",
            '[{"name": "test"}]',
            "--commit-sha",
            "abc123",
        ]
        with mock.patch("sys.argv", test_argv), mock.patch("builtins.print"):  # Suppress output during test
            main()

        mock_create_issue.assert_called_once_with("owner", "repo", [{"name": "test"}], "abc123")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
