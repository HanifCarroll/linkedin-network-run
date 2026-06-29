"""Markdown and text rendering for network automation state."""

from __future__ import annotations

from .models import (
    AcceptanceReport,
    CandidateStatus,
    PendingCleanupRun,
    PendingWithdrawStatus,
    Run,
    RunState,
    source_yield_report,
)


def format_option(value: object | None) -> str:
    return "None" if value is None else f"Some({value})"


def format_delta(value: int | None) -> str:
    return "None" if value is None else f"Some({value})"


def percentage_suffix(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return ""
    return f" ({numerator * 100.0 / denominator:.1f}%)"


def render_report(run: Run) -> str:
    audited_delta = run.audited_delta()
    audit_top_up_count = sum(
        1 for candidate in run.candidates if candidate.status == CandidateStatus.AUDIT_TOP_UP
    )
    accepted_count = sum(
        1 for candidate in run.candidates if candidate.status == CandidateStatus.ACCEPTED
    )
    pending_count = sum(
        1 for candidate in run.candidates if candidate.status == CandidateStatus.PENDING
    )
    skipped_count = sum(
        1 for candidate in run.candidates if candidate.status == CandidateStatus.SKIPPED
    )
    failed_count = sum(
        1 for candidate in run.candidates if candidate.status == CandidateStatus.FAILED
    )
    lines = [
        f"# LinkedIn Network Run {run.date.isoformat()}",
        "",
        f"- Run id: `{run.id}`",
        f"- State: `{run.state.value}`",
        f"- Target: {run.target}",
        f"- Start audit: {format_option(run.start_audit)}",
        f"- Final/latest audit: {format_option(run.latest_audit)}",
        f"- Audited delta: {format_delta(audited_delta)}",
        f"- Durable confirmed sends: {run.verified_count()}",
        f"- Confirmed pending: {pending_count}",
        f"- Confirmed accepted: {accepted_count}",
        f"- Provisional sends awaiting confirmation: {run.provisional_count()}",
        f"- Reverted to Connect / not durable: {run.reverted_connect_count()}",
        f"- Real send attempts recorded: {run.real_send_attempt_count()}",
        f"- Audit top-ups recorded: {audit_top_up_count}",
        f"- Skipped candidates: {skipped_count}",
        f"- Failed candidates: {failed_count}",
        f"- Imported candidate observations: {len(run.observations)}",
        "",
        "## Source Counts",
    ]
    for source in run.sources:
        verified = run.source_verified_count(source.name)
        target_text = f" / target {source.target}" if source.target > 0 else ""
        exhausted_text = " (exhausted)" if source.exhausted else ""
        lines.append(f"- {source.name}: {verified} durable{target_text}{exhausted_text}")
    lines.extend(["", "## Source Yield"])
    for stats in source_yield_report(run):
        yield_text = (
            "n/a" if stats.connectable_yield is None else f"{stats.connectable_yield * 100.0:.1f}%"
        )
        lines.append(
            f"- {stats.source}: {stats.connectable_count} connectable / {stats.raw_row_count} "
            f"rows ({yield_text}); already pending {stats.already_pending_count}; "
            f"email-required skips {stats.email_required_skips}; {stats.recommendation}"
        )
    if audited_delta != run.target:
        lines.extend(["", "## Reconciliation"])
        if run.start_audit is not None:
            lines.append(f"- Expected final audit: People ({run.start_audit + run.target})")
        if audited_delta is None:
            lines.append("- Sent-page audit shortfall: unknown; no final audit delta recorded")
        elif audited_delta < run.target:
            lines.append(f"- Sent-page audit shortfall: {run.target - audited_delta}")
        else:
            lines.append(f"- Sent-page audit surplus: {audited_delta - run.target}")
        recorded_invite_events = run.verified_count() + audit_top_up_count
        if audited_delta is not None and recorded_invite_events != audited_delta:
            gap = recorded_invite_events - audited_delta
            lines.append(f"- Recorded invite events minus audited delta: {gap}")
            if gap > 0 and run.state in {RunState.SENDING, RunState.NEEDS_REAUDIT}:
                lines.append(
                    "- Uncertain send recovery: pause further sends until the latest "
                    "clicked-send artifact and a fresh sent-page audit prove whether the "
                    "clicked invitation landed."
                )
        if audited_delta is None or audited_delta < run.target:
            lines.append(
                "- Finish guidance: Sent-page delta is now a pending-queue sanity check, not "
                "the completion source of truth. Finish only after durable confirmed sends "
                "reach the target."
            )
    if run.timings:
        lines.extend(["", "## Phase Timing"])
        total = sum(event.duration_ms for event in run.timings)
        lines.append(f"- Total recorded: {format_duration_ms(total)}")
        by_phase: dict[str, int] = {}
        for event in run.timings:
            by_phase[event.phase] = by_phase.get(event.phase, 0) + event.duration_ms
        for phase in sorted(by_phase):
            lines.append(f"- {phase}: {format_duration_ms(by_phase[phase])}")
    if run.notes:
        lines.extend(["", "## Notes"])
        lines.extend("- " + note for note in run.notes)
    lines.extend(["", "## Verified Names"])
    names = sorted(
        {
            candidate.name
            for candidate in run.candidates
            if candidate.status in {CandidateStatus.PENDING, CandidateStatus.ACCEPTED}
        }
    )
    lines.extend(("- None recorded",) if not names else ("- " + name for name in names))
    reverted_names = sorted(
        {
            candidate.name
            for candidate in run.candidates
            if candidate.status == CandidateStatus.REVERTED_CONNECT
        }
    )
    if reverted_names:
        lines.extend(["", "## Reverted To Connect Names"])
        lines.extend("- " + name for name in reverted_names)
    top_up_names = sorted(
        {
            candidate.name
            for candidate in run.candidates
            if candidate.status == CandidateStatus.AUDIT_TOP_UP
        }
    )
    if top_up_names:
        lines.extend(["", "## Audit Top-Up Names"])
        lines.extend("- " + name for name in top_up_names)
    return "\n".join(lines)


def render_acceptance_report(report: AcceptanceReport) -> str:
    lines = [
        "# LinkedIn Acceptance Report",
        "",
        f"- Min age days: {report.min_age_days}",
        f"- Max age days: {'none' if report.max_age_days is None else report.max_age_days}",
        f"- Total sent in window: {report.total_sent}",
        f"- Checked: {report.checked}",
        f"- Unchecked: {report.unchecked}",
        f"- Accepted: {report.accepted}{percentage_suffix(report.accepted, report.checked)}",
        f"- Pending: {report.pending}",
        f"- Connectable/not pending: {report.connectable}",
        f"- Unknown: {report.unknown}",
        f"- Blocked: {report.blocked}",
        f"- Failed: {report.failed}",
        f"- Withdrawn: {report.withdrawn}",
        "",
        "## By Source",
    ]
    if not report.by_source:
        lines.append("- No invitations in window")
    else:
        for source in sorted(report.by_source):
            source_report = report.by_source[source]
            lines.append(
                f"- {source}: accepted {source_report.accepted}"
                f"{percentage_suffix(source_report.accepted, source_report.checked)} / "
                f"checked {source_report.checked}, pending {source_report.pending}, "
                f"connectable {source_report.connectable}, unknown {source_report.unknown}, "
                f"unchecked {source_report.unchecked}"
            )
    return "\n".join(lines)


def format_pending_threshold(run: PendingCleanupRun) -> str:
    if run.threshold_days > 0:
        if run.threshold_days % 7 == 0:
            weeks = run.threshold_days // 7
            return f"{weeks} {'week' if weeks == 1 else 'weeks'}"
        return f"{run.threshold_days} {'day' if run.threshold_days == 1 else 'days'}"
    return f"{run.threshold_months} {'month' if run.threshold_months == 1 else 'months'}"


def render_pending_report(run: PendingCleanupRun) -> str:
    lines = [
        f"# LinkedIn Pending Cleanup {run.date.isoformat()}",
        "",
        f"- Run id: `{run.id}`",
        f"- State: `{run.state.value}`",
        f"- Threshold: {format_pending_threshold(run)}",
        f"- Safety cap: {run.max_withdrawals}",
        f"- Start audit: {format_option(run.start_audit)}",
        f"- Final/latest audit: {format_option(run.latest_audit)}",
        f"- Audited delta: {format_delta(run.audited_delta())}",
        f"- Withdrawn: {run.withdrawn_count()}",
        f"- Imported pending observations: {len(run.observations)}",
        "",
        "## Withdrawn Names",
    ]
    names = sorted(
        f"{event.name} ({event.age_text})"
        for event in run.withdrawals
        if event.status == PendingWithdrawStatus.WITHDRAWN
    )
    lines.extend(("- None recorded",) if not names else ("- " + name for name in names))
    return "\n".join(lines)


def format_duration_ms(duration_ms: int) -> str:
    if duration_ms < 1000:
        return f"{duration_ms}ms"
    seconds = duration_ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60.0:.1f}m"
