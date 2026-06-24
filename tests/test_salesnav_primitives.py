from __future__ import annotations

from pathlib import Path

import pytest

from packages.linkedin_browser import RealAction, RealActionApproval, UnsafeRealActionError
from packages.linkedin_salesnav import (
    CandidateIdentity,
    MessageActionCandidate,
    guarded_connection_request,
    guarded_message_click,
    guarded_withdraw_invitation,
    load_audit_artifact,
    load_capture_artifact,
    sales_profile_id_from_url,
    sales_profile_urn_to_lead_url,
    validate_message_action_candidate,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "browser"


def test_capture_artifact_parser_preserves_rows() -> None:
    artifact = load_capture_artifact(FIXTURE_ROOT / "salesnav_capture_page.json")

    assert artifact.source == "Network - test"
    assert artifact.output_row_count == 1
    row = artifact.rows[0]
    assert row.name == "Ada Lovelace"
    assert row.menu_state == "connectable"
    assert row.visible_has_message is True
    assert row.menu_labels[0].text == "Connect"


def test_audit_artifact_parser_preserves_sent_count() -> None:
    audit = load_audit_artifact(FIXTURE_ROOT / "salesnav_audit.json")

    assert audit.people_count == 1001
    assert audit.recent_names == ("Ada Lovelace", "Grace Hopper")


def test_sales_profile_helpers_parse_url_and_urn() -> None:
    url = "https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token"
    urn = "urn:li:fs_salesProfile:(abc,NAME_SEARCH,token)"

    assert sales_profile_id_from_url(url) == "abc"
    assert sales_profile_urn_to_lead_url(urn) == url


def test_message_action_requires_candidate_identity_match() -> None:
    candidate = CandidateIdentity(
        name="Ada Lovelace",
        profile_url="https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token",
    )
    action = MessageActionCandidate(
        kind="message",
        action_label="Message",
        identity_label="Grace Hopper",
        source="search-row",
    )

    result = validate_message_action_candidate(candidate, action)

    assert result.status == "message-action-candidate-mismatch"


def test_profile_more_menu_message_path_requires_opened_url() -> None:
    with pytest.raises(ValueError, match="opened_page_url"):
        MessageActionCandidate(
            kind="inmail",
            action_label="InMail Ada Lovelace",
            identity_label="Ada Lovelace",
            source="profile-more-menu",
            used_profile_more_menu=True,
        )


@pytest.mark.asyncio
async def test_guarded_message_click_dry_run_does_not_click() -> None:
    clicked = False
    candidate = CandidateIdentity(
        name="Ada Lovelace",
        profile_url="https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token",
    )
    action = MessageActionCandidate(
        kind="message",
        action_label="Message Ada Lovelace",
        identity_label="Ada Lovelace",
        source="search-row",
    )

    async def click() -> None:
        nonlocal clicked
        clicked = True

    result = await guarded_message_click(candidate, action, click)

    assert result.status == "dry-run-send-message"
    assert clicked is False


@pytest.mark.asyncio
async def test_guarded_salesnav_actions_require_matching_approval() -> None:
    candidate = CandidateIdentity(
        name="Ada Lovelace",
        profile_url="https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token",
    )

    async def click() -> None:
        return None

    with pytest.raises(UnsafeRealActionError):
        await guarded_connection_request(candidate, click, dry_run=False)

    message = await guarded_message_click(
        candidate,
        MessageActionCandidate(
            kind="message",
            action_label="Message Ada Lovelace",
            identity_label="Ada Lovelace",
            source="search-row",
        ),
        click,
        dry_run=False,
        approval=RealActionApproval(RealAction.SEND_MESSAGE, allow=True),
    )
    withdrawal = await guarded_withdraw_invitation(
        candidate,
        click,
        dry_run=False,
        approval=RealActionApproval(RealAction.WITHDRAW_INVITATION, allow=True),
    )

    assert message.guard is not None
    assert message.guard.clicked is True
    assert withdrawal.clicked is True
