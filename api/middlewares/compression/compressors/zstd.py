"""Zstandard compression implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import zstandard as zstd

from .base_compressor import BaseCompressor
from .util import StreamingBuffer, wrap_file

if TYPE_CHECKING:
    from collections.abc import Generator


class ZstdCompressor(BaseCompressor):
    """Compressor class for Zstandard encoding.

    Attributes:
        encoding (str): The encoding name, always 'zstd'.
        priority (int): Priority for compressor selection (lower is preferred).
        compression_level (int): Compression level for Zstandard (default: 4).
    """

    encoding: str = "zstd"
    priority: int = 10  # lower priority is preferred
    compression_level: int = 4

    def compress(self: ZstdCompressor, data: bytes) -> bytes:
        """Compress a bytes object using Zstandard.

        Args:
            data (bytes): The data to compress.

        Returns:
            bytes: The compressed data.
        """
        cctx = zstd.ZstdCompressor(level=self.compression_level)
        return cctx.compress(data)

    def compress_stream(self: ZstdCompressor, stream: object) -> Generator[bytes]:
        """Compress a stream of bytes using Zstandard, yielding compressed chunks.

        Args:
            stream: An iterable or file-like object yielding bytes.

        Yields:
            bytes: Chunks of compressed data.
        """
        yield b""
        if not hasattr(stream, "read"):
            stream = wrap_file(stream)
        buf = StreamingBuffer()
        cctx = zstd.ZstdCompressor(level=self.compression_level)
        with cctx.stream_writer(buf) as compressor:
            yield buf.read()
            for item in stream:
                compressor.write(item)
                data = buf.read()
                if data:
                    yield data
        yield buf.read()
