"""Error monitoring and exception tracking utilities."""

import logging
import os

import falcon
import orjson

logger = logging.getLogger(__name__)



# Try to import honeybadger, fall back to basic error handling if not available
try:
    api_key = os.environ["HONEYBADGER_API_KEY"]
except KeyError:
    # Fallback error handler when honeybadger is not available
    def error_handler(req: falcon.Request, exception: Exception) -> None:
        """Handle an error with basic logging when Honeybadger is not available.

        Args:
            req: The Falcon request object
            exception: The exception that occurred
        """
        del req  # Unused when honeybadger is not available
        logger.error("Error handling request: %s", exception, exc_info=True)
else:
    import pathlib
    import socket

    from honeybadger import honeybadger

    deployment_env = os.getenv("ENVIRONMENT", "unknown")
    hostname = os.getenv("HOSTNAME", socket.gethostname())


    honeybadger_config = {
        "deployment_env": deployment_env,
        "environment": f"{deployment_env}-{hostname}",
        "force_report_data": True,
        "hostname": hostname,
        "project_root": str(pathlib.Path(__file__).parent.parent.parent),
        "report_local_variables": True,
    }

    # Only configure honeybadger if API key is available
    if api_key:
        honeybadger.configure(
            api_key=api_key,
            **honeybadger_config,
        )

    def error_handler(req: falcon.Request, exception: Exception) -> None:
        """Handle an error with Honeybadger error monitoring.

        Args:
            req: The Falcon request object
            exception: The exception that occurred
        """
        logger.error("Error handling request: %s", exception, exc_info=True)
        if api_key:
            logger.error("Honeybadger config: %s", honeybadger_config)
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
