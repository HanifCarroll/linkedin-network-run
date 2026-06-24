"""CLI for cutover readiness helpers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from .automation_audit import (
    Expectation,
    audit_automation_prompts,
    default_automation_root,
    plan_automation_prompt_edits,
    render_automation_audit,
    render_automation_edit_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-tools cutover",
        description="Cutover readiness checks.",
    )
    subparsers = parser.add_subparsers(dest="command")

    audit_parser = subparsers.add_parser("audit-automations")
    audit_parser.add_argument(
        "--root",
        default=str(default_automation_root()),
        help="Codex automation root to inspect",
    )
    audit_parser.add_argument(
        "--expect",
        choices=("any", "pre-cutover", "post-cutover"),
        default="any",
        help="expected prompt migration state",
    )
    audit_parser.add_argument("--json", action="store_true")

    plan_parser = subparsers.add_parser("plan-automation-edits")
    plan_parser.add_argument(
        "--root",
        default=str(default_automation_root()),
        help="Codex automation root to inspect",
    )
    plan_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        _run_command(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_command(args: argparse.Namespace) -> None:
    if args.command == "audit-automations":
        audit_report = audit_automation_prompts(
            root=Path(args.root),
            expectation=_expectation(args.expect),
        )
        print(audit_report.to_json() if args.json else render_automation_audit(audit_report))
        if not audit_report.passed:
            raise RuntimeError("automation prompt cutover audit failed")
        return
    if args.command == "plan-automation-edits":
        edit_report = plan_automation_prompt_edits(root=Path(args.root))
        print(edit_report.to_json() if args.json else render_automation_edit_plan(edit_report))
        return
    raise ValueError(f"unsupported command {args.command!r}")


def _expectation(value: str) -> Expectation:
    if value not in {"any", "pre-cutover", "post-cutover"}:
        raise ValueError(f"unsupported expectation {value!r}")
    return cast(Expectation, value)


if __name__ == "__main__":
    raise SystemExit(main())
