"""Markdown and text rendering for network automation state."""

from __future__ import annotations

from .models import (
    AcceptanceReport,
    CandidateStatus,
    PendingCleanupRun,
    PendingWithdrawStatus,
    Run,
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
    lines = [
        f"# LinkedIn Network Run {run.date.isoformat()}",
        "",
        f"- Run id: `{run.id}`",
        f"- State: `{run.state.value}`",
        f"- Target: {run.target}",
        f"- Start audit: {format_option(run.start_audit)}",
        f"- Final/latest audit: {format_option(run.latest_audit)}",
        f"- Audited delta: {format_delta(run.audited_delta())}",
        f"- Row-level verified pending: {run.verified_count()}",
        f"- Imported candidate observations: {len(run.observations)}",
        "",
        "## Source Counts",
    ]
    for source in run.sources:
        verified = run.source_verified_count(source.name)
        target_text = f" / target {source.target}" if source.target > 0 else ""
        exhausted_text = " (exhausted)" if source.exhausted else ""
        lines.append(f"- {source.name}: {verified} verified{target_text}{exhausted_text}")
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
            if candidate.status == CandidateStatus.PENDING
        }
    )
    lines.extend(("- None recorded",) if not names else ("- " + name for name in names))
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
