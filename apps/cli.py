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
    from apps.comment_extractor.cli import main as comments_main
    from apps.network_automation.cli import main as network_main
    from apps.opportunity_intel.cli import main as opportunity_main
    from apps.recruiter_agency_outreach.cli import main as recruiter_agency_main
    from apps.review_ui.cli import main as ui_main

    parser = build_parser()
    args, remaining = parser.parse_known_args(argv)
    if args.app is None:
        parser.print_help()
        return 0
    dispatchers = {
        "network": network_main,
        "recruiter-agency": recruiter_agency_main,
        "opportunity": opportunity_main,
        "comments": comments_main,
        "ui": ui_main,
    }
    return dispatchers[str(args.app)](remaining)


if __name__ == "__main__":
    raise SystemExit(main())
