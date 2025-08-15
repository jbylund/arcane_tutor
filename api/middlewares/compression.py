import logging
import time

from .brotli import BrotliCompressor
from .gzip import GzipCompressor
from .zstd import ZstdCompressor

MIN_SIZE = 200

logger = logging.getLogger(__name__)

def parse_q_list(accept_encoding: str, server_priorities: dict[str, int]) -> list[str]:
    values = []
    for name in accept_encoding.split(","):
        encoding = name.strip().lower()
        server_prioritiy = server_priorities.get(encoding)
        if server_prioritiy is None:
            continue
        q = 1.0
        if ";q=" in name:
            name, q = name.split(";q=")[:2]
            try:
                q = float(q)
            except ValueError:
                q = 0.0
            if q == 0.0:
                continue
        client_priority = q
        values.append((encoding, server_prioritiy, client_priority))
    values.sort(key=lambda v: v[1])
    values.sort(key=lambda v: v[2], reverse=True)
    return [v[0] for v in values]


class CompressionMiddleware:
    def __init__(self) -> None:
        self._compressors = {}
        self._priorities = {}
        self._add_compressor(BrotliCompressor())
        self._add_compressor(GzipCompressor())
        self._add_compressor(ZstdCompressor())

    def _add_compressor(self, compressor) -> None:
        self._priorities[compressor.encoding] = compressor.priority
        self._compressors[compressor.encoding] = compressor

    def _get_compressor(self, accept_encoding):
        for encoding in parse_q_list(accept_encoding, self._priorities):
            compressor = self._compressors.get(encoding)
            if compressor:
                return compressor
        return None

    def process_response(self, req, resp, resource, req_succeeded) -> None:
        """Post-processing of the response (after routing).

        Args:
            req: Request object.
            resp: Response object.
            resource: Resource object to which the request was
                routed. May be None if no route was found
                for the request.
            req_succeeded: True if no exceptions were raised while
                the framework processed and routed the request;
                otherwise False.
        """
        accept_encoding = req.get_header("Accept-Encoding")
        if accept_encoding is None:
            return

        # If content-encoding is already set don't compress.
        if resp.get_header("Content-Encoding"):
            return

        # my accept encoding is "gzip, deflate, br, zstd"
        compressor = self._get_compressor(accept_encoding)
        if compressor is None:
            return
        logger.info("Using compressor: %s", compressor.encoding)

        if resp.stream:
            logger.info("Compressing stream")
            resp.stream = compressor.compress_stream(resp.stream)
            resp.content_length = None
        else:
            data = resp.render_body()
            # If there is no content or it is very short then don't compress.
            if data is None or len(data) < MIN_SIZE:
                logger.info("Skipping compression for short response")
                return
            size_before_compression = len(data)
            before_compression = time.monotonic()
            resp.data = compressed = compressor.compress(data)
            after_compression = time.monotonic()
            resp.text = None
            size_after_compression = len(compressed)
            logger.info(
                "Compressed %s bytes to %s bytes (%.2f x compression) in %.2f ms",
                f"{size_before_compression:,}",
                f"{size_after_compression:,}",
                size_before_compression / size_after_compression,
                1000 * (after_compression - before_compression),
            )

        resp.set_header("Content-Encoding", compressor.encoding)
        resp.append_header("Vary", "Accept-Encoding")
