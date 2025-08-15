import gzip

from .util import StreamingBuffer, wrap_file


class GzipCompressor:
    encoding = "gzip"
    priority = 30 # lower priority is preferred
    compression_level = 6

    def compress(self, data):
        return gzip.compress(data, compresslevel=self.compression_level, mtime=0)

    def compress_stream(self, stream):
        yield b""
        if not hasattr(stream, "read"):
            stream = wrap_file(stream)
        buf = StreamingBuffer()
        with gzip.GzipFile(
            mode="wb", compresslevel=self.compression_level, fileobj=buf, mtime=0,
        ) as zfile:
            yield buf.read()
            for item in stream:
                zfile.write(item)
                data = buf.read()
                if data:
                    yield data
        yield buf.read()
