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
