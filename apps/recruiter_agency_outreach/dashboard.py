"""Dashboard and agency-pool reporting for recruiter/agency outreach."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .drafts import clean_inline, draft_subject
from .models import (
    AgencyAccount,
    AgencyAccountStatus,
    AgencyContactCandidate,
    AgencyContactCandidateStatus,
    AgencyContactReviewStatus,
    Lead,
    LeadStatus,
    LeadType,
    MessageStatus,
    OutreachState,
)
from .utils import clean_text, now_iso


@dataclass(slots=True)
class BucketCounts:
    agencies: int = 0
    recruiters: int = 0


@dataclass(slots=True)
class RunCounts:
    sent: BucketCounts = field(default_factory=BucketCounts)
    dry_run_ready: BucketCounts = field(default_factory=BucketCounts)
    conversation_exists: BucketCounts = field(default_factory=BucketCounts)
    not_messageable: BucketCounts = field(default_factory=BucketCounts)
    blocked: BucketCounts = field(default_factory=BucketCounts)
    send_failed: BucketCounts = field(default_factory=BucketCounts)


@dataclass(slots=True)
class StatusCounts:
    by_status: dict[LeadStatus, int] = field(default_factory=dict)
    by_lead_type: dict[LeadType, int] = field(default_factory=dict)
    by_message_status: dict[MessageStatus, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    by_agency_account_status: dict[AgencyAccountStatus, int] = field(default_factory=dict)
    by_agency_contact_candidate_status: dict[AgencyContactCandidateStatus, int] = field(
        default_factory=dict
    )
    by_agency_contact_candidate_review_status: dict[AgencyContactReviewStatus, int] = field(
        default_factory=dict
    )
    by_agency_contact_candidate_source: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class DailyLeadAction:
    at: str
    bucket: str
    lead_id: str
    name: str
    lead_type: LeadType
    message_status: MessageStatus
    action: str
    result: str
    run_id: str = ""
    profile_url: str | None = None
    note: str | None = None


@dataclass(slots=True)
class AgencyAccountFunnel:
    qualified: int = 0
    with_contacts: int = 0
    with_messageable_or_sent_contacts: int = 0
    exhausted_without_contacts: int = 0
    exhausted_after_contact_attempts: int = 0


@dataclass(slots=True)
class AgencyPoolAccountDiagnosis:
    id: str
    name: str
    status: AgencyAccountStatus
    fit_score: int
    next_step: str
    website: str | None = None
    domain: str | None = None
    contact_capture_count: int = 0
    contacts: int = 0
    open_leads: int = 0
    messageable_or_sent: int = 0
    last_contact_strategy: str | None = None
    last_contact_error: str | None = None


@dataclass(slots=True)
class AgencyPoolDiagnosis:
    generated_at: str
    state_path: str
    counts: StatusCounts
    funnel: AgencyAccountFunnel
    website_candidates: int
    qualified_website_candidates: int
    exhausted_website_candidates: int
    missing_linkedin_company_url: int
    accounts: list[AgencyPoolAccountDiagnosis]


@dataclass(slots=True)
class AgencyPoolNextAction:
    generated_at: str
    state_path: str
    action: str
    reason: str
    command: str = ""
    lead: Lead | None = None
    candidate: AgencyContactCandidate | None = None
    account: AgencyAccount | None = None


@dataclass(slots=True)
class DashboardReport:
    generated_at: str
    mode: str
    state_path: str
    target_agencies: int
    target_recruiters: int
    allow_send: bool
    counts: StatusCounts
    run_counts: RunCounts
    backlog_counts: BucketCounts
    ready_counts: BucketCounts
    lifetime_counts: BucketCounts
    agency_funnel_counts: AgencyAccountFunnel
    ready_agencies: list[Lead]
    ready_recruiters: list[Lead]
    approved_agencies: list[Lead]
    approved_recruiters: list[Lead]
    sent_agencies: list[Lead]
    sent_recruiters: list[Lead]
    skipped_agencies: list[Lead]
    skipped_recruiters: list[Lead]
    actions: list[DailyLeadAction] = field(default_factory=list)
    dashboard_path: str = ""
    limiting_reason: str = ""


def counts(state: OutreachState) -> StatusCounts:
    result = StatusCounts()
    for lead in state.leads:
        result.by_status[lead.status] = result.by_status.get(lead.status, 0) + 1
        result.by_lead_type[lead.lead_type] = result.by_lead_type.get(lead.lead_type, 0) + 1
        result.by_message_status[lead.message_status] = (
            result.by_message_status.get(lead.message_status, 0) + 1
        )
        result.by_source[lead.source] = result.by_source.get(lead.source, 0) + 1
    for account in state.agency_accounts:
        result.by_agency_account_status[account.status] = (
            result.by_agency_account_status.get(account.status, 0) + 1
        )
    for candidate in state.agency_contact_candidates:
        result.by_agency_contact_candidate_status[candidate.status] = (
            result.by_agency_contact_candidate_status.get(candidate.status, 0) + 1
        )
        result.by_agency_contact_candidate_review_status[candidate.review_status] = (
            result.by_agency_contact_candidate_review_status.get(candidate.review_status, 0) + 1
        )
        result.by_agency_contact_candidate_source[candidate.source] = (
            result.by_agency_contact_candidate_source.get(candidate.source, 0) + 1
        )
    return result


def build_dashboard_report(
    state: OutreachState,
    state_path: str,
    *,
    target_agencies: int = 5,
    target_recruiters: int = 5,
    allow_send: bool = False,
    actions: list[DailyLeadAction] | None = None,
    mode: str = "render",
    dashboard_path: str = "",
) -> DashboardReport:
    action_list = actions or []
    return DashboardReport(
        generated_at=now_iso(),
        mode=mode,
        state_path=state_path,
        dashboard_path=dashboard_path,
        target_agencies=target_agencies,
        target_recruiters=target_recruiters,
        allow_send=allow_send,
        counts=counts(state),
        run_counts=dashboard_run_counts(action_list),
        backlog_counts=BucketCounts(
            agencies=dashboard_bucket_count(state, "agency", MessageStatus.DRAFTED),
            recruiters=dashboard_bucket_count(state, "recruiter", MessageStatus.DRAFTED),
        ),
        ready_counts=BucketCounts(
            agencies=dashboard_bucket_count(state, "agency", MessageStatus.DRY_RUN_READY),
            recruiters=dashboard_bucket_count(state, "recruiter", MessageStatus.DRY_RUN_READY),
        ),
        lifetime_counts=BucketCounts(
            agencies=dashboard_bucket_count(state, "agency", MessageStatus.SENT),
            recruiters=dashboard_bucket_count(state, "recruiter", MessageStatus.SENT),
        ),
        agency_funnel_counts=agency_account_funnel_counts(state),
        ready_agencies=dashboard_leads(state, "agency", MessageStatus.DRY_RUN_READY),
        ready_recruiters=dashboard_leads(state, "recruiter", MessageStatus.DRY_RUN_READY),
        approved_agencies=dashboard_leads(state, "agency", MessageStatus.APPROVED),
        approved_recruiters=dashboard_leads(state, "recruiter", MessageStatus.APPROVED),
        sent_agencies=dashboard_leads(state, "agency", MessageStatus.SENT),
        sent_recruiters=dashboard_leads(state, "recruiter", MessageStatus.SENT),
        skipped_agencies=dashboard_skipped_leads(state, "agency"),
        skipped_recruiters=dashboard_skipped_leads(state, "recruiter"),
        actions=action_list,
        limiting_reason=dashboard_limiting_reason(state, target_agencies, target_recruiters),
    )


def render_dashboard_markdown(report: DashboardReport) -> str:
    account_counts = report.counts.by_agency_account_status
    candidate_counts = report.counts.by_agency_contact_candidate_status
    review_counts = report.counts.by_agency_contact_candidate_review_status
    lines = [
        f"# Recruiter And Agency Outreach {report.generated_at[:10]}",
        "",
        f"- Generated: `{report.generated_at}`",
        f"- Mode: `{report.mode}`",
        f"- State: `{report.state_path}`",
        (
            f"- This-run target: `{report.target_agencies}` agencies, "
            f"`{report.target_recruiters}` recruiters"
        ),
        f"- Real sends enabled: `{str(report.allow_send).lower()}`",
        (
            f"- This-run sent: `{report.run_counts.sent.agencies}` agencies, "
            f"`{report.run_counts.sent.recruiters}` recruiters"
        ),
        (
            "- Ready now: "
            f"`{report.ready_counts.agencies}` agencies, "
            f"`{report.ready_counts.recruiters}` recruiters"
        ),
        (
            "- Backlog drafted/needs validation: "
            f"`{report.backlog_counts.agencies}` agencies, "
            f"`{report.backlog_counts.recruiters}` recruiters"
        ),
        (
            "- Lifetime sent: "
            f"`{report.lifetime_counts.agencies}` agencies, "
            f"`{report.lifetime_counts.recruiters}` recruiters"
        ),
        (
            "- Agency accounts: "
            f"`{account_counts.get(AgencyAccountStatus.QUALIFIED, 0)}` qualified, "
            f"`{account_counts.get(AgencyAccountStatus.NEEDS_REVIEW, 0)}` needs review, "
            f"`{account_counts.get(AgencyAccountStatus.REJECTED, 0)}` rejected, "
            f"`{account_counts.get(AgencyAccountStatus.EXHAUSTED, 0)}` exhausted"
        ),
        (
            "- Agency review-only contacts: "
            f"`{candidate_counts.get(AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE, 0)}` "
            "website_contact_candidate, "
            f"`{candidate_counts.get(AgencyContactCandidateStatus.GENERIC_INBOX, 0)}` "
            "generic_inbox, "
            f"`{candidate_counts.get(AgencyContactCandidateStatus.CONTACT_FORM, 0)}` contact_form"
        ),
        (
            "- Agency contact review: "
            f"`{review_counts.get(AgencyContactReviewStatus.NEEDS_REVIEW, 0)}` needs_review, "
            f"`{review_counts.get(AgencyContactReviewStatus.APPROVED, 0)}` approved, "
            f"`{review_counts.get(AgencyContactReviewStatus.REJECTED, 0)}` rejected, "
            f"`{review_counts.get(AgencyContactReviewStatus.CONVERTED, 0)}` converted"
        ),
        "",
        "## Sourcing Readiness",
        "",
        (
            f"- Ready to send: `{report.ready_counts.agencies}` agencies, "
            f"`{report.ready_counts.recruiters}` recruiters"
        ),
        (
            "- Drafted/needs validation: "
            f"`{report.backlog_counts.agencies}` agencies, "
            f"`{report.backlog_counts.recruiters}` recruiters"
        ),
        "",
        "## Send Results",
        "",
        (
            f"- This-run sent: `{report.run_counts.sent.agencies}` agencies, "
            f"`{report.run_counts.sent.recruiters}` recruiters"
        ),
        (
            f"- Lifetime sent: `{report.lifetime_counts.agencies}` agencies, "
            f"`{report.lifetime_counts.recruiters}` recruiters"
        ),
        "",
    ]
    if report.limiting_reason:
        lines.extend(["- Limiting reason: " + clean_inline(report.limiting_reason), ""])
    lines.extend(["## Agencies", ""])
    lines.extend(render_lead_cards("messageable/sendable", report.ready_agencies))
    lines.extend(render_lead_cards("manually approved", report.approved_agencies))
    lines.extend(render_lead_cards("sent", report.sent_agencies))
    lines.extend(render_lead_cards("checked/skipped", report.skipped_agencies))
    lines.extend(["## Recruiters", ""])
    lines.extend(render_lead_cards("messageable/sendable", report.ready_recruiters))
    lines.extend(render_lead_cards("manually approved", report.approved_recruiters))
    lines.extend(render_lead_cards("sent", report.sent_recruiters))
    lines.extend(render_lead_cards("checked/skipped", report.skipped_recruiters))
    return "\n".join(lines)


def write_dashboard_markdown(path: str | Path, report: DashboardReport) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard_markdown(report))


def render_lead_cards(label: str, leads: list[Lead]) -> list[str]:
    if not leads:
        return []
    lines = [f"### {label.title()}", ""]
    for lead in leads:
        lines.append(f"- `{lead.id}` {clean_inline(lead.name)} - `{lead.message_status.value}`")
        if lead.profile_url:
            lines.append(f"  Profile: {clean_inline(lead.profile_url)}")
        if lead.draft:
            lines.append(f"  Subject: {clean_inline(draft_subject(lead))}")
            lines.append(f"  Draft evidence: {clean_inline('; '.join(lead.draft.evidence))}")
    lines.append("")
    return lines


def bucket_for_lead(lead: Lead) -> str:
    if lead.lead_type == LeadType.CONTRACT_RECRUITER:
        return "recruiter"
    if lead.lead_type in {
        LeadType.AGENCY_RESOURCE,
        LeadType.AGENCY_DELIVERY,
        LeadType.AGENCY_FOUNDER,
    }:
        return "agency"
    return ""


def lead_matches_sendable_bucket(state: OutreachState, lead: Lead, bucket: str) -> bool:
    if lead.status != LeadStatus.ELIGIBLE or bucket_for_lead(lead) != bucket:
        return False
    if bucket != "agency":
        return True
    return lead_has_qualified_agency_account(state, lead)


def lead_has_qualified_agency_account(state: OutreachState, lead: Lead) -> bool:
    if not lead.agency_account_id:
        return False
    return any(
        account.id == lead.agency_account_id and account.status == AgencyAccountStatus.QUALIFIED
        for account in state.agency_accounts
    )


def ready_leads(state: OutreachState, bucket: str) -> list[Lead]:
    leads = [
        lead
        for lead in state.leads
        if lead_matches_sendable_bucket(state, lead, bucket)
        and lead.message_status == MessageStatus.DRY_RUN_READY
    ]
    leads.sort(key=lambda lead: (-lead.fit_score, lead.name))
    return leads


def leads_for_message_validation(state: OutreachState, bucket: str) -> list[Lead]:
    leads = [
        lead
        for lead in state.leads
        if lead_matches_sendable_bucket(state, lead, bucket)
        and lead.profile_url
        and lead.draft is not None
        and lead.message_status == MessageStatus.DRAFTED
    ]
    leads.sort(key=lambda lead: (-lead.fit_score, lead.name))
    return leads


def dashboard_bucket_count(state: OutreachState, bucket: str, message_status: MessageStatus) -> int:
    return sum(
        1
        for lead in state.leads
        if lead.message_status == message_status and bucket_for_lead(lead) == bucket
    )


def dashboard_leads(state: OutreachState, bucket: str, message_status: MessageStatus) -> list[Lead]:
    leads = [
        lead
        for lead in state.leads
        if lead.message_status == message_status
        and bucket_for_lead(lead) == bucket
        and lead.status == LeadStatus.ELIGIBLE
    ]
    leads.sort(key=lambda lead: (-lead.fit_score, lead.name))
    return leads


def dashboard_skipped_leads(state: OutreachState, bucket: str) -> list[Lead]:
    skipped = {
        MessageStatus.NOT_MESSAGEABLE,
        MessageStatus.CONVERSATION_EXISTS,
        MessageStatus.BLOCKED,
        MessageStatus.SEND_FAILED,
    }
    leads = [
        lead
        for lead in state.leads
        if lead.message_status in skipped
        and bucket_for_lead(lead) == bucket
        and lead.status == LeadStatus.ELIGIBLE
    ]
    leads.sort(key=lambda lead: (-lead.fit_score, lead.name))
    return leads


def dashboard_run_counts(actions: list[DailyLeadAction]) -> RunCounts:
    result = RunCounts()
    for action in actions:
        target = getattr(result, _run_count_field(action.result), None)
        if not isinstance(target, BucketCounts):
            continue
        if action.bucket == "agency":
            target.agencies += 1
        elif action.bucket == "recruiter":
            target.recruiters += 1
    return result


def _run_count_field(result: str) -> str:
    return {
        "sent-clicked": "sent",
        "dry-run-messageable": "dry_run_ready",
        "conversation-exists": "conversation_exists",
        "not-messageable": "not_messageable",
        "blocked": "blocked",
    }.get(result, "send_failed")


def agency_account_funnel_counts(state: OutreachState) -> AgencyAccountFunnel:
    lead_counts = agency_pool_lead_counts_by_account(state)
    funnel = AgencyAccountFunnel()
    for account in state.agency_accounts:
        account_counts = lead_counts.get(account.id, AgencyPoolLeadCounts())
        if account.status == AgencyAccountStatus.QUALIFIED:
            funnel.qualified += 1
        if account_counts.contacts:
            funnel.with_contacts += 1
        if account_counts.messageable_or_sent:
            funnel.with_messageable_or_sent_contacts += 1
        if account.status == AgencyAccountStatus.EXHAUSTED and account_counts.contacts == 0:
            funnel.exhausted_without_contacts += 1
        if account.status == AgencyAccountStatus.EXHAUSTED and account_counts.contacts > 0:
            funnel.exhausted_after_contact_attempts += 1
    return funnel


@dataclass(slots=True)
class AgencyPoolLeadCounts:
    contacts: int = 0
    open_leads: int = 0
    messageable_or_sent: int = 0


def agency_pool_lead_counts_by_account(state: OutreachState) -> dict[str, AgencyPoolLeadCounts]:
    by_account: dict[str, AgencyPoolLeadCounts] = {}
    for lead in state.leads:
        if not lead.agency_account_id or bucket_for_lead(lead) != "agency":
            continue
        if lead.status != LeadStatus.ELIGIBLE:
            continue
        account_counts = by_account.setdefault(lead.agency_account_id, AgencyPoolLeadCounts())
        account_counts.contacts += 1
        if lead.message_status in {
            MessageStatus.DRY_RUN_READY,
            MessageStatus.SENT,
            MessageStatus.MANUALLY_SENT,
        }:
            account_counts.messageable_or_sent += 1
        if lead.message_status in {
            MessageStatus.NONE,
            MessageStatus.DRAFTED,
            MessageStatus.DRY_RUN_READY,
        }:
            account_counts.open_leads += 1
    return by_account


def build_agency_pool_diagnosis(
    state: OutreachState,
    state_path: str,
    limit: int = 20,
) -> AgencyPoolDiagnosis:
    lead_counts = agency_pool_lead_counts_by_account(state)
    accounts: list[AgencyPoolAccountDiagnosis] = []
    website_candidates = 0
    qualified_website_candidates = 0
    exhausted_website_candidates = 0
    missing_company_url = 0
    for account in state.agency_accounts:
        item = build_agency_pool_account_diagnosis(
            account,
            lead_counts.get(account.id, AgencyPoolLeadCounts()),
        )
        if item.next_step == "website_enrichment":
            website_candidates += 1
            if account.status == AgencyAccountStatus.QUALIFIED:
                qualified_website_candidates += 1
            elif account.status == AgencyAccountStatus.EXHAUSTED:
                exhausted_website_candidates += 1
        if item.next_step == "missing_linkedin_company_url":
            missing_company_url += 1
        if item.next_step != "no_action":
            accounts.append(item)
    accounts.sort(
        key=lambda item: (agency_pool_next_step_rank(item.next_step), -item.fit_score, item.name)
    )
    if limit > 0:
        accounts = accounts[:limit]
    return AgencyPoolDiagnosis(
        generated_at=now_iso(),
        state_path=state_path,
        counts=counts(state),
        funnel=agency_account_funnel_counts(state),
        website_candidates=website_candidates,
        qualified_website_candidates=qualified_website_candidates,
        exhausted_website_candidates=exhausted_website_candidates,
        missing_linkedin_company_url=missing_company_url,
        accounts=accounts,
    )


def build_agency_pool_account_diagnosis(
    account: AgencyAccount,
    lead_counts: AgencyPoolLeadCounts,
) -> AgencyPoolAccountDiagnosis:
    next_step = "no_action"
    if account.status == AgencyAccountStatus.QUALIFIED and lead_counts.open_leads > 0:
        next_step = "validate_or_send_open_lead"
    elif account.status == AgencyAccountStatus.QUALIFIED and account.last_contact_error:
        next_step = "retry_linkedin_contact_search"
    elif account.status == AgencyAccountStatus.QUALIFIED and not account.account_url:
        next_step = "missing_linkedin_company_url"
    elif account.status == AgencyAccountStatus.QUALIFIED and account.contact_capture_count < 3:
        next_step = "continue_linkedin_contact_search"
    elif (
        account.website
        and lead_counts.contacts == 0
        and agency_account_website_enrichment_eligible(account)
    ):
        next_step = "website_enrichment"
    elif account.status == AgencyAccountStatus.NEEDS_REVIEW:
        next_step = "review_account_fit"
    return AgencyPoolAccountDiagnosis(
        id=account.id,
        name=account.name,
        status=account.status,
        fit_score=account.fit_score,
        website=account.website,
        domain=account.domain,
        contact_capture_count=account.contact_capture_count,
        last_contact_strategy=account.last_contact_strategy,
        last_contact_error=account.last_contact_error,
        contacts=lead_counts.contacts,
        open_leads=lead_counts.open_leads,
        messageable_or_sent=lead_counts.messageable_or_sent,
        next_step=next_step,
    )


def build_agency_pool_next_action(state: OutreachState, state_path: str) -> AgencyPoolNextAction:
    if leads := ready_leads(state, "agency"):
        lead = leads[0]
        return AgencyPoolNextAction(
            generated_at=now_iso(),
            state_path=state_path,
            action="send_ready_agency_lead",
            reason="Agency lead is already validated as messageable.",
            command=(
                "recruiter-agency-outreach send-message "
                f"--lead-id {lead.id} --session auto --allow-send"
            ),
            lead=lead,
        )
    if leads := leads_for_message_validation(state, "agency"):
        lead = leads[0]
        return AgencyPoolNextAction(
            generated_at=now_iso(),
            state_path=state_path,
            action="validate_drafted_agency_lead",
            reason=(
                "Agency lead has a draft and needs a dry-run messageability "
                "check before any real send."
            ),
            command=f"recruiter-agency-outreach send-message --lead-id {lead.id} --session auto",
            lead=lead,
        )
    if candidates := agency_contact_candidates_ready_for_promotion(state):
        candidate = candidates[0]
        return AgencyPoolNextAction(
            generated_at=now_iso(),
            state_path=state_path,
            action="promote_approved_agency_contact",
            reason=(
                "Reviewed agency website contact has Sales Navigator identity "
                "and can be promoted."
            ),
            command=(
                "recruiter-agency-outreach agency-pool promote-contact "
                f"--candidate-id {candidate.id} --draft"
            ),
            candidate=candidate,
        )
    if candidates := agency_contact_candidates_needing_salesnav_identity(state):
        candidate = candidates[0]
        return AgencyPoolNextAction(
            generated_at=now_iso(),
            state_path=state_path,
            action="resolve_agency_contact_salesnav_identity",
            reason=(
                "Approved agency website contact has public profile context "
                "but no Sales Navigator identity."
            ),
            candidate=candidate,
        )
    if candidates := agency_contact_candidates_needing_review(state):
        candidate = candidates[0]
        return AgencyPoolNextAction(
            generated_at=now_iso(),
            state_path=state_path,
            action="review_agency_website_contacts",
            reason="Agency website contacts need human review before promotion.",
            command=(
                "recruiter-agency-outreach agency-pool contacts "
                "--status website_contact_candidate --review-status needs_review --limit 20"
            ),
            candidate=candidate,
        )
    diagnosis = build_agency_pool_diagnosis(state, state_path, 20)
    missing = next(
        (item for item in diagnosis.accounts if item.next_step == "missing_linkedin_company_url"),
        None,
    )
    if missing:
        account = next((item for item in state.agency_accounts if item.id == missing.id), None)
        return AgencyPoolNextAction(
            generated_at=now_iso(),
            state_path=state_path,
            action="missing_linkedin_company_url",
            reason=(
                "Qualified agency account needs a LinkedIn company URL before "
                "account-scoped Sales Navigator contact search."
            ),
            account=account,
        )
    if diagnosis.website_candidates > 0:
        return AgencyPoolNextAction(
            generated_at=now_iso(),
            state_path=state_path,
            action="enrich_agency_websites",
            reason=(
                f"{diagnosis.website_candidates} agency account(s) have "
                "websites that can be checked for explicit contacts."
            ),
            command="recruiter-agency-outreach agency-pool enrich-websites --limit 25",
        )
    return AgencyPoolNextAction(
        generated_at=now_iso(),
        state_path=state_path,
        action="no_action",
        reason=(
            "No agency ready lead, drafted lead, reviewable contact, "
            "enrichable website, or retry recommendation is available."
        ),
    )


def render_agency_pool_next_action_text(next_action: AgencyPoolNextAction) -> str:
    lines = [
        f"action={next_action.action}",
        f"reason={clean_text(next_action.reason)}",
        f"state={clean_text(next_action.state_path)}",
    ]
    if next_action.command:
        lines.append(f"command={next_action.command}")
    if next_action.lead:
        lines.extend(
            [
                f"lead={next_action.lead.id}",
                f"lead_name={clean_text(next_action.lead.name)}",
                f"message_status={next_action.lead.message_status.value}",
            ]
        )
    if next_action.candidate:
        lines.extend(
            [
                f"candidate={next_action.candidate.id}",
                f"candidate_status={next_action.candidate.status.value}",
                f"candidate_review_status={next_action.candidate.review_status.value}",
                f"agency={clean_text(next_action.candidate.agency_account_name)}",
            ]
        )
        if next_action.candidate.profile_url:
            lines.append(f"profile_url={next_action.candidate.profile_url}")
    if next_action.account:
        lines.extend(
            [
                f"account={next_action.account.id}",
                f"account_name={clean_text(next_action.account.name)}",
                f"account_status={next_action.account.status.value}",
            ]
        )
    return "\n".join(lines)


def agency_contact_candidates_ready_for_promotion(
    state: OutreachState,
) -> list[AgencyContactCandidate]:
    candidates = [
        candidate
        for candidate in state.agency_contact_candidates
        if candidate.status == AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE
        and candidate.review_status == AgencyContactReviewStatus.APPROVED
        and bool(candidate.sales_profile_urn)
    ]
    candidates.sort(key=lambda item: (item.agency_account_name, item.id))
    return candidates


def agency_contact_candidates_needing_salesnav_identity(
    state: OutreachState,
) -> list[AgencyContactCandidate]:
    candidates = [
        candidate
        for candidate in state.agency_contact_candidates
        if candidate.status == AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE
        and candidate.review_status == AgencyContactReviewStatus.APPROVED
        and not candidate.sales_profile_urn
    ]
    candidates.sort(key=lambda item: (item.agency_account_name, item.id))
    return candidates


def agency_contact_candidates_needing_review(
    state: OutreachState,
) -> list[AgencyContactCandidate]:
    candidates = [
        candidate
        for candidate in state.agency_contact_candidates
        if candidate.status == AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE
        and candidate.review_status == AgencyContactReviewStatus.NEEDS_REVIEW
    ]
    candidates.sort(key=lambda item: (item.agency_account_name, item.id))
    return candidates


def agency_account_website_enrichment_eligible(account: AgencyAccount) -> bool:
    return account.status in {AgencyAccountStatus.QUALIFIED, AgencyAccountStatus.EXHAUSTED}


def agency_pool_next_step_rank(step: str) -> int:
    if step == "validate_or_send_open_lead":
        return 0
    if step == "retry_linkedin_contact_search":
        return 1
    if step == "missing_linkedin_company_url":
        return 2
    if step.startswith("continue_linkedin_contact_search"):
        return 3
    if step == "website_enrichment":
        return 4
    if step == "review_account_fit":
        return 5
    return 9


def dashboard_limiting_reason(
    state: OutreachState,
    target_agencies: int,
    target_recruiters: int,
) -> str:
    agency_gap = max(0, target_agencies - len(ready_leads(state, "agency")))
    recruiter_gap = max(0, target_recruiters - len(ready_leads(state, "recruiter")))
    if agency_gap > 0:
        return f"Agency ready-to-send pool is short by {agency_gap} for this render target."
    if recruiter_gap > 0:
        return f"Recruiter ready-to-send pool is short by {recruiter_gap} for this render target."
    return ""
