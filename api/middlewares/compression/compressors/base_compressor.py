from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


class BaseCompressor:
    """Base class for compressors.

    Attributes:
        encoding (str): The encoding name.
        priority (int): Priority for compressor selection (lower is preferred).
        compression_level (int): Compression level for the compressor.
    """

    encoding: str = "base"
    priority: int = 20  # lower priority is preferred
    compression_level: int = 4


    def compress(self: BaseCompressor, data: bytes) -> bytes:
        """Compress a bytes object using the compressor.

        Args:
            data (bytes): The data to compress.

        Returns:
            bytes: The compressed data.
        """
        msg = "Subclasses must implement this method"
        raise NotImplementedError(msg)

    def compress_stream(self: BaseCompressor, stream: object) -> Generator[bytes, None, None]:
        """Compress a stream of bytes using the compressor, yielding compressed chunks.

        Args:
            stream: An iterable or file-like object yielding bytes.

        Yields:
            bytes: Chunks of compressed data.
        """
        msg = "Subclasses must implement this method"
        raise NotImplementedError(msg)
