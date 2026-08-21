"""Tests that AppContext's shared signals actually cross a real process boundary.

`api/app_context.py`'s docstring claims `cache_generation`/`last_import_time`/`engine_reload_guard`
are "expected to be created once in the master process ... and handed to every worker's AppContext
unchanged." Every other AppContext test builds one instance and calls methods on it directly, which
never exercises that claim -- a bug that broke cross-process visibility (e.g. passing a *copy* of a
`multiprocessing.Value` instead of the object itself) would still pass every one of those tests.

These tests spawn a real child process via `multiprocessing.get_context("fork")` -- fork, not the
platform default (`spawn` on macOS), so behavior matches production, which forks workers under
Linux/Docker either way. Each side builds its OWN `AppContext`, sharing only the primitive under
test, with `reader_pool`/`writer_pool`/`engine` mocked out on both sides since those are explicitly
*not* meant to survive a fork (see the AppContext docstring) and are irrelevant to what's being
proven here.
"""

from __future__ import annotations

import multiprocessing
from typing import TYPE_CHECKING

from api.tests.support import mock_app_context

if TYPE_CHECKING:
    from multiprocessing.sharedctypes import Synchronized
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType

_JOIN_TIMEOUT = 5


def _child_bumps_cache_generation(cache_generation: Synchronized) -> None:
    mock_app_context(cache_generation=cache_generation).bump_cache_generation()


def _child_writes_last_import_time(last_import_time: Synchronized, value: float) -> None:
    mock_app_context(last_import_time=last_import_time).last_import_time.value = value


def _child_holds_the_guard(guard: LockType, acquired: EventType, release: EventType) -> None:
    ctx = mock_app_context(engine_reload_guard=guard)
    ctx.engine_reload_guard.acquire()
    acquired.set()
    release.wait(timeout=_JOIN_TIMEOUT)
    ctx.engine_reload_guard.release()


class TestSharedSignalsCrossProcesses:
    """Two AppContexts in two OS processes, sharing only the primitive under test."""

    def test_cache_generation_bump_is_visible_across_processes(self) -> None:
        fork_ctx = multiprocessing.get_context("fork")
        cache_generation = multiprocessing.Value("i", 0)
        process = fork_ctx.Process(target=_child_bumps_cache_generation, args=(cache_generation,))
        process.start()
        process.join(timeout=_JOIN_TIMEOUT)

        assert process.exitcode == 0
        # A *different* AppContext instance, in this (parent) process, built from the same shared
        # Value -- not the one the child mutated -- must see the child's write.
        parent_ctx = mock_app_context(cache_generation=cache_generation)
        assert parent_ctx.cache_generation.value == 1

    def test_last_import_time_write_is_visible_across_processes(self) -> None:
        fork_ctx = multiprocessing.get_context("fork")
        last_import_time = multiprocessing.Value("d", 0.0, lock=True)
        process = fork_ctx.Process(target=_child_writes_last_import_time, args=(last_import_time, 12345.0))
        process.start()
        process.join(timeout=_JOIN_TIMEOUT)

        assert process.exitcode == 0
        parent_ctx = mock_app_context(last_import_time=last_import_time)
        assert parent_ctx.last_import_time.value == 12345.0

    def test_engine_reload_guard_serialises_across_processes(self) -> None:
        fork_ctx = multiprocessing.get_context("fork")
        guard = multiprocessing.Lock()
        acquired = fork_ctx.Event()
        release = fork_ctx.Event()
        process = fork_ctx.Process(target=_child_holds_the_guard, args=(guard, acquired, release))
        process.start()
        try:
            assert acquired.wait(timeout=_JOIN_TIMEOUT), "child never signalled that it holds the guard"

            parent_ctx = mock_app_context(engine_reload_guard=guard)
            # The child holds the lock from a different process: a non-blocking acquire here must fail.
            assert parent_ctx.engine_reload_guard.acquire(block=False) is False

            release.set()
            process.join(timeout=_JOIN_TIMEOUT)
            assert process.exitcode == 0

            # Released now -- the parent (which never itself held it) can take it.
            assert parent_ctx.engine_reload_guard.acquire(block=False) is True
            parent_ctx.engine_reload_guard.release()
        finally:
            if process.is_alive():
                process.terminate()
                process.join()
