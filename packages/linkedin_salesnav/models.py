"""Data models shared by Sales Navigator browser primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class CandidateIdentity:
    name: str
    profile_url: str
    candidate_id: str | None = None
    sales_profile_urn: str | None = None

    @property
    def stable_id(self) -> str:
        return self.candidate_id or self.profile_url


@dataclass(frozen=True)
class MenuLabel:
    index: int
    text: str | None = None
    aria: str | None = None
    tag: str | None = None
    href: str | None = None
    disabled: bool = False


@dataclass(frozen=True)
class SalesNavCaptureRow:
    index: int
    name: str | None
    profile_url: str | None
    menu_state: str
    page_number: int | None = None
    global_index: int | None = None
    scroll_urn: str | None = None
    visible_saved: bool | None = None
    visible_viewed: bool | None = None
    visible_has_message: bool | None = None
    visible_has_save: bool | None = None
    api_state: Mapping[str, object] | None = None
    menu_labels: tuple[MenuLabel, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SalesNavCaptureArtifact:
    schema_version: int
    captured_at: datetime | None
    url: str
    resume_url: str | None
    source: str | None
    menu_inspection: str | None
    raw_row_count: int
    output_row_count: int
    rows: tuple[SalesNavCaptureRow, ...]
    api_state: Mapping[str, object] | None = None
