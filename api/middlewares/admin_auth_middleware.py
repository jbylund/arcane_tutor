"""HTTP Basic Auth gate for every route under the admin mount.

The mount (`api/admin_resource.py`'s `_admin` prefix) is what makes this cheap: one check where
requests enter the child, rather than a decorator on every handler that someone forgets on the
next one. See docs/issues/security-admin-route-split-child-resources.md for the fuller design
history -- this is that doc's last open step, "auth at the mount".

Basic Auth over a bespoke header was the deliberate choice: it gets a native browser login prompt
for free (no admin UI to build), and `curl -u user:pass` needs no separate tooling either. The
username is not checked -- there is one shared secret, not per-operator accounts -- only the
password is compared, in constant time, against ADMIN_PASSWORD.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
from typing import TYPE_CHECKING

from api.admin_resource import ADMIN_MOUNT_PREFIX
from api.settings import settings

if TYPE_CHECKING:
    import falcon

logger = logging.getLogger(__name__)

_REALM = "admin"
_ADMIN_PREFIX_WITH_SLASH = f"{ADMIN_MOUNT_PREFIX}/"


def _is_admin_path(path: str) -> bool:
    """Check whether a request path falls under the admin mount.

    Args:
        path: req.path, unmodified (leading slash still present).

    Returns:
        True if the path is the admin mount itself or anything nested under it.
    """
    normalized = path.strip("/")
    return normalized == ADMIN_MOUNT_PREFIX or normalized.startswith(_ADMIN_PREFIX_WITH_SLASH)


def _is_authenticated(req: falcon.Request) -> bool:
    """Check whether a request carries a valid admin credential.

    Args:
        req: The incoming request.

    Returns:
        True if ADMIN_PASSWORD is set and the request's Basic Auth password matches it.
    """
    expected = settings.admin_password
    if not expected:
        return False
    supplied = _supplied_password(req.get_header("Authorization"))
    return supplied is not None and secrets.compare_digest(supplied.encode(), expected.encode())


def _supplied_password(authorization_header: str | None) -> str | None:
    """Extract the password from a `Basic` Authorization header.

    Args:
        authorization_header: Raw Authorization header value, or None if absent.

    Returns:
        The password half of `user:password`, or None if the header is missing, is not a
        well-formed Basic credential, or carries no `:` separator.
    """
    if not authorization_header:
        return None
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "basic" or not token:
        return None
    token = token.strip()
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    _username, sep, password = decoded.partition(":")
    return password if sep else None


class AdminAuthMiddleware:
    """Requires a valid Basic Auth password on every request under the admin mount.

    Runs ahead of CachingMiddleware in the middleware list so a rejected request never reaches
    the cache lookup, and stamps `Cache-Control: no-store` on every admin-path response -- pass
    or reject -- so nothing under the mount is ever eligible for CachingMiddleware to store in
    the first place, independent of ordering.

    Also stamps `req.context["admin_authenticated"]` on *every* request, not only ones under the
    mount: `_raise_not_found` (`api/api_resource.py`) reads it to decide whether a 404 gets the
    public route listing or the full one including admin routes -- there's no reason to hide the
    admin surface from a caller who has already proven they hold the shared secret. Checking the
    credential unconditionally costs nothing on the hot path: with no Authorization header (the
    overwhelming majority of requests) `_supplied_password` returns immediately.
    """

    def __init__(self) -> None:
        """Warn once at startup if the admin surface has no password to check against."""
        if not settings.admin_password:
            logger.warning("ADMIN_PASSWORD is not set -- every _admin request will be rejected")

    def process_request(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Reject unauthenticated requests to the admin mount; mark the rest no-store.

        Args:
            req: The incoming request.
            resp: The response to populate on rejection, or annotate on passthrough.
        """
        authenticated = _is_authenticated(req)
        req.context["admin_authenticated"] = authenticated

        if not _is_admin_path(req.path):
            return

        resp.set_header("Cache-Control", "no-store")

        if authenticated:
            return

        resp.status = "401 Unauthorized"
        resp.set_header("WWW-Authenticate", f'Basic realm="{_REALM}"')
        resp.media = {"error": "Unauthorized"}
        resp.complete = True
