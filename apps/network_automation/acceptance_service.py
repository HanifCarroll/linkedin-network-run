"""Acceptance tracking and accepted follow-up workflows."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from html.parser import HTMLParser
from pathlib import Path

import httpx

from .browser import BrowserClient
from .models import (
    WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE,
    AcceptanceCheckCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceOutcomeArtifact,
    AcceptedCodexDraftJob,
    AcceptedCodexDraftResult,
    AcceptedCodexResearchJob,
    AcceptedCodexResearchResult,
    AcceptedDraftCandidate,
    AcceptedFollowupReviewItem,
    AcceptedFollowupReviewPacket,
    AcceptedResearchArtifact,
    AcceptedResearchDecisionArtifact,
    AcceptedResearchDecisionItem,
    AcceptedResearchDecisionStatus,
    AcceptedResearchQueueItem,
    AcceptedResearchQueuePacket,
    AcceptedResearchRow,
    DraftReport,
    DraftStrategy,
    LeadLedger,
    RunState,
    _non_empty,
    acceptance_followup_id,
    accepted_followup_candidate_key,
    apply_acceptance_followup_send_result,
    build_accepted_message_queue_packet,
    build_accepted_research_queue_packet,
    build_draft_report,
    build_draft_report_from_reviewed_research,
    candidate_key,
    clean_inline,
    first_name,
    lead_key_for_values,
    normalize_reviewed_proposed_message,
    now_utc,
    render_draft_markdown,
    validate_acceptance_followup_can_send,
    validate_accepted_research_decision_artifact,
)
from .reports import render_acceptance_report
from .store import Store, read_model, write_json_atomic

CODEX_DRAFT_MODEL = "gpt-5.5"
CODEX_DRAFT_REASONING_EFFORT = "xhigh"
CODEX_DRAFT_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidate_key", "status", "message", "reason", "warnings"],
    "properties": {
        "candidate_key": {"type": "string", "minLength": 1},
        "status": {"type": "string", "enum": ["ready_for_draft", "needs_review"]},
        "message": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "reason": {"type": "string", "minLength": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}
CODEX_RESEARCH_OUTPUT_SCHEMA = {
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
        "notes",
        "warnings",
        "template_key",
        "angle",
    ],
    "properties": {
        "candidate_key": {"type": "string", "minLength": 1},
        "status": {
            "type": "string",
            "enum": ["research_ready", "needs_review", "skip"],
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
        "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "template_key": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["general", "agency", "recruiter", "advisor"],
                },
                {"type": "null"},
            ]
        },
        "angle": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
}

ACCEPTED_FOLLOWUP_DRAFTING_GUIDELINES = [
    "Write in Hanif's voice: clear, plain, matter-of-fact, and low pressure.",
    "After the greeting, start with what Hanif does: he builds web apps and internal "
    "tools for teams stuck with spreadsheets, manual handoffs, or half-working "
    "automations.",
    "Use one researched fact only to create a practical bridge to workflow/tooling "
    "cleanup; do not treat the fact itself as the connection.",
    "Use blank lines between short paragraphs.",
    "End with a low-pressure relevance check, usually: `Does that kind of cleanup "
    "come up much with the teams you work with?`",
    "Do not ask to send a resume, project examples, or set up a call in this first "
    "note.",
    "Do not open with `I saw` or `I noticed`. Do not use `curious`, `that made me "
    "think of`, buzzwords, hype, or partnership language.",
]

ACCEPTED_FOLLOWUP_DEFAULT_MESSAGE_SHAPE = [
    "Greeting: `Hey, {first_name}. Thanks for connecting.`",
    "Paragraph 1: `I build web apps and internal tools for teams that are stuck with "
    "spreadsheets, manual handoffs, or half-working automations.`",
    "Paragraph 2: connect the researched fact to the practical problem Hanif works on. "
    "A good default is: `{company_or_work} seems adjacent to what I do: turning messy "
    "workflows into tools people can actually use.`",
    "Paragraph 3: `Does that kind of cleanup come up much with the teams you work "
    "with?`",
]

ACCEPTED_FOLLOWUP_EXAMPLE_MESSAGE = (
    "Hey, Ana. Thanks for connecting.\n\n"
    "I build web apps and internal tools for teams that are stuck with spreadsheets, "
    "manual handoffs, or half-working automations.\n\n"
    "Olyra's work with companies on AI and operations seems adjacent to what I do: "
    "turning messy workflows into tools people can actually use.\n\n"
    "Does that kind of cleanup come up much with the teams you work with?"
)


def append_accepted_followup_drafting_prompt(lines: list[str]) -> None:
    lines.append("Core prompt for Codex drafting:")
    lines.extend("- " + guideline for guideline in ACCEPTED_FOLLOWUP_DRAFTING_GUIDELINES)
    lines.extend(["", "Default message shape:"])
    lines.extend("- " + part for part in ACCEPTED_FOLLOWUP_DEFAULT_MESSAGE_SHAPE)
    lines.extend(
        ["", "Example:", "", "```text", ACCEPTED_FOLLOWUP_EXAMPLE_MESSAGE, "```"]
    )


def add_lead_review_context_to_draft_report(
    report: DraftReport, lead_ledger: LeadLedger
) -> None:
    for item in report.items:
        candidate = item.candidate
        lead_key = lead_key_for_values(
            candidate.sales_nav_profile_url or candidate.profile_url,
            None,
            candidate.name,
        )
        record = lead_ledger.leads.get(lead_key)
        if record is None:
            continue
        if record.first_source:
            item.evidence.append(f"Original connection source: {record.first_source}")
        if record.approved_reason:
            item.evidence.append(
                f"Original connection approval: {record.approved_reason}"
            )


def accepted_research_by_key(
    artifact: AcceptedResearchArtifact | None,
) -> dict[str, AcceptedResearchRow]:
    if artifact is None:
        return {}
    return {
        candidate_key(row.source, row.name, row.sales_nav_profile_url or row.profile_url): row
        for row in artifact.rows
    }


def build_accepted_followup_review_packet(
    report: DraftReport,
    artifact: AcceptedResearchArtifact | None,
    *,
    report_path: Path,
    research_path: Path | None,
) -> AcceptedFollowupReviewPacket:
    research_rows = accepted_research_by_key(artifact)
    items: list[AcceptedFollowupReviewItem] = []
    for item in report.items:
        key = accepted_followup_candidate_key(item.candidate)
        items.append(
            AcceptedFollowupReviewItem(
                followup_id=acceptance_followup_id(key),
                candidate=item.candidate,
                template_key=item.template_key,
                angle=item.angle,
                draft=item.draft,
                person_does=item.person_does,
                company_does=item.company_does,
                message_fit=item.message_fit,
                company_profile_url=item.company_profile_url,
                company_website_url=item.company_website_url,
                evidence=list(item.evidence),
                warnings=list(item.warnings),
                research=research_rows.get(key),
            )
        )
    return AcceptedFollowupReviewPacket(
        report_path=str(report_path),
        research_path=str(research_path) if research_path else None,
        items=items,
    )


def build_reviewed_accepted_followup_review_packet(
    report: DraftReport,
    artifact: AcceptedResearchDecisionArtifact,
    *,
    report_path: Path,
    research_path: Path,
) -> AcceptedFollowupReviewPacket:
    decisions = {
        decision.candidate_key: decision
        for decision in artifact.decisions
        if decision.status == AcceptedResearchDecisionStatus.READY_FOR_DRAFT
    }
    items: list[AcceptedFollowupReviewItem] = []
    for item in report.items:
        key = accepted_followup_candidate_key(item.candidate)
        items.append(
            AcceptedFollowupReviewItem(
                followup_id=acceptance_followup_id(key),
                candidate=item.candidate,
                template_key=item.template_key,
                angle=item.angle,
                draft=item.draft,
                person_does=item.person_does,
                company_does=item.company_does,
                message_fit=item.message_fit,
                company_profile_url=item.company_profile_url,
                company_website_url=item.company_website_url,
                evidence=list(item.evidence),
                warnings=list(item.warnings),
                reviewed_research=decisions.get(key),
            )
        )
    return AcceptedFollowupReviewPacket(
        report_path=str(report_path),
        research_path=str(research_path),
        items=items,
    )


def render_accepted_followup_review_markdown(packet: AcceptedFollowupReviewPacket) -> str:
    lines = [
        "# Accepted Follow-Up Draft Review",
        "",
        f"- Generated: `{packet.generated_at.isoformat()}`",
        f"- Report: `{packet.report_path}`",
        f"- Research: `{packet.research_path or ''}`",
        f"- Drafts: `{len(packet.items)}`",
        "",
        "Review these drafts before running any dry-run or send command.",
    ]
    for item in packet.items:
        lines.extend(
            [
                "",
                "## " + item.candidate.name,
                f"- Follow-up ID: `{item.followup_id}`",
                f"- Source: `{item.candidate.source}`",
                f"- Template: `{item.template_key.value}`",
                "- Best angle: " + item.angle,
            ]
        )
        if item.person_does:
            lines.append("- Person does: " + item.person_does)
        if item.company_does:
            lines.append("- Company does: " + item.company_does)
        if item.message_fit:
            lines.append("- Why this draft fits: " + item.message_fit)
        if item.company_profile_url:
            lines.append("- Company profile: " + item.company_profile_url)
        if item.company_website_url:
            lines.append("- Company website: " + item.company_website_url)
        if item.reviewed_research:
            decision = item.reviewed_research
            lines.append("- Reviewed research:")
            lines.append(f"  - Status: `{decision.status.value}`")
            lines.append(
                f"  - Confidence: `{decision.confidence.value if decision.confidence else ''}`"
            )
            if decision.official_company_url:
                lines.append("  - Official company URL: " + decision.official_company_url)
            if decision.evidence_urls:
                lines.append("  - Evidence URLs:")
                lines.extend("    - " + url for url in decision.evidence_urls)
        if item.evidence:
            lines.append("- Evidence:")
            lines.extend("  - " + evidence for evidence in item.evidence)
        if item.warnings:
            lines.append("- Warnings:")
            lines.extend("  - " + warning for warning in item.warnings)
        lines.extend(["", "Draft:", ""])
        lines.extend("> " + line if line else ">" for line in item.draft.splitlines())
    return "\n".join(lines) + "\n"


def write_accepted_followup_review_packet(
    packet: AcceptedFollowupReviewPacket, out: Path
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, packet.model_dump(mode="json", by_alias=False))
    markdown_path = out.with_suffix(".md")
    markdown_path.write_text(render_accepted_followup_review_markdown(packet), encoding="utf-8")
    return markdown_path


def render_accepted_research_queue_markdown(packet: AcceptedResearchQueuePacket) -> str:
    items = packet.items
    lines = [
        "# Accepted Follow-Up Research Queue",
        "",
        "Codex should research each person/company and fill the decision fields.",
        "Only use `ready_for_draft` when the identity, company, evidence, and "
        "`proposed_message` are clear.",
        "",
        "Drafting prompt for `proposed_message`:",
    ]
    append_accepted_followup_drafting_prompt(lines)
    lines.extend(
        [
            "",
            f"- Items: `{len(items)}`",
        ]
    )
    for item in items:
        lines.extend(
            [
                "",
                "## " + item.candidate.name,
                f"- Follow-up ID: `{item.followup_id}`",
                f"- Candidate key: `{item.candidate_key}`",
                f"- Source: `{item.candidate.source}`",
                f"- LinkedIn profile: {item.candidate.profile_url or ''}",
                f"- Sales Nav profile: {item.candidate.sales_nav_profile_url or ''}",
                f"- Accepted at: `{item.candidate.accepted_at.isoformat()}`",
            ]
        )
        if item.evidence:
            lines.append("- Existing evidence:")
            lines.extend("  - " + evidence for evidence in item.evidence)
        lines.extend(
            [
                "",
                "Decision fields to fill:",
                "",
                "```json",
                json.dumps(item.decision.model_dump(mode="json"), indent=2),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def render_accepted_message_queue_markdown(packet: AcceptedResearchQueuePacket) -> str:
    items = packet.items
    lines = [
        "# Accepted Follow-Up Message Queue",
        "",
        "Codex should write or update only the `proposed_message` field for each item.",
        "The research fields are already reviewed; keep the message grounded in that "
        "evidence.",
        "",
        "Drafting prompt for `proposed_message`:",
    ]
    append_accepted_followup_drafting_prompt(lines)
    lines.extend(
        [
            "",
            f"- Items: `{len(items)}`",
        ]
    )
    for item in items:
        lines.extend(
            [
                "",
                "## " + item.candidate.name,
                f"- Follow-up ID: `{item.followup_id}`",
                f"- Candidate key: `{item.candidate_key}`",
                f"- Source: `{item.candidate.source}`",
                f"- LinkedIn profile: {item.candidate.profile_url or ''}",
                f"- Sales Nav profile: {item.candidate.sales_nav_profile_url or ''}",
                f"- Accepted at: `{item.candidate.accepted_at.isoformat()}`",
            ]
        )
        if item.evidence:
            lines.append("- Reviewed research:")
            lines.extend("  - " + evidence for evidence in item.evidence)
        lines.extend(
            [
                "",
                "Message fields to fill:",
                "",
                "```json",
                json.dumps(item.decision.model_dump(mode="json"), indent=2),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def render_accepted_codex_draft_prompt(item: AcceptedResearchQueueItem) -> str:
    decision = item.decision
    person_summary = clean_inline(decision.person_summary) or "Not provided."
    company_name = clean_inline(decision.company_name)
    company_summary = clean_inline(decision.company_summary) or "Not provided."
    company_context = (
        f"{company_name}: {company_summary}" if company_name else company_summary
    )
    evidence_lines: list[str] = []
    if decision.official_company_url:
        evidence_lines.append(f"- Official company URL: {decision.official_company_url}")
    evidence_lines.extend(f"- Evidence URL: {url}" for url in decision.evidence_urls)
    if decision.notes:
        evidence_lines.append(f"- Research note: {clean_inline(decision.notes)}")
    evidence_lines.extend(
        f"- Warning: {clean_inline(warning)}" for warning in decision.warnings
    )
    if not evidence_lines:
        evidence_lines.append("- Not provided.")
    first = first_name(item.candidate.name)
    return "\n".join(
        [
            "Draft one LinkedIn follow-up message for Hanif.",
            "",
            "Use only the context below. If the context does not support a specific "
            "relevance bridge, return `needs_review`.",
            "",
            "Context about Hanif:",
            "Hanif builds web apps and internal tools for teams that are stuck with "
            "spreadsheets, manual handoffs, or half-working automations. His angle is "
            "practical workflow cleanup: turning messy internal processes into tools "
            "people can actually use.",
            "",
            "Context about the person:",
            person_summary,
            "",
            "Context about the company:",
            company_context,
            "",
            "Relevant evidence:",
            *evidence_lines,
            "",
            "Write the message in Hanif's voice:",
            "- clear, plain, matter-of-fact",
            "- low pressure",
            "- starts with what Hanif does",
            "- connects Hanif's work to the person/company through a real workflow/tooling "
            "problem",
            "- uses blank lines between short paragraphs",
            "- ends with a simple relevance-check question",
            "",
            "Default structure:",
            f"Hey, {first}. Thanks for connecting.",
            "",
            "I build web apps and internal tools for teams that are stuck with spreadsheets, "
            "manual handoffs, or half-working automations.",
            "",
            "{one sentence connecting their work/company to practical workflow cleanup}",
            "",
            "{low-pressure relevance-check question}",
            "",
            f"Return `candidate_key` exactly as: `{item.candidate_key}`",
            "",
            "Return JSON only:",
            "{",
            '  "candidate_key": string,',
            '  "status": "ready_for_draft" | "needs_review",',
            '  "message": string | null,',
            '  "reason": string,',
            '  "warnings": [string]',
            "}",
        ]
    ) + "\n"


def render_accepted_codex_research_prompt(
    item: AcceptedResearchQueueItem,
    *,
    context_path: Path,
    sources_path: Path,
) -> str:
    existing = item.decision
    lines = [
        "Research one accepted LinkedIn connection for Hanif.",
        "",
        "Read the local source files listed in the source bundle before deciding. "
        "Do not browse the web or rely on URLs you have not read from the bundle.",
        "",
        "Your job is research extraction, not message drafting. Use your judgment to "
        "identify the person, company, relevant work, and why the evidence may connect "
        "to Hanif's practical workflow/tooling work.",
        "",
        "Return `research_ready` only when the identity, company, and relevance bridge "
        "are supported by source excerpts. Return `needs_review` if the source bundle is "
        "thin, blocked, ambiguous, or contradictory.",
        "",
        "Status/confidence contract:",
        "- `research_ready` requires `confidence` = `high`.",
        "- If useful evidence exists but confidence is `medium` or `low`, return "
        "`needs_review`.",
        "- Return `skip` when the source bundle does not support a relevant follow-up.",
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
    ]
    if item.evidence:
        lines.append("- Existing ledger evidence:")
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
            "",
            f"Return `candidate_key` exactly as: `{item.candidate_key}`",
            "",
            "Return JSON only:",
            "{",
            '  "candidate_key": string,',
            '  "status": "research_ready" | "needs_review" | "skip",',
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
            '  "notes": string | null,',
            '  "warnings": [string],',
            '  "template_key": "general" | "agency" | "recruiter" | "advisor" | null,',
            '  "angle": string | null',
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def codex_draft_worker_command(
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


def _source_urls_for_research_item(item: AcceptedResearchQueueItem) -> list[str]:
    candidates: list[str] = []
    candidates.extend(item.decision.evidence_urls)
    if item.decision.official_company_url:
        candidates.append(item.decision.official_company_url)
    if item.candidate.profile_url and "/sales/" not in item.candidate.profile_url:
        candidates.append(item.candidate.profile_url)
    for evidence in item.evidence:
        candidates.extend(
            url
            for url in re.findall(r"https?://[^\s)>\]]+", evidence)
            if "/sales/" not in url
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


def write_research_source_bundle(
    item: AcceptedResearchQueueItem,
    *,
    sources_dir: Path,
    timeout_seconds: float,
) -> Path:
    source_dir = sources_dir / item.followup_id
    source_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, object]] = []
    for index, url in enumerate(_source_urls_for_research_item(item), start=1):
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
    artifact, path = browser.check_acceptance_outcomes(
        candidates=candidates,
        input_path=input_path,
        out=out,
        offset=offset,
        limit=limit,
        delay_ms=delay_ms,
    )
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
    store: Store, *, min_age_days: int, max_age_days: int | None, as_json: bool = False
) -> str:
    ledger = store.load_acceptance_ledger()
    report = ledger.report(min_age_days, max_age_days)
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
    draft_followups: bool,
    followup_out: Path | None,
    followup_review_out: Path | None,
    followup_research_out_dir: Path | None,
    include_drafted: bool,
    strategy: DraftStrategy,
    research_delay_ms: int,
) -> str:
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
            acceptance_report(
                store,
                min_age_days=min_age_days,
                max_age_days=max_age_days,
                as_json=False,
            )
        )
        return "\n".join(messages)

    browser = browser_factory()
    try:
        check_messages = _acceptance_check_and_import_chunks(
            store,
            browser,
            candidates=candidates,
            candidates_out=candidates_out,
            outcomes_out=outcomes_out,
            chunk_dir=chunk_dir,
            chunk_size=chunk_size,
            retries=chunk_retries,
            delay_ms=check_delay_ms,
        )
        messages.extend(check_messages)
        if any(message.startswith("stopped:") for message in check_messages):
            messages.append(
                acceptance_report(
                    store,
                    min_age_days=min_age_days,
                    max_age_days=max_age_days,
                    as_json=False,
                )
            )
            return "\n".join(messages)
        if draft_followups:
            messages.append(
                acceptance_draft_followups(
                    store,
                    research=None,
                    out=followup_out,
                    include_drafted=include_drafted,
                    strategy=strategy,
                    browser=browser,
                    research_out_dir=followup_research_out_dir,
                    delay_ms=research_delay_ms,
                    review_out=followup_review_out,
                )
            )
    finally:
        close = getattr(browser, "close", None)
        if callable(close):
            close()
    messages.append(
        acceptance_report(
            store,
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            as_json=False,
        )
    )
    return "\n".join(messages)


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
) -> list[str]:
    chunk_size = max(1, chunk_size)
    retries = max(0, retries)
    attempts = 1 + retries
    chunk_dir.mkdir(parents=True, exist_ok=True)
    messages: list[str] = []
    chunk_paths: list[Path] = []
    blockers: list[str] = []
    for offset in range(0, len(candidates), chunk_size):
        limit = min(chunk_size, len(candidates) - offset)
        chunk_path = chunk_dir / f"chunk-{offset}.json"
        if chunk_path.exists():
            existing = read_model(chunk_path, AcceptanceOutcomeArtifact)
            existing_blocked_rows = [
                row
                for row in existing.rows
                if str(getattr(row, "status", "")).lower() == "blocked"
            ]
            if (
                existing.complete is True
                and existing.input == str(candidates_out)
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
            return messages
        artifact = read_model(chunk_path, AcceptanceOutcomeArtifact)
        chunk_paths.append(chunk_path)
        if artifact.complete is not True:
            blockers.append(f"{chunk_path} is incomplete")
        if len(artifact.rows) != limit:
            blockers.append(f"{chunk_path} has {len(artifact.rows)}/{limit} rows")
        blocked_rows = [
            row
            for row in artifact.rows
            if str(getattr(row, "status", "")).lower() == "blocked"
        ]
        if blocked_rows:
            blockers.append(f"{chunk_path} has {len(blocked_rows)} blocked rows")
    if blockers:
        store.append_acceptance_event(
            "run-daily-session-blocked",
            {"reason": "incomplete chunks", "blockers": blockers},
        )
        messages.append("stopped: " + "; ".join(blockers))
        return messages

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
        messages.append(
            f"stopped: merged acceptance row count {len(rows)} "
            f"does not equal candidate count {len(candidates)}"
        )
        return messages

    merged = AcceptanceOutcomeArtifact(
        captured_at=now_utc().isoformat(),
        input=str(candidates_out),
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
    return messages


def acceptance_draft_followups(
    store: Store,
    *,
    research: Path | None,
    out: Path | None,
    include_drafted: bool,
    strategy: DraftStrategy,
    browser: BrowserClient | None = None,
    research_out_dir: Path | None = None,
    delay_ms: int = 500,
    research_offset: int = 0,
    research_limit: int = 0,
    review_out: Path | None = None,
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    candidates = ledger.accepted_for_followup(followups, include_drafted)
    if research_limit > 0:
        report_candidates = candidates[research_offset : research_offset + research_limit]
    elif research_offset > 0:
        report_candidates = candidates[research_offset:]
    else:
        report_candidates = candidates
    report_path = out or store.default_acceptance_followup_report_path()
    generated_research: Path | None = None
    if report_candidates and research is None:
        if browser is None:
            raise RuntimeError("--session is required when --research is not provided")
        generated_dir = research_out_dir or (store.dir / "acceptance-followups" / "research")
        generated_dir.mkdir(parents=True, exist_ok=True)
        candidates_path = generated_dir / "accepted-candidates.json"
        generated_research = generated_dir / "accepted-research.json"
        write_json_atomic(
            candidates_path,
            [candidate.model_dump(mode="json", by_alias=False) for candidate in candidates],
        )
        browser.research_accepted_candidates(
            candidates=candidates,
            input_path=candidates_path,
            out=generated_research,
            offset=research_offset,
            limit=research_limit,
            delay_ms=delay_ms,
        )
        research = generated_research
    artifact = read_model(research, AcceptedResearchArtifact) if research else None
    report = build_draft_report(
        report_candidates, artifact, strategy, str(research) if research is not None else None
    )
    add_lead_review_context_to_draft_report(report, store.load_lead_ledger())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_draft_markdown(report))
    review_path = review_out or report_path.with_suffix(".review.json")
    review_packet = build_accepted_followup_review_packet(
        report,
        artifact,
        report_path=report_path,
        research_path=research,
    )
    review_markdown_path = write_accepted_followup_review_packet(review_packet, review_path)
    recorded = followups.record_report(
        report, str(report_path), str(research) if research else None
    )
    store.save_acceptance_followup_ledger(followups)
    store.append_acceptance_event(
        "draft-followups",
        {
            "report_path": str(report_path),
            "research_path": str(research) if research else None,
            "draft_count": len(report.items),
            "recorded": recorded,
            "strategy": strategy.value,
            "include_drafted": include_drafted,
            "generated_research": str(generated_research) if generated_research else None,
            "review_path": str(review_path),
            "review_markdown_path": str(review_markdown_path),
        },
    )
    suffix = f"; research artifact: {research}" if research else ""
    suffix += f"; review packet: {review_path}"
    if not report.items:
        return (
            f"accepted follow-up drafts: 0 written to {report_path}; "
            "no newly accepted connections need first-message drafts; "
            f"review packet: {review_path}"
        )
    return (
        f"accepted follow-up drafts: {len(report.items)} written to {report_path}{suffix}; "
        "stopped before dry-run/send for review"
    )


def acceptance_research(
    store: Store,
    browser: BrowserClient,
    *,
    input_path: Path,
    out: Path,
    offset: int,
    limit: int,
    delay_ms: int,
) -> str:
    candidates = load_accepted_draft_candidates(input_path)
    artifact, path = browser.research_accepted_candidates(
        candidates=candidates,
        input_path=input_path,
        out=out,
        offset=offset,
        limit=limit,
        delay_ms=delay_ms,
    )
    store.append_acceptance_event(
        "research",
        {
            "input": str(input_path),
            "out": path,
            "count": len(artifact.rows),
            "offset": offset,
            "limit": limit,
        },
    )
    return f"accepted research: {len(artifact.rows)} rows written to {path}"


def acceptance_export_followup_candidates(
    store: Store, *, out: Path, include_drafted: bool
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    candidates = ledger.accepted_for_followup(followups, include_drafted)
    write_json_atomic(
        out, [candidate.model_dump(mode="json", by_alias=False) for candidate in candidates]
    )
    store.append_acceptance_event(
        "export-followup-candidates",
        {"out": str(out), "count": len(candidates), "include_drafted": include_drafted},
    )
    return f"exported {len(candidates)} accepted follow-up candidates to {out}"


def acceptance_export_research_queue(
    store: Store,
    *,
    out: Path,
    markdown_out: Path | None,
    offset: int,
    limit: int,
    include_drafted: bool,
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    candidates = ledger.accepted_for_followup(followups, include_drafted)
    if limit > 0:
        selected = candidates[offset : offset + limit]
    elif offset > 0:
        selected = candidates[offset:]
    else:
        selected = candidates
    packet = build_accepted_research_queue_packet(selected)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, packet.model_dump(mode="json", by_alias=False))
    markdown_path = markdown_out or out.with_suffix(".md")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_accepted_research_queue_markdown(packet), encoding="utf-8")
    store.append_acceptance_event(
        "export-research-queue",
        {
            "out": str(out),
            "markdown_out": str(markdown_path),
            "count": len(packet.items),
            "offset": offset,
            "limit": limit,
            "include_drafted": include_drafted,
        },
    )
    return (
        f"exported {len(packet.items)} accepted research queue item(s) to {out}; "
        f"markdown: {markdown_path}"
    )


def acceptance_export_message_queue(
    store: Store,
    *,
    reviewed_research: Path,
    out: Path,
    markdown_out: Path | None,
    include_drafted: bool,
    offset: int,
    limit: int,
) -> str:
    artifact = read_model(reviewed_research, AcceptedResearchDecisionArtifact)
    followups = store.load_acceptance_followup_ledger()
    decisions = [
        decision
        for decision in artifact.decisions
        if decision.status
        in {
            AcceptedResearchDecisionStatus.RESEARCH_READY,
            AcceptedResearchDecisionStatus.READY_FOR_DRAFT,
        }
        and (include_drafted or not followups.has_draft_for(decision.candidate))
    ]
    if limit > 0:
        selected = decisions[offset : offset + limit]
    elif offset > 0:
        selected = decisions[offset:]
    else:
        selected = decisions
    packet = build_accepted_message_queue_packet(selected)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, packet.model_dump(mode="json", by_alias=False))
    markdown_path = markdown_out or out.with_suffix(".md")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_accepted_message_queue_markdown(packet), encoding="utf-8")
    store.append_acceptance_event(
        "export-message-queue",
        {
            "reviewed_research": str(reviewed_research),
            "out": str(out),
            "markdown_out": str(markdown_path),
            "count": len(packet.items),
            "offset": offset,
            "limit": limit,
            "include_drafted": include_drafted,
        },
    )
    return (
        f"exported {len(packet.items)} accepted message queue item(s) to {out}; "
        f"markdown: {markdown_path}"
    )


def acceptance_launch_codex_research_workers(
    store: Store,
    *,
    research_queue: Path,
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
    packet = read_model(research_queue, AcceptedResearchQueuePacket)
    items = _select_queue_items(packet.items, offset=offset, limit=limit)
    if not items:
        raise RuntimeError("no accepted research queue item(s) selected")
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
        sources_path = write_research_source_bundle(
            item,
            sources_dir=sources_dir,
            timeout_seconds=fetch_timeout_seconds,
        )
        context = {
            "research_queue": str(research_queue),
            "queue_generated_at": packet.generated_at.isoformat(),
            "item": item.model_dump(mode="json", by_alias=False),
            "sources_path": str(sources_path),
        }
        write_json_atomic(context_path, context)
        packet_path.write_text(
            render_accepted_codex_research_prompt(
                item,
                context_path=context_path,
                sources_path=sources_path,
            ),
            encoding="utf-8",
        )
        write_json_atomic(schema_path, CODEX_RESEARCH_OUTPUT_SCHEMA)
        command = codex_draft_worker_command(
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
        job = AcceptedCodexResearchJob(
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
        "launch-codex-research-workers",
        {
            "research_queue": str(research_queue),
            "jobs_dir": str(jobs_dir),
            "sources_dir": str(sources_dir),
            "count": len(items),
            "launched": launched,
            "skipped": skipped,
            "offset": offset,
            "limit": limit,
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
    )
    return (
        f"launched {launched} Codex research worker(s) from {research_queue}; "
        f"{skipped} existing job(s) skipped; jobs: {jobs_dir}; sources: {sources_dir}"
    )


def acceptance_collect_codex_research_workers(
    store: Store,
    *,
    research_queue: Path,
    jobs_dir: Path,
    out: Path,
    offset: int,
    limit: int,
) -> str:
    packet = read_model(research_queue, AcceptedResearchQueuePacket)
    items = _select_queue_items(packet.items, offset=offset, limit=limit)
    if not items:
        raise RuntimeError("no accepted research queue item(s) selected")
    decisions: list[AcceptedResearchDecisionItem] = []
    pending = 0
    for item in items:
        result_path = jobs_dir / item.followup_id / "result.json"
        if not result_path.exists():
            pending += 1
            continue
        result = _load_codex_research_result(result_path)
        decisions.append(_codex_research_result_to_decision(item, result))
    if not decisions and pending:
        store.append_acceptance_event(
            "collect-codex-research-workers-pending",
            {
                "research_queue": str(research_queue),
                "jobs_dir": str(jobs_dir),
                "pending": pending,
                "offset": offset,
                "limit": limit,
            },
        )
        raise RuntimeError(
            f"no completed Codex research worker result(s); {pending} pending in {jobs_dir}"
        )
    artifact = AcceptedResearchDecisionArtifact(
        source_path=str(jobs_dir),
        decisions=decisions,
    )
    validate_accepted_research_decision_artifact(artifact)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, artifact.model_dump(mode="json", by_alias=False))
    research_ready = sum(
        1
        for decision in decisions
        if decision.status == AcceptedResearchDecisionStatus.RESEARCH_READY
    )
    needs_review = sum(
        1
        for decision in decisions
        if decision.status == AcceptedResearchDecisionStatus.NEEDS_REVIEW
    )
    skipped = sum(
        1 for decision in decisions if decision.status == AcceptedResearchDecisionStatus.SKIP
    )
    store.append_acceptance_event(
        "collect-codex-research-workers",
        {
            "research_queue": str(research_queue),
            "jobs_dir": str(jobs_dir),
            "out": str(out),
            "completed": len(decisions),
            "pending": pending,
            "research_ready": research_ready,
            "needs_review": needs_review,
            "skipped": skipped,
            "offset": offset,
            "limit": limit,
        },
    )
    return (
        f"collected {len(decisions)} Codex research worker result(s): "
        f"{research_ready} research ready, {needs_review} needs review, "
        f"{skipped} skipped, {pending} pending; wrote {out}"
    )


def acceptance_launch_codex_draft_workers(
    store: Store,
    *,
    message_queue: Path,
    jobs_dir: Path,
    codex_bin: str,
    cwd: Path,
    model: str,
    reasoning_effort: str,
    offset: int,
    limit: int,
    force: bool,
) -> str:
    packet = read_model(message_queue, AcceptedResearchQueuePacket)
    items = _select_queue_items(packet.items, offset=offset, limit=limit)
    jobs_dir.mkdir(parents=True, exist_ok=True)
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
        if not force and (result_path.exists() or job_path.exists()):
            skipped += 1
            continue
        job_dir.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(render_accepted_codex_draft_prompt(item), encoding="utf-8")
        write_json_atomic(schema_path, CODEX_DRAFT_OUTPUT_SCHEMA)
        command = codex_draft_worker_command(
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
        job = AcceptedCodexDraftJob(
            followup_id=item.followup_id,
            candidate_key=item.candidate_key,
            packet_path=str(packet_path),
            schema_path=str(schema_path),
            result_path=str(result_path),
            events_path=str(events_path),
            stderr_path=str(stderr_path),
            pid=process.pid,
            command=command,
        )
        write_json_atomic(job_path, job.model_dump(mode="json", by_alias=False))
        launched += 1
    store.append_acceptance_event(
        "launch-codex-draft-workers",
        {
            "message_queue": str(message_queue),
            "jobs_dir": str(jobs_dir),
            "count": len(items),
            "launched": launched,
            "skipped": skipped,
            "offset": offset,
            "limit": limit,
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
    )
    return (
        f"launched {launched} Codex draft worker(s) from {message_queue}; "
        f"{skipped} existing job(s) skipped; jobs: {jobs_dir}"
    )


def acceptance_collect_codex_draft_workers(
    store: Store,
    *,
    message_queue: Path,
    jobs_dir: Path,
    out: Path,
    offset: int,
    limit: int,
) -> str:
    packet = read_model(message_queue, AcceptedResearchQueuePacket)
    decisions, pending, _selected_count = _collect_codex_draft_worker_decisions(
        packet,
        jobs_dir=jobs_dir,
        offset=offset,
        limit=limit,
    )
    if not decisions and pending:
        store.append_acceptance_event(
            "collect-codex-draft-workers-pending",
            {
                "message_queue": str(message_queue),
                "jobs_dir": str(jobs_dir),
                "pending": pending,
                "offset": offset,
                "limit": limit,
            },
        )
        raise RuntimeError(
            f"no completed Codex draft worker result(s); {pending} pending in {jobs_dir}"
        )
    artifact = AcceptedResearchDecisionArtifact(
        source_path=str(jobs_dir),
        decisions=decisions,
    )
    validate_accepted_research_decision_artifact(artifact)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, artifact.model_dump(mode="json", by_alias=False))
    ready = sum(
        1
        for decision in decisions
        if decision.status == AcceptedResearchDecisionStatus.READY_FOR_DRAFT
    )
    needs_review = sum(
        1
        for decision in decisions
        if decision.status == AcceptedResearchDecisionStatus.NEEDS_REVIEW
    )
    store.append_acceptance_event(
        "collect-codex-draft-workers",
        {
            "message_queue": str(message_queue),
            "jobs_dir": str(jobs_dir),
            "out": str(out),
            "completed": len(decisions),
            "pending": pending,
            "ready": ready,
            "needs_review": needs_review,
            "offset": offset,
            "limit": limit,
        },
    )
    return (
        f"collected {len(decisions)} Codex draft worker result(s): {ready} ready, "
        f"{needs_review} needs review, {pending} pending; wrote {out}"
    )


def acceptance_finalize_codex_draft_workers(
    store: Store,
    *,
    message_queue: Path,
    jobs_dir: Path,
    message_decisions_out: Path,
    reviewed_research_out: Path,
    draft_out: Path,
    review_out: Path,
    codex_bin: str,
    cwd: Path,
    model: str,
    reasoning_effort: str,
    offset: int,
    limit: int,
    force: bool,
    wait_seconds: float,
    poll_seconds: float,
    include_drafted: bool,
    strategy: DraftStrategy,
) -> str:
    if wait_seconds < 0:
        raise ValueError("wait_seconds must be >= 0")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be > 0")
    launch_output = acceptance_launch_codex_draft_workers(
        store,
        message_queue=message_queue,
        jobs_dir=jobs_dir,
        codex_bin=codex_bin,
        cwd=cwd,
        model=model,
        reasoning_effort=reasoning_effort,
        offset=offset,
        limit=limit,
        force=force,
    )
    packet = read_model(message_queue, AcceptedResearchQueuePacket)
    deadline = time.monotonic() + wait_seconds
    decisions: list[AcceptedResearchDecisionItem] = []
    pending = 0
    selected_count = 0
    while True:
        decisions, pending, selected_count = _collect_codex_draft_worker_decisions(
            packet,
            jobs_dir=jobs_dir,
            offset=offset,
            limit=limit,
        )
        if selected_count == 0:
            raise RuntimeError("no accepted message queue item(s) selected")
        if pending == 0:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))
    if not decisions:
        store.append_acceptance_event(
            "finalize-codex-draft-workers-pending",
            {
                "message_queue": str(message_queue),
                "jobs_dir": str(jobs_dir),
                "pending": pending,
                "offset": offset,
                "limit": limit,
                "wait_seconds": wait_seconds,
            },
        )
        raise RuntimeError(
            "no completed Codex draft worker result(s) after waiting "
            f"{wait_seconds:g} second(s); {pending} pending in {jobs_dir}"
        )
    artifact = AcceptedResearchDecisionArtifact(
        source_path=str(jobs_dir),
        decisions=decisions,
    )
    validate_accepted_research_decision_artifact(artifact)
    message_decisions_out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        message_decisions_out,
        artifact.model_dump(mode="json", by_alias=False),
    )
    ready = sum(
        1
        for decision in decisions
        if decision.status == AcceptedResearchDecisionStatus.READY_FOR_DRAFT
    )
    needs_review = sum(
        1
        for decision in decisions
        if decision.status == AcceptedResearchDecisionStatus.NEEDS_REVIEW
    )
    acceptance_apply_research_decisions(
        store,
        input_path=message_decisions_out,
        out=reviewed_research_out,
    )
    draft_output = acceptance_draft_reviewed_followups(
        store,
        reviewed_research=reviewed_research_out,
        out=draft_out,
        include_drafted=include_drafted,
        strategy=strategy,
        review_out=review_out,
    )
    store.append_acceptance_event(
        "finalize-codex-draft-workers",
        {
            "message_queue": str(message_queue),
            "jobs_dir": str(jobs_dir),
            "message_decisions_out": str(message_decisions_out),
            "reviewed_research_out": str(reviewed_research_out),
            "draft_out": str(draft_out),
            "review_out": str(review_out),
            "completed": len(decisions),
            "pending": pending,
            "ready": ready,
            "needs_review": needs_review,
            "selected": selected_count,
            "offset": offset,
            "limit": limit,
            "wait_seconds": wait_seconds,
        },
    )
    return (
        "finalized accepted message queue: "
        f"{launch_output}; collected {len(decisions)} Codex draft worker result(s): "
        f"{ready} ready, {needs_review} needs review, {pending} pending; "
        f"message decisions: {message_decisions_out}; "
        f"reviewed research: {reviewed_research_out}; {draft_output}"
    )


def acceptance_apply_research_decisions(
    store: Store,
    *,
    input_path: Path,
    out: Path,
) -> str:
    artifact = load_accepted_research_decisions(input_path)
    artifact.source_path = str(input_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, artifact.model_dump(mode="json", by_alias=False))
    ready = sum(
        1
        for decision in artifact.decisions
        if decision.status == AcceptedResearchDecisionStatus.READY_FOR_DRAFT
    )
    needs_review = sum(
        1
        for decision in artifact.decisions
        if decision.status == AcceptedResearchDecisionStatus.NEEDS_REVIEW
    )
    research_ready = sum(
        1
        for decision in artifact.decisions
        if decision.status == AcceptedResearchDecisionStatus.RESEARCH_READY
    )
    skipped = sum(
        1
        for decision in artifact.decisions
        if decision.status == AcceptedResearchDecisionStatus.SKIP
    )
    store.append_acceptance_event(
        "apply-research-decisions",
        {
            "input": str(input_path),
            "out": str(out),
            "count": len(artifact.decisions),
            "ready": ready,
            "research_ready": research_ready,
            "needs_review": needs_review,
            "skipped": skipped,
        },
    )
    return (
        f"applied accepted research decisions: {ready} ready, "
        f"{research_ready} research ready, {needs_review} needs review, "
        f"{skipped} skipped; wrote {out}"
    )


def acceptance_draft_reviewed_followups(
    store: Store,
    *,
    reviewed_research: Path,
    out: Path | None,
    include_drafted: bool,
    strategy: DraftStrategy,
    review_out: Path | None,
) -> str:
    artifact = read_model(reviewed_research, AcceptedResearchDecisionArtifact)
    followups = store.load_acceptance_followup_ledger()
    if include_drafted:
        selected = artifact
    else:
        selected = artifact.model_copy(
            update={
                "decisions": [
                    decision
                    for decision in artifact.decisions
                    if not followups.has_draft_for(decision.candidate)
                ]
            }
        )
    report_path = out or store.default_acceptance_followup_report_path()
    report = build_draft_report_from_reviewed_research(
        selected, strategy, str(reviewed_research)
    )
    add_lead_review_context_to_draft_report(report, store.load_lead_ledger())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_draft_markdown(report), encoding="utf-8")
    review_path = review_out or report_path.with_suffix(".review.json")
    review_packet = build_reviewed_accepted_followup_review_packet(
        report,
        selected,
        report_path=report_path,
        research_path=reviewed_research,
    )
    review_markdown_path = write_accepted_followup_review_packet(review_packet, review_path)
    recorded = followups.record_report(report, str(report_path), str(reviewed_research))
    store.save_acceptance_followup_ledger(followups)
    store.append_acceptance_event(
        "draft-reviewed-followups",
        {
            "reviewed_research": str(reviewed_research),
            "report_path": str(report_path),
            "review_path": str(review_path),
            "review_markdown_path": str(review_markdown_path),
            "draft_count": len(report.items),
            "recorded": recorded,
            "strategy": strategy.value,
            "include_drafted": include_drafted,
        },
    )
    return (
        f"accepted follow-up drafts from reviewed research: {len(report.items)} "
        f"written to {report_path}; review packet: {review_path}; "
        "stopped before dry-run/send for review"
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
        "send-followup",
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


def acceptance_retry_send_followup(
    store: Store,
    browser: BrowserClient,
    *,
    record_id: str,
    allow_send: bool,
) -> str:
    if not allow_send:
        raise RuntimeError("retry-send-followup requires --allow-send")
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
                "retry-send-followup stopped: dry-run did not make the follow-up ready",
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
        raise RuntimeError("send-ready-followups requires --allow-send")
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
    return "\n".join(["Accepted follow-up send summary", render_row(headers), divider] + [
        render_row(row) for row in rows
    ])


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


def load_accepted_draft_candidates(path: Path) -> list[AcceptedDraftCandidate]:
    return [
        AcceptedDraftCandidate.model_validate(item)
        for item in _load_json_list(path, "accepted draft candidates")
    ]


def _select_queue_items(
    items: list[AcceptedResearchQueueItem], *, offset: int, limit: int
) -> list[AcceptedResearchQueueItem]:
    if limit > 0:
        return items[offset : offset + limit]
    if offset > 0:
        return items[offset:]
    return items


def _load_codex_draft_result(path: Path) -> AcceptedCodexDraftResult:
    data = json.loads(path.read_text())
    result = AcceptedCodexDraftResult.model_validate(data)
    if result.status not in {
        AcceptedResearchDecisionStatus.READY_FOR_DRAFT,
        AcceptedResearchDecisionStatus.NEEDS_REVIEW,
    }:
        raise ValueError(f"unexpected Codex draft status in {path}: {result.status.value}")
    if not _non_empty(result.reason):
        raise ValueError(f"Codex draft result is missing reason: {path}")
    return result


def _collect_codex_draft_worker_decisions(
    packet: AcceptedResearchQueuePacket,
    *,
    jobs_dir: Path,
    offset: int,
    limit: int,
) -> tuple[list[AcceptedResearchDecisionItem], int, int]:
    items = _select_queue_items(packet.items, offset=offset, limit=limit)
    decisions: list[AcceptedResearchDecisionItem] = []
    pending = 0
    for item in items:
        result_path = jobs_dir / item.followup_id / "result.json"
        if not result_path.exists():
            pending += 1
            continue
        result = _load_codex_draft_result(result_path)
        decisions.append(_codex_result_to_research_decision(item, result))
    return decisions, pending, len(items)


def _load_codex_research_result(path: Path) -> AcceptedCodexResearchResult:
    data = json.loads(path.read_text())
    result = AcceptedCodexResearchResult.model_validate(data)
    if result.status not in {
        AcceptedResearchDecisionStatus.RESEARCH_READY,
        AcceptedResearchDecisionStatus.NEEDS_REVIEW,
        AcceptedResearchDecisionStatus.SKIP,
    }:
        raise ValueError(f"unexpected Codex research status in {path}: {result.status.value}")
    if result.status == AcceptedResearchDecisionStatus.RESEARCH_READY:
        if result.confidence is None:
            raise ValueError(f"Codex research result is missing confidence: {path}")
        if not result.research_evidence:
            raise ValueError(f"Codex research result is missing research_evidence: {path}")
    return result


def _codex_research_result_to_decision(
    item: AcceptedResearchQueueItem,
    result: AcceptedCodexResearchResult,
) -> AcceptedResearchDecisionItem:
    if result.candidate_key != item.candidate_key:
        raise ValueError(
            "Codex research result candidate_key mismatch: "
            f"expected {item.candidate_key}, got {result.candidate_key}"
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
        "notes": result.notes,
        "warnings": [*item.decision.warnings, *result.warnings],
        "template_key": result.template_key,
        "angle": result.angle,
        "proposed_message": None,
    }
    return AcceptedResearchDecisionItem.model_validate(payload)


def _codex_result_to_research_decision(
    item: AcceptedResearchQueueItem,
    result: AcceptedCodexDraftResult,
) -> AcceptedResearchDecisionItem:
    if result.candidate_key != item.candidate_key:
        raise ValueError(
            "Codex draft result candidate_key mismatch: "
            f"expected {item.candidate_key}, got {result.candidate_key}"
        )
    payload = item.decision.model_dump(mode="python", by_alias=False)
    payload.update(
        {
            "followup_id": item.followup_id,
            "candidate_key": item.candidate_key,
            "candidate": item.candidate,
            "status": result.status,
            "warnings": [*item.decision.warnings, *result.warnings],
            "notes": _append_note(item.decision.notes, result.reason),
        }
    )
    if result.status == AcceptedResearchDecisionStatus.READY_FOR_DRAFT:
        payload["proposed_message"] = normalize_reviewed_proposed_message(result.message)
    else:
        payload["proposed_message"] = None
    return AcceptedResearchDecisionItem.model_validate(payload)


def _append_note(existing: str | None, note: str) -> str:
    cleaned = clean_inline(note)
    if existing and cleaned:
        return f"{clean_inline(existing)} Draft worker: {cleaned}"
    if existing:
        return clean_inline(existing)
    return f"Draft worker: {cleaned}" if cleaned else ""


def load_accepted_research_decisions(path: Path) -> AcceptedResearchDecisionArtifact:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"accepted research decisions must be a JSON object: {path}")
    raw_decisions = data.get("decisions")
    if raw_decisions is None:
        raw_decisions = data.get("items")
    if not isinstance(raw_decisions, list):
        raise ValueError(f"accepted research decisions must contain items/decisions: {path}")
    decisions: list[AcceptedResearchDecisionItem] = []
    for raw_item in raw_decisions:
        if not isinstance(raw_item, dict):
            raise ValueError(f"accepted research decision item must be an object: {path}")
        if "decision" in raw_item:
            decision = raw_item.get("decision") or {}
            if not isinstance(decision, dict):
                raise ValueError(f"accepted research decision must be an object: {path}")
            payload = {
                **decision,
                "followup_id": raw_item.get("followup_id"),
                "candidate_key": raw_item.get("candidate_key"),
                "candidate": raw_item.get("candidate"),
            }
        else:
            payload = raw_item
        decisions.append(AcceptedResearchDecisionItem.model_validate(payload))
    artifact = AcceptedResearchDecisionArtifact(
        source_path=str(path),
        decisions=decisions,
    )
    validate_accepted_research_decision_artifact(artifact)
    return artifact


def _load_json_list(path: Path, label: str) -> list[object]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{label} artifact must be a JSON array: {path}")
    return data
