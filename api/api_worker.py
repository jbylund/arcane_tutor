"""API worker process."""

from __future__ import annotations

import json
import logging
import multiprocessing
import os

import bjoern
import falcon
import falcon.media
import orjson

# Set up a logger for this module
logger = logging.getLogger(__name__)


def json_error_serializer(request: falcon.Request, response: falcon.Response, exception: falcon.HTTPError) -> None:
    """An error serializer that formats Falcon HTTP errors as JSON responses.

    Args:
        request (falcon.Request): The incoming HTTP request (unused).
        response (falcon.Response): The HTTP response object to modify.
        exception (falcon.HTTPError): The exception to serialize.
    """
    del request  # request is unused, but required by the interface
    exception_dict = exception.to_dict()  # Convert the exception to a dictionary
    exception_dict = json.loads(json.dumps(exception_dict, default=str))  # Ensure all values are JSON serializable
    response.media = exception_dict  # Set the response body
    response.content_type = "application/json"  # Set the content type


class ApiWorker(multiprocessing.Process):
    """A worker process that runs a Falcon API server in a separate subprocess.

    This class is designed to be used with Python's multiprocessing module to run
    the API server in its own process, allowing for parallelism and isolation.
    """

    def __init__(self, *, host: str = "0.0.0.0", port: int = 8080, exit_flag: multiprocessing.Event | None = None) -> None:
        """Initialize the API worker process.

        Args:
            host (str): The host address to bind the server to. Defaults to "0.0.0.0".
            port (int): The port to listen on. Defaults to 8080.
            exit_flag (multiprocessing.Event | None): An optional event to signal process exit.
        """
        super().__init__()
        self.host = host
        self.port = port
        self.exit_flag = exit_flag

    @classmethod
    def get_api(cls: type[ApiWorker]) -> falcon.App:
        """Create and configure the Falcon API application.

        Returns:
            falcon.App: The configured Falcon application instance.
        """
        # Importing here (post-fork) is safer for some servers/clients than importing before forking.
        import api_resource  # pylint: disable=import-outside-toplevel
        from middlewares import CachingMiddleware, CompressionMiddleware, TimingMiddleware, TracingMiddleware
        from telemetry import setup_tracing

        # Initialize tracing early so spans are exported
        setup_tracing(service_name="apiservice")

        api = falcon.App(
            middleware=[
                TracingMiddleware(),
                TimingMiddleware(),
                # ProfilingMiddleware(),
                CachingMiddleware(), # important that this is first
                CompressionMiddleware(),
            ],
        )
        api.set_error_serializer(json_error_serializer)  # Use custom JSON error serializer
        sink = api_resource.APIResource()  # Create the main API resource
        api.add_sink(sink._handle, prefix="/")  # Route all requests to the sink handler

        json_handler = falcon.media.JSONHandler(
            dumps=orjson.dumps,
            loads=orjson.loads,
        )
        extra_handlers = {
            "application/json": json_handler,
        }

        api.req_options.media_handlers.update(extra_handlers)
        api.resp_options.media_handlers.update(extra_handlers)

        return api

    def run(self) -> None:
        """Run the API server indefinitely in this process.

        This method is called when the process starts. It sets up logging, creates the API,
        and starts the Bjoern server. If an error occurs, it logs the error and sets the exit flag.
        """
        logging.basicConfig(level=logging.INFO)
        logging.info("Starting worker with pid %d", os.getpid())
        try:
            app = self.get_api()  # Get the Falcon app
            bjoern.run(app, self.host, self.port, reuse_port=True)  # Start the Bjoern server
        except Exception as e:
            logger.error("Error running server: %s", e)
            if self.exit_flag:
                self.exit_flag.set()  # Signal exit if an exit flag is provided
