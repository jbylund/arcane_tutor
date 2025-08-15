import argparse
import logging
import time

logger = logging.getLogger("api")


def get_args() -> dict:
    """Parse command-line arguments and return them as a dictionary.

    Returns:
    -------
        dict: Dictionary of parsed command-line arguments.

    """
    parser = argparse.ArgumentParser()
    return vars(parser.parse_args())


def main() -> None:
    """Main entry point for the client.

    Sets up logging and enters an infinite loop.
    """
    logging.basicConfig(level=logging.INFO)
    _args = get_args()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
