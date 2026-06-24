"""Draft generation for recruiter/agency outreach."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Lead, LeadStatus, LeadType, MessageDraft, MessageStatus, OutreachState
from .utils import clean_text, contains_any, now_iso


@dataclass(slots=True)
class QueueItem:
    id: str
    name: str
    source: str
    lead_type: LeadType
    status: LeadStatus
    message_status: MessageStatus
    fit_score: int
    fit_reasons: list[str]
    evidence_text: str
    profile_url: str | None = None
    title: str | None = None
    company: str | None = None
    agency_account_name: str | None = None
    agency_account_url: str | None = None
    agency_account_reasons: list[str] | None = None
    agency_account_evidence: str = ""
    draft: str | None = None


@dataclass(slots=True)
class DraftReport:
    generated_at: str
    items: list[QueueItem]


def draft_messages(state: OutreachState, limit: int = 20) -> DraftReport:
    items = draftable_queue(state, limit)
    generated_at = now_iso()
    for item in items:
        lead = next((candidate for candidate in state.leads if candidate.id == item.id), None)
        if lead is None:
            continue
        draft = build_message_draft_record(lead, generated_at)
        lead.draft = draft
        lead.message_status = MessageStatus.DRAFTED
        lead.updated_at = generated_at
        item.message_status = MessageStatus.DRAFTED
        item.draft = draft.body
    return DraftReport(generated_at=generated_at, items=items)


def draftable_queue(state: OutreachState, limit: int = 20) -> list[QueueItem]:
    items: list[QueueItem] = []
    for lead in sorted(state.leads, key=lambda item: (-item.fit_score, item.name)):
        if lead.status != LeadStatus.ELIGIBLE:
            continue
        if is_terminal_message_status(lead.message_status):
            continue
        items.append(queue_item_from_lead(lead, include_draft=False))
        if limit > 0 and len(items) >= limit:
            break
    return items


def is_terminal_message_status(status: MessageStatus) -> bool:
    return status in {
        MessageStatus.DRY_RUN_READY,
        MessageStatus.NEEDS_EDIT,
        MessageStatus.APPROVED,
        MessageStatus.SEND_FAILED,
        MessageStatus.SENT,
        MessageStatus.MANUALLY_SENT,
        MessageStatus.NOT_MESSAGEABLE,
        MessageStatus.CONVERSATION_EXISTS,
        MessageStatus.BLOCKED,
        MessageStatus.REPLIED,
        MessageStatus.REPLIED_NOT_FIT,
        MessageStatus.REPLIED_FUTURE,
        MessageStatus.REPLIED_UNKNOWN,
    }


def build_message_draft_record(lead: Lead, generated_at: str | None = None) -> MessageDraft:
    angle = draft_angle(lead)
    return MessageDraft(
        subject=message_subject(lead),
        body=message_body_for_angle(lead),
        angle=angle,
        evidence=draft_evidence(lead),
        generated_at=generated_at or now_iso(),
    )


def message_body_for_angle(lead: Lead) -> str:
    if lead.lead_type == LeadType.CONTRACT_RECRUITER:
        return recruiter_draft(lead)
    if lead.lead_type in {
        LeadType.AGENCY_RESOURCE,
        LeadType.AGENCY_DELIVERY,
        LeadType.AGENCY_FOUNDER,
    }:
        return agency_draft(lead)
    return recruiter_draft(lead)


def draft_angle(lead: Lead) -> str:
    if lead.lead_type == LeadType.CONTRACT_RECRUITER:
        return "contract recruiter routing for remote C2C/1099 product-engineering work"
    website = is_website_agency_lead(lead)
    if lead.lead_type == LeadType.AGENCY_RESOURCE:
        if website:
            return (
                "web design/WordPress agency resource manager for senior "
                "frontend/CMS implementation support"
            )
        return "agency resource manager for immediate outside senior engineering coverage"
    if lead.lead_type == LeadType.AGENCY_DELIVERY:
        if website:
            return (
                "web design/WordPress agency delivery leader for "
                "frontend-heavy implementation overflow"
            )
        return "agency delivery or technical leader for overflow/rescue/prototyping support"
    if lead.lead_type == LeadType.AGENCY_FOUNDER:
        if website:
            return (
                "web design/WordPress agency founder for senior frontend/CMS "
                "implementation capacity"
            )
        return "agency founder/partner for senior contractor capacity on active client work"
    return "general contract product-engineering availability"


def draft_evidence(lead: Lead) -> list[str]:
    evidence: list[str] = []
    if lead.title:
        evidence.append(f"Title: {lead.title}")
    company = company_for_draft(lead.company)
    if company:
        evidence.append(f"Company: {company}")
    if lead.agency_account_name:
        evidence.append(f"Agency account: {lead.agency_account_name}")
    if lead.agency_account_reasons:
        evidence.append("Agency account reasons: " + "; ".join(lead.agency_account_reasons))
    if lead.fit_reasons:
        evidence.append("Fit reasons: " + "; ".join(lead.fit_reasons))
    if lead.agency_account_evidence:
        evidence.append(f"Agency account evidence: {lead.agency_account_evidence}")
    if lead.evidence_text:
        evidence.append(f"Sales Nav evidence: {lead.evidence_text}")
    return evidence


def recruiter_draft(lead: Lead) -> str:
    opener = recruiter_opening(lead)
    return (
        f"Hi {lead.first_name},\n\n"
        f"{opener}\n\n"
        "I'm a full-stack product engineer (8 YoE) that builds and launches "
        "AI-powered web & mobile products.\n\n"
        "Recent projects:\n\n"
        "• Turned an AI media MVP into a production agent platform for Amazon "
        "sellers (first 100 paying customers)\n"
        "• Built and launched a Spanish reading app (iOS, Android + web) from "
        "concept to App Store with teacher workflows, AI features, and subscriptions\n\n"
        "US citizen contracting via my LLC (1099/C2C). Available for US-hours work "
        "from Buenos Aires.\n\n"
        "Are you the right person to ask about contract roles that fit this background?"
    )


def agency_draft(lead: Lead) -> str:
    target = clean_text(lead.agency_account_name) or company_for_draft(lead.company)
    return agency_project_draft(lead.first_name, target)


def agency_project_draft(first_name: str, agency_name: str) -> str:
    intro = (
        "I'm a full-stack product engineer (8 YoE) that builds and launches "
        "AI-powered web & mobile products. I'm reaching out about project or overflow work."
    )
    cleaned_agency = clean_text(agency_name)
    if cleaned_agency:
        intro = (
            "I'm a full-stack product engineer (8 YoE) that builds and launches "
            f"AI-powered web & mobile products. I came across {cleaned_agency}, "
            "and I'm reaching out about project or overflow work."
        )
    return (
        f"Hi {first_name},\n\n"
        f"{intro}\n\n"
        "Recent projects:\n\n"
        "• Turned an AI media MVP into a production agent platform for Amazon "
        "sellers (first 100 paying customers)\n"
        "• Built and launched a Spanish reading app (iOS, Android + web) from "
        "concept to App Store with teacher workflows, AI features, and subscriptions\n\n"
        "US citizen contracting via my LLC (1099/C2C). Available for US-hours work "
        "from Buenos Aires. Comfortable collaborating with design and product teams.\n\n"
        "Are you the right person to ask about this kind of project support?"
    )


def recruiter_opening(lead: Lead) -> str:
    focus = recruiter_role_focus(lead)
    company = company_for_draft(lead.company)
    if company:
        return (
            f"I saw that you recruit for {focus} at {company}, "
            "and I'm reaching out about contract work."
        )
    return f"I saw that you recruit for {focus}, and I'm reaching out about contract work."


def recruiter_role_focus(lead: Lead) -> str:
    title = clean_text(lead.title).lower()
    if contains_any(title, "technical", "tech ", "software", "engineering", "developer"):
        return "contract technical roles"
    if contains_any(title, "gtm", "go-to-market"):
        return "contract GTM roles"
    if contains_any(title, "talent acquisition", "sourcer", "headhunter"):
        return "contract recruiting roles"
    return "contract roles"


def is_website_agency_lead(lead: Lead) -> bool:
    parts = [
        lead.agency_account_evidence,
        lead.evidence_text,
        *(lead.agency_account_reasons or []),
        *(lead.fit_reasons or []),
        lead.agency_account_name or "",
        lead.company or "",
    ]
    text = " ".join(parts).lower()
    return contains_any(
        text,
        "website/wordpress build account signal",
        "wordpress",
        "shopify",
        "webflow",
        "cms",
        "web design",
        "web designer",
        "web developer",
        "website design",
        "website development",
        "high-performing websites",
    )


def company_for_draft(company: str | None) -> str:
    value = clean_text(company)
    if not value or is_likely_location(value):
        return ""
    return value


def is_likely_location(value: str) -> bool:
    lower = clean_text(value).lower()
    return (
        contains_any(lower, "metropolitan area", "bay area", "united states")
        or value.count(",") >= 2
    )


def message_subject(lead: Lead) -> str:
    if lead.lead_type in {
        LeadType.AGENCY_RESOURCE,
        LeadType.AGENCY_DELIVERY,
        LeadType.AGENCY_FOUNDER,
    }:
        return "Full-Stack Product Engineer Available for Project Work"
    return "Full-Stack + AI Product Engineer | Open to Contract Work"


def draft_subject(lead: Lead) -> str:
    if lead.draft and clean_text(lead.draft.subject):
        return clean_text(lead.draft.subject)
    return message_subject(lead)


def queue_item_from_lead(lead: Lead, *, include_draft: bool) -> QueueItem:
    return QueueItem(
        id=lead.id,
        name=lead.name,
        profile_url=lead.profile_url,
        title=lead.title,
        company=lead.company,
        agency_account_name=lead.agency_account_name,
        agency_account_url=lead.agency_account_url,
        agency_account_reasons=list(lead.agency_account_reasons),
        agency_account_evidence=lead.agency_account_evidence,
        source=lead.source,
        lead_type=lead.lead_type,
        status=lead.status,
        message_status=lead.message_status,
        fit_score=lead.fit_score,
        fit_reasons=list(lead.fit_reasons),
        evidence_text=lead.evidence_text,
        draft=lead.draft.body if include_draft and lead.draft else None,
    )


def render_draft_markdown(report: DraftReport) -> str:
    lines = [
        f"# Recruiter And Agency Drafts {report.generated_at[:10]}",
        "",
        f"- Generated: `{report.generated_at}`",
        f"- Draft count: {len(report.items)}",
        (
            "- Send policy: draft-only. No connection request or LinkedIn "
            "message was sent by this command."
        ),
    ]
    if not report.items:
        lines.extend(["", "No eligible recruiter or agency leads need drafts."])
        return "\n".join(lines)
    for item in report.items:
        lines.extend(["", f"## {clean_inline(item.name)}"])
        lines.append(f"- ID: `{item.id}`")
        lines.append(f"- Source: {clean_inline(item.source)}")
        lines.append(f"- Type: `{item.lead_type.value}`")
        lines.append(f"- Fit score: `{item.fit_score}`")
        if item.profile_url:
            lines.append(f"- Profile: {clean_inline(item.profile_url)}")
        if item.title:
            lines.append(f"- Title: {clean_inline(item.title)}")
        if item.company:
            lines.append(f"- Company: {clean_inline(item.company)}")
        if item.agency_account_name:
            lines.append(f"- Agency account: {clean_inline(item.agency_account_name)}")
        if item.fit_reasons:
            lines.append("- Fit reasons: " + clean_inline("; ".join(item.fit_reasons)))
        if item.evidence_text:
            lines.append(f"- Evidence: {clean_inline(item.evidence_text)}")
        lines.extend(["", "Draft:", ""])
        lines.extend(render_markdown_quote(item.draft or "No draft generated."))
    return "\n".join(lines)


def write_draft_markdown(path: str, report: DraftReport) -> None:
    from pathlib import Path

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_draft_markdown(report))


def render_markdown_quote(value: str) -> list[str]:
    lines: list[str] = []
    for line in value.replace("\r\n", "\n").strip().split("\n"):
        if not line.strip():
            lines.append(">")
        else:
            lines.append("> " + line.replace("`", "'"))
    return lines or [">"]


def clean_inline(value: str) -> str:
    return clean_text(value).replace("`", "'")
