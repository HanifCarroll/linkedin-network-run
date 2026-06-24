"""CLI namespace for the recruiter/agency outreach port."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from .daily import DailyOptions, run_daily
from .dashboard import (
    build_agency_pool_diagnosis,
    build_agency_pool_next_action,
    build_dashboard_report,
    render_agency_pool_next_action_text,
    render_dashboard_markdown,
    write_dashboard_markdown,
)
from .drafts import draft_messages, write_draft_markdown
from .models import AgencyContactReviewStatus, MessageStatus
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

    import_capture_parser = subparsers.add_parser("import-capture")
    import_capture_parser.add_argument("path")
    import_capture_parser.add_argument("--only-connectable", action="store_true")

    import_accounts_parser = subparsers.add_parser("import-accounts")
    import_accounts_parser.add_argument("path")

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
        parser.exit(1, f"error: {exc}\n")
    return 0


def _run_command(args: argparse.Namespace, store: Store) -> None:
    if args.command == "run-daily":
        result = run_daily(
            store,
            DailyOptions(
                session=args.session,
                target_agencies=args.target_agencies,
                target_recruiters=args.target_recruiters,
                allow_send=args.allow_send,
                print_markdown=args.print_markdown,
            ),
        )
        print(f"dashboard={result.dashboard_path}")
        if args.print_markdown:
            print(result.markdown)
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
                lead.message_status = MessageStatus(args.status)
                store.save(state)
                print(f"lead={lead.id} message_status={lead.message_status.value}")
                return
        raise ValueError(f"unknown lead id {args.lead_id!r}")
    if args.command == "agency-pool":
        _run_agency_pool_command(args, store)
        return
    raise ValueError(f"unsupported command {args.command!r}")


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
