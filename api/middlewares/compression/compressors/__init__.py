"""Compression algorithms package."""

from api.middlewares.compression.compressors.brotli import BrotliCompressor
from api.middlewares.compression.compressors.gzip import GzipCompressor
from api.middlewares.compression.compressors.zstd import ZstdCompressor

__all__ = [
    "BrotliCompressor",
    "GzipCompressor",
    "ZstdCompressor",
]
