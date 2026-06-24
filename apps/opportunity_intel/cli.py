"""CLI namespace for the opportunity intelligence port."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from apps.opportunity_intel.experiments import run_source_experiment
from apps.opportunity_intel.post_discovery import discover_posts_from_registry
from apps.opportunity_intel.sources import (
    DEFAULT_QUERY_PACK_PATH,
    DEFAULT_SOURCE_REGISTRY_PATH,
    load_query_pack,
    load_source_registry,
    validate_registry_against_queries,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opportunity-intel",
        description="Recommend-only LinkedIn opportunity intelligence.",
    )
    subparsers = parser.add_subparsers(dest="command")

    validate_parser = subparsers.add_parser("validate-contracts")
    validate_parser.add_argument(
        "--source-registry", type=Path, default=DEFAULT_SOURCE_REGISTRY_PATH
    )
    validate_parser.add_argument("--query-pack", type=Path, default=DEFAULT_QUERY_PACK_PATH)
    validate_parser.set_defaults(handler=_handle_validate_contracts)

    queue_parser = subparsers.add_parser("post-queue")
    queue_parser.add_argument("--source-registry", type=Path, default=DEFAULT_SOURCE_REGISTRY_PATH)
    queue_parser.set_defaults(handler=_handle_post_queue)

    experiment_parser = subparsers.add_parser("run-experiment")
    experiment_parser.add_argument("--comments-csv", type=Path, required=True)
    experiment_parser.add_argument("--out-dir", type=Path, required=True)
    experiment_parser.add_argument(
        "--source-registry", type=Path, default=DEFAULT_SOURCE_REGISTRY_PATH
    )
    experiment_parser.add_argument("--query-pack", type=Path, default=DEFAULT_QUERY_PACK_PATH)
    experiment_parser.add_argument("--run-id", default=None)
    experiment_parser.set_defaults(handler=_handle_run_experiment)

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


def _handle_validate_contracts(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    query_pack = load_query_pack(args.query_pack)
    validate_registry_against_queries(registry, query_pack)
    print(
        f"validated {len(registry.sources)} sources and {len(query_pack.queries)} queries "
        f"from {args.source_registry}"
    )
    return 0


def _handle_post_queue(args: argparse.Namespace) -> int:
    registry = load_source_registry(args.source_registry)
    for candidate in discover_posts_from_registry(registry):
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


if __name__ == "__main__":
    raise SystemExit(main())
