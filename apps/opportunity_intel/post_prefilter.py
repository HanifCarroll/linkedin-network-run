"""Post queue filtering from measured extraction manifests."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from apps.opportunity_intel.post_discovery import PostCandidate

POST_QUEUE_COLUMNS: tuple[str, ...] = (
    "source_id",
    "source_kind",
    "query_id",
    "post_url",
    "source_url",
    "search_query",
    "priority",
    "reason",
)

PREFILTER_METRIC_COLUMNS: tuple[str, ...] = (
    "source_id",
    "source_kind",
    "query_id",
    "post_url",
    "source_url",
    "search_query",
    "priority",
    "queue_reason",
    "run_id",
    "status",
    "comments_found",
    "kept",
    "prefilter_reason",
)


@dataclass(frozen=True)
class PostEngagementMetric:
    post_url: str
    run_id: str
    status: str
    comments_found: int


@dataclass(frozen=True)
class PostPrefilterDecision:
    candidate: PostCandidate
    metric: PostEngagementMetric | None
    kept: bool
    reason: str


@dataclass(frozen=True)
class PostPrefilterResult:
    post_queue_path: Path
    manifest_path: Path
    output_path: Path
    metrics_path: Path
    min_comments: int
    total_candidates: int
    measured_candidates: int
    kept_candidates: int
    rejected_candidates: int
    missing_metric_candidates: int

    def to_json_object(self) -> dict[str, object]:
        return {
            "post_queue_path": str(self.post_queue_path),
            "manifest_path": str(self.manifest_path),
            "output_path": str(self.output_path),
            "metrics_path": str(self.metrics_path),
            "min_comments": self.min_comments,
            "total_candidates": self.total_candidates,
            "measured_candidates": self.measured_candidates,
            "kept_candidates": self.kept_candidates,
            "rejected_candidates": self.rejected_candidates,
            "missing_metric_candidates": self.missing_metric_candidates,
        }


def prefilter_post_queue_from_manifest(
    *,
    post_queue_path: Path,
    manifest_path: Path,
    output_path: Path,
    metrics_path: Path | None = None,
    min_comments: int = 10,
) -> PostPrefilterResult:
    """Filter a post queue using measured comment counts from an extractor manifest."""
    if min_comments < 0:
        raise ValueError("min_comments must be >= 0")
    candidates = read_post_queue(post_queue_path)
    metrics = read_extraction_manifest(manifest_path)
    decisions = tuple(
        _decide_candidate(candidate, metrics.get(candidate.post_url), min_comments)
        for candidate in candidates
    )
    selected = tuple(decision.candidate for decision in decisions if decision.kept)
    metrics_output = metrics_path or output_path.with_suffix(output_path.suffix + ".metrics.csv")
    write_post_queue(output_path, selected)
    write_prefilter_metrics(metrics_output, decisions)
    measured_count = sum(1 for decision in decisions if decision.metric is not None)
    kept_count = len(selected)
    missing_count = sum(1 for decision in decisions if decision.metric is None)
    return PostPrefilterResult(
        post_queue_path=post_queue_path,
        manifest_path=manifest_path,
        output_path=output_path,
        metrics_path=metrics_output,
        min_comments=min_comments,
        total_candidates=len(candidates),
        measured_candidates=measured_count,
        kept_candidates=kept_count,
        rejected_candidates=len(candidates) - kept_count,
        missing_metric_candidates=missing_count,
    )


def read_post_queue(path: Path) -> tuple[PostCandidate, ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        missing_columns = [column for column in POST_QUEUE_COLUMNS if column not in fieldnames]
        if missing_columns:
            raise ValueError("post queue missing columns: " + ",".join(missing_columns))
        rows: list[PostCandidate] = []
        for row_number, row in enumerate(reader, start=2):
            priority_value = row.get("priority", "")
            try:
                priority = int(priority_value)
            except ValueError as exc:
                raise ValueError(f"invalid priority at row {row_number}: {priority_value}") from exc
            rows.append(
                PostCandidate(
                    source_id=row.get("source_id", ""),
                    source_kind=row.get("source_kind", ""),
                    query_id=row.get("query_id", ""),
                    post_url=row.get("post_url", ""),
                    source_url=row.get("source_url", ""),
                    search_query=row.get("search_query", ""),
                    priority=priority,
                    reason=row.get("reason", ""),
                )
            )
    return tuple(rows)


def write_post_queue(path: Path, candidates: tuple[PostCandidate, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=POST_QUEUE_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(candidate.__dict__)


def read_extraction_manifest(path: Path) -> dict[str, PostEngagementMetric]:
    metrics: dict[str, PostEngagementMetric] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            clean_line = line.strip()
            if not clean_line:
                continue
            payload = json.loads(clean_line)
            if not isinstance(payload, dict):
                raise ValueError(f"manifest line {line_number} must be a JSON object")
            post_url = _required_text(payload, "post_url", line_number)
            comments_value = payload.get("comments_found")
            if not isinstance(comments_value, int):
                raise ValueError(f"manifest line {line_number} missing integer comments_found")
            metrics[post_url] = PostEngagementMetric(
                post_url=post_url,
                run_id=_optional_text(payload.get("run_id")),
                status=_optional_text(payload.get("status")),
                comments_found=comments_value,
            )
    return metrics


def write_prefilter_metrics(
    path: Path,
    decisions: tuple[PostPrefilterDecision, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREFILTER_METRIC_COLUMNS)
        writer.writeheader()
        for decision in decisions:
            candidate = decision.candidate
            metric = decision.metric
            writer.writerow(
                {
                    "source_id": candidate.source_id,
                    "source_kind": candidate.source_kind,
                    "query_id": candidate.query_id,
                    "post_url": candidate.post_url,
                    "source_url": candidate.source_url,
                    "search_query": candidate.search_query,
                    "priority": candidate.priority,
                    "queue_reason": candidate.reason,
                    "run_id": metric.run_id if metric else "",
                    "status": metric.status if metric else "",
                    "comments_found": metric.comments_found if metric else "",
                    "kept": str(decision.kept).lower(),
                    "prefilter_reason": decision.reason,
                }
            )


def _decide_candidate(
    candidate: PostCandidate,
    metric: PostEngagementMetric | None,
    min_comments: int,
) -> PostPrefilterDecision:
    if not candidate.post_url:
        return PostPrefilterDecision(
            candidate=candidate,
            metric=metric,
            kept=False,
            reason="missing_post_url",
        )
    if metric is None:
        return PostPrefilterDecision(
            candidate=candidate,
            metric=None,
            kept=False,
            reason="missing_extraction_metric",
        )
    if metric.status == "failed":
        return PostPrefilterDecision(
            candidate=candidate,
            metric=metric,
            kept=False,
            reason="extraction_failed",
        )
    if metric.comments_found < min_comments:
        return PostPrefilterDecision(
            candidate=candidate,
            metric=metric,
            kept=False,
            reason=f"comments_found_below_{min_comments}",
        )
    return PostPrefilterDecision(
        candidate=candidate,
        metric=metric,
        kept=True,
        reason="comments_found_met_threshold",
    )


def _required_text(payload: dict[object, object], field: str, line_number: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"manifest line {line_number} missing text {field}")
    return value


def _optional_text(value: object) -> str:
    return value if isinstance(value, str) else ""
