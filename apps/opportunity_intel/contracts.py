"""Contracts for recommend-only opportunity intelligence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

SOURCE_REGISTRY_CONTRACT_VERSION = "opportunity-source-registry.v1"
QUERY_PACK_CONTRACT_VERSION = "opportunity-comment-signal-queries.v1"
RAW_COMMENTS_CONTRACT_VERSION = "raw_comments.v1"

CANONICAL_COMMENT_COLUMNS: tuple[str, ...] = (
    "query_id",
    "source_id",
    "source_kind",
    "source_url",
    "search_query",
    "post_url",
    "post_author_name",
    "post_text",
    "comment_id",
    "comment_url",
    "commenter_name",
    "commenter_profile_url",
    "commenter_headline",
    "commenter_company",
    "relationship",
    "comment_text",
    "commented_at",
)

PROVIDER_COLUMN_ALIASES: dict[str, str] = {
    "query": "query_id",
    "queryid": "query_id",
    "source": "source_id",
    "sourceid": "source_id",
    "source_type": "source_kind",
    "sourcekind": "source_kind",
    "url": "post_url",
    "post": "post_url",
    "linkedin_post_url": "post_url",
    "activity_url": "post_url",
    "author": "post_author_name",
    "post_author": "post_author_name",
    "post_body": "post_text",
    "post_content": "post_text",
    "comment": "comment_text",
    "text": "comment_text",
    "comment_body": "comment_text",
    "commenter": "commenter_name",
    "name": "commenter_name",
    "profile_url": "commenter_profile_url",
    "linkedin_profile_url": "commenter_profile_url",
    "profile": "commenter_profile_url",
    "headline": "commenter_headline",
    "company": "commenter_company",
    "date": "commented_at",
    "timestamp": "commented_at",
    "comment_date": "commented_at",
    "permalink": "comment_url",
}


class OpportunityContractError(ValueError):
    """Raised when an opportunity-intel contract is violated."""


class SourceKind(StrEnum):
    LINKEDIN_SEARCH = "linkedin_search"
    KNOWN_POST = "known_post"
    MANUAL_CSV = "manual_csv"
    PROVIDER_CSV = "provider_csv"
    WATCHLIST = "watchlist"


class RankLevel(StrEnum):
    HOT = "hot"
    WARM = "warm"
    WATCH = "watch"
    REJECT = "reject"


class SourceDecisionKind(StrEnum):
    PROMOTE = "promote_passing_calibrated_configuration"
    LABEL_MORE = "label_more_before_promotion"
    TIGHTEN_FALSE_POSITIVES = "tighten_false_positives"
    RECOVER_FALSE_NEGATIVES = "recover_false_negatives"
    COLLECT_MORE = "collect_more"
    REPLACE = "replace_source"
    USE_HIGHER_CONTROL_FEED = "use_higher_control_actual_comment_feed"


type JsonObject = dict[str, object]


@dataclass(frozen=True)
class QueryDefinition:
    query_id: str
    title: str
    search_queries: tuple[str, ...]
    comment_text_patterns: tuple[str, ...]
    need_categories: tuple[str, ...]
    source_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: JsonObject) -> QueryDefinition:
        return cls(
            query_id=_required_string(payload, "query_id"),
            title=_required_string(payload, "title"),
            search_queries=_tuple_of_strings(payload.get("search_queries", ())),
            comment_text_patterns=_tuple_of_strings(payload.get("comment_text_patterns", ())),
            need_categories=_tuple_of_strings(payload.get("need_categories", ())),
            source_ids=_tuple_of_strings(payload.get("source_ids", ())),
        )


@dataclass(frozen=True)
class QueryPack:
    contract_version: str
    queries: tuple[QueryDefinition, ...]

    def require_query(self, query_id: str) -> QueryDefinition:
        for query in self.queries:
            if query.query_id == query_id:
                return query
        raise OpportunityContractError(f"unknown query_id: {query_id}")


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    source_kind: SourceKind
    title: str
    description: str
    query_ids: tuple[str, ...]
    urls: tuple[str, ...] = ()
    search_queries: tuple[str, ...] = ()
    priority: int = 0
    enabled: bool = True

    @classmethod
    def from_mapping(cls, payload: JsonObject) -> SourceDefinition:
        return cls(
            source_id=_required_string(payload, "source_id"),
            source_kind=SourceKind(_required_string(payload, "source_kind")),
            title=_required_string(payload, "title"),
            description=_optional_string(payload.get("description")),
            query_ids=_tuple_of_strings(payload.get("query_ids", ())),
            urls=_tuple_of_strings(payload.get("urls", ())),
            search_queries=_tuple_of_strings(payload.get("search_queries", ())),
            priority=_optional_int(payload.get("priority")),
            enabled=_optional_bool(payload.get("enabled"), default=True),
        )


@dataclass(frozen=True)
class SourceRegistry:
    contract_version: str
    sources: tuple[SourceDefinition, ...]

    def enabled_sources(self) -> tuple[SourceDefinition, ...]:
        return tuple(source for source in self.sources if source.enabled)

    def require_source(self, source_id: str) -> SourceDefinition:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise OpportunityContractError(f"unknown source_id: {source_id}")


@dataclass(frozen=True)
class CommentEvidence:
    query_id: str
    source_id: str
    source_kind: str
    source_url: str
    search_query: str
    post_url: str
    post_author_name: str
    post_text: str
    comment_id: str
    comment_url: str
    commenter_name: str
    commenter_profile_url: str
    commenter_headline: str
    commenter_company: str
    relationship: str
    comment_text: str
    commented_at: str
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: dict[str, str], warnings: tuple[str, ...] = ()) -> CommentEvidence:
        values = {column: clean_cell(row.get(column, "")) for column in CANONICAL_COMMENT_COLUMNS}
        return cls(
            query_id=values["query_id"],
            source_id=values["source_id"],
            source_kind=values["source_kind"],
            source_url=values["source_url"],
            search_query=values["search_query"],
            post_url=values["post_url"],
            post_author_name=values["post_author_name"],
            post_text=values["post_text"],
            comment_id=values["comment_id"],
            comment_url=values["comment_url"],
            commenter_name=values["commenter_name"],
            commenter_profile_url=values["commenter_profile_url"],
            commenter_headline=values["commenter_headline"],
            commenter_company=values["commenter_company"],
            relationship=values["relationship"],
            comment_text=values["comment_text"],
            commented_at=values["commented_at"],
            warnings=warnings,
        )

    def to_row(self) -> dict[str, str]:
        return {column: str(getattr(self, column)) for column in CANONICAL_COMMENT_COLUMNS}

    def to_json_object(self) -> dict[str, object]:
        payload: dict[str, object] = dict(self.to_row())
        payload["contract_version"] = RAW_COMMENTS_CONTRACT_VERSION
        payload["warnings"] = list(self.warnings)
        return payload


@dataclass(frozen=True)
class ValidationIssue:
    row_number: int
    field: str
    message: str


@dataclass(frozen=True)
class ImportResult:
    valid_comments: tuple[CommentEvidence, ...]
    rejected_rows: tuple[ValidationIssue, ...]


@dataclass(frozen=True)
class DedupeResult:
    comments: tuple[CommentEvidence, ...]
    duplicate_count: int


@dataclass(frozen=True)
class RankedComment:
    comment: CommentEvidence
    rank_level: RankLevel
    rank_points: int
    direct_buyer: bool
    need_categories: tuple[str, ...]
    positive_signals: tuple[str, ...]
    fit_reasons: tuple[str, ...]
    reject_reasons: tuple[str, ...]
    evidence_quote: str

    def review_row(self) -> dict[str, str]:
        return {
            "rank_level": self.rank_level.value,
            "rank_points": str(self.rank_points),
            "direct_buyer": str(self.direct_buyer).lower(),
            "query_id": self.comment.query_id,
            "source_id": self.comment.source_id,
            "post_url": self.comment.post_url,
            "commenter_name": self.comment.commenter_name,
            "commenter_profile_url": self.comment.commenter_profile_url,
            "commenter_headline": self.comment.commenter_headline,
            "commenter_company": self.comment.commenter_company,
            "comment_text": self.comment.comment_text,
            "need_categories": ";".join(self.need_categories),
            "positive_signals": ";".join(self.positive_signals),
            "fit_reasons": ";".join(self.fit_reasons),
            "reject_reasons": ";".join(self.reject_reasons),
            "evidence_quote": self.evidence_quote,
        }


@dataclass(frozen=True)
class GateThresholds:
    min_valid_comments: int = 100
    min_warm_hot_per_100: int = 3
    min_warm_hot_total: int = 20
    min_direct_buyer_rate: float = 0.08
    max_noise_rate: float = 0.65
    require_evidence_fields: bool = True


@dataclass(frozen=True)
class GateResult:
    passed: bool
    valid_comment_count: int
    warm_hot_count: int
    direct_buyer_count: int
    noise_count: int
    warm_hot_per_100: float
    direct_buyer_rate: float
    noise_rate: float
    failed_reasons: tuple[str, ...]
    thresholds: GateThresholds = field(default_factory=GateThresholds)

    def to_json_object(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "valid_comment_count": self.valid_comment_count,
            "warm_hot_count": self.warm_hot_count,
            "direct_buyer_count": self.direct_buyer_count,
            "noise_count": self.noise_count,
            "warm_hot_per_100": self.warm_hot_per_100,
            "direct_buyer_rate": self.direct_buyer_rate,
            "noise_rate": self.noise_rate,
            "failed_reasons": list(self.failed_reasons),
            "thresholds": {
                "min_valid_comments": self.thresholds.min_valid_comments,
                "min_warm_hot_per_100": self.thresholds.min_warm_hot_per_100,
                "min_warm_hot_total": self.thresholds.min_warm_hot_total,
                "min_direct_buyer_rate": self.thresholds.min_direct_buyer_rate,
                "max_noise_rate": self.thresholds.max_noise_rate,
                "require_evidence_fields": self.thresholds.require_evidence_fields,
            },
        }


@dataclass(frozen=True)
class CalibrationThresholds:
    min_labels_overall: int = 20
    min_labels_per_source_query: int = 5
    min_precision: float = 0.70
    min_recall: float = 0.60


@dataclass(frozen=True)
class CalibrationReport:
    passed: bool
    matched_label_count: int
    precision: float
    recall: float
    failed_reasons: tuple[str, ...]
    thresholds: CalibrationThresholds = field(default_factory=CalibrationThresholds)


@dataclass(frozen=True)
class SourceDecision:
    decision: SourceDecisionKind
    reasons: tuple[str, ...]

    def to_json_object(self) -> dict[str, object]:
        return {"decision": self.decision.value, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class ExperimentArtifacts:
    output_dir: Path
    source_report_path: Path
    gate_path: Path
    calibration_template_path: Path
    calibration_report_path: Path
    source_decision_path: Path
    action_plan_path: Path
    run_history_path: Path
    review_queue_csv_path: Path
    review_queue_jsonl_path: Path


def clean_cell(value: str) -> str:
    return value.replace("\u00a0", " ").strip()


def has_linkedin_host(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc.endswith("linkedin.com")


def has_linkedin_profile_path(url: str) -> bool:
    parsed = urlparse(url)
    return has_linkedin_host(url) and parsed.path.startswith("/in/")


def _required_string(payload: JsonObject, field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise OpportunityContractError(f"{field_name} is required")
    return value.strip()


def _optional_string(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise OpportunityContractError("expected string")
    return value.strip()


def _tuple_of_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise OpportunityContractError("expected list of strings")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise OpportunityContractError("expected list of strings")
        if item.strip():
            strings.append(item.strip())
    return tuple(strings)


def _optional_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise OpportunityContractError("expected integer")
    return value


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise OpportunityContractError("expected boolean")
    return value
