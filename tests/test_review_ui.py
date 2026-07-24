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
    assert "Latest Playwriter Artifacts And Failed Actions" in browser.text


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

    blocked = test_client.post("/actions/network-send-ready-greetings")
    allowed = test_client.post(
        "/actions/network-send-ready-greetings",
        data={"access_token": TOKEN},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert service.seen == ["network-send-ready-greetings"]
    assert "linkedin-tools network acceptance send-ready-greetings" in allowed.text


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
                    profile_url="https://www.linkedin.com/in/accepted-lead",
                    sales_nav_profile_url="https://www.linkedin.com/sales/lead/accepted-lead",
                    accepted_at=accepted_at,
                    draft="Hi Accepted - saw the product work and wanted to compare notes.",
                    status=AcceptanceFollowupStatus.DRAFTED,
                    report_path="/tmp/accepted.md",
                ),
                AcceptanceFollowupRecord(
                    key="excluded-key",
                    id="afu_excluded",
                    source="Network - Founder Operators (11-50)",
                    name="Excluded Lead",
                    profile_url="https://www.linkedin.com/in/excluded-lead",
                    sales_nav_profile_url="https://www.linkedin.com/sales/lead/excluded-lead",
                    accepted_at=accepted_at,
                    draft="Hi Excluded - older source draft.",
                    status=AcceptanceFollowupStatus.EXCLUDED,
                    report_path="/tmp/excluded.md",
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
