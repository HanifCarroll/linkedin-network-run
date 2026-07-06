"""CLI namespace for the network automation port."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from .browser import (
    DEFAULT_AUDIT_OUT_DIR,
    DEFAULT_CAPTURE_OUT_DIR,
    DEFAULT_SEND_OUT_DIR,
    DEFAULT_WITHDRAW_OUT_DIR,
    BrowserClient,
    FixtureBrowserClient,
    PlaywriterBrowserClient,
)
from .cli_acceptance import dispatch_acceptance, register_acceptance_commands
from .models import CandidateStatus
from .old_state import inspect_old_state
from .reports import render_pending_report, render_report, render_send_summary
from .service import (
    SENT_INVITATION_AUDIT_LOAD_MORE,
    apply_lead_review_decisions,
    capture_saved_searches,
    capture_source,
    drain_stale_candidates,
    finish_run,
    import_audit,
    import_capture_path,
    needs_reaudit,
    network_run_session,
    network_sends_summary,
    network_state_db_status,
    network_state_migrate_sqlite,
    pending_cleanup_audit,
    pending_cleanup_capture,
    pending_cleanup_finish,
    pending_cleanup_import_audit,
    pending_cleanup_import_capture,
    pending_cleanup_record_withdraw_result,
    pending_cleanup_run_session,
    pending_cleanup_start,
    pending_cleanup_withdraw_next,
    reconcile_audit,
    record_audit,
    record_candidate,
    record_send_result_from_path,
    record_top_up_result_from_path,
    reservoir_capture,
    reservoir_clear,
    reservoir_fill_run,
    reservoir_import_capture,
    reset_source_progress,
    resume_blocked,
    retry_failed_lead,
    review_candidates,
    send_guarded,
    send_next,
    set_lead_public_profile_url,
    source_exhausted,
    start_run,
    top_up_reconcile,
    tune_sources,
)
from .store import Store

DEFAULT_RESERVOIR_CAPTURE_OUT_DIR = Path("/tmp/linkedin-network-run-reservoir-capture")
DEFAULT_SAVED_SEARCHES = Path("/tmp/linkedin-network-run-saved-searches.json")
DEFAULT_SAVED_SEARCHES_URL = "https://www.linkedin.com/sales/search/people"
DEFAULT_NETWORK_SESSION_OUT_DIR = Path("/tmp/linkedin-network-session")
DEFAULT_LEAD_REVIEW = DEFAULT_NETWORK_SESSION_OUT_DIR / "lead-review-candidates.json"
DEFAULT_PENDING_CAPTURE = Path("/tmp/linkedin-pending-cleanup-capture.json")
DEFAULT_PENDING_SESSION_OUT_DIR = Path("/tmp/linkedin-pending-cleanup-session")
BACKEND_HELP = """browser backend:
  Playwriter only
  Playwriter session: set LINKEDIN_TOOLS_PLAYWRITER_SESSION=<id>, or let the CLI create one
  Playwriter browser: set LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY=<key> before session creation
"""


def _emit_progress(message: str) -> None:
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-tools network",
        description="Durable controller for LinkedIn Sales Navigator networking runs.",
        epilog=BACKEND_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--state-dir", default=None, help="state directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--target", type=int, default=30)
    start.add_argument("--per-source-target", type=int, default=None)
    start.add_argument("--date", default=None)
    start.add_argument("--force", action="store_true")
    start.add_argument("--max-real-sends", type=int, default=None)
    start.add_argument("--no-fallback", action="store_true")
    start.add_argument(
        "--source",
        action="append",
        default=None,
        help="explicit source name to target; repeat with --per-source-target",
    )

    run_session = subparsers.add_parser("run-session")
    run_session.add_argument(
        "--session",
        default="auto",
        help=(
            "logical automation session name; with the default Playwriter backend, "
            "pass a Playwriter session id here or set LINKEDIN_TOOLS_PLAYWRITER_SESSION"
        ),
    )
    run_session.add_argument("--target", type=int, default=10)
    run_session.add_argument("--per-source-target", type=int, default=None)
    run_session.add_argument("--max-real-sends", type=int, default=None)
    run_session.add_argument("--force", action="store_true")
    run_session.add_argument("--resume", action="store_true")
    run_session.add_argument("--no-fallback", action="store_true")
    run_session.add_argument("--saved-searches-url", default=DEFAULT_SAVED_SEARCHES_URL)
    run_session.add_argument("--saved-searches", default=str(DEFAULT_SAVED_SEARCHES))
    run_session.add_argument(
        "--refresh-saved-searches",
        action="store_true",
        help="recapture the Sales Navigator saved-search index before resolving sources",
    )
    run_session.add_argument("--out-dir", default=str(DEFAULT_NETWORK_SESSION_OUT_DIR))
    run_session.add_argument("--review-out", default=None)
    run_session.add_argument("--allow-send", action="store_true")
    run_session.add_argument("--audit-attempts", type=int, default=3)
    run_session.add_argument("--audit-delay-ms", type=int, default=5000)
    run_session.add_argument(
        "--audit-load-more", type=int, default=SENT_INVITATION_AUDIT_LOAD_MORE
    )
    run_session.add_argument("--confirm-delay-ms", type=int, default=5000)
    run_session.add_argument(
        "--confirm-out-dir", default="/tmp/linkedin-network-run-confirm-send"
    )
    run_session.add_argument("--max-steps", type=int, default=100)
    run_session.add_argument("--finish", action="store_true")
    run_session.add_argument("--fixture-result", default=None)
    run_session.add_argument(
        "--source",
        action="append",
        default=None,
        help="explicit source name to target on a new run; repeat with --per-source-target",
    )

    audit = subparsers.add_parser("audit")
    audit.add_argument("people_count", type=int)
    audit.add_argument("--note", default=None)

    import_audit_parser = subparsers.add_parser("import-audit")
    import_audit_parser.add_argument("path")

    saved_searches = subparsers.add_parser("saved-searches")
    saved_searches.add_argument("--session", default="auto")
    saved_searches.add_argument("--url", default=DEFAULT_SAVED_SEARCHES_URL)
    saved_searches.add_argument("--out", default=str(DEFAULT_SAVED_SEARCHES))
    saved_searches.add_argument("--fixture-result", default=None)

    reconcile = subparsers.add_parser("reconcile-audit")
    reconcile.add_argument("--session", default="auto")
    reconcile.add_argument("--attempts", type=int, default=3)
    reconcile.add_argument("--delay-ms", type=int, default=5000)
    reconcile.add_argument(
        "--load-more", type=int, default=SENT_INVITATION_AUDIT_LOAD_MORE
    )
    reconcile.add_argument("--finish", action="store_true")
    reconcile.add_argument("--out-dir", default=str(DEFAULT_AUDIT_OUT_DIR))
    reconcile.add_argument("--fixture-result", default=None)

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

    drain_stale = subparsers.add_parser("drain-stale-candidates")
    drain_stale.add_argument("--source", default=None)

    for name in ("send-next", "send-guarded"):
        send = subparsers.add_parser(name)
        send.add_argument("--session", default="auto")
        send.add_argument("--dry-run", action="store_true")
        send.add_argument("--allow-send", action="store_true")
        send.add_argument("--no-record", action="store_true")
        send.add_argument("--confirm-delay-ms", type=int, default=5000)
        send.add_argument(
            "--confirm-out-dir", default="/tmp/linkedin-network-run-confirm-send"
        )
        send.add_argument("--fixture-result", default=None)
        send.add_argument(
            "--out-dir",
            default=str(
                DEFAULT_SEND_OUT_DIR
                if name == "send-next"
                else Path("/tmp/linkedin-network-run-send-guarded")
            ),
        )
        if name == "send-guarded":
            send.add_argument("--single-pass", action="store_true")
            send.add_argument("--max-attempts", type=int, default=30)

    top_up = subparsers.add_parser("top-up-reconcile")
    top_up.add_argument("--session", default="auto")
    top_up.add_argument("--out-dir", default="/tmp/linkedin-network-run-top-up-reconcile")
    top_up.add_argument("--max-attempts", type=int, default=20)
    top_up.add_argument("--delay-ms", type=int, default=1000)
    top_up.add_argument("--allow-send", action="store_true")
    top_up.add_argument("--finish", action="store_true")
    top_up.add_argument("--fallback-source", default="FO - Founders - Urgent")
    top_up.add_argument("--fallback-url", default=None)
    top_up.add_argument("--saved-searches", default=str(DEFAULT_SAVED_SEARCHES))
    top_up.add_argument("--fallback-pages", type=int, default=5)
    top_up.add_argument("--fallback-stop-after", type=int, default=10)
    top_up.add_argument("--fallback-limit", type=int, default=18)
    top_up.add_argument("--fallback-row-scroll-delay-ms", type=int, default=250)
    top_up.add_argument("--no-fallback-capture", action="store_true")
    top_up.add_argument("--fixture-send-result", default=None)
    top_up.add_argument("--fixture-audit-result", default=None)
    top_up.add_argument("--fixture-capture-result", default=None)

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

    capture = subparsers.add_parser("capture")
    capture.add_argument("--session", default="auto")
    capture.add_argument("--source", default=None)
    capture.add_argument("--url", default=None)
    capture.add_argument("--saved-searches", default=str(DEFAULT_SAVED_SEARCHES))
    capture.add_argument("--pages", type=int, default=5)
    capture.add_argument("--limit", type=int, default=18)
    capture.add_argument("--stop-after-connectable", type=int, default=10)
    capture.add_argument("--row-scroll-delay-ms", type=int, default=250)
    capture.add_argument("--only-connectable", action="store_true")
    capture.add_argument("--out-dir", default=str(DEFAULT_CAPTURE_OUT_DIR))
    capture.add_argument("--fixture-result", default=None)

    review = subparsers.add_parser("review-candidates")
    review.add_argument("--source", default=None)
    review.add_argument("--out", default=str(DEFAULT_LEAD_REVIEW))
    review.add_argument("--json", action="store_true")

    apply_review = subparsers.add_parser("apply-lead-decisions")
    apply_review.add_argument("path")
    set_public_profile = subparsers.add_parser("set-public-profile-url")
    set_public_profile.add_argument("--lead-key", required=True)
    set_public_profile.add_argument("--url", required=True)
    retry_failed = subparsers.add_parser("retry-failed-lead")
    retry_failed.add_argument("--lead-key", required=True)
    retry_failed.add_argument("--reason", default=None)
    reset_progress = subparsers.add_parser("reset-source-progress")
    reset_progress.add_argument("--source", action="append", required=True)

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
    sends = subparsers.add_parser("sends")
    sends.add_argument("--date", default="today")
    sends.add_argument("--timezone", default="local")
    sends.add_argument("--sync-history", action="store_true")
    sends.add_argument("--json", action="store_true")
    state = subparsers.add_parser("state")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_status = state_sub.add_parser("db-status")
    state_status.add_argument("--json", action="store_true")
    state_migrate = state_sub.add_parser("migrate-sqlite")
    mode = state_migrate.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    state_migrate.add_argument("--json", action="store_true")
    subparsers.add_parser("report")
    finish = subparsers.add_parser("finish")
    finish.add_argument("--force", action="store_true")

    tune = subparsers.add_parser("tune-sources")
    tune.add_argument("--min-raw-rows", type=int, default=50)
    tune.add_argument("--max-connectable-yield", type=float, default=0.05)
    tune.add_argument("--apply", action="store_true")

    register_acceptance_commands(subparsers)

    reservoir = subparsers.add_parser("reservoir")
    reservoir_sub = reservoir.add_subparsers(dest="reservoir_command", required=True)
    reservoir_capture_parser = reservoir_sub.add_parser("capture")
    reservoir_capture_parser.add_argument("--session", default="auto")
    reservoir_capture_parser.add_argument("--source", required=True)
    reservoir_capture_parser.add_argument("--url", default=None)
    reservoir_capture_parser.add_argument("--saved-searches", default=str(DEFAULT_SAVED_SEARCHES))
    reservoir_capture_parser.add_argument("--pages", type=int, default=5)
    reservoir_capture_parser.add_argument("--limit", type=int, default=18)
    reservoir_capture_parser.add_argument("--stop-after-connectable", type=int, default=10)
    reservoir_capture_parser.add_argument("--row-scroll-delay-ms", type=int, default=250)
    reservoir_capture_parser.add_argument("--only-connectable", action="store_true")
    reservoir_capture_parser.add_argument(
        "--out-dir", default=str(DEFAULT_RESERVOIR_CAPTURE_OUT_DIR)
    )
    reservoir_capture_parser.add_argument("--fixture-result", default=None)
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
    pending_audit = pending_sub.add_parser("audit")
    pending_audit.add_argument("--session", default="auto")
    pending_audit.add_argument("--load-more", type=int, default=0)
    pending_audit.add_argument("--out-dir", default=str(DEFAULT_AUDIT_OUT_DIR))
    pending_audit.add_argument("--fixture-result", default=None)
    pending_import_audit = pending_sub.add_parser("import-audit")
    pending_import_audit.add_argument("path")
    pending_import_capture = pending_sub.add_parser("import-capture")
    pending_import_capture.add_argument("path")
    pending_capture = pending_sub.add_parser("capture")
    pending_capture.add_argument("--session", default="auto")
    pending_capture.add_argument("--load-more", type=int, default=0)
    pending_capture.add_argument("--threshold-days", type=int, default=0)
    pending_capture.add_argument("--threshold-weeks", type=int, default=2)
    pending_capture.add_argument("--threshold-months", type=int, default=0)
    pending_capture.add_argument("--out", default=str(DEFAULT_PENDING_CAPTURE))
    pending_capture.add_argument("--fixture-result", default=None)
    pending_plan = pending_sub.add_parser("plan")
    pending_plan.add_argument("--json", action="store_true")
    pending_next = pending_sub.add_parser("next")
    pending_next.add_argument("--json", action="store_true")
    pending_record = pending_sub.add_parser("record-withdraw-result")
    pending_record.add_argument("path")
    pending_withdraw = pending_sub.add_parser("withdraw-next")
    pending_withdraw.add_argument("--session", default="auto")
    pending_withdraw.add_argument("--dry-run", action="store_true")
    pending_withdraw.add_argument("--allow-withdraw", action="store_true")
    pending_withdraw.add_argument("--no-record", action="store_true")
    pending_withdraw.add_argument("--fixture-result", default=None)
    pending_withdraw.add_argument("--out-dir", default=str(DEFAULT_WITHDRAW_OUT_DIR))
    pending_withdraw.add_argument("--max-load-more", type=int, default=260)
    pending_withdraw.add_argument("--withdraw-timeout-seconds", type=float, default=90.0)
    pending_session = pending_sub.add_parser("run-session")
    pending_session.add_argument("--session", default="auto")
    pending_session.add_argument("--audit-load-more", type=int, default=0)
    pending_session.add_argument("--capture-load-more", type=int, default=40)
    pending_session.add_argument("--threshold-days", type=int, default=0)
    pending_session.add_argument("--threshold-weeks", type=int, default=2)
    pending_session.add_argument("--threshold-months", type=int, default=0)
    pending_session.add_argument("--out", default=str(DEFAULT_PENDING_CAPTURE))
    pending_session.add_argument("--out-dir", default=str(DEFAULT_PENDING_SESSION_OUT_DIR))
    pending_session.add_argument("--withdraw-limit", type=int, default=1)
    pending_session.add_argument("--allow-withdraw", action="store_true")
    pending_session.add_argument("--skip-dry-run", action="store_true")
    pending_session.add_argument("--finish", action="store_true")
    pending_session.add_argument("--fixture-result", default=None)
    pending_session.add_argument("--max-load-more", type=int, default=260)
    pending_session.add_argument("--withdraw-timeout-seconds", type=float, default=90.0)
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
            per_source_target=args.per_source_target,
            allow_fallback_sources=not args.no_fallback,
            source_names=args.source,
        )
    if command == "run-session":
        browser = browser_from_args(args, saved_searches=True, capture=True, send=True, audit=True)
        try:
            network_run_session(
                store,
                browser,
                target=args.target,
                max_real_sends=args.max_real_sends,
                force=args.force,
                resume=args.resume,
                per_source_target=args.per_source_target,
                allow_fallback_sources=not args.no_fallback,
                saved_searches_url=args.saved_searches_url,
                saved_searches_out=Path(args.saved_searches),
                refresh_saved_searches=args.refresh_saved_searches,
                audit_attempts=args.audit_attempts,
                audit_delay_ms=args.audit_delay_ms,
                audit_load_more=args.audit_load_more,
                allow_send=args.allow_send,
                max_steps=args.max_steps,
                finish=args.finish,
                confirm_delay_ms=args.confirm_delay_ms,
                confirm_out_dir=Path(args.confirm_out_dir),
                review_out=(
                    Path(args.review_out)
                    if args.review_out
                    else Path(args.out_dir) / "lead-review-candidates.json"
                ),
                source_names=args.source,
                emit=_emit_progress,
            )
            return None
        finally:
            close = getattr(browser, "close", None)
            if callable(close):
                close()
    if command == "audit":
        return record_audit(store, args.people_count, args.note)
    if command == "import-audit":
        return import_audit(store, Path(args.path))
    if command == "saved-searches":
        return capture_saved_searches(
            browser_from_args(args, saved_searches=True),
            url=args.url,
            out=Path(args.out),
        )
    if command == "reconcile-audit":
        return reconcile_audit(
            store,
            browser_from_args(args, audit=True),
            attempts=args.attempts,
            delay_ms=args.delay_ms,
            load_more=args.load_more,
            finish=args.finish,
        )
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
    if command == "drain-stale-candidates":
        return drain_stale_candidates(store, args.source)
    if command == "send-next":
        send_next(
            store,
            browser_from_args(args, send=True),
            dry_run=args.dry_run,
            allow_send=args.allow_send,
            no_record=args.no_record,
            confirm_delay_ms=args.confirm_delay_ms,
            confirm_out_dir=Path(args.confirm_out_dir),
            emit=_emit_progress,
        )
        return None
    if command == "send-guarded":
        send_guarded(
            store,
            browser_from_args(args, send=True),
            dry_run=args.dry_run,
            allow_send=args.allow_send,
            max_attempts=args.max_attempts,
            single_pass=args.single_pass,
            no_record=args.no_record,
            confirm_delay_ms=args.confirm_delay_ms,
            confirm_out_dir=Path(args.confirm_out_dir),
            emit=_emit_progress,
        )
        return None
    if command == "top-up-reconcile":
        return top_up_reconcile(
            store,
            browser_from_args(args, send=True, capture=True, audit=True),
            max_attempts=args.max_attempts,
            delay_ms=args.delay_ms,
            allow_send=args.allow_send,
            finish=args.finish,
            fallback_source=args.fallback_source,
            fallback_url=args.fallback_url,
            saved_searches=Path(args.saved_searches) if args.saved_searches else None,
            fallback_pages=args.fallback_pages,
            fallback_stop_after_connectable=args.fallback_stop_after,
            fallback_limit=args.fallback_limit,
            fallback_row_scroll_delay_ms=args.fallback_row_scroll_delay_ms,
            no_fallback_capture=args.no_fallback_capture,
        )
    if command == "source-exhausted":
        return source_exhausted(store, args.source, args.note)
    if command == "needs-reaudit":
        return needs_reaudit(store, args.reason)
    if command == "resume-blocked":
        return resume_blocked(store, args.reason)
    if command == "import-capture":
        return import_capture_path(store, Path(args.path), args.only_connectable)
    if command == "capture":
        return capture_source(
            store,
            browser_from_args(args, capture=True),
            source=args.source,
            url=args.url,
            saved_searches=Path(args.saved_searches) if args.saved_searches else None,
            pages=args.pages,
            limit=args.limit,
            stop_after_connectable=args.stop_after_connectable,
            only_connectable=args.only_connectable,
            row_scroll_delay_ms=args.row_scroll_delay_ms,
        )
    if command == "review-candidates":
        return review_candidates(store, source=args.source, out=Path(args.out), as_json=args.json)
    if command == "apply-lead-decisions":
        return apply_lead_review_decisions(store, Path(args.path))
    if command == "set-public-profile-url":
        return set_lead_public_profile_url(store, args.lead_key, args.url)
    if command == "retry-failed-lead":
        return retry_failed_lead(store, args.lead_key, args.reason)
    if command == "reset-source-progress":
        return reset_source_progress(store, args.source)
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
    if command == "sends":
        summary = network_sends_summary(
            store,
            date_arg=args.date,
            timezone_name=args.timezone,
            sync_history=args.sync_history,
        )
        if args.json:
            return json_model_or_text(summary, as_json=True)
        return render_send_summary(summary)
    if command == "state":
        return dispatch_state(args, store)
    if command == "report":
        run = store.load_run()
        summary = network_sends_summary(
            store,
            date_arg=run.date.isoformat(),
            timezone_name="local",
        )
        return render_report(run, send_summary=summary)
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
        return dispatch_acceptance(args, store, browser_from_args=browser_from_args)
    if command == "reservoir":
        return dispatch_reservoir(args, store)
    if command == "pending-cleanup":
        return dispatch_pending(args, store)
    if command == "old-state":
        return dispatch_old_state(args)
    raise RuntimeError(f"unhandled command {command}")


def dispatch_state(args: argparse.Namespace, store: Store) -> str:
    command = str(args.state_command)
    if command == "db-status":
        return network_state_db_status(store, as_json=args.json)
    if command == "migrate-sqlite":
        return network_state_migrate_sqlite(store, apply=args.apply, as_json=args.json)
    raise RuntimeError(f"unhandled state command {command}")


def dispatch_reservoir(args: argparse.Namespace, store: Store) -> str:
    command = str(args.reservoir_command)
    if command == "capture":
        return reservoir_capture(
            store,
            browser_from_args(args, capture=True),
            source=args.source,
            url=args.url,
            saved_searches=Path(args.saved_searches) if args.saved_searches else None,
            pages=args.pages,
            limit=args.limit,
            stop_after_connectable=args.stop_after_connectable,
            only_connectable=args.only_connectable,
            row_scroll_delay_ms=args.row_scroll_delay_ms,
        )
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
    if command == "audit":
        return pending_cleanup_audit(
            store,
            browser_from_args(args, audit=True),
            load_more=args.load_more,
        )
    if command == "import-audit":
        return pending_cleanup_import_audit(store, Path(args.path))
    if command == "import-capture":
        return pending_cleanup_import_capture(store, Path(args.path))
    if command == "capture":
        threshold_days = pending_threshold_days(args)
        return pending_cleanup_capture(
            store,
            browser_from_args(args, pending_capture=True),
            load_more=args.load_more,
            threshold_days=threshold_days,
            out=Path(args.out),
        )
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
    if command == "run-session":
        threshold_days = pending_threshold_days(args)
        browser = browser_from_args(args, audit=True, pending_capture=True, withdraw=True)
        try:
            return pending_cleanup_run_session(
                store,
                browser,
                audit_load_more=args.audit_load_more,
                capture_load_more=args.capture_load_more,
                threshold_days=threshold_days,
                capture_out=Path(args.out),
                withdraw_limit=args.withdraw_limit,
                allow_withdraw=args.allow_withdraw,
                dry_run_first=not args.skip_dry_run,
                finish=args.finish,
            )
        finally:
            close = getattr(browser, "close", None)
            if callable(close):
                close()
    if command == "status":
        return json_model_or_text(store.load_pending(), as_json=args.json)
    if command == "report":
        return render_pending_report(store.load_pending())
    if command == "finish":
        return pending_cleanup_finish(store, force=args.force)
    raise RuntimeError(f"unhandled pending-cleanup command {command}")


def pending_threshold_days(args: argparse.Namespace) -> int:
    threshold_days = int(args.threshold_days)
    if threshold_days == 0:
        threshold_months = int(args.threshold_months)
        threshold_weeks = int(args.threshold_weeks)
        return threshold_months * 30 if threshold_months else threshold_weeks * 7
    return threshold_days


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
    capture: bool = False,
    audit: bool = False,
    saved_searches: bool = False,
    acceptance_outcomes: bool = False,
    accepted_research: bool = False,
    pending_capture: bool = False,
    followup: bool = False,
    withdraw: bool = False,
) -> BrowserClient:
    fixture = getattr(args, "fixture_result", None)
    if fixture:
        path = Path(fixture)
        return FixtureBrowserClient(
            send_result=path if send else None,
            capture=path if capture else None,
            audit=path if audit else None,
            saved_searches=path if saved_searches else None,
            acceptance_outcomes=path if acceptance_outcomes else None,
            accepted_research=path if accepted_research else None,
            pending_capture=path if pending_capture else None,
            followup_result=path if followup else None,
            withdraw_result=path if withdraw else None,
        )
    send_fixture = getattr(args, "fixture_send_result", None)
    capture_fixture = getattr(args, "fixture_capture_result", None)
    audit_fixture = getattr(args, "fixture_audit_result", None)
    if send_fixture or capture_fixture or audit_fixture:
        return FixtureBrowserClient(
            send_result=Path(send_fixture) if send_fixture and send else None,
            capture=Path(capture_fixture) if capture_fixture and capture else None,
            audit=Path(audit_fixture) if audit_fixture and audit else None,
        )
    session_arg = getattr(args, "session", None)
    session = None if session_arg in {None, "", "auto"} else str(session_arg)
    return PlaywriterBrowserClient(
        out_dir=Path(getattr(args, "out_dir", str(DEFAULT_SEND_OUT_DIR))),
        session=session,
    )


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
