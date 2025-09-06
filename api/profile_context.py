from __future__ import annotations

import cProfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType


class ProfileContext:
    """Context manager for profiling using cProfile.

    Args:
        filename (str): The file to which the profile stats will be written.
    """

    def __init__(self: ProfileContext, *, filename: str) -> None:
        """Initialize a ProfileContext object."""
        self.filename = filename

    def __enter__(self: ProfileContext) -> None:
        """Enter the context manager and start profiling."""
        self.profiler = cProfile.Profile()
        self.profiler.enable()

    def __exit__(
        self: ProfileContext,
        err_type: type[BaseException] | None,
        err_val: BaseException | None,
        err_tb: TracebackType | None,
    ) -> None:
        """Exit the context manager and write profile stats to file."""
        self.profiler.disable()
        self.profiler.dump_stats(self.filename)
