from io import BytesIO


class StreamingBuffer(BytesIO):
    def read(self):
        ret = self.getvalue()
        self.seek(0)
        self.truncate()
        return ret


def wrap_file(file_stream, block_size=8192):
    while True:
        data = file_stream.read(block_size)
        if not data:
            break
        yield data
