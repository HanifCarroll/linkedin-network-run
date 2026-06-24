"""CLI namespace for the network automation port."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from .browser import FixtureBrowserClient, UnavailableBrowserClient
from .models import CandidateStatus, DraftStrategy
from .old_state import inspect_old_state
from .reports import render_pending_report, render_report
from .service import (
    acceptance_draft_followups,
    acceptance_dry_run_followups,
    acceptance_export,
    acceptance_import,
    acceptance_report,
    acceptance_seed,
    acceptance_seed_history,
    acceptance_send_followup,
    acceptance_send_ready_followups,
    finish_run,
    import_audit,
    import_capture_path,
    needs_reaudit,
    pending_cleanup_finish,
    pending_cleanup_import_audit,
    pending_cleanup_import_capture,
    pending_cleanup_record_withdraw_result,
    pending_cleanup_start,
    pending_cleanup_withdraw_next,
    record_audit,
    record_candidate,
    record_send_result_from_path,
    record_top_up_result_from_path,
    reservoir_clear,
    reservoir_fill_run,
    reservoir_import_capture,
    resume_blocked,
    send_guarded,
    send_next,
    source_exhausted,
    start_run,
    tune_sources,
)
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-tools network",
        description="Durable controller for LinkedIn Sales Navigator networking runs.",
    )
    parser.add_argument("--state-dir", default=None, help="state directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--target", type=int, default=30)
    start.add_argument("--date", default=None)
    start.add_argument("--force", action="store_true")
    start.add_argument("--max-real-sends", type=int, default=None)

    audit = subparsers.add_parser("audit")
    audit.add_argument("people_count", type=int)
    audit.add_argument("--note", default=None)

    import_audit_parser = subparsers.add_parser("import-audit")
    import_audit_parser.add_argument("path")

    record = subparsers.add_parser("record")
    record.add_argument("--source", required=True)
    record.add_argument("--name", required=True)
    record.add_argument("--profile-url", default=None)
    record.add_argument(
        "--status", required=True, choices=[status.value for status in CandidateStatus]
    )
    record.add_argument("--note", default=None)

    record_send = subparsers.add_parser("record-send-result")
    record_send.add_argument("path")

    record_top_up = subparsers.add_parser("record-top-up-result")
    record_top_up.add_argument("path")
    record_top_up.add_argument("--note", default=None)

    for name in ("send-next", "send-guarded"):
        send = subparsers.add_parser(name)
        send.add_argument("--dry-run", action="store_true")
        send.add_argument("--allow-send", action="store_true")
        send.add_argument("--no-record", action="store_true")
        send.add_argument("--fixture-result", default=None)
        if name == "send-guarded":
            send.add_argument("--single-pass", action="store_true")
            send.add_argument("--max-attempts", type=int, default=30)

    source = subparsers.add_parser("source-exhausted")
    source.add_argument("--source", required=True)
    source.add_argument("--note", default=None)

    needs = subparsers.add_parser("needs-reaudit")
    needs.add_argument("--reason", required=True)

    resume = subparsers.add_parser("resume-blocked")
    resume.add_argument("--reason", required=True)

    import_capture = subparsers.add_parser("import-capture")
    import_capture.add_argument("path")
    import_capture.add_argument("--only-connectable", action="store_true")

    subparsers.add_parser("next")
    next_candidate = subparsers.add_parser("next-candidate")
    next_candidate.add_argument("--json", action="store_true")
    candidates = subparsers.add_parser("candidates")
    candidates.add_argument("--json", action="store_true")
    candidates.add_argument("--status", default=None)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--json", action="store_true")
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    subparsers.add_parser("report")
    finish = subparsers.add_parser("finish")
    finish.add_argument("--force", action="store_true")

    tune = subparsers.add_parser("tune-sources")
    tune.add_argument("--min-raw-rows", type=int, default=50)
    tune.add_argument("--max-connectable-yield", type=float, default=0.05)
    tune.add_argument("--apply", action="store_true")

    acceptance = subparsers.add_parser("acceptance")
    acceptance_sub = acceptance.add_subparsers(dest="acceptance_command", required=True)
    acceptance_seed_parser = acceptance_sub.add_parser("seed")
    acceptance_seed_parser.add_argument("--include-unfinished", action="store_true")
    acceptance_sub.add_parser("seed-history")
    acceptance_export_parser = acceptance_sub.add_parser("export")
    acceptance_export_parser.add_argument("--min-age-days", type=int, default=7)
    acceptance_export_parser.add_argument("--max-age-days", type=int, default=None)
    acceptance_export_parser.add_argument(
        "--out", default="/tmp/linkedin-acceptance-candidates.json"
    )
    acceptance_import_parser = acceptance_sub.add_parser("import")
    acceptance_import_parser.add_argument("path")
    acceptance_report_parser = acceptance_sub.add_parser("report")
    acceptance_report_parser.add_argument("--min-age-days", type=int, default=0)
    acceptance_report_parser.add_argument("--max-age-days", type=int, default=None)
    acceptance_report_parser.add_argument("--json", action="store_true")
    acceptance_draft = acceptance_sub.add_parser("draft-followups")
    acceptance_draft.add_argument("--research", default=None)
    acceptance_draft.add_argument("--out", default=None)
    acceptance_draft.add_argument("--include-drafted", action="store_true")
    acceptance_draft.add_argument("--strategy", default=DraftStrategy.ASAP_CONTRACT_V1.value)
    acceptance_send = acceptance_sub.add_parser("send-followup")
    acceptance_send.add_argument("--id", required=True)
    acceptance_send.add_argument("--dry-run", action="store_true")
    acceptance_send.add_argument("--preview-fill", action="store_true")
    acceptance_send.add_argument("--allow-send", action="store_true")
    acceptance_send.add_argument("--fixture-result", default=None)
    acceptance_dry = acceptance_sub.add_parser("dry-run-followups")
    acceptance_dry.add_argument("--limit", type=int, default=5)
    acceptance_dry.add_argument("--fixture-result", default=None)
    acceptance_ready = acceptance_sub.add_parser("send-ready-followups")
    acceptance_ready.add_argument("--limit", type=int, default=5)
    acceptance_ready.add_argument("--allow-send", action="store_true")
    acceptance_ready.add_argument("--fixture-result", default=None)

    reservoir = subparsers.add_parser("reservoir")
    reservoir_sub = reservoir.add_subparsers(dest="reservoir_command", required=True)
    reservoir_import = reservoir_sub.add_parser("import-capture")
    reservoir_import.add_argument("path")
    reservoir_import.add_argument("--only-connectable", action="store_true")
    reservoir_fill = reservoir_sub.add_parser("fill-run")
    reservoir_fill.add_argument("--source", default=None)
    reservoir_fill.add_argument("--limit", type=int, default=None)
    reservoir_report = reservoir_sub.add_parser("report")
    reservoir_report.add_argument("--json", action="store_true")
    reservoir_clear_parser = reservoir_sub.add_parser("clear")
    reservoir_clear_parser.add_argument("--source", default=None)

    pending = subparsers.add_parser("pending-cleanup")
    pending_sub = pending.add_subparsers(dest="pending_command", required=True)
    pending_start = pending_sub.add_parser("start")
    pending_start.add_argument("--max-withdrawals", type=int, default=75)
    pending_start.add_argument("--threshold-days", type=int, default=0)
    pending_start.add_argument("--threshold-weeks", type=int, default=2)
    pending_start.add_argument("--threshold-months", type=int, default=0)
    pending_start.add_argument("--force", action="store_true")
    pending_import_audit = pending_sub.add_parser("import-audit")
    pending_import_audit.add_argument("path")
    pending_import_capture = pending_sub.add_parser("import-capture")
    pending_import_capture.add_argument("path")
    pending_plan = pending_sub.add_parser("plan")
    pending_plan.add_argument("--json", action="store_true")
    pending_next = pending_sub.add_parser("next")
    pending_next.add_argument("--json", action="store_true")
    pending_record = pending_sub.add_parser("record-withdraw-result")
    pending_record.add_argument("path")
    pending_withdraw = pending_sub.add_parser("withdraw-next")
    pending_withdraw.add_argument("--dry-run", action="store_true")
    pending_withdraw.add_argument("--allow-withdraw", action="store_true")
    pending_withdraw.add_argument("--no-record", action="store_true")
    pending_withdraw.add_argument("--fixture-result", default=None)
    pending_status = pending_sub.add_parser("status")
    pending_status.add_argument("--json", action="store_true")
    pending_sub.add_parser("report")
    pending_finish = pending_sub.add_parser("finish")
    pending_finish.add_argument("--force", action="store_true")

    old_state = subparsers.add_parser("old-state")
    old_state_sub = old_state.add_subparsers(dest="old_state_command", required=True)
    old_inspect = old_state_sub.add_parser("inspect")
    old_inspect.add_argument("--old-state-dir", default=None)
    old_inspect.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = Store(args.state_dir)
    try:
        output = dispatch(args, store)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if output is not None:
        print(output)
    return 0


def dispatch(args: argparse.Namespace, store: Store) -> str | None:
    command = str(args.command)
    if command == "start":
        from datetime import date

        parsed_date = date.fromisoformat(args.date) if args.date else None
        return start_run(
            store,
            target=args.target,
            run_date=parsed_date,
            force=args.force,
            max_real_sends=args.max_real_sends,
        )
    if command == "audit":
        return record_audit(store, args.people_count, args.note)
    if command == "import-audit":
        return import_audit(store, Path(args.path))
    if command == "record":
        return record_candidate(
            store,
            source=args.source,
            name=args.name,
            profile_url=args.profile_url,
            status=CandidateStatus(args.status),
            note=args.note,
        )
    if command == "record-send-result":
        return record_send_result_from_path(store, Path(args.path))
    if command == "record-top-up-result":
        return record_top_up_result_from_path(store, Path(args.path), args.note)
    if command == "send-next":
        return send_next(
            store,
            browser_from_args(args, send=True),
            dry_run=args.dry_run,
            allow_send=args.allow_send,
            no_record=args.no_record,
        )
    if command == "send-guarded":
        return send_guarded(
            store,
            browser_from_args(args, send=True),
            dry_run=args.dry_run,
            allow_send=args.allow_send,
            max_attempts=args.max_attempts,
            single_pass=args.single_pass,
            no_record=args.no_record,
        )
    if command == "source-exhausted":
        return source_exhausted(store, args.source, args.note)
    if command == "needs-reaudit":
        return needs_reaudit(store, args.reason)
    if command == "resume-blocked":
        return resume_blocked(store, args.reason)
    if command == "import-capture":
        return import_capture_path(store, Path(args.path), args.only_connectable)
    if command == "next":
        return json_model_or_text(store.load_run().next_source())
    if command == "next-candidate":
        return json_model_or_text(
            store.load_run().next_connectable_observation(), as_json=args.json
        )
    if command == "candidates":
        observations = [
            observation
            for observation in store.load_run().observations
            if args.status is None or observation.menu_state == args.status
        ]
        return json.dumps(
            [observation.model_dump(mode="json") for observation in observations], indent=2
        )
    if command == "plan":
        plan = store.load_run().operator_plan_with_reservoir(store.load_reservoir())
        return json_model_or_text(plan, as_json=args.json)
    if command == "status":
        run = store.load_run()
        return json_model_or_text(run, as_json=args.json)
    if command == "report":
        return render_report(store.load_run())
    if command == "finish":
        return finish_run(store, force=args.force)
    if command == "tune-sources":
        return tune_sources(
            store,
            min_raw_rows=args.min_raw_rows,
            max_connectable_yield=args.max_connectable_yield,
            apply=args.apply,
        )
    if command == "acceptance":
        return dispatch_acceptance(args, store)
    if command == "reservoir":
        return dispatch_reservoir(args, store)
    if command == "pending-cleanup":
        return dispatch_pending(args, store)
    if command == "old-state":
        return dispatch_old_state(args)
    raise RuntimeError(f"unhandled command {command}")


def dispatch_acceptance(args: argparse.Namespace, store: Store) -> str:
    command = str(args.acceptance_command)
    if command == "seed":
        return acceptance_seed(store, include_unfinished=args.include_unfinished)
    if command == "seed-history":
        return acceptance_seed_history(store)
    if command == "export":
        return acceptance_export(
            store,
            min_age_days=args.min_age_days,
            max_age_days=args.max_age_days,
            out=Path(args.out),
        )
    if command == "import":
        return acceptance_import(store, Path(args.path))
    if command == "report":
        return acceptance_report(
            store,
            min_age_days=args.min_age_days,
            max_age_days=args.max_age_days,
            as_json=args.json,
        )
    if command == "draft-followups":
        return acceptance_draft_followups(
            store,
            research=Path(args.research) if args.research else None,
            out=Path(args.out) if args.out else None,
            include_drafted=args.include_drafted,
            strategy=DraftStrategy(args.strategy),
        )
    if command == "send-followup":
        return acceptance_send_followup(
            store,
            browser_from_args(args, followup=True),
            record_id=args.id,
            dry_run=args.dry_run,
            preview_fill=args.preview_fill,
            allow_send=args.allow_send,
        )
    if command == "dry-run-followups":
        return acceptance_dry_run_followups(
            store, browser_from_args(args, followup=True), limit=args.limit
        )
    if command == "send-ready-followups":
        return acceptance_send_ready_followups(
            store,
            browser_from_args(args, followup=True),
            limit=args.limit,
            allow_send=args.allow_send,
        )
    raise RuntimeError(f"unhandled acceptance command {command}")


def dispatch_reservoir(args: argparse.Namespace, store: Store) -> str:
    command = str(args.reservoir_command)
    if command == "import-capture":
        return reservoir_import_capture(store, Path(args.path), args.only_connectable)
    if command == "fill-run":
        return reservoir_fill_run(store, source=args.source, limit=args.limit)
    if command == "report":
        reservoir = store.load_reservoir()
        if args.json:
            return json.dumps(reservoir.model_dump(mode="json"), indent=2)
        by_source: dict[str, int] = {}
        for observation in reservoir.observations:
            by_source[observation.source] = by_source.get(observation.source, 0) + 1
        lines = [
            "# LinkedIn Candidate Reservoir",
            f"- Total candidates: {len(reservoir.observations)}",
            f"- Updated at: {reservoir.updated_at}",
            "",
            "## Source Counts",
        ]
        lines.extend(f"- {source}: {by_source[source]}" for source in sorted(by_source))
        return "\n".join(lines)
    if command == "clear":
        return reservoir_clear(store, args.source)
    raise RuntimeError(f"unhandled reservoir command {command}")


def dispatch_pending(args: argparse.Namespace, store: Store) -> str:
    command = str(args.pending_command)
    if command == "start":
        threshold_days = args.threshold_days
        if threshold_days == 0:
            threshold_days = (
                args.threshold_months * 30 if args.threshold_months else args.threshold_weeks * 7
            )
        return pending_cleanup_start(
            store,
            max_withdrawals=args.max_withdrawals,
            threshold_days=threshold_days,
            threshold_months=args.threshold_months,
            force=args.force,
        )
    if command == "import-audit":
        return pending_cleanup_import_audit(store, Path(args.path))
    if command == "import-capture":
        return pending_cleanup_import_capture(store, Path(args.path))
    if command == "plan":
        return json_model_or_text(store.load_pending().operator_plan(), as_json=args.json)
    if command == "next":
        return json_model_or_text(
            store.load_pending().next_eligible_observation(), as_json=args.json
        )
    if command == "record-withdraw-result":
        return pending_cleanup_record_withdraw_result(store, Path(args.path))
    if command == "withdraw-next":
        return pending_cleanup_withdraw_next(
            store,
            browser_from_args(args, withdraw=True),
            dry_run=args.dry_run,
            allow_withdraw=args.allow_withdraw,
            no_record=args.no_record,
        )
    if command == "status":
        return json_model_or_text(store.load_pending(), as_json=args.json)
    if command == "report":
        return render_pending_report(store.load_pending())
    if command == "finish":
        return pending_cleanup_finish(store, force=args.force)
    raise RuntimeError(f"unhandled pending-cleanup command {command}")


def dispatch_old_state(args: argparse.Namespace) -> str:
    command = str(args.old_state_command)
    if command == "inspect":
        snapshot = inspect_old_state(Path(args.old_state_dir) if args.old_state_dir else None)
        if args.json:
            return json.dumps(snapshot.model_dump(mode="json"), indent=2)
        return "\n".join(
            [
                f"old state dir: {snapshot.state_dir}",
                f"active run: {'yes' if snapshot.active_run else 'no'}",
                f"acceptance ledger: {'yes' if snapshot.acceptance_ledger else 'no'}",
                f"acceptance followups: {'yes' if snapshot.acceptance_followups else 'no'}",
                f"reservoir: {'yes' if snapshot.reservoir else 'no'}",
                "warnings: " + (", ".join(snapshot.warnings) if snapshot.warnings else "none"),
            ]
        )
    raise RuntimeError(f"unhandled old-state command {command}")


def browser_from_args(
    args: argparse.Namespace,
    *,
    send: bool = False,
    followup: bool = False,
    withdraw: bool = False,
) -> FixtureBrowserClient | UnavailableBrowserClient:
    fixture = getattr(args, "fixture_result", None)
    if fixture:
        path = Path(fixture)
        return FixtureBrowserClient(
            send_result=path if send else None,
            followup_result=path if followup else None,
            withdraw_result=path if withdraw else None,
        )
    return UnavailableBrowserClient()


def json_model_or_text(value: BaseModel | object | None, *, as_json: bool = True) -> str:
    if value is None:
        return "null" if as_json else ""
    if isinstance(value, BaseModel):
        if as_json:
            return json.dumps(value.model_dump(mode="json"), indent=2)
        return "\n".join(f"{key}: {item}" for key, item in value.model_dump(mode="json").items())
    return json.dumps(value, indent=2) if as_json else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
