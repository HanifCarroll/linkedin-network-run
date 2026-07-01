"""Daily recruiter/agency/advisor orchestration surface."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .dashboard import (
    DailyLeadAction,
    DashboardReport,
    bucket_for_lead,
    build_dashboard_report,
    leads_for_message_validation,
    ready_leads,
    render_dashboard_markdown,
    write_dashboard_markdown,
)
from .drafts import draft_messages
from .models import AgencyAccount, AgencyAccountStatus, MessageStatus, OutreachState, RunEvent
from .run_summary import (
    daily_action_from_lead,
    latest_run_summary,
    new_run_id,
    render_run_summary_text,
    sent_count_from_actions,
)
from .send import SendMessageOptions, load_message_send_result, send_message
from .sourcing import (
    find_lead_by_id,
    import_account_capture,
    import_salesnav_capture,
    load_json_object,
)
from .storage import Store, append_run_event
from .suppression import apply_network_suppression_to_outreach_state
from .utils import now_iso

RECRUITER_SOURCE = "ASAP - Contract Recruiter Titles"
AGENCY_SOURCE = "ASAP - Agency Digital Agency Leaders"
AGENCY_SOFTWARE_CONSULTING_SOURCE = "ASAP - Agency Software Consulting Leaders"
AGENCY_DEVELOPMENT_AGENCY_SOURCE = "ASAP - Agency Development Agency Leaders"
AGENCY_PRODUCT_STUDIO_SOURCE = "ASAP - Agency Product Studio Leaders"
AGENCY_ACCOUNT_SOURCE = "ASAP - Agency Accounts Digital Agency"
AGENCY_ACCOUNT_DEVELOPMENT_SOURCE = "ASAP - Agency Accounts Development Agency"
AGENCY_ACCOUNT_PRODUCT_SOURCE = "ASAP - Agency Accounts Product Studio"
AGENCY_ACCOUNT_CONTACTS_SOURCE = "ASAP - Agency Account Contacts"
ADVISOR_IMPLEMENTATION_SOURCE = "ASAP - AI Advisors Implementation Partners"
RECRUITER_AGENCY_CAPTURE_TIMEOUT_SECONDS = 90.0


@dataclass(slots=True)
class DailyOptions:
    session: str
    target_agencies: int = 5
    target_recruiters: int = 5
    target_advisors: int = 5
    allow_send: bool = False
    print_markdown: bool = False
    refresh_saved_searches: bool = False
    pages_per_capture: int = 2
    account_pages_per_capture: int = 2
    limit: int = 25
    account_limit: int = 25
    max_capture_rounds: int = 4
    stop_after_connectable: int = 0
    row_scroll_delay_ms: int = 250
    capture_out_dir: str = ""
    account_capture_out_dir: str = ""
    message_out_dir: str = ""


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
    target_advisors: int = 5
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


def _progress(message: str) -> None:
    print(f"progress {now_iso()} {message}", file=sys.stderr, flush=True)


def run_daily(store: Store, options: DailyOptions) -> DailyResult:
    if options.allow_send:
        raise ValueError("run-daily is sourcing-only; use send-ready --allow-send for real sends")
    if not options.session:
        raise ValueError("--session is required")
    started_at = now_iso()
    run_id = "daily-" + started_at.replace(":", "").replace("-", "")
    dashboard_path = str(store.run_dashboard_path(run_id))
    run_args = _run_daily_args(options)
    state = store.load()
    append_run_event(
        state,
        RunEvent(
            at=started_at,
            run_id=run_id,
            phase="run-start",
            command="run-daily",
            args=run_args,
            state_path=str(store.state_path),
            dashboard_path=dashboard_path,
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            target_advisors=max(0, options.target_advisors),
            allow_send=False,
            started_at=started_at,
        ),
    )
    store.save(state)
    _progress(
        f"run-start run_id={run_id} target_agencies={max(0, options.target_agencies)} "
        f"target_recruiters={max(0, options.target_recruiters)} "
        f"target_advisors={max(0, options.target_advisors)} "
        f"refresh_saved_searches={str(options.refresh_saved_searches).lower()}"
    )
    actions: list[DailyLeadAction] = []
    try:
        for bucket, sources, target in daily_buckets(
            options.target_agencies,
            options.target_recruiters,
            options.target_advisors,
        ):
            if target <= 0:
                continue
            _progress(f"bucket-start bucket={bucket} target={target}")
            _draft_and_validate_bucket(store, options, run_id, bucket, target, actions)
            if _ready_count(store, bucket) >= target:
                _progress(f"bucket-ready bucket={bucket} ready={_ready_count(store, bucket)}")
                continue
            if bucket == "agency":
                _run_agency_bucket(store, options, run_id, target, actions)
            else:
                _run_people_bucket(store, options, run_id, bucket, sources, target, actions)
            _progress(f"bucket-finish bucket={bucket} ready={_ready_count(store, bucket)}")
    except Exception as exc:
        completed_at = now_iso()
        _append_lifecycle_event(
            store,
            RunEvent(
                at=completed_at,
                run_id=run_id,
                phase="run-finish",
                command="run-daily",
                args=run_args,
                result="failed",
                blocker=str(exc),
                state_path=str(store.state_path),
                dashboard_path=dashboard_path,
                target_agencies=max(0, options.target_agencies),
                target_recruiters=max(0, options.target_recruiters),
                target_advisors=max(0, options.target_advisors),
                allow_send=False,
                started_at=started_at,
                completed_at=completed_at,
            ),
        )
        state = store.load()
        report = build_dashboard_report(
            state,
            str(store.state_path),
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            target_advisors=max(0, options.target_advisors),
            allow_send=False,
            actions=actions,
            mode="sourcing",
            dashboard_path=dashboard_path,
        )
        write_dashboard_markdown(dashboard_path, report)
        write_dashboard_markdown(str(store.latest_run_dashboard_path()), report)
        _progress(f"run-failed run_id={run_id} blocker={exc}")
        raise
    state = store.load()
    completed_at = now_iso()
    append_run_event(
        state,
        RunEvent(
            at=completed_at,
            run_id=run_id,
            phase="run-finish",
            command="run-daily",
            args=run_args,
            result="completed",
            state_path=str(store.state_path),
            dashboard_path=dashboard_path,
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            target_advisors=max(0, options.target_advisors),
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
        target_advisors=max(0, options.target_advisors),
        allow_send=False,
        actions=actions,
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
    _progress(f"run-finish run_id={run_id} dashboard={dashboard_path}")
    return DailyResult(report=report, dashboard_path=dashboard_path, markdown=markdown)


def send_ready(store: Store, options: SendReadyOptions) -> SendReadyResult:
    if not options.allow_send:
        raise ValueError("send-ready requires --allow-send")
    if not options.session:
        raise ValueError("--session is required")
    result_root = Path(options.result_dir) if options.result_dir else None
    if result_root is not None and not result_root.is_dir():
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
            target_advisors=max(0, options.target_advisors),
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
        _send_ready_bucket(
            store,
            result_root,
            run_id=run_id,
            session=options.session,
            bucket="advisor",
            target=max(0, options.target_advisors),
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
                target_advisors=max(0, options.target_advisors),
                allow_send=True,
                started_at=started_at,
                completed_at=completed_at,
            ),
        )
        _write_send_ready_dashboard(
            store,
            dashboard_path=dashboard_path,
            target_agencies=max(0, options.target_agencies),
            target_recruiters=max(0, options.target_recruiters),
            target_advisors=max(0, options.target_advisors),
            actions=actions,
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
            target_advisors=max(0, options.target_advisors),
            allow_send=True,
            started_at=started_at,
            completed_at=completed_at,
        ),
    )
    store.save(state)

    report = _write_send_ready_dashboard(
        store,
        dashboard_path=dashboard_path,
        target_agencies=max(0, options.target_agencies),
        target_recruiters=max(0, options.target_recruiters),
        target_advisors=max(0, options.target_advisors),
        actions=actions,
    )
    markdown = render_dashboard_markdown(report)
    summary = latest_run_summary(state, str(store.state_path))
    summary_text = render_run_summary_text(summary) if summary else ""
    return SendReadyResult(
        report=report,
        dashboard_path=dashboard_path,
        markdown=markdown,
        summary_text=summary_text,
    )


def _write_send_ready_dashboard(
    store: Store,
    *,
    dashboard_path: str,
    target_agencies: int,
    target_recruiters: int,
    target_advisors: int,
    actions: list[DailyLeadAction],
) -> DashboardReport:
    state = store.load()
    report = build_dashboard_report(
        state,
        str(store.state_path),
        target_agencies=target_agencies,
        target_recruiters=target_recruiters,
        target_advisors=target_advisors,
        allow_send=True,
        actions=actions,
        mode="sending",
        dashboard_path=dashboard_path,
    )
    write_dashboard_markdown(dashboard_path, report)
    write_dashboard_markdown(str(store.latest_run_dashboard_path()), report)
    return report


def daily_buckets(
    target_agencies: int, target_recruiters: int, target_advisors: int = 5
) -> list[tuple[str, list[str], int]]:
    return [
        ("agency", [], max(0, target_agencies)),
        ("recruiter", [RECRUITER_SOURCE], max(0, target_recruiters)),
        ("advisor", [ADVISOR_IMPLEMENTATION_SOURCE], max(0, target_advisors)),
    ]


def _run_daily_args(options: DailyOptions) -> list[str]:
    args = [
        "run-daily",
        "--session",
        options.session,
        "--target-agencies",
        str(max(0, options.target_agencies)),
        "--target-recruiters",
        str(max(0, options.target_recruiters)),
        "--target-advisors",
        str(max(0, options.target_advisors)),
    ]
    if options.refresh_saved_searches:
        args.append("--refresh-saved-searches")
    if options.print_markdown:
        args.append("--print-markdown")
    return args


def _draft_and_validate_bucket(
    store: Store,
    options: DailyOptions,
    run_id: str,
    bucket: str,
    target: int,
    actions: list[DailyLeadAction],
) -> None:
    _progress(f"draft-start bucket={bucket}")
    state = store.load()
    draft_messages(state, 0)
    suppressed = apply_network_suppression_to_outreach_state(state)
    store.save(state)
    if suppressed:
        _progress(f"cross-workflow-suppressed bucket={bucket} count={suppressed}")
    _progress(f"validate-start bucket={bucket} ready={_ready_count(store, bucket)} target={target}")
    _validate_bucket(
        store,
        run_id=run_id,
        session=options.session,
        message_out_dir=options.message_out_dir,
        bucket=bucket,
        target=target,
        actions=actions,
    )
    _progress(f"validate-finish bucket={bucket} ready={_ready_count(store, bucket)}")


def _run_people_bucket(
    store: Store,
    options: DailyOptions,
    run_id: str,
    bucket: str,
    sources: list[str],
    target: int,
    actions: list[DailyLeadAction],
) -> None:
    if not sources:
        raise ValueError(f"daily bucket {bucket!r} has no sources")
    for round_number in range(1, max(1, options.max_capture_rounds) + 1):
        _progress(f"people-round-start bucket={bucket} round={round_number}")
        for source in sources:
            if _ready_count(store, bucket) >= target:
                return
            _capture_people_source(store, options, run_id, source, round_number)
            _draft_and_validate_bucket(store, options, run_id, bucket, target, actions)
            if _ready_count(store, bucket) >= target:
                return


def _run_agency_bucket(
    store: Store,
    options: DailyOptions,
    run_id: str,
    target: int,
    actions: list[DailyLeadAction],
) -> None:
    for round_number in range(1, max(1, options.max_capture_rounds) + 1):
        _progress(f"agency-round-start round={round_number}")
        if _ready_count(store, "agency") >= target:
            return
        if len(_agency_accounts_needing_contact_capture(store.load(), target)) < target:
            for source in _default_agency_account_sources():
                try:
                    _capture_account_source(store, options, run_id, source, round_number)
                except Exception as exc:
                    _progress(
                        f"capture-account-failed source={source!r} "
                        f"round={round_number} error={exc}"
                    )
                    continue
                if len(_agency_accounts_needing_contact_capture(store.load(), target)) >= target:
                    break
        _capture_agency_contacts_from_accounts(store, options, run_id, target, round_number)
        _draft_and_validate_bucket(store, options, run_id, "agency", target, actions)
        if _ready_count(store, "agency") >= target:
            return


def _capture_people_source(
    store: Store,
    options: DailyOptions,
    run_id: str,
    source: str,
    round_number: int,
) -> None:
    state = store.load()
    url = _people_capture_url(state, source)
    _progress(f"capture-people-start source={source!r} round={round_number} url={url}")
    browser = _capture_browser(store, options, run_id, source, round_number)
    try:
        _capture, artifact_path = browser.capture_salesnav(
            source=source,
            url=url,
            pages=max(1, options.pages_per_capture),
            limit=max(1, options.limit),
            stop_after_connectable=max(0, options.stop_after_connectable),
            only_connectable=False,
            row_scroll_delay_ms=max(1, options.row_scroll_delay_ms),
        )
    finally:
        close = getattr(browser, "close", None)
        if callable(close):
            close()
    state = store.load()
    import_salesnav_capture(state, load_json_object(artifact_path))
    suppressed = apply_network_suppression_to_outreach_state(state)
    store.save(state)
    _progress(
        f"capture-people-finish source={source!r} suppressed={suppressed} "
        f"artifact={artifact_path}"
    )


def _capture_account_source(
    store: Store,
    options: DailyOptions,
    run_id: str,
    source: str,
    round_number: int,
) -> None:
    state = store.load()
    url = _account_capture_url(state, source)
    _progress(f"capture-account-start source={source!r} round={round_number} url={url}")
    browser = _account_browser(store, options, run_id, source, round_number)
    try:
        _capture, artifact_path = browser.capture_accounts(
            source=source,
            url=url,
            pages=max(1, options.account_pages_per_capture),
            limit=max(1, options.account_limit),
        )
    finally:
        close = getattr(browser, "close", None)
        if callable(close):
            close()
    state = store.load()
    import_account_capture(state, load_json_object(artifact_path))
    store.save(state)
    _progress(f"capture-account-finish source={source!r} artifact={artifact_path}")


def _capture_agency_contacts_from_accounts(
    store: Store,
    options: DailyOptions,
    run_id: str,
    target: int,
    round_number: int,
) -> None:
    needed = max(0, target - _ready_count(store, "agency"))
    if needed <= 0:
        return
    accounts = _agency_accounts_needing_contact_capture(
        store.load(),
        _agency_contact_account_limit(needed),
    )
    _progress(
        f"capture-agency-contacts-start round={round_number} needed={needed} "
        f"accounts={len(accounts)}"
    )
    for account in accounts:
        if _ready_count(store, "agency") >= target:
            return
        strategy = _next_agency_contact_search_strategy(account)
        if strategy is None:
            continue
        url = _agency_account_contact_search_url(account, strategy)
        if not url:
            _progress(f"capture-agency-contact-skip account={account.name!r} reason=missing_url")
            continue
        source = _agency_contact_source(account, strategy)
        _progress(
            f"capture-agency-contact-start account={account.name!r} "
            f"strategy={strategy.name} round={round_number} url={url}"
        )
        browser = _capture_browser(store, options, run_id, source, round_number)
        try:
            _capture, artifact_path = browser.capture_salesnav(
                source=source,
                url=url,
                pages=max(1, options.pages_per_capture),
                limit=max(1, options.limit),
                stop_after_connectable=max(0, options.stop_after_connectable),
                only_connectable=False,
                row_scroll_delay_ms=max(1, options.row_scroll_delay_ms),
            )
        except Exception as exc:
            _progress(
                f"capture-agency-contact-failed account={account.name!r} "
                f"strategy={strategy.name} error={exc}"
            )
            _record_agency_contact_capture_error(store, account.id, strategy.name, exc)
            continue
        finally:
            close = getattr(browser, "close", None)
            if callable(close):
                close()
        state = store.load()
        index = _find_agency_account_by_id(state.agency_accounts, account.id)
        if index < 0:
            continue
        account_for_import = state.agency_accounts[index]
        open_before = _agency_account_open_lead_count(state, account.id)
        capture = load_json_object(artifact_path)
        import_salesnav_capture(state, capture, agency_account=account_for_import)
        suppressed = apply_network_suppression_to_outreach_state(state)
        open_after = _agency_account_open_lead_count(state, account.id)
        now = now_iso()
        updated_account = state.agency_accounts[index]
        updated_account.contact_capture_count += 1
        updated_account.last_contact_capture_at = now
        updated_account.last_contact_strategy = strategy.name
        updated_account.last_contact_error = None
        updated_account.last_contact_error_at = None
        updated_account.updated_at = now
        if (
            open_after <= open_before
            and updated_account.contact_capture_count
            >= _agency_contact_strategy_count(updated_account)
        ):
            updated_account.status = AgencyAccountStatus.EXHAUSTED
        store.save(state)
        _progress(
            f"capture-agency-contact-finish account={account.name!r} strategy={strategy.name} "
            f"open_before={open_before} open_after={open_after} suppressed={suppressed} "
            f"artifact={artifact_path}"
        )


def _capture_browser(
    store: Store,
    options: DailyOptions,
    run_id: str,
    source: str,
    round_number: int,
) -> Any:
    from apps.network_automation.browser import PlaywriterBrowserClient

    root = Path(options.capture_out_dir) if options.capture_out_dir else store.dir / "captures"
    out_dir = root / run_id / _safe_path_segment(source) / f"round-{round_number:02d}"
    _ = RECRUITER_AGENCY_CAPTURE_TIMEOUT_SECONDS
    return PlaywriterBrowserClient(out_dir=out_dir)


def _account_browser(
    store: Store,
    options: DailyOptions,
    run_id: str,
    source: str,
    round_number: int,
) -> Any:
    from .account_browser import PlaywriterAccountCaptureClient

    root = (
        Path(options.account_capture_out_dir)
        if options.account_capture_out_dir
        else store.dir / "account-captures"
    )
    out_dir = root / run_id / _safe_path_segment(source) / f"round-{round_number:02d}"
    return PlaywriterAccountCaptureClient(out_dir=out_dir)


def _ready_count(store: Store, bucket: str) -> int:
    return len(ready_leads(store.load(), bucket))


def _validate_bucket(
    store: Store,
    *,
    run_id: str,
    session: str,
    message_out_dir: str = "",
    bucket: str,
    target: int,
    actions: list[DailyLeadAction],
) -> None:
    processed: set[str] = set()
    while len(ready_leads(store.load(), bucket)) < target:
        state = store.load()
        candidates = [
            lead for lead in leads_for_message_validation(state, bucket) if lead.id not in processed
        ]
        if not candidates:
            return
        lead = candidates[0]
        processed.add(lead.id)
        _progress(f"validate-lead-start bucket={bucket} lead_id={lead.id} name={lead.name!r}")
        send_message(
            store,
            SendMessageOptions(
                lead_id=lead.id,
                run_id=run_id,
                session=session,
                out_dir=message_out_dir,
                dry_run=True,
                allow_send=False,
            ),
        )
        updated_state = store.load()
        updated = find_lead_by_id(updated_state.leads, lead.id)
        if updated is None:
            continue
        latest_attempt = updated.send_attempts[-1] if updated.send_attempts else None
        actions.append(
            daily_action_from_lead(
                updated,
                run_id=run_id,
                bucket=bucket_for_lead(updated),
                action="dry-run-message",
                result=latest_attempt.status if latest_attempt else updated.message_status.value,
                note=latest_attempt.note if latest_attempt else None,
            )
        )
        _progress(
            f"validate-lead-finish bucket={bucket} lead_id={updated.id} "
            f"status={latest_attempt.status if latest_attempt else updated.message_status.value}"
        )


def _people_capture_url(state: OutreachState, source: str) -> str:
    cursor = state.capture_cursors.get(source)
    if cursor and cursor.resume_url:
        return cursor.resume_url
    generated = _default_people_source_url(source)
    if generated:
        return generated
    raise ValueError(f"no URL for source {source}")


def _account_capture_url(state: OutreachState, source: str) -> str:
    cursor = state.capture_cursors.get(source)
    if cursor and cursor.resume_url:
        return cursor.resume_url
    generated = _default_account_source_url(source)
    if generated:
        return generated
    raise ValueError(f"no URL for account source {source}")


def _default_people_source_url(source: str) -> str:
    if source == RECRUITER_SOURCE:
        return _sales_nav_people_search_url(
            _append_filters(_base_people_filters(), _contract_recruiter_title_filter()),
            "",
        )
    if source == AGENCY_SOURCE:
        return _sales_nav_people_search_url(
            _append_filters(
                _base_people_filters(),
                _agency_leader_title_filter(),
                _agency_industry_filter(),
            ),
            "digital agency",
        )
    if source == AGENCY_SOFTWARE_CONSULTING_SOURCE:
        return _sales_nav_people_search_url(
            _append_filters(
                _base_people_filters(),
                _agency_leader_title_filter(),
                _agency_industry_filter(),
            ),
            "software consulting",
        )
    if source == AGENCY_DEVELOPMENT_AGENCY_SOURCE:
        return _sales_nav_people_search_url(
            _append_filters(_base_people_filters(), _agency_leader_title_filter()),
            "development agency",
        )
    if source == AGENCY_PRODUCT_STUDIO_SOURCE:
        return _sales_nav_people_search_url(
            _append_filters(
                _base_people_filters(),
                _agency_leader_title_filter(),
                _agency_industry_filter(),
            ),
            "product studio",
        )
    if source == ADVISOR_IMPLEMENTATION_SOURCE:
        return _sales_nav_people_search_url(
            _base_people_filters(),
            (
                "AI consultant OR AI advisor OR business consultant OR operations consultant OR "
                "fractional COO OR fractional CTO OR growth consultant OR automation consultant OR "
                "AI strategy OR workflow automation OR AI implementation OR AI diagnostic OR "
                "back office automation OR decision support"
            ),
        )
    return ""


def _default_agency_account_sources() -> list[str]:
    return [AGENCY_ACCOUNT_DEVELOPMENT_SOURCE, AGENCY_ACCOUNT_SOURCE, AGENCY_ACCOUNT_PRODUCT_SOURCE]


def _default_account_source_url(source: str) -> str:
    base = [
        _sales_nav_filter("REGION", [("103644278", "United States")]),
        _agency_industry_filter(),
        _sales_nav_filter(
            "COMPANY_HEADCOUNT",
            [("C", "11-50"), ("D", "51-200"), ("E", "201-500")],
        ),
    ]
    if source == AGENCY_ACCOUNT_SOURCE:
        return _sales_nav_account_search_url(base, "digital product agency")
    if source == AGENCY_ACCOUNT_DEVELOPMENT_SOURCE:
        return _sales_nav_account_search_url(base, "custom software development agency")
    if source == AGENCY_ACCOUNT_PRODUCT_SOURCE:
        return _sales_nav_account_search_url(base, "product studio")
    return ""


@dataclass(frozen=True, slots=True)
class _AgencyContactStrategy:
    name: str
    title_filter: dict[str, object] | None = None
    keywords: str = ""
    include_recent_activity: bool = False


EXECUTIVE_DELIVERY_KEYWORDS = (
    '("CEO" OR "President" OR "Managing Director" OR "Head of Engineering" OR '
    '"VP Engineering" OR "Technical Director" OR "Head of Delivery" OR '
    '"Client Services" OR Partnerships)'
)
RESOURCE_DELIVERY_KEYWORDS = (
    '("Resource Manager" OR Resourcing OR "Talent Manager" OR "Head of Talent" OR '
    '"Client Services" OR Partnerships OR Delivery)'
)


def _agency_contact_search_strategies(account: AgencyAccount) -> list[_AgencyContactStrategy]:
    leader_filter = _agency_leader_title_filter()
    strategies = [
        _AgencyContactStrategy(
            name="founder_recent",
            title_filter=leader_filter,
            include_recent_activity=True,
        ),
        _AgencyContactStrategy(
            name="executive_delivery_broad",
            keywords=EXECUTIVE_DELIVERY_KEYWORDS,
        ),
    ]
    if account.fit_score >= 75:
        strategies.append(
            _AgencyContactStrategy(
                name="resource_delivery_broad",
                keywords=RESOURCE_DELIVERY_KEYWORDS,
            )
        )
    return strategies


def _next_agency_contact_search_strategy(account: AgencyAccount) -> _AgencyContactStrategy | None:
    strategies = _agency_contact_search_strategies(account)
    if account.contact_capture_count < 0 or account.contact_capture_count >= len(strategies):
        return None
    return strategies[account.contact_capture_count]


def _agency_contact_strategy_count(account: AgencyAccount) -> int:
    return len(_agency_contact_search_strategies(account))


def _agency_account_contact_search_url(
    account: AgencyAccount,
    strategy: _AgencyContactStrategy,
) -> str:
    company_id = _sales_nav_company_id(account)
    if not company_id:
        return ""
    company = _sales_nav_filter("CURRENT_COMPANY", [(company_id, account.name)])
    filters = _append_filters(_base_people_filters(strategy.include_recent_activity), company)
    if strategy.title_filter:
        filters = _append_filters(filters, strategy.title_filter)
    return _sales_nav_people_search_url(filters, strategy.keywords)


def _agency_contact_source(account: AgencyAccount, strategy: _AgencyContactStrategy) -> str:
    return f"{AGENCY_ACCOUNT_CONTACTS_SOURCE} - {account.name} - {strategy.name}".strip()


def _agency_accounts_needing_contact_capture(
    state: OutreachState,
    target: int,
) -> list[AgencyAccount]:
    accounts = [
        account
        for account in state.agency_accounts
        if account.status == AgencyAccountStatus.QUALIFIED
        and account.account_url
        and not _account_has_active_lead(state, account.id)
        and _next_agency_contact_search_strategy(account) is not None
    ]
    accounts.sort(
        key=lambda account: (
            account.last_contact_capture_at or "",
            -account.fit_score,
            account.name,
        )
    )
    if target > 0:
        return accounts[:target]
    return accounts


def _agency_contact_account_limit(needed: int) -> int:
    return max(5, needed * 2)


def _account_has_active_lead(state: OutreachState, account_id: str) -> bool:
    for lead in state.leads:
        if lead.agency_account_id != account_id or lead.status.value == "rejected":
            continue
        if (
            _is_terminal_message_status(lead.message_status)
            and lead.message_status != MessageStatus.DRY_RUN_READY
        ):
            continue
        return True
    return False


def _agency_account_open_lead_count(state: OutreachState, account_id: str) -> int:
    count = 0
    for lead in state.leads:
        if lead.agency_account_id != account_id or lead.status.value != "eligible":
            continue
        if (
            _is_terminal_message_status(lead.message_status)
            and lead.message_status != MessageStatus.DRY_RUN_READY
        ):
            continue
        count += 1
    return count


def _is_terminal_message_status(status: MessageStatus) -> bool:
    return status in {
        MessageStatus.DRY_RUN_READY,
        MessageStatus.NEEDS_EDIT,
        MessageStatus.APPROVED,
        MessageStatus.SEND_FAILED,
        MessageStatus.SENT,
        MessageStatus.MANUALLY_SENT,
        MessageStatus.NOT_MESSAGEABLE,
        MessageStatus.CONVERSATION_EXISTS,
        MessageStatus.BLOCKED,
        MessageStatus.SUPPRESSED,
        MessageStatus.REPLIED,
        MessageStatus.REPLIED_NOT_FIT,
        MessageStatus.REPLIED_FUTURE,
        MessageStatus.REPLIED_UNKNOWN,
    }


def _record_agency_contact_capture_error(
    store: Store,
    account_id: str,
    strategy_name: str,
    cause: Exception,
) -> None:
    state = store.load()
    index = _find_agency_account_by_id(state.agency_accounts, account_id)
    if index < 0:
        return
    now = now_iso()
    account = state.agency_accounts[index]
    account.last_contact_capture_at = now
    account.last_contact_strategy = strategy_name
    account.last_contact_error = str(cause)[:240]
    account.last_contact_error_at = now
    account.contact_error_count += 1
    account.updated_at = now
    store.save(state)


def _find_agency_account_by_id(accounts: list[AgencyAccount], account_id: str) -> int:
    for index, account in enumerate(accounts):
        if account.id == account_id:
            return index
    return -1


def _sales_nav_company_id(account: AgencyAccount) -> str:
    raw = account.account_url or ""
    parsed = urlparse(raw)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "sales" and index + 2 < len(parts) and parts[index + 1] == "company":
            return parts[index + 2].strip()
    marker = "/sales/company/"
    if marker in raw:
        return raw.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0].strip("/")
    return ""


def _base_people_filters(include_recent_activity: bool = True) -> list[dict[str, object]]:
    filters = [
        _sales_nav_filter("REGION", [("103644278", "United States")]),
        _sales_nav_filter("RELATIONSHIP", [("S", "2nd degree connections")]),
    ]
    if include_recent_activity:
        filters.append(_sales_nav_filter("POSTED_ON_LINKEDIN", [("RPOL", "Posted on LinkedIn")]))
    return filters


def _contract_recruiter_title_filter() -> dict[str, object]:
    return _sales_nav_filter(
        "CURRENT_TITLE",
        [
            ("1711", "Contract Recruiter"),
            ("8379", "Senior Contract Recruiter"),
            ("16659", "Contract Technical Recruiter"),
            ("21060", "Senior Technical Recruiter Contract"),
        ],
    )


def _agency_leader_title_filter() -> dict[str, object]:
    return _sales_nav_filter(
        "CURRENT_TITLE",
        [
            ("35", "Founder"),
            ("103", "Co-Founder"),
            ("1", "Owner"),
            ("18", "Partner"),
            ("154", "Managing Partner"),
            ("182", "Principal Consultant"),
            ("200", "Technical Director"),
        ],
    )


def _agency_industry_filter() -> dict[str, object]:
    return _sales_nav_filter(
        "INDUSTRY",
        [
            ("4", "Software Development"),
            ("96", "IT Services and IT Consulting"),
            ("99", "Design Services"),
        ],
    )


def _sales_nav_filter(filter_type: str, values: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "type": filter_type,
        "values": [{"id": item_id, "text": text} for item_id, text in values],
    }


def _append_filters(
    base: list[dict[str, object]],
    *extra: dict[str, object],
) -> list[dict[str, object]]:
    return [*base, *extra]


def _sales_nav_people_search_url(filters: list[dict[str, object]], keywords: str) -> str:
    return "https://www.linkedin.com/sales/search/people?query=" + _sales_nav_query(
        filters,
        keywords,
    )


def _sales_nav_account_search_url(filters: list[dict[str, object]], keywords: str) -> str:
    return "https://www.linkedin.com/sales/search/company?query=" + _sales_nav_query(
        filters,
        keywords,
    )


def _sales_nav_query(filters: list[dict[str, object]], keywords: str) -> str:
    expressions = [_sales_nav_filter_expression(item) for item in filters]
    body = f"filters:List({','.join(expressions)})"
    if keywords.strip():
        body += ",keywords:" + _sales_nav_value_escape(keywords)
    return quote(f"({body})", safe="")


def _sales_nav_filter_expression(item: dict[str, object]) -> str:
    raw_values = item.get("values")
    values = raw_values if isinstance(raw_values, list) else []
    expressions = []
    for value in values:
        if not isinstance(value, dict):
            continue
        expressions.append(
            "("
            f"id:{_sales_nav_value_escape(str(value.get('id') or ''))},"
            f"text:{_sales_nav_value_escape(str(value.get('text') or ''))},"
            "selectionType:INCLUDED"
            ")"
        )
    escaped_type = _sales_nav_value_escape(str(item.get("type") or ""))
    return f"(type:{escaped_type},values:List({','.join(expressions)}))"


def _sales_nav_value_escape(value: str) -> str:
    return quote(value, safe="")


def _safe_path_segment(value: str) -> str:
    cleaned = " ".join(value.lower().split())
    for old, new in (("/", "-"), ("\\", "-"), (" ", "-"), (":", "-")):
        cleaned = cleaned.replace(old, new)
    return cleaned or "source"


def _send_ready_bucket(
    store: Store,
    result_root: Path | None,
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
        result_path: Path | None = None
        if result_root is not None:
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
                result_path=str(result_path) if result_path else "",
            ),
        )
        updated_state = store.load()
        updated = find_lead_by_id(updated_state.leads, lead.id)
        if updated is None:
            continue
        latest_attempt = updated.send_attempts[-1] if updated.send_attempts else None
        actions.append(
            daily_action_from_lead(
                updated,
                run_id=run_id,
                bucket=bucket_for_lead(updated),
                action="send-message",
                result=latest_attempt.status if latest_attempt else updated.message_status.value,
                note=latest_attempt.note if latest_attempt else None,
            )
        )
        if latest_attempt and latest_attempt.status == MessageStatus.BLOCKED.value:
            reason = latest_attempt.note or "blocked"
            raise RuntimeError(f"browser blocked while sending {updated.id}: {reason}")


def _append_lifecycle_event(store: Store, event: RunEvent) -> None:
    state = store.load()
    append_run_event(state, event)
    store.save(state)
