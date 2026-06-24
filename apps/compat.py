"""Temporary compatibility command entrypoints during migration."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from packages.linkedin_common.paths import DEFAULT_STATE_ROOT
from packages.linkedin_storage.migrations import (
    SourceApp,
    import_legacy_network_state,
    import_legacy_opportunity_runs,
    import_legacy_recruiter_agency_state,
    latest_import_summary,
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

REAL_SEND_FLAGS = frozenset({"--allow-send", "--allow-withdraw"})


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
    if _has_real_action_flag(command_args):
        print(
            f"{command_name} {command}: real send/withdraw flags are blocked in the "
            "temporary Python compatibility shim until the owning app port lands.",
            file=sys.stderr,
        )
        return 2
    if command in {"status", "plan", "dashboard", "report", "last-run", "queue"}:
        return _status_placeholder(
            command_name=command_name,
            command=command,
            source_app=source_app,
            argv=command_args,
        )
    return _no_send_placeholder(
        command_name=command_name,
        command=command,
        source_app=source_app,
        argv=command_args,
    )


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
        "\nReal send and withdraw flags are blocked until the owning Python app port lands.",
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


def _status_placeholder(
    *,
    command_name: str,
    command: str,
    source_app: SourceApp,
    argv: Sequence[str],
) -> int:
    target_root = _target_root_from_args(argv)
    payload = _placeholder_payload(
        command_name=command_name,
        command=command,
        source_app=source_app,
        target_root=target_root,
    )
    if "--json" in argv:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        latest = payload["latest_legacy_import"]
        print(f"{command_name} {command}: Python port behavior is not wired yet.")
        if latest is None:
            print(f"No legacy import found under {target_root}.")
        else:
            print(f"Latest legacy import: {latest}")
    return 0


def _no_send_placeholder(
    *,
    command_name: str,
    command: str,
    source_app: SourceApp,
    argv: Sequence[str],
) -> int:
    target_root = _target_root_from_args(argv)
    payload = _placeholder_payload(
        command_name=command_name,
        command=command,
        source_app=source_app,
        target_root=target_root,
    )
    payload["dry_run"] = True
    payload["result"] = "no_send_placeholder"
    if "--json" in argv:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"{command_name} {command}: no-send compatibility placeholder; "
            "owning app behavior is pending."
        )
    return 0


def _placeholder_payload(
    *,
    command_name: str,
    command: str,
    source_app: SourceApp,
    target_root: Path,
) -> dict[str, object]:
    return {
        "command_name": command_name,
        "command": command,
        "source_app": source_app,
        "compatibility_shim": True,
        "status": "not_ported",
        "target_root": str(target_root),
        "latest_legacy_import": latest_import_summary(
            source_app=source_app,
            target_root=target_root,
        ),
        "parity_gap": "Python app behavior is owned by a separate porting workstream.",
    }


def _target_root_from_args(argv: Sequence[str]) -> Path:
    args = list(argv)
    for index, value in enumerate(args):
        if value == "--target-root" and index + 1 < len(args):
            return Path(args[index + 1])
        if value.startswith("--target-root="):
            return Path(value.split("=", 1)[1])
    return DEFAULT_STATE_ROOT


def _has_real_action_flag(argv: Sequence[str]) -> bool:
    return any(arg in REAL_SEND_FLAGS for arg in argv)


def _command_name_for_source(source_app: SourceApp) -> str:
    if source_app == "network":
        return "linkedin-network-run"
    if source_app == "recruiter_agency":
        return "recruiter-agency-outreach"
    return "linkedin-opportunity-intel"
