from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

from apps.comment_extractor.contracts import PostHTMLInput
from apps.comment_extractor.linkedin_post_comments import (
    extract_comments_from_html_file,
    write_raw_comments_jsonl,
)
from apps.network_automation.models import (
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    CandidateEvent,
    CandidateObservation,
    CandidateStatus,
    PendingCandidateObservation,
    new_pending_cleanup_run,
    new_run,
    now_utc,
)
from apps.network_automation.store import Store as NetworkStore
from apps.opportunity_intel.sources import load_query_pack, load_source_registry
from apps.opportunity_intel.store import OpportunityStore, stable_comment_key
from apps.recruiter_agency_outreach.models import (
    AgencyAccount,
    AgencyAccountStatus,
    Lead,
    LeadStatus,
    LeadType,
    MessageDraft,
    MessageStatus,
    OutreachState,
    RunEvent,
)
from apps.recruiter_agency_outreach.storage import Store as RecruiterStore
from apps.review_ui import create_app
from packages.linkedin_ui import ActionResult, ReviewAction, list_review_actions

TOKEN = "test-local-token"
FIXTURE_DIR = Path("tests/fixtures/opportunity_intel")


@dataclass
class RecordingActionService:
    seen: list[str] = field(default_factory=list)

    def execute(self, action: ReviewAction) -> ActionResult:
        self.seen.append(action.id)
        return ActionResult(
            action_id=action.id,
            status="recorded",
            command=action.guarded_command.argv,
            message="recorded by test service",
        )


def client(tmp_path: Path) -> tuple[TestClient, OpportunityStore, str]:
    store, comment_id = _seed_opportunity_store(tmp_path)
    return (
        TestClient(
            create_app(
                access_token=TOKEN,
                opportunity_store=store,
                network_store=NetworkStore(tmp_path / "network"),
                recruiter_store=RecruiterStore(tmp_path / "recruiter"),
            )
        ),
        store,
        comment_id,
    )


def test_review_pages_render_required_surfaces(tmp_path: Path) -> None:
    test_client, _, _ = client(tmp_path)

    overview = test_client.get("/")
    opportunities = test_client.get("/opportunities")
    network = test_client.get("/network")
    recruiter = test_client.get("/recruiter-agency")
    browser = test_client.get("/browser")
    dashboard_alias = test_client.get("/dashboard")

    assert overview.status_code == 200
    assert dashboard_alias.status_code == 200
    assert "Cross-System Review" in overview.text
    assert "Source Registry" in opportunities.text
    assert "Ranked Comments" in opportunities.text
    assert "Experiment Report" in opportunities.text
    assert "Calibration Queue" in opportunities.text
    assert "Current Run Status" in network.text
    assert "Lead Queue, Drafts, And Messageability" in recruiter.text
    assert "Latest Playwright Artifacts And Failed Actions" in browser.text


def test_review_ui_reads_network_and_recruiter_state(tmp_path: Path) -> None:
    opportunity_store, _ = _seed_opportunity_store(tmp_path)
    network_store = _seed_network_store(tmp_path / "network")
    recruiter_store = _seed_recruiter_store(tmp_path / "recruiter")
    test_client = TestClient(
        create_app(
            access_token=TOKEN,
            opportunity_store=opportunity_store,
            network_store=network_store,
            recruiter_store=recruiter_store,
        )
    )

    overview = test_client.get("/")
    network = test_client.get("/network")
    recruiter = test_client.get("/recruiter-agency")

    assert "Stubbed read models" not in overview.text
    assert "Live read models" in overview.text
    assert "Sent Founder" in network.text
    assert "Queued CTO" in network.text
    assert "Hi Accepted" in network.text
    assert "Old Invite" in network.text
    assert "Thread 4 read model pending" not in network.text
    assert "Acme Talent" in recruiter.text
    assert "Riley Recruiter" in recruiter.text
    assert "Jordan Agency" in recruiter.text
    assert "Hi Riley" in recruiter.text
    assert "Hi Jordan" in recruiter.text
    assert "Thread 5 read model pending" not in recruiter.text


def test_opportunity_page_uses_registry_when_sqlite_is_empty(tmp_path: Path) -> None:
    store = OpportunityStore(tmp_path / "empty-opportunity-intel")
    first_source = load_source_registry().sources[0]
    test_client = TestClient(
        create_app(
            access_token=TOKEN,
            opportunity_store=store,
            network_store=NetworkStore(tmp_path / "network"),
            recruiter_store=RecruiterStore(tmp_path / "recruiter"),
        )
    )

    response = test_client.get("/opportunities")

    assert response.status_code == 200
    assert first_source.source_id in response.text
    assert "not extracted yet" in response.text


def test_opportunity_pages_exclude_real_action_controls(tmp_path: Path) -> None:
    test_client, _, _ = client(tmp_path)
    response = test_client.get("/opportunities")

    assert response.status_code == 200
    assert "--allow-send" not in response.text
    assert "--allow-withdraw" not in response.text
    assert "send-message" not in response.text
    assert "send-guarded" not in response.text
    assert "Connect" not in response.text
    assert "Withdraw" not in response.text


def test_state_changing_opportunity_label_requires_token(tmp_path: Path) -> None:
    test_client, store, comment_id = client(tmp_path)

    blocked = test_client.post(
        f"/opportunities/comments/{comment_id}/label",
        data={"label": "strong"},
    )
    allowed = test_client.post(
        f"/opportunities/comments/{comment_id}/label",
        data={"label": "ready for outreach", "access_token": TOKEN, "notes": "follow up"},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert "marked ready for outreach" in allowed.text
    rows = store.fetch_all(
        "SELECT label, notes FROM review_labels WHERE comment_key = ?",
        (comment_id,),
    )
    assert rows[0]["label"] == "ready for outreach"
    assert rows[0]["notes"] == "follow up"


def test_guarded_actions_page_requires_token(tmp_path: Path) -> None:
    test_client, _, _ = client(tmp_path)

    blocked = test_client.get("/actions")
    allowed = test_client.get(f"/actions?access_token={TOKEN}")

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert "--allow-send" in allowed.text
    assert "--allow-withdraw" in allowed.text


def test_real_action_registry_uses_guarded_cli_flags() -> None:
    actions = list_review_actions()

    assert actions
    for action in actions:
        assert action.is_real_action_guarded()
        assert action.guarded_command.approval_flag in action.guarded_command.argv
        assert action.guarded_command.argv[0] == "linkedin-tools"


def test_real_action_routes_delegate_to_action_service_only_with_token(tmp_path: Path) -> None:
    service = RecordingActionService()
    test_client = TestClient(
        create_app(
            access_token=TOKEN,
            action_service=service,
            opportunity_store=OpportunityStore(tmp_path / "opportunity-intel"),
        )
    )

    blocked = test_client.post("/actions/network-send-ready-followup")
    allowed = test_client.post(
        "/actions/network-send-ready-followup",
        data={"access_token": TOKEN},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert service.seen == ["network-send-ready-followup"]
    assert "linkedin-tools network acceptance send-ready-followups" in allowed.text


def test_alpine_state_is_presentational_and_htmx_targets_server_routes(tmp_path: Path) -> None:
    test_client, _, comment_id = client(tmp_path)
    response = test_client.get(f"/opportunities?access_token={TOKEN}")

    assert response.status_code == 200
    assert 'x-data="{ tab: ' in response.text
    assert f'hx-post="/opportunities/comments/{comment_id}/label"' in response.text
    assert 'name="access_token"' in response.text


def _seed_network_store(path: Path) -> NetworkStore:
    store = NetworkStore(path)
    run = new_run(target=2)
    run.start_audit = 100
    run.latest_audit = 101
    run.candidates.append(
        CandidateEvent(
            source="ASAP - Startup CTO Eng Leaders",
            name="Sent Founder",
            profile_url="https://www.linkedin.com/sales/lead/sent-founder",
            status=CandidateStatus.PENDING,
            note="verified send",
        )
    )
    run.observations.append(
        CandidateObservation(
            source="ASAP - Startup CTO Eng Leaders",
            index=2,
            name="Queued CTO",
            profile_url="https://www.linkedin.com/sales/lead/queued-cto",
            menu_state="connectable",
        )
    )
    store.save_run(run)
    accepted_at = now_utc()
    store.save_acceptance_followup_ledger(
        AcceptanceFollowupLedger(
            drafts=[
                AcceptanceFollowupRecord(
                    key="accepted-key",
                    id="afu_test",
                    source="ASAP - Startup CTO Eng Leaders",
                    name="Accepted Lead",
                    profile_url="https://www.linkedin.com/sales/lead/accepted-lead",
                    accepted_at=accepted_at,
                    angle="general",
                    draft="Hi Accepted - saw the product work and wanted to compare notes.",
                    status=AcceptanceFollowupStatus.DRAFTED,
                    report_path="/tmp/accepted.md",
                )
            ]
        )
    )
    pending = new_pending_cleanup_run(max_withdrawals=5, threshold_days=14)
    pending.observations.append(
        PendingCandidateObservation(
            index=1,
            name="Old Invite",
            profile_url="https://www.linkedin.com/in/old-invite",
            age_text="3 weeks ago",
            age_days=21,
            eligible=True,
        )
    )
    store.save_pending(pending)
    return store


def _seed_recruiter_store(path: Path) -> RecruiterStore:
    store = RecruiterStore(path)
    state = OutreachState(
        leads=[
            Lead(
                id="lead_recruiter",
                source="ASAP - Contract Recruiter Titles",
                name="Riley Recruiter",
                first_name="Riley",
                lead_type=LeadType.CONTRACT_RECRUITER,
                status=LeadStatus.ELIGIBLE,
                message_status=MessageStatus.DRAFTED,
                fit_score=90,
                profile_url="https://www.linkedin.com/sales/lead/riley",
                title="Senior Technical Recruiter",
                company="Riley Recruiting",
                draft=MessageDraft(
                    subject="Product engineering support",
                    body="Hi Riley - I help teams cover product-engineering and AI-product work.",
                    angle="recruiter",
                ),
            ),
            Lead(
                id="lead_agency",
                source="ASAP - Agency Owners Delivery",
                name="Jordan Agency",
                first_name="Jordan",
                lead_type=LeadType.AGENCY_FOUNDER,
                status=LeadStatus.ELIGIBLE,
                message_status=MessageStatus.DRY_RUN_READY,
                fit_score=85,
                profile_url="https://www.linkedin.com/sales/lead/jordan",
                title="Founder",
                company="Acme Talent",
                agency_account_id="acct_acme",
                agency_account_name="Acme Talent",
                draft=MessageDraft(
                    subject="Extra product build capacity",
                    body="Hi Jordan - I can help when client product builds need senior execution.",
                    angle="agency",
                ),
            ),
        ],
        agency_accounts=[
            AgencyAccount(
                id="acct_acme",
                source="ASAP - Agency Owners Delivery",
                name="Acme Talent",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=88,
                account_url="https://www.linkedin.com/sales/company/acme",
            )
        ],
        run_events=[
            RunEvent(
                at="2026-06-24T12:00:00Z",
                phase="daily",
                run_id="daily-20260624",
                started_at="2026-06-24T12:00:00Z",
                result="completed",
            )
        ],
    )
    store.save(state)
    return store


def _seed_opportunity_store(tmp_path: Path) -> tuple[OpportunityStore, str]:
    store = OpportunityStore(tmp_path / "opportunity-intel")
    store.sync_source_registry(load_source_registry())
    result = extract_comments_from_html_file(
        PostHTMLInput(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:7350000000000000001/",
            html_path=FIXTURE_DIR / "linkedin_post_comments.html",
            source_id="known_high_signal_post_engagement",
            query_id="known_high_signal_post_engagement",
        )
    )
    run_id = store.start_extraction_run(
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:7350000000000000001/",
        source_id="known_high_signal_post_engagement",
        query_id="known_high_signal_post_engagement",
        source_kind="known_post",
        source_url="",
        search_query="",
        browser_profile="fixture",
        safety_limits={},
    )
    html_path = FIXTURE_DIR / "linkedin_post_comments.html"
    raw_path = write_raw_comments_jsonl(result.comments, tmp_path / "raw")
    store.record_artifact(run_id=run_id, kind="html", path=html_path)
    store.record_artifact(run_id=run_id, kind="raw_comments", path=raw_path)
    store.persist_comments(
        run_id=run_id,
        comments=result.comments,
        query_pack=load_query_pack(),
    )
    store.finish_extraction_run(
        run_id,
        status="extracted",
        comments_found=len(result.comments),
        failures=0,
        warning_count=len(result.warnings),
        retry_recommendation="No retry needed",
    )
    return store, stable_comment_key(result.comments[0])
