from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from apps.review_ui import create_app
from packages.linkedin_ui import ActionResult, ReviewAction, list_review_actions

TOKEN = "test-local-token"


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


def client() -> TestClient:
    return TestClient(create_app(access_token=TOKEN))


def test_review_pages_render_required_surfaces() -> None:
    test_client = client()

    overview = test_client.get("/")
    opportunities = test_client.get("/opportunities")
    network = test_client.get("/network")
    recruiter = test_client.get("/recruiter-agency")
    browser = test_client.get("/browser")

    assert overview.status_code == 200
    assert "Cross-System Review" in overview.text
    assert "Source Registry" in opportunities.text
    assert "Ranked Comments" in opportunities.text
    assert "Experiment Report" in opportunities.text
    assert "Calibration Queue" in opportunities.text
    assert "Current Run Status" in network.text
    assert "Lead Queue, Drafts, And Messageability" in recruiter.text
    assert "Latest Playwright Artifacts And Failed Actions" in browser.text


def test_opportunity_pages_exclude_real_action_controls() -> None:
    response = client().get("/opportunities")

    assert response.status_code == 200
    assert "--allow-send" not in response.text
    assert "--allow-withdraw" not in response.text
    assert "send-message" not in response.text
    assert "send-guarded" not in response.text
    assert "Connect" not in response.text
    assert "Withdraw" not in response.text


def test_state_changing_opportunity_label_requires_token() -> None:
    test_client = client()

    blocked = test_client.post(
        "/opportunities/comments/comment-hot-1/label",
        data={"label": "qualified"},
    )
    allowed = test_client.post(
        "/opportunities/comments/comment-hot-1/label",
        data={"label": "qualified", "access_token": TOKEN},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert "No durable mutation was written" in allowed.text


def test_guarded_actions_page_requires_token() -> None:
    test_client = client()

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


def test_real_action_routes_delegate_to_action_service_only_with_token() -> None:
    service = RecordingActionService()
    test_client = TestClient(create_app(access_token=TOKEN, action_service=service))

    blocked = test_client.post("/actions/network-send-ready-followup")
    allowed = test_client.post(
        "/actions/network-send-ready-followup",
        data={"access_token": TOKEN},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 200
    assert service.seen == ["network-send-ready-followup"]
    assert "linkedin-tools network acceptance send-ready-followups" in allowed.text


def test_alpine_state_is_presentational_and_htmx_targets_server_routes() -> None:
    response = client().get(f"/opportunities?access_token={TOKEN}")

    assert response.status_code == 200
    assert 'x-data="{ tab: ' in response.text
    assert 'hx-post="/opportunities/comments/comment-hot-1/label"' in response.text
    assert 'name="access_token"' in response.text
