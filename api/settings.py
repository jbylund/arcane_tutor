"""Settings module for runtime configuration."""

from __future__ import annotations

import os

# Statement timeout for the prefer-score backfill, in milliseconds. The backfill rescores the
# whole corpus in a single UPDATE, so its runtime scales with disk speed: on a virtualized
# Docker-for-Mac volume it can exceed a limit that real Linux hardware clears comfortably, and
# the import is then abandoned after the upsert has already succeeded (#876). Raise this in
# environments with slow storage.
DEFAULT_PREFER_SCORE_BACKFILL_TIMEOUT_MS = 120_000


def _is_truthy(value: str | None) -> bool:
    """Check if a string value is truthy.

    Args:
        value: String value to check

    Returns:
        True if value is "true", "1", or "yes" (case-insensitive), False otherwise
    """
    if value is None:
        return False
    return value.lower() in ("true", "1", "yes")


def _non_negative_int(name: str, default: int) -> int:
    """Read a non-negative integer from the environment, falling back to a default.

    Raises rather than silently substituting the default on a malformed value: these tune
    timeouts, and quietly ignoring a typo means the operator only finds out when the import
    dies at the limit they thought they had raised.

    Args:
        name: Environment variable to read.
        default: Value to use when the variable is unset or empty.

    Returns:
        The parsed value, or default if the variable is unset or empty.

    Raises:
        ValueError: If the variable is set to something that is not a non-negative integer.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        msg = f"{name} must be a non-negative integer, got: {raw!r}"
        raise ValueError(msg) from None
    if value < 0:
        msg = f"{name} must be a non-negative integer, got: {raw!r}"
        raise ValueError(msg)
    return value


class Settings:
    """Simple settings class for runtime configuration."""

    def __init__(self) -> None:
        """Initialize settings from environment variables."""
        self._enable_cache = _is_truthy(os.environ.get("ENABLE_CACHE", "false"))
        self._enable_engine = _is_truthy(os.environ.get("ENABLE_ENGINE", "true"))
        self._shared_cache_path: str = os.environ.get("SHARED_CACHE_PATH", "/tmp/sylvan.cache")  # noqa: S108
        self._prefer_score_backfill_timeout_ms = _non_negative_int(
            "PREFER_SCORE_BACKFILL_TIMEOUT_MS",
            DEFAULT_PREFER_SCORE_BACKFILL_TIMEOUT_MS,
        )

    @property
    def enable_cache(self) -> bool:
        """Check if caching is enabled."""
        return self._enable_cache

    @enable_cache.setter
    def enable_cache(self, value: bool) -> None:
        """Set caching enabled state."""
        self._enable_cache = value

    @property
    def enable_engine(self) -> bool:
        """Check if the Rust card filter engine serves searches.

        Enabled by default. When disabled, the engine is fully inert: _search
        routes every query to SQL and AppContext.reload_engine never runs. Disable via
        ENABLE_ENGINE=false for environments where the full-table fetch cost is
        unacceptable (e.g. low-memory dev machines).
        """
        return self._enable_engine

    @enable_engine.setter
    def enable_engine(self, value: bool) -> None:
        """Set engine enabled state."""
        self._enable_engine = value

    @property
    def shared_cache_path(self) -> str:
        """Filesystem path for the shared mmap cache file."""
        return self._shared_cache_path

    @property
    def prefer_score_backfill_timeout_ms(self) -> int:
        """Statement timeout for the prefer-score backfill, in milliseconds.

        Override with PREFER_SCORE_BACKFILL_TIMEOUT_MS. 0 disables the timeout entirely.
        """
        return self._prefer_score_backfill_timeout_ms


# Global settings instance
settings = Settings()
