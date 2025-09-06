from __future__ import annotations

from typing import TYPE_CHECKING

import brotli

from .base_compressor import BaseCompressor
from .util import wrap_file

if TYPE_CHECKING:
    from collections.abc import Generator


class BrotliCompressor(BaseCompressor):
    """Compressor class for Brotli encoding.

    Attributes:
        encoding (str): The encoding name, always 'br'.
        priority (int): Priority for compressor selection (lower is preferred).
        compression_level (int): Compression level for Brotli (default: 4).
    """

    encoding: str = "br"
    priority: int = 20  # lower priority is preferred
    compression_level: int = 4

    def compress(self: BrotliCompressor, data: bytes) -> bytes:
        """Compress a bytes object using Brotli.

        Args:
            data (bytes): The data to compress.

        Returns:
            bytes: The compressed data.
        """
        return brotli.compress(data, quality=self.compression_level)

    def compress_stream(self: BrotliCompressor, stream: object) -> Generator[bytes, None, None]:
        """Compress a stream of bytes using Brotli, yielding compressed chunks.

        Args:
            stream: An iterable or file-like object yielding bytes.

        Yields:
            bytes: Chunks of compressed data.
        """
        yield b""
        if hasattr(stream, "read"):
            stream = wrap_file(stream)
        compressor = brotli.Compressor(quality=self.compression_level)
        for block in stream:
            output = compressor.process(block)
            if output:
                yield output
        output = compressor.finish()
        if output:
            yield output
