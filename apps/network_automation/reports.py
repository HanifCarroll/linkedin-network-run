"""Markdown and text rendering for network automation state."""

from __future__ import annotations

from .models import (
    AcceptanceReport,
    CandidateStatus,
    PendingCleanupRun,
    PendingWithdrawStatus,
    Run,
    RunState,
    SendLedgerSummary,
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


def render_report(run: Run, send_summary: SendLedgerSummary | None = None) -> str:
    audited_delta = run.audited_delta()
    plan = run.operator_plan()
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
        f"- Next action: `{plan.action}`" + (f" ({plan.reason})" if plan.reason else ""),
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
    active_incident = run.active_browser_incident()
    if active_incident is not None:
        source_counts_header = lines.pop()
        lines.extend(
            [
                "## Browser Inspection",
                "",
                f"- Incident: `{active_incident.incident_path}`",
                f"- Operation: `{active_incident.operation}`",
                f"- Possible send: `{'yes' if active_incident.possible_send else 'no'}`",
                f"- Diagnostic: `{active_incident.diagnostic_path or 'unavailable'}`",
                f"- Screenshot: `{active_incident.screenshot_path or 'unavailable'}`",
                f"- Receipt: `{active_incident.receipt_path}`",
                "",
                source_counts_header,
            ]
        )
    for source in run.sources:
        verified = run.source_verified_count(source.name)
        target_text = f" / target {source.target}" if source.target > 0 else ""
        cursor = run.capture_cursors.get(source.name)
        status_text = ""
        if source.exhausted:
            status_text = " (exhausted)"
        elif cursor and cursor.deferred_for_run:
            status_text = " (deferred for run)"
        lines.append(f"- {source.name}: {verified} durable{target_text}{status_text}")
    if send_summary is not None:
        lines.extend(["", "## Daily Send Ledger"])
        lines.append(f"- Date: {send_summary.date.isoformat()} ({send_summary.timezone})")
        lines.append(f"- Durable sends: {send_summary.durable_sent_count}")
        lines.append(f"- Provisional sends: {send_summary.provisional_count}")
        lines.append(f"- Failed attempts: {send_summary.failed_count}")
        lines.append(f"- Reverted to Connect: {send_summary.reverted_count}")
        lines.append(f"- Audit top-ups: {send_summary.top_up_count}")
        lines.append(f"- Ledger path: {send_summary.ledger_path}")
        lines.append("- Durable sends by source:")
        if send_summary.by_source:
            for source_name, count in send_summary.by_source.items():
                lines.append(f"  - {source_name}: {count}")
        else:
            lines.append("  - None recorded")
    lines.extend(["", "## Source Yield"])
    for stats in source_yield_report(run):
        yield_text = (
            "n/a" if stats.connectable_yield is None else f"{stats.connectable_yield * 100.0:.1f}%"
        )
        lines.append(
            f"- {stats.source}: {stats.connectable_count} connectable / {stats.raw_row_count} "
            f"rows ({yield_text}); already pending {stats.already_pending_count}; "
            f"email-required skips {stats.email_required_skips}; "
            f"reverted-to-connect {stats.reverted_connect_count}; {stats.recommendation}"
        )
    if run.capture_cursors:
        lines.extend(["", "## Source Scan Progress"])
        for source in run.sources:
            cursor = run.capture_cursors.get(source.name)
            if cursor is None:
                continue
            next_text = cursor.next_url or cursor.resume_url or "None"
            if cursor.end_of_results:
                status = "end of results"
            elif cursor.deferred_for_run:
                status = f"deferred: {cursor.deferred_reason or cursor.cursor_status or 'unknown'}"
            else:
                status = cursor.cursor_status or "resume available"
            lines.append(
                f"- {source.name}: {status}; next scan URL: {next_text}; "
                f"last scanned: {cursor.last_scanned_url or 'unknown'}"
            )
    if audited_delta != run.target:
        lines.extend(["", "## Reconciliation"])
        if run.state == RunState.DONE and run.verified_count() < run.target:
            lines.append(
                "- Completion status: run is closed incomplete; do not treat this as a "
                "successful target-complete send run."
            )
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
        if (audited_delta is None or audited_delta < run.target) and not (
            run.state == RunState.DONE and run.verified_count() < run.target
        ):
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


def render_send_summary(summary: SendLedgerSummary) -> str:
    lines = [
        f"# LinkedIn Network Sends {summary.date.isoformat()}",
        "",
        f"- Timezone: {summary.timezone}",
        f"- Ledger path: {summary.ledger_path}",
        f"- Durable sent count: {summary.durable_sent_count}",
        f"- Provisional sends: {summary.provisional_count}",
        f"- Failed attempts: {summary.failed_count}",
        f"- Reverted to Connect: {summary.reverted_count}",
        f"- Audit top-ups: {summary.top_up_count}",
    ]
    if summary.history_logs_scanned or summary.synced_entries:
        lines.append(
            f"- History sync: {summary.synced_entries} new entr"
            f"{'y' if summary.synced_entries == 1 else 'ies'} from "
            f"{summary.history_logs_scanned} run log"
            f"{'' if summary.history_logs_scanned == 1 else 's'}"
        )
    lines.extend(["", "## Durable Sends By Source"])
    if summary.by_source:
        for source, count in summary.by_source.items():
            lines.append(f"- {source}: {count}")
    else:
        lines.append("- None recorded")
    lines.extend(["", "## Latest Attempt Statuses"])
    if summary.by_status:
        for status, count in summary.by_status.items():
            lines.append(f"- {status}: {count}")
    else:
        lines.append("- None recorded")
    lines.extend(["", "## Entries"])
    if summary.entries:
        for entry in summary.entries:
            durable = "durable" if entry.durable else "not durable"
            lines.append(
                f"- {entry.attempted_at.isoformat()} | {entry.source} | "
                f"{entry.name} | {entry.status.value} ({durable})"
            )
    else:
        lines.append("- None recorded")
    return "\n".join(lines)


def render_acceptance_report(report: AcceptanceReport) -> str:
    if report.unchecked:
        reconciliation_note = (
            f"Daily acceptance reconciliation should check {report.unchecked} "
            "candidate(s) in this report window."
        )
    else:
        reconciliation_note = (
            "Daily acceptance reconciliation appears current for this report window."
        )
    lines = [
        "# LinkedIn Acceptance Report",
        "",
        f"- Min age days: {report.min_age_days}",
        f"- Max age days: {'none' if report.max_age_days is None else report.max_age_days}",
        f"- Total sent in window: {report.total_sent}",
        f"- Checked: {report.checked}",
        f"- Unchecked: {report.unchecked}",
        f"- Accepted: {report.accepted}{percentage_suffix(report.accepted, report.checked)}",
        f"- Invalidated accepted evidence: {report.invalidated}",
        f"- Pending: {report.pending}",
        f"- Connectable/not pending: {report.connectable}",
        f"- Unknown: {report.unknown}",
        f"- Blocked: {report.blocked}",
        f"- Failed: {report.failed}",
        f"- Withdrawn: {report.withdrawn}",
        f"- Current enough: {reconciliation_note}",
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
                f"checked {source_report.checked}, invalidated {source_report.invalidated}, "
                f"pending {source_report.pending}, "
                f"connectable {source_report.connectable}, unknown {source_report.unknown}, "
                f"unchecked {source_report.unchecked}"
            )
    lines.extend(["", f"## Daily Confirmations ({report.daily_timezone})"])
    for summary in report.daily_windows:
        lines.append(
            f"- Last {summary.days} days: {summary.newly_confirmed_accepted} newly "
            f"confirmed ({summary.per_calendar_day:.2f}/calendar day); "
            f"coverage complete on {summary.complete_days}/{summary.days} days"
        )
    if not report.daily:
        lines.append("- No daily metrics requested")
    else:
        for item in report.daily:
            if item.coverage_complete is True:
                coverage = f"complete ({item.checked}/{item.eligible} checked)"
            elif item.coverage_complete is False:
                coverage = f"incomplete ({item.checked}/{item.eligible} checked)"
            else:
                coverage = "not recorded"
            blocker = f"; blocker: {item.blocker}" if item.blocker else ""
            lines.append(
                f"- {item.date.isoformat()}: {item.newly_confirmed_accepted} newly "
                f"confirmed; coverage {coverage}{blocker}"
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
