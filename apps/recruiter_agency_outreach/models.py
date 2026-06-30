"""Typed state model for recruiter/agency/advisor outreach."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LeadType(StrEnum):
    CONTRACT_RECRUITER = "contract_recruiter"
    AGENCY_RESOURCE = "agency_resource"
    AGENCY_DELIVERY = "agency_delivery"
    AGENCY_FOUNDER = "agency_founder"
    AI_ADVISOR_IMPLEMENTATION_PARTNER = "ai_advisor_implementation_partner"
    BAD_FIT = "bad_fit"


class LeadStatus(StrEnum):
    CAPTURED = "captured"
    ELIGIBLE = "eligible"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class AgencyAccountStatus(StrEnum):
    QUALIFIED = "qualified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    EXHAUSTED = "exhausted"


class AgencyContactCandidateStatus(StrEnum):
    WEBSITE_CONTACT_CANDIDATE = "website_contact_candidate"
    GENERIC_INBOX = "generic_inbox"
    CONTACT_FORM = "contact_form"
    REJECTED = "rejected"
    CONVERTED = "converted"


class AgencyContactReviewStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONVERTED = "converted"


class MessageStatus(StrEnum):
    NONE = "none"
    DRAFTED = "drafted"
    NEEDS_EDIT = "needs_edit"
    APPROVED = "approved"
    DRY_RUN_READY = "dry_run_ready"
    SENT = "sent"
    MANUALLY_SENT = "manually_sent"
    NOT_MESSAGEABLE = "not_messageable"
    CONVERSATION_EXISTS = "conversation_exists"
    SEND_FAILED = "send_failed"
    BLOCKED = "blocked"
    REPLIED = "replied"
    REPLIED_NOT_FIT = "replied_not_fit"
    REPLIED_FUTURE = "replied_future"
    REPLIED_UNKNOWN = "replied_unknown"


def _list_str(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _mapping_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _optional_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return default


@dataclass(slots=True)
class MessageDraft:
    subject: str = ""
    body: str = ""
    angle: str = ""
    evidence: list[str] = field(default_factory=list)
    generated_at: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> MessageDraft | None:
        if not data:
            return None
        return cls(
            subject=str(data.get("subject") or ""),
            body=str(data.get("body") or ""),
            angle=str(data.get("angle") or ""),
            evidence=_list_str(data.get("evidence")),
            generated_at=str(data.get("generated_at") or data.get("generatedAt") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "body": self.body,
            "angle": self.angle,
            "evidence": self.evidence,
            "generated_at": self.generated_at,
        }


@dataclass(slots=True)
class SendAttempt:
    at: str = ""
    run_id: str = ""
    dry_run: bool = True
    status: str = ""
    result_url: str | None = None
    note: str | None = None
    out_path: str = ""
    diagnostics: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SendAttempt:
        raw_diagnostics = data.get("diagnostics")
        diagnostics = (
            {str(key): str(value) for key, value in raw_diagnostics.items()}
            if isinstance(raw_diagnostics, Mapping)
            else {}
        )
        return cls(
            at=str(data.get("at") or ""),
            run_id=str(data.get("run_id") or data.get("runID") or ""),
            dry_run=bool(data.get("dry_run", data.get("dryRun", True))),
            status=str(data.get("status") or ""),
            result_url=_optional_str(data.get("result_url") or data.get("resultUrl")),
            note=_optional_str(data.get("note")),
            out_path=str(data.get("out_path") or data.get("outPath") or ""),
            diagnostics=diagnostics,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "status": self.status,
            "result_url": self.result_url,
            "note": self.note,
            "out_path": self.out_path,
            "diagnostics": self.diagnostics,
        }


@dataclass(slots=True)
class Lead:
    id: str
    source: str
    name: str
    first_name: str
    lead_type: LeadType
    status: LeadStatus = LeadStatus.CAPTURED
    message_status: MessageStatus = MessageStatus.NONE
    fit_score: int = 0
    profile_url: str | None = None
    sales_profile_urn: str | None = None
    title: str | None = None
    company: str | None = None
    agency_account_id: str | None = None
    agency_account_name: str | None = None
    agency_account_url: str | None = None
    agency_account_reasons: list[str] = field(default_factory=list)
    agency_account_evidence: str = ""
    fit_reasons: list[str] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)
    evidence_text: str = ""
    menu_state: str = "unknown"
    captured_at: str | None = None
    imported_at: str = ""
    updated_at: str = ""
    message_status_at: str | None = None
    draft: MessageDraft | None = None
    send_attempts: list[SendAttempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Lead:
        return cls(
            id=str(data.get("id") or ""),
            source=str(data.get("source") or ""),
            name=str(data.get("name") or ""),
            first_name=str(data.get("first_name") or data.get("firstName") or ""),
            profile_url=_optional_str(data.get("profile_url") or data.get("profileURL")),
            sales_profile_urn=_optional_str(
                data.get("sales_profile_urn") or data.get("salesProfileURN")
            ),
            title=_optional_str(data.get("title")),
            company=_optional_str(data.get("company")),
            agency_account_id=_optional_str(data.get("agency_account_id")),
            agency_account_name=_optional_str(data.get("agency_account_name")),
            agency_account_url=_optional_str(data.get("agency_account_url")),
            agency_account_reasons=_list_str(data.get("agency_account_reasons")),
            agency_account_evidence=str(data.get("agency_account_evidence") or ""),
            lead_type=LeadType(str(data.get("lead_type") or LeadType.BAD_FIT.value)),
            status=LeadStatus(str(data.get("status") or LeadStatus.CAPTURED.value)),
            message_status=MessageStatus(
                str(data.get("message_status") or MessageStatus.NONE.value)
            ),
            fit_score=_optional_int(data.get("fit_score")),
            fit_reasons=_list_str(data.get("fit_reasons")),
            reject_reasons=_list_str(data.get("reject_reasons")),
            evidence_text=str(data.get("evidence_text") or ""),
            menu_state=str(data.get("menu_state") or "unknown"),
            captured_at=_optional_str(data.get("captured_at")),
            imported_at=str(data.get("imported_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            message_status_at=_optional_str(data.get("message_status_at")),
            draft=MessageDraft.from_mapping(
                data.get("draft") if isinstance(data.get("draft"), Mapping) else None
            ),
            send_attempts=[
                SendAttempt.from_mapping(item)
                for item in _mapping_list(data.get("send_attempts"))
            ],
            notes=_list_str(data.get("notes")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "name": self.name,
            "first_name": self.first_name,
            "profile_url": self.profile_url,
            "sales_profile_urn": self.sales_profile_urn,
            "title": self.title,
            "company": self.company,
            "agency_account_id": self.agency_account_id,
            "agency_account_name": self.agency_account_name,
            "agency_account_url": self.agency_account_url,
            "agency_account_reasons": self.agency_account_reasons,
            "agency_account_evidence": self.agency_account_evidence,
            "lead_type": self.lead_type.value,
            "status": self.status.value,
            "message_status": self.message_status.value,
            "fit_score": self.fit_score,
            "fit_reasons": self.fit_reasons,
            "reject_reasons": self.reject_reasons,
            "evidence_text": self.evidence_text,
            "menu_state": self.menu_state,
            "captured_at": self.captured_at,
            "imported_at": self.imported_at,
            "updated_at": self.updated_at,
            "message_status_at": self.message_status_at,
            "draft": self.draft.to_dict() if self.draft else None,
            "send_attempts": [attempt.to_dict() for attempt in self.send_attempts],
            "notes": self.notes,
        }


@dataclass(slots=True)
class AgencyAccount:
    id: str
    source: str
    name: str
    status: AgencyAccountStatus = AgencyAccountStatus.NEEDS_REVIEW
    fit_score: int = 0
    account_url: str | None = None
    website: str | None = None
    domain: str | None = None
    industry: str | None = None
    headcount: str | None = None
    location: str | None = None
    fit_reasons: list[str] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)
    evidence_text: str = ""
    captured_at: str | None = None
    imported_at: str = ""
    updated_at: str = ""
    last_contact_capture_at: str | None = None
    contact_capture_count: int = 0
    last_contact_strategy: str | None = None
    last_contact_error: str | None = None
    last_contact_error_at: str | None = None
    contact_error_count: int = 0
    last_website_enriched_at: str | None = None
    website_enrichment_count: int = 0
    last_website_enrichment_error: str | None = None
    last_website_enrichment_error_at: str | None = None
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AgencyAccount:
        return cls(
            id=str(data.get("id") or ""),
            source=str(data.get("source") or ""),
            name=str(data.get("name") or ""),
            account_url=_optional_str(data.get("account_url") or data.get("accountURL")),
            website=_optional_str(data.get("website")),
            domain=_optional_str(data.get("domain")),
            industry=_optional_str(data.get("industry")),
            headcount=_optional_str(data.get("headcount")),
            location=_optional_str(data.get("location")),
            status=AgencyAccountStatus(
                str(data.get("status") or AgencyAccountStatus.NEEDS_REVIEW.value)
            ),
            fit_score=_optional_int(data.get("fit_score")),
            fit_reasons=_list_str(data.get("fit_reasons")),
            reject_reasons=_list_str(data.get("reject_reasons")),
            evidence_text=str(data.get("evidence_text") or ""),
            captured_at=_optional_str(data.get("captured_at")),
            imported_at=str(data.get("imported_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            last_contact_capture_at=_optional_str(data.get("last_contact_capture_at")),
            contact_capture_count=_optional_int(data.get("contact_capture_count")),
            last_contact_strategy=_optional_str(data.get("last_contact_strategy")),
            last_contact_error=_optional_str(data.get("last_contact_error")),
            last_contact_error_at=_optional_str(data.get("last_contact_error_at")),
            contact_error_count=_optional_int(data.get("contact_error_count")),
            last_website_enriched_at=_optional_str(data.get("last_website_enriched_at")),
            website_enrichment_count=_optional_int(data.get("website_enrichment_count")),
            last_website_enrichment_error=_optional_str(data.get("last_website_enrichment_error")),
            last_website_enrichment_error_at=_optional_str(
                data.get("last_website_enrichment_error_at")
            ),
            notes=_list_str(data.get("notes")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "name": self.name,
            "account_url": self.account_url,
            "website": self.website,
            "domain": self.domain,
            "industry": self.industry,
            "headcount": self.headcount,
            "location": self.location,
            "status": self.status.value,
            "fit_score": self.fit_score,
            "fit_reasons": self.fit_reasons,
            "reject_reasons": self.reject_reasons,
            "evidence_text": self.evidence_text,
            "captured_at": self.captured_at,
            "imported_at": self.imported_at,
            "updated_at": self.updated_at,
            "last_contact_capture_at": self.last_contact_capture_at,
            "contact_capture_count": self.contact_capture_count,
            "last_contact_strategy": self.last_contact_strategy,
            "last_contact_error": self.last_contact_error,
            "last_contact_error_at": self.last_contact_error_at,
            "contact_error_count": self.contact_error_count,
            "last_website_enriched_at": self.last_website_enriched_at,
            "website_enrichment_count": self.website_enrichment_count,
            "last_website_enrichment_error": self.last_website_enrichment_error,
            "last_website_enrichment_error_at": self.last_website_enrichment_error_at,
            "notes": self.notes,
        }


@dataclass(slots=True)
class AgencyContactCandidate:
    id: str
    agency_account_id: str
    agency_account_name: str
    source: str
    status: AgencyContactCandidateStatus = AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE
    review_status: AgencyContactReviewStatus = AgencyContactReviewStatus.NEEDS_REVIEW
    source_url: str | None = None
    name: str | None = None
    title: str | None = None
    email: str | None = None
    profile_url: str | None = None
    sales_profile_urn: str | None = None
    contact_url: str | None = None
    form_action: str | None = None
    evidence: list[str] = field(default_factory=list)
    promoted_lead_id: str | None = None
    imported_at: str = ""
    updated_at: str = ""
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AgencyContactCandidate:
        return cls(
            id=str(data.get("id") or ""),
            agency_account_id=str(data.get("agency_account_id") or ""),
            agency_account_name=str(data.get("agency_account_name") or ""),
            source=str(data.get("source") or ""),
            source_url=_optional_str(data.get("source_url")),
            status=AgencyContactCandidateStatus(
                str(
                    data.get("status")
                    or AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE.value
                )
            ),
            review_status=AgencyContactReviewStatus(
                str(data.get("review_status") or AgencyContactReviewStatus.NEEDS_REVIEW.value)
            ),
            name=_optional_str(data.get("name")),
            title=_optional_str(data.get("title")),
            email=_optional_str(data.get("email")),
            profile_url=_optional_str(data.get("profile_url")),
            sales_profile_urn=_optional_str(data.get("sales_profile_urn")),
            contact_url=_optional_str(data.get("contact_url")),
            form_action=_optional_str(data.get("form_action")),
            evidence=_list_str(data.get("evidence")),
            promoted_lead_id=_optional_str(data.get("promoted_lead_id")),
            imported_at=str(data.get("imported_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            notes=_list_str(data.get("notes")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agency_account_id": self.agency_account_id,
            "agency_account_name": self.agency_account_name,
            "source": self.source,
            "source_url": self.source_url,
            "status": self.status.value,
            "review_status": self.review_status.value,
            "name": self.name,
            "title": self.title,
            "email": self.email,
            "profile_url": self.profile_url,
            "sales_profile_urn": self.sales_profile_urn,
            "contact_url": self.contact_url,
            "form_action": self.form_action,
            "evidence": self.evidence,
            "promoted_lead_id": self.promoted_lead_id,
            "imported_at": self.imported_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
        }


@dataclass(slots=True)
class CaptureCursor:
    source: str
    updated_at: str
    captured_at: str | None = None
    resume_url: str | None = None
    page_label: str | None = None
    captured_pages: int = 0
    raw_row_count: int = 0
    output_row_count: int = 0
    connectable_count: int = 0
    already_pending_count: int = 0
    state_counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> CaptureCursor:
        raw_state_counts = data.get("state_counts")
        state_counts = (
            {str(key): _optional_int(value) for key, value in raw_state_counts.items()}
            if isinstance(raw_state_counts, Mapping)
            else {}
        )
        return cls(
            source=str(data.get("source") or ""),
            updated_at=str(data.get("updated_at") or ""),
            captured_at=_optional_str(data.get("captured_at")),
            resume_url=_optional_str(data.get("resume_url")),
            page_label=_optional_str(data.get("page_label")),
            captured_pages=_optional_int(data.get("captured_pages")),
            raw_row_count=_optional_int(data.get("raw_row_count")),
            output_row_count=_optional_int(data.get("output_row_count")),
            connectable_count=_optional_int(data.get("connectable_count")),
            already_pending_count=_optional_int(data.get("already_pending_count")),
            state_counts=state_counts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "updated_at": self.updated_at,
            "captured_at": self.captured_at,
            "resume_url": self.resume_url,
            "page_label": self.page_label,
            "captured_pages": self.captured_pages,
            "raw_row_count": self.raw_row_count,
            "output_row_count": self.output_row_count,
            "connectable_count": self.connectable_count,
            "already_pending_count": self.already_pending_count,
            "state_counts": self.state_counts,
        }


@dataclass(slots=True)
class RunEvent:
    at: str
    phase: str
    run_id: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    bucket: str = ""
    lead_id: str = ""
    account_id: str = ""
    name: str = ""
    result: str = ""
    note: str = ""
    out_path: str = ""
    dashboard_path: str = ""
    state_path: str = ""
    target_agencies: int = 0
    target_recruiters: int = 0
    target_advisors: int = 0
    allow_send: bool = False
    started_at: str = ""
    completed_at: str = ""
    blocker: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> RunEvent:
        return cls(
            at=str(data.get("at") or ""),
            run_id=str(data.get("run_id") or ""),
            phase=str(data.get("phase") or ""),
            command=str(data.get("command") or ""),
            args=_list_str(data.get("args")),
            bucket=str(data.get("bucket") or ""),
            lead_id=str(data.get("lead_id") or ""),
            account_id=str(data.get("account_id") or ""),
            name=str(data.get("name") or ""),
            result=str(data.get("result") or ""),
            note=str(data.get("note") or ""),
            out_path=str(data.get("out_path") or ""),
            dashboard_path=str(data.get("dashboard_path") or ""),
            state_path=str(data.get("state_path") or ""),
            target_agencies=_optional_int(data.get("target_agencies")),
            target_recruiters=_optional_int(data.get("target_recruiters")),
            target_advisors=_optional_int(data.get("target_advisors")),
            allow_send=bool(data.get("allow_send", False)),
            started_at=str(data.get("started_at") or ""),
            completed_at=str(data.get("completed_at") or ""),
            blocker=str(data.get("blocker") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "run_id": self.run_id,
            "phase": self.phase,
            "command": self.command,
            "args": self.args,
            "bucket": self.bucket,
            "lead_id": self.lead_id,
            "account_id": self.account_id,
            "name": self.name,
            "result": self.result,
            "note": self.note,
            "out_path": self.out_path,
            "dashboard_path": self.dashboard_path,
            "state_path": self.state_path,
            "target_agencies": self.target_agencies,
            "target_recruiters": self.target_recruiters,
            "target_advisors": self.target_advisors,
            "allow_send": self.allow_send,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "blocker": self.blocker,
        }


@dataclass(slots=True)
class OutreachState:
    schema_version: int = 1
    leads: list[Lead] = field(default_factory=list)
    agency_accounts: list[AgencyAccount] = field(default_factory=list)
    agency_contact_candidates: list[AgencyContactCandidate] = field(default_factory=list)
    capture_cursors: dict[str, CaptureCursor] = field(default_factory=dict)
    run_events: list[RunEvent] = field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> OutreachState:
        raw_cursors = data.get("capture_cursors", {})
        cursors = (
            {
                str(key): CaptureCursor.from_mapping(value)
                for key, value in raw_cursors.items()
                if isinstance(value, Mapping)
            }
            if isinstance(raw_cursors, Mapping)
            else {}
        )
        return cls(
            schema_version=_optional_int(data.get("schema_version"), 1),
            leads=[
                Lead.from_mapping(item)
                for item in _mapping_list(data.get("leads"))
            ],
            agency_accounts=[
                AgencyAccount.from_mapping(item)
                for item in _mapping_list(data.get("agency_accounts"))
            ],
            agency_contact_candidates=[
                AgencyContactCandidate.from_mapping(item)
                for item in _mapping_list(data.get("agency_contact_candidates"))
            ],
            capture_cursors=cursors,
            run_events=[
                RunEvent.from_mapping(item)
                for item in _mapping_list(data.get("run_events"))
            ],
            updated_at=str(data.get("updated_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "leads": [lead.to_dict() for lead in self.leads],
            "agency_accounts": [account.to_dict() for account in self.agency_accounts],
            "agency_contact_candidates": [
                candidate.to_dict() for candidate in self.agency_contact_candidates
            ],
            "capture_cursors": {
                source: cursor.to_dict() for source, cursor in self.capture_cursors.items()
            },
            "run_events": [event.to_dict() for event in self.run_events],
            "updated_at": self.updated_at,
        }
