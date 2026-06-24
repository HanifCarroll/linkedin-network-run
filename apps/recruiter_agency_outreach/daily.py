"""Daily recruiter/agency orchestration surface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dashboard import (
    DailyLeadAction,
    DashboardReport,
    bucket_for_lead,
    build_dashboard_report,
    ready_leads,
    render_dashboard_markdown,
    write_dashboard_markdown,
)
from .drafts import draft_messages
from .models import RunEvent
from .run_summary import (
    daily_action_from_lead,
    latest_run_summary,
    new_run_id,
    render_run_summary_text,
    sent_count_from_actions,
)
from .send import SendMessageOptions, load_message_send_result, result_note, send_message
from .sourcing import find_lead_by_id
from .storage import Store, append_run_event
from .utils import now_iso


@dataclass(slots=True)
class DailyOptions:
    session: str
    target_agencies: int = 5
    target_recruiters: int = 5
    allow_send: bool = False
    print_markdown: bool = False


@dataclass(slots=True)
class DailyResult:
    report: DashboardReport
    dashboard_path: str
    markdown: str


@dataclass(slots=True)
class SendReadyOptions:
    session: str
    target_agencies: int = 5
    target_recruiters: int = 5
    allow_send: bool = False
    print_markdown: bool = False
    result_dir: str = ""
    dashboard_path: str = ""


@dataclass(slots=True)
class SendReadyResult:
    report: DashboardReport
    dashboard_path: str
    markdown: str
    summary_text: str


def run_daily(store: Store, options: DailyOptions) -> DailyResult:
    if options.allow_send:
        raise ValueError("run-daily is sourcing-only; use send-ready --allow-send for real sends")
    if not options.session:
        raise ValueError("--session is required")
    started_at = now_iso()
    run_id = "daily-" + started_at.replace(":", "").replace("-", "")
    state = store.load()
    append_run_event(
        state,
        RunEvent(
            at=started_at,
            run_id=run_id,
            phase="run-start",
            command="run-daily",
            state_path=str(store.state_path),
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            allow_send=False,
            started_at=started_at,
        ),
    )
    draft_messages(state, 0)
    dashboard_path = str(store.run_dashboard_path(run_id))
    completed_at = now_iso()
    append_run_event(
        state,
        RunEvent(
            at=completed_at,
            run_id=run_id,
            phase="run-finish",
            command="run-daily",
            result="completed",
            state_path=str(store.state_path),
            dashboard_path=dashboard_path,
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            allow_send=False,
            started_at=started_at,
            completed_at=completed_at,
        ),
    )
    store.save(state)
    report = build_dashboard_report(
        state,
        str(store.state_path),
        target_agencies=max(0, options.target_agencies),
        target_recruiters=max(0, options.target_recruiters),
        allow_send=False,
        mode="sourcing",
        dashboard_path=dashboard_path,
    )
    markdown = render_dashboard_markdown(report)
    output = store.run_dashboard_path(run_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown)
    latest = store.latest_run_dashboard_path()
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(markdown)
    return DailyResult(report=report, dashboard_path=dashboard_path, markdown=markdown)


def send_ready(store: Store, options: SendReadyOptions) -> SendReadyResult:
    if not options.allow_send:
        raise ValueError("send-ready requires --allow-send")
    if not options.session:
        raise ValueError("--session is required")
    if not options.result_dir:
        raise RuntimeError(
            "live browser message adapter is not wired in this workstream; "
            "pass --result-dir with one structured result artifact per ready lead"
        )
    result_root = Path(options.result_dir)
    if not result_root.is_dir():
        raise ValueError(f"--result-dir is not a directory: {result_root}")

    run_id = new_run_id("send-ready")
    dashboard_path = options.dashboard_path or str(store.run_dashboard_path(run_id))
    started_at = now_iso()
    _append_lifecycle_event(
        store,
        RunEvent(
            at=started_at,
            run_id=run_id,
            phase="run-start",
            command="send-ready",
            state_path=str(store.state_path),
            dashboard_path=dashboard_path,
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            allow_send=True,
            started_at=started_at,
        ),
    )
    actions: list[DailyLeadAction] = []
    try:
        _send_ready_bucket(
            store,
            result_root,
            run_id=run_id,
            session=options.session,
            bucket="agency",
            target=max(0, options.target_agencies),
            actions=actions,
        )
        _send_ready_bucket(
            store,
            result_root,
            run_id=run_id,
            session=options.session,
            bucket="recruiter",
            target=max(0, options.target_recruiters),
            actions=actions,
        )
    except Exception as exc:
        completed_at = now_iso()
        _append_lifecycle_event(
            store,
            RunEvent(
                at=completed_at,
                run_id=run_id,
                phase="run-finish",
                command="send-ready",
                result="failed",
                blocker=str(exc),
                state_path=str(store.state_path),
                dashboard_path=dashboard_path,
                target_agencies=max(0, options.target_agencies),
                target_recruiters=max(0, options.target_recruiters),
                allow_send=True,
                started_at=started_at,
                completed_at=completed_at,
            ),
        )
        raise

    completed_at = now_iso()
    state = store.load()
    append_run_event(
        state,
        RunEvent(
            at=completed_at,
            run_id=run_id,
            phase="run-finish",
            command="send-ready",
            result="completed",
            state_path=str(store.state_path),
            dashboard_path=dashboard_path,
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            allow_send=True,
            started_at=started_at,
            completed_at=completed_at,
        ),
    )
    store.save(state)

    state = store.load()
    report = build_dashboard_report(
        state,
        str(store.state_path),
        target_agencies=max(0, options.target_agencies),
        target_recruiters=max(0, options.target_recruiters),
        allow_send=True,
        actions=actions,
        mode="sending",
        dashboard_path=dashboard_path,
    )
    write_dashboard_markdown(dashboard_path, report)
    write_dashboard_markdown(str(store.latest_run_dashboard_path()), report)
    markdown = render_dashboard_markdown(report)
    summary = latest_run_summary(state, str(store.state_path))
    summary_text = render_run_summary_text(summary) if summary else ""
    return SendReadyResult(
        report=report,
        dashboard_path=dashboard_path,
        markdown=markdown,
        summary_text=summary_text,
    )


def daily_buckets(target_agencies: int, target_recruiters: int) -> list[tuple[str, list[str], int]]:
    return [
        ("agency", [], max(0, target_agencies)),
        ("recruiter", ["ASAP - Contract Recruiter Titles"], max(0, target_recruiters)),
    ]


def _send_ready_bucket(
    store: Store,
    result_root: Path,
    *,
    run_id: str,
    session: str,
    bucket: str,
    target: int,
    actions: list[DailyLeadAction],
) -> None:
    processed: set[str] = set()
    while sent_count_from_actions(actions, bucket) < target:
        state = store.load()
        candidates = [lead for lead in ready_leads(state, bucket) if lead.id not in processed]
        if not candidates:
            return
        lead = candidates[0]
        processed.add(lead.id)
        result_path = result_root / f"{lead.id}.json"
        if not result_path.exists():
            raise ValueError(f"missing structured result artifact for {lead.id}: {result_path}")
        result = load_message_send_result(result_path)
        if result.dry_run:
            raise ValueError(f"send-ready result for {lead.id} is dry_run=true")
        send_message(
            store,
            SendMessageOptions(
                lead_id=lead.id,
                run_id=run_id,
                session=session,
                allow_send=True,
                result_path=str(result_path),
            ),
        )
        updated_state = store.load()
        updated = find_lead_by_id(updated_state.leads, lead.id)
        if updated is None:
            continue
        actions.append(
            daily_action_from_lead(
                updated,
                run_id=run_id,
                bucket=bucket_for_lead(updated),
                action="send-message",
                result=result.status,
                note=result_note(result),
            )
        )


def _append_lifecycle_event(store: Store, event: RunEvent) -> None:
    state = store.load()
    append_run_event(state, event)
    store.save(state)
