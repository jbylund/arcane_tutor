"""API worker process."""

from __future__ import annotations

import json
import logging
import multiprocessing
import os

import falcon
import falcon.media
import orjson

# Set up a logger for this module
logger = logging.getLogger(__name__)

DEFAULT_IMPORT_GUARD = multiprocessing.RLock()
ALL_INTERFACES = "0.0.0.0"  # noqa: S104


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

    def __init__(
        self,
        *,
        host: str = ALL_INTERFACES,
        port: int = 8080,
        exit_flag: multiprocessing.Event | None = None,
        debug: bool = False,
        import_guard: multiprocessing.RLock = DEFAULT_IMPORT_GUARD,
    ) -> None:
        """Initialize the API worker process.

        Args:
            host (str): The host address to bind the server to. Defaults to ALL_INTERFACES.
            port (int): The port to listen on. Defaults to 8080.
            exit_flag (multiprocessing.Event | None): An optional event to signal process exit.
            import_guard (multiprocessing.RLock): An optional lock to synchronize imports.
            debug (bool): Whether to run in debug mode.
        """
        super().__init__()
        self.host = host
        self.port = port
        self.exit_flag = exit_flag
        self.import_guard = import_guard
        self.debug = debug

    @classmethod
    def get_api(cls: type[ApiWorker], import_guard: multiprocessing.Lock) -> falcon.App:
        """Create and configure the Falcon API application.

        Returns:
            falcon.App: The configured Falcon application instance.
        """
        # Importing here (post-fork) is safer for some servers/clients than importing before forking.
        from .api_resource import APIResource  # pylint: disable=import-outside-toplevel  # noqa: PLC0415
        from .middlewares import CachingMiddleware, CompressionMiddleware, TimingMiddleware  # noqa: PLC0415

        api = falcon.App(
            middleware=[
                TimingMiddleware(),
                CachingMiddleware(),  # important that this is first
                CompressionMiddleware(),
            ],
        )
        api.set_error_serializer(json_error_serializer)  # Use custom JSON error serializer
        sink = APIResource(
            import_guard=import_guard,
        )  # Create the main API resource
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
            import bjoern  # noqa: PLC0415
            app = self.get_api(import_guard=self.import_guard)  # Get the Falcon app
            bjoern.run(
                wsgi_app=app,
                host=self.host,
                port=self.port,
                reuse_port=True,
                listen_backlog=1024 * 4,
            )  # Start the Bjoern server
        except Exception as oops:
            logger.error("Error running server: %s", oops, exc_info=True)
            if self.exit_flag:
                self.exit_flag.set()  # Signal exit if an exit flag is provided
