#!/usr/bin/env python3
"""Main entrypoint for the api container"""
import argparse
import logging
import multiprocessing
import time
from typing import Dict

import bjoern
import falcon
from api_worker import ApiWorker

logger = logging.getLogger("api")


def get_args() -> Dict:
    """Argument parsing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--workers", type=int, default=10)
    return vars(parser.parse_args())


def main():
    """Main entrypoint for the api container"""
    logging.basicConfig(level=logging.INFO)
    args = get_args()
    workers = []

    logger.info("Starting %d workers on port %d...", args["workers"], args["port"])

    # start workers
    for _ in range(args["workers"]):
        iworker = ApiWorker(host="0.0.0.0", port=args["port"])
        workers.append(iworker)
        iworker.start()

    def all_workers_alive():
        for iworker in workers:
            if iworker.is_alive():
                pass
            else:
                return False
        return True

    try:
        while all_workers_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    # terminate all the workers
    for iworker in workers:
        iworker.terminate()


if "__main__" == __name__:
    main()
