"""Acceptance command parser and dispatch wiring."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .acceptance_service import (
    CODEX_DRAFT_MODEL,
    CODEX_DRAFT_REASONING_EFFORT,
    acceptance_apply_research_decisions,
    acceptance_check,
    acceptance_collect_codex_draft_workers,
    acceptance_collect_codex_research_workers,
    acceptance_draft_followups,
    acceptance_draft_reviewed_followups,
    acceptance_dry_run_followups,
    acceptance_export,
    acceptance_export_followup_candidates,
    acceptance_export_message_queue,
    acceptance_export_research_queue,
    acceptance_finalize_codex_draft_workers,
    acceptance_import,
    acceptance_invalidate_weak_message_acceptances,
    acceptance_launch_codex_draft_workers,
    acceptance_launch_codex_research_workers,
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
DEFAULT_ACCEPTED_RESEARCH_QUEUE = Path("/tmp/linkedin-accepted-followups/research-queue.json")
DEFAULT_ACCEPTED_RESEARCH_JOBS_DIR = Path("/tmp/linkedin-accepted-followups/research-jobs")
DEFAULT_ACCEPTED_SOURCE_BUNDLES_DIR = Path("/tmp/linkedin-accepted-followups/source-bundles")
DEFAULT_ACCEPTED_RESEARCH_DECISIONS = Path(
    "/tmp/linkedin-accepted-followups/research-decisions.json"
)
DEFAULT_ACCEPTED_MESSAGE_QUEUE = Path("/tmp/linkedin-accepted-followups/message-queue.json")
DEFAULT_ACCEPTED_DRAFT_JOBS_DIR = Path("/tmp/linkedin-accepted-followups/draft-jobs")
DEFAULT_ACCEPTED_MESSAGE_DECISIONS = Path(
    "/tmp/linkedin-accepted-followups/message-decisions.json"
)
DEFAULT_ACCEPTED_REVIEWED_RESEARCH = Path(
    "/tmp/linkedin-accepted-followups/reviewed-research.json"
)
DEFAULT_ACCEPTED_FOLLOWUPS_REPORT = Path("/tmp/linkedin-accepted-followups/followups.md")
DEFAULT_ACCEPTED_FOLLOWUPS_REVIEW = Path(
    "/tmp/linkedin-accepted-followups/followups.review.json"
)


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
    acceptance_daily.add_argument("--chunk-retries", type=int, default=3)
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
    acceptance_research_queue = acceptance_sub.add_parser("export-research-queue")
    acceptance_research_queue.add_argument(
        "--out", default="/tmp/linkedin-accepted-followups/research-queue.json"
    )
    acceptance_research_queue.add_argument("--markdown-out", default=None)
    acceptance_research_queue.add_argument("--offset", type=int, default=0)
    acceptance_research_queue.add_argument("--limit", type=int, default=10)
    acceptance_research_queue.add_argument("--include-drafted", action="store_true")
    acceptance_launch_research_workers = acceptance_sub.add_parser("launch-research-workers")
    acceptance_launch_research_workers.add_argument(
        "--research-queue", default=str(DEFAULT_ACCEPTED_RESEARCH_QUEUE)
    )
    acceptance_launch_research_workers.add_argument(
        "--jobs-dir", default=str(DEFAULT_ACCEPTED_RESEARCH_JOBS_DIR)
    )
    acceptance_launch_research_workers.add_argument(
        "--sources-dir", default=str(DEFAULT_ACCEPTED_SOURCE_BUNDLES_DIR)
    )
    acceptance_launch_research_workers.add_argument("--codex-bin", default="codex")
    acceptance_launch_research_workers.add_argument("--cwd", default=str(Path.cwd()))
    acceptance_launch_research_workers.add_argument("--model", default=CODEX_DRAFT_MODEL)
    acceptance_launch_research_workers.add_argument(
        "--reasoning-effort", default=CODEX_DRAFT_REASONING_EFFORT
    )
    acceptance_launch_research_workers.add_argument("--offset", type=int, default=0)
    acceptance_launch_research_workers.add_argument("--limit", type=int, default=0)
    acceptance_launch_research_workers.add_argument("--force", action="store_true")
    acceptance_launch_research_workers.add_argument(
        "--fetch-timeout-seconds", type=float, default=20.0
    )
    acceptance_collect_research_workers = acceptance_sub.add_parser("collect-research-workers")
    acceptance_collect_research_workers.add_argument(
        "--research-queue", default=str(DEFAULT_ACCEPTED_RESEARCH_QUEUE)
    )
    acceptance_collect_research_workers.add_argument(
        "--jobs-dir", default=str(DEFAULT_ACCEPTED_RESEARCH_JOBS_DIR)
    )
    acceptance_collect_research_workers.add_argument(
        "--out", default=str(DEFAULT_ACCEPTED_RESEARCH_DECISIONS)
    )
    acceptance_collect_research_workers.add_argument("--offset", type=int, default=0)
    acceptance_collect_research_workers.add_argument("--limit", type=int, default=0)
    acceptance_apply_decisions = acceptance_sub.add_parser("apply-research-decisions")
    acceptance_apply_decisions.add_argument("path")
    acceptance_apply_decisions.add_argument(
        "--out", default="/tmp/linkedin-accepted-followups/reviewed-research.json"
    )
    acceptance_message_queue = acceptance_sub.add_parser("export-message-queue")
    acceptance_message_queue.add_argument(
        "--reviewed-research",
        default="/tmp/linkedin-accepted-followups/reviewed-research.json",
    )
    acceptance_message_queue.add_argument(
        "--out", default="/tmp/linkedin-accepted-followups/message-queue.json"
    )
    acceptance_message_queue.add_argument("--markdown-out", default=None)
    acceptance_message_queue.add_argument("--include-drafted", action="store_true")
    acceptance_message_queue.add_argument("--offset", type=int, default=0)
    acceptance_message_queue.add_argument("--limit", type=int, default=10)
    acceptance_launch_workers = acceptance_sub.add_parser("launch-draft-workers")
    acceptance_launch_workers.add_argument(
        "--message-queue", default=str(DEFAULT_ACCEPTED_MESSAGE_QUEUE)
    )
    acceptance_launch_workers.add_argument(
        "--jobs-dir", default=str(DEFAULT_ACCEPTED_DRAFT_JOBS_DIR)
    )
    acceptance_launch_workers.add_argument("--codex-bin", default="codex")
    acceptance_launch_workers.add_argument("--cwd", default=str(Path.cwd()))
    acceptance_launch_workers.add_argument("--model", default=CODEX_DRAFT_MODEL)
    acceptance_launch_workers.add_argument(
        "--reasoning-effort", default=CODEX_DRAFT_REASONING_EFFORT
    )
    acceptance_launch_workers.add_argument("--offset", type=int, default=0)
    acceptance_launch_workers.add_argument("--limit", type=int, default=0)
    acceptance_launch_workers.add_argument("--force", action="store_true")
    acceptance_collect_workers = acceptance_sub.add_parser("collect-draft-workers")
    acceptance_collect_workers.add_argument(
        "--message-queue", default=str(DEFAULT_ACCEPTED_MESSAGE_QUEUE)
    )
    acceptance_collect_workers.add_argument(
        "--jobs-dir", default=str(DEFAULT_ACCEPTED_DRAFT_JOBS_DIR)
    )
    acceptance_collect_workers.add_argument(
        "--out", default=str(DEFAULT_ACCEPTED_MESSAGE_DECISIONS)
    )
    acceptance_collect_workers.add_argument("--offset", type=int, default=0)
    acceptance_collect_workers.add_argument("--limit", type=int, default=0)
    acceptance_finalize_workers = acceptance_sub.add_parser("finalize-message-queue")
    acceptance_finalize_workers.add_argument(
        "--message-queue", default=str(DEFAULT_ACCEPTED_MESSAGE_QUEUE)
    )
    acceptance_finalize_workers.add_argument(
        "--jobs-dir", default=str(DEFAULT_ACCEPTED_DRAFT_JOBS_DIR)
    )
    acceptance_finalize_workers.add_argument(
        "--message-decisions-out", default=str(DEFAULT_ACCEPTED_MESSAGE_DECISIONS)
    )
    acceptance_finalize_workers.add_argument(
        "--reviewed-research-out", default=str(DEFAULT_ACCEPTED_REVIEWED_RESEARCH)
    )
    acceptance_finalize_workers.add_argument(
        "--out", default=str(DEFAULT_ACCEPTED_FOLLOWUPS_REPORT)
    )
    acceptance_finalize_workers.add_argument(
        "--review-out", default=str(DEFAULT_ACCEPTED_FOLLOWUPS_REVIEW)
    )
    acceptance_finalize_workers.add_argument("--codex-bin", default="codex")
    acceptance_finalize_workers.add_argument("--cwd", default=str(Path.cwd()))
    acceptance_finalize_workers.add_argument("--model", default=CODEX_DRAFT_MODEL)
    acceptance_finalize_workers.add_argument(
        "--reasoning-effort", default=CODEX_DRAFT_REASONING_EFFORT
    )
    acceptance_finalize_workers.add_argument("--offset", type=int, default=0)
    acceptance_finalize_workers.add_argument("--limit", type=int, default=0)
    acceptance_finalize_workers.add_argument("--force", action="store_true")
    acceptance_finalize_workers.add_argument("--wait-seconds", type=float, default=1200.0)
    acceptance_finalize_workers.add_argument("--poll-seconds", type=float, default=20.0)
    acceptance_finalize_workers.add_argument("--include-drafted", action="store_true")
    acceptance_finalize_workers.add_argument(
        "--strategy", default=DraftStrategy.ASAP_CONTRACT_V1.value
    )
    acceptance_reviewed_draft = acceptance_sub.add_parser("draft-reviewed-followups")
    acceptance_reviewed_draft.add_argument(
        "--reviewed-research",
        default="/tmp/linkedin-accepted-followups/reviewed-research.json",
    )
    acceptance_reviewed_draft.add_argument("--out", default=None)
    acceptance_reviewed_draft.add_argument("--review-out", default=None)
    acceptance_reviewed_draft.add_argument("--include-drafted", action="store_true")
    acceptance_reviewed_draft.add_argument(
        "--strategy", default=DraftStrategy.ASAP_CONTRACT_V1.value
    )
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
            chunk_retries=args.chunk_retries,
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
    if command == "export-research-queue":
        return acceptance_export_research_queue(
            store,
            out=Path(args.out),
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            offset=args.offset,
            limit=args.limit,
            include_drafted=args.include_drafted,
        )
    if command == "launch-research-workers":
        return acceptance_launch_codex_research_workers(
            store,
            research_queue=Path(args.research_queue),
            jobs_dir=Path(args.jobs_dir),
            sources_dir=Path(args.sources_dir),
            codex_bin=args.codex_bin,
            cwd=Path(args.cwd),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            offset=args.offset,
            limit=args.limit,
            force=args.force,
            fetch_timeout_seconds=args.fetch_timeout_seconds,
        )
    if command == "collect-research-workers":
        return acceptance_collect_codex_research_workers(
            store,
            research_queue=Path(args.research_queue),
            jobs_dir=Path(args.jobs_dir),
            out=Path(args.out),
            offset=args.offset,
            limit=args.limit,
        )
    if command == "apply-research-decisions":
        return acceptance_apply_research_decisions(
            store,
            input_path=Path(args.path),
            out=Path(args.out),
        )
    if command == "export-message-queue":
        return acceptance_export_message_queue(
            store,
            reviewed_research=Path(args.reviewed_research),
            out=Path(args.out),
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            include_drafted=args.include_drafted,
            offset=args.offset,
            limit=args.limit,
        )
    if command == "launch-draft-workers":
        return acceptance_launch_codex_draft_workers(
            store,
            message_queue=Path(args.message_queue),
            jobs_dir=Path(args.jobs_dir),
            codex_bin=args.codex_bin,
            cwd=Path(args.cwd),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            offset=args.offset,
            limit=args.limit,
            force=args.force,
        )
    if command == "collect-draft-workers":
        return acceptance_collect_codex_draft_workers(
            store,
            message_queue=Path(args.message_queue),
            jobs_dir=Path(args.jobs_dir),
            out=Path(args.out),
            offset=args.offset,
            limit=args.limit,
        )
    if command == "finalize-message-queue":
        return acceptance_finalize_codex_draft_workers(
            store,
            message_queue=Path(args.message_queue),
            jobs_dir=Path(args.jobs_dir),
            message_decisions_out=Path(args.message_decisions_out),
            reviewed_research_out=Path(args.reviewed_research_out),
            draft_out=Path(args.out),
            review_out=Path(args.review_out),
            codex_bin=args.codex_bin,
            cwd=Path(args.cwd),
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            offset=args.offset,
            limit=args.limit,
            force=args.force,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
            include_drafted=args.include_drafted,
            strategy=DraftStrategy(args.strategy),
        )
    if command == "draft-reviewed-followups":
        return acceptance_draft_reviewed_followups(
            store,
            reviewed_research=Path(args.reviewed_research),
            out=Path(args.out) if args.out else None,
            include_drafted=args.include_drafted,
            strategy=DraftStrategy(args.strategy),
            review_out=Path(args.review_out) if args.review_out else None,
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
