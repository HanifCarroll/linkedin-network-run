"""Shared source experiment metric helpers."""

from __future__ import annotations

from dataclasses import dataclass


def rate(numerator: int, denominator: int) -> float:
    """Return ``numerator / denominator`` with zero-denominator protection."""

    if denominator <= 0:
        return 0.0
    return numerator / denominator


def per_100(numerator: int, denominator: int) -> float:
    """Return a per-100 rate with zero-denominator protection."""

    return rate(numerator, denominator) * 100.0


@dataclass(frozen=True)
class SourceExperimentMetrics:
    """Core source experiment metrics shared by reports and gates."""

    posts_discovered: int = 0
    posts_extracted: int = 0
    raw_comments: int = 0
    valid_comments: int = 0
    qualified_comments: int = 0
    direct_buyer_comments: int = 0
    noise_comments: int = 0
    warm_hot_count: int = 0
    review_queue_count: int = 0

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

    @property
    def qualified_per_100(self) -> float:
        return per_100(self.qualified_comments, self.valid_comments)

    @property
    def direct_buyer_rate(self) -> float:
        return rate(self.direct_buyer_comments, self.valid_comments)

    @property
    def noise_rate(self) -> float:
        return rate(self.noise_comments, self.valid_comments)

    @property
    def warm_hot_per_100(self) -> float:
        return per_100(self.warm_hot_count, self.valid_comments)
