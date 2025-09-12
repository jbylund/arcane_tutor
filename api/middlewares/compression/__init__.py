"""Compression middleware package for Falcon API responses."""

from .compression_mod import CompressionMiddleware

__all__ = [
    "CompressionMiddleware",
]
