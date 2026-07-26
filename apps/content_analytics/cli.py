"""CLI for deterministic LinkedIn content analytics exports."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from .exporter import export_content_analytics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-tools analytics",
        description="Read-only LinkedIn content analytics workflows.",
    )
    commands = parser.add_subparsers(dest="command")
    export = commands.add_parser(
        "export",
        help="Download the current combined content analytics XLSX through Playwriter.",
    )
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--session", default="auto")
    export.add_argument("--browser", default=None)
    export.add_argument("--playwriter-bin", default=None)
    export.set_defaults(handler=_handle_export)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return cast(Callable[[argparse.Namespace], int], handler)(args)


def _handle_export(args: argparse.Namespace) -> int:
    receipt = export_content_analytics(
        out=args.out,
        session=None if args.session in {None, "", "auto"} else str(args.session),
        browser_key=args.browser,
        playwriter_bin=args.playwriter_bin,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0
