from __future__ import annotations

import pytest

from packages.linkedin_experiments import (
    SourceExperimentMetrics,
    SourceGateThresholds,
    evaluate_source_gate,
)


def test_source_experiment_metrics_calculate_rates() -> None:
    metrics = SourceExperimentMetrics(
        valid_comments=200,
        qualified_comments=12,
        direct_buyer_comments=20,
        noise_comments=50,
        warm_hot_count=10,
    )

    assert metrics.qualified_per_100 == 6.0
    assert metrics.direct_buyer_rate == 0.1
    assert metrics.noise_rate == 0.25
    assert metrics.warm_hot_per_100 == 5.0


def test_source_gate_reports_blocking_reasons() -> None:
    result = evaluate_source_gate(SourceExperimentMetrics(valid_comments=10))

    assert not result.passed
    assert "valid_comment_volume_below_threshold" in result.reasons


def test_source_gate_passes_when_thresholds_are_met() -> None:
    metrics = SourceExperimentMetrics(
        valid_comments=200,
        direct_buyer_comments=20,
        noise_comments=50,
        warm_hot_count=20,
    )

    result = evaluate_source_gate(metrics, SourceGateThresholds())

    assert result.passed
    assert result.reasons == ()


def test_source_metrics_reject_negative_counts() -> None:
    with pytest.raises(ValueError):
        SourceExperimentMetrics(valid_comments=-1)
