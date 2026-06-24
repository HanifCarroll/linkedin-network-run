"""Source experiment reporting for opportunity intelligence."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from apps.opportunity_intel.contracts import (
    CalibrationReport,
    CalibrationThresholds,
    ExperimentArtifacts,
    GateResult,
    GateThresholds,
    RankedComment,
    RankLevel,
    SourceDecision,
    SourceDecisionKind,
)
from apps.opportunity_intel.imports import read_comment_csv
from apps.opportunity_intel.normalization import normalize_and_dedupe
from apps.opportunity_intel.ranking import rank_comment
from apps.opportunity_intel.review_queue import (
    review_queue_rows,
    write_review_queue_csv,
    write_review_queue_jsonl,
)
from apps.opportunity_intel.sources import (
    DEFAULT_QUERY_PACK_PATH,
    DEFAULT_SOURCE_REGISTRY_PATH,
    load_query_pack,
    load_source_registry,
    validate_registry_against_queries,
)

CALIBRATION_TEMPLATE_COLUMNS: tuple[str, ...] = (
    "source_id",
    "query_id",
    "comment_id",
    "commenter_name",
    "commenter_profile_url",
    "rank_level",
    "comment_text",
    "human_label",
    "notes",
)


def run_source_experiment(
    *,
    comments_csv_path: Path,
    output_dir: Path,
    source_registry_path: Path = DEFAULT_SOURCE_REGISTRY_PATH,
    query_pack_path: Path = DEFAULT_QUERY_PACK_PATH,
    run_id: str | None = None,
) -> ExperimentArtifacts:
    registry = load_source_registry(source_registry_path)
    query_pack = load_query_pack(query_pack_path)
    validate_registry_against_queries(registry, query_pack)

    import_result = read_comment_csv(comments_csv_path, query_pack)
    dedupe_result = normalize_and_dedupe(import_result.valid_comments)
    ranked_comments = tuple(
        rank_comment(comment, query_pack.require_query(comment.query_id))
        for comment in dedupe_result.comments
    )
    gate = evaluate_gate(ranked_comments)
    calibration = empty_calibration_report()
    decision = decide_source(gate, calibration)

    resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_dir / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    artifacts = ExperimentArtifacts(
        output_dir=run_dir,
        source_report_path=run_dir / "source_report.md",
        gate_path=run_dir / "source_gate.json",
        calibration_template_path=run_dir / "calibration_template.csv",
        calibration_report_path=run_dir / "calibration_report.md",
        source_decision_path=run_dir / "source_decision.json",
        action_plan_path=run_dir / "action_plan.md",
        run_history_path=output_dir / "run_history.jsonl",
        review_queue_csv_path=run_dir / "review_queue.csv",
        review_queue_jsonl_path=run_dir / "review_queue.jsonl",
    )

    write_review_queue_csv(artifacts.review_queue_csv_path, ranked_comments)
    write_review_queue_jsonl(artifacts.review_queue_jsonl_path, ranked_comments)
    _write_gate(artifacts.gate_path, gate)
    _write_calibration_template(artifacts.calibration_template_path, ranked_comments)
    _write_calibration_report(artifacts.calibration_report_path, calibration)
    _write_source_decision(artifacts.source_decision_path, decision)
    _write_action_plan(artifacts.action_plan_path, decision, gate)
    _write_source_report(
        artifacts.source_report_path,
        ranked_comments,
        gate,
        calibration,
        decision,
        rejected_count=len(import_result.rejected_rows),
        duplicate_count=dedupe_result.duplicate_count,
    )
    _append_run_history(
        artifacts.run_history_path,
        run_id=resolved_run_id,
        comments_csv_path=comments_csv_path,
        artifacts=artifacts,
        gate=gate,
        calibration=calibration,
        decision=decision,
    )
    return artifacts


def evaluate_gate(
    ranked_comments: tuple[RankedComment, ...],
    thresholds: GateThresholds | None = None,
) -> GateResult:
    active_thresholds = thresholds or GateThresholds()
    valid_count = len(ranked_comments)
    warm_hot_count = sum(
        1 for item in ranked_comments if item.rank_level in {RankLevel.HOT, RankLevel.WARM}
    )
    direct_buyer_count = sum(1 for item in ranked_comments if item.direct_buyer)
    noise_count = sum(1 for item in ranked_comments if item.rank_level is RankLevel.REJECT)
    warm_hot_per_100 = (warm_hot_count / valid_count * 100) if valid_count else 0.0
    direct_buyer_rate = (direct_buyer_count / valid_count) if valid_count else 0.0
    noise_rate = (noise_count / valid_count) if valid_count else 1.0
    failed: list[str] = []
    if valid_count < active_thresholds.min_valid_comments:
        failed.append("minimum_valid_comments_not_met")
    if warm_hot_count < active_thresholds.min_warm_hot_total:
        failed.append("minimum_warm_hot_total_not_met")
    if warm_hot_per_100 < active_thresholds.min_warm_hot_per_100:
        failed.append("minimum_warm_hot_per_100_not_met")
    if direct_buyer_rate < active_thresholds.min_direct_buyer_rate:
        failed.append("minimum_direct_buyer_rate_not_met")
    if noise_rate > active_thresholds.max_noise_rate:
        failed.append("maximum_noise_rate_exceeded")
    if active_thresholds.require_evidence_fields and not _all_required_evidence_present(
        ranked_comments
    ):
        failed.append("required_evidence_fields_missing")
    return GateResult(
        passed=not failed,
        valid_comment_count=valid_count,
        warm_hot_count=warm_hot_count,
        direct_buyer_count=direct_buyer_count,
        noise_count=noise_count,
        warm_hot_per_100=warm_hot_per_100,
        direct_buyer_rate=direct_buyer_rate,
        noise_rate=noise_rate,
        failed_reasons=tuple(failed),
        thresholds=active_thresholds,
    )


def empty_calibration_report(
    thresholds: CalibrationThresholds | None = None,
) -> CalibrationReport:
    active_thresholds = thresholds or CalibrationThresholds()
    return CalibrationReport(
        passed=False,
        matched_label_count=0,
        precision=0.0,
        recall=0.0,
        failed_reasons=("minimum_labels_overall_not_met",),
        thresholds=active_thresholds,
    )


def decide_source(gate: GateResult, calibration: CalibrationReport) -> SourceDecision:
    if gate.passed and calibration.passed:
        return SourceDecision(
            decision=SourceDecisionKind.PROMOTE,
            reasons=("gate_passed", "calibration_passed"),
        )
    if gate.passed and not calibration.passed:
        return SourceDecision(
            decision=SourceDecisionKind.LABEL_MORE,
            reasons=("gate_passed", *calibration.failed_reasons),
        )
    if gate.valid_comment_count == 0:
        return SourceDecision(
            decision=SourceDecisionKind.USE_HIGHER_CONTROL_FEED,
            reasons=("no_actual_comment_rows",),
        )
    if gate.valid_comment_count >= gate.thresholds.min_valid_comments and gate.warm_hot_count == 0:
        return SourceDecision(
            decision=SourceDecisionKind.REPLACE,
            reasons=("proof_volume_without_warm_signal",),
        )
    if gate.valid_comment_count < gate.thresholds.min_valid_comments and gate.warm_hot_count > 0:
        return SourceDecision(
            decision=SourceDecisionKind.COLLECT_MORE,
            reasons=("warm_signal_below_proof_volume",),
        )
    if gate.noise_rate > gate.thresholds.max_noise_rate:
        return SourceDecision(
            decision=SourceDecisionKind.TIGHTEN_FALSE_POSITIVES,
            reasons=("noise_rate_above_gate",),
        )
    return SourceDecision(
        decision=SourceDecisionKind.COLLECT_MORE,
        reasons=gate.failed_reasons,
    )


def _all_required_evidence_present(ranked_comments: tuple[RankedComment, ...]) -> bool:
    return all(
        item.comment.post_url
        and item.comment.commenter_profile_url
        and item.comment.commenter_name
        and item.comment.comment_text
        and item.comment.query_id
        and item.comment.source_id
        for item in ranked_comments
    )


def _write_gate(path: Path, gate: GateResult) -> None:
    path.write_text(
        json.dumps(gate.to_json_object(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_calibration_template(path: Path, ranked_comments: tuple[RankedComment, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CALIBRATION_TEMPLATE_COLUMNS)
        writer.writeheader()
        for ranked_comment in review_queue_rows(ranked_comments):
            writer.writerow(
                {
                    "source_id": ranked_comment.comment.source_id,
                    "query_id": ranked_comment.comment.query_id,
                    "comment_id": ranked_comment.comment.comment_id,
                    "commenter_name": ranked_comment.comment.commenter_name,
                    "commenter_profile_url": ranked_comment.comment.commenter_profile_url,
                    "rank_level": ranked_comment.rank_level.value,
                    "comment_text": ranked_comment.comment.comment_text,
                    "human_label": "",
                    "notes": "",
                }
            )


def _write_calibration_report(path: Path, calibration: CalibrationReport) -> None:
    body = "\n".join(
        (
            "# Calibration Report",
            "",
            f"- Passed: {str(calibration.passed).lower()}",
            f"- Matched labels: {calibration.matched_label_count}",
            f"- Precision: {calibration.precision:.2f}",
            f"- Recall: {calibration.recall:.2f}",
            f"- Failed reasons: {', '.join(calibration.failed_reasons) or 'none'}",
            "",
        )
    )
    path.write_text(body, encoding="utf-8")


def _write_source_decision(path: Path, decision: SourceDecision) -> None:
    path.write_text(
        json.dumps(decision.to_json_object(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_action_plan(path: Path, decision: SourceDecision, gate: GateResult) -> None:
    body = "\n".join(
        (
            "# Action Plan",
            "",
            f"- Decision: {decision.decision.value}",
            f"- Valid comments: {gate.valid_comment_count}",
            f"- Warm/hot comments: {gate.warm_hot_count}",
            f"- Direct-buyer rate: {gate.direct_buyer_rate:.1%}",
            f"- Noise rate: {gate.noise_rate:.1%}",
            f"- Next action: {_next_action(decision)}",
            "",
        )
    )
    path.write_text(body, encoding="utf-8")


def _write_source_report(
    path: Path,
    ranked_comments: tuple[RankedComment, ...],
    gate: GateResult,
    calibration: CalibrationReport,
    decision: SourceDecision,
    *,
    rejected_count: int,
    duplicate_count: int,
) -> None:
    hot_count = sum(1 for item in ranked_comments if item.rank_level is RankLevel.HOT)
    warm_count = sum(1 for item in ranked_comments if item.rank_level is RankLevel.WARM)
    watch_count = sum(1 for item in ranked_comments if item.rank_level is RankLevel.WATCH)
    body = "\n".join(
        (
            "# Opportunity Source Experiment Report",
            "",
            "## Summary",
            "",
            f"- Gate passed: {str(gate.passed).lower()}",
            f"- Source decision: {decision.decision.value}",
            f"- Valid comments: {gate.valid_comment_count}",
            f"- Rejected import rows: {rejected_count}",
            f"- Duplicate comments removed: {duplicate_count}",
            f"- Hot: {hot_count}",
            f"- Warm: {warm_count}",
            f"- Watch: {watch_count}",
            f"- Warm/hot per 100: {gate.warm_hot_per_100:.1f}",
            f"- Direct-buyer rate: {gate.direct_buyer_rate:.1%}",
            f"- Noise rate: {gate.noise_rate:.1%}",
            "",
            "## Gate",
            "",
            f"- Failed reasons: {', '.join(gate.failed_reasons) or 'none'}",
            "",
            "## Calibration",
            "",
            f"- Passed: {str(calibration.passed).lower()}",
            f"- Failed reasons: {', '.join(calibration.failed_reasons) or 'none'}",
            "",
            "## Review Queue",
            "",
            f"- Rows exported: {len(review_queue_rows(ranked_comments))}",
            "",
        )
    )
    path.write_text(body, encoding="utf-8")


def _append_run_history(
    path: Path,
    *,
    run_id: str,
    comments_csv_path: Path,
    artifacts: ExperimentArtifacts,
    gate: GateResult,
    calibration: CalibrationReport,
    decision: SourceDecision,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "comments_csv_path": str(comments_csv_path),
        "output_dir": str(artifacts.output_dir),
        "gate": gate.to_json_object(),
        "calibration": {
            "passed": calibration.passed,
            "matched_label_count": calibration.matched_label_count,
            "precision": calibration.precision,
            "recall": calibration.recall,
            "failed_reasons": list(calibration.failed_reasons),
        },
        "source_decision": decision.to_json_object(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _next_action(decision: SourceDecision) -> str:
    actions = {
        SourceDecisionKind.PROMOTE: "Promote this calibrated source configuration.",
        SourceDecisionKind.LABEL_MORE: "Fill the calibration template with human labels.",
        SourceDecisionKind.TIGHTEN_FALSE_POSITIVES: (
            "Narrow query patterns and review rejected noise."
        ),
        SourceDecisionKind.RECOVER_FALSE_NEGATIVES: (
            "Review missed warm examples and update patterns."
        ),
        SourceDecisionKind.COLLECT_MORE: "Collect another actual-comment batch.",
        SourceDecisionKind.REPLACE: "Replace the source or query set.",
        SourceDecisionKind.USE_HIGHER_CONTROL_FEED: (
            "Use an actual-comment provider or manual feed."
        ),
    }
    return actions[decision.decision]
