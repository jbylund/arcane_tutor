import argparse
import logging
import time

logger = logging.getLogger("api")

def get_args():
    parser = argparse.ArgumentParser()
    return vars(parser.parse_args())

def main():
    logging.basicConfig(level=logging.INFO)
    args = get_args()
    while True:
        time.sleep(1)

if "__main__" == __name__:
    main()
