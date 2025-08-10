import json
import logging
import multiprocessing
import os

import bjoern
import falcon

logger = logging.getLogger(__name__)


def json_error_serializer(request: falcon.Request, response: falcon.Response, exception: falcon.HTTPError) -> None:
    """An error serializer that goes to json."""
    del request
    exception_dict = exception.to_dict()
    exception_dict = json.loads(json.dumps(exception_dict, default=str))
    response.media = exception_dict
    response.content_type = "application/json"


class ApiWorker(multiprocessing.Process):
    """Start a server process in a subprocess"""

    def __init__(self, *, host="0.0.0.0", port=8080, exit_flag: multiprocessing.Event = None):
        super().__init__()
        self.host = host
        self.port = port
        self.exit_flag = exit_flag

    @staticmethod
    def get_api() -> falcon.App:
        """Get a falcon api."""
        # we're doing this as a sort of post-fork load, I don't think there's anything in there
        # for which it matters, but for some servers or clients this is a little safer than
        # importing then forking

        import api_resource  # pylint: disable=import-outside-toplevel

        api = falcon.App()
        api.set_error_serializer(json_error_serializer)
        sink = api_resource.APIResource()
        api.add_sink(sink.handle, prefix="/")
        return api

    def run(self):
        """Run the server indefinitely"""
        logging.basicConfig(level=logging.INFO)
        logging.info("Starting worker with pid %d", os.getpid())
        try:
            app = self.get_api()
            bjoern.run(app, self.host, self.port, reuse_port=True)
        except Exception as e:
            logger.error("Error running server: %s", e)
            self.exit_flag.set()
