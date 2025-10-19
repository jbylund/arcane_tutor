"""Command-line interface for Gatherer import functionality."""
import argparse
import contextlib
import json
import sys
from pathlib import Path

from .fetch_gatherer_data import GathererFetcher


def main() -> int:
    """Run the Gatherer import command-line interface."""
    parser = argparse.ArgumentParser(
        description="Import Magic: The Gathering card data from Gatherer",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # list-sets command
    list_parser = subparsers.add_parser("list-sets", help="List all available sets")
    list_parser.add_argument("--output", "-o", help="Output JSON file path")

    # fetch-set command
    fetch_parser = subparsers.add_parser("fetch-set", help="Fetch a specific set")
    fetch_parser.add_argument("set_code", help="Set code (e.g., TDM)")
    fetch_parser.add_argument("--output", "-o", help="Output directory (default: gatherer_data)",
                             default="gatherer_data")

    # fetch-all command
    fetch_all_parser = subparsers.add_parser("fetch-all", help="Fetch all sets")
    fetch_all_parser.add_argument("--output", "-o", help="Output directory (default: gatherer_data)",
                                  default="gatherer_data")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    fetcher = GathererFetcher()

    if args.command == "list-sets":
        sets = fetcher.fetch_all_sets()

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(sets, f, indent=2, ensure_ascii=False)
        else:
            pass

    elif args.command == "fetch-set":
        fetcher.save_set_to_json(args.set_code, args.output)

    elif args.command == "fetch-all":
        sets = fetcher.fetch_all_sets()

        # Extract set codes from the sets data
        for _idx, set_data in enumerate(sets, 1):
            # The set_data structure may have different formats
            # Try to extract the set code
            set_code = None
            if isinstance(set_data, dict):
                set_code = set_data.get("code") or set_data.get("Code") or set_data.get("setCode")

            if not set_code:
                continue

            with contextlib.suppress(Exception):
                fetcher.save_set_to_json(set_code, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
