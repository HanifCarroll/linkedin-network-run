"""CLI namespace for the network automation port."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from packages.linkedin_browser import LINKEDIN_CDP_URL_ENV, chrome_profile_from_env

from .browser import (
    DEFAULT_AUDIT_OUT_DIR,
    DEFAULT_CAPTURE_OUT_DIR,
    DEFAULT_FOLLOWUP_OUT_DIR,
    DEFAULT_SEND_OUT_DIR,
    DEFAULT_WITHDRAW_OUT_DIR,
    BrowserClient,
    FixtureBrowserClient,
    PlaywrightBrowserClient,
)
from .models import CandidateStatus, DraftStrategy
from .old_state import inspect_old_state
from .reports import render_pending_report, render_report
from .service import (
    acceptance_check,
    acceptance_draft_followups,
    acceptance_dry_run_followups,
    acceptance_export,
    acceptance_export_followup_candidates,
    acceptance_import,
    acceptance_report,
    acceptance_research,
    acceptance_run_daily_session,
    acceptance_seed,
    acceptance_seed_history,
    acceptance_send_followup,
    acceptance_send_ready_followups,
    browser_session_cdp_url,
    browser_session_start,
    browser_session_status,
    browser_session_stop,
    capture_saved_searches,
    capture_source,
    drain_stale_candidates,
    finish_run,
    import_audit,
    import_capture_path,
    needs_reaudit,
    network_run_session,
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
    resume_blocked,
    send_guarded,
    send_next,
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
DEFAULT_ACCEPTANCE_CANDIDATES = Path("/tmp/linkedin-acceptance-candidates.json")
DEFAULT_ACCEPTANCE_OUTCOMES = Path("/tmp/linkedin-acceptance-outcomes.json")
DEFAULT_ACCEPTANCE_CHUNK_DIR = Path("/tmp/linkedin-acceptance-chunks")
DEFAULT_ACCEPTANCE_SESSION_OUT_DIR = Path("/tmp/linkedin-acceptance-daily-session")
DEFAULT_ACCEPTED_RESEARCH = Path("/tmp/linkedin-accepted-followups/accepted-research.json")
DEFAULT_ACCEPTED_CANDIDATES = Path("/tmp/linkedin-accepted-followups/accepted-candidates.json")
DEFAULT_ACCEPTED_FOLLOWUP_OUT_DIR = Path("/tmp/linkedin-accepted-followups")
DEFAULT_PENDING_CAPTURE = Path("/tmp/linkedin-pending-cleanup-capture.json")
DEFAULT_PENDING_SESSION_OUT_DIR = Path("/tmp/linkedin-pending-cleanup-session")


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

    run_session = subparsers.add_parser("run-session")
    run_session.add_argument("--session", default="auto")
    run_session.add_argument("--target", type=int, default=30)
    run_session.add_argument("--max-real-sends", type=int, default=30)
    run_session.add_argument("--force", action="store_true")
    run_session.add_argument("--saved-searches-url", default=DEFAULT_SAVED_SEARCHES_URL)
    run_session.add_argument("--saved-searches", default=str(DEFAULT_SAVED_SEARCHES))
    run_session.add_argument("--out-dir", default=str(DEFAULT_NETWORK_SESSION_OUT_DIR))
    run_session.add_argument("--allow-send", action="store_true")
    run_session.add_argument("--audit-attempts", type=int, default=3)
    run_session.add_argument("--audit-delay-ms", type=int, default=5000)
    run_session.add_argument("--max-steps", type=int, default=100)
    run_session.add_argument("--finish", action="store_true")
    run_session.add_argument("--fixture-result", default=None)

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

    browser_session = subparsers.add_parser("browser-session")
    browser_session_sub = browser_session.add_subparsers(
        dest="browser_session_command", required=True
    )
    browser_session_start_parser = browser_session_sub.add_parser("start")
    browser_session_start_parser.add_argument("--url", default=DEFAULT_SAVED_SEARCHES_URL)
    browser_session_start_parser.add_argument("--force", action="store_true")
    browser_session_status_parser = browser_session_sub.add_parser("status")
    browser_session_status_parser.add_argument("--json", action="store_true")
    browser_session_sub.add_parser("stop")

    reconcile = subparsers.add_parser("reconcile-audit")
    reconcile.add_argument("--session", default="auto")
    reconcile.add_argument("--attempts", type=int, default=3)
    reconcile.add_argument("--delay-ms", type=int, default=5000)
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
    acceptance_daily = acceptance_sub.add_parser("run-daily-session")
    acceptance_daily.add_argument("--session", default="auto")
    acceptance_daily.add_argument("--min-age-days", type=int, default=1)
    acceptance_daily.add_argument("--max-age-days", type=int, default=45)
    acceptance_daily.add_argument("--candidates-out", default=str(DEFAULT_ACCEPTANCE_CANDIDATES))
    acceptance_daily.add_argument("--outcomes-out", default=str(DEFAULT_ACCEPTANCE_OUTCOMES))
    acceptance_daily.add_argument("--chunk-dir", default=str(DEFAULT_ACCEPTANCE_CHUNK_DIR))
    acceptance_daily.add_argument("--chunk-size", type=int, default=25)
    acceptance_daily.add_argument("--check-delay-ms", type=int, default=750)
    acceptance_daily.add_argument("--no-draft-followups", action="store_true")
    acceptance_daily.add_argument("--draft-report", default=None)
    acceptance_daily.add_argument("--draft-out-dir", default=str(DEFAULT_ACCEPTED_FOLLOWUP_OUT_DIR))
    acceptance_daily.add_argument("--include-drafted", action="store_true")
    acceptance_daily.add_argument("--strategy", default=DraftStrategy.ASAP_CONTRACT_V1.value)
    acceptance_daily.add_argument("--no-public-web", action="store_true")
    acceptance_daily.add_argument("--max-web-results", type=int, default=5)
    acceptance_daily.add_argument("--research-delay-ms", type=int, default=500)
    acceptance_daily.add_argument("--out-dir", default=str(DEFAULT_ACCEPTANCE_SESSION_OUT_DIR))
    acceptance_daily.add_argument("--fixture-result", default=None)
    acceptance_export_parser = acceptance_sub.add_parser("export")
    acceptance_export_parser.add_argument("--min-age-days", type=int, default=7)
    acceptance_export_parser.add_argument("--max-age-days", type=int, default=None)
    acceptance_export_parser.add_argument(
        "--out", default="/tmp/linkedin-acceptance-candidates.json"
    )
    acceptance_import_parser = acceptance_sub.add_parser("import")
    acceptance_import_parser.add_argument("path")
    acceptance_check_parser = acceptance_sub.add_parser("check")
    acceptance_check_parser.add_argument("--session", default="auto")
    acceptance_check_parser.add_argument(
        "--in", dest="input", default="/tmp/linkedin-acceptance-candidates.json"
    )
    acceptance_check_parser.add_argument("--out", default=str(DEFAULT_ACCEPTANCE_OUTCOMES))
    acceptance_check_parser.add_argument("--offset", type=int, default=0)
    acceptance_check_parser.add_argument("--limit", type=int, default=0)
    acceptance_check_parser.add_argument("--delay-ms", type=int, default=500)
    acceptance_check_parser.add_argument("--fixture-result", default=None)
    acceptance_report_parser = acceptance_sub.add_parser("report")
    acceptance_report_parser.add_argument("--min-age-days", type=int, default=0)
    acceptance_report_parser.add_argument("--max-age-days", type=int, default=None)
    acceptance_report_parser.add_argument("--json", action="store_true")
    acceptance_candidates = acceptance_sub.add_parser("export-followup-candidates")
    acceptance_candidates.add_argument("--out", default=str(DEFAULT_ACCEPTED_CANDIDATES))
    acceptance_candidates.add_argument("--include-drafted", action="store_true")
    acceptance_research_parser = acceptance_sub.add_parser("research")
    acceptance_research_parser.add_argument("--session", default="auto")
    acceptance_research_parser.add_argument(
        "--in", dest="input", default=str(DEFAULT_ACCEPTED_CANDIDATES)
    )
    acceptance_research_parser.add_argument("--out", default=str(DEFAULT_ACCEPTED_RESEARCH))
    acceptance_research_parser.add_argument("--offset", type=int, default=0)
    acceptance_research_parser.add_argument("--limit", type=int, default=0)
    acceptance_research_parser.add_argument("--no-public-web", action="store_true")
    acceptance_research_parser.add_argument("--max-web-results", type=int, default=5)
    acceptance_research_parser.add_argument("--delay-ms", type=int, default=500)
    acceptance_research_parser.add_argument("--fixture-result", default=None)
    acceptance_draft = acceptance_sub.add_parser("draft-followups")
    acceptance_draft.add_argument("--session", default=None)
    acceptance_draft.add_argument("--research", default=None)
    acceptance_draft.add_argument("--out", default=None)
    acceptance_draft.add_argument("--out-dir", default="/tmp/linkedin-accepted-followups")
    acceptance_draft.add_argument("--include-drafted", action="store_true")
    acceptance_draft.add_argument("--strategy", default=DraftStrategy.ASAP_CONTRACT_V1.value)
    acceptance_draft.add_argument("--no-public-web", action="store_true")
    acceptance_draft.add_argument("--max-web-results", type=int, default=5)
    acceptance_draft.add_argument("--delay-ms", type=int, default=500)
    acceptance_draft.add_argument("--fixture-result", default=None)
    acceptance_send = acceptance_sub.add_parser("send-followup")
    acceptance_send.add_argument("--id", required=True)
    acceptance_send.add_argument("--session", default="auto")
    acceptance_send.add_argument("--dry-run", action="store_true")
    acceptance_send.add_argument("--preview-fill", action="store_true")
    acceptance_send.add_argument("--allow-send", action="store_true")
    acceptance_send.add_argument("--fixture-result", default=None)
    acceptance_send.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))
    acceptance_dry = acceptance_sub.add_parser("dry-run-followups")
    acceptance_dry.add_argument("--session", default="auto")
    acceptance_dry.add_argument("--limit", type=int, default=5)
    acceptance_dry.add_argument("--fixture-result", default=None)
    acceptance_dry.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))
    acceptance_ready = acceptance_sub.add_parser("send-ready-followups")
    acceptance_ready.add_argument("--session", default="auto")
    acceptance_ready.add_argument("--limit", type=int, default=5)
    acceptance_ready.add_argument("--allow-send", action="store_true")
    acceptance_ready.add_argument("--fixture-result", default=None)
    acceptance_ready.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))

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
    pending_session.add_argument("--capture-load-more", type=int, default=10)
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
        )
    if command == "run-session":
        browser = browser_from_args(args, saved_searches=True, capture=True, send=True, audit=True)
        try:
            return network_run_session(
                store,
                browser,
                target=args.target,
                max_real_sends=args.max_real_sends,
                force=args.force,
                saved_searches_url=args.saved_searches_url,
                saved_searches_out=Path(args.saved_searches),
                audit_attempts=args.audit_attempts,
                audit_delay_ms=args.audit_delay_ms,
                allow_send=args.allow_send,
                max_steps=args.max_steps,
                finish=args.finish,
            )
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
    if command == "browser-session":
        return dispatch_browser_session(args, store)
    if command == "reconcile-audit":
        return reconcile_audit(
            store,
            browser_from_args(args, audit=True),
            attempts=args.attempts,
            delay_ms=args.delay_ms,
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
    if command == "run-daily-session":
        return acceptance_run_daily_session(
            store,
            lambda: browser_from_args(args, acceptance_outcomes=True, accepted_research=True),
            min_age_days=args.min_age_days,
            max_age_days=args.max_age_days,
            candidates_out=Path(args.candidates_out),
            outcomes_out=Path(args.outcomes_out),
            chunk_dir=Path(args.chunk_dir),
            chunk_size=args.chunk_size,
            check_delay_ms=args.check_delay_ms,
            draft_followups=not args.no_draft_followups,
            followup_out=Path(args.draft_report) if args.draft_report else None,
            followup_research_out_dir=Path(args.draft_out_dir) if args.draft_out_dir else None,
            include_drafted=args.include_drafted,
            strategy=DraftStrategy(args.strategy),
            public_web=not args.no_public_web,
            max_web_results=args.max_web_results,
            research_delay_ms=args.research_delay_ms,
        )
    if command == "export":
        return acceptance_export(
            store,
            min_age_days=args.min_age_days,
            max_age_days=args.max_age_days,
            out=Path(args.out),
        )
    if command == "import":
        return acceptance_import(store, Path(args.path))
    if command == "check":
        return acceptance_check(
            store,
            browser_from_args(args, acceptance_outcomes=True),
            input_path=Path(args.input),
            out=Path(args.out),
            offset=args.offset,
            limit=args.limit,
            delay_ms=args.delay_ms,
        )
    if command == "report":
        return acceptance_report(
            store,
            min_age_days=args.min_age_days,
            max_age_days=args.max_age_days,
            as_json=args.json,
        )
    if command == "export-followup-candidates":
        return acceptance_export_followup_candidates(
            store,
            out=Path(args.out),
            include_drafted=args.include_drafted,
        )
    if command == "research":
        return acceptance_research(
            store,
            browser_from_args(args, accepted_research=True),
            input_path=Path(args.input),
            out=Path(args.out),
            offset=args.offset,
            limit=args.limit,
            public_web=not args.no_public_web,
            max_web_results=args.max_web_results,
            delay_ms=args.delay_ms,
        )
    if command == "draft-followups":
        return acceptance_draft_followups(
            store,
            research=Path(args.research) if args.research else None,
            out=Path(args.out) if args.out else None,
            include_drafted=args.include_drafted,
            strategy=DraftStrategy(args.strategy),
            browser=browser_from_args(args, accepted_research=True)
            if args.session is not None or args.fixture_result
            else None,
            research_out_dir=Path(args.out_dir) if args.out_dir else None,
            public_web=not args.no_public_web,
            max_web_results=args.max_web_results,
            delay_ms=args.delay_ms,
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


def dispatch_browser_session(args: argparse.Namespace, store: Store) -> str:
    command = str(args.browser_session_command)
    if command == "start":
        return browser_session_start(
            store,
            config=chrome_profile_from_env(),
            start_url=args.url,
            force=args.force,
        )
    if command == "status":
        return browser_session_status(store, as_json=args.json)
    if command == "stop":
        return browser_session_stop(store)
    raise RuntimeError(f"unhandled browser-session command {command}")


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
    cdp_url = None
    if not os.environ.get(LINKEDIN_CDP_URL_ENV):
        cdp_url = browser_session_cdp_url(Store(getattr(args, "state_dir", None)))
    return PlaywrightBrowserClient(
        out_dir=Path(getattr(args, "out_dir", str(DEFAULT_SEND_OUT_DIR))),
        cdp_url=cdp_url,
        max_load_more=int(getattr(args, "max_load_more", 260)),
        withdraw_timeout_seconds=float(getattr(args, "withdraw_timeout_seconds", 90.0)),
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
