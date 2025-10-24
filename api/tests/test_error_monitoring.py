"""Tests for error monitoring configuration."""

from __future__ import annotations

import os
from unittest import mock


def test_honeybadger_env_configuration_with_env_var() -> None:
    """Test that honeybadger uses ENV variable when set."""
    # Mock the honeybadger module to test configuration
    with (
        mock.patch.dict(os.environ, {"ENV": "prod"}),
        mock.patch("api.utils.error_monitoring.honeybadger") as mock_honeybadger,
    ):
        # Re-import the module to trigger configuration with mocked environment
        import importlib  # noqa: PLC0415

        import api.utils.error_monitoring  # noqa: PLC0415

        importlib.reload(api.utils.error_monitoring)

        # Verify honeybadger.configure was called with environment='prod'
        if mock_honeybadger.configure.called:
            call_kwargs = mock_honeybadger.configure.call_args[1]
            assert "environment" in call_kwargs
            assert call_kwargs["environment"] == "prod"


def test_honeybadger_env_configuration_with_default() -> None:
    """Test that honeybadger uses default environment when ENV not set."""
    # Mock the honeybadger module to test configuration
    with (
        mock.patch.dict(os.environ, {}, clear=True),
        mock.patch("api.utils.error_monitoring.honeybadger") as mock_honeybadger,
    ):
        # Re-import the module to trigger configuration with mocked environment
        import importlib  # noqa: PLC0415

        import api.utils.error_monitoring  # noqa: PLC0415

        importlib.reload(api.utils.error_monitoring)

        # Verify honeybadger.configure was called with environment='development'
        if mock_honeybadger.configure.called:
            call_kwargs = mock_honeybadger.configure.call_args[1]
            assert "environment" in call_kwargs
            assert call_kwargs["environment"] == "development"


def test_honeybadger_env_configuration_with_dev() -> None:
    """Test that honeybadger uses ENV=dev when set."""
    # Mock the honeybadger module to test configuration
    with (
        mock.patch.dict(os.environ, {"ENV": "dev"}),
        mock.patch("api.utils.error_monitoring.honeybadger") as mock_honeybadger,
    ):
        # Re-import the module to trigger configuration with mocked environment
        import importlib  # noqa: PLC0415

        import api.utils.error_monitoring  # noqa: PLC0415

        importlib.reload(api.utils.error_monitoring)

        # Verify honeybadger.configure was called with environment='dev'
        if mock_honeybadger.configure.called:
            call_kwargs = mock_honeybadger.configure.call_args[1]
            assert "environment" in call_kwargs
            assert call_kwargs["environment"] == "dev"
