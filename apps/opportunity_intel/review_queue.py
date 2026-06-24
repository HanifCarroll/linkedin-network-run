"""Review queue export helpers."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from apps.opportunity_intel.contracts import RankedComment, RankLevel

REVIEW_QUEUE_COLUMNS: tuple[str, ...] = (
    "rank_level",
    "rank_points",
    "direct_buyer",
    "query_id",
    "source_id",
    "post_url",
    "commenter_name",
    "commenter_profile_url",
    "commenter_headline",
    "commenter_company",
    "comment_text",
    "need_categories",
    "positive_signals",
    "fit_reasons",
    "reject_reasons",
    "evidence_quote",
)


def review_queue_rows(ranked_comments: tuple[RankedComment, ...]) -> tuple[RankedComment, ...]:
    allowed_levels = {RankLevel.HOT, RankLevel.WARM, RankLevel.WATCH}
    return tuple(comment for comment in ranked_comments if comment.rank_level in allowed_levels)


def write_review_queue_csv(path: Path, ranked_comments: tuple[RankedComment, ...]) -> None:
    rows = review_queue_rows(ranked_comments)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_QUEUE_COLUMNS)
        writer.writeheader()
        for ranked_comment in rows:
            writer.writerow(ranked_comment.review_row())


def write_review_queue_jsonl(path: Path, ranked_comments: tuple[RankedComment, ...]) -> None:
    rows = review_queue_rows(ranked_comments)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for ranked_comment in rows:
            handle.write(json.dumps(ranked_comment.review_row(), sort_keys=True) + "\n")
