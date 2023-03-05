import multiprocessing
import falcon
import bjoern

class ApiWorker(multiprocessing.Process):
    """Start a server process in a subprocess"""

    def __init__(self, host="0.0.0.0", port=8080):
        super().__init__()
        self.host = host
        self.port = port

    @staticmethod
    def json_error_serializer(
        _: object, response: falcon.Response, exception: falcon.HTTPError
    ) -> None:
        """An error serializer that goes to json."""
        response.body = exception.to_json()
        response.content_type = "application/json"

    @staticmethod
    def get_api() -> falcon.App:
        """Get a falcon api."""
        # we're doing this as a sort of post-fork load, I don't think there's anything in there
        # for which it matters, but for some servers or clients this is a little safer than
        # importing then forking

        import api_resource  # pylint: disable=import-outside-toplevel

        api = falcon.App()
        api.set_error_serializer(ApiWorker.json_error_serializer)
        sink = api_resource.APIResource()
        api.add_sink(sink.handle, prefix="/")
        return api

    def run(self):
        """Run the server indefinitely"""
        app = self.get_api()
        bjoern.run(app, self.host, self.port, reuse_port=True)