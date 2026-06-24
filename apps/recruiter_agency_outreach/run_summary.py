"""Run summary and retry recommendation helpers for recruiter/agency outreach."""

from __future__ import annotations

from dataclasses import dataclass, field

from .dashboard import (
    BucketCounts,
    DailyLeadAction,
    RunCounts,
    bucket_for_lead,
    dashboard_run_counts,
)
from .models import Lead, LeadType, MessageStatus, OutreachState, RunEvent
from .sourcing import find_lead_by_id
from .utils import clean_text, now_iso


@dataclass(slots=True)
class RunRecommendation:
    should_retry: bool = False
    command: str = ""
    reason: str = ""
    blocker: str = ""


@dataclass(slots=True)
class RunSummary:
    run_id: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    status: str = "running"
    blocker: str = ""
    dashboard_path: str = ""
    state_path: str = ""
    target_agencies: int = 0
    target_recruiters: int = 0
    allow_send: bool = False
    counts: RunCounts = field(default_factory=RunCounts)
    actions: list[DailyLeadAction] = field(default_factory=list)
    recommendation: RunRecommendation = field(default_factory=RunRecommendation)


def new_run_id(prefix: str) -> str:
    cleaned_prefix = clean_text(prefix).lower().replace(" ", "-").replace("_", "-") or "run"
    stamp = now_iso().replace(":", "").replace("-", "").replace(".", "")
    return f"{cleaned_prefix}-{stamp}"


def latest_run_summary(state: OutreachState, state_path: str) -> RunSummary | None:
    summaries = _run_summaries_from_events(state, state_path)
    if not summaries:
        return None
    summaries.sort(key=_effective_run_time, reverse=True)
    return summaries[0]


def recommend_next_run(
    state: OutreachState,
    state_path: str,
    *,
    target_agencies: int,
    target_recruiters: int,
    allow_send: bool,
) -> RunRecommendation:
    summary = latest_run_summary(state, state_path)
    if summary is not None:
        if summary.target_agencies == 0:
            summary.target_agencies = target_agencies
        if summary.target_recruiters == 0:
            summary.target_recruiters = target_recruiters
        if allow_send:
            summary.allow_send = True
        return recommend_next_run_summary(summary)
    if target_agencies > 0:
        return RunRecommendation(
            should_retry=True,
            command=_retry_command(target_agencies, 0, allow_send),
            reason=(
                "No previous run summary is available. Start with an agency-focused "
                "run if agency coverage is the open question."
            ),
        )
    return RunRecommendation(reason="No previous run summary is available.")


def recommend_next_run_summary(summary: RunSummary) -> RunRecommendation:
    agency_gap = _non_zero(summary.target_agencies, 0) - summary.counts.sent.agencies
    recruiter_gap = _non_zero(summary.target_recruiters, 0) - summary.counts.sent.recruiters
    if summary.status == "failed" or clean_text(summary.blocker):
        target_agencies = _positive_or_default(agency_gap, _non_zero(summary.target_agencies, 5))
        target_recruiters = _positive_or_default(
            recruiter_gap,
            _non_zero(summary.target_recruiters, 5),
        )
        if agency_gap > 0 and recruiter_gap <= 0:
            target_recruiters = 0
        elif recruiter_gap > 0 and agency_gap <= 0:
            target_agencies = 0
        return RunRecommendation(
            should_retry=True,
            command=_retry_command(target_agencies, target_recruiters, summary.allow_send),
            reason="The latest run did not finish cleanly.",
            blocker=summary.blocker,
        )
    if summary.allow_send:
        if agency_gap > 0:
            return RunRecommendation(
                should_retry=True,
                command=_retry_command(agency_gap, 0, True),
                reason=(
                    f"Agency target is still short by {agency_gap} sends; validate "
                    "the fixed agency lane without spending time on recruiters."
                ),
            )
        if recruiter_gap > 0:
            return RunRecommendation(
                should_retry=True,
                command=_retry_command(0, recruiter_gap, True),
                reason=f"Recruiter target is still short by {recruiter_gap} sends.",
            )
    if not summary.allow_send:
        return RunRecommendation(
            reason=(
                "Latest sourcing run completed. Use send-ready for already "
                "dry_run_ready leads, or continue sourcing if the dashboard shows "
                "a readiness shortfall."
            )
        )
    return RunRecommendation(
        reason="Latest sending run reached its requested send target; no retry is needed."
    )


def render_run_summary_text(summary: RunSummary) -> str:
    lines = [
        f"run_id={summary.run_id}",
        f"status={summary.status}",
        f"state={summary.state_path}",
        f"dashboard={summary.dashboard_path}",
        f"target={summary.target_agencies} agencies,{summary.target_recruiters} recruiters",
        f"sent={summary.counts.sent.agencies} agencies,{summary.counts.sent.recruiters} recruiters",
        (
            "checked_skipped="
            f"conversation_exists {summary.counts.conversation_exists.agencies} agencies,"
            f"{summary.counts.conversation_exists.recruiters} recruiters; "
            f"not_messageable {summary.counts.not_messageable.agencies} agencies,"
            f"{summary.counts.not_messageable.recruiters} recruiters; "
            f"blocked {summary.counts.blocked.agencies} agencies,"
            f"{summary.counts.blocked.recruiters} recruiters; "
            f"send_failed {summary.counts.send_failed.agencies} agencies,"
            f"{summary.counts.send_failed.recruiters} recruiters"
        ),
    ]
    if clean_text(summary.blocker):
        lines.append(f"blocker={clean_text(summary.blocker)}")
    if summary.recommendation.should_retry:
        lines.append(f"recommendation={summary.recommendation.reason}")
        lines.append(f"next_command={summary.recommendation.command}")
    elif clean_text(summary.recommendation.reason):
        lines.append(f"recommendation={summary.recommendation.reason}")
    return "\n".join(lines)


def daily_action_from_lead(
    lead: Lead,
    *,
    run_id: str,
    bucket: str,
    action: str,
    result: str,
    note: str | None = None,
    at: str | None = None,
) -> DailyLeadAction:
    return DailyLeadAction(
        at=at or now_iso(),
        run_id=run_id,
        bucket=bucket,
        lead_id=lead.id,
        name=lead.name,
        profile_url=lead.profile_url,
        lead_type=lead.lead_type,
        message_status=lead.message_status,
        action=action,
        result=result,
        note=note,
    )


def sent_count_from_actions(actions: list[DailyLeadAction], bucket: str) -> int:
    return sum(
        1 for action in actions if action.bucket == bucket and action.result == "sent-clicked"
    )


def _run_summaries_from_events(
    state: OutreachState,
    state_path: str,
) -> list[RunSummary]:
    by_run_id: dict[str, RunSummary] = {}
    for event in state.run_events:
        if not clean_text(event.run_id):
            continue
        summary = by_run_id.setdefault(
            event.run_id,
            RunSummary(run_id=event.run_id, state_path=state_path),
        )
        if event.started_at:
            summary.started_at = event.started_at
        if not summary.started_at and event.phase == "run-start":
            summary.started_at = event.at
        if event.completed_at:
            summary.completed_at = event.completed_at
        if event.command:
            summary.command = event.command
        if event.args:
            summary.args = list(event.args)
        if event.dashboard_path:
            summary.dashboard_path = event.dashboard_path
        if event.state_path:
            summary.state_path = event.state_path
        if event.target_agencies:
            summary.target_agencies = event.target_agencies
        if event.target_recruiters:
            summary.target_recruiters = event.target_recruiters
        if event.allow_send:
            summary.allow_send = True
        if event.blocker:
            summary.blocker = event.blocker
        if event.phase == "send-message":
            summary.actions.append(_run_event_action(state, event))
        if event.phase == "run-finish":
            summary.status = event.result or "completed"
    summaries = list(by_run_id.values())
    for summary in summaries:
        summary.counts = dashboard_run_counts(summary.actions)
        summary.recommendation = recommend_next_run_summary(summary)
    return summaries


def _run_event_action(state: OutreachState, event: RunEvent) -> DailyLeadAction:
    lead = find_lead_by_id(state.leads, event.lead_id)
    if lead is None:
        return DailyLeadAction(
            at=event.at,
            run_id=event.run_id,
            bucket=event.bucket,
            lead_id=event.lead_id,
            name=event.name,
            profile_url=None,
            lead_type=LeadType.BAD_FIT,
            message_status=MessageStatus.NONE,
            action="send-message",
            result=event.result,
            note=event.note or None,
        )
    return daily_action_from_lead(
        lead,
        run_id=event.run_id,
        bucket=event.bucket or bucket_for_lead(lead),
        action="send-message",
        result=event.result or lead.message_status.value,
        note=event.note or None,
        at=event.at,
    )


def _effective_run_time(summary: RunSummary) -> str:
    return summary.completed_at or summary.started_at


def _retry_command(target_agencies: int, target_recruiters: int, allow_send: bool) -> str:
    if allow_send:
        return (
            "recruiter-agency-outreach send-ready --session auto "
            f"--target-agencies {target_agencies} "
            f"--target-recruiters {target_recruiters} "
            "--allow-send --print-markdown"
        )
    return (
        "recruiter-agency-outreach run-daily --session auto "
        f"--target-agencies {target_agencies} "
        f"--target-recruiters {target_recruiters} "
        "--print-markdown"
    )


def _non_zero(value: int, default: int) -> int:
    return value if value != 0 else default


def _positive_or_default(value: int, default: int) -> int:
    return value if value > 0 else default


def empty_run_counts() -> RunCounts:
    return RunCounts(
        sent=BucketCounts(),
        dry_run_ready=BucketCounts(),
        conversation_exists=BucketCounts(),
        not_messageable=BucketCounts(),
        blocked=BucketCounts(),
        send_failed=BucketCounts(),
    )
