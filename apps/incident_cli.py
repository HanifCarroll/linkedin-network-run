"""CLI for the shared LinkedIn browser incident gate."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from packages.linkedin_browser.incident import (
    IncidentKind,
    active_incident,
    clear_incident,
    load_incident,
    open_incident,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-tools incident",
        description="Inspect, open, or explicitly clear the shared LinkedIn incident gate.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")

    open_command = commands.add_parser("open")
    open_command.add_argument(
        "--kind", choices=[kind.value for kind in IncidentKind], required=True
    )
    open_command.add_argument("--source", required=True)
    open_command.add_argument("--summary", required=True)
    open_command.add_argument("--operation", default=None)
    open_command.add_argument("--evidence-path", default=None)

    clear = commands.add_parser("clear")
    clear.add_argument("--reason", required=True)
    clear.add_argument("--confirm-account-access", action="store_true")
    clear.add_argument("--confirm-warning-cleared", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        incident = load_incident()
        if args.json:
            print(
                json.dumps(
                    {
                        "active": incident.active if incident is not None else False,
                        "incident": (
                            incident.model_dump(mode="json") if incident is not None else None
                        ),
                    },
                    indent=2,
                )
            )
        elif incident is None:
            print("LinkedIn incident gate: clear; no incident has been recorded")
        else:
            state = "ACTIVE" if incident.active else "cleared"
            print(
                f"LinkedIn incident gate: {state}; id={incident.id}; "
                f"latest={incident.latest.kind.value}; {incident.latest.summary}"
            )
        return 2 if active_incident() is not None else 0
    if args.command == "open":
        incident = open_incident(
            kind=IncidentKind(args.kind),
            source=args.source,
            summary=args.summary,
            operation=args.operation,
            evidence_path=args.evidence_path,
        )
        print(f"LinkedIn incident opened: {incident.id} ({incident.latest.kind.value})")
        return 0
    if args.command == "clear":
        incident = clear_incident(
            reason=args.reason,
            account_access_confirmed=args.confirm_account_access,
            warning_cleared_confirmed=args.confirm_warning_cleared,
        )
        print(f"LinkedIn incident cleared: {incident.id}")
        return 0
    raise RuntimeError(f"unhandled incident command {args.command}")
