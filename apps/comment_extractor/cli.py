"""CLI namespace for the comment extractor app."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from apps.comment_extractor.contracts import PostHTMLInput
from apps.comment_extractor.linkedin_post_comments import (
    extract_comments_from_html_file,
    write_raw_comments_jsonl,
)
from apps.opportunity_intel.contracts import CommentEvidence


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
    extract_parser.set_defaults(handler=_handle_extract)

    queue_parser = subparsers.add_parser("extract-queue")
    queue_parser.add_argument("--post-queue", type=Path, required=True)
    queue_parser.add_argument("--out-dir", type=Path, required=True)
    queue_parser.set_defaults(handler=_handle_extract_queue)

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
    print(f"raw comments: {output_path}")
    return 0


def _handle_extract_queue(args: argparse.Namespace) -> int:
    comments: list[CommentEvidence] = []
    for input_row in _read_post_queue(args.post_queue):
        result = extract_comments_from_html_file(input_row)
        comments.extend(result.comments)
    output_path = write_raw_comments_jsonl(tuple(comments), args.out_dir)
    print(f"raw comments: {output_path}")
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


if __name__ == "__main__":
    raise SystemExit(main())
