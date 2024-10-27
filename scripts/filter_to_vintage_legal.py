import json
import argparse


def get_args():
    """Argument parsing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("filename")
    return vars(parser.parse_args())

def main():
    args = get_args()
    with open(args["filename"], "r") as f:
        cards = json.load(f)
    legal_cards = []
    for icard in cards:
        if icard["legalities"]["vintage"] != "legal":
            continue
        try:
            icard["collector_number"] = int(icard["collector_number"])
        except ValueError:
            continue
        legal_cards.append(icard)
    with open(args["filename"], "w") as f:
        json.dump(legal_cards, f, indent=4, sort_keys=True)


if "__main__" == __name__:
    main()
