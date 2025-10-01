"""Main entrypoint for the api container."""

import argparse
import contextlib
import logging
import multiprocessing
import os
import signal
from types import FrameType

from .api_worker import ApiWorker

logger = logging.getLogger("api")

ALL_INTERFACES = "0.0.0.0"  # noqa: S104
DEFAULT_PORT = 8080
DEFAULT_WORKERS = 10

def get_args() -> dict:
    """Argument parsing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--workers", type=int, dest="num_workers", default=DEFAULT_WORKERS)
    return vars(parser.parse_args())

def run_server(  # noqa: C901
    *,
    port: int = DEFAULT_PORT,
    num_workers: int = DEFAULT_WORKERS,
) -> None:
    """Run the server."""
    logging.basicConfig(level=logging.INFO)
    workers: list[ApiWorker] = []
    logger.info("Starting %d workers on port %d...", num_workers, port)
    os.getpid()

    exit_flag = multiprocessing.Event()

    def graceful_shutdown(signum: int, frame: FrameType) -> None:
        """Graceful shutdown."""
        del frame
        logger.info("Received signal %d in pid %d, setting exit flag", signum, os.getpid())
        for iworker in workers:
            if iworker.pid is None:
                logger.warning("Worker %s has no pid", iworker)
                continue
            logger.info("Terminating worker %d", iworker.pid)
            with contextlib.suppress(AttributeError):
                iworker.terminate()
        wait_time = 1
        for iworker in workers:
            logger.info("Joining worker %d", iworker.pid)
            iworker.join(timeout=wait_time)
            if iworker.is_alive():
                logger.info("Killing worker %d", iworker.pid)
                iworker.kill()
            wait_time = 1 / 100
        logger.info("Shutdown complete")

    # Create shared objects for all workers
    import_guard = multiprocessing.RLock()
    schema_setup_event = multiprocessing.Event()

    # start workers
    for _ in range(num_workers):
        iworker = ApiWorker(
            exit_flag=exit_flag,
            host=ALL_INTERFACES,
            import_guard=import_guard,
            port=port,
            schema_setup_event=schema_setup_event,
        )
        workers.append(iworker)

    for iworker in workers:
        iworker.start()

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    def all_workers_alive() -> bool:
        if exit_flag.is_set():
            return False
        for iworker in workers:
            if iworker.is_alive():
                pass
            else:
                return False
        return True

    try:
        while all_workers_alive():
            # block for up to 1 second on exit flag being set
            response = exit_flag.wait(1 / 20)
            if response:
                logger.info("Exit flag set, terminating workers")
                break
    except KeyboardInterrupt:
        graceful_shutdown(signal.SIGINT, None)

    logger.info("Main server process exiting")


def main() -> None:
    """Main entrypoint for the api container."""
    logging.basicConfig(level=logging.INFO)
    args = get_args()
    run_server(
        port=args["port"],
        num_workers=args["num_workers"],
    )

if __name__ == "__main__":
    main()
