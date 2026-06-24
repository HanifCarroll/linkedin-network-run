"""Inspection and reporting helpers for recruiter/agency CLI parity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .dashboard import bucket_for_lead, counts, lead_matches_sendable_bucket
from .drafts import QueueItem, queue_item_from_lead
from .models import (
    AgencyAccount,
    AgencyContactCandidate,
    Lead,
    LeadStatus,
    MessageStatus,
    OutreachState,
)
from .sourcing import find_agency_account_by_id, find_lead_by_id, sort_agency_accounts
from .utils import clean_text


@dataclass(slots=True)
class LeadDetail:
    state_path: str
    bucket: str
    sendable: bool
    lead: Lead
    queue_item: QueueItem
    agency_account: AgencyAccount | None = None
    agency_contact_candidate: AgencyContactCandidate | None = None


def queue_items(
    state: OutreachState,
    statuses: list[LeadStatus],
    limit: int,
    *,
    include_drafts: bool,
) -> list[QueueItem]:
    status_set = set(statuses)
    items = [
        queue_item_from_lead(lead, include_draft=include_drafts)
        for lead in state.leads
        if not status_set or lead.status in status_set
    ]
    items.sort(key=lambda item: (-item.fit_score, item.name))
    return items[:limit] if limit > 0 else items


def queue_item_by_lead_id(
    state: OutreachState,
    lead_id: str,
    *,
    include_drafts: bool,
) -> QueueItem | None:
    lead = find_lead_by_id(state.leads, clean_text(lead_id))
    if lead is None:
        return None
    return queue_item_from_lead(lead, include_draft=include_drafts)


def agency_account_queue(
    state: OutreachState,
    statuses: list[str],
    limit: int,
) -> list[AgencyAccount]:
    status_set = set(statuses)
    items = [
        account
        for account in state.agency_accounts
        if not status_set or account.status.value in status_set
    ]
    sort_agency_accounts(items)
    return items[:limit] if limit > 0 else items


def build_lead_detail(
    state: OutreachState,
    state_path: str,
    lead_id: str,
) -> LeadDetail | None:
    lead = find_lead_by_id(state.leads, clean_text(lead_id))
    if lead is None:
        return None
    bucket = bucket_for_lead(lead)
    agency_account = (
        find_agency_account_by_id(state.agency_accounts, lead.agency_account_id)
        if lead.agency_account_id
        else None
    )
    return LeadDetail(
        state_path=state_path,
        bucket=bucket,
        sendable=lead_matches_sendable_bucket(state, lead, bucket),
        lead=lead,
        queue_item=queue_item_from_lead(lead, include_draft=True),
        agency_account=agency_account,
        agency_contact_candidate=_candidate_by_promoted_lead_id(
            state.agency_contact_candidates,
            lead.id,
        ),
    )


def render_lead_detail_text(detail: LeadDetail) -> str:
    lead = detail.lead
    lines = [
        f"lead={lead.id}",
        f"state={_value_or_dash(detail.state_path)}",
        f"name={_value_or_dash(lead.name)}",
        f"bucket={_value_or_dash(detail.bucket)}",
        f"type={lead.lead_type.value}",
        f"status={lead.status.value}",
        f"message_status={lead.message_status.value}",
        f"sendable={str(detail.sendable).lower()}",
        f"fit_score={lead.fit_score}",
    ]
    if lead.profile_url:
        lines.append(f"profile_url={clean_text(lead.profile_url)}")
    if lead.title:
        lines.append(f"title={clean_text(lead.title)}")
    if lead.company:
        lines.append(f"company={clean_text(lead.company)}")
    if detail.agency_account is not None:
        account = detail.agency_account
        lines.extend(
            [
                "",
                f"agency_account={account.id}",
                f"agency_account_name={_value_or_dash(account.name)}",
                f"agency_account_status={account.status.value}",
                f"agency_account_score={account.fit_score}",
            ]
        )
        if account.account_url:
            lines.append(f"agency_account_url={clean_text(account.account_url)}")
        if account.website:
            lines.append(f"agency_account_website={clean_text(account.website)}")
        if account.fit_reasons:
            lines.append("agency_account_reasons=" + "; ".join(account.fit_reasons))
    if detail.agency_contact_candidate is not None:
        candidate = detail.agency_contact_candidate
        lines.extend(
            [
                "",
                f"agency_contact_candidate={candidate.id}",
                f"candidate_source={_value_or_dash(candidate.source)}",
                f"candidate_status={candidate.status.value}",
                f"candidate_review_status={candidate.review_status.value}",
            ]
        )
        if candidate.source_url:
            lines.append(f"candidate_source_url={clean_text(candidate.source_url)}")
        if candidate.profile_url:
            lines.append(f"candidate_profile_url={clean_text(candidate.profile_url)}")
        if candidate.evidence:
            lines.append("candidate_evidence=" + "; ".join(candidate.evidence))
    if lead.fit_reasons:
        lines.extend(["", "fit_reasons:"])
        lines.extend(f"- {clean_text(reason)}" for reason in lead.fit_reasons)
    if lead.evidence_text:
        lines.extend(["", "evidence:", lead.evidence_text])
    if lead.draft is not None:
        lines.extend(
            [
                "",
                "draft:",
                f"subject={_value_or_dash(lead.draft.subject)}",
                f"angle={_value_or_dash(lead.draft.angle)}",
                f"generated_at={_value_or_dash(lead.draft.generated_at)}",
            ]
        )
        if lead.draft.evidence:
            lines.append("draft_evidence:")
            lines.extend(f"- {clean_text(item)}" for item in lead.draft.evidence)
        lines.extend(["body:", lead.draft.body])
    if lead.send_attempts:
        lines.extend(["", "send_attempts:"])
        for attempt in lead.send_attempts:
            line = (
                f"- {_value_or_dash(attempt.at)} status={attempt.status} "
                f"dry_run={str(attempt.dry_run).lower()}"
            )
            if attempt.run_id:
                line += f" run_id={attempt.run_id}"
            if attempt.note:
                line += f" note={clean_text(attempt.note)}"
            if attempt.out_path:
                line += f" out={attempt.out_path}"
            lines.append(line)
    if lead.notes:
        lines.extend(["", "notes:"])
        lines.extend(f"- {clean_text(note)}" for note in lead.notes)
    return "\n".join(lines)


def render_queue_text(items: list[QueueItem]) -> str:
    return "\n".join(
        "\t".join(
            [
                item.id,
                str(item.fit_score),
                item.lead_type.value,
                item.name,
                item.title or "-",
                item.company or "-",
                item.agency_account_name or "-",
                item.profile_url or "-",
            ]
        )
        for item in items
    )


def render_agency_accounts_text(items: list[AgencyAccount]) -> str:
    return "\n".join(
        "\t".join(
            [
                account.id,
                str(account.fit_score),
                account.status.value,
                account.name,
                account.website or "-",
                account.account_url or "-",
            ]
        )
        for account in items
    )


def render_counts_text(state: OutreachState, state_path: str) -> str:
    current_counts = counts(state)
    lines = [f"state: {state_path}"]
    lines.extend(_render_enum_counts("by status", current_counts.by_status))
    lines.extend(_render_enum_counts("by lead type", current_counts.by_lead_type))
    lines.extend(_render_enum_counts("by message status", current_counts.by_message_status))
    lines.extend(
        _render_enum_counts(
            "by agency account status",
            current_counts.by_agency_account_status,
        )
    )
    lines.extend(_render_string_counts("by source", current_counts.by_source))
    return "\n".join(lines)


def parse_lead_statuses(values: list[str]) -> list[LeadStatus]:
    statuses: list[LeadStatus] = []
    for value in _split_values(values):
        statuses.append(LeadStatus(value))
    return statuses


def parse_message_status(value: str) -> MessageStatus:
    return MessageStatus(value)


def _candidate_by_promoted_lead_id(
    candidates: list[AgencyContactCandidate],
    lead_id: str,
) -> AgencyContactCandidate | None:
    for candidate in candidates:
        if candidate.promoted_lead_id == lead_id:
            return candidate
    return None


def _render_enum_counts(label: str, values: Mapping[Any, int]) -> list[str]:
    lines = [label + ":"]
    for key, count in sorted(
        values.items(), key=lambda item: str(getattr(item[0], "value", item[0]))
    ):
        name = str(getattr(key, "value", key))
        lines.append(f"- {name}: {count}")
    return lines


def _render_string_counts(label: str, values: dict[str, int]) -> list[str]:
    lines = [label + ":"]
    for key, count in sorted(values.items()):
        lines.append(f"- {key}: {count}")
    return lines


def _split_values(values: list[str]) -> list[str]:
    split: list[str] = []
    for value in values:
        split.extend(clean_text(item) for item in value.split(",") if clean_text(item))
    return split


def _value_or_dash(value: str | None) -> str:
    cleaned = clean_text(value)
    return cleaned or "-"
