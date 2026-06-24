"""Shared source experiment gate evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import SourceExperimentMetrics


@dataclass(frozen=True)
class SourceGateThresholds:
    """Default opportunity-intel source gate thresholds from the PRD notes."""

    min_valid_comments: int = 100
    min_warm_hot_per_100: float = 3.0
    min_warm_hot_total: int = 20
    min_direct_buyer_rate: float = 0.08
    max_noise_rate: float = 0.65
    require_evidence_fields: bool = True


@dataclass(frozen=True)
class SourceGateResult:
    """Decision and reasons for one source experiment gate."""

    passed: bool
    reasons: tuple[str, ...]


DEFAULT_SOURCE_GATE_THRESHOLDS = SourceGateThresholds()


def evaluate_source_gate(
    metrics: SourceExperimentMetrics,
    thresholds: SourceGateThresholds = DEFAULT_SOURCE_GATE_THRESHOLDS,
    *,
    evidence_fields_complete: bool = True,
) -> SourceGateResult:
    """Evaluate source metrics against shared promotion gates."""

    reasons: list[str] = []
    if metrics.valid_comments < thresholds.min_valid_comments:
        reasons.append("valid_comment_volume_below_threshold")
    if metrics.warm_hot_count < thresholds.min_warm_hot_total:
        reasons.append("warm_hot_total_below_threshold")
    if metrics.warm_hot_per_100 < thresholds.min_warm_hot_per_100:
        reasons.append("warm_hot_rate_below_threshold")
    if metrics.direct_buyer_rate < thresholds.min_direct_buyer_rate:
        reasons.append("direct_buyer_rate_below_threshold")
    if metrics.noise_rate > thresholds.max_noise_rate:
        reasons.append("noise_rate_above_threshold")
    if thresholds.require_evidence_fields and not evidence_fields_complete:
        reasons.append("required_evidence_fields_missing")
    return SourceGateResult(passed=not reasons, reasons=tuple(reasons))
