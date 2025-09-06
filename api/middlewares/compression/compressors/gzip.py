from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

from .base_compressor import BaseCompressor
from .util import StreamingBuffer, wrap_file

if TYPE_CHECKING:
    from collections.abc import Generator


class GzipCompressor(BaseCompressor):
    """Compressor class for gzip encoding.

    Attributes:
        encoding (str): The encoding name, always 'gzip'.
        priority (int): Priority for compressor selection (lower is preferred).
        compression_level (int): Compression level for gzip (default: 6).
    """

    encoding: str = "gzip"
    priority: int = 30  # lower priority is preferred
    compression_level: int = 6

    def compress(self: GzipCompressor, data: bytes) -> bytes:
        """Compress a bytes object using gzip.

        Args:
            data (bytes): The data to compress.

        Returns:
            bytes: The compressed data.
        """
        return gzip.compress(data, compresslevel=self.compression_level, mtime=0)

    def compress_stream(self: GzipCompressor, stream: object) -> Generator[bytes, None, None]:
        """Compress a stream of bytes using gzip, yielding compressed chunks.

        Args:
            stream: An iterable or file-like object yielding bytes.

        Yields:
            bytes: Chunks of compressed data.
        """
        yield b""
        if not hasattr(stream, "read"):
            stream = wrap_file(stream)
        buf = StreamingBuffer()
        with gzip.GzipFile(
            mode="wb",
            compresslevel=self.compression_level,
            fileobj=buf,
            mtime=0,
        ) as zfile:
            yield buf.read()
            for item in stream:
                zfile.write(item)
                data = buf.read()
                if data:
                    yield data
        yield buf.read()
