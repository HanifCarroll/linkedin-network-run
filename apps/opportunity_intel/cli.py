"""CLI namespace for the opportunity intelligence port."""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from apps.comment_extractor.browser import run_browser_preflight, write_preflight_artifact
from apps.comment_extractor.cli import main as comment_extractor_main
from apps.opportunity_intel.company_pages import extract_company_page_post_candidates_from_html_file
from apps.opportunity_intel.contracts import (
    CANONICAL_COMMENT_COLUMNS,
    CommentEvidence,
)
from apps.opportunity_intel.experiments import evaluate_gate, run_source_experiment
from apps.opportunity_intel.imports import read_comment_csv
from apps.opportunity_intel.normalization import normalize_and_dedupe
from apps.opportunity_intel.post_discovery import PostCandidate, discover_posts_from_registry
from apps.opportunity_intel.post_prefilter import prefilter_post_queue_from_manifest
from apps.opportunity_intel.ranking import rank_comment
from apps.opportunity_intel.search_capture import (
    SearchCaptureLimits,
    capture_search_posts_from_queue,
)
from apps.opportunity_intel.sources import (
    DEFAULT_QUERY_PACK_PATH,
    DEFAULT_SOURCE_REGISTRY_PATH,
    load_query_pack,
    load_source_registry,
    validate_registry_against_queries,
)
from apps.opportunity_intel.store import OpportunityStore
from packages.linkedin_common.progress import ProgressReporter

DEFAULT_BATCH_DIR = Path("/tmp/linkedin-opportunity-intel")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opportunity-intel",
        description="Recommend-only LinkedIn opportunity intelligence.",
    )
    subparsers = parser.add_subparsers(dest="command")

    validate_parser = _add_command(subparsers, "validate-contracts", _handle_validate_contracts)
    _add_contract_args(validate_parser)

    sources_parser = _add_command(subparsers, "sources", _handle_sources)
    _add_contract_args(sources_parser)
    sources_parser.add_argument("--json", action="store_true")

    query_parser = _add_command(subparsers, "query-pack", _handle_query_pack)
    _add_contract_args(query_parser)
    query_parser.add_argument("--json", action="store_true")

    status_parser = _add_command(subparsers, "status", _handle_status)
    _add_contract_args(status_parser)
    status_parser.add_argument("--json", action="store_true")

    preflight_parser = _add_command(subparsers, "preflight", _handle_preflight)
    _add_contract_args(preflight_parser)
    preflight_parser.add_argument("--state-dir", type=Path, default=None)
    preflight_parser.add_argument("--check-browser", action="store_true")
    preflight_parser.add_argument("--cdp-url", default=None)
    preflight_parser.add_argument("--json", action="store_true")

    for name in ("post-queue", "collection-queue", "salesnav-feeder", "salesnav-activity"):
        queue_parser = _add_command(subparsers, name, _handle_post_queue)
        _add_contract_args(queue_parser)
        queue_parser.add_argument("--out", type=Path, default=None)
        queue_parser.add_argument("--json", action="store_true")

    coverage_parser = _add_command(subparsers, "collection-coverage", _handle_collection_coverage)
    _add_contract_args(coverage_parser)
    coverage_parser.add_argument("--json", action="store_true")

    readiness_parser = _add_command(subparsers, "provider-readiness", _handle_provider_readiness)
    _add_contract_args(readiness_parser)
    readiness_parser.add_argument("--json", action="store_true")

    template_parser = _add_command(
        subparsers,
        "provider-export-csv",
        _handle_provider_export_csv,
    )
    template_parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_BATCH_DIR / "provider-template.csv",
    )

    prepare_parser = _add_command(subparsers, "prepare-batch", _handle_prepare_batch)
    _add_contract_args(prepare_parser)
    prepare_parser.add_argument("--out", type=Path, default=DEFAULT_BATCH_DIR / "post-queue.csv")

    company_capture_parser = _add_command(
        subparsers,
        "company-post-capture",
        _handle_company_post_capture,
    )
    _add_contract_args(company_capture_parser)
    company_capture_parser.add_argument("--source-id", required=True)
    company_capture_parser.add_argument("--html", type=Path, required=True)
    company_capture_parser.add_argument("--out", type=Path, required=True)

    run_batch_parser = _add_command(subparsers, "run-batch", _handle_run_batch)
    run_batch_parser.add_argument("--post-queue", type=Path, required=True)
    run_batch_parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_BATCH_DIR / "raw-comments",
    )
    run_batch_parser.add_argument("--state-dir", type=Path, default=None)
    run_batch_parser.add_argument(
        "--provider-csv",
        type=Path,
        default=DEFAULT_BATCH_DIR / "provider-comments.csv",
    )
    run_batch_parser.add_argument("--cdp-url", default=None)

    search_capture_parser = _add_command(
        subparsers,
        "capture-search-posts",
        _handle_capture_search_posts,
    )
    search_capture_parser.add_argument("--post-queue", type=Path, required=True)
    search_capture_parser.add_argument("--out", type=Path, required=True)
    search_capture_parser.add_argument("--metrics-jsonl", type=Path, default=None)
    search_capture_parser.add_argument("--checkpoint", type=Path, default=None)
    search_capture_parser.add_argument("--cdp-url", default=None)
    search_capture_parser.add_argument("--max-results-per-search", type=int, default=50)
    search_capture_parser.add_argument("--max-scrolls", type=int, default=20)
    search_capture_parser.add_argument("--scroll-pixels", type=int, default=1800)
    search_capture_parser.add_argument("--navigation-timeout-ms", type=int, default=30000)
    search_capture_parser.add_argument("--action-timeout-ms", type=int, default=5000)
    search_capture_parser.add_argument("--settle-ms", type=int, default=1000)
    search_capture_parser.add_argument("--json", action="store_true")

    prefilter_queue_parser = _add_command(
        subparsers,
        "prefilter-post-queue",
        _handle_prefilter_post_queue,
    )
    prefilter_queue_parser.add_argument("--post-queue", type=Path, required=True)
    prefilter_queue_parser.add_argument("--manifest", type=Path, required=True)
    prefilter_queue_parser.add_argument("--out", type=Path, required=True)
    prefilter_queue_parser.add_argument("--metrics-out", type=Path, default=None)
    prefilter_queue_parser.add_argument("--min-comments", type=int, default=10)
    prefilter_queue_parser.add_argument("--json", action="store_true")

    batch_status_parser = _add_command(subparsers, "batch-status", _handle_batch_status)
    batch_status_parser.add_argument("--out-dir", type=Path, default=DEFAULT_BATCH_DIR)
    batch_status_parser.add_argument("--json", action="store_true")

    for name in ("validate-batch", "process-batch", "evaluate"):
        comments_parser = _add_command(subparsers, name, _handle_comments_summary)
        _add_comments_args(comments_parser, comments_required=True)
        comments_parser.add_argument("--json", action="store_true")

    import_parser = _add_command(subparsers, "import-signals", _handle_import_signals)
    _add_contract_args(import_parser)
    import_parser.add_argument("--comments-csv", type=Path, required=True, action="append")
    import_parser.add_argument("--state-dir", type=Path, default=None)
    import_parser.add_argument("--run-id", default=None)
    import_parser.add_argument("--json", action="store_true")

    merge_parser = _add_command(subparsers, "merge-comments-csv", _handle_merge_comments_csv)
    _add_contract_args(merge_parser)
    merge_parser.add_argument("--out", type=Path, required=True)
    merge_parser.add_argument("paths", nargs="+", type=Path)

    export_parser = _add_command(subparsers, "export-captures-csv", _handle_export_captures_csv)
    _add_contract_args(export_parser)
    export_parser.add_argument("--out", type=Path, required=True)
    export_parser.add_argument("paths", nargs="+", type=Path)

    for name in ("run-experiment", "run-spike"):
        experiment_parser = _add_command(subparsers, name, _handle_run_experiment)
        _add_experiment_args(experiment_parser)

    for name in (
        "review-queue",
        "calibration-template",
        "calibration-report",
        "gate-report",
        "source-decision",
        "action-plan",
    ):
        artifact_parser = _add_command(subparsers, name, _handle_experiment_artifact)
        _add_experiment_or_run_dir_args(artifact_parser)

    history_parser = _add_command(subparsers, "run-history", _handle_run_history)
    history_parser.add_argument("--out-dir", type=Path, default=DEFAULT_BATCH_DIR)
    history_parser.add_argument("--json", action="store_true")

    checkpoint_parser = _add_command(subparsers, "checkpoint", _handle_checkpoint)
    checkpoint_parser.add_argument("--out-dir", type=Path, default=DEFAULT_BATCH_DIR)

    iteration_parser = _add_command(subparsers, "iteration-plan", _handle_iteration_plan)
    iteration_parser.add_argument("--out-dir", type=Path, default=DEFAULT_BATCH_DIR)

    public_capture_parser = _add_command(
        subparsers,
        "public-post-capture",
        _handle_public_post_capture,
    )
    public_capture_parser.add_argument("--post-url", required=True)
    public_capture_parser.add_argument("--html", type=Path, required=True)
    public_capture_parser.add_argument("--source-id", required=True)
    public_capture_parser.add_argument("--query-id", required=True)
    public_capture_parser.add_argument("--source-kind", default="known_post")
    public_capture_parser.add_argument("--source-url", default="")
    public_capture_parser.add_argument("--search-query", default="")
    public_capture_parser.add_argument("--out-dir", type=Path, required=True)

    profile_parser = _add_command(subparsers, "profile-enrich", _handle_profile_enrich)
    profile_parser.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    typed_handler = cast(Callable[[argparse.Namespace], int], handler)
    return typed_handler(args)


def _add_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    handler: Callable[[argparse.Namespace], int],
) -> argparse.ArgumentParser:
    command = subparsers.add_parser(name)
    command.set_defaults(handler=handler)
    return command


def _add_contract_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-registry", type=Path, default=DEFAULT_SOURCE_REGISTRY_PATH)
    parser.add_argument("--query-pack", type=Path, default=DEFAULT_QUERY_PACK_PATH)


def _add_comments_args(parser: argparse.ArgumentParser, *, comments_required: bool) -> None:
    _add_contract_args(parser)
    parser.add_argument("--comments-csv", type=Path, required=comments_required)


def _add_experiment_args(parser: argparse.ArgumentParser) -> None:
    _add_comments_args(parser, comments_required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--run-id", default=None)


def _add_experiment_or_run_dir_args(parser: argparse.ArgumentParser) -> None:
    _add_contract_args(parser)
    parser.add_argument("--comments-csv", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_BATCH_DIR)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--run-dir", type=Path, default=None)


def _handle_validate_contracts(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    query_pack = load_query_pack(args.query_pack)
    validate_registry_against_queries(registry, query_pack)
    print(
        f"validated {len(registry.sources)} sources and {len(query_pack.queries)} queries "
        f"from {args.source_registry}"
    )
    return 0


def _handle_sources(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    rows = [
        {
            "source_id": source.source_id,
            "source_kind": source.source_kind.value,
            "title": source.title,
            "enabled": source.enabled,
            "priority": source.priority,
            "query_ids": list(source.query_ids),
        }
        for source in registry.sources
    ]
    _print_json_or_table(
        rows,
        args.json,
        ("source_id", "source_kind", "enabled", "priority", "title"),
    )
    return 0


def _handle_query_pack(args: argparse.Namespace) -> int:
    query_pack = load_query_pack(args.query_pack)
    rows = [
        {
            "query_id": query.query_id,
            "title": query.title,
            "search_queries": list(query.search_queries),
            "need_categories": list(query.need_categories),
            "source_ids": list(query.source_ids),
        }
        for query in query_pack.queries
    ]
    _print_json_or_table(rows, args.json, ("query_id", "title", "source_ids"))
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    query_pack = load_query_pack(args.query_pack)
    validate_registry_against_queries(registry, query_pack)
    payload = {
        "sources": len(registry.sources),
        "enabled_sources": len(registry.enabled_sources()),
        "queries": len(query_pack.queries),
        "recommend_only": True,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "opportunity-intel status: "
            f"sources={payload['sources']} enabled={payload['enabled_sources']} "
            f"queries={payload['queries']} recommend_only=true"
        )
    return 0


def _handle_preflight(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    query_pack = load_query_pack(args.query_pack)
    validate_registry_against_queries(registry, query_pack)
    candidates = discover_posts_from_registry(registry)
    store = OpportunityStore(args.state_dir)
    store.sync_source_registry(registry)
    store.sync_post_candidates(candidates)
    browser = run_browser_preflight(check_browser=args.check_browser, cdp_url=args.cdp_url)
    artifact_path = write_preflight_artifact(store=store, result=browser)
    payload = {
        "ready": browser.ready,
        "recommend_only": True,
        "sources": len(registry.sources),
        "enabled_sources": len(registry.enabled_sources()),
        "post_candidates": len(candidates),
        "browser": browser.to_json_object(),
        "artifact_path": str(artifact_path),
        "state_dir": str(store.dir),
        "database_path": str(store.database_path),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"preflight_ready={str(browser.ready).lower()}")
        print(f"sources={payload['sources']} enabled={payload['enabled_sources']}")
        print(f"post_candidates={payload['post_candidates']}")
        print(f"state_dir={store.dir}")
        print(f"artifact={artifact_path}")
        for warning in browser.warnings:
            print(f"warning={warning}")
    return 0


def _handle_post_queue(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    candidates = discover_posts_from_registry(registry)
    if args.out is not None:
        _write_post_queue_csv(args.out, candidates)
        print(f"post queue: {args.out}")
        return 0
    if args.json:
        print(
            json.dumps(
                [candidate.__dict__ for candidate in candidates],
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    for candidate in candidates:
        print(
            "\t".join(
                (
                    candidate.source_id,
                    candidate.query_id,
                    candidate.reason,
                    candidate.post_url or candidate.search_query,
                )
            )
        )
    return 0


def _handle_collection_coverage(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    candidates = discover_posts_from_registry(registry)
    by_source: dict[str, int] = {}
    for candidate in candidates:
        by_source[candidate.source_id] = by_source.get(candidate.source_id, 0) + 1
    payload = {
        "enabled_sources": len(registry.enabled_sources()),
        "post_candidates": len(candidates),
        "by_source": by_source,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"post_candidates={len(candidates)} enabled_sources={len(registry.enabled_sources())}"
        )
        for source_id, count in sorted(by_source.items()):
            print(f"{source_id}\t{count}")
    return 0


def _handle_provider_readiness(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    provider_sources = [
        source
        for source in registry.sources
        if source.source_kind.value in {"provider_csv", "manual_csv"}
    ]
    payload = {
        "ready": True,
        "contract": list(CANONICAL_COMMENT_COLUMNS),
        "provider_sources": [source.source_id for source in provider_sources],
        "note": "Native providers stay behind the canonical CSV contract.",
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("provider_readiness=ready")
        print("contract_columns=" + ",".join(CANONICAL_COMMENT_COLUMNS))
        provider_source_ids = cast(list[str], payload["provider_sources"])
        print("provider_sources=" + ",".join(provider_source_ids))
    return 0


def _handle_provider_export_csv(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COMMENT_COLUMNS)
        writer.writeheader()
    print(f"provider template: {args.out}")
    return 0


def _handle_prepare_batch(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    _write_post_queue_csv(args.out, discover_posts_from_registry(registry))
    print(f"post queue: {args.out}")
    return 0


def _handle_company_post_capture(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    query_pack = load_query_pack(args.query_pack)
    validate_registry_against_queries(registry, query_pack)
    source = registry.require_source(args.source_id)
    candidates = extract_company_page_post_candidates_from_html_file(
        source=source,
        html_path=args.html,
    )
    _write_post_queue_csv(args.out, candidates)
    print(f"company post queue: {args.out} rows={len(candidates)}")
    return 0


def _handle_run_batch(args: argparse.Namespace) -> int:
    command = [
        "extract-url-queue",
        "--post-queue",
        str(args.post_queue),
        "--out-dir",
        str(args.out_dir),
        "--provider-csv",
        str(args.provider_csv),
    ]
    if args.state_dir is not None:
        command.extend(["--state-dir", str(args.state_dir)])
    if args.cdp_url:
        command.extend(["--cdp-url", str(args.cdp_url)])
    return comment_extractor_main(command)


def _handle_capture_search_posts(args: argparse.Namespace) -> int:
    metrics_path = args.metrics_jsonl or args.out.with_suffix(args.out.suffix + ".metrics.jsonl")
    checkpoint_path = args.checkpoint or args.out.with_suffix(args.out.suffix + ".checkpoint.json")
    result = capture_search_posts_from_queue(
        post_queue_path=args.post_queue,
        output_path=args.out,
        metrics_path=metrics_path,
        checkpoint_path=checkpoint_path,
        limits=SearchCaptureLimits(
            max_results_per_search=args.max_results_per_search,
            max_scrolls=args.max_scrolls,
            scroll_pixels=args.scroll_pixels,
            navigation_timeout_ms=args.navigation_timeout_ms,
            action_timeout_ms=args.action_timeout_ms,
            settle_ms=args.settle_ms,
        ),
        cdp_url=args.cdp_url,
        progress=ProgressReporter(),
    )
    if args.json:
        print(json.dumps(result.to_json_object(), indent=2, sort_keys=True))
    else:
        print(f"processed_searches={result.processed_searches}")
        print(f"known_posts={result.known_posts}")
        print(f"captured_posts={result.captured_posts}")
        print(f"duplicate_posts={result.duplicate_posts}")
        print(f"failed_searches={result.failed_searches}")
        print(f"post_queue={result.output_path}")
        print(f"metrics={result.metrics_path}")
        print(f"checkpoint={result.checkpoint_path}")
    return 0


def _handle_prefilter_post_queue(args: argparse.Namespace) -> int:
    reporter = ProgressReporter()
    reporter.emit(
        "prefilter_start",
        post_queue=args.post_queue,
        manifest=args.manifest,
        min_comments=args.min_comments,
    )
    result = prefilter_post_queue_from_manifest(
        post_queue_path=args.post_queue,
        manifest_path=args.manifest,
        output_path=args.out,
        metrics_path=args.metrics_out,
        min_comments=args.min_comments,
    )
    reporter.emit(
        "prefilter_done",
        kept_candidates=result.kept_candidates,
        rejected_candidates=result.rejected_candidates,
        missing_metric_candidates=result.missing_metric_candidates,
    )
    payload = result.to_json_object()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"kept_candidates={result.kept_candidates}")
        print(f"rejected_candidates={result.rejected_candidates}")
        print(f"missing_metric_candidates={result.missing_metric_candidates}")
        print(f"filtered_post_queue={result.output_path}")
        print(f"metrics={result.metrics_path}")
    return 0


def _handle_batch_status(args: argparse.Namespace) -> int:
    files = sorted(args.out_dir.rglob("*")) if args.out_dir.exists() else []
    payload = {
        "out_dir": str(args.out_dir),
        "exists": args.out_dir.exists(),
        "file_count": sum(1 for path in files if path.is_file()),
        "raw_comment_files": [str(path) for path in files if path.name == "raw_comments.jsonl"],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"batch_status exists={str(payload['exists']).lower()} "
            f"file_count={payload['file_count']}"
        )
        for path in payload["raw_comment_files"]:
            print(f"raw_comments={path}")
    return 0


def _handle_comments_summary(args: argparse.Namespace) -> int:
    query_pack = load_query_pack(args.query_pack)
    import_result = read_comment_csv(args.comments_csv, query_pack)
    deduped = normalize_and_dedupe(import_result.valid_comments)
    ranked = tuple(
        rank_comment(comment, query_pack.require_query(comment.query_id))
        for comment in deduped.comments
    )
    gate = evaluate_gate(ranked)
    payload = {
        "valid_comments": len(import_result.valid_comments),
        "rejected_rows": len(import_result.rejected_rows),
        "duplicates_removed": deduped.duplicate_count,
        "gate": gate.to_json_object(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"valid_comments={payload['valid_comments']} "
            f"rejected_rows={payload['rejected_rows']} "
            f"duplicates_removed={payload['duplicates_removed']} "
            f"gate_passed={str(gate.passed).lower()}"
        )
    return 0


def _handle_import_signals(args: argparse.Namespace) -> int:
    query_pack = load_query_pack(args.query_pack)
    comments: list[CommentEvidence] = []
    rejected_rows = 0
    for path in args.comments_csv:
        import_result = read_comment_csv(path, query_pack)
        comments.extend(import_result.valid_comments)
        rejected_rows += len(import_result.rejected_rows)

    deduped = normalize_and_dedupe(tuple(comments))
    store = OpportunityStore(args.state_dir)
    store.sync_source_registry(load_source_registry(args.source_registry))
    run_id = args.run_id or "import_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ranked = store.persist_comments(
        run_id=run_id,
        comments=deduped.comments,
        query_pack=query_pack,
    )
    gate = evaluate_gate(ranked)
    payload = {
        "valid_comments": len(comments),
        "rejected_rows": rejected_rows,
        "duplicates_removed": deduped.duplicate_count,
        "imported_comments": len(deduped.comments),
        "run_id": run_id,
        "state_dir": str(store.dir),
        "database_path": str(store.database_path),
        "gate": gate.to_json_object(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"imported_comments={payload['imported_comments']} "
            f"valid_comments={payload['valid_comments']} "
            f"rejected_rows={payload['rejected_rows']} "
            f"duplicates_removed={payload['duplicates_removed']} "
            f"gate_passed={str(gate.passed).lower()}"
        )
        print(f"state_dir={store.dir}")
    return 0


def _handle_merge_comments_csv(args: argparse.Namespace) -> int:
    comments = _read_many_comments(args.paths, args.query_pack)
    _write_comments_csv(args.out, comments)
    print(f"merged comments: {args.out} rows={len(comments)}")
    return 0


def _handle_export_captures_csv(args: argparse.Namespace) -> int:
    comments = _read_many_comments(args.paths, args.query_pack)
    _write_comments_csv(args.out, comments)
    print(f"captures csv: {args.out} rows={len(comments)}")
    return 0


def _handle_run_experiment(args: argparse.Namespace) -> int:
    artifacts = run_source_experiment(
        comments_csv_path=args.comments_csv,
        output_dir=args.out_dir,
        source_registry_path=args.source_registry,
        query_pack_path=args.query_pack,
        run_id=args.run_id,
    )
    print(f"source report: {artifacts.source_report_path}")
    return 0


def _handle_experiment_artifact(args: argparse.Namespace) -> int:
    run_dir = _ensure_run_dir(args)
    path = run_dir / _artifact_name_for_command(args.command)
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    print(path.read_text(encoding="utf-8").rstrip())
    return 0


def _handle_run_history(args: argparse.Namespace) -> int:
    path = args.out_dir / "run_history.jsonl"
    if not path.exists():
        if args.json:
            print("[]")
        else:
            print(f"run_history missing: {path}")
        return 0
    if args.json:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        print(path.read_text(encoding="utf-8").rstrip())
    return 0


def _handle_checkpoint(args: argparse.Namespace) -> int:
    path = args.out_dir / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "out_dir": str(args.out_dir),
        "recommend_only": True,
        "run_history_exists": (args.out_dir / "run_history.jsonl").exists(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"checkpoint: {path}")
    return 0


def _handle_iteration_plan(args: argparse.Namespace) -> int:
    history = args.out_dir / "run_history.jsonl"
    if not history.exists():
        print("iteration_plan=collect_actual_comment_batch")
        print(
            "next_command=opportunity-intel run-experiment "
            "--comments-csv <csv> --out-dir " + str(args.out_dir)
        )
        return 0
    lines = [line for line in history.read_text(encoding="utf-8").splitlines() if line]
    latest = json.loads(lines[-1]) if lines else {}
    decision = latest.get("source_decision", {})
    print("iteration_plan=" + str(decision.get("decision", "review_latest_run")))
    print("latest_run_id=" + str(latest.get("run_id", "")))
    return 0


def _handle_public_post_capture(args: argparse.Namespace) -> int:
    argv = [
        "extract",
        "--post-url",
        args.post_url,
        "--html",
        str(args.html),
        "--source-id",
        args.source_id,
        "--query-id",
        args.query_id,
        "--source-kind",
        args.source_kind,
        "--source-url",
        args.source_url,
        "--search-query",
        args.search_query,
        "--out-dir",
        str(args.out_dir),
    ]
    return comment_extractor_main(argv)


def _handle_profile_enrich(args: argparse.Namespace) -> int:
    payload = {
        "recommend_only": True,
        "status": "not_applicable",
        "reason": "Profile enrichment is not part of the recommend-only opportunity workflow.",
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("profile_enrich=not_applicable")
        print(payload["reason"])
    return 0


def _ensure_run_dir(args: argparse.Namespace) -> Path:
    if args.run_dir is not None:
        return cast(Path, args.run_dir)
    if args.comments_csv is None:
        raise ValueError("--run-dir or --comments-csv is required")
    artifacts = run_source_experiment(
        comments_csv_path=args.comments_csv,
        output_dir=args.out_dir,
        source_registry_path=args.source_registry,
        query_pack_path=args.query_pack,
        run_id=args.run_id,
    )
    return Path(artifacts.output_dir)


def _artifact_name_for_command(command: str) -> str:
    return {
        "review-queue": "review_queue.csv",
        "calibration-template": "calibration_template.csv",
        "calibration-report": "calibration_report.md",
        "gate-report": "source_gate.json",
        "source-decision": "source_decision.json",
        "action-plan": "action_plan.md",
    }[command]


def _read_many_comments(
    paths: Sequence[Path],
    query_pack_path: Path,
) -> tuple[CommentEvidence, ...]:
    query_pack = load_query_pack(query_pack_path)
    comments: list[CommentEvidence] = []
    for path in paths:
        comments.extend(read_comment_csv(path, query_pack).valid_comments)
    return normalize_and_dedupe(tuple(comments)).comments


def _write_comments_csv(path: Path, comments: tuple[CommentEvidence, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COMMENT_COLUMNS)
        writer.writeheader()
        for comment in comments:
            writer.writerow(comment.to_row())


def _write_post_queue_csv(path: Path, candidates: tuple[PostCandidate, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "source_id",
                "source_kind",
                "query_id",
                "post_url",
                "source_url",
                "search_query",
                "priority",
                "reason",
            ),
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(candidate.__dict__)


def _print_json_or_table(
    rows: list[dict[str, Any]],
    as_json: bool,
    fields: tuple[str, ...],
) -> None:
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    for row in rows:
        print("\t".join(str(row.get(field, "")) for field in fields))


if __name__ == "__main__":
    raise SystemExit(main())
