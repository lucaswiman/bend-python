"""Project setup helpers: ``python -m bend_python vendor bend``."""

import argparse

from .library import get_include, vendor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    copy = commands.add_parser("vendor", help="copy the bundled Bend library into a project")
    copy.add_argument("directory", nargs="?", default="bend")
    copy.add_argument("--force", action="store_true", help="replace modified library files")
    commands.add_parser("include", help="print the installed Bend library directory")
    args = parser.parse_args()
    try:
        result = (
            vendor(args.directory, force=args.force) if args.command == "vendor" else get_include()
        )
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"bend-python: {error}\n")
    print(result)


if __name__ == "__main__":
    main()
