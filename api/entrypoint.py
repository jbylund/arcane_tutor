"""Main entrypoint for the api container."""

import argparse
import logging
import multiprocessing

from api_worker import ApiWorker

logger = logging.getLogger("api")


def get_args() -> dict:
    """Argument parsing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--workers", type=int, default=10)
    return vars(parser.parse_args())


def main() -> None:
    """Main entrypoint for the api container."""
    logging.basicConfig(level=logging.INFO)
    args = get_args()
    workers = []

    logger.info("Starting %d workers on port %d...", args["workers"], args["port"])

    exit_flag = multiprocessing.Event()
    # start workers
    for _ in range(args["workers"]):
        iworker = ApiWorker(host="0.0.0.0", port=args["port"], exit_flag=exit_flag)
        workers.append(iworker)
        iworker.start()

    def all_workers_alive() -> bool:
        for iworker in workers:
            if iworker.is_alive():
                pass
            else:
                return False
        return True

    try:
        while all_workers_alive():
            # block for up to 1 second on exit flag being set
            response = exit_flag.wait(1)
            if response:
                logger.info("Exit flag set, terminating workers")
                break
    except KeyboardInterrupt:
        pass

    # terminate all the workers
    for iworker in workers:
        iworker.terminate()


if __name__ == "__main__":
    main()
