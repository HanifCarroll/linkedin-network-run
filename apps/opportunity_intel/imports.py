"""Provider and manual CSV import contract for actual comment rows."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from apps.opportunity_intel.contracts import (
    CANONICAL_COMMENT_COLUMNS,
    PROVIDER_COLUMN_ALIASES,
    CommentEvidence,
    ImportResult,
    OpportunityContractError,
    QueryPack,
    ValidationIssue,
    clean_cell,
    has_linkedin_host,
    has_linkedin_profile_path,
)


def read_comment_csv(path: Path, query_pack: QueryPack) -> ImportResult:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise OpportunityContractError("comment CSV requires a header row")
        normalized_headers = normalize_headers(reader.fieldnames)
        valid: list[CommentEvidence] = []
        rejected: list[ValidationIssue] = []
        for row_number, raw_row in enumerate(reader, start=2):
            canonical_row = normalize_provider_row(raw_row, normalized_headers)
            row_issues = validate_comment_row(canonical_row, query_pack, row_number=row_number)
            if row_issues:
                rejected.extend(row_issues)
                continue
            valid.append(CommentEvidence.from_row(canonical_row))
    return ImportResult(valid_comments=tuple(valid), rejected_rows=tuple(rejected))


def write_comment_csv(path: Path, comments: Sequence[CommentEvidence]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COMMENT_COLUMNS)
        writer.writeheader()
        for comment in comments:
            writer.writerow(comment.to_row())
            count += 1
    return count


def normalize_headers(fieldnames: Sequence[str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for fieldname in fieldnames:
        key = _header_key(fieldname)
        canonical = PROVIDER_COLUMN_ALIASES.get(key, key)
        if canonical in CANONICAL_COMMENT_COLUMNS:
            normalized[fieldname] = canonical
    return normalized


def normalize_provider_row(
    raw_row: dict[str, str | None], headers: dict[str, str]
) -> dict[str, str]:
    row = {column: "" for column in CANONICAL_COMMENT_COLUMNS}
    for original_name, canonical_name in headers.items():
        value = raw_row.get(original_name)
        row[canonical_name] = clean_cell(value or "")
    row["post_author_name"] = _clean_adjacent_duplicate_author(row["post_author_name"])
    return row


def validate_comment_row(
    row: dict[str, str], query_pack: QueryPack, *, row_number: int
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    required_fields = (
        "query_id",
        "source_id",
        "source_kind",
        "post_url",
        "commenter_name",
        "commenter_profile_url",
        "comment_text",
    )
    for field_name in required_fields:
        if not row[field_name]:
            issues.append(ValidationIssue(row_number, field_name, "required actual-comment field"))
    if row["post_url"] and not has_linkedin_host(row["post_url"]):
        issues.append(ValidationIssue(row_number, "post_url", "must be a LinkedIn URL"))
    if row["commenter_profile_url"] and not has_linkedin_profile_path(row["commenter_profile_url"]):
        issues.append(
            ValidationIssue(
                row_number,
                "commenter_profile_url",
                "must be a LinkedIn person profile URL",
            )
        )
    if row["query_id"]:
        try:
            query = query_pack.require_query(row["query_id"])
        except OpportunityContractError as exc:
            issues.append(ValidationIssue(row_number, "query_id", str(exc)))
        else:
            if query.comment_text_patterns and not _matches_any_pattern(
                row["comment_text"], query.comment_text_patterns
            ):
                issues.append(
                    ValidationIssue(
                        row_number,
                        "comment_text",
                        "must match one configured comment_text_patterns entry",
                    )
                )
    return tuple(issues)


def _matches_any_pattern(comment_text: str, patterns: tuple[str, ...]) -> bool:
    text = comment_text.casefold()
    return any(pattern.casefold() in text for pattern in patterns)


def _header_key(fieldname: str) -> str:
    return fieldname.strip().casefold().replace(" ", "_").replace("-", "_")


def _clean_adjacent_duplicate_author(value: str) -> str:
    midpoint = len(value) // 2
    if value and len(value) % 2 == 0 and value[:midpoint] == value[midpoint:]:
        return value[:midpoint]
    return value
