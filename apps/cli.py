"""Top-level CLI dispatcher for the LinkedIn tools monorepo."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

APP_NAMES = ("network", "recruiter-agency", "opportunity", "comments", "ui")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-tools",
        description="LinkedIn networking, outreach, and opportunity intelligence tools.",
    )
    subparsers = parser.add_subparsers(dest="app")
    for app_name in APP_NAMES:
        subparser = subparsers.add_parser(app_name)
        subparser.set_defaults(app=app_name)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.app is None:
        parser.print_help()
        return 0
    print(f"{args.app} namespace is scaffolded; implementation is owned by its workstream.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
