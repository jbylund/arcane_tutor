"""Cache resource for handling Scryfall API requests."""

from __future__ import annotations

import base64
import json
import logging
import math
import pathlib
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import cachetools
import falcon
import requests

from .dns_utils import custom_dns

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger("cache_service")

# Rate limiting: Scryfall allows 10 requests per second
RATE_LIMIT_CALLS = 10
RATE_LIMIT_PERIOD = 1  # second

DO_NOT_CACHE = object()

def default_getsizeof(_: object) -> int:
    """Default getsizeof function."""
    return 0

class FilesystemCache(cachetools.Cache):
    """Filesystem cache."""
    def __init__(self, maxsize: float = math.inf, getsizeof: Callable[[object], int] = default_getsizeof, cache_dir: str = "/data/cache") -> None:
        """Initialize the filesystem cache."""
        # Store parameters for potential future use
        self._maxsize = maxsize
        self._getsizeof = getsizeof
        self.cache_dir = pathlib.Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("FilesystemCache initialized with cache_dir: %s", self.cache_dir)

    def encode_key(self, key: object) -> pathlib.Path:
        """Encode a key."""
        host, pathname = key
        return self.cache_dir / host / base64.b64encode(pathname.encode()).decode("ascii").rstrip("=")

    def decode_key(self, encoded_key: str) -> tuple[str, str]:
        """Decode a key."""
        host, pathname = encoded_key.split("/")
        return host, base64.b64decode(pathname.encode()).decode("ascii").rstrip("=")

    def __getitem__(self, key: object) -> object:
        """Get an item from the cache."""
        if key is DO_NOT_CACHE:
            raise KeyError(key)
        encoded_key = self.encode_key(key)
        try:
            with encoded_key.open() as fh:
                logger.info("Getting item from cache: %s -> %s", key, encoded_key)
                # TODO: load headers into a CaseInsensitiveDict
                return json.load(fh)
        except FileNotFoundError:
            logger.info("Cache miss: %s -> %s", key, encoded_key)
            raise KeyError(key) from None

    def __setitem__(self, key: object, value: object) -> None:
        """Set an item in the cache."""
        if key is DO_NOT_CACHE:
            return
        encoded_key = self.encode_key(key)
        encoded_key.parent.mkdir(parents=True, exist_ok=True)
        with encoded_key.open("w") as fh:
            json.dump(value, fh, indent=4, sort_keys=True)

    def __delitem__(self, key: object) -> None:
        """Delete an item from the cache."""
        if key is DO_NOT_CACHE:
            return
        encoded_key = self.encode_key(key)
        encoded_key.unlink(missing_ok=True)

    def __contains__(self, key: object) -> bool:
        """Check if an item is in the cache."""
        if key is DO_NOT_CACHE:
            return False
        encoded_key = self.encode_key(key)
        return encoded_key.exists()

    def __len__(self) -> int:
        """Get the length of the cache."""
        return len(self.cache_dir.glob("**/*.json"))

    def __iter__(self) -> Iterator[pathlib.Path]:
        """Iterate over the cache."""
        return iter(self.cache_dir.glob("**/*.json"))


def _cache_key_for_handle_request(_ignored: object, method: str, uri: str) -> tuple[str, str]:
    """Get the cache key for the handle_request method."""
    if method == "GET":
        parsed = urlparse(uri)
        host = parsed.netloc
        path = parsed.path
        query = parsed.query
        return (host, f"{path}?{query}".rstrip("?"))
    return DO_NOT_CACHE

class CacheResource:
    """Falcon resource for the cache service API."""

    def __init__(self, cache_dir: str = "/data/cache") -> None:
        """Initialize the cache resource."""
        self.filesystem_cache = FilesystemCache(cache_dir)

        # Create reusable requests session with appropriate headers
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "ScryfallfOS-Cache/1.0 (https://github.com/jbylund/scryfallos)",
        })

    @cachetools.cachedmethod(
        cache=lambda self: self.filesystem_cache,
        key=_cache_key_for_handle_request,
    )
    def handle_request(self, method: str, uri: str) -> None:
        """Handle the request."""
        logger.info("Handling %s request: %s", method, uri)
        try:
            with custom_dns(["1.1.1.1", "8.8.8.8"]):
                upstream_response = self._session.request(method, uri)
        except requests.RequestException as oops:
            logger.error("Request failed for %s: %s", uri, oops)
            raise falcon.HTTPBadGateway(
                title="Upstream Error",
                description=f"Failed to fetch data from Scryfall: {oops}",
            ) from oops
        if upstream_response.headers.get("Content-Type") == "application/json":
            content = upstream_response.json()
        else:
            content = upstream_response.content.decode("utf-8")
        dict_headers = {
            k: upstream_response.headers[k]
            for k in upstream_response.headers
        }
        json.dumps(dict_headers)
        return {
            "content": content,
            "headers": dict_headers,
        }

    def _handle(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Handle the request."""
        response_object = self.handle_request(req.method, req.uri)
        headers = response_object["headers"]
        content = response_object["content"]

        if headers.get("Content-Type") == "application/json":
            resp.media = content
        else:
            resp.text = content
        resp.headers.update(headers)
