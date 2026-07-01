"""CLI namespace for the comment extractor app."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from apps.comment_extractor.browser import (
    BrowserExtractionInput,
    BrowserSafetyLimits,
    extract_post_comments_from_url,
    extract_post_comments_from_url_queue,
    run_browser_preflight,
    write_preflight_artifact,
)
from apps.comment_extractor.contracts import PostHTMLInput
from apps.comment_extractor.linkedin_post_comments import (
    extract_comments_from_html_file,
    write_raw_comments_jsonl,
)
from apps.opportunity_intel.contracts import CommentEvidence
from apps.opportunity_intel.sources import load_query_pack
from apps.opportunity_intel.store import OpportunityStore
from packages.linkedin_common.progress import ProgressReporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comment-extractor",
        description="Extract raw comments from known LinkedIn post HTML artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command")

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--post-url", required=True)
    extract_parser.add_argument("--html", type=Path, required=True)
    extract_parser.add_argument("--source-id", required=True)
    extract_parser.add_argument("--query-id", required=True)
    extract_parser.add_argument("--source-kind", default="known_post")
    extract_parser.add_argument("--source-url", default="")
    extract_parser.add_argument("--search-query", default="")
    extract_parser.add_argument("--out-dir", type=Path, required=True)
    extract_parser.add_argument("--state-dir", type=Path, default=None)
    extract_parser.set_defaults(handler=_handle_extract)

    extract_url_parser = subparsers.add_parser("extract-url")
    extract_url_parser.add_argument("--post-url", required=True)
    extract_url_parser.add_argument("--source-id", required=True)
    extract_url_parser.add_argument("--query-id", required=True)
    extract_url_parser.add_argument("--source-kind", default="known_post")
    extract_url_parser.add_argument("--source-url", default="")
    extract_url_parser.add_argument("--search-query", default="")
    extract_url_parser.add_argument("--out-dir", type=Path, required=True)
    extract_url_parser.add_argument("--state-dir", type=Path, default=None)
    _add_safety_limit_args(extract_url_parser)
    extract_url_parser.set_defaults(handler=_handle_extract_url)

    url_queue_parser = subparsers.add_parser("extract-url-queue")
    url_queue_parser.add_argument("--post-queue", type=Path, required=True)
    url_queue_parser.add_argument("--out-dir", type=Path, required=True)
    url_queue_parser.add_argument("--state-dir", type=Path, default=None)
    url_queue_parser.add_argument("--provider-csv", type=Path, default=None)
    _add_safety_limit_args(url_queue_parser)
    url_queue_parser.set_defaults(handler=_handle_extract_url_queue)

    queue_parser = subparsers.add_parser("extract-queue")
    queue_parser.add_argument("--post-queue", type=Path, required=True)
    queue_parser.add_argument("--out-dir", type=Path, required=True)
    queue_parser.set_defaults(handler=_handle_extract_queue)

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--state-dir", type=Path, default=None)
    preflight_parser.add_argument("--check-browser", action="store_true")
    preflight_parser.add_argument("--json", action="store_true")
    preflight_parser.set_defaults(handler=_handle_preflight)

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


def _handle_extract(args: argparse.Namespace) -> int:
    input_row = PostHTMLInput(
        post_url=args.post_url,
        html_path=args.html,
        source_id=args.source_id,
        query_id=args.query_id,
        source_kind=args.source_kind,
        source_url=args.source_url,
        search_query=args.search_query,
    )
    result = extract_comments_from_html_file(input_row)
    output_path = write_raw_comments_jsonl(result.comments, args.out_dir)
    if args.state_dir is not None:
        store = OpportunityStore(args.state_dir)
        run_id = store.start_extraction_run(
            post_url=args.post_url,
            source_id=args.source_id,
            query_id=args.query_id,
            source_kind=args.source_kind,
            source_url=args.source_url,
            search_query=args.search_query,
            browser_profile="saved-html",
            safety_limits={},
        )
        store.record_artifact(run_id=run_id, kind="html", path=args.html)
        store.record_artifact(
            run_id=run_id,
            kind="raw_comments",
            path=output_path,
            metadata={"comment_count": len(result.comments)},
        )
        store.persist_comments(
            run_id=run_id,
            comments=result.comments,
            query_pack=load_query_pack(),
        )
        store.finish_extraction_run(
            run_id,
            status="extracted",
            comments_found=len(result.comments),
            failures=0,
            warning_count=len(result.warnings),
            retry_recommendation="No retry needed" if result.comments else "Review HTML artifact",
        )
    print(f"raw comments: {output_path}")
    return 0


def _handle_extract_url(args: argparse.Namespace) -> int:
    result = extract_post_comments_from_url(
        input_row=BrowserExtractionInput(
            post_url=args.post_url,
            source_id=args.source_id,
            query_id=args.query_id,
            source_kind=args.source_kind,
            source_url=args.source_url,
            search_query=args.search_query,
        ),
        output_dir=args.out_dir,
        store=OpportunityStore(args.state_dir),
        limits=_safety_limits_from_args(args),
        progress=ProgressReporter(),
    )
    print(f"run: {result.run_id}")
    print(f"raw comments: {result.raw_comments_path}")
    print(f"comments_found={result.comments_found}")
    print(f"stop_reason={result.stop_reason}")
    return 0


def _handle_extract_url_queue(args: argparse.Namespace) -> int:
    provider_csv = args.provider_csv or args.out_dir / "provider-comments.csv"
    result = extract_post_comments_from_url_queue(
        input_rows=_read_browser_post_queue(args.post_queue),
        output_dir=args.out_dir,
        store=OpportunityStore(args.state_dir),
        limits=_safety_limits_from_args(args),
        provider_csv_path=provider_csv,
        progress=ProgressReporter(),
    )
    print(f"processed={result.processed}")
    print(f"succeeded={result.succeeded}")
    print(f"failed={result.failed}")
    print(f"skipped={result.skipped}")
    print(f"manifest={result.manifest_path}")
    print(f"checkpoint={result.checkpoint_path}")
    if result.provider_csv_path is not None:
        print(f"provider_csv={result.provider_csv_path}")
    return 0


def _handle_extract_queue(args: argparse.Namespace) -> int:
    comments: list[CommentEvidence] = []
    for input_row in _read_post_queue(args.post_queue):
        result = extract_comments_from_html_file(input_row)
        comments.extend(result.comments)
    output_path = write_raw_comments_jsonl(tuple(comments), args.out_dir)
    print(f"raw comments: {output_path}")
    return 0


def _handle_preflight(args: argparse.Namespace) -> int:
    result = run_browser_preflight(check_browser=args.check_browser)
    artifact_path = write_preflight_artifact(
        store=OpportunityStore(args.state_dir),
        result=result,
    )
    if args.json:
        import json

        payload = result.to_json_object()
        payload["artifact_path"] = str(artifact_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"browser_preflight_ready={str(result.ready).lower()}")
        print(f"profile={result.profile_name}")
        print(f"artifact={artifact_path}")
        for warning in result.warnings:
            print(f"warning={warning}")
    return 0


def _read_post_queue(path: Path) -> tuple[PostHTMLInput, ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows: list[PostHTMLInput] = []
        for row in reader:
            rows.append(
                PostHTMLInput(
                    post_url=row.get("post_url", ""),
                    html_path=Path(row.get("html_path", "")),
                    source_id=row.get("source_id", ""),
                    query_id=row.get("query_id", ""),
                    source_kind=row.get("source_kind", "known_post"),
                    source_url=row.get("source_url", ""),
                    search_query=row.get("search_query", ""),
                )
            )
    return tuple(rows)


def _read_browser_post_queue(path: Path) -> tuple[BrowserExtractionInput, ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows: list[BrowserExtractionInput] = []
        for row in reader:
            post_url = row.get("post_url", "")
            if not post_url:
                continue
            rows.append(
                BrowserExtractionInput(
                    post_url=post_url,
                    source_id=row.get("source_id", ""),
                    query_id=row.get("query_id", ""),
                    source_kind=row.get("source_kind", "known_post"),
                    source_url=row.get("source_url", ""),
                    search_query=row.get("search_query", ""),
                )
            )
    return tuple(rows)


def _add_safety_limit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-scrolls", type=int, default=BrowserSafetyLimits.max_scrolls)
    parser.add_argument(
        "--max-comment-control-clicks",
        type=int,
        default=BrowserSafetyLimits.max_comment_control_clicks,
    )
    parser.add_argument(
        "--max-reply-control-clicks",
        type=int,
        default=BrowserSafetyLimits.max_reply_control_clicks,
    )
    parser.add_argument(
        "--navigation-timeout-ms",
        type=int,
        default=BrowserSafetyLimits.navigation_timeout_ms,
    )
    parser.add_argument(
        "--action-timeout-ms",
        type=int,
        default=BrowserSafetyLimits.action_timeout_ms,
    )
    parser.add_argument("--settle-ms", type=int, default=BrowserSafetyLimits.settle_ms)
    parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=BrowserSafetyLimits.max_runtime_seconds,
    )
    parser.add_argument(
        "--max-no-progress-passes",
        type=int,
        default=BrowserSafetyLimits.max_no_progress_passes,
    )


def _safety_limits_from_args(args: argparse.Namespace) -> BrowserSafetyLimits:
    return BrowserSafetyLimits(
        max_scrolls=args.max_scrolls,
        max_comment_control_clicks=args.max_comment_control_clicks,
        max_reply_control_clicks=args.max_reply_control_clicks,
        navigation_timeout_ms=args.navigation_timeout_ms,
        action_timeout_ms=args.action_timeout_ms,
        settle_ms=args.settle_ms,
        max_runtime_seconds=args.max_runtime_seconds,
        max_no_progress_passes=args.max_no_progress_passes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
