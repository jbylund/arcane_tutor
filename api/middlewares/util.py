from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


class StreamingBuffer(BytesIO):
    """A BytesIO buffer that clears itself after each read."""
    def read(self: StreamingBuffer) -> bytes:
        """Read and clear the buffer, returning its contents as bytes.

        Returns:
            bytes: The contents of the buffer.
        """
        ret = self.getvalue()
        self.seek(0)
        self.truncate()
        return ret


def wrap_file(file_stream: object, block_size: int = 8192) -> Generator[bytes, None, None]:
    """Wrap a file-like object to yield blocks of bytes.

    Args:
        file_stream: A file-like object with a read() method.
        block_size (int, optional): The size of each block to read. Defaults to 8192.

    Yields:
        bytes: Blocks of data read from the file.
    """
    while True:
        data = file_stream.read(block_size)
        if not data:
            break
        yield data
