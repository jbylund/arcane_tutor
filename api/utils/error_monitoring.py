"""Error monitoring and exception tracking utilities."""

import logging
import os
import pathlib

import falcon
import orjson

logger = logging.getLogger(__name__)

# Try to import honeybadger, fall back to basic error handling if not available
try:
    import socket

    from honeybadger import honeybadger

    # Configure honeybadger if available
    honeybadger.configure(
        api_key="hbp_mHbJs4KJAOeUhK17Ixr0AzDC0gx8Zt2WG6kH",
        environment=os.getenv("ENV", "development"),
        hostname=socket.gethostname(),
        project_root=str(pathlib.Path(__file__).parent.parent.parent),
        report_local_variables=True,
    )

    def error_handler(req: falcon.Request, exception: Exception) -> None:
        """Handle an error with Honeybadger error monitoring.

        Args:
            req: The Falcon request object
            exception: The exception that occurred
        """
        logger.error("Error handling request: %s", exception, exc_info=True)
        honeybadger.notify(
            exception=exception,
            context={
                "headers": req.headers,
                "method": req.method,
                "params": req.params,
                "path": req.path,
                "query_string": req.query_string,
                "uri": req.uri,
            },
        )

except ImportError:
    # Fallback error handler when honeybadger is not available
    def error_handler(req: falcon.Request, exception: Exception) -> None:
        """Handle an error with basic logging when Honeybadger is not available.

        Args:
            req: The Falcon request object
            exception: The exception that occurred
        """
        del req  # Unused when honeybadger is not available
        logger.error("Error handling request: %s", exception, exc_info=True)


def can_serialize(iobj: object) -> bool:
    """Check if an object is JSON serializable and not too large.

    Args:
    ----
        iobj (object): The object to check.

    Returns:
    -------
        bool: True if serializable and not too large, False otherwise.

    """
    max_json_object_length = 16_000
    try:
        s = orjson.dumps(iobj).decode("utf-8")
        return len(s) < max_json_object_length
    except TypeError:
        return False
    return True
