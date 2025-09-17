"""SSL session handling for accepting local certificates."""

import logging
import pathlib
from urllib.parse import urlparse

import requests

logger = logging.getLogger("apiresource")


class AcceptLocalCertSession(requests.Session):
    """Session that accepts local certificates."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Initialize the session with SSL certificate settings."""
        super().__init__(*args, **kwargs)
        self.ssl_cert_path = "/data/ssl/certs/nginx-selfsigned.crt"
        cert_path = pathlib.Path(self.ssl_cert_path)
        if cert_path.exists():
            self.verify = self.ssl_cert_path
        else:
            self.verify = True


    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        """Request with appropriate SSL verification settings."""
        parsed_url = urlparse(url)
        if parsed_url.netloc.endswith("scryfall.com"):
            kwargs["verify"] = self.verify
        logger.info(
            "Requesting %s / %s with verify %s / %s",
            url,
            parsed_url.netloc,
            kwargs.get("verify"),
            kwargs,
        )
        return super().request(method, url, **kwargs)
