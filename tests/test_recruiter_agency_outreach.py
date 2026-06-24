from __future__ import annotations

from pathlib import Path

import pytest

from apps.recruiter_agency_outreach.cli import build_parser, main
from apps.recruiter_agency_outreach.daily import DailyOptions, daily_buckets, run_daily
from apps.recruiter_agency_outreach.dashboard import (
    build_agency_pool_diagnosis,
    build_agency_pool_next_action,
    build_dashboard_report,
    ready_leads,
)
from apps.recruiter_agency_outreach.drafts import draft_messages
from apps.recruiter_agency_outreach.models import (
    AgencyAccount,
    AgencyAccountStatus,
    AgencyContactCandidate,
    AgencyContactCandidateStatus,
    AgencyContactReviewStatus,
    Lead,
    LeadStatus,
    LeadType,
    MessageDraft,
    MessageStatus,
    OutreachState,
)
from apps.recruiter_agency_outreach.send import SendMessageOptions, send_message
from apps.recruiter_agency_outreach.sourcing import (
    import_account_capture,
    import_agency_source_capture,
    import_salesnav_capture,
    load_agency_source_csv,
    promote_agency_contact_candidates,
    review_agency_contact_candidate,
    validate_agency_source_capture,
)
from apps.recruiter_agency_outreach.storage import Store

FIXTURES = Path(__file__).parent / "fixtures" / "recruiter_agency_outreach"


def test_import_capture_classifies_contract_recruiter_and_drafts() -> None:
    state = OutreachState()
    capture = {
        "source": "ASAP - Contract Recruiters Staffing",
        "rows": [
            {
                "index": 0,
                "name": "Riley Recruiter",
                "text": (
                    "Riley Recruiter\nSenior Technical Recruiter\n"
                    "Acme Staffing\nContract React TypeScript roles"
                ),
                "profileUrl": "https://www.linkedin.com/sales/lead/abc?_ntb=x",
                "menuState": "connectable",
            }
        ],
    }

    summary = import_salesnav_capture(state, capture)
    assert summary.eligible == 1
    assert len(state.leads) == 1
    lead = state.leads[0]
    assert lead.lead_type == LeadType.CONTRACT_RECRUITER
    assert lead.status == LeadStatus.ELIGIBLE
    assert lead.title == "Senior Technical Recruiter"

    report = draft_messages(state, 10)
    assert len(report.items) == 1
    assert state.leads[0].message_status == MessageStatus.DRAFTED
    assert state.leads[0].draft is not None
    assert "I saw that you recruit for contract technical roles" in state.leads[0].draft.body
    assert "Are you the right person to ask about contract roles" in state.leads[0].draft.body


def test_agency_source_promotion_requires_salesnav_identity() -> None:
    capture = load_agency_source_csv(
        FIXTURES / "agency_directory.csv",
        source="Reviewed agency directory",
        source_type="manual_directory",
    )
    assert validate_agency_source_capture(capture) == []

    state = OutreachState()
    summary = import_agency_source_capture(state, capture)
    assert summary.contact_candidates_stored == 2
    assert len(state.leads) == 0

    for candidate in state.agency_contact_candidates:
        review_agency_contact_candidate(
            state,
            candidate_id=candidate.id,
            review_status=AgencyContactReviewStatus.APPROVED,
        )

    promoted = promote_agency_contact_candidates(
        state,
        limit=10,
        draft=True,
        allow_multiple_per_agency=True,
    )
    assert promoted.stored == 1
    assert promoted.drafted == 1
    assert len(promoted.skipped) == 1
    assert promoted.skipped[0].reason == "resolve_agency_contact_salesnav_identity"

    lead = promoted.leads[0]
    assert lead.profile_url == "https://www.linkedin.com/sales/lead/ACwAA123ABC,NAME_SEARCH,abcdef"
    assert lead.sales_profile_urn == "urn:li:fs_salesProfile:(ACwAA123ABC,NAME_SEARCH,abcdef)"
    assert (
        "Public LinkedIn profile: https://www.linkedin.com/in/lorenzo-fernandez-297017b/"
        in lead.evidence_text
    )
    assert lead.draft is not None


def test_import_account_capture_qualifies_agency_accounts() -> None:
    state = OutreachState()
    summary = import_account_capture(
        state,
        {
            "source": "ASAP - Agency Accounts Product Studio",
            "rows": [
                {
                    "name": "Bright Product Studio",
                    "text": "Software Development custom software AI MVP product launches",
                    "accountUrl": "https://www.linkedin.com/sales/company/12345?_ntb=x",
                    "website": "https://bright.example.com",
                    "industry": "Software Development",
                }
            ],
        },
    )

    assert summary.qualified == 1
    assert len(state.agency_accounts) == 1
    account = state.agency_accounts[0]
    assert account.status == AgencyAccountStatus.QUALIFIED
    assert account.account_url == "https://www.linkedin.com/sales/company/12345"
    assert account.domain == "bright.example.com"


def test_next_action_surfaces_salesnav_identity_resolution() -> None:
    state = OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_bright",
                source="manual",
                name="Bright Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=90,
            )
        ],
        agency_contact_candidates=[
            AgencyContactCandidate(
                id="agc_jane",
                agency_account_id="acct_bright",
                agency_account_name="Bright Studio",
                source="website_enrichment",
                status=AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE,
                review_status=AgencyContactReviewStatus.APPROVED,
                name="Jane Doe",
                title="Founder",
                profile_url="https://www.linkedin.com/in/jane-doe/",
            )
        ],
    )

    next_action = build_agency_pool_next_action(state, "/tmp/outreach.sqlite")
    assert next_action.action == "resolve_agency_contact_salesnav_identity"
    assert next_action.candidate is not None
    assert next_action.candidate.profile_url == "https://www.linkedin.com/in/jane-doe/"


def test_missing_linkedin_company_url_blocks_account_scoped_search_first() -> None:
    state = OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_missing",
                source="manual",
                name="Missing Company URL Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=92,
                website="https://missing.example.com",
            )
        ]
    )

    diagnosis = build_agency_pool_diagnosis(state, "/tmp/outreach.sqlite")
    assert diagnosis.missing_linkedin_company_url == 1
    assert diagnosis.accounts[0].next_step == "missing_linkedin_company_url"
    next_action = build_agency_pool_next_action(state, "/tmp/outreach.sqlite")
    assert next_action.action == "missing_linkedin_company_url"


def test_guarded_send_flow_requires_dry_run_ready_and_updates_dashboard(tmp_path: Path) -> None:
    store = Store(tmp_path)
    state = OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_bright",
                source="manual",
                name="Bright Product Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=95,
            )
        ],
        leads=[
            Lead(
                id="lead_fixture",
                source="Agency website contact - Bright Product Studio",
                name="Dana Delivery",
                first_name="Dana",
                lead_type=LeadType.AGENCY_DELIVERY,
                status=LeadStatus.ELIGIBLE,
                message_status=MessageStatus.DRAFTED,
                fit_score=95,
                profile_url="https://www.linkedin.com/sales/lead/dana",
                agency_account_id="acct_bright",
                agency_account_name="Bright Product Studio",
                draft=MessageDraft(subject="Subject", body="Body", angle="agency"),
            )
        ],
    )
    store.save(state)

    with pytest.raises(ValueError, match="real sends require dry_run_ready"):
        send_message(
            store,
            SendMessageOptions(
                lead_id="lead_fixture",
                session="auto",
                allow_send=True,
                result_path=str(FIXTURES / "sent_clicked.json"),
            ),
        )

    with pytest.raises(ValueError, match="real send result requires --allow-send"):
        send_message(
            store,
            SendMessageOptions(
                lead_id="lead_fixture",
                session="auto",
                result_path=str(FIXTURES / "sent_clicked.json"),
            ),
        )

    send_message(
        store,
        SendMessageOptions(
            lead_id="lead_fixture",
            session="auto",
            result_path=str(FIXTURES / "dry_run_messageable.json"),
        ),
    )
    loaded = store.load()
    assert loaded.leads[0].message_status == MessageStatus.DRY_RUN_READY
    assert len(ready_leads(loaded, "agency")) == 1

    report = build_dashboard_report(loaded, str(store.state_path), allow_send=True)
    assert report.ready_counts.agencies == 1

    send_message(
        store,
        SendMessageOptions(
            lead_id="lead_fixture",
            session="auto",
            allow_send=True,
            result_path=str(FIXTURES / "sent_clicked.json"),
        ),
    )
    sent = store.load()
    assert sent.leads[0].message_status == MessageStatus.SENT
    assert len(sent.leads[0].send_attempts) == 2


def test_run_daily_is_no_send_and_agency_bucket_is_account_first(tmp_path: Path) -> None:
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="run-daily is sourcing-only"):
        run_daily(store, DailyOptions(session="auto", allow_send=True))

    buckets = daily_buckets(target_agencies=5, target_recruiters=5)
    assert buckets[0] == ("agency", [], 5)
    assert buckets[1][0] == "recruiter"


def test_cli_namespace_is_wired(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["--state-dir", str(tmp_path), "dashboard", "--print-markdown"])
    assert args.command == "dashboard"
    assert main(["--state-dir", str(tmp_path), "run-daily", "--session", "auto"]) == 0


def test_cli_state_parity_commands_update_and_render(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRAFTED))
    body_file = tmp_path / "revision.txt"
    body_file.write_text("Revised message body")

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "revise",
                "--lead-id",
                "lead_fixture",
                "--body-file",
                str(body_file),
                "--subject",
                "Updated subject",
            ]
        )
        == 0
    )
    revised_draft = store.load().leads[0].draft
    assert revised_draft is not None
    assert revised_draft.body == "Revised message body"

    assert main(["--state-dir", str(tmp_path), "lead", "show", "--lead-id", "lead_fixture"]) == 0
    assert "lead=lead_fixture" in capsys.readouterr().out

    assert main(["--state-dir", str(tmp_path), "queue", "--lead-id", "lead_fixture"]) == 0
    assert "lead_fixture" in capsys.readouterr().out

    assert main(["--state-dir", str(tmp_path), "accounts"]) == 0
    assert "Bright Product Studio" in capsys.readouterr().out

    assert main(["--state-dir", str(tmp_path), "report"]) == 0
    assert "by message status:" in capsys.readouterr().out

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "mark-message",
                "--lead-id",
                "lead_fixture",
                "--status",
                "needs_edit",
                "--note",
                "manual review",
            ]
        )
        == 0
    )
    marked = store.load().leads[0]
    assert marked.message_status == MessageStatus.NEEDS_EDIT
    assert "manual review" in marked.notes

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "reject",
                "--lead-id",
                "lead_fixture",
                "--reason",
                "not a fit",
            ]
        )
        == 0
    )
    rejected = store.load().leads[0]
    assert rejected.status == LeadStatus.REJECTED
    assert "not a fit" in rejected.reject_reasons


def test_send_ready_applies_structured_results_and_records_last_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRY_RUN_READY))
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "lead_fixture.json").write_text((FIXTURES / "sent_clicked.json").read_text())

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "send-ready",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
                "--allow-send",
                "--result-dir",
                str(result_dir),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "sent=1 agencies,0 recruiters" in output
    sent = store.load()
    assert sent.leads[0].message_status == MessageStatus.SENT
    assert [event.phase for event in sent.run_events] == [
        "run-start",
        "send-message",
        "run-finish",
    ]

    assert main(["--state-dir", str(tmp_path), "last-run"]) == 0
    assert "status=completed" in capsys.readouterr().out

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "recommend-next-run",
                "--target-recruiters",
                "0",
                "--allow-send",
            ]
        )
        == 0
    )
    assert "no retry is needed" in capsys.readouterr().out


def test_send_ready_requires_real_send_result_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRY_RUN_READY))
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "lead_fixture.json").write_text(
        (FIXTURES / "dry_run_messageable.json").read_text()
    )

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "send-ready",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
                "--allow-send",
                "--result-dir",
                str(result_dir),
            ]
        )
        == 1
    )
    assert "dry_run=true" in capsys.readouterr().err
    assert store.load().run_events[-1].result == "failed"


def test_live_capture_commands_report_browser_gap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "capture",
                "--session",
                "auto",
                "--source",
                "ASAP - Contract Recruiter Titles",
            ]
        )
        == 1
    )
    assert "live Sales Navigator capture is not wired" in capsys.readouterr().err


def _sendable_state(*, message_status: MessageStatus) -> OutreachState:
    return OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_bright",
                source="manual",
                name="Bright Product Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=95,
            )
        ],
        leads=[
            Lead(
                id="lead_fixture",
                source="Agency website contact - Bright Product Studio",
                name="Dana Delivery",
                first_name="Dana",
                lead_type=LeadType.AGENCY_DELIVERY,
                status=LeadStatus.ELIGIBLE,
                message_status=message_status,
                fit_score=95,
                profile_url="https://www.linkedin.com/sales/lead/dana",
                agency_account_id="acct_bright",
                agency_account_name="Bright Product Studio",
                draft=MessageDraft(subject="Subject", body="Body", angle="agency"),
            )
        ],
    )
