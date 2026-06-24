"""Contracts for extracting comments from known LinkedIn post pages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from apps.opportunity_intel.contracts import CommentEvidence

RAW_COMMENTS_FILENAME = "raw_comments.jsonl"


@dataclass(frozen=True)
class PostHTMLInput:
    post_url: str
    html_path: Path
    source_id: str
    query_id: str
    source_kind: str = "known_post"
    source_url: str = ""
    search_query: str = ""


@dataclass(frozen=True)
class ExtractionResult:
    comments: tuple[CommentEvidence, ...]
    warnings: tuple[str, ...]
