"""Top-level CLI dispatcher for the LinkedIn tools monorepo."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

APP_NAMES = ("network", "recruiter-agency", "opportunity", "comments", "ui", "cutover")


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
    from apps.cutover.cli import main as cutover_main
    from apps.network_automation.cli import main as network_main
    from apps.opportunity_intel.cli import main as opportunity_main
    from apps.recruiter_agency_outreach.cli import main as recruiter_agency_main
    from apps.review_ui.cli import main as ui_main

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        build_parser().print_help()
        return 0
    app = args[0]
    if app not in APP_NAMES:
        parser = build_parser()
        parser.error(f"unknown app namespace: {app}")
        return 2
    remaining = args[1:]
    if not remaining:
        # Let the app namespace print its own help whenever possible.
        remaining = ["--help"]
    dispatchers = {
        "network": network_main,
        "recruiter-agency": recruiter_agency_main,
        "opportunity": opportunity_main,
        "comments": comments_main,
        "ui": ui_main,
        "cutover": cutover_main,
    }
    return dispatchers[app](remaining)


if __name__ == "__main__":
    raise SystemExit(main())
