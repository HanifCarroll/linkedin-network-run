"""Acceptance, welcome-message, and relationship-enrichment CLI wiring."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .acceptance_service import (
    CODEX_ENRICHMENT_MODEL,
    CODEX_ENRICHMENT_REASONING_EFFORT,
    DEFAULT_ACCEPTANCE_TIMEZONE,
    acceptance_check,
    acceptance_collect_enrichment_workers,
    acceptance_dry_run_followups,
    acceptance_export,
    acceptance_export_browser_investigation_queue,
    acceptance_export_enrichment_queue,
    acceptance_import,
    acceptance_invalidate_weak_message_acceptances,
    acceptance_launch_enrichment_workers,
    acceptance_prepare_welcome_messages,
    acceptance_report,
    acceptance_retry_send_followup,
    acceptance_run_daily_session,
    acceptance_run_welcome_messages,
    acceptance_seed,
    acceptance_seed_history,
    acceptance_send_followup,
    acceptance_send_ready_followups,
)
from .browser import DEFAULT_FOLLOWUP_OUT_DIR, BrowserClient
from .relationship_radar import (
    apply_browser_investigation,
    save_recommended_watchlist_leads,
    sync_relationship_radar_actions,
    update_relationship_radar,
)
from .store import Store

DEFAULT_ACCEPTANCE_CANDIDATES = Path("/tmp/linkedin-acceptance-candidates.json")
DEFAULT_ACCEPTANCE_OUTCOMES = Path("/tmp/linkedin-acceptance-outcomes.json")
DEFAULT_ACCEPTANCE_CHUNK_DIR = Path("/tmp/linkedin-acceptance-chunks")
DEFAULT_ACCEPTANCE_SESSION_OUT_DIR = Path("/tmp/linkedin-acceptance-daily-session")
DEFAULT_ENRICHMENT_DIR = Path("/tmp/linkedin-relationship-radar")
DEFAULT_ENRICHMENT_QUEUE = DEFAULT_ENRICHMENT_DIR / "enrichment-queue.json"
DEFAULT_ENRICHMENT_JOBS_DIR = DEFAULT_ENRICHMENT_DIR / "enrichment-jobs"
DEFAULT_ENRICHMENT_SOURCES_DIR = DEFAULT_ENRICHMENT_DIR / "source-bundles"
DEFAULT_ENRICHMENT_DECISIONS = DEFAULT_ENRICHMENT_DIR / "enrichment-decisions.json"
DEFAULT_WELCOME_ELIGIBILITY = Path("/tmp/linkedin-accepted-welcome/eligibility.json")


def register_acceptance_commands(subparsers: Any) -> None:
    acceptance = subparsers.add_parser("acceptance")
    commands = acceptance.add_subparsers(dest="acceptance_command", required=True)

    seed = commands.add_parser("seed")
    seed.add_argument("--include-unfinished", action="store_true")
    commands.add_parser("seed-history")

    daily = commands.add_parser("run-daily-session")
    daily.add_argument("--session", default="auto")
    daily.add_argument("--min-age-days", type=int, default=1)
    daily.add_argument("--max-age-days", type=int, default=45)
    daily.add_argument("--candidates-out", default=str(DEFAULT_ACCEPTANCE_CANDIDATES))
    daily.add_argument("--outcomes-out", default=str(DEFAULT_ACCEPTANCE_OUTCOMES))
    daily.add_argument("--chunk-dir", default=str(DEFAULT_ACCEPTANCE_CHUNK_DIR))
    daily.add_argument("--chunk-size", type=int, default=25)
    daily.add_argument("--chunk-retries", type=int, default=3)
    daily.add_argument("--check-delay-ms", type=int, default=750)
    daily.add_argument("--timezone", default=DEFAULT_ACCEPTANCE_TIMEZONE)
    daily.add_argument("--out-dir", default=str(DEFAULT_ACCEPTANCE_SESSION_OUT_DIR))
    daily.add_argument("--fixture-result", default=None)

    export = commands.add_parser("export")
    export.add_argument("--min-age-days", type=int, default=7)
    export.add_argument("--max-age-days", type=int, default=None)
    export.add_argument("--out", default=str(DEFAULT_ACCEPTANCE_CANDIDATES))

    import_outcomes = commands.add_parser("import")
    import_outcomes.add_argument("path")

    invalidate = commands.add_parser("invalidate-weak-message-acceptances")
    invalidate.add_argument("--apply", action="store_true")
    invalidate.add_argument("--sample-limit", type=int, default=10)

    check = commands.add_parser("check")
    check.add_argument("--session", default="auto")
    check.add_argument("--in", dest="input", default=str(DEFAULT_ACCEPTANCE_CANDIDATES))
    check.add_argument("--out", default=str(DEFAULT_ACCEPTANCE_OUTCOMES))
    check.add_argument("--offset", type=int, default=0)
    check.add_argument("--limit", type=int, default=0)
    check.add_argument("--delay-ms", type=int, default=500)
    check.add_argument("--fixture-result", default=None)

    report = commands.add_parser("report")
    report.add_argument("--min-age-days", type=int, default=0)
    report.add_argument("--max-age-days", type=int, default=None)
    report.add_argument("--json", action="store_true")
    report.add_argument("--daily-days", type=int, default=30)
    report.add_argument("--timezone", default=DEFAULT_ACCEPTANCE_TIMEZONE)

    enrichment_queue = commands.add_parser("export-enrichment-queue")
    enrichment_queue.add_argument("--out", default=str(DEFAULT_ENRICHMENT_QUEUE))
    enrichment_queue.add_argument("--markdown-out", default=None)
    enrichment_queue.add_argument("--offset", type=int, default=0)
    enrichment_queue.add_argument("--limit", type=int, default=30)
    enrichment_queue.add_argument("--stale-after-days", type=int, default=30)
    enrichment_queue.add_argument("--prioritize-engagement", action="store_true")

    browser_queue = commands.add_parser("export-browser-investigation-queue")
    browser_queue.add_argument("--out", required=True)
    browser_queue.add_argument("--markdown-out", default=None)
    browser_queue.add_argument("--limit", type=int, default=5)
    browser_queue.add_argument("--cooldown-days", type=int, default=30)

    launch_enrichment = commands.add_parser("launch-enrichment-workers")
    launch_enrichment.add_argument("--enrichment-queue", default=str(DEFAULT_ENRICHMENT_QUEUE))
    launch_enrichment.add_argument("--jobs-dir", default=str(DEFAULT_ENRICHMENT_JOBS_DIR))
    launch_enrichment.add_argument("--sources-dir", default=str(DEFAULT_ENRICHMENT_SOURCES_DIR))
    launch_enrichment.add_argument("--codex-bin", default="codex")
    launch_enrichment.add_argument("--cwd", default=str(Path.cwd()))
    launch_enrichment.add_argument("--model", default=CODEX_ENRICHMENT_MODEL)
    launch_enrichment.add_argument("--reasoning-effort", default=CODEX_ENRICHMENT_REASONING_EFFORT)
    launch_enrichment.add_argument("--offset", type=int, default=0)
    launch_enrichment.add_argument("--limit", type=int, default=0)
    launch_enrichment.add_argument("--force", action="store_true")
    launch_enrichment.add_argument("--fetch-timeout-seconds", type=float, default=20.0)

    collect_enrichment = commands.add_parser("collect-enrichment-workers")
    collect_enrichment.add_argument("--enrichment-queue", default=str(DEFAULT_ENRICHMENT_QUEUE))
    collect_enrichment.add_argument("--jobs-dir", default=str(DEFAULT_ENRICHMENT_JOBS_DIR))
    collect_enrichment.add_argument("--out", default=str(DEFAULT_ENRICHMENT_DECISIONS))
    collect_enrichment.add_argument("--offset", type=int, default=0)
    collect_enrichment.add_argument("--limit", type=int, default=0)

    welcome = commands.add_parser("prepare-welcome-messages")
    welcome.add_argument("--out", default=str(DEFAULT_WELCOME_ELIGIBILITY))
    welcome.add_argument("--report-out", default=None)
    welcome.add_argument("--limit", type=int, default=30)

    radar = commands.add_parser("update-relationship-radar")
    radar.add_argument("--enrichment", default=str(DEFAULT_ENRICHMENT_DECISIONS))
    radar.add_argument("--out", default=None)
    radar.add_argument("--markdown-out", default=None)

    browser_apply = commands.add_parser("apply-browser-investigation")
    browser_apply.add_argument("--queue", required=True)
    browser_apply.add_argument("--enrichment", required=True)
    browser_apply.add_argument("--out", default=None)
    browser_apply.add_argument("--markdown-out", default=None)

    radar_sync = commands.add_parser("sync-relationship-radar-actions")
    radar_sync.add_argument("--out", default=None)
    radar_sync.add_argument("--markdown-out", default=None)

    watchlist = commands.add_parser("save-watchlist-leads")
    watchlist.add_argument("--session", default="auto")
    watchlist.add_argument("--limit", type=int, default=30)
    watchlist.add_argument("--allow-save", action="store_true")
    watchlist.add_argument("--fixture-result", default=None)
    watchlist.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))

    _register_greeting_action_commands(commands)


def _register_greeting_action_commands(commands: Any) -> None:
    send = commands.add_parser("send-greeting")
    send.add_argument("--id", required=True)
    send.add_argument("--session", default="auto")
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--preview-fill", action="store_true")
    send.add_argument("--allow-send", action="store_true")
    send.add_argument("--fixture-result", default=None)
    send.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))

    retry = commands.add_parser("retry-send-greeting")
    retry.add_argument("--id", required=True)
    retry.add_argument("--session", default="auto")
    retry.add_argument("--allow-send", action="store_true")
    retry.add_argument("--fixture-result", default=None)
    retry.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))

    dry_run = commands.add_parser("dry-run-greetings")
    dry_run.add_argument("--session", default="auto")
    dry_run.add_argument("--limit", type=int, default=5)
    dry_run.add_argument("--retry-classified", action="store_true")
    dry_run.add_argument("--fixture-result", default=None)
    dry_run.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))

    ready = commands.add_parser("send-ready-greetings")
    ready.add_argument("--session", default="auto")
    ready.add_argument("--limit", type=int, default=5)
    ready.add_argument("--allow-send", action="store_true")
    ready.add_argument("--fixture-result", default=None)
    ready.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))

    welcome = commands.add_parser("run-welcome-messages")
    welcome.add_argument("--session", default="auto")
    welcome.add_argument("--limit", type=int, default=30)
    welcome.add_argument("--allow-send", action="store_true")
    welcome.add_argument("--out-dir", default=str(DEFAULT_FOLLOWUP_OUT_DIR))


def dispatch_acceptance(
    args: argparse.Namespace,
    store: Store,
    *,
    browser_from_args: Callable[..., BrowserClient],
    emit: Callable[[str], None] | None = None,
) -> str:
    command = str(args.acceptance_command)
    if command == "seed":
        return acceptance_seed(store, include_unfinished=args.include_unfinished)
    if command == "seed-history":
        return acceptance_seed_history(store)
    if command == "run-daily-session":
        return acceptance_run_daily_session(
            store,
            lambda: browser_from_args(args, acceptance_outcomes=True),
            min_age_days=args.min_age_days,
            max_age_days=args.max_age_days,
            candidates_out=Path(args.candidates_out),
            outcomes_out=Path(args.outcomes_out),
            chunk_dir=Path(args.chunk_dir),
            chunk_size=args.chunk_size,
            chunk_retries=args.chunk_retries,
            check_delay_ms=args.check_delay_ms,
            daily_timezone=args.timezone,
            emit=emit,
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
            store, apply=args.apply, sample_limit=args.sample_limit
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
            daily_days=args.daily_days,
            daily_timezone=args.timezone,
        )
    if command == "export-enrichment-queue":
        return acceptance_export_enrichment_queue(
            store,
            out=Path(args.out),
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            offset=args.offset,
            limit=args.limit,
            stale_after_days=args.stale_after_days,
            prioritize_engagement=args.prioritize_engagement,
        )
    if command == "export-browser-investigation-queue":
        return acceptance_export_browser_investigation_queue(
            store,
            out=Path(args.out),
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            limit=args.limit,
            cooldown_days=args.cooldown_days,
        )
    if command == "launch-enrichment-workers":
        return acceptance_launch_enrichment_workers(
            store,
            enrichment_queue=Path(args.enrichment_queue),
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
    if command == "collect-enrichment-workers":
        return acceptance_collect_enrichment_workers(
            store,
            enrichment_queue=Path(args.enrichment_queue),
            jobs_dir=Path(args.jobs_dir),
            out=Path(args.out),
            offset=args.offset,
            limit=args.limit,
        )
    if command == "prepare-welcome-messages":
        return acceptance_prepare_welcome_messages(
            store,
            out=Path(args.out),
            report_out=Path(args.report_out) if args.report_out else None,
            limit=args.limit,
        )
    if command == "update-relationship-radar":
        return update_relationship_radar(
            store,
            enrichment=Path(args.enrichment),
            out=Path(args.out) if args.out else None,
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
        )
    if command == "apply-browser-investigation":
        return apply_browser_investigation(
            store,
            queue=Path(args.queue),
            enrichment=Path(args.enrichment),
            out=Path(args.out) if args.out else None,
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
        )
    if command == "sync-relationship-radar-actions":
        return sync_relationship_radar_actions(
            store,
            out=Path(args.out) if args.out else None,
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
        )
    if command == "save-watchlist-leads":
        return save_recommended_watchlist_leads(
            store,
            browser_from_args(args, lead_list=True),
            limit=args.limit,
            allow_save=args.allow_save,
        )
    return _dispatch_greeting_action(command, args, store, browser_from_args)


def _dispatch_greeting_action(
    command: str,
    args: argparse.Namespace,
    store: Store,
    browser_from_args: Callable[..., BrowserClient],
) -> str:
    if command == "send-greeting":
        return acceptance_send_followup(
            store,
            browser_from_args(args, followup=True),
            record_id=args.id,
            dry_run=args.dry_run,
            preview_fill=args.preview_fill,
            allow_send=args.allow_send,
        )
    if command == "retry-send-greeting":
        return acceptance_retry_send_followup(
            store,
            browser_from_args(args, followup=True),
            record_id=args.id,
            allow_send=args.allow_send,
        )
    if command == "dry-run-greetings":
        return acceptance_dry_run_followups(
            store,
            browser_from_args(args, followup=True),
            limit=args.limit,
            retry_classified=args.retry_classified,
        )
    if command == "send-ready-greetings":
        return acceptance_send_ready_followups(
            store,
            browser_from_args(args, followup=True),
            limit=args.limit,
            allow_send=args.allow_send,
        )
    if command == "run-welcome-messages":
        return acceptance_run_welcome_messages(
            store,
            browser_from_args(args, followup=True),
            run_limit=args.limit,
            allow_send=args.allow_send,
        )
    raise RuntimeError(f"unhandled acceptance command {command}")
