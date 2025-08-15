import zstandard as zstd

from .util import StreamingBuffer, wrap_file


class ZstdCompressor:
    encoding = "zstd"
    priority = 10  # lower priority is preferred
    compression_level = 4

    def compress(self, data):
        cctx = zstd.ZstdCompressor(level=self.compression_level)
        return cctx.compress(data)

    def compress_stream(self, stream):
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
