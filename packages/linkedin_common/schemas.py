"""Pydantic data contracts shared across the LinkedIn tools."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import AppNamespace
from .urls import canonicalize_linkedin_post_url, canonicalize_sales_profile_url


class StrictModel(BaseModel):
    """Base model for source-faithful shared contracts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunStatus(StrEnum):
    """Common run lifecycle states."""

    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"


class WarningRecord(StrictModel):
    """Structured warning attached to capture, import, or report records."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    field_name: str | None = None


class SourceAttribution(StrictModel):
    """Source/query attribution carried through raw and normalized records."""

    source_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    source_url: str | None = None
    query_id: str | None = None
    search_query: str | None = None


class SourceRecord(StrictModel):
    """Versioned source registry record."""

    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    url: str | None = None
    hypothesis: str = Field(min_length=1)
    target_needs: list[str] = Field(default_factory=list)
    priority: int = Field(ge=0)
    enabled: bool = True
    safety_notes: str = ""
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class CaptureArtifact(StrictModel):
    """Filesystem or URL pointer to a raw artifact created during capture."""

    kind: str = Field(min_length=1)
    path: str = Field(min_length=1)
    created_at: datetime | None = None
    sha256: str | None = None


class CaptureRecord(StrictModel):
    """Base capture record for raw streams before app-specific normalization."""

    record_id: str = Field(min_length=1)
    source: SourceAttribution
    captured_at: datetime
    raw_ref: str | None = None
    artifacts: list[CaptureArtifact] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)


class PostRecord(StrictModel):
    """Shared post queue contract for comment extraction and opportunity intel."""

    post_id: str = Field(min_length=1)
    post_url: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    post_author_name: str | None = None
    post_author_url: str | None = None
    post_text: str | None = None
    discovered_at: datetime
    priority_score: float = Field(ge=0)
    priority_reasons: list[str] = Field(default_factory=list)

    @field_validator("post_url")
    @classmethod
    def canonical_post_url(cls, value: str) -> str:
        return canonicalize_linkedin_post_url(value)


class CommentRecord(StrictModel):
    """Raw or normalized LinkedIn comment row with required proof fields."""

    post_url: str = Field(min_length=1)
    comment_text: str = Field(min_length=1)
    commenter_name: str = Field(min_length=1)
    commenter_profile_url: str = Field(min_length=1)
    comment_id: str | None = None
    comment_url: str | None = None
    commenter_headline: str | None = None
    commenter_company: str | None = None
    relationship: str | None = None
    commented_at: datetime | None = None
    post_author_name: str | None = None
    post_text: str | None = None
    source: SourceAttribution | None = None
    capture_artifacts: list[CaptureArtifact] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)

    @field_validator("post_url")
    @classmethod
    def canonical_comment_post_url(cls, value: str) -> str:
        return canonicalize_linkedin_post_url(value)

    @field_validator("commenter_profile_url")
    @classmethod
    def canonical_commenter_profile_url(cls, value: str) -> str:
        return canonicalize_sales_profile_url(value)


class SalesNavLeadRef(StrictModel):
    """Shared Sales Navigator lead identity reference."""

    name: str = Field(min_length=1)
    profile_url: str = Field(min_length=1)
    sales_profile_urn: str | None = None
    source: SourceAttribution | None = None

    @field_validator("profile_url")
    @classmethod
    def canonical_sales_nav_url(cls, value: str) -> str:
        return canonicalize_sales_profile_url(value)


class RunManifest(StrictModel):
    """Machine-readable manifest for an app run or batch."""

    run_id: str = Field(min_length=1)
    namespace: AppNamespace
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    inputs: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    artifacts: list[CaptureArtifact] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)
