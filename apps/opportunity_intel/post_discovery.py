"""Post discovery and prioritization interfaces."""

from __future__ import annotations

from dataclasses import dataclass

from apps.opportunity_intel.contracts import (
    CommentEvidence,
    SourceDefinition,
    SourceKind,
    SourceRegistry,
)


@dataclass(frozen=True)
class PostCandidate:
    source_id: str
    source_kind: str
    query_id: str
    post_url: str
    source_url: str
    search_query: str
    priority: int
    reason: str


def discover_posts_from_registry(registry: SourceRegistry) -> tuple[PostCandidate, ...]:
    candidates: list[PostCandidate] = []
    for source in registry.enabled_sources():
        candidates.extend(_source_candidates(source))
    return prioritize_posts(tuple(candidates))


def discover_posts_from_comments(
    comments: tuple[CommentEvidence, ...],
) -> tuple[PostCandidate, ...]:
    candidates: list[PostCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for comment in comments:
        key = (comment.source_id, comment.query_id, comment.post_url)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            PostCandidate(
                source_id=comment.source_id,
                source_kind=comment.source_kind,
                query_id=comment.query_id,
                post_url=comment.post_url,
                source_url=comment.source_url,
                search_query=comment.search_query,
                priority=0,
                reason="actual_comment_import",
            )
        )
    return prioritize_posts(tuple(candidates))


def prioritize_posts(candidates: tuple[PostCandidate, ...]) -> tuple[PostCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -candidate.priority,
                candidate.source_id,
                candidate.query_id,
                candidate.post_url,
            ),
        )
    )


def _source_candidates(source: SourceDefinition) -> tuple[PostCandidate, ...]:
    query_ids = source.query_ids or ("unassigned",)
    candidates: list[PostCandidate] = []
    if source.source_kind is SourceKind.KNOWN_POST:
        for post_url in source.urls:
            for query_id in query_ids:
                candidates.append(
                    PostCandidate(
                        source_id=source.source_id,
                        source_kind=source.source_kind.value,
                        query_id=query_id,
                        post_url=post_url,
                        source_url=post_url,
                        search_query="",
                        priority=source.priority,
                        reason="known_post_url",
                    )
                )
    if source.source_kind is SourceKind.LINKEDIN_SEARCH:
        for search_query in source.search_queries:
            for query_id in query_ids:
                candidates.append(
                    PostCandidate(
                        source_id=source.source_id,
                        source_kind=source.source_kind.value,
                        query_id=query_id,
                        post_url="",
                        source_url="",
                        search_query=search_query,
                        priority=source.priority,
                        reason="search_query",
                    )
                )
    return tuple(candidates)
