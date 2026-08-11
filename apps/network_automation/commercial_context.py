"""Strict commercial-profile and qualification-evidence validation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from .models import (
    DEFAULT_COMMERCIAL_OFFER_ID,
    DEFAULT_ICP_SOURCE_PATH,
    DEFAULT_OFFERS_SOURCE_PATH,
    CommercialContextReference,
    CommercialCriterionAssessment,
    CommercialCriterionEvidence,
    RelationshipEnrichmentArtifact,
    RelationshipEnrichmentEvidence,
)


def validate_commercial_context_sources(
    context: CommercialContextReference,
) -> tuple[str, ...]:
    if context.offer_id != DEFAULT_COMMERCIAL_OFFER_ID:
        raise ValueError(
            "commercial context offer id mismatch: "
            f"expected {DEFAULT_COMMERCIAL_OFFER_ID}, got {context.offer_id}"
        )
    profiles = (
        (
            "ICP",
            context.icp_source_path,
            context.icp_source_sha256,
            DEFAULT_ICP_SOURCE_PATH,
        ),
        (
            "offers",
            context.offers_source_path,
            context.offers_source_sha256,
            DEFAULT_OFFERS_SOURCE_PATH,
        ),
    )
    qualification_criterion_ids: tuple[str, ...] | None = None
    for label, source_path, source_sha256, expected_path in profiles:
        if source_path != expected_path:
            raise ValueError(
                f"commercial context {label} source path mismatch: "
                f"expected {expected_path}, got {source_path}"
            )
        path = Path(source_path)
        if not path.is_file():
            raise FileNotFoundError(f"commercial context {label} source is missing: {path}")
        frontmatter = read_required_profile_frontmatter(path, label=label)
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if source_sha256 != actual_sha256:
            raise ValueError(
                f"commercial context {label} source digest mismatch: "
                f"expected {actual_sha256}, context has {source_sha256}"
            )
        if frontmatter["status"] != "active":
            raise ValueError(
                f"commercial context {label} profile is not active: "
                f"{frontmatter['status']} in {path}"
            )
        if label == "ICP":
            qualification_criterion_ids = read_qualification_contract_ids(path)
        if label == "offers":
            offer_catalog = read_offer_catalog(path)
            offer_status = offer_catalog.get(context.offer_id)
            if offer_status is None:
                raise ValueError(
                    f"commercial context offer {context.offer_id} is missing from "
                    f"the Offers Offer Catalog: {path}"
                )
            if offer_status != "active":
                raise ValueError(
                    f"commercial context offer {context.offer_id} is not active: "
                    f"{offer_status} in {path}"
                )
    if qualification_criterion_ids is None:
        raise ValueError("commercial context ICP qualification contract was not loaded")
    return qualification_criterion_ids


def read_required_profile_frontmatter(path: Path, *, label: str) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"commercial context {label} has no opening frontmatter: {path}")
    try:
        closing_index = lines.index("---", 1)
    except ValueError as error:
        raise ValueError(
            f"commercial context {label} has unterminated frontmatter: {path}"
        ) from error

    required = {"status"}
    values: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:closing_index], start=2):
        for key in required:
            prefix = f"{key}:"
            if not line.startswith(prefix):
                continue
            if key in values:
                raise ValueError(
                    f"commercial context {label} has duplicate {key} at "
                    f"{path}:{line_number}"
                )
            value = line[len(prefix) :].strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
                raise ValueError(
                    f"commercial context {label} has malformed {key} at "
                    f"{path}:{line_number}"
                )
            values[key] = value
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError(
            f"commercial context {label} frontmatter is missing "
            f"{', '.join(missing)}: {path}"
        )
    return values


def read_qualification_contract_ids(path: Path) -> tuple[str, ...]:
    lines = path.read_text(encoding="utf-8").splitlines()
    heading = "## Qualification Contract"
    heading_indexes = [index for index, line in enumerate(lines) if line == heading]
    if len(heading_indexes) != 1:
        raise ValueError(
            "commercial context ICP must contain exactly one "
            f"{heading!r} section: {path}"
        )
    header = (
        "| Criterion ID | Qualification question | Evidence that can support a match |"
    )
    separator = "| --- | --- | --- |"
    section_start = heading_indexes[0] + 1
    section_end = next(
        (
            index
            for index in range(section_start, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    table_header_indexes = [
        index for index in range(section_start, section_end) if lines[index] == header
    ]
    if len(table_header_indexes) != 1:
        raise ValueError(
            "commercial context ICP qualification table must contain exactly one "
            f"expected header: {path}"
        )
    header_index = table_header_indexes[0]
    if header_index + 1 >= len(lines) or lines[header_index + 1] != separator:
        raise ValueError(
            f"commercial context ICP qualification table separator is malformed: {path}"
        )

    criterion_ids: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines[header_index + 2 :], start=header_index + 3):
        if not line:
            break
        if not (line.startswith("|") and line.endswith("|")):
            raise ValueError(
                "commercial context ICP qualification table row is malformed at "
                f"{path}:{line_number}"
            )
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 3 or not cells[1] or not cells[2]:
            raise ValueError(
                "commercial context ICP qualification table row is malformed at "
                f"{path}:{line_number}"
            )
        match = re.fullmatch(r"`([a-z0-9][a-z0-9._-]*)`", cells[0])
        if match is None:
            raise ValueError(
                "commercial context ICP criterion ID is malformed at "
                f"{path}:{line_number}"
            )
        criterion_id = match.group(1)
        if criterion_id in seen:
            raise ValueError(
                f"commercial context ICP has duplicate criterion ID {criterion_id}: {path}"
            )
        seen.add(criterion_id)
        criterion_ids.append(criterion_id)
    if not criterion_ids:
        raise ValueError(f"commercial context ICP qualification table has no criteria: {path}")
    return tuple(criterion_ids)


def read_offer_catalog(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    heading = "## Offer Catalog"
    heading_indexes = [index for index, line in enumerate(lines) if line == heading]
    if len(heading_indexes) != 1:
        raise ValueError(
            "commercial context Offers profile must contain exactly one "
            f"{heading!r} section: {path}"
        )
    header = "| Offer ID | Offer | Status | Commercial shape |"
    separator = "| --- | --- | --- | --- |"
    section_start = heading_indexes[0] + 1
    section_end = next(
        (
            index
            for index in range(section_start, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    table_header_indexes = [
        index for index in range(section_start, section_end) if lines[index] == header
    ]
    if len(table_header_indexes) != 1:
        raise ValueError(
            "commercial context Offers Offer Catalog must contain exactly one "
            f"expected header: {path}"
        )
    header_index = table_header_indexes[0]
    if header_index + 1 >= len(lines) or lines[header_index + 1] != separator:
        raise ValueError(
            f"commercial context Offers Offer Catalog separator is malformed: {path}"
        )

    offers: dict[str, str] = {}
    for line_number, line in enumerate(lines[header_index + 2 :], start=header_index + 3):
        if not line:
            break
        if not (line.startswith("|") and line.endswith("|")):
            raise ValueError(
                "commercial context Offers Offer Catalog row is malformed at "
                f"{path}:{line_number}"
            )
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 4 or any(not cell for cell in cells[1:]):
            raise ValueError(
                "commercial context Offers Offer Catalog row is malformed at "
                f"{path}:{line_number}"
            )
        offer_match = re.fullmatch(r"`([a-z0-9][a-z0-9._-]*)`", cells[0])
        if offer_match is None:
            raise ValueError(
                "commercial context Offers offer ID is malformed at "
                f"{path}:{line_number}"
            )
        status = cells[2]
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", status):
            raise ValueError(
                "commercial context Offers status is malformed at "
                f"{path}:{line_number}"
            )
        offer_id = offer_match.group(1)
        if offer_id in offers:
            raise ValueError(
                f"commercial context Offers has duplicate offer ID {offer_id}: {path}"
            )
        offers[offer_id] = status
    if not offers:
        raise ValueError(f"commercial context Offers Offer Catalog has no offers: {path}")
    return offers


def validate_commercial_criterion_evidence(
    criterion_evidence: Sequence[CommercialCriterionEvidence],
    research_evidence: Sequence[RelationshipEnrichmentEvidence],
    expected_criterion_ids: tuple[str, ...],
    *,
    label: str,
) -> None:
    returned_criterion_ids = [item.criterion_id for item in criterion_evidence]
    duplicate_criterion_ids = sorted(
        {
            criterion_id
            for criterion_id in returned_criterion_ids
            if returned_criterion_ids.count(criterion_id) > 1
        }
    )
    if duplicate_criterion_ids:
        raise ValueError(
            f"{label} has duplicate criterion IDs {duplicate_criterion_ids}"
        )
    expected_ids = set(expected_criterion_ids)
    returned_ids = set(returned_criterion_ids)
    missing_ids = sorted(expected_ids - returned_ids)
    undeclared_ids = sorted(returned_ids - expected_ids)
    if missing_ids or undeclared_ids:
        raise ValueError(
            f"{label} criterion IDs do not match the ICP qualification contract: "
            f"missing={missing_ids}, undeclared={undeclared_ids}"
        )

    evidence_id_values = [evidence.evidence_id for evidence in research_evidence]
    duplicate_evidence_ids = sorted(
        {
            evidence_id
            for evidence_id in evidence_id_values
            if evidence_id_values.count(evidence_id) > 1
        }
    )
    if duplicate_evidence_ids:
        raise ValueError(f"{label} has duplicate research evidence IDs {duplicate_evidence_ids}")
    for evidence in research_evidence:
        required = {
            "evidence_id": evidence.evidence_id,
            "source_url": evidence.source_url,
            "claim": evidence.claim,
            "source_excerpt": evidence.source_excerpt,
        }
        missing_fields = [name for name, value in required.items() if not value.strip()]
        if missing_fields:
            raise ValueError(
                f"{label} research evidence is missing {', '.join(missing_fields)}"
            )

    evidence_ids = set(evidence_id_values)
    for criterion in criterion_evidence:
        if not criterion.criterion_id.strip() or not criterion.explanation.strip():
            raise ValueError(f"{label} criterion evidence is incomplete")
        missing_evidence_ids = set(criterion.evidence_ids) - evidence_ids
        if missing_evidence_ids:
            raise ValueError(
                f"{label} criterion evidence references missing evidence IDs "
                f"{sorted(missing_evidence_ids)}"
            )
        if (
            criterion.assessment != CommercialCriterionAssessment.UNKNOWN
            and not criterion.evidence_ids
        ):
            raise ValueError(
                f"{label} matched/not_matched criterion has no evidence IDs"
            )


def validate_relationship_enrichment_commercial_contract(
    artifact: RelationshipEnrichmentArtifact,
) -> None:
    context = artifact.commercial_context
    if context is None:
        raise ValueError("relationship enrichment artifact is missing commercial_context")
    expected_criterion_ids = validate_commercial_context_sources(context)
    for index, decision in enumerate(artifact.decisions, start=1):
        label = f"relationship enrichment decision {decision.followup_id or index}"
        if decision.commercial_context != context:
            raise ValueError(f"{label} commercial_context mismatch")
        validate_commercial_criterion_evidence(
            decision.criterion_evidence,
            decision.research_evidence,
            expected_criterion_ids,
            label=label,
        )
