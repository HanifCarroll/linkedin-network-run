"""Daily recruiter/agency orchestration surface."""

from __future__ import annotations

from dataclasses import dataclass

from .dashboard import DashboardReport, build_dashboard_report, render_dashboard_markdown
from .drafts import draft_messages
from .models import RunEvent
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


def daily_buckets(target_agencies: int, target_recruiters: int) -> list[tuple[str, list[str], int]]:
    return [
        ("agency", [], max(0, target_agencies)),
        ("recruiter", ["ASAP - Contract Recruiter Titles"], max(0, target_recruiters)),
    ]
