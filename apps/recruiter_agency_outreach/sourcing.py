"""Capture and source import behavior for recruiter/agency outreach."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import account_from_capture_row, lead_from_capture_row, link_lead_to_agency_account
from .models import (
    AgencyAccount,
    AgencyAccountStatus,
    AgencyContactCandidate,
    AgencyContactCandidateStatus,
    AgencyContactReviewStatus,
    CaptureCursor,
    Lead,
    LeadStatus,
    LeadType,
    MessageStatus,
    OutreachState,
)
from .utils import (
    clean_text,
    dedupe_strings,
    domain_from_website,
    is_generic_inbox,
    normalize_email,
    normalized_linkedin_account_url,
    normalized_public_profile_url,
    optional_clean,
    sales_profile_urn_to_lead_url,
    stable_agency_account_id,
    stable_agency_contact_candidate_id,
    truncate_evidence,
)

AGENCY_SOURCE_SCHEMA_VERSION = 1


@dataclass(slots=True)
class ImportSummary:
    source: str = ""
    stored: int = 0
    updated: int = 0
    rejected: int = 0
    reviewed: int = 0
    eligible: int = 0
    total_leads: int = 0


@dataclass(slots=True)
class AccountImportSummary:
    source: str = ""
    stored: int = 0
    updated: int = 0
    qualified: int = 0
    needs_review: int = 0
    rejected: int = 0
    total: int = 0


@dataclass(slots=True)
class AgencySourceImportSummary:
    source: str = ""
    stored: int = 0
    updated: int = 0
    qualified: int = 0
    needs_review: int = 0
    rejected: int = 0
    contact_candidates_stored: int = 0
    contact_candidates_updated: int = 0
    total_accounts: int = 0


@dataclass(slots=True)
class AgencySourceValidationWarning:
    message: str
    row: int = 0
    field: str = ""


@dataclass(slots=True)
class AgencySourceCapture:
    source: str
    source_type: str
    rows: list[dict[str, Any]]
    schema_version: int = AGENCY_SOURCE_SCHEMA_VERSION
    captured_at: str | None = None
    url: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AgencySourceCapture:
        rows = data.get("rows")
        return cls(
            schema_version=int(
                data.get("schema_version")
                or data.get("schemaVersion")
                or AGENCY_SOURCE_SCHEMA_VERSION
            ),
            source=str(data.get("source") or ""),
            source_type=str(data.get("source_type") or data.get("sourceType") or ""),
            captured_at=optional_clean(data.get("captured_at") or data.get("capturedAt")),
            url=optional_clean(data.get("url")),
            rows=[row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else [],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "source_type": self.source_type,
            "captured_at": self.captured_at,
            "url": self.url,
            "rows": self.rows,
        }


def load_json_object(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return raw


def import_salesnav_capture(
    state: OutreachState,
    capture: Mapping[str, Any],
    *,
    only_connectable: bool = False,
    agency_account: AgencyAccount | None = None,
) -> ImportSummary:
    source = clean_text(capture.get("source"))
    if not source:
        raise ValueError("capture did not include source")
    now = _now()
    rows = _rows(capture)
    summary = ImportSummary(source=source)
    state.capture_cursors[source] = _capture_cursor_from_capture(source, capture, now)
    for row in rows:
        menu_state = clean_text(row.get("menuState") or row.get("menu_state") or "unknown")
        if only_connectable and menu_state != "connectable":
            continue
        lead = lead_from_capture_row(
            source, row, optional_clean(capture.get("capturedAt")), now, agency_account
        )
        if lead is None:
            continue
        index = find_lead_index(state.leads, lead)
        if index >= 0:
            existing = state.leads[index]
            preserve_lead_runtime_fields(lead, existing)
            if agency_account is None and existing.agency_account_id:
                lead.agency_account_id = existing.agency_account_id
                lead.agency_account_name = existing.agency_account_name
                lead.agency_account_url = existing.agency_account_url
                lead.agency_account_reasons = existing.agency_account_reasons
                lead.agency_account_evidence = existing.agency_account_evidence
            state.leads[index] = lead
            summary.updated += 1
        else:
            state.leads.append(lead)
            summary.stored += 1
        if lead.status == LeadStatus.ELIGIBLE:
            summary.eligible += 1
        elif lead.status == LeadStatus.NEEDS_REVIEW:
            summary.reviewed += 1
        elif lead.status == LeadStatus.REJECTED:
            summary.rejected += 1
    sort_leads(state.leads)
    summary.total_leads = len(state.leads)
    return summary


def import_account_capture(
    state: OutreachState,
    capture: Mapping[str, Any],
) -> AccountImportSummary:
    source = clean_text(capture.get("source"))
    if not source:
        raise ValueError("account capture did not include source")
    now = _now()
    summary = AccountImportSummary(source=source)
    state.capture_cursors[source] = _capture_cursor_from_account_capture(source, capture, now)
    for row in _rows(capture):
        account = account_from_capture_row(
            source, row, optional_clean(capture.get("capturedAt")), now
        )
        if account is None:
            continue
        index = find_agency_account_index(state.agency_accounts, account)
        if index >= 0:
            existing = state.agency_accounts[index]
            preserve_account_runtime_fields(account, existing)
            account.id = existing.id
            account.imported_at = existing.imported_at
            state.agency_accounts[index] = account
            summary.updated += 1
        else:
            state.agency_accounts.append(account)
            summary.stored += 1
        if account.status == AgencyAccountStatus.QUALIFIED:
            summary.qualified += 1
        elif account.status == AgencyAccountStatus.NEEDS_REVIEW:
            summary.needs_review += 1
        elif account.status == AgencyAccountStatus.REJECTED:
            summary.rejected += 1
    sort_agency_accounts(state.agency_accounts)
    summary.total = len(state.agency_accounts)
    return summary


def load_agency_source_capture(path: str | Path) -> AgencySourceCapture:
    return AgencySourceCapture.from_mapping(load_json_object(path))


def write_agency_source_capture(path: str | Path, capture: AgencySourceCapture) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(capture.to_dict(), indent=2, sort_keys=True) + "\n")


def load_agency_source_csv(
    path: str | Path,
    *,
    source: str,
    source_type: str = "manual_directory",
    url: str = "",
    captured_at: str | None = None,
) -> AgencySourceCapture:
    source = clean_text(source)
    if not source:
        raise ValueError("--source is required")
    with Path(path).open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, Any]] = []
        for record in reader:
            normalized = {
                _normalize_source_header(key): clean_text(value) for key, value in record.items()
            }
            if not normalized.get("name"):
                continue
            row = _agency_source_row_from_csv(normalized)
            if row.get("source_url") is None and url:
                row["source_url"] = url
            contact = _agency_source_contact_from_csv(normalized)
            if contact is not None:
                row["contacts"] = [contact]
            rows.append(row)
    return AgencySourceCapture(
        source=source,
        source_type=clean_text(source_type),
        captured_at=captured_at,
        url=optional_clean(url),
        rows=rows,
    )


def validate_agency_source_capture(
    capture: AgencySourceCapture,
) -> list[AgencySourceValidationWarning]:
    warnings: list[AgencySourceValidationWarning] = []
    if capture.schema_version != AGENCY_SOURCE_SCHEMA_VERSION:
        warnings.append(
            AgencySourceValidationWarning(
                field="schema_version",
                message=f"expected schema_version {AGENCY_SOURCE_SCHEMA_VERSION}",
            )
        )
    if not clean_text(capture.source):
        warnings.append(AgencySourceValidationWarning(field="source", message="source is required"))
    seen: dict[str, int] = {}
    for index, row in enumerate(capture.rows, start=1):
        if not clean_text(row.get("name")):
            warnings.append(
                AgencySourceValidationWarning(
                    row=index,
                    field="name",
                    message="name is required",
                )
            )
            continue
        if not any(
            clean_text(row.get(key))
            for key in ("website", "account_url", "linkedin_url", "source_url")
        ):
            warnings.append(
                AgencySourceValidationWarning(
                    row=index,
                    field="identity",
                    message=(
                        "provide at least one of website, account_url, "
                        "linkedin_url, or source_url"
                    ),
                )
            )
        key = agency_source_row_identity_key(row)
        if key:
            prior = seen.get(key)
            if prior:
                warnings.append(
                    AgencySourceValidationWarning(
                        row=index,
                        field="identity",
                        message=f"duplicates row {prior} by {key}",
                    )
                )
            else:
                seen[key] = index
    return warnings


def import_agency_source_capture(
    state: OutreachState,
    capture: AgencySourceCapture,
) -> AgencySourceImportSummary:
    source = clean_text(capture.source)
    if not source:
        raise ValueError("agency source capture did not include source")
    now = _now()
    summary = AgencySourceImportSummary(source=source)
    for row in capture.rows:
        account = agency_account_from_source_row(
            source, capture.source_type, capture.captured_at, row, now
        )
        if account is None:
            continue
        index = find_agency_account_index(state.agency_accounts, account)
        if index >= 0:
            existing = state.agency_accounts[index]
            preserve_account_runtime_fields(account, existing)
            account.id = existing.id
            account.imported_at = existing.imported_at
            state.agency_accounts[index] = account
            summary.updated += 1
        else:
            state.agency_accounts.append(account)
            summary.stored += 1
        if account.status == AgencyAccountStatus.QUALIFIED:
            summary.qualified += 1
        elif account.status == AgencyAccountStatus.NEEDS_REVIEW:
            summary.needs_review += 1
        elif account.status == AgencyAccountStatus.REJECTED:
            summary.rejected += 1
        contacts = row.get("contacts")
        if isinstance(contacts, list):
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                candidate = agency_contact_candidate_from_source_row(
                    source,
                    account,
                    optional_clean(row.get("source_url")),
                    contact,
                    now,
                )
                if candidate is None:
                    continue
                if upsert_agency_contact_candidate(state, candidate):
                    summary.contact_candidates_updated += 1
                else:
                    summary.contact_candidates_stored += 1
    sort_agency_accounts(state.agency_accounts)
    sort_agency_contact_candidates(state.agency_contact_candidates)
    summary.total_accounts = len(state.agency_accounts)
    return summary


def agency_account_from_source_row(
    source: str,
    source_type: str,
    captured_at: str | None,
    row: Mapping[str, Any],
    imported_at: str,
) -> AgencyAccount | None:
    name = clean_text(row.get("name"))
    if not name:
        return None
    account_url = normalized_linkedin_account_url(row.get("account_url") or row.get("linkedin_url"))
    website = optional_clean(row.get("website"))
    domain = domain_from_website(website)
    status, fit_score, fit_reasons, reject_reasons = source_row_disposition(source_type, row)
    evidence = truncate_evidence(source_row_evidence(row))
    return AgencyAccount(
        id=stable_agency_account_id(source, name, account_url, domain),
        source=source,
        name=name,
        account_url=account_url,
        website=website,
        domain=domain,
        industry=optional_clean(row.get("industry")),
        headcount=optional_clean(row.get("headcount")),
        location=optional_clean(row.get("location")),
        status=status,
        fit_score=fit_score,
        fit_reasons=fit_reasons,
        reject_reasons=reject_reasons,
        evidence_text=evidence,
        captured_at=captured_at,
        imported_at=imported_at,
        updated_at=imported_at,
    )


def source_row_disposition(
    source_type: str,
    row: Mapping[str, Any],
) -> tuple[AgencyAccountStatus, int, list[str], list[str]]:
    status = AgencyAccountStatus.NEEDS_REVIEW
    raw_status = clean_text(row.get("status"))
    if raw_status:
        status = AgencyAccountStatus(raw_status)
    fit_score = _optional_int(row.get("fit_score"), 50)
    has_fit_score = clean_text(row.get("fit_score")) != ""
    fit_reasons = _list(row.get("fit_reasons")) or ["imported from structured agency source"]
    reject_reasons = _list(row.get("reject_reasons"))
    if _is_partner_source(source_type) and not has_fit_score:
        fit_score += 15
        fit_reasons.append("partner directory source")
    for label in _list(row.get("services")) + _list(row.get("specialties")):
        if _target_agency_service_tag(label) and not has_fit_score:
            fit_score += 15
            fit_reasons.append(f"structured service tag: {clean_text(label)}")
        if _marketing_service_tag(label):
            fit_reasons.append("marketing service profile; review for dev/product support gap")
    fit_score = max(0, min(100, fit_score))
    if not raw_status:
        status = (
            AgencyAccountStatus.QUALIFIED if fit_score >= 65 else AgencyAccountStatus.NEEDS_REVIEW
        )
    return status, fit_score, dedupe_strings(fit_reasons), dedupe_strings(reject_reasons)


def agency_contact_candidate_from_source_row(
    source: str,
    account: AgencyAccount,
    source_url: str | None,
    row: Mapping[str, Any],
    imported_at: str,
) -> AgencyContactCandidate | None:
    email = normalize_email(row.get("email"))
    profile_url = normalized_public_profile_url(row.get("profile_url"))
    sales_profile_urn = optional_clean(
        row.get("sales_profile_urn")
        or row.get("contact_sales_profile_urn")
        or row.get("profile_urn")
    )
    contact_url = optional_clean(row.get("contact_url"))
    form_action = optional_clean(row.get("form_action"))
    if (
        email is None
        and profile_url is None
        and sales_profile_urn is None
        and contact_url is None
        and form_action is None
    ):
        return None
    raw_status = clean_text(row.get("status"))
    if raw_status:
        status = AgencyContactCandidateStatus(raw_status)
    elif email and is_generic_inbox(email):
        status = AgencyContactCandidateStatus.GENERIC_INBOX
    elif form_action or contact_url:
        status = AgencyContactCandidateStatus.CONTACT_FORM
    else:
        status = AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE
    candidate = AgencyContactCandidate(
        id="",
        agency_account_id=account.id,
        agency_account_name=account.name,
        source=source,
        source_url=source_url,
        status=status,
        review_status=AgencyContactReviewStatus.NEEDS_REVIEW,
        name=optional_clean(row.get("name")),
        title=optional_clean(row.get("title")),
        email=email,
        profile_url=profile_url,
        sales_profile_urn=sales_profile_urn,
        contact_url=contact_url,
        form_action=form_action,
        evidence=_list(row.get("evidence")),
        imported_at=imported_at,
        updated_at=imported_at,
    )
    candidate.id = stable_agency_contact_candidate_id(agency_contact_candidate_key(candidate))
    return candidate


def review_agency_contact_candidate(
    state: OutreachState,
    *,
    candidate_id: str,
    review_status: AgencyContactReviewStatus,
    name: str = "",
    title: str = "",
    note: str = "",
) -> AgencyContactCandidate:
    index = find_agency_contact_candidate_by_id(state.agency_contact_candidates, candidate_id)
    if index < 0:
        raise ValueError(f"agency contact candidate {candidate_id} not found")
    candidate = state.agency_contact_candidates[index]
    candidate.review_status = review_status
    if review_status == AgencyContactReviewStatus.REJECTED:
        candidate.status = AgencyContactCandidateStatus.REJECTED
    elif review_status == AgencyContactReviewStatus.CONVERTED:
        candidate.status = AgencyContactCandidateStatus.CONVERTED
    if clean_text(name):
        candidate.name = clean_text(name)
    if clean_text(title):
        candidate.title = clean_text(title)
    if clean_text(note):
        candidate.notes.append(clean_text(note))
    candidate.updated_at = _now()
    state.agency_contact_candidates[index] = candidate
    sort_agency_contact_candidates(state.agency_contact_candidates)
    return candidate


@dataclass(slots=True)
class AgencyContactPromotionSkip:
    candidate_id: str
    reason: str


@dataclass(slots=True)
class AgencyContactPromotionSummary:
    stored: int = 0
    updated: int = 0
    drafted: int = 0
    skipped: list[AgencyContactPromotionSkip] = field(default_factory=list)
    leads: list[Lead] = field(default_factory=list)


def promote_agency_contact_candidates(
    state: OutreachState,
    *,
    candidate_ids: list[str] | None = None,
    limit: int = 0,
    draft: bool = False,
    max_per_agency: int = 1,
    allow_multiple_per_agency: bool = False,
) -> AgencyContactPromotionSummary:
    from .drafts import build_message_draft_record, is_terminal_message_status

    selected = selected_agency_contact_candidates(state, candidate_ids or [], limit)
    summary = AgencyContactPromotionSummary()
    active_by_agency = active_agency_leads_by_account(state)
    if allow_multiple_per_agency:
        max_per_agency = 0
    now = _now()
    for candidate in selected:
        lead, reason = lead_from_agency_contact_candidate(state, candidate, now)
        if lead is None:
            summary.skipped.append(AgencyContactPromotionSkip(candidate.id, reason))
            continue
        account_id = lead.agency_account_id or ""
        existing_index = find_lead_index(state.leads, lead)
        existing_was_active = existing_index >= 0 and active_agency_promotion_lead(
            state.leads[existing_index]
        )
        active = active_by_agency.get(account_id, [])
        if (
            max_per_agency > 0
            and account_id
            and len(active) >= max_per_agency
            and not existing_was_active
        ):
            refs = "; ".join(
                f"{item.name} ({item.id}, {item.message_status.value})" for item in active
            )
            summary.skipped.append(
                AgencyContactPromotionSkip(
                    candidate.id,
                    (
                        f"agency already has {len(active)} active outreach lead(s); "
                        f"max per agency is {max_per_agency}; active lead(s): {refs or 'none'}"
                    ),
                )
            )
            continue
        if existing_index >= 0:
            preserve_lead_runtime_fields(lead, state.leads[existing_index])
            state.leads[existing_index] = lead
            lead_index = existing_index
            summary.updated += 1
        else:
            state.leads.append(lead)
            lead_index = len(state.leads) - 1
            summary.stored += 1
        if draft and not is_terminal_message_status(state.leads[lead_index].message_status):
            state.leads[lead_index].draft = build_message_draft_record(state.leads[lead_index])
            state.leads[lead_index].message_status = MessageStatus.DRAFTED
            state.leads[lead_index].updated_at = now
            summary.drafted += 1
        candidate.promoted_lead_id = state.leads[lead_index].id
        candidate.review_status = AgencyContactReviewStatus.CONVERTED
        candidate.status = AgencyContactCandidateStatus.CONVERTED
        candidate.updated_at = now
        summary.leads.append(state.leads[lead_index])
        if (
            not existing_was_active
            and active_agency_promotion_lead(state.leads[lead_index])
            and account_id
        ):
            active_by_agency.setdefault(account_id, []).append(state.leads[lead_index])
    sort_leads(state.leads)
    sort_agency_contact_candidates(state.agency_contact_candidates)
    return summary


def lead_from_agency_contact_candidate(
    state: OutreachState,
    candidate: AgencyContactCandidate,
    imported_at: str,
) -> tuple[Lead | None, str]:
    from .utils import first_name, stable_lead_id

    if candidate.review_status != AgencyContactReviewStatus.APPROVED:
        return None, "candidate is not approved"
    if candidate.status != AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE:
        return None, "only personal LinkedIn profile candidates can be promoted"
    if not candidate.profile_url:
        return None, "candidate has no LinkedIn profile URL"
    if not candidate.sales_profile_urn:
        return None, "resolve_agency_contact_salesnav_identity"
    sales_nav_url = sales_profile_urn_to_lead_url(candidate.sales_profile_urn)
    if not sales_nav_url:
        return None, "resolve_agency_contact_salesnav_identity"
    name = clean_text(candidate.name)
    if not usable_reviewed_contact_name(name):
        return None, "candidate needs a reviewed person name"
    title = clean_text(candidate.title)
    if not title:
        return None, "candidate needs a reviewed title"
    account = find_agency_account_by_id(state.agency_accounts, candidate.agency_account_id)
    if account is None:
        return None, "agency account not found"
    if account.status == AgencyAccountStatus.REJECTED:
        return None, "agency account is rejected"
    company = candidate.agency_account_name or account.name
    lead_type, fit_score, fit_reasons = promoted_agency_lead_disposition(title)
    evidence = promoted_agency_lead_evidence(candidate)
    lead = Lead(
        id=stable_lead_id("agency_contact_candidate", name, None, candidate.sales_profile_urn),
        source=f"Agency website contact - {account.name}",
        name=name,
        first_name=first_name(name),
        profile_url=sales_nav_url,
        sales_profile_urn=candidate.sales_profile_urn,
        title=title,
        company=company,
        lead_type=lead_type,
        status=LeadStatus.ELIGIBLE,
        fit_score=fit_score,
        fit_reasons=fit_reasons,
        reject_reasons=[],
        evidence_text=evidence,
        imported_at=imported_at,
        updated_at=imported_at,
        notes=[f"promoted from agency contact candidate {candidate.id}"],
    )
    link_lead_to_agency_account(lead, account)
    return lead, ""


def promoted_agency_lead_disposition(title: str) -> tuple[LeadType, int, list[str]]:
    lower = clean_text(title).lower()
    reasons = [
        "reviewed website contact candidate",
        "official agency website linked this LinkedIn profile",
        "agency account qualified or previously sourced",
        f"title supplied during review: {clean_text(title)}",
        "Sales Navigator identity resolved before promotion",
    ]
    if any(token in lower for token in ("founder", "co-founder", "owner", "partner", "principal")):
        return LeadType.AGENCY_FOUNDER, 95, reasons + ["founder/partner executive title"]
    if any(token in lower for token in ("ceo", "president", "chairman")):
        return LeadType.AGENCY_FOUNDER, 95, reasons + ["founder/partner executive title"]
    if any(token in lower for token in ("recruit", "talent", "people", "resourcing")):
        return LeadType.AGENCY_RESOURCE, 88, reasons + ["agency talent/resource title"]
    return LeadType.AGENCY_DELIVERY, 88, reasons + ["agency delivery/sales/client leadership title"]


def promoted_agency_lead_evidence(candidate: AgencyContactCandidate) -> str:
    parts = [f"Agency contact candidate: {candidate.id}"]
    if candidate.source_url:
        parts.append(f"Source URL: {candidate.source_url}")
    if candidate.profile_url:
        parts.append(f"Public LinkedIn profile: {candidate.profile_url}")
    if candidate.sales_profile_urn:
        parts.append(f"Sales Navigator profile URN: {candidate.sales_profile_urn}")
    parts.extend(candidate.evidence)
    if candidate.notes:
        parts.append("Review notes: " + "; ".join(candidate.notes))
    return truncate_evidence("\n".join(parts))


def usable_reviewed_contact_name(name: str) -> bool:
    return clean_text(name).lower() not in {
        "",
        "linkedin",
        "linked in",
        "linkedin profile",
        "profile",
        "social",
        "contact",
        "learn more",
    }


def selected_agency_contact_candidates(
    state: OutreachState,
    ids: list[str],
    limit: int,
) -> list[AgencyContactCandidate]:
    clean_ids = dedupe_strings(ids)
    if clean_ids:
        selected: list[AgencyContactCandidate] = []
        for candidate_id in clean_ids:
            index = find_agency_contact_candidate_by_id(
                state.agency_contact_candidates,
                candidate_id,
            )
            if index < 0:
                raise ValueError(f"agency contact candidate {candidate_id} not found")
            selected.append(state.agency_contact_candidates[index])
        return selected
    selected = [
        candidate
        for candidate in state.agency_contact_candidates
        if candidate.review_status == AgencyContactReviewStatus.APPROVED
    ]
    sort_agency_contact_candidates(selected)
    return selected[:limit] if limit > 0 else selected


def active_agency_leads_by_account(state: OutreachState) -> dict[str, list[Lead]]:
    result: dict[str, list[Lead]] = {}
    for lead in state.leads:
        if not active_agency_promotion_lead(lead) or not lead.agency_account_id:
            continue
        result.setdefault(lead.agency_account_id, []).append(lead)
    for leads in result.values():
        sort_leads(leads)
    return result


def active_agency_promotion_lead(lead: Lead) -> bool:
    from .dashboard import bucket_for_lead
    from .drafts import is_terminal_message_status
    from .models import MessageStatus

    if lead.status != LeadStatus.ELIGIBLE or bucket_for_lead(lead) != "agency":
        return False
    if lead.message_status in {
        MessageStatus.NOT_MESSAGEABLE,
        MessageStatus.BLOCKED,
        MessageStatus.REPLIED_NOT_FIT,
    }:
        return False
    return not is_terminal_message_status(lead.message_status) or (
        lead.message_status == MessageStatus.DRY_RUN_READY
    )


def upsert_agency_contact_candidate(
    state: OutreachState,
    candidate: AgencyContactCandidate,
) -> bool:
    key = agency_contact_candidate_key(candidate)
    if not candidate.id:
        candidate.id = stable_agency_contact_candidate_id(key)
    for index, existing in enumerate(state.agency_contact_candidates):
        if agency_contact_candidate_key(existing) == key:
            candidate.id = existing.id
            candidate.imported_at = existing.imported_at
            candidate.review_status = existing.review_status
            candidate.promoted_lead_id = existing.promoted_lead_id
            if existing.notes:
                candidate.notes = existing.notes
            state.agency_contact_candidates[index] = candidate
            return True
    state.agency_contact_candidates.append(candidate)
    return False


def agency_contact_candidate_key(candidate: AgencyContactCandidate) -> str:
    parts = [candidate.agency_account_id, candidate.status.value]
    for value in (
        candidate.sales_profile_urn,
        candidate.email,
        candidate.profile_url,
        candidate.contact_url,
        candidate.form_action,
    ):
        cleaned = clean_text(value).lower()
        if cleaned:
            parts.append(cleaned)
    if len(parts) <= 2 and candidate.source_url:
        parts.append(candidate.source_url.lower())
    return "|".join(parts)


def agency_contact_candidate_rank(candidate: AgencyContactCandidate) -> int:
    rank = 0
    if candidate.status == AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE:
        rank = 50
    elif candidate.status == AgencyContactCandidateStatus.CONTACT_FORM:
        rank = 20
    elif candidate.status == AgencyContactCandidateStatus.GENERIC_INBOX:
        rank = 10
    role_text = clean_text(f"{candidate.title or ''} {candidate.name or ''}").lower()
    if any(token in role_text for token in ("founder", "co-founder", "owner", "partner")):
        rank += 50
    elif any(token in role_text for token in ("chief executive", "ceo", "president")):
        rank += 50
    elif any(token in role_text for token in ("vp", "vice president", "head of", "director")):
        rank += 35
    elif any(token in role_text for token in ("principal", "engineering", "delivery")):
        rank += 25
    elif any(token in role_text for token in ("technology", "partnership", "client services")):
        rank += 25
    elif any(token in role_text for token in ("operations", "resource")):
        rank += 25
    if candidate.sales_profile_urn:
        rank += 20
    if candidate.profile_url:
        rank += 10
    if candidate.review_status == AgencyContactReviewStatus.APPROVED:
        rank += 5
    return rank


def find_lead_by_id(leads: list[Lead], lead_id: str) -> Lead | None:
    for lead in leads:
        if lead.id == lead_id:
            return lead
    return None


def find_lead_index(leads: list[Lead], candidate: Lead) -> int:
    for index, lead in enumerate(leads):
        if lead.id == candidate.id:
            return index
    candidate_key = lead_key(candidate)
    for index, lead in enumerate(leads):
        if lead_key(lead) == candidate_key:
            return index
    return -1


def lead_key(lead: Lead) -> str:
    if lead.sales_profile_urn:
        return "urn:" + clean_text(lead.sales_profile_urn)
    if lead.profile_url:
        return "url:" + lead.profile_url.lower()
    return "name:" + f"{lead.source}|{lead.name}".lower()


def find_agency_account_by_id(
    accounts: list[AgencyAccount], account_id: str
) -> AgencyAccount | None:
    for account in accounts:
        if account.id == account_id:
            return account
    return None


def find_agency_account_index(accounts: list[AgencyAccount], candidate: AgencyAccount) -> int:
    candidate_key = agency_account_key(candidate)
    for index, account in enumerate(accounts):
        if agency_account_key(account) == candidate_key:
            return index
    return -1


def agency_account_key(account: AgencyAccount) -> str:
    if account.account_url:
        return "url:" + account.account_url.lower()
    if account.domain:
        return "domain:" + account.domain.lower()
    return "name:" + account.name.lower()


def find_agency_contact_candidate_by_id(
    candidates: list[AgencyContactCandidate],
    candidate_id: str,
) -> int:
    for index, candidate in enumerate(candidates):
        if candidate.id == candidate_id:
            return index
    return -1


def sort_leads(leads: list[Lead]) -> None:
    leads.sort(key=lambda lead: (-lead.fit_score, lead.name))


def sort_agency_accounts(accounts: list[AgencyAccount]) -> None:
    accounts.sort(key=lambda account: (account.status.value, -account.fit_score, account.name))


def sort_agency_contact_candidates(candidates: list[AgencyContactCandidate]) -> None:
    candidates.sort(
        key=lambda candidate: (
            candidate.review_status.value,
            -agency_contact_candidate_rank(candidate),
            candidate.status.value,
            candidate.agency_account_name,
            candidate.id,
        )
    )


def preserve_lead_runtime_fields(lead: Lead, existing: Lead) -> None:
    lead.id = existing.id
    lead.imported_at = existing.imported_at
    lead.draft = existing.draft
    lead.message_status = existing.message_status
    lead.message_status_at = existing.message_status_at
    lead.send_attempts = existing.send_attempts
    if existing.notes:
        lead.notes = existing.notes


def preserve_account_runtime_fields(account: AgencyAccount, existing: AgencyAccount) -> None:
    account.last_contact_capture_at = existing.last_contact_capture_at
    account.contact_capture_count = existing.contact_capture_count
    account.last_contact_strategy = existing.last_contact_strategy
    account.last_contact_error = existing.last_contact_error
    account.last_contact_error_at = existing.last_contact_error_at
    account.contact_error_count = existing.contact_error_count
    account.last_website_enriched_at = existing.last_website_enriched_at
    account.website_enrichment_count = existing.website_enrichment_count
    account.last_website_enrichment_error = existing.last_website_enrichment_error
    account.last_website_enrichment_error_at = existing.last_website_enrichment_error_at
    if existing.notes:
        account.notes = existing.notes


def agency_source_row_identity_key(row: Mapping[str, Any]) -> str:
    account_url = normalized_linkedin_account_url(row.get("account_url"))
    if account_url:
        return "account_url:" + account_url.lower()
    linkedin_url = normalized_linkedin_account_url(row.get("linkedin_url"))
    if linkedin_url:
        return "linkedin_url:" + linkedin_url.lower()
    domain = domain_from_website(row.get("website"))
    if domain:
        return "domain:" + domain
    source_url = clean_text(row.get("source_url"))
    if source_url:
        return "source_url:" + source_url
    return "name:" + clean_text(row.get("name")).lower()


def source_row_evidence(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if clean_text(row.get("source_url")):
        parts.append(f"source_url: {clean_text(row.get('source_url'))}")
    for key in ("services", "specialties"):
        values = _list(row.get(key))
        if values:
            parts.append(f"{key}: {'; '.join(values)}")
    for key in ("description", "industry", "headcount", "location", "website"):
        value = clean_text(row.get(key))
        if value:
            parts.append(value)
    parts.extend(_list(row.get("evidence")))
    return "\n".join(parts)


def _agency_source_row_from_csv(record: Mapping[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": record.get("name", ""),
        "website": optional_clean(record.get("website")),
        "account_url": optional_clean(record.get("account_url")),
        "linkedin_url": optional_clean(record.get("linkedin_url")),
        "source_url": optional_clean(record.get("source_url")),
        "services": _split_source_list(record.get("services", "")),
        "specialties": _split_source_list(record.get("specialties", "")),
        "industry": optional_clean(record.get("industry")),
        "headcount": optional_clean(record.get("headcount")),
        "location": optional_clean(record.get("location")),
        "description": optional_clean(record.get("description")),
        "fit_reasons": _split_source_list(record.get("fit_reasons", "")),
        "reject_reasons": _split_source_list(record.get("reject_reasons", "")),
        "evidence": _split_source_list(record.get("evidence", "")),
    }
    for key in ("status", "fit_score"):
        if record.get(key):
            row[key] = record[key]
    return row


def _agency_source_contact_from_csv(record: Mapping[str, str]) -> dict[str, Any] | None:
    contact: dict[str, Any] = {
        "name": optional_clean(record.get("contact_name")),
        "title": optional_clean(record.get("contact_title")),
        "email": optional_clean(record.get("contact_email")),
        "profile_url": optional_clean(record.get("contact_profile_url")),
        "sales_profile_urn": optional_clean(record.get("contact_sales_profile_urn")),
        "contact_url": optional_clean(record.get("contact_url")),
        "form_action": optional_clean(record.get("contact_form_action")),
        "evidence": _split_source_list(record.get("contact_evidence", "")),
    }
    if record.get("contact_status"):
        contact["status"] = record["contact_status"]
    if not any(
        contact.get(key)
        for key in (
            "name",
            "title",
            "email",
            "profile_url",
            "sales_profile_urn",
            "contact_url",
            "form_action",
        )
    ):
        return None
    return contact


def _normalize_source_header(value: str | None) -> str:
    cleaned = clean_text(value).lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "linkedin": "linkedin_url",
        "linkedin_company_url": "linkedin_url",
        "url": "source_url",
        "profile_url": "source_url",
        "contact_linkedin": "contact_profile_url",
        "contact_linkedin_url": "contact_profile_url",
        "form_action": "contact_form_action",
        "sales_profile_urn": "contact_sales_profile_urn",
        "profile_urn": "contact_sales_profile_urn",
    }
    return aliases.get(cleaned, cleaned)


def _split_source_list(value: str) -> list[str]:
    cleaned = clean_text(value)
    if not cleaned:
        return []
    parts = cleaned.replace("|", ";").split(";")
    return dedupe_strings(parts)


def _capture_cursor_from_capture(
    source: str,
    capture: Mapping[str, Any],
    updated_at: str,
) -> CaptureCursor:
    rows = _rows(capture)
    state_counts = capture.get("stateCounts") or capture.get("state_counts") or {}
    parsed_counts = (
        {str(key): _optional_int(value) for key, value in state_counts.items()}
        if isinstance(state_counts, Mapping)
        else {}
    )
    return CaptureCursor(
        source=source,
        updated_at=updated_at,
        captured_at=optional_clean(capture.get("capturedAt")),
        resume_url=optional_clean(capture.get("resumeUrl") or capture.get("url")),
        captured_pages=len(capture.get("pages", []))
        if isinstance(capture.get("pages"), list)
        else 0,
        raw_row_count=_optional_int(capture.get("rawRowCount"), len(rows)),
        output_row_count=_optional_int(capture.get("outputRowCount"), len(rows)),
        connectable_count=parsed_counts.get("connectable", 0),
        already_pending_count=parsed_counts.get("already-pending", 0),
        state_counts=parsed_counts,
    )


def _capture_cursor_from_account_capture(
    source: str,
    capture: Mapping[str, Any],
    updated_at: str,
) -> CaptureCursor:
    rows = _rows(capture)
    return CaptureCursor(
        source=source,
        updated_at=updated_at,
        captured_at=optional_clean(capture.get("capturedAt")),
        resume_url=optional_clean(capture.get("resumeUrl") or capture.get("url")),
        captured_pages=len(capture.get("pages", []))
        if isinstance(capture.get("pages"), list)
        else 0,
        raw_row_count=_optional_int(capture.get("rawRowCount"), len(rows)),
        output_row_count=_optional_int(capture.get("outputRowCount"), len(rows)),
        state_counts={"accounts": len(rows)},
    )


def _rows(capture: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = capture.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _list(value: object) -> list[str]:
    if isinstance(value, list):
        return dedupe_strings([str(item) for item in value])
    if isinstance(value, str):
        return _split_source_list(value)
    return []


def _optional_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if value is None or clean_text(value) == "":
        return default
    try:
        return int(str(value))
    except ValueError:
        return default


def _is_partner_source(value: str) -> bool:
    return clean_text(value).lower() in {
        "shopify_partner",
        "webflow_partner",
        "hubspot_partner",
        "google_partner",
    }


def _target_agency_service_tag(value: str) -> bool:
    tag = clean_text(value.lower().replace("&", " and ").replace("/", " ").replace("-", " "))
    return tag in {
        "application development",
        "custom api integrations",
        "crm implementation",
        "data migration",
        "ecommerce development",
        "mobile app development",
        "product design",
        "shopify development",
        "software development",
        "solutions architecture design",
        "web development",
        "web design",
        "website design",
        "website development",
        "webflow development",
        "wordpress development",
    }


def _marketing_service_tag(value: str) -> bool:
    tag = clean_text(value.lower().replace("&", " and ").replace("/", " ").replace("-", " "))
    return tag in {
        "account based marketing",
        "content creation",
        "digital marketing",
        "email marketing",
        "full inbound marketing services",
        "paid advertising",
        "seo",
        "social media marketing",
    }


def _now() -> str:
    from .utils import now_iso

    return now_iso()
