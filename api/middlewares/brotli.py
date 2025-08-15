import brotli

from .util import wrap_file


class BrotliCompressor:
    encoding = "br"
    priority = 20 # lower priority is preferred
    compression_level = 4

    def compress(self, data):
        return brotli.compress(data, quality=self.compression_level)

    def compress_stream(self, stream):
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
