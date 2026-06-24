"""Normalizing and deduplicating actual comment evidence."""

from __future__ import annotations

from apps.opportunity_intel.contracts import CommentEvidence, DedupeResult


def normalize_and_dedupe(comments: tuple[CommentEvidence, ...]) -> DedupeResult:
    seen: set[tuple[str, str, str, str]] = set()
    unique_comments: list[CommentEvidence] = []
    duplicate_count = 0
    for comment in comments:
        normalized = _normalize_comment(comment)
        key = _dedupe_key(normalized)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        unique_comments.append(normalized)
    return DedupeResult(comments=tuple(unique_comments), duplicate_count=duplicate_count)


def _normalize_comment(comment: CommentEvidence) -> CommentEvidence:
    return CommentEvidence(
        query_id=comment.query_id.strip(),
        source_id=comment.source_id.strip(),
        source_kind=comment.source_kind.strip(),
        source_url=comment.source_url.strip(),
        search_query=comment.search_query.strip(),
        post_url=comment.post_url.strip(),
        post_author_name=comment.post_author_name.strip(),
        post_text=comment.post_text.strip(),
        comment_id=comment.comment_id.strip(),
        comment_url=comment.comment_url.strip(),
        commenter_name=comment.commenter_name.strip(),
        commenter_profile_url=comment.commenter_profile_url.strip(),
        commenter_headline=comment.commenter_headline.strip(),
        commenter_company=comment.commenter_company.strip(),
        relationship=comment.relationship.strip(),
        comment_text=comment.comment_text.strip(),
        commented_at=comment.commented_at.strip(),
        warnings=comment.warnings,
    )


def _dedupe_key(comment: CommentEvidence) -> tuple[str, str, str, str]:
    if comment.comment_id:
        return (comment.post_url.casefold(), comment.comment_id.casefold(), "", "")
    return (
        comment.post_url.casefold(),
        comment.commenter_profile_url.casefold(),
        comment.comment_text.casefold(),
        comment.commented_at.casefold(),
    )
