# `python -m bottles.backend.steps check|dump KIND PATH`
#
# Parses a manifest file on any host: this imports only the grammar and the
# frontend, so no GTK, Wine or bottle is needed to check that a manifest is
# well-formed or to see the program it compiles to.
import argparse
import sys

from bottles.backend.steps.grammar import ProgramKind
from bottles.backend.steps.yaml_frontend import ManifestError, parse_file


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bottles.backend.steps",
        description="Check a dependency or installer manifest, or dump its program.",
    )
    parser.add_argument("verb", choices=["check", "dump"])
    parser.add_argument("kind", choices=[kind.value for kind in ProgramKind])
    parser.add_argument("path", help="path to the manifest YAML file")
    args = parser.parse_args(argv)

    try:
        program = parse_file(args.path, ProgramKind(args.kind))
    except ManifestError as error:
        print(error.message, file=sys.stderr)
        return 1
    except OSError as error:
        print(error, file=sys.stderr)
        return 1

    if args.verb == "dump":
        text = program.dump()
        if text:
            print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
