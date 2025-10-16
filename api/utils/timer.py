"""Timer for timing nested code blocks."""
from __future__ import annotations

import copy
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType


class Timer:
    """Hierarchical context manager for timing nested code blocks.

    Usage:
        timer = Timer()
        with timer("outer"):
            with timer("inner"):
                pass
        timer.get_timings()
        # Returns:
        # {
        #     "outer": {
        #         "_meta": {"count": 1, "duration": 0.15, "duration_ms": 150, "frequency": 6.67},
        #         "_children": {
        #             "inner": {"_meta": {...}}
        #         }
        #     }
        # }

    Each node has _meta (count, duration, duration_ms, frequency) and optionally _children.
    """

    def __init__(self) -> None:
        """Initialize with empty timing state."""
        self.timings_dict = {}
        self.enter_times: list[float] = []
        self.ptrs = [self.timings_dict]

    def __call__(self, checkpoint_name: str) -> Timer:
        """Add checkpoint to path and return self as context manager."""
        cur_ptr = self.ptrs[-1]
        new_ptr = cur_ptr.setdefault("_children", {}).setdefault(checkpoint_name, {})
        self.ptrs.append(new_ptr)
        return self

    def __enter__(self) -> Timer:
        """Record start time."""
        self.enter_times.append(monotonic())
        return self

    def __exit__(self, exc_type: type | None, exc_value: object | None, traceback: TracebackType | None) -> None:
        """Calculate duration in milliseconds and restore state."""
        now = monotonic()
        duration = (now - self.enter_times.pop())
        cur_ptr = self.ptrs[-1]
        ptr_metadata = cur_ptr.setdefault("_meta", {})
        key_duration = duration + ptr_metadata.get("duration", 0)
        ptr_metadata["count"] = key_count = ptr_metadata.get("count", 0) + 1
        ptr_metadata["duration"] = key_duration
        ptr_metadata["duration_ms"] = key_duration * 1000
        ptr_metadata["frequency"] = key_count / key_duration
        self.ptrs.pop()

    def get_timings(self) -> dict:
        """Return nested timing tree with _meta and _children for each node."""
        res = copy.deepcopy(self.timings_dict)

        def _recurse_round(node: dict) -> None:
            meta = node.get("_meta", {})
            for k, v in meta.items():
                meta[k] = round(v, 3)
            children = node.get("_children", {})
            for v in children.values():
                _recurse_round(v)

        _recurse_round(res)
        return res.get("_children", {})

    def reset(self) -> None:
        """Clear all recorded timings."""
        self.timings_dict.clear()
