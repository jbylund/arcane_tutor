from __future__ import annotations

"""
Middlewares package for response compression and related utilities.

Exports:
    CompressionMiddleware: Main middleware class for handling response compression.
"""
from .compression import CompressionMiddleware

__all__ = ["CompressionMiddleware"]
