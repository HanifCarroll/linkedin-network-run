"""Temporary compatibility command entrypoints during migration."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from apps.network_automation.cli import main as network_main
from apps.opportunity_intel.cli import main as opportunity_main
from apps.recruiter_agency_outreach.cli import main as recruiter_agency_main
from packages.linkedin_common.paths import DEFAULT_STATE_ROOT
from packages.linkedin_storage.migrations import (
    SourceApp,
    import_legacy_network_state,
    import_legacy_opportunity_runs,
    import_legacy_recruiter_agency_state,
)

NETWORK_COMMANDS = (
    "start",
    "audit",
    "import-audit",
    "next",
    "record",
    "record-send-result",
    "send-next",
    "send-guarded",
    "drain-stale-candidates",
    "reconcile-audit",
    "top-up-reconcile",
    "source-exhausted",
    "needs-reaudit",
    "resume-blocked",
    "capture",
    "import-capture",
    "record-top-up-result",
    "next-candidate",
    "candidates",
    "plan",
    "status",
    "report",
    "finish",
    "acceptance",
    "reservoir",
    "tune-sources",
    "pending-cleanup",
    "old-state",
    "import-legacy-state",
)

RECRUITER_AGENCY_COMMANDS = (
    "run-daily",
    "capture",
    "capture-accounts",
    "import-capture",
    "import-accounts",
    "accounts",
    "agency-pool",
    "lead",
    "queue",
    "draft",
    "dashboard",
    "last-run",
    "recommend-next-run",
    "revise",
    "serve",
    "send-ready",
    "send-message",
    "mark-message",
    "reject",
    "report",
    "import-legacy-state",
)

OPPORTUNITY_COMMANDS = (
    "validate-contracts",
    "post-queue",
    "run-experiment",
    "sources",
    "query-pack",
    "collection-queue",
    "collection-coverage",
    "prepare-batch",
    "run-batch",
    "batch-status",
    "provider-readiness",
    "process-batch",
    "validate-batch",
    "review-queue",
    "calibration-template",
    "calibration-report",
    "source-decision",
    "action-plan",
    "export-captures-csv",
    "merge-comments-csv",
    "provider-export-csv",
    "run-history",
    "checkpoint",
    "gate-report",
    "iteration-plan",
    "import-signals",
    "run-spike",
    "public-post-capture",
    "evaluate",
    "profile-enrich",
    "salesnav-feeder",
    "salesnav-activity",
    "import-legacy-state",
    "status",
)

NETWORK_APP_COMMANDS = frozenset(
    {
        "start",
        "audit",
        "import-audit",
        "reconcile-audit",
        "record",
        "record-send-result",
        "record-top-up-result",
        "send-next",
        "send-guarded",
        "drain-stale-candidates",
        "source-exhausted",
        "needs-reaudit",
        "resume-blocked",
        "capture",
        "import-capture",
        "next",
        "next-candidate",
        "candidates",
        "plan",
        "status",
        "report",
        "finish",
        "top-up-reconcile",
        "tune-sources",
        "acceptance",
        "reservoir",
        "pending-cleanup",
        "old-state",
    }
)
RECRUITER_AGENCY_APP_COMMANDS = frozenset(
    {
        "run-daily",
        "capture",
        "capture-accounts",
        "import-capture",
        "import-accounts",
        "accounts",
        "lead",
        "queue",
        "draft",
        "dashboard",
        "last-run",
        "recommend-next-run",
        "serve",
        "revise",
        "send-ready",
        "send-message",
        "mark-message",
        "reject",
        "report",
        "agency-pool",
    }
)
OPPORTUNITY_APP_COMMANDS = frozenset(
    command for command in OPPORTUNITY_COMMANDS if command != "import-legacy-state"
)


def linkedin_network_run(argv: Sequence[str] | None = None) -> int:
    return _dispatch_compat_command(
        command_name="linkedin-network-run",
        source_app="network",
        commands=NETWORK_COMMANDS,
        argv=sys.argv[1:] if argv is None else argv,
    )


def recruiter_agency_outreach(argv: Sequence[str] | None = None) -> int:
    return _dispatch_compat_command(
        command_name="recruiter-agency-outreach",
        source_app="recruiter_agency",
        commands=RECRUITER_AGENCY_COMMANDS,
        argv=sys.argv[1:] if argv is None else argv,
    )


def linkedin_opportunity_intel(argv: Sequence[str] | None = None) -> int:
    return _dispatch_compat_command(
        command_name="linkedin-opportunity-intel",
        source_app="opportunity",
        commands=OPPORTUNITY_COMMANDS,
        argv=sys.argv[1:] if argv is None else argv,
    )


def _dispatch_compat_command(
    *,
    command_name: str,
    source_app: SourceApp,
    commands: tuple[str, ...],
    argv: Sequence[str],
) -> int:
    args = list(argv)
    if not args or args[0] in {"-h", "--help"}:
        _print_help(command_name, commands)
        return 0

    command = args[0]
    command_args = args[1:]
    if command in {"-h", "--help"}:
        _print_help(command_name, commands)
        return 0
    if command == "import-legacy-state":
        return _import_legacy_state(source_app=source_app, argv=command_args)
    if command not in commands:
        print(f"{command_name}: unsupported compatibility command: {command}", file=sys.stderr)
        _print_help(command_name, commands, stream=sys.stderr)
        return 2
    if _is_app_command(source_app=source_app, command=command):
        return _dispatch_app_command(source_app=source_app, argv=args)
    print(f"{command_name}: command is listed but not routed: {command}", file=sys.stderr)
    return 2


def _print_help(
    command_name: str,
    commands: tuple[str, ...],
    *,
    stream: TextIO | None = None,
) -> None:
    output = stream or sys.stdout
    print(f"usage: {command_name} <command> [options]\n", file=output)
    print("Temporary Python compatibility shim for migration.", file=output)
    print("\ncommands:", file=output)
    for command in commands:
        print(f"  {command}", file=output)
    print(
        "\nKnown commands delegate to the Python app ports. import-legacy-state "
        "runs the migration bridge.",
        file=output,
    )


def _import_legacy_state(*, source_app: SourceApp, argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{_command_name_for_source(source_app)} import-legacy-state",
        description="Import read-only legacy state into the linkedin-tools compatibility store.",
    )
    parser.add_argument("--old-state-dir", type=Path)
    parser.add_argument("--target-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(list(argv))

    if source_app == "network":
        result = import_legacy_network_state(
            old_state_dir=args.old_state_dir,
            target_root=args.target_root,
        )
    elif source_app == "recruiter_agency":
        result = import_legacy_recruiter_agency_state(
            old_state_dir=args.old_state_dir,
            target_root=args.target_root,
        )
    else:
        result = import_legacy_opportunity_runs(
            old_state_dir=args.old_state_dir,
            target_root=args.target_root,
        )

    payload = result.to_json_dict()
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Imported {result.artifact_count} {result.source_app} legacy artifacts "
            f"into {result.database_path}"
        )
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


def _is_app_command(*, source_app: SourceApp, command: str) -> bool:
    if source_app == "network":
        return command in NETWORK_APP_COMMANDS
    if source_app == "recruiter_agency":
        return command in RECRUITER_AGENCY_APP_COMMANDS
    return command in OPPORTUNITY_APP_COMMANDS


def _dispatch_app_command(*, source_app: SourceApp, argv: Sequence[str]) -> int:
    normalized_argv = _normalize_app_argv(source_app=source_app, argv=argv)
    if source_app == "network":
        return network_main(normalized_argv)
    if source_app == "recruiter_agency":
        return recruiter_agency_main(normalized_argv)
    return opportunity_main(normalized_argv)


def _normalize_app_argv(*, source_app: SourceApp, argv: Sequence[str]) -> list[str]:
    args = list(argv)
    if source_app == "opportunity":
        return _strip_compat_target_root(args)
    if source_app not in {"network", "recruiter_agency"} or not args:
        return args

    normalized: list[str] = []
    command_and_args: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--state-dir" and index + 1 < len(args):
            normalized.extend([value, args[index + 1]])
            index += 2
            continue
        if value.startswith("--state-dir="):
            normalized.append(value)
            index += 1
            continue
        command_and_args.append(value)
        index += 1
    normalized.extend(command_and_args)
    return normalized


def _strip_compat_target_root(argv: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    args = list(argv)
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--target-root" and index + 1 < len(args):
            index += 2
            continue
        if value.startswith("--target-root="):
            index += 1
            continue
        normalized.append(value)
        index += 1
    return normalized


def _command_name_for_source(source_app: SourceApp) -> str:
    if source_app == "network":
        return "linkedin-network-run"
    if source_app == "recruiter_agency":
        return "recruiter-agency-outreach"
    return "linkedin-opportunity-intel"
