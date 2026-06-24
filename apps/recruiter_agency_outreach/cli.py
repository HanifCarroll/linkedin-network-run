"""CLI namespace for the recruiter/agency outreach port."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .daily import DailyOptions, SendReadyOptions, run_daily, send_ready
from .dashboard import (
    build_agency_pool_diagnosis,
    build_agency_pool_next_action,
    build_dashboard_report,
    render_agency_pool_next_action_text,
    render_dashboard_markdown,
    write_dashboard_markdown,
)
from .drafts import draft_evidence, draft_messages, message_subject, write_draft_markdown
from .inspection import (
    agency_account_queue,
    build_lead_detail,
    parse_lead_statuses,
    parse_message_status,
    queue_item_by_lead_id,
    queue_items,
    render_agency_accounts_text,
    render_counts_text,
    render_lead_detail_text,
    render_queue_text,
)
from .models import (
    AgencyAccountStatus,
    AgencyContactReviewStatus,
    LeadStatus,
    MessageDraft,
    MessageStatus,
)
from .run_summary import latest_run_summary, recommend_next_run, render_run_summary_text
from .send import SendMessageOptions, send_message
from .sourcing import (
    import_account_capture,
    import_agency_source_capture,
    import_salesnav_capture,
    load_agency_source_capture,
    load_agency_source_csv,
    load_json_object,
    promote_agency_contact_candidates,
    review_agency_contact_candidate,
    write_agency_source_capture,
)
from .storage import Store
from .utils import now_iso

DEFAULT_CAPTURE_DIRNAME = "captures"
DEFAULT_ACCOUNT_CAPTURE_DIRNAME = "account-captures"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recruiter-agency-outreach",
        description="Recruiter and agency sourcing, drafting, and guarded messages.",
    )
    parser.add_argument("--state-dir", default="", help="state directory")
    subparsers = parser.add_subparsers(dest="command")

    run_daily_parser = subparsers.add_parser("run-daily")
    run_daily_parser.add_argument("--session", default="auto")
    run_daily_parser.add_argument("--target-agencies", type=int, default=5)
    run_daily_parser.add_argument("--target-recruiters", type=int, default=5)
    run_daily_parser.add_argument("--allow-send", action="store_true")
    run_daily_parser.add_argument("--print-markdown", action="store_true")
    run_daily_parser.add_argument("--refresh-saved-searches", action="store_true")
    run_daily_parser.add_argument("--pages", type=int, default=2)
    run_daily_parser.add_argument("--account-pages", type=int, default=2)
    run_daily_parser.add_argument("--limit", type=int, default=25)
    run_daily_parser.add_argument("--account-limit", type=int, default=25)
    run_daily_parser.add_argument("--max-capture-rounds", type=int, default=4)
    run_daily_parser.add_argument("--stop-after-connectable", type=int, default=0)
    run_daily_parser.add_argument("--row-scroll-delay-ms", type=int, default=250)
    run_daily_parser.add_argument("--capture-out-dir", default="")
    run_daily_parser.add_argument("--account-capture-out-dir", default="")
    run_daily_parser.add_argument("--message-out-dir", default="")

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--session", default="auto")
    capture_parser.add_argument("--source", default="")
    capture_parser.add_argument("--url", default="")
    capture_parser.add_argument("--out-dir", default="")
    capture_parser.add_argument("--pages", type=int, default=2)
    capture_parser.add_argument("--limit", type=int, default=25)
    capture_parser.add_argument("--only-connectable", action="store_true")

    capture_accounts_parser = subparsers.add_parser("capture-accounts")
    capture_accounts_parser.add_argument("--session", default="auto")
    capture_accounts_parser.add_argument("--source", default="")
    capture_accounts_parser.add_argument("--url", default="")
    capture_accounts_parser.add_argument("--out-dir", default="")
    capture_accounts_parser.add_argument("--pages", type=int, default=2)
    capture_accounts_parser.add_argument("--limit", type=int, default=25)

    import_capture_parser = subparsers.add_parser("import-capture")
    import_capture_parser.add_argument("path")
    import_capture_parser.add_argument("--only-connectable", action="store_true")

    import_accounts_parser = subparsers.add_parser("import-accounts")
    import_accounts_parser.add_argument("path")

    accounts_parser = subparsers.add_parser("accounts")
    accounts_parser.add_argument("--limit", type=int, default=20)
    accounts_parser.add_argument("--status", action="append", default=None)
    accounts_parser.add_argument("--json", action="store_true")

    lead_parser = subparsers.add_parser("lead")
    lead_subparsers = lead_parser.add_subparsers(dest="lead_command")
    lead_show_parser = lead_subparsers.add_parser("show")
    lead_show_parser.add_argument("--lead-id", required=True)
    lead_show_parser.add_argument("--json", action="store_true")

    queue_parser = subparsers.add_parser("queue")
    queue_parser.add_argument("--limit", type=int, default=20)
    queue_parser.add_argument("--lead-id", default="")
    queue_parser.add_argument("--status", action="append", default=None)
    queue_parser.add_argument("--json", action="store_true")
    queue_parser.add_argument("--include-drafts", action="store_true")

    draft_parser = subparsers.add_parser("draft")
    draft_parser.add_argument("--limit", type=int, default=20)
    draft_parser.add_argument("--out", default="")
    draft_parser.add_argument("--json", action="store_true")

    dashboard_parser = subparsers.add_parser("dashboard")
    dashboard_parser.add_argument("--out", default="")
    dashboard_parser.add_argument("--target-agencies", type=int, default=5)
    dashboard_parser.add_argument("--target-recruiters", type=int, default=5)
    dashboard_parser.add_argument("--allow-send", action="store_true")
    dashboard_parser.add_argument("--print-markdown", action="store_true")

    last_run_parser = subparsers.add_parser("last-run")
    last_run_parser.add_argument("--json", action="store_true")

    recommend_parser = subparsers.add_parser("recommend-next-run")
    recommend_parser.add_argument("--target-agencies", type=int, default=5)
    recommend_parser.add_argument("--target-recruiters", type=int, default=5)
    recommend_parser.add_argument("--allow-send", action="store_true")
    recommend_parser.add_argument("--json", action="store_true")

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--addr", default="127.0.0.1:8765")
    serve_parser.add_argument("--access-token", default="")
    serve_parser.add_argument("--log-level", default="info")

    revise_parser = subparsers.add_parser("revise")
    revise_parser.add_argument("--lead-id", required=True)
    revise_parser.add_argument("--body-file", required=True)
    revise_parser.add_argument("--subject", default="")
    revise_parser.add_argument("--angle", default="")

    send_ready_parser = subparsers.add_parser("send-ready")
    send_ready_parser.add_argument("--session", default="auto")
    send_ready_parser.add_argument("--target-agencies", type=int, default=5)
    send_ready_parser.add_argument("--target-recruiters", type=int, default=5)
    send_ready_parser.add_argument("--allow-send", action="store_true")
    send_ready_parser.add_argument("--result-dir", default="")
    send_ready_parser.add_argument("--dashboard", default="")
    send_ready_parser.add_argument("--print-markdown", action="store_true")

    send_parser = subparsers.add_parser("send-message")
    send_parser.add_argument("--lead-id", required=True)
    send_parser.add_argument("--session", default="auto")
    send_parser.add_argument("--dry-run", action="store_true")
    send_parser.add_argument("--allow-send", action="store_true")
    send_parser.add_argument("--result-path", default="")
    send_parser.add_argument("--out-dir", default="")

    mark_parser = subparsers.add_parser("mark-message")
    mark_parser.add_argument("--lead-id", required=True)
    mark_parser.add_argument("--status", required=True)
    mark_parser.add_argument("--note", default="")

    reject_parser = subparsers.add_parser("reject")
    reject_parser.add_argument("--lead-id", required=True)
    reject_parser.add_argument("--reason", required=True)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--json", action="store_true")

    agency_pool = subparsers.add_parser("agency-pool")
    agency_subparsers = agency_pool.add_subparsers(dest="agency_command")

    import_source_parser = agency_subparsers.add_parser("import-source")
    import_source_parser.add_argument("path")
    import_source_parser.add_argument("--json", action="store_true")

    build_source_parser = agency_subparsers.add_parser("build-source")
    build_source_parser.add_argument("--csv", required=True)
    build_source_parser.add_argument("--source", required=True)
    build_source_parser.add_argument("--source-type", default="manual_directory")
    build_source_parser.add_argument("--url", default="")
    build_source_parser.add_argument("--out", required=True)
    build_source_parser.add_argument("--json", action="store_true")

    import_directory_parser = agency_subparsers.add_parser("import-directory")
    import_directory_parser.add_argument("--csv", required=True)
    import_directory_parser.add_argument("--source", required=True)
    import_directory_parser.add_argument("--source-type", default="manual_directory")
    import_directory_parser.add_argument("--url", default="")
    import_directory_parser.add_argument("--out", default="")
    import_directory_parser.add_argument("--json", action="store_true")

    review_parser = agency_subparsers.add_parser("review-contact")
    review_parser.add_argument("--candidate-id", required=True)
    review_parser.add_argument("--review-status", required=True)
    review_parser.add_argument("--name", default="")
    review_parser.add_argument("--title", default="")
    review_parser.add_argument("--note", default="")

    promote_parser = agency_subparsers.add_parser("promote-contact")
    promote_parser.add_argument("--candidate-id", required=True)
    promote_parser.add_argument("--draft", action="store_true")

    promote_many_parser = agency_subparsers.add_parser("promote-contacts")
    promote_many_parser.add_argument("--limit", type=int, default=20)
    promote_many_parser.add_argument("--draft", action="store_true")
    promote_many_parser.add_argument("--allow-multiple-per-agency", action="store_true")

    contacts_parser = agency_subparsers.add_parser("contacts")
    contacts_parser.add_argument("--status", default="")
    contacts_parser.add_argument("--review-status", default="")
    contacts_parser.add_argument("--limit", type=int, default=20)
    contacts_parser.add_argument("--json", action="store_true")

    agency_subparsers.add_parser("next").add_argument("--json", action="store_true")
    agency_subparsers.add_parser("diagnose").add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    store = Store(args.state_dir or None)
    try:
        _run_command(args, store)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_command(args: argparse.Namespace, store: Store) -> None:
    if args.command == "run-daily":
        daily_result = run_daily(
            store,
            DailyOptions(
                session=args.session,
                target_agencies=args.target_agencies,
                target_recruiters=args.target_recruiters,
                allow_send=args.allow_send,
                print_markdown=args.print_markdown,
                refresh_saved_searches=args.refresh_saved_searches,
                pages_per_capture=args.pages,
                account_pages_per_capture=args.account_pages,
                limit=args.limit,
                account_limit=args.account_limit,
                max_capture_rounds=args.max_capture_rounds,
                stop_after_connectable=args.stop_after_connectable,
                row_scroll_delay_ms=args.row_scroll_delay_ms,
                capture_out_dir=args.capture_out_dir,
                account_capture_out_dir=args.account_capture_out_dir,
                message_out_dir=args.message_out_dir,
            ),
        )
        print(f"dashboard={daily_result.dashboard_path}")
        if args.print_markdown:
            print(daily_result.markdown)
        return
    if args.command == "capture":
        _run_capture_command(args, store)
        return
    if args.command == "capture-accounts":
        _run_capture_accounts_command(args, store)
        return
    if args.command == "import-capture":
        state = store.load()
        capture_summary = import_salesnav_capture(
            state,
            load_json_object(args.path),
            only_connectable=args.only_connectable,
        )
        store.save(state)
        print(
            f"source={capture_summary.source} stored={capture_summary.stored} "
            f"updated={capture_summary.updated} eligible={capture_summary.eligible} "
            f"needs_review={capture_summary.reviewed} rejected={capture_summary.rejected} "
            f"total={capture_summary.total_leads}"
        )
        return
    if args.command == "import-accounts":
        state = store.load()
        account_summary = import_account_capture(state, load_json_object(args.path))
        store.save(state)
        print(
            f"source={account_summary.source} stored={account_summary.stored} "
            f"updated={account_summary.updated} qualified={account_summary.qualified} "
            f"needs_review={account_summary.needs_review} "
            f"rejected={account_summary.rejected} total={account_summary.total}"
        )
        return
    if args.command == "accounts":
        state = store.load()
        statuses = _parse_account_statuses(args.status or [AgencyAccountStatus.QUALIFIED.value])
        account_items = agency_account_queue(state, statuses, args.limit)
        if args.json:
            print(_json(account_items))
            return
        print(render_agency_accounts_text(account_items))
        return
    if args.command == "lead":
        _run_lead_command(args, store)
        return
    if args.command == "queue":
        state = store.load()
        if args.lead_id:
            item = queue_item_by_lead_id(
                state,
                args.lead_id,
                include_drafts=args.include_drafts,
            )
            if item is None:
                raise ValueError(f"unknown lead id {args.lead_id!r}")
            queue_result = [item]
        else:
            queue_result = queue_items(
                state,
                parse_lead_statuses(args.status or [LeadStatus.ELIGIBLE.value]),
                args.limit,
                include_drafts=args.include_drafts,
            )
        if args.json:
            print(_json(queue_result))
            return
        print(render_queue_text(queue_result))
        return
    if args.command == "draft":
        state = store.load()
        draft_report = draft_messages(state, args.limit)
        store.save(state)
        out = args.out or str(store.default_draft_report_path())
        write_draft_markdown(out, draft_report)
        if args.json:
            print(_json(draft_report))
        print(f"drafted={len(draft_report.items)} out={out}")
        return
    if args.command == "dashboard":
        state = store.load()
        out = args.out or str(store.latest_render_dashboard_path())
        dashboard_report = build_dashboard_report(
            state,
            str(store.state_path),
            target_agencies=args.target_agencies,
            target_recruiters=args.target_recruiters,
            allow_send=args.allow_send,
            dashboard_path=out,
        )
        write_dashboard_markdown(out, dashboard_report)
        print(f"dashboard={out}")
        if args.print_markdown:
            print(render_dashboard_markdown(dashboard_report))
        return
    if args.command == "last-run":
        state = store.load()
        summary = latest_run_summary(state, str(store.state_path))
        if summary is None:
            raise ValueError("no run summary found")
        if args.json:
            print(_json(summary))
            return
        print(render_run_summary_text(summary))
        return
    if args.command == "recommend-next-run":
        state = store.load()
        recommendation = recommend_next_run(
            state,
            str(store.state_path),
            target_agencies=args.target_agencies,
            target_recruiters=args.target_recruiters,
            allow_send=args.allow_send,
        )
        if args.json:
            print(_json(recommendation))
            return
        print(f"recommendation={recommendation.reason}")
        if recommendation.blocker:
            print(f"blocker={recommendation.blocker}")
        if recommendation.should_retry:
            print(f"next_command={recommendation.command}")
        return
    if args.command == "serve":
        _run_serve_command(args)
        return
    if args.command == "revise":
        state = store.load()
        body = Path(args.body_file).read_text().replace("\r\n", "\n").strip()
        if not body:
            raise ValueError("revision body is empty")
        for lead in state.leads:
            if lead.id == args.lead_id:
                subject = args.subject or (lead.draft.subject if lead.draft else "")
                if not subject:
                    subject = message_subject(lead)
                angle = args.angle or (lead.draft.angle if lead.draft else "")
                lead.draft = MessageDraft(
                    subject=subject,
                    body=body,
                    angle=angle,
                    evidence=list(lead.draft.evidence) if lead.draft else draft_evidence(lead),
                    generated_at=now_iso(),
                )
                lead.message_status = MessageStatus.DRAFTED
                store.save(state)
                print(f"revised={lead.id}")
                return
        raise ValueError(f"unknown lead id {args.lead_id!r}")
    if args.command == "send-ready":
        ready_result = send_ready(
            store,
            SendReadyOptions(
                session=args.session,
                target_agencies=args.target_agencies,
                target_recruiters=args.target_recruiters,
                allow_send=args.allow_send,
                result_dir=args.result_dir,
                dashboard_path=args.dashboard,
                print_markdown=args.print_markdown,
            ),
        )
        if ready_result.summary_text:
            print(ready_result.summary_text)
        print(f"dashboard={ready_result.dashboard_path}")
        if args.print_markdown:
            print(ready_result.markdown)
        return
    if args.command == "send-message":
        print(
            send_message(
                store,
                SendMessageOptions(
                    lead_id=args.lead_id,
                    session=args.session,
                    dry_run=args.dry_run,
                    allow_send=args.allow_send,
                    result_path=args.result_path,
                    out_dir=args.out_dir,
                ),
            )
        )
        return
    if args.command == "mark-message":
        state = store.load()
        for lead in state.leads:
            if lead.id == args.lead_id:
                lead.message_status = parse_message_status(args.status)
                if args.note:
                    lead.notes.append(args.note)
                store.save(state)
                print(f"lead={lead.id} message_status={lead.message_status.value}")
                return
        raise ValueError(f"unknown lead id {args.lead_id!r}")
    if args.command == "reject":
        state = store.load()
        for lead in state.leads:
            if lead.id == args.lead_id:
                lead.status = LeadStatus.REJECTED
                lead.reject_reasons.append(args.reason)
                store.save(state)
                print(f"rejected={lead.id}")
                return
        raise ValueError(f"unknown lead id {args.lead_id!r}")
    if args.command == "report":
        state = store.load()
        if args.json:
            print(_json(build_dashboard_report(state, str(store.state_path)).counts))
            return
        print(render_counts_text(state, str(store.state_path)))
        return
    if args.command == "agency-pool":
        _run_agency_pool_command(args, store)
        return
    raise ValueError(f"unsupported command {args.command!r}")


def _run_capture_command(args: argparse.Namespace, store: Store) -> None:
    source = str(args.source or "").strip()
    if not source:
        raise ValueError("--source is required")
    browser = _browser_from_capture_args(args, store)
    try:
        _capture, artifact_path = browser.capture_salesnav(
            source=source,
            url=args.url or None,
            pages=args.pages,
            limit=args.limit,
            only_connectable=args.only_connectable,
        )
    finally:
        close = getattr(browser, "close", None)
        if callable(close):
            close()
    state = store.load()
    capture_summary = import_salesnav_capture(
        state,
        load_json_object(artifact_path),
        only_connectable=args.only_connectable,
    )
    store.save(state)
    print(
        f"artifact={artifact_path} source={capture_summary.source} "
        f"stored={capture_summary.stored} updated={capture_summary.updated} "
        f"eligible={capture_summary.eligible} needs_review={capture_summary.reviewed} "
        f"rejected={capture_summary.rejected} total={capture_summary.total_leads}"
    )


def _browser_from_capture_args(args: argparse.Namespace, store: Store) -> Any:
    from apps.network_automation.browser import PlaywrightBrowserClient

    out_dir = Path(args.out_dir) if args.out_dir else store.dir / DEFAULT_CAPTURE_DIRNAME
    return PlaywrightBrowserClient(out_dir=out_dir)


def _run_capture_accounts_command(args: argparse.Namespace, store: Store) -> None:
    source = str(args.source or "").strip()
    if not source:
        raise ValueError("--source is required")
    browser = _account_browser_from_args(args, store)
    try:
        _capture, artifact_path = browser.capture_accounts(
            source=source,
            url=args.url or None,
            pages=args.pages,
            limit=args.limit,
        )
    finally:
        close = getattr(browser, "close", None)
        if callable(close):
            close()
    state = store.load()
    account_summary = import_account_capture(state, load_json_object(artifact_path))
    store.save(state)
    print(
        f"artifact={artifact_path} source={account_summary.source} "
        f"stored={account_summary.stored} updated={account_summary.updated} "
        f"qualified={account_summary.qualified} needs_review={account_summary.needs_review} "
        f"rejected={account_summary.rejected} total={account_summary.total}"
    )


def _account_browser_from_args(args: argparse.Namespace, store: Store) -> Any:
    from .account_browser import PlaywrightAccountCaptureClient

    out_dir = Path(args.out_dir) if args.out_dir else store.dir / DEFAULT_ACCOUNT_CAPTURE_DIRNAME
    return PlaywrightAccountCaptureClient(out_dir=out_dir)


def _run_serve_command(args: argparse.Namespace) -> None:
    import uvicorn

    from apps.review_ui.server import create_app
    from packages.linkedin_ui import LocalAccessToken

    host, port = _parse_addr(args.addr)
    token = args.access_token or LocalAccessToken.generate().token
    app = create_app(access_token=token)
    print(f"review_ui=http://{host}:{port}/recruiter-agency?access_token={token}")
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)


def _parse_addr(value: str) -> tuple[str, int]:
    if ":" not in value:
        raise ValueError("--addr must be HOST:PORT")
    host, raw_port = value.rsplit(":", 1)
    if not host:
        raise ValueError("--addr host is required")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("--addr port must be an integer") from exc
    return host, port


def _run_lead_command(args: argparse.Namespace, store: Store) -> None:
    if args.lead_command is None:
        raise ValueError("lead command is required")
    if args.lead_command == "show":
        state = store.load()
        detail = build_lead_detail(state, str(store.state_path), args.lead_id)
        if detail is None:
            raise ValueError(f"unknown lead id {args.lead_id!r}")
        if args.json:
            print(_json(detail))
            return
        print(render_lead_detail_text(detail))
        return
    raise ValueError(f"unsupported lead command {args.lead_command!r}")


def _parse_account_statuses(values: list[str]) -> list[str]:
    statuses: list[str] = []
    for value in values:
        for item in value.split(","):
            cleaned = item.strip()
            if not cleaned:
                continue
            statuses.append(AgencyAccountStatus(cleaned).value)
    return statuses


def _run_agency_pool_command(args: argparse.Namespace, store: Store) -> None:
    if args.agency_command is None:
        raise ValueError("agency-pool command is required")
    if args.agency_command == "build-source":
        capture = load_agency_source_csv(
            args.csv,
            source=args.source,
            source_type=args.source_type,
            url=args.url,
        )
        write_agency_source_capture(args.out, capture)
        if args.json:
            print(_json(capture))
        print(
            f"artifact={args.out} source={capture.source} "
            f"source_type={capture.source_type} rows={len(capture.rows)}"
        )
        return
    if args.agency_command == "import-source":
        state = store.load()
        source_summary = import_agency_source_capture(state, load_agency_source_capture(args.path))
        store.save(state)
        if args.json:
            print(_json(source_summary))
            return
        print(_promotion_or_import_summary(source_summary))
        return
    if args.agency_command == "import-directory":
        out = args.out or str(store.agency_source_artifact_path(args.source))
        capture = load_agency_source_csv(
            args.csv,
            source=args.source,
            source_type=args.source_type,
            url=args.url,
        )
        write_agency_source_capture(out, capture)
        state = store.load()
        directory_summary = import_agency_source_capture(state, capture)
        store.save(state)
        if args.json:
            print(_json({"artifact": out, "import": directory_summary}))
            return
        print(f"artifact={out} " + _promotion_or_import_summary(directory_summary))
        return
    if args.agency_command == "review-contact":
        state = store.load()
        candidate = review_agency_contact_candidate(
            state,
            candidate_id=args.candidate_id,
            review_status=AgencyContactReviewStatus(args.review_status),
            name=args.name,
            title=args.title,
            note=args.note,
        )
        store.save(state)
        print(f"candidate={candidate.id} review_status={candidate.review_status.value}")
        return
    if args.agency_command in {"promote-contact", "promote-contacts"}:
        state = store.load()
        ids = [args.candidate_id] if args.agency_command == "promote-contact" else []
        promotion_summary = promote_agency_contact_candidates(
            state,
            candidate_ids=ids,
            limit=getattr(args, "limit", 0),
            draft=args.draft,
            allow_multiple_per_agency=getattr(args, "allow_multiple_per_agency", False),
        )
        store.save(state)
        print(
            f"stored={promotion_summary.stored} updated={promotion_summary.updated} "
            f"drafted={promotion_summary.drafted} skipped={len(promotion_summary.skipped)}"
        )
        for skipped in promotion_summary.skipped:
            print(f"skipped={skipped.candidate_id} reason={skipped.reason}")
        return
    if args.agency_command == "contacts":
        state = store.load()
        candidates = [
            candidate
            for candidate in state.agency_contact_candidates
            if (not args.status or candidate.status.value == args.status)
            and (not args.review_status or candidate.review_status.value == args.review_status)
        ]
        candidates.sort(key=lambda candidate: (candidate.agency_account_name, candidate.id))
        if args.limit > 0:
            candidates = candidates[: args.limit]
        if args.json:
            print(_json([candidate.to_dict() for candidate in candidates]))
            return
        print(f"agency_contact_candidates={len(candidates)}")
        for candidate in candidates:
            print(
                f"{candidate.id}\t{candidate.review_status.value}\t{candidate.status.value}\t"
                f"{candidate.agency_account_name}\t{candidate.profile_url or '-'}\t"
                f"{candidate.sales_profile_urn or '-'}"
            )
        return
    if args.agency_command == "next":
        state = store.load()
        next_action = build_agency_pool_next_action(state, str(store.state_path))
        print(_json(next_action) if args.json else render_agency_pool_next_action_text(next_action))
        return
    if args.agency_command == "diagnose":
        state = store.load()
        diagnosis = build_agency_pool_diagnosis(state, str(store.state_path))
        print(_json(diagnosis))
        return
    raise ValueError(f"unsupported agency-pool command {args.agency_command!r}")


def _promotion_or_import_summary(summary: Any) -> str:
    return (
        f"source={summary.source} stored={summary.stored} updated={summary.updated} "
        f"qualified={summary.qualified} needs_review={summary.needs_review} "
        f"rejected={summary.rejected} "
        f"contact_candidates_stored={summary.contact_candidates_stored} "
        f"contact_candidates_updated={summary.contact_candidates_updated} "
        f"total_accounts={summary.total_accounts}"
    )


def _json(value: Any) -> str:
    return json.dumps(_to_jsonable(value), indent=2, sort_keys=True)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


if __name__ == "__main__":
    raise SystemExit(main())
