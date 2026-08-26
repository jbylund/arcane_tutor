"""Error monitoring and exception tracking utilities."""

from __future__ import annotations

import collections.abc
import logging
import os
import pathlib
import re
import socket
from typing import TYPE_CHECKING

import orjson

if TYPE_CHECKING:
    import falcon

logger = logging.getLogger(__name__)

FILTERED_VALUE = "[FILTERED]"

PARAMS_FILTERS: tuple[str, ...] = (
    "password",
    "password_confirmation",
    "credit_card",
    "CSRF_COOKIE",
    "authorization",
    "cookie",
    "admin_password",
    "honeybadger_api_key",
)

SENSITIVE_NORMALIZED_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "http_authorization",
        "proxy_authorization",
        "cookie",
        "cookies",
        "http_cookie",
        "set_cookie",
        "csrf_cookie",
        "admin_password",
        "password",
        "password_confirmation",
        "credit_card",
        "honeybadger_api_key",
        "honeybadger_key",
        "honeybadger_apikey",
    }
)


def _normalize_key(key: object) -> str:
    """Normalize a key string to lowercase snake_case for insensitive matching."""
    if not isinstance(key, str):
        key = str(key)
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", key)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    s3 = re.sub(r"[^a-zA-Z0-9]+", "_", s2)
    return s3.strip("_").lower()


def is_sensitive_key(key: object) -> bool:
    """Determine if a key name matches sensitive patterns case-insensitively."""
    norm = _normalize_key(key)
    if norm in SENSITIVE_NORMALIZED_KEYS:
        return True
    if norm.endswith(("_authorization", "_password", "_cookie", "_cookies", "_honeybadger_api_key")):
        return True
    return bool(norm.startswith("honeybadger_") and norm.endswith(("_key", "_api_key", "_token")))


def sanitize_data(data: object, memo: set[int] | None = None) -> object:
    """Recursively redact sensitive keys from mappings and sequences.

    Copies data structures so the caller's input is never mutated.

    Args:
        data: The data structure to sanitize.
        memo: Set of object ids to prevent infinite loops in cyclic references.

    Returns:
        A sanitized copy with sensitive fields replaced with `[FILTERED]`.
    """
    if memo is None:
        memo = set()
    obj_id = id(data)
    if obj_id in memo:
        return "[CIRCULAR]"

    if isinstance(data, collections.abc.Mapping):
        memo.add(obj_id)
        try:
            return {k: FILTERED_VALUE if is_sensitive_key(k) else sanitize_data(v, memo) for k, v in data.items()}
        finally:
            memo.remove(obj_id)
    elif isinstance(data, (list, tuple, set)):
        memo.add(obj_id)
        try:
            sanitized_items = [sanitize_data(item, memo) for item in data]
            if isinstance(data, tuple):
                return tuple(sanitized_items)
            if isinstance(data, set):
                return set(sanitized_items)
            return sanitized_items
        finally:
            memo.remove(obj_id)
    return data


def sanitize_notice(notice: object) -> object:
    """Sanitize a Honeybadger notice before notification.

    Redacts sensitive keys from context and retained local variable dictionaries.

    Args:
        notice: The Honeybadger Notice instance or dictionary payload.

    Returns:
        The sanitized notice.
    """
    if hasattr(notice, "context") and isinstance(notice.context, collections.abc.Mapping):
        notice.context = sanitize_data(notice.context)
    if hasattr(notice, "payload"):
        payload = notice.payload
        if isinstance(payload, dict):
            req_data = payload.get("request")
            if isinstance(req_data, dict):
                payload["request"] = sanitize_data(req_data)
    elif isinstance(notice, dict):
        if "request" in notice and isinstance(notice["request"], dict):
            notice["request"] = sanitize_data(notice["request"])
        elif "context" in notice and isinstance(notice["context"], dict):
            notice["context"] = sanitize_data(notice["context"])
        else:
            notice = sanitize_data(notice)
    return notice


api_key: str | None = os.environ.get("HONEYBADGER_API_KEY")

deployment_env = os.getenv("ENVIRONMENT", "unknown")
hostname = os.getenv("HOSTNAME", socket.gethostname())

honeybadger_config = {
    "deployment_env": deployment_env,
    "environment": f"{deployment_env}-{hostname}",
    "force_report_data": True,
    "hostname": hostname,
    "project_root": str(pathlib.Path(__file__).parent.parent.parent),
    "report_local_variables": True,
    "params_filters": list(PARAMS_FILTERS),
    "before_notify": sanitize_notice,
}

try:
    from honeybadger import honeybadger

    if api_key:
        honeybadger.configure(
            api_key=api_key,
            **honeybadger_config,
        )
except ImportError:
    honeybadger = None


def error_handler(req: falcon.Request, exception: Exception) -> None:
    """Handle an error with Honeybadger error monitoring or logging fallback.

    Args:
        req: The Falcon request object
        exception: The exception that occurred
    """
    logger.error("Error handling request: %s", exception, exc_info=True)
    if api_key and honeybadger is not None:
        logger.error("Honeybadger config: %s", honeybadger_config)
        context = {
            "headers": sanitize_data(dict(req.headers)) if hasattr(req, "headers") and req.headers else {},
            "method": getattr(req, "method", None),
            "params": sanitize_data(dict(req.params)) if hasattr(req, "params") and req.params else {},
            "path": getattr(req, "path", None),
        }
        honeybadger.notify(
            exception=exception,
            context=context,
        )


def can_serialize(iobj: object) -> bool:
    """Check if an object is JSON serializable and not too large.

    Args:
        iobj: The object to check.

    Returns:
        True if serializable and not too large, False otherwise.
    """
    max_json_object_length = 16_000
    try:
        s = orjson.dumps(iobj).decode("utf-8")
        return len(s) < max_json_object_length
    except TypeError:
        return False
