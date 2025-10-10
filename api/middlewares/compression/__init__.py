"""Compression middleware package for Falcon API responses."""

from api.middlewares.compression.compression_mod import CompressionMiddleware

__all__ = [
    "CompressionMiddleware",
]
