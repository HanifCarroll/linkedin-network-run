"""Acceptance command parser and dispatch wiring."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .acceptance_service import (
    acceptance_check,
    acceptance_draft_followups,
    acceptance_dry_run_followups,
    acceptance_export,
    acceptance_export_followup_candidates,
    acceptance_import,
    acceptance_invalidate_weak_message_acceptances,
    acceptance_report,
    acceptance_research,
    acceptance_retry_send_followup,
    acceptance_run_daily_session,
    acceptance_seed,
    acceptance_seed_history,
    acceptance_send_followup,
    acceptance_send_ready_followups,
)
from .browser import DEFAULT_FOLLOWUP_OUT_DIR, BrowserClient
from .models import DraftStrategy
from .store import Store

DEFAULT_ACCEPTANCE_CANDIDATES = Path("/tmp/linkedin-acceptance-candidates.json")
DEFAULT_ACCEPTANCE_OUTCOMES = Path("/tmp/linkedin-acceptance-outcomes.json")
DEFAULT_ACCEPTANCE_CHUNK_DIR = Path("/tmp/linkedin-acceptance-chunks")
DEFAULT_ACCEPTANCE_SESSION_OUT_DIR = Path("/tmp/linkedin-acceptance-daily-session")
DEFAULT_ACCEPTED_RESEARCH = Path("/tmp/linkedin-accepted-followups/accepted-research.json")
DEFAULT_ACCEPTED_CANDIDATES = Path("/tmp/linkedin-accepted-followups/accepted-candidates.json")
DEFAULT_ACCEPTED_FOLLOWUP_OUT_DIR = Path("/tmp/linkedin-accepted-followups")


def register_acceptance_commands(subparsers: Any) -> None:
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
    acceptance_daily.add_argument("--draft-review", default=None)
    acceptance_daily.add_argument("--draft-out-dir", default=str(DEFAULT_ACCEPTED_FOLLOWUP_OUT_DIR))
    acceptance_daily.add_argument("--include-drafted", action="store_true")
    acceptance_daily.add_argument("--strategy", default=DraftStrategy.ASAP_CONTRACT_V1.value)
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
    acceptance_invalidate = acceptance_sub.add_parser("invalidate-weak-message-acceptances")
    acceptance_invalidate.add_argument("--apply", action="store_true")
    acceptance_invalidate.add_argument("--sample-limit", type=int, default=10)
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
    acceptance_research_parser.add_argument("--delay-ms", type=int, default=500)
    acceptance_research_parser.add_argument("--fixture-result", default=None)
    acceptance_draft = acceptance_sub.add_parser("draft-followups")
    acceptance_draft.add_argument("--session", default=None)
    acceptance_draft.add_argument("--research", default=None)
    acceptance_draft.add_argument("--out", default=None)
    acceptance_draft.add_argument("--review-out", default=None)
    acceptance_draft.add_argument("--out-dir", default="/tmp/linkedin-accepted-followups")
    acceptance_draft.add_argument("--include-drafted", action="store_true")
    acceptance_draft.add_argument("--strategy", default=DraftStrategy.ASAP_CONTRACT_V1.value)
    acceptance_draft.add_argument("--delay-ms", type=int, default=500)
    acceptance_draft.add_argument("--research-offset", type=int, default=0)
    acceptance_draft.add_argument("--research-limit", type=int, default=0)
    acceptance_draft.add_argument("--fixture-result", default=None)
    acceptance_send = acceptance_sub.add_parser("send-followup")
    acceptance_send.add_argument("--id", required=True)
    acceptance_send.add_argument("--session", default="auto")
    acceptance_send.add_argument("--dry-run", action="store_true")
    acceptance_send.add_argument("--preview-fill", action="store_true")
    acceptance_send.add_argument("--allow-send", action="store_true")
    acceptance_send.add_argument("--fixture-result", default=None)
    acceptance_send.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))
    acceptance_retry = acceptance_sub.add_parser("retry-send-followup")
    acceptance_retry.add_argument("--id", required=True)
    acceptance_retry.add_argument("--session", default="auto")
    acceptance_retry.add_argument("--allow-send", action="store_true")
    acceptance_retry.add_argument("--fixture-result", default=None)
    acceptance_retry.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))
    acceptance_dry = acceptance_sub.add_parser("dry-run-followups")
    acceptance_dry.add_argument("--session", default="auto")
    acceptance_dry.add_argument("--limit", type=int, default=5)
    acceptance_dry.add_argument("--retry-classified", action="store_true")
    acceptance_dry.add_argument("--fixture-result", default=None)
    acceptance_dry.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))
    acceptance_ready = acceptance_sub.add_parser("send-ready-followups")
    acceptance_ready.add_argument("--session", default="auto")
    acceptance_ready.add_argument("--limit", type=int, default=5)
    acceptance_ready.add_argument("--allow-send", action="store_true")
    acceptance_ready.add_argument("--fixture-result", default=None)
    acceptance_ready.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))


def dispatch_acceptance(
    args: argparse.Namespace,
    store: Store,
    *,
    browser_from_args: Callable[..., BrowserClient],
) -> str:
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
            followup_review_out=Path(args.draft_review) if args.draft_review else None,
            followup_research_out_dir=Path(args.draft_out_dir) if args.draft_out_dir else None,
            include_drafted=args.include_drafted,
            strategy=DraftStrategy(args.strategy),
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
    if command == "invalidate-weak-message-acceptances":
        return acceptance_invalidate_weak_message_acceptances(
            store,
            apply=args.apply,
            sample_limit=args.sample_limit,
        )
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
            delay_ms=args.delay_ms,
            research_offset=args.research_offset,
            research_limit=args.research_limit,
            review_out=Path(args.review_out) if args.review_out else None,
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
    if command == "retry-send-followup":
        return acceptance_retry_send_followup(
            store,
            browser_from_args(args, followup=True),
            record_id=args.id,
            allow_send=args.allow_send,
        )
    if command == "dry-run-followups":
        return acceptance_dry_run_followups(
            store,
            browser_from_args(args, followup=True),
            limit=args.limit,
            retry_classified=args.retry_classified,
        )
    if command == "send-ready-followups":
        return acceptance_send_ready_followups(
            store,
            browser_from_args(args, followup=True),
            limit=args.limit,
            allow_send=args.allow_send,
        )
    raise RuntimeError(f"unhandled acceptance command {command}")
