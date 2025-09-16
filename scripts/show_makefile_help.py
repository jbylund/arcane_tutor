"""Lift out "docstrings" from a specially constructed makefile."""

# BEWARE: this script runs OUTSIDE containers, so pay special attention
# to portability / avoid third party deps
import argparse
from pathlib import Path


def get_targets_to_docs(filename: str) -> dict[str, str]:
    """Get a mapping of {target: doc} for documented targets in the makefile."""
    needle = "@doc"  # but not the first one

    def linefilter(line: str) -> bool:
        return (
            ":" in line
            and needle in line  # target lines must have a :
            and not line.lstrip().startswith("#")  # must have an @doc  # no comment lines
        )

    targets_to_docs = {}
    with Path(filename).open() as mkfilefh:
        for line in list(filter(linefilter, mkfilefh)):
            targets, _, rest = line.partition(":")
            docstring = rest.partition(needle)[-1].strip()
            for itarget in targets.split():
                targets_to_docs[itarget] = docstring
    return targets_to_docs


def pretty_output(targets_to_docs: dict[str, str]) -> None:
    """Output target info to stdout."""
    if not targets_to_docs:
        return
    len(max(targets_to_docs, key=len))
    for _targetname, _targetdoc in sorted(targets_to_docs.items()):
        pass


def get_args() -> dict[str, str]:
    """Argument parsing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("makefile")
    return vars(parser.parse_args())


def main() -> None:
    """Main entry point."""
    args = get_args()
    targets_to_docs = get_targets_to_docs(args["makefile"])
    pretty_output(targets_to_docs)


if __name__ == "__main__":
    main()
