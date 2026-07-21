"""Acceptance tracking and accepted follow-up workflows."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .browser import BrowserClient
from .commercial_context import (
    validate_commercial_context_sources as _validate_commercial_context_sources,
)
from .commercial_context import (
    validate_commercial_criterion_evidence,
    validate_relationship_enrichment_commercial_contract,
)
from .models import (
    APPROVED_RELATIONSHIP_ROLE_BY_SOURCE,
    WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE,
    AcceptanceCheckCandidate,
    AcceptanceDailyRun,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceOutcomeArtifact,
    AcceptanceStatus,
    AcceptedDraftCandidate,
    AcceptedGreetingEligibilityArtifact,
    AcceptedGreetingEligibilityItem,
    CodexRelationshipEnrichmentJob,
    CodexRelationshipEnrichmentResult,
    CommercialContextReference,
    GreetingEligibilityStatus,
    LeadLedger,
    LeadRecord,
    RelationshipEnrichmentArtifact,
    RelationshipEnrichmentDecision,
    RelationshipEnrichmentDecisionStatus,
    RelationshipEnrichmentQueue,
    RelationshipEnrichmentQueueItem,
    RelationshipEnrichmentStatus,
    RelationshipRole,
    RelationshipSignalType,
    RunState,
    acceptance_followup_id,
    accepted_event_confirms_followup,
    accepted_followup_candidate_key,
    accepted_welcome_message,
    apply_acceptance_followup_send_result,
    build_relationship_enrichment_queue,
    clean_inline,
    first_name,
    is_public_linkedin_profile_url,
    is_sales_nav_profile_url,
    latest_acceptance_event,
    lead_key_for_values,
    now_utc,
    validate_acceptance_followup_can_send,
    validate_relationship_enrichment_artifact,
)
from .reports import render_acceptance_report
from .store import Store, read_model, write_json_atomic

CODEX_ENRICHMENT_MODEL = "gpt-5.5"
CODEX_ENRICHMENT_REASONING_EFFORT = "xhigh"
DEFAULT_ACCEPTANCE_TIMEZONE = "America/Argentina/Buenos_Aires"


@dataclass(frozen=True)
class AcceptanceDailyCheckResult:
    messages: list[str]
    checked: int
    coverage_complete: bool
    blocker: str | None = None
CODEX_ENRICHMENT_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "candidate_key",
        "status",
        "confidence",
        "person_summary",
        "company_name",
        "company_summary",
        "official_company_url",
        "evidence_urls",
        "research_evidence",
        "commercial_context",
        "criterion_evidence",
        "unknowns",
        "notes",
        "warnings",
        "relationship_role",
        "priority",
        "signal_type",
        "visible_signal",
        "signal_url",
        "followup_reason",
        "next_useful_action",
        "permission_boundary",
    ],
    "properties": {
        "candidate_key": {"type": "string", "minLength": 1},
        "status": {
            "type": "string",
            "enum": ["enriched", "needs_review", "skip"],
        },
        "confidence": {
            "anyOf": [
                {"type": "string", "enum": ["high", "medium", "low"]},
                {"type": "null"},
            ]
        },
        "person_summary": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "company_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "company_summary": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "official_company_url": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "evidence_urls": {"type": "array", "items": {"type": "string"}},
        "research_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "evidence_id",
                    "source_url",
                    "claim",
                    "relevance",
                    "source_excerpt",
                ],
                "properties": {
                    "evidence_id": {"type": "string", "minLength": 1},
                    "source_url": {"type": "string", "minLength": 1},
                    "claim": {"type": "string", "minLength": 1},
                    "relevance": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "source_excerpt": {"type": "string", "minLength": 1},
                },
            },
        },
        "commercial_context": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "icp_profile_id",
                "icp_source_path",
                "offers_profile_id",
                "offers_source_path",
                "offer_id",
            ],
            "properties": {
                "icp_profile_id": {"type": "string", "minLength": 1},
                "icp_source_path": {"type": "string", "minLength": 1},
                "offers_profile_id": {"type": "string", "minLength": 1},
                "offers_source_path": {"type": "string", "minLength": 1},
                "offer_id": {"type": "string", "minLength": 1},
            },
        },
        "criterion_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion_id", "assessment", "evidence_ids", "explanation"],
                "properties": {
                    "criterion_id": {"type": "string", "minLength": 1},
                    "assessment": {
                        "type": "string",
                        "enum": ["matched", "not_matched", "unknown"],
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string", "minLength": 1},
                },
            },
        },
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "relationship_role": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": [
                        "buyer",
                        "referral_partner",
                        "hiring_recruiter",
                        "other",
                    ],
                },
                {"type": "null"},
            ]
        },
        "priority": {
            "anyOf": [
                {"type": "string", "enum": ["high", "normal", "low", "pause"]},
                {"type": "null"},
            ]
        },
        "signal_type": {
            "type": "string",
            "enum": ["linkedin_post", "company_site", "profile", "other", "none"],
        },
        "visible_signal": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "signal_url": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "followup_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "next_useful_action": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "permission_boundary": {"type": "string", "enum": ["review_only"]},
    },
}


def lead_review_record_for_candidate(
    lead_ledger: LeadLedger,
    candidate: AcceptedDraftCandidate,
) -> tuple[str | None, LeadRecord | None]:
    identity_urls = [candidate.sales_nav_profile_url, candidate.profile_url]
    checked: set[str] = set()
    for identity_url in identity_urls:
        lead_key = lead_key_for_values(identity_url, None, candidate.name)
        if lead_key in checked:
            continue
        checked.add(lead_key)
        record = lead_ledger.leads.get(lead_key)
        if record is not None:
            return lead_key, record
    return None, None


def lead_review_approval_source(record: LeadRecord) -> str | None:
    if record.approved_source:
        return record.approved_source
    if record.first_source and record.first_source == record.last_source:
        return record.first_source
    return None


def accepted_greeting_eligibility_item(
    candidate: AcceptedDraftCandidate,
    lead_ledger: LeadLedger,
) -> AcceptedGreetingEligibilityItem:
    candidate_key_value = accepted_followup_candidate_key(candidate)
    followup_id = acceptance_followup_id(candidate_key_value)
    expected_role = APPROVED_RELATIONSHIP_ROLE_BY_SOURCE.get(candidate.source)
    lead_key, record = lead_review_record_for_candidate(lead_ledger, candidate)
    approval_source = lead_review_approval_source(record) if record is not None else None
    warnings: list[str] = []
    status = GreetingEligibilityStatus.ELIGIBLE

    usable_profile = any(
        (
            is_public_linkedin_profile_url(candidate.profile_url),
            is_sales_nav_profile_url(candidate.profile_url),
            is_sales_nav_profile_url(candidate.sales_nav_profile_url),
        )
    )
    if not usable_profile:
        status = GreetingEligibilityStatus.NEEDS_REVIEW
        warnings.append("Durably accepted candidate has no usable LinkedIn profile URL.")
    if record is None:
        warnings.append(
            "Original connection-review evidence was not found; welcome eligibility comes "
            "from the durable accepted relationship."
        )

    return AcceptedGreetingEligibilityItem(
        followup_id=followup_id,
        candidate_key=candidate_key_value,
        candidate=candidate,
        status=status,
        lead_key=lead_key if record is not None else None,
        original_connection_source=approval_source,
        original_connection_approved_at=record.approved_at if record is not None else None,
        original_connection_approval_reason=(
            record.approved_reason if record is not None else None
        ),
        original_connection_review_text=(record.last_row_text if record is not None else None),
        relationship_role=expected_role,
        sales_nav_list_name=None,
        proposed_message=(
            accepted_welcome_message(first_name(candidate.name))
            if status == GreetingEligibilityStatus.ELIGIBLE
            else None
        ),
        warnings=warnings,
    )


def render_greeting_eligibility_markdown(
    artifact: AcceptedGreetingEligibilityArtifact,
) -> str:
    eligible = sum(item.status == GreetingEligibilityStatus.ELIGIBLE for item in artifact.items)
    needs_review = sum(
        item.status == GreetingEligibilityStatus.NEEDS_REVIEW for item in artifact.items
    )
    ineligible = sum(item.status == GreetingEligibilityStatus.INELIGIBLE for item in artifact.items)
    lines = [
        "# LinkedIn Accepted Greeting Eligibility",
        "",
        f"- Generated: `{artifact.generated_at.isoformat()}`",
        f"- Eligible: `{eligible}`",
        f"- Needs review: `{needs_review}`",
        f"- Ineligible: `{ineligible}`",
        "- Eligibility source: durable accepted first-degree relationship",
        "- Relationship enrichment: separate and non-blocking",
    ]
    for item in artifact.items:
        approved_at = (
            item.original_connection_approved_at.isoformat()
            if item.original_connection_approved_at
            else ""
        )
        lines.extend(
            [
                "",
                f"## {clean_inline(item.candidate.name)}",
                f"- Status: `{item.status.value}`",
                f"- Source: `{item.candidate.source}`",
                f"- Lead key: `{item.lead_key or ''}`",
                f"- Approved at: `{approved_at}`",
                "- Original approval: " + clean_inline(item.original_connection_approval_reason),
            ]
        )
        if item.warnings:
            lines.append("- Warnings:")
            lines.extend("  - " + clean_inline(warning) for warning in item.warnings)
        if item.proposed_message:
            lines.extend(["", "Welcome message:", "", "> " + item.proposed_message])
    return "\n".join(lines) + "\n"


def render_accepted_enrichment_queue_markdown(
    packet: RelationshipEnrichmentQueue,
) -> str:
    lines = [
        "# LinkedIn Relationship Enrichment Queue",
        "",
        "This queue enriches accepted relationships for the Relationship Radar.",
        "It does not determine greeting eligibility and authorizes no LinkedIn action.",
        "Original connection-review evidence should be reused before fresh public sources.",
        "",
        f"- Items: `{len(packet.items)}`",
    ]
    for item in packet.items:
        lines.extend(
            [
                "",
                f"## {clean_inline(item.candidate.name)}",
                f"- Follow-up ID: `{item.followup_id}`",
                f"- Candidate key: `{item.candidate_key}`",
                f"- Source: `{item.candidate.source}`",
                f"- Enrichment status: `{item.enrichment_status.value}`",
                f"- Enrichment reason: `{item.enrichment_reason or ''}`",
                f"- Original approval: {clean_inline(item.original_connection_approval_reason)}",
                f"- Accepted at: `{item.candidate.accepted_at.isoformat()}`",
            ]
        )
        if item.evidence:
            lines.append("- Reusable evidence:")
            lines.extend("  - " + clean_inline(value) for value in item.evidence)
    return "\n".join(lines) + "\n"


def render_relationship_enrichment_prompt(
    item: RelationshipEnrichmentQueueItem,
    *,
    context_path: Path,
    sources_path: Path,
    commercial_context: CommercialContextReference,
    qualification_criterion_ids: tuple[str, ...],
) -> str:
    existing = item.decision
    lines = [
        "Enrich one accepted LinkedIn relationship for Hanif's Relationship Radar.",
        "",
        "Start with the original connection-review evidence in the queue item. Read the "
        "local public-source files only to fill information that is missing, stale, or "
        "needed to prioritize engagement. Do not browse the web or rely on URLs you have "
        "not read from the bundle.",
        "",
        "Your job is relationship enrichment, not greeting eligibility or message drafting. "
        "Identify the person, company, audience role, one source-backed visible signal, "
        "and the next useful review action.",
        "",
        "Return `enriched` only when the identity, company, and relevance bridge "
        "are supported by source excerpts. Return `needs_review` if the source bundle is "
        "thin, blocked, ambiguous, or contradictory.",
        "",
        "Status/confidence contract:",
        "- `enriched` requires `confidence` = `high`.",
        "- If useful evidence exists but confidence is `medium` or `low`, return `needs_review`.",
        "- Return `skip` when the source bundle does not support a relevant follow-up.",
        "- The employment lane is paused. Classify recruiters and hiring-only contacts as "
        "`hiring_recruiter` and return `skip` unless the evidence shows a separate concrete "
        "consulting-buyer or referral role.",
        "",
        "Relationship role contract:",
        "- `buyer`: a founder, owner, or operations leader with authority over a growing "
        "business and a plausible business-systems problem.",
        "- `referral_partner`: a trusted advisor who is likely to see workflow or systems "
        "problems and introduce the right specialist.",
        "- `hiring_recruiter`: recruiting or hiring is the primary relationship context.",
        "- `other`: the evidence does not support one of the roles above.",
        "- Treat the saved-search source as context, not proof. Classify from excerpts.",
        "",
        "Commercial authority:",
        f"- Read ICP profile `{commercial_context.icp_profile_id}` from "
        f"{commercial_context.icp_source_path}",
        f"- Read offers profile `{commercial_context.offers_profile_id}` from "
        f"{commercial_context.offers_source_path}",
        f"- Evaluate offer `{commercial_context.offer_id}` only under the scope, status, "
        "qualification, and exclusion rules in the offers profile.",
        "- These two files are the only authority for buyer fit and offer routing. If a "
        "file is unreadable, its profile ID does not match, or a required criterion is "
        "not declared, return `needs_review`, add a warning, and preserve the gap in "
        "`unknowns`.",
        "- Return one `criterion_evidence` entry for every applicable `criterion_id` "
        "declared in the ICP profile. For this run the exact required IDs are: "
        + ", ".join(f"`{value}`" for value in qualification_criterion_ids)
        + ".",
        "- A `matched` or `not_matched` assessment must link "
        "to supporting `research_evidence` IDs. Missing support stays `unknown`.",
        "- Do not classify from keyword or regex scores, generic page text, page titles, "
        "URL text, source bucket names, or historical skill copy.",
        "",
        "Context files:",
        f"- Queue item snapshot: {context_path}",
        f"- Source bundle manifest: {sources_path}",
        "",
        "Candidate:",
        f"- Name: {item.candidate.name}",
        f"- Source: {item.candidate.source}",
        f"- LinkedIn profile: {item.candidate.profile_url or ''}",
        f"- Sales Nav profile: {item.candidate.sales_nav_profile_url or ''}",
        f"- Accepted at: {item.candidate.accepted_at.isoformat()}",
        f"- Enrichment status: {item.enrichment_status.value}",
        f"- Enrichment reason: {item.enrichment_reason or ''}",
    ]
    if item.evidence:
        lines.append("- Reusable original-review and ledger evidence:")
        lines.extend("  - " + evidence for evidence in item.evidence)
    if existing.person_summary or existing.company_summary or existing.evidence_urls:
        lines.extend(
            [
                "",
                "Previously reviewed hints. Treat these as hints only; verify against "
                "the local source files before using them:",
            ]
        )
        if existing.person_summary:
            lines.append("- Person summary hint: " + clean_inline(existing.person_summary))
        if existing.company_name:
            lines.append("- Company name hint: " + clean_inline(existing.company_name))
        if existing.company_summary:
            lines.append("- Company summary hint: " + clean_inline(existing.company_summary))
        if existing.official_company_url:
            lines.append("- Official company URL hint: " + existing.official_company_url)
        if existing.evidence_urls:
            lines.append("- Evidence URL hints:")
            lines.extend("  - " + url for url in existing.evidence_urls)
    lines.extend(
        [
            "",
            "Evidence output rules:",
            "- Include 1-5 `research_evidence` items.",
            "- Each `research_evidence` item must quote or closely paraphrase a source "
            "excerpt you read from a local source file.",
            "- `source_excerpt` must be the exact short passage or shortest useful excerpt "
            "from the local file.",
            "- `claim` is the researched fact supported by that excerpt.",
            "- `relevance` explains why that fact matters for Hanif's follow-up, or null.",
            "- Do not fill fields from broad guessing, page titles alone, or URL text.",
            "- Use `linkedin_post` only when the bundle contains an exact post URL and "
            "post evidence. Otherwise use the actual source type or `none`; do not invent a "
            "post from the saved-search activity filter.",
            "- `visible_signal`, `followup_reason`, and `next_useful_action` must be concrete "
            "and supported by the evidence. Use null when the bundle cannot support them.",
            "- Set `permission_boundary` to `review_only`. No message, comment, connection "
            "request, or other LinkedIn action is authorized by this enrichment.",
            "",
            f"Return `candidate_key` exactly as: `{item.candidate_key}`",
            "",
            "Return JSON only:",
            "{",
            '  "candidate_key": string,',
            '  "status": "enriched" | "needs_review" | "skip",',
            '  "confidence": "high" | "medium" | "low" | null,',
            '  "person_summary": string | null,',
            '  "company_name": string | null,',
            '  "company_summary": string | null,',
            '  "official_company_url": string | null,',
            '  "evidence_urls": [string],',
            '  "research_evidence": [',
            "    {",
            '      "evidence_id": string,',
            '      "source_url": string,',
            '      "claim": string,',
            '      "relevance": string | null,',
            '      "source_excerpt": string',
            "    }",
            "  ],",
            '  "commercial_context": {',
            f'    "icp_profile_id": "{commercial_context.icp_profile_id}",',
            f'    "icp_source_path": "{commercial_context.icp_source_path}",',
            f'    "offers_profile_id": "{commercial_context.offers_profile_id}",',
            f'    "offers_source_path": "{commercial_context.offers_source_path}",',
            f'    "offer_id": "{commercial_context.offer_id}"',
            "  },",
            '  "criterion_evidence": [',
            "    {",
            '      "criterion_id": string,',
            '      "assessment": "matched" | "not_matched" | "unknown",',
            '      "evidence_ids": [string],',
            '      "explanation": string',
            "    }",
            "  ],",
            '  "unknowns": [string],',
            '  "notes": string | null,',
            '  "warnings": [string],',
            '  "relationship_role": "buyer" | "referral_partner" | '
            '"hiring_recruiter" | "other" | null,',
            '  "priority": "high" | "normal" | "low" | "pause" | null,',
            '  "signal_type": "linkedin_post" | "company_site" | "profile" | "other" | "none",',
            '  "visible_signal": string | null,',
            '  "signal_url": string | null,',
            '  "followup_reason": string | null,',
            '  "next_useful_action": string | null,',
            '  "permission_boundary": "review_only"',
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def codex_enrichment_worker_command(
    *,
    codex_bin: str,
    cwd: Path,
    schema_path: Path,
    result_path: Path,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    return [
        codex_bin,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        str(cwd),
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--output-schema",
        str(schema_path),
        "-o",
        str(result_path),
        "--json",
        "-",
    ]


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag.lower() in {"p", "div", "section", "br", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "section", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(part.split()) for part in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _html_to_readable_text(value: str) -> str:
    parser = _ReadableHtmlParser()
    parser.feed(value)
    return parser.text()


def _safe_source_name(source_id: str, url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return f"{source_id.lower()}-{digest}"


def _source_urls_for_enrichment_item(item: RelationshipEnrichmentQueueItem) -> list[str]:
    candidates: list[str] = []
    candidates.extend(item.decision.evidence_urls)
    if item.decision.official_company_url:
        candidates.append(item.decision.official_company_url)
    if item.candidate.profile_url and "/sales/" not in item.candidate.profile_url:
        candidates.append(item.candidate.profile_url)
    for evidence in item.evidence:
        candidates.extend(
            url for url in re.findall(r"https?://[^\s)>\]]+", evidence) if "/sales/" not in url
        )
    urls: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        normalized = value.strip().rstrip(".,")
        if not normalized.startswith(("http://", "https://")):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def fetch_source_material(url: str, *, timeout_seconds: float) -> dict[str, object]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
        )
    }
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout_seconds,
            headers=headers,
        ) as client:
            response = client.get(url)
        content_type = response.headers.get("content-type", "")
        text = response.text
        readable_text = (
            _html_to_readable_text(text) if "html" in content_type.lower() else text.strip()
        )
        return {
            "ok": 200 <= response.status_code < 400,
            "status_code": response.status_code,
            "url": url,
            "final_url": str(response.url),
            "content_type": content_type,
            "html": text,
            "text": readable_text,
            "warning": None,
        }
    except Exception as error:
        return {
            "ok": False,
            "status_code": None,
            "url": url,
            "final_url": None,
            "content_type": None,
            "html": "",
            "text": "",
            "warning": f"source fetch failed: {error}",
        }


def write_enrichment_source_bundle(
    item: RelationshipEnrichmentQueueItem,
    *,
    sources_dir: Path,
    timeout_seconds: float,
) -> Path:
    source_dir = sources_dir / item.followup_id
    source_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, object]] = []
    for index, url in enumerate(_source_urls_for_enrichment_item(item), start=1):
        source_id = f"S{index}"
        stem = _safe_source_name(source_id, url)
        material = fetch_source_material(url, timeout_seconds=timeout_seconds)
        html_path = source_dir / f"{stem}.html"
        text_path = source_dir / f"{stem}.txt"
        html_path.write_text(str(material["html"]), encoding="utf-8")
        text_path.write_text(str(material["text"]), encoding="utf-8")
        sources.append(
            {
                "source_id": source_id,
                "url": url,
                "final_url": material["final_url"],
                "status_code": material["status_code"],
                "content_type": material["content_type"],
                "ok": material["ok"],
                "html_path": str(html_path),
                "text_path": str(text_path),
                "warning": material["warning"],
            }
        )
    manifest = {
        "followup_id": item.followup_id,
        "candidate_key": item.candidate_key,
        "candidate_name": item.candidate.name,
        "generated_at": now_utc().isoformat(),
        "sources": sources,
    }
    manifest_path = source_dir / "sources.json"
    write_json_atomic(manifest_path, manifest)
    return manifest_path


def acceptance_seed(store: Store, *, include_unfinished: bool = False) -> str:
    run = store.load_run()
    if not include_unfinished and run.state != RunState.DONE:
        raise RuntimeError(
            "active run is not Done; pass --include-unfinished to seed provisional sends"
        )
    ledger = store.load_acceptance_ledger()
    seeded = ledger.upsert_from_run(run)
    store.save_acceptance_ledger(ledger)
    store.append_acceptance_event(
        "seed", {"run_id": str(run.id), "seeded": seeded, "include_unfinished": include_unfinished}
    )
    return f"acceptance ledger seeded: {seeded} new invitations"


def acceptance_seed_history(store: Store) -> str:
    ledger = store.load_acceptance_ledger()
    summary = store.seed_acceptance_from_history(ledger)
    store.save_acceptance_ledger(ledger)
    store.append_acceptance_event("seed-history", summary)
    return (
        f"acceptance ledger history seeded: {summary.seeded} new invitations from "
        f"{summary.run_logs} run logs ({summary.sent_events} sent events scanned)"
    )


def acceptance_export(
    store: Store, *, min_age_days: int, max_age_days: int | None, out: Path
) -> str:
    ledger = store.load_acceptance_ledger()
    candidates = [
        AcceptanceCheckCandidate(
            run_id=str(invitation.run_id),
            run_date=invitation.run_date,
            source=invitation.source,
            name=invitation.name,
            profile_url=invitation.profile_url,
            sent_at=invitation.sent_at,
            latest_status=invitation.latest_status,
            latest_checked_at=invitation.latest_checked_at,
        )
        for invitation in ledger.eligible_for_check(min_age_days, max_age_days)
    ]
    write_json_atomic(out, [candidate.model_dump(mode="json") for candidate in candidates])
    store.append_acceptance_event(
        "export",
        {
            "path": str(out),
            "min_age_days": min_age_days,
            "max_age_days": max_age_days,
            "count": len(candidates),
        },
    )
    return f"exported {len(candidates)} acceptance-check candidates to {out}"


def acceptance_import(store: Store, path: Path) -> str:
    artifact = read_model(path, AcceptanceOutcomeArtifact)
    ledger = store.load_acceptance_ledger()
    summary = ledger.import_outcomes(artifact)
    store.save_acceptance_ledger(ledger)
    store.append_acceptance_event("import", {"path": str(path), "summary": summary})
    return (
        f"imported acceptance outcomes: {summary.rows} rows, "
        f"{summary.matched} matched, {summary.unmatched} unmatched"
    )


def acceptance_invalidate_weak_message_acceptances(
    store: Store, *, apply: bool, sample_limit: int = 10
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    invitations = ledger.weak_message_acceptances()
    keys = {invitation.key() for invitation in invitations}
    followup_records = followups.invalidatable_for_acceptance_keys(keys)
    followup_status_by_key = {record.key: record.status.value for record in followups.drafts}

    if apply and invitations:
        invalidated_keys = set(ledger.invalidate_weak_message_acceptances())
        followup_count = followups.invalidate_acceptance_keys(invalidated_keys)
        store.save_acceptance_ledger(ledger)
        store.save_acceptance_followup_ledger(followups)
        store.append_acceptance_event(
            "invalidate-weak-message-acceptances",
            {
                "apply": True,
                "reason": WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE,
                "invitations": len(invalidated_keys),
                "followups": followup_count,
                "sample": [
                    {
                        "name": invitation.name,
                        "source": invitation.source,
                        "profile_url": invitation.profile_url,
                    }
                    for invitation in invitations[: max(0, sample_limit)]
                ],
            },
        )
        mode = "applied"
        followup_count_for_output = followup_count
    else:
        mode = "dry-run"
        followup_count_for_output = len(followup_records)

    lines = [
        (
            f"weak acceptance invalidation {mode}: {len(invitations)} invitation(s), "
            f"{followup_count_for_output} follow-up draft(s)"
        )
    ]
    if sample_limit > 0 and invitations:
        lines.append("sample:")
        for invitation in invitations[:sample_limit]:
            followup_status = followup_status_by_key.get(invitation.key(), "none")
            lines.append(
                "- "
                f"{invitation.name} | {invitation.source} | "
                f"followup={followup_status} | {invitation.profile_url or 'no profile URL'}"
            )
    if not apply and invitations:
        lines.append("rerun with --apply to update acceptance and follow-up ledgers")
    return "\n".join(lines)


def acceptance_check(
    store: Store,
    browser: BrowserClient,
    *,
    input_path: Path,
    out: Path,
    offset: int,
    limit: int,
    delay_ms: int,
) -> str:
    candidates = load_acceptance_check_candidates(input_path)
    input_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
    artifact, path = browser.check_acceptance_outcomes(
        candidates=candidates,
        input_path=input_path,
        out=out,
        offset=offset,
        limit=limit,
        delay_ms=delay_ms,
    )
    artifact.input_sha256 = input_sha256
    if Path(path).resolve() == out.resolve():
        write_json_atomic(out, artifact.model_dump(mode="json", by_alias=False))
    store.append_acceptance_event(
        "check",
        {
            "input": str(input_path),
            "out": path,
            "count": len(artifact.rows),
            "offset": offset,
            "limit": limit,
            "complete": artifact.complete,
        },
    )
    statuses: dict[str, int] = {}
    for row in artifact.rows:
        statuses[row.status.value] = statuses.get(row.status.value, 0) + 1
    return (
        f"acceptance outcomes: {len(artifact.rows)} rows written to {path}; "
        f"statuses={json.dumps(statuses, sort_keys=True)}"
    )


def acceptance_report(
    store: Store,
    *,
    min_age_days: int,
    max_age_days: int | None,
    as_json: bool = False,
    daily_days: int = 30,
    daily_timezone: str = DEFAULT_ACCEPTANCE_TIMEZONE,
) -> str:
    ledger = store.load_acceptance_ledger()
    report = ledger.report(
        min_age_days,
        max_age_days,
        daily_runs=store.load_acceptance_daily_runs(),
        daily_days=daily_days,
        daily_timezone=daily_timezone,
    )
    if as_json:
        import json

        return json.dumps(report.model_dump(mode="json"), indent=2)
    return render_acceptance_report(report)


def acceptance_run_daily_session(
    store: Store,
    browser_factory: Callable[[], BrowserClient],
    *,
    min_age_days: int,
    max_age_days: int | None,
    candidates_out: Path,
    outcomes_out: Path,
    chunk_dir: Path,
    chunk_size: int,
    chunk_retries: int,
    check_delay_ms: int,
    daily_timezone: str = DEFAULT_ACCEPTANCE_TIMEZONE,
    emit: Callable[[str], None] | None = None,
) -> str:
    zone = ZoneInfo(daily_timezone)
    started_at = now_utc()
    before_accepted = _durably_accepted_invitation_keys(store)
    messages = [
        acceptance_seed_history(store),
        acceptance_export(
            store,
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            out=candidates_out,
        ),
    ]
    candidates = load_acceptance_check_candidates(candidates_out)
    if not candidates:
        messages.append("no acceptance-check candidates; browser not opened")
        messages.append(
            _record_acceptance_daily_run(
                store,
                started_at=started_at,
                timezone=daily_timezone,
                zone=zone,
                min_age_days=min_age_days,
                max_age_days=max_age_days,
                eligible=0,
                checked=0,
                coverage_complete=True,
                blocker=None,
                before_accepted=before_accepted,
            )
        )
        messages.append(
            acceptance_report(
                store,
                min_age_days=min_age_days,
                max_age_days=max_age_days,
                as_json=False,
                daily_timezone=daily_timezone,
            )
        )
        return "\n".join(messages)

    browser: BrowserClient | None = None
    try:
        browser = browser_factory()
        check_result = _acceptance_check_and_import_chunks(
            store,
            browser,
            candidates=candidates,
            candidates_out=candidates_out,
            outcomes_out=outcomes_out,
            chunk_dir=chunk_dir,
            chunk_size=chunk_size,
            retries=chunk_retries,
            delay_ms=check_delay_ms,
            emit=emit,
        )
        messages.extend(check_result.messages)
    except Exception as exc:
        messages.append(
            _record_acceptance_daily_run(
                store,
                started_at=started_at,
                timezone=daily_timezone,
                zone=zone,
                min_age_days=min_age_days,
                max_age_days=max_age_days,
                eligible=len(candidates),
                checked=0,
                coverage_complete=False,
                blocker=str(exc),
                before_accepted=before_accepted,
            )
        )
        raise
    finally:
        if browser is not None:
            close = getattr(browser, "close", None)
            if callable(close):
                close()
    messages.append(
        _record_acceptance_daily_run(
            store,
            started_at=started_at,
            timezone=daily_timezone,
            zone=zone,
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            eligible=len(candidates),
            checked=check_result.checked,
            coverage_complete=check_result.coverage_complete,
            blocker=check_result.blocker,
            before_accepted=before_accepted,
        )
    )
    messages.append(
        acceptance_report(
            store,
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            as_json=False,
            daily_timezone=daily_timezone,
        )
    )
    return "\n".join(messages)


def _durably_accepted_invitation_keys(store: Store) -> set[str]:
    ledger = store.load_acceptance_ledger()
    keys: set[str] = set()
    for invitation in ledger.invitations:
        if invitation.latest_status != AcceptanceStatus.ACCEPTED:
            continue
        event = latest_acceptance_event(invitation)
        if event is not None and accepted_event_confirms_followup(event):
            keys.add(invitation.key())
    return keys


def _record_acceptance_daily_run(
    store: Store,
    *,
    started_at: datetime,
    timezone: str,
    zone: ZoneInfo,
    min_age_days: int,
    max_age_days: int | None,
    eligible: int,
    checked: int,
    coverage_complete: bool,
    blocker: str | None,
    before_accepted: set[str],
) -> str:
    completed_at = now_utc()
    after_accepted = _durably_accepted_invitation_keys(store)
    ledger = store.load_acceptance_ledger()
    remaining_unresolved = len(ledger.eligible_for_check(min_age_days, max_age_days))
    run = AcceptanceDailyRun(
        started_at=started_at,
        completed_at=completed_at,
        local_date=completed_at.astimezone(zone).date(),
        timezone=timezone,
        min_age_days=min_age_days,
        max_age_days=max_age_days,
        eligible=eligible,
        checked=checked,
        newly_confirmed_accepted=len(after_accepted - before_accepted),
        remaining_unresolved=remaining_unresolved,
        coverage_complete=coverage_complete,
        blocker=blocker,
    )
    store.append_acceptance_daily_run(run)
    state = "complete" if coverage_complete else "incomplete"
    return (
        f"daily acceptance coverage: {state}; eligible={eligible}, checked={checked}, "
        f"newly_confirmed_accepted={run.newly_confirmed_accepted}, "
        f"remaining_unresolved={remaining_unresolved}"
    )


def _acceptance_check_and_import_chunks(
    store: Store,
    browser: BrowserClient,
    *,
    candidates: list[AcceptanceCheckCandidate],
    candidates_out: Path,
    outcomes_out: Path,
    chunk_dir: Path,
    chunk_size: int,
    retries: int,
    delay_ms: int,
    emit: Callable[[str], None] | None = None,
) -> AcceptanceDailyCheckResult:
    chunk_size = max(1, chunk_size)
    retries = max(0, retries)
    attempts = 1 + retries
    chunk_dir.mkdir(parents=True, exist_ok=True)
    messages: list[str] = []
    chunk_paths: list[Path] = []
    blockers: list[str] = []
    notify = emit or (lambda _message: None)
    candidates_sha256 = hashlib.sha256(candidates_out.read_bytes()).hexdigest()
    chunk_count = (len(candidates) + chunk_size - 1) // chunk_size
    for offset in range(0, len(candidates), chunk_size):
        chunk_number = (offset // chunk_size) + 1
        limit = min(chunk_size, len(candidates) - offset)
        chunk_path = chunk_dir / f"chunk-{offset}.json"
        if chunk_path.exists():
            existing = read_model(chunk_path, AcceptanceOutcomeArtifact)
            existing_blocked_rows = [
                row for row in existing.rows if str(getattr(row, "status", "")).lower() == "blocked"
            ]
            if (
                existing.complete is True
                and existing.input == str(candidates_out)
                and existing.input_sha256 == candidates_sha256
                and existing.offset == offset
                and existing.limit == limit
                and existing.total_candidates == len(candidates)
                and len(existing.rows) == limit
                and not existing_blocked_rows
            ):
                store.append_acceptance_event(
                    "run-daily-session-check-reuse",
                    {
                        "input": str(candidates_out),
                        "out": str(chunk_path),
                        "offset": offset,
                        "limit": limit,
                        "candidates": len(candidates),
                    },
                )
                messages.append(f"reused complete acceptance chunk: {chunk_path}")
                notify(
                    f"acceptance check: chunk {chunk_number}/{chunk_count} reused "
                    f"with {limit} row(s)"
                )
                chunk_paths.append(chunk_path)
                continue
        store.append_acceptance_event(
            "run-daily-session-check-start",
            {
                "input": str(candidates_out),
                "out": str(chunk_path),
                "offset": offset,
                "limit": limit,
                "candidates": len(candidates),
            },
        )
        last_exception: Exception | None = None
        for attempt in range(1, attempts + 1):
            notify(
                f"acceptance check: chunk {chunk_number}/{chunk_count}, "
                f"attempt {attempt}/{attempts}"
            )
            if attempt > 1:
                store.append_acceptance_event(
                    "run-daily-session-check-retry",
                    {
                        "input": str(candidates_out),
                        "out": str(chunk_path),
                        "offset": offset,
                        "limit": limit,
                        "candidates": len(candidates),
                        "attempt": attempt,
                        "attempts": attempts,
                        "retries": retries,
                        "previous_error": str(last_exception),
                    },
                )
                messages.append(
                    f"retrying acceptance chunk: {chunk_path} "
                    f"(attempt {attempt}/{attempts}) after: {last_exception}"
                )
            try:
                messages.append(
                    acceptance_check(
                        store,
                        browser,
                        input_path=candidates_out,
                        out=chunk_path,
                        offset=offset,
                        limit=limit,
                        delay_ms=delay_ms,
                    )
                )
                last_exception = None
                break
            except Exception as exc:
                last_exception = exc
                if attempt < attempts:
                    recover = getattr(browser, "recover_after_failure", None)
                    if callable(recover):
                        recover()
        if last_exception is not None:
            blocker = (
                f"{chunk_path} failed during acceptance check after {attempts} attempt(s) "
                f"({retries} retries) "
                f"(offset={offset}, limit={limit}, candidates={len(candidates)}): "
                f"{last_exception}"
            )
            store.append_acceptance_event(
                "run-daily-session-blocked",
                {
                    "reason": "acceptance chunk check failed",
                    "blockers": [blocker],
                    "input": str(candidates_out),
                    "out": str(chunk_path),
                    "offset": offset,
                    "limit": limit,
                    "candidates": len(candidates),
                },
            )
            messages.append("stopped: " + blocker)
            return AcceptanceDailyCheckResult(
                messages=messages,
                checked=_acceptance_completed_row_count(chunk_paths),
                coverage_complete=False,
                blocker=blocker,
            )
        artifact = read_model(chunk_path, AcceptanceOutcomeArtifact)
        notify(
            f"acceptance check: chunk {chunk_number}/{chunk_count} completed "
            f"with {len(artifact.rows)} row(s)"
        )
        chunk_paths.append(chunk_path)
        if artifact.complete is not True:
            blockers.append(f"{chunk_path} is incomplete")
        if len(artifact.rows) != limit:
            blockers.append(f"{chunk_path} has {len(artifact.rows)}/{limit} rows")
        blocked_rows = [
            row for row in artifact.rows if str(getattr(row, "status", "")).lower() == "blocked"
        ]
        if blocked_rows:
            blockers.append(f"{chunk_path} has {len(blocked_rows)} blocked rows")
    if blockers:
        store.append_acceptance_event(
            "run-daily-session-blocked",
            {"reason": "incomplete chunks", "blockers": blockers},
        )
        blocker = "; ".join(blockers)
        messages.append("stopped: " + blocker)
        return AcceptanceDailyCheckResult(
            messages=messages,
            checked=_acceptance_completed_row_count(chunk_paths),
            coverage_complete=False,
            blocker=blocker,
        )

    rows = [
        row
        for chunk_path in chunk_paths
        for row in read_model(chunk_path, AcceptanceOutcomeArtifact).rows
    ]
    if len(rows) != len(candidates):
        store.append_acceptance_event(
            "run-daily-session-blocked",
            {
                "reason": "merged row count mismatch",
                "rows": len(rows),
                "candidates": len(candidates),
            },
        )
        blocker = (
            f"merged acceptance row count {len(rows)} "
            f"does not equal candidate count {len(candidates)}"
        )
        messages.append("stopped: " + blocker)
        return AcceptanceDailyCheckResult(
            messages=messages,
            checked=len(rows),
            coverage_complete=False,
            blocker=blocker,
        )

    merged = AcceptanceOutcomeArtifact(
        captured_at=now_utc().isoformat(),
        input=str(candidates_out),
        input_sha256=candidates_sha256,
        count=len(rows),
        offset=0,
        limit=0,
        total_candidates=len(candidates),
        complete=True,
        rows=rows,
    )
    write_json_atomic(outcomes_out, merged.model_dump(mode="json", by_alias=False))
    store.append_acceptance_event(
        "run-daily-session-merge",
        {
            "candidates": len(candidates),
            "rows": len(rows),
            "chunks": [str(path) for path in chunk_paths],
            "out": str(outcomes_out),
        },
    )
    messages.append(f"merged acceptance outcomes: {len(rows)} rows to {outcomes_out}")
    messages.append(acceptance_import(store, outcomes_out))
    return AcceptanceDailyCheckResult(
        messages=messages,
        checked=len(rows),
        coverage_complete=True,
    )


def _acceptance_completed_row_count(chunk_paths: Sequence[Path]) -> int:
    checked = 0
    for path in chunk_paths:
        artifact = read_model(path, AcceptanceOutcomeArtifact)
        if artifact.complete is not True:
            continue
        if any(row.status == AcceptanceStatus.BLOCKED for row in artifact.rows):
            continue
        checked += len(artifact.rows)
    return checked


def acceptance_export_enrichment_queue(
    store: Store,
    *,
    out: Path,
    markdown_out: Path | None,
    offset: int,
    limit: int,
    stale_after_days: int,
    prioritize_engagement: bool,
) -> str:
    if stale_after_days <= 0:
        raise ValueError("stale_after_days must be > 0")
    from .relationship_radar import (
        RelationshipRadarLedger,
        render_relationship_radar_markdown,
    )

    radar_path = store.dir / "relationship-radar" / "ledger.json"
    radar = (
        read_model(radar_path, RelationshipRadarLedger)
        if radar_path.exists()
        else RelationshipRadarLedger()
    )
    radar_by_key = {record.candidate_key: record for record in radar.records}
    lead_ledger = store.load_lead_ledger()
    current = now_utc()
    selected_items: list[RelationshipEnrichmentQueueItem] = []
    stale_updates = 0

    for candidate in store.load_acceptance_ledger().accepted_connections():
        key = accepted_followup_candidate_key(candidate)
        record = radar_by_key.get(key)
        if record is None:
            enrichment_status = RelationshipEnrichmentStatus.MISSING
            enrichment_reason = "missing"
        else:
            enrichment_status = record.relationship_enrichment_status
            enrichment_reason = enrichment_status.value
            if (
                enrichment_status == RelationshipEnrichmentStatus.CURRENT
                and record.enriched_at is not None
                and (current - record.enriched_at).days >= stale_after_days
            ):
                enrichment_status = RelationshipEnrichmentStatus.STALE
                enrichment_reason = "stale"
                record.relationship_enrichment_status = enrichment_status
                record.updated_at = current
                stale_updates += 1
            elif prioritize_engagement and record.review_state in {
                "active",
                "needs_enrichment",
                "needs_review",
            }:
                enrichment_reason = "prioritize_engagement"
            elif enrichment_status in {
                RelationshipEnrichmentStatus.CURRENT,
                RelationshipEnrichmentStatus.NEEDS_REVIEW,
                RelationshipEnrichmentStatus.NOT_APPLICABLE,
            }:
                continue

        item = build_relationship_enrichment_queue([candidate]).items[0]
        _lead_key, lead_record = lead_review_record_for_candidate(lead_ledger, candidate)
        evidence = list(item.evidence)
        if lead_record is not None:
            approval_source = lead_review_approval_source(lead_record)
            if approval_source:
                evidence.append(f"Original connection source: {approval_source}")
            if lead_record.approved_reason:
                evidence.append(f"Original connection approval: {lead_record.approved_reason}")
            if lead_record.last_row_text:
                evidence.append(f"Original connection review row: {lead_record.last_row_text}")
        decision_update: dict[str, object] = {}
        if record is not None:
            decision_update = {
                "person_summary": record.person_summary,
                "company_name": record.company_name,
                "company_summary": record.company_summary,
                "relationship_role": record.relationship_role,
                "priority": record.priority,
                "signal_type": record.signal_type,
                "visible_signal": record.visible_signal,
                "signal_url": record.signal_url,
                "followup_reason": record.followup_reason,
                "next_useful_action": record.next_useful_action,
                "evidence_urls": list(record.evidence_urls),
                "research_evidence": list(record.research_evidence),
                "warnings": list(record.warnings),
            }
        selected_items.append(
            item.model_copy(
                update={
                    "evidence": evidence,
                    "enrichment_status": enrichment_status,
                    "enrichment_reason": enrichment_reason,
                    "original_connection_approved_at": (
                        lead_record.approved_at if lead_record is not None else None
                    ),
                    "original_connection_approval_reason": (
                        lead_record.approved_reason if lead_record is not None else None
                    ),
                    "original_connection_review_text": (
                        lead_record.last_row_text if lead_record is not None else None
                    ),
                    "decision": item.decision.model_copy(update=decision_update),
                }
            )
        )

    if stale_updates:
        radar.generated_at = current
        write_json_atomic(radar_path, radar.model_dump(mode="json", by_alias=False))
        radar_path.with_suffix(".md").write_text(
            render_relationship_radar_markdown(radar), encoding="utf-8"
        )

    if limit > 0:
        selected_items = selected_items[offset : offset + limit]
    elif offset > 0:
        selected_items = selected_items[offset:]
    packet = RelationshipEnrichmentQueue(items=selected_items)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, packet.model_dump(mode="json", by_alias=False))
    markdown_path = markdown_out or out.with_suffix(".md")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_accepted_enrichment_queue_markdown(packet), encoding="utf-8")
    store.append_acceptance_event(
        "export-enrichment-queue",
        {
            "out": str(out),
            "markdown_out": str(markdown_path),
            "count": len(packet.items),
            "offset": offset,
            "limit": limit,
            "stale_after_days": stale_after_days,
            "prioritize_engagement": prioritize_engagement,
            "stale_updates": stale_updates,
        },
    )
    return (
        f"exported {len(packet.items)} relationship enrichment queue item(s) to {out}; "
        f"original review evidence reused; markdown: {markdown_path}"
    )


def acceptance_export_browser_investigation_queue(
    store: Store,
    *,
    out: Path,
    markdown_out: Path | None,
    limit: int,
    cooldown_days: int,
) -> str:
    """Export unresolved radar records for a bounded authenticated-browser pass."""

    if limit <= 0:
        raise ValueError("limit must be > 0")
    if cooldown_days <= 0:
        raise ValueError("cooldown_days must be > 0")
    from .relationship_radar import RelationshipRadarLedger

    radar_path = store.dir / "relationship-radar" / "ledger.json"
    radar = (
        read_model(radar_path, RelationshipRadarLedger)
        if radar_path.exists()
        else RelationshipRadarLedger()
    )
    candidates = {
        accepted_followup_candidate_key(candidate): candidate
        for candidate in store.load_acceptance_ledger().accepted_connections()
    }
    lead_ledger = store.load_lead_ledger()
    current = now_utc()
    selected: list[RelationshipEnrichmentQueueItem] = []
    for record in radar.records:
        if record.review_state != "needs_review":
            continue
        if record.browser_investigated_at is not None and (
            current - record.browser_investigated_at
        ).days < cooldown_days:
            continue
        candidate = candidates.get(record.candidate_key)
        if candidate is None or not any(
            (
                is_public_linkedin_profile_url(candidate.profile_url),
                is_sales_nav_profile_url(candidate.profile_url),
                is_sales_nav_profile_url(candidate.sales_nav_profile_url),
            )
        ):
            continue
        item = build_relationship_enrichment_queue([candidate]).items[0]
        _lead_key, lead_record = lead_review_record_for_candidate(lead_ledger, candidate)
        evidence = list(item.evidence)
        if lead_record is not None:
            if lead_record.approved_reason:
                evidence.append(f"Original connection approval: {lead_record.approved_reason}")
            if lead_record.last_row_text:
                evidence.append(f"Original connection review row: {lead_record.last_row_text}")
        decision = item.decision.model_copy(
            update={
                "person_summary": record.person_summary,
                "company_name": record.company_name,
                "company_summary": record.company_summary,
                "evidence_urls": list(record.evidence_urls),
                "research_evidence": list(record.research_evidence),
                "commercial_context": record.commercial_context,
                "criterion_evidence": list(record.criterion_evidence),
                "unknowns": list(record.unknowns),
                "warnings": list(record.warnings),
                "relationship_role": record.relationship_role,
                "priority": record.priority,
                "signal_type": record.signal_type,
                "visible_signal": record.visible_signal,
                "signal_url": record.signal_url,
                "followup_reason": record.followup_reason,
                "next_useful_action": record.next_useful_action,
            }
        )
        selected.append(
            item.model_copy(
                update={
                    "evidence": evidence,
                    "enrichment_status": record.relationship_enrichment_status,
                    "enrichment_reason": "browser_needs_review",
                    "original_connection_approved_at": record.original_connection_approved_at,
                    "original_connection_approval_reason": (
                        record.original_connection_approval_reason
                    ),
                    "decision": decision,
                }
            )
        )
        if len(selected) >= limit:
            break

    packet = RelationshipEnrichmentQueue(items=selected)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, packet.model_dump(mode="json", by_alias=False))
    markdown_path = markdown_out or out.with_suffix(".md")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_accepted_enrichment_queue_markdown(packet), encoding="utf-8")
    store.append_acceptance_event(
        "export-browser-investigation-queue",
        {
            "out": str(out),
            "markdown_out": str(markdown_path),
            "count": len(packet.items),
            "limit": limit,
            "cooldown_days": cooldown_days,
            "permission_boundary": "review_only",
        },
    )
    return (
        f"exported {len(packet.items)} browser investigation queue item(s) to {out}; "
        f"cooldown={cooldown_days} days; markdown: {markdown_path}"
    )


def acceptance_launch_enrichment_workers(
    store: Store,
    *,
    enrichment_queue: Path,
    jobs_dir: Path,
    sources_dir: Path,
    codex_bin: str,
    cwd: Path,
    model: str,
    reasoning_effort: str,
    offset: int,
    limit: int,
    force: bool,
    fetch_timeout_seconds: float,
) -> str:
    packet = read_model(enrichment_queue, RelationshipEnrichmentQueue)
    qualification_criterion_ids = _validate_commercial_context_sources(
        packet.commercial_context
    )
    items = _select_queue_items(packet.items, offset=offset, limit=limit)
    if not items:
        raise RuntimeError("no relationship enrichment queue item(s) selected")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)
    launched = 0
    skipped = 0
    for item in items:
        job_dir = jobs_dir / item.followup_id
        packet_path = job_dir / "packet.md"
        schema_path = job_dir / "schema.json"
        result_path = job_dir / "result.json"
        events_path = job_dir / "events.jsonl"
        stderr_path = job_dir / "stderr.log"
        job_path = job_dir / "job.json"
        context_path = job_dir / "context.json"
        if not force and (result_path.exists() or job_path.exists()):
            skipped += 1
            continue
        job_dir.mkdir(parents=True, exist_ok=True)
        sources_path = write_enrichment_source_bundle(
            item,
            sources_dir=sources_dir,
            timeout_seconds=fetch_timeout_seconds,
        )
        context = {
            "enrichment_queue": str(enrichment_queue),
            "queue_generated_at": packet.generated_at.isoformat(),
            "commercial_context": packet.commercial_context.model_dump(mode="json"),
            "qualification_criterion_ids": list(qualification_criterion_ids),
            "item": item.model_dump(mode="json", by_alias=False),
            "sources_path": str(sources_path),
        }
        write_json_atomic(context_path, context)
        packet_path.write_text(
            render_relationship_enrichment_prompt(
                item,
                context_path=context_path,
                sources_path=sources_path,
                commercial_context=packet.commercial_context,
                qualification_criterion_ids=qualification_criterion_ids,
            ),
            encoding="utf-8",
        )
        write_json_atomic(schema_path, CODEX_ENRICHMENT_OUTPUT_SCHEMA)
        command = codex_enrichment_worker_command(
            codex_bin=codex_bin,
            cwd=cwd,
            schema_path=schema_path,
            result_path=result_path,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        with (
            packet_path.open("r", encoding="utf-8") as stdin,
            events_path.open("w", encoding="utf-8") as stdout,
            stderr_path.open("w", encoding="utf-8") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
        job = CodexRelationshipEnrichmentJob(
            followup_id=item.followup_id,
            candidate_key=item.candidate_key,
            packet_path=str(packet_path),
            schema_path=str(schema_path),
            result_path=str(result_path),
            events_path=str(events_path),
            stderr_path=str(stderr_path),
            context_path=str(context_path),
            sources_path=str(sources_path),
            pid=process.pid,
            command=command,
        )
        write_json_atomic(job_path, job.model_dump(mode="json", by_alias=False))
        launched += 1
    store.append_acceptance_event(
        "launch-codex-enrichment-workers",
        {
            "enrichment_queue": str(enrichment_queue),
            "jobs_dir": str(jobs_dir),
            "sources_dir": str(sources_dir),
            "count": len(items),
            "launched": launched,
            "skipped": skipped,
            "offset": offset,
            "limit": limit,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "commercial_context": packet.commercial_context.model_dump(mode="json"),
        },
    )
    return (
        f"launched {launched} Codex enrichment worker(s) from {enrichment_queue}; "
        f"{skipped} existing job(s) skipped; jobs: {jobs_dir}; sources: {sources_dir}"
    )


def acceptance_collect_enrichment_workers(
    store: Store,
    *,
    enrichment_queue: Path,
    jobs_dir: Path,
    out: Path,
    offset: int,
    limit: int,
) -> str:
    packet = read_model(enrichment_queue, RelationshipEnrichmentQueue)
    qualification_criterion_ids = _validate_commercial_context_sources(
        packet.commercial_context
    )
    items = _select_queue_items(packet.items, offset=offset, limit=limit)
    if not items:
        raise RuntimeError("no relationship enrichment queue item(s) selected")
    decisions: list[RelationshipEnrichmentDecision] = []
    pending = 0
    for item in items:
        result_path = jobs_dir / item.followup_id / "result.json"
        if not result_path.exists():
            pending += 1
            continue
        result = _load_codex_enrichment_result(
            result_path,
            expected_criterion_ids=qualification_criterion_ids,
        )
        decisions.append(
            _codex_enrichment_result_to_decision(
                item,
                result,
                expected_commercial_context=packet.commercial_context,
            )
        )
    if not decisions and pending:
        store.append_acceptance_event(
            "collect-codex-enrichment-workers-pending",
            {
                "enrichment_queue": str(enrichment_queue),
                "jobs_dir": str(jobs_dir),
                "pending": pending,
                "offset": offset,
                "limit": limit,
            },
        )
        raise RuntimeError(
            f"no completed Codex enrichment worker result(s); {pending} pending in {jobs_dir}"
        )
    artifact = RelationshipEnrichmentArtifact(
        source_path=str(jobs_dir),
        commercial_context=packet.commercial_context,
        decisions=decisions,
    )
    validate_relationship_enrichment_commercial_contract(artifact)
    validate_relationship_enrichment_artifact(artifact)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, artifact.model_dump(mode="json", by_alias=False))
    enriched = sum(
        1
        for decision in decisions
        if decision.status == RelationshipEnrichmentDecisionStatus.ENRICHED
    )
    needs_review = sum(
        1
        for decision in decisions
        if decision.status == RelationshipEnrichmentDecisionStatus.NEEDS_REVIEW
    )
    skipped = sum(
        1 for decision in decisions if decision.status == RelationshipEnrichmentDecisionStatus.SKIP
    )
    store.append_acceptance_event(
        "collect-codex-enrichment-workers",
        {
            "enrichment_queue": str(enrichment_queue),
            "jobs_dir": str(jobs_dir),
            "out": str(out),
            "completed": len(decisions),
            "pending": pending,
            "enriched": enriched,
            "needs_review": needs_review,
            "skipped": skipped,
            "commercial_context": packet.commercial_context.model_dump(mode="json"),
            "offset": offset,
            "limit": limit,
        },
    )
    return (
        f"collected {len(decisions)} Codex enrichment worker result(s): "
        f"{enriched} enriched, {needs_review} needs review, "
        f"{skipped} skipped, {pending} pending; wrote {out}"
    )


def acceptance_prepare_welcome_messages(
    store: Store,
    *,
    out: Path,
    report_out: Path | None,
    limit: int,
) -> str:
    """Prepare the exact welcome message for every durably accepted connection."""

    followups = store.load_acceptance_followup_ledger()
    followups_by_key = {record.key: record for record in followups.drafts}
    candidates = []
    for candidate in store.load_acceptance_ledger().accepted_connections():
        existing = followups_by_key.get(accepted_followup_candidate_key(candidate))
        if existing is not None and existing.terminal():
            continue
        candidates.append(candidate)
    if limit > 0:
        candidates = candidates[:limit]
    lead_ledger = store.load_lead_ledger()
    items = [accepted_greeting_eligibility_item(candidate, lead_ledger) for candidate in candidates]
    approved = AcceptedGreetingEligibilityArtifact(items=items)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, approved.model_dump(mode="json", by_alias=False))
    markdown_out = out.with_suffix(".md")
    rendered = render_greeting_eligibility_markdown(approved)
    markdown_out.write_text(rendered, encoding="utf-8")

    for item in items:
        if item.status == GreetingEligibilityStatus.ELIGIBLE:
            continue
        existing = followups_by_key.get(item.candidate_key)
        if existing is None or existing.terminal():
            continue
        existing.greeting_eligibility_status = item.status
        existing.original_connection_approved_at = item.original_connection_approved_at
        existing.original_connection_approval_reason = item.original_connection_approval_reason
        existing.original_connection_review_text = item.original_connection_review_text
        existing.updated_at = approved.generated_at
        existing.warnings = list(dict.fromkeys([*existing.warnings, *item.warnings]))

    report_path = report_out or store.default_acceptance_followup_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(rendered, encoding="utf-8")
    recorded = followups.record_welcome_messages(approved, str(report_path))
    store.save_acceptance_followup_ledger(followups)

    ready = sum(item.status == GreetingEligibilityStatus.ELIGIBLE for item in items)
    needs_review = sum(item.status == GreetingEligibilityStatus.NEEDS_REVIEW for item in items)
    ineligible = sum(item.status == GreetingEligibilityStatus.INELIGIBLE for item in items)
    store.append_acceptance_event(
        "prepare-welcome-messages",
        {
            "out": str(out),
            "markdown_out": str(markdown_out),
            "report_out": str(report_path),
            "eligible": ready,
            "needs_review": needs_review,
            "ineligible": ineligible,
            "recorded": recorded,
            "eligibility_source": "durable accepted first-degree relationship",
            "relationship_enrichment": "separate and non-blocking",
            "message": "exact accepted-connection welcome",
        },
    )
    return (
        f"prepared welcome messages for durable acceptances: {ready} eligible, "
        f"{needs_review} needs review, {ineligible} ineligible, {recorded} recorded; "
        f"eligibility: {out}; report: {report_path}"
    )


def acceptance_send_followup(
    store: Store,
    browser: BrowserClient,
    *,
    record_id: str,
    dry_run: bool,
    preview_fill: bool,
    allow_send: bool,
) -> str:
    ledger = store.load_acceptance_followup_ledger()
    index = ledger.find_by_id(record_id)
    if index is None:
        raise RuntimeError(f"unknown acceptance follow-up id {record_id!r}")
    effective_dry_run = dry_run or preview_fill or not allow_send
    validate_acceptance_followup_can_send(ledger.drafts[index], effective_dry_run, allow_send)
    result, out_path = browser.send_acceptance_followup(
        ledger.drafts[index],
        dry_run=effective_dry_run,
        preview_fill=preview_fill,
        allow_send=allow_send,
    )
    apply_acceptance_followup_send_result(ledger.drafts[index], result, out_path)
    store.save_acceptance_followup_ledger(ledger)
    store.append_acceptance_event(
        "send-greeting",
        {
            "id": record_id,
            "name": ledger.drafts[index].name,
            "status": result.status,
            "dry_run": effective_dry_run,
            "preview_fill": preview_fill,
            "out": out_path,
        },
    )
    return (
        f"accepted_followup={record_id} status={result.status} "
        f"dry_run={effective_dry_run} out={out_path}"
    )


def acceptance_run_welcome_messages(
    store: Store,
    browser: BrowserClient,
    *,
    run_limit: int,
    allow_send: bool,
) -> str:
    if run_limit <= 0:
        raise ValueError("run_limit must be > 0")
    if not allow_send:
        raise RuntimeError("welcome-message run requires --allow-send")

    ledger = store.load_acceptance_followup_ledger()
    sent_before = ledger.welcome_message_sent_count()

    messages: list[str] = []
    processed = 0
    deferred_ids: set[str] = set()
    while processed < run_limit:
        ledger = store.load_acceptance_followup_ledger()
        candidates = [
            record
            for record in ledger.welcome_message_records()
            if not record.terminal() and record.id not in deferred_ids
        ]
        if not candidates:
            break
        record = candidates[0]
        processed += 1

        retryable_dry_run_classification = (
            record.status
            in {
                AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                AcceptanceFollowupStatus.BLOCKED,
                AcceptanceFollowupStatus.SEND_FAILED,
            }
            and bool(record.attempts)
            and record.attempts[-1].dry_run
        )
        if record.status == AcceptanceFollowupStatus.DRAFTED or retryable_dry_run_classification:
            messages.append(
                acceptance_send_followup(
                    store,
                    browser,
                    record_id=record.id,
                    dry_run=True,
                    preview_fill=False,
                    allow_send=False,
                )
            )
            current = store.load_acceptance_followup_ledger()
            current_index = current.find_by_id(record.id)
            if current_index is None:
                raise RuntimeError(f"accepted follow-up {record.id} disappeared after dry-run")
            record = current.drafts[current_index]
            if record.status != AcceptanceFollowupStatus.DRY_RUN_READY:
                messages.append(
                    f"welcome deferred {record.id}: message dry-run did not reach a verified "
                    "ready state"
                )
                deferred_ids.add(record.id)
                continue

        if record.status != AcceptanceFollowupStatus.DRY_RUN_READY:
            messages.append(
                f"welcome deferred {record.id}: requires review in status {record.status.value}"
            )
            deferred_ids.add(record.id)
            continue
        messages.append(
            acceptance_send_followup(
                store,
                browser,
                record_id=record.id,
                dry_run=False,
                preview_fill=False,
                allow_send=True,
            )
        )
        current = store.load_acceptance_followup_ledger()
        current_index = current.find_by_id(record.id)
        if current_index is None:
            raise RuntimeError(f"accepted follow-up {record.id} disappeared after send")
        if current.drafts[current_index].status not in {
            AcceptanceFollowupStatus.SENT,
            AcceptanceFollowupStatus.CONVERSATION_EXISTS,
        }:
            messages.append("welcome run stopped: real send did not reach a terminal safe state")
            break

    ledger = store.load_acceptance_followup_ledger()
    sent_after = ledger.welcome_message_sent_count()
    store.append_acceptance_event(
        "run-welcome-messages",
        {
            "run_limit": run_limit,
            "processed": processed,
            "sent_before": sent_before,
            "sent_after": sent_after,
        },
    )
    if processed == 0:
        messages.append(
            f"welcome-message run idle: {sent_after} total messages sent; "
            "no unsent durable acceptances"
        )
    else:
        messages.append(
            f"welcome-message run complete: {sent_after - sent_before} sent this run, "
            f"{sent_after} total"
        )
    return "\n".join(messages)


def acceptance_retry_send_followup(
    store: Store,
    browser: BrowserClient,
    *,
    record_id: str,
    allow_send: bool,
) -> str:
    if not allow_send:
        raise RuntimeError("retry-send-greeting requires --allow-send")
    messages = [
        acceptance_send_followup(
            store,
            browser,
            record_id=record_id,
            dry_run=True,
            preview_fill=False,
            allow_send=False,
        )
    ]
    ledger = store.load_acceptance_followup_ledger()
    index = ledger.find_by_id(record_id)
    if index is None:
        raise RuntimeError(f"unknown acceptance follow-up id {record_id!r}")
    record = ledger.drafts[index]
    if record.status != AcceptanceFollowupStatus.DRY_RUN_READY:
        return "\n".join(
            messages
            + [
                "retry-send-greeting stopped: dry-run did not make the greeting ready",
                _render_acceptance_followup_send_table([record]),
            ]
        )
    messages.append(
        acceptance_send_followup(
            store,
            browser,
            record_id=record_id,
            dry_run=False,
            preview_fill=False,
            allow_send=True,
        )
    )
    final_ledger = store.load_acceptance_followup_ledger()
    final_index = final_ledger.find_by_id(record_id)
    final_record = final_ledger.drafts[final_index] if final_index is not None else record
    return "\n".join(messages + ["", _render_acceptance_followup_send_table([final_record])])


def acceptance_send_ready_followups(
    store: Store, browser: BrowserClient, *, limit: int, allow_send: bool
) -> str:
    if not allow_send:
        raise RuntimeError("send-ready-greetings requires --allow-send")
    ledger = store.load_acceptance_followup_ledger()
    ready = ledger.ready(limit)
    if not ready:
        return "no accepted follow-ups are ready to send"
    messages = []
    sent_records: list[AcceptanceFollowupRecord] = []
    for record in ready:
        messages.append(
            acceptance_send_followup(
                store,
                browser,
                record_id=record.id,
                dry_run=False,
                preview_fill=False,
                allow_send=True,
            )
        )
        current_ledger = store.load_acceptance_followup_ledger()
        current_index = current_ledger.find_by_id(record.id)
        if current_index is not None:
            sent_records.append(current_ledger.drafts[current_index])
    return "\n".join(messages + ["", _render_acceptance_followup_send_table(sent_records)])


def _render_acceptance_followup_send_table(records: Sequence[AcceptanceFollowupRecord]) -> str:
    if not records:
        return "No accepted follow-ups were processed."
    rows = [
        [
            record.name,
            record.id,
            record.status.value,
            record.sent_at.isoformat() if record.sent_at else "",
        ]
        for record in records
    ]
    headers = ["Name", "ID", "Status", "Sent at"]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def render_row(values: Sequence[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    divider = "-+-".join("-" * width for width in widths)
    return "\n".join(
        ["Accepted follow-up send summary", render_row(headers), divider]
        + [render_row(row) for row in rows]
    )


def acceptance_dry_run_followups(
    store: Store, browser: BrowserClient, *, limit: int, retry_classified: bool = False
) -> str:
    ledger = store.load_acceptance_followup_ledger()
    pending = ledger.needs_dry_run(limit, retry_classified=retry_classified)
    if not pending:
        return "no drafted accepted follow-ups need a dry-run check"
    messages = [
        acceptance_send_followup(
            store,
            browser,
            record_id=record.id,
            dry_run=True,
            preview_fill=False,
            allow_send=False,
        )
        for record in pending
    ]
    return "\n".join(messages)


def load_acceptance_check_candidates(path: Path) -> list[AcceptanceCheckCandidate]:
    return [
        AcceptanceCheckCandidate.model_validate(item)
        for item in _load_json_list(path, "acceptance candidates")
    ]


def _select_queue_items(
    items: list[RelationshipEnrichmentQueueItem], *, offset: int, limit: int
) -> list[RelationshipEnrichmentQueueItem]:
    if limit > 0:
        return items[offset : offset + limit]
    if offset > 0:
        return items[offset:]
    return items


def _load_codex_enrichment_result(
    path: Path,
    *,
    expected_criterion_ids: tuple[str, ...],
) -> CodexRelationshipEnrichmentResult:
    data = json.loads(path.read_text())
    result = CodexRelationshipEnrichmentResult.model_validate(data)
    if result.status not in {
        RelationshipEnrichmentDecisionStatus.ENRICHED,
        RelationshipEnrichmentDecisionStatus.NEEDS_REVIEW,
        RelationshipEnrichmentDecisionStatus.SKIP,
    }:
        raise ValueError(f"unexpected Codex enrichment status in {path}: {result.status.value}")
    validate_commercial_criterion_evidence(
        result.criterion_evidence,
        result.research_evidence,
        expected_criterion_ids,
        label=f"Codex enrichment result {path}",
    )
    if result.status == RelationshipEnrichmentDecisionStatus.ENRICHED:
        if result.confidence is None:
            raise ValueError(f"Codex enrichment result is missing confidence: {path}")
        if not result.research_evidence:
            raise ValueError(f"Codex enrichment result is missing research_evidence: {path}")
        if result.relationship_role not in {
            RelationshipRole.BUYER,
            RelationshipRole.REFERRAL_PARTNER,
        }:
            raise ValueError(
                f"Codex enrichment result has no active consulting relationship role: {path}"
            )
        relationship_fields = {
            "priority": result.priority,
            "visible_signal": result.visible_signal,
            "followup_reason": result.followup_reason,
            "next_useful_action": result.next_useful_action,
        }
        missing = [
            field
            for field, value in relationship_fields.items()
            if value is None or (isinstance(value, str) and not value.strip())
        ]
        if missing:
            raise ValueError(
                f"Codex enrichment result is missing relationship fields "
                f"{', '.join(missing)}: {path}"
            )
        if result.permission_boundary != "review_only":
            raise ValueError(
                f"Codex enrichment result permission boundary is not review_only: {path}"
            )
        if (
            result.signal_type == RelationshipSignalType.LINKEDIN_POST
            and not (result.signal_url or "").strip()
        ):
            raise ValueError(f"Codex enrichment LinkedIn post signal has no URL: {path}")
    return result


def _codex_enrichment_result_to_decision(
    item: RelationshipEnrichmentQueueItem,
    result: CodexRelationshipEnrichmentResult,
    *,
    expected_commercial_context: CommercialContextReference,
) -> RelationshipEnrichmentDecision:
    if result.candidate_key != item.candidate_key:
        raise ValueError(
            "Codex enrichment result candidate_key mismatch: "
            f"expected {item.candidate_key}, got {result.candidate_key}"
        )
    if result.commercial_context != expected_commercial_context:
        raise ValueError(
            "Codex enrichment result commercial_context mismatch: "
            f"expected {expected_commercial_context.model_dump(mode='json')}, "
            f"got {result.commercial_context.model_dump(mode='json')}"
        )
    evidence_urls = list(result.evidence_urls)
    for evidence in result.research_evidence:
        if evidence.source_url not in evidence_urls:
            evidence_urls.append(evidence.source_url)
    payload = {
        "followup_id": item.followup_id,
        "candidate_key": item.candidate_key,
        "candidate": item.candidate,
        "status": result.status,
        "confidence": result.confidence,
        "person_summary": result.person_summary,
        "company_name": result.company_name,
        "company_summary": result.company_summary,
        "official_company_url": result.official_company_url,
        "evidence_urls": evidence_urls,
        "research_evidence": result.research_evidence,
        "commercial_context": result.commercial_context,
        "criterion_evidence": result.criterion_evidence,
        "unknowns": result.unknowns,
        "notes": result.notes,
        "warnings": [*item.decision.warnings, *result.warnings],
        "relationship_role": result.relationship_role,
        "priority": result.priority,
        "signal_type": result.signal_type,
        "visible_signal": result.visible_signal,
        "signal_url": result.signal_url,
        "followup_reason": result.followup_reason,
        "next_useful_action": result.next_useful_action,
        "permission_boundary": result.permission_boundary,
    }
    return RelationshipEnrichmentDecision.model_validate(payload)


def _load_json_list(path: Path, label: str) -> list[object]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{label} artifact must be a JSON array: {path}")
    return data
