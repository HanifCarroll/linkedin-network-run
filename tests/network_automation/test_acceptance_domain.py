from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from apps.network_automation.models import (
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceFollowupStatus,
    AcceptanceInvitation,
    AcceptanceLedger,
    AcceptanceOutcomeArtifact,
    AcceptanceOutcomeEvent,
    AcceptanceReport,
    AcceptanceSourceReport,
    AcceptanceStatus,
    AcceptedDraftCandidate,
    AcceptedFollowupTemplateKey,
    CandidateEvent,
    CandidateStatus,
    DraftItem,
    DraftReport,
    acceptance_followup_diagnostics,
    acceptance_followup_result_note,
    acceptance_followup_status_for_result,
    advisor_accepted_followup_draft,
    agency_accepted_followup_draft,
    choose_angle,
    general_accepted_followup_draft,
    recruiter_accepted_followup_draft,
    render_draft_markdown,
)
from apps.network_automation.reports import render_acceptance_report

from .helpers import (
    _run_id,
)


def test_acceptance_report_marks_current_daily_reconciliation() -> None:
    report = AcceptanceReport(
        min_age_days=7,
        max_age_days=45,
        total_sent=1,
        checked=1,
        accepted=1,
        by_source={
            "ASAP - Agency Owners Delivery": AcceptanceSourceReport(
                total_sent=1,
                checked=1,
                accepted=1,
            )
        },
    )

    output = render_acceptance_report(report)

    assert "- Unchecked: 0" in output
    assert (
        "- Ledger freshness: Daily acceptance reconciliation appears current for this "
        "report window."
        in output
    )


def test_acceptance_report_routes_unchecked_candidates_to_daily_reconciliation() -> None:
    report = AcceptanceReport(
        min_age_days=7,
        max_age_days=45,
        total_sent=3,
        checked=1,
        accepted=1,
        unchecked=2,
        by_source={
            "ASAP - Agency Owners Delivery": AcceptanceSourceReport(
                total_sent=3,
                checked=1,
                accepted=1,
                unchecked=2,
            )
        },
    )

    output = render_acceptance_report(report)

    assert (
        "- Ledger freshness: Daily acceptance reconciliation should check 2 candidate(s) "
        "in this report window."
    ) in output


def test_acceptance_import_downgrades_message_only_acceptance() -> None:
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Weak Lead",
            profile_url="https://www.linkedin.com/sales/lead/weak-lead",
            status=CandidateStatus.PENDING,
        ),
    )
    artifact = AcceptanceOutcomeArtifact.model_validate(
        {
            "rows": [
                {
                    "source": "ASAP - Agency Owners Delivery",
                    "name": "Weak Lead",
                    "profileUrl": "https://www.linkedin.com/sales/lead/weak-lead",
                    "status": "accepted",
                    "checkedAt": "2026-07-02T12:00:00Z",
                    "relationship": "1st",
                    "evidence": '{"labels":["Weak Lead","Message"]}',
                    "note": "profile shows first-degree/message evidence",
                }
            ]
        }
    )

    summary = ledger.import_outcomes(artifact)

    assert summary.matched == 1
    assert ledger.invitations[0].latest_status == AcceptanceStatus.UNKNOWN
    assert "did not include first-degree relationship evidence" in (
        ledger.invitations[0].history[0].note or ""
    )


def test_acceptance_followup_candidates_prefer_public_profile_url() -> None:
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 7, 2),
        CandidateEvent(
            at=datetime(2026, 7, 2, tzinfo=UTC),
            source="ASAP - Agency Owners Delivery",
            name="Public Lead",
            profile_url="https://www.linkedin.com/sales/lead/public-lead,NAME_SEARCH,x",
            public_profile_url="https://www.linkedin.com/in/public-lead",
            status=CandidateStatus.ACCEPTED,
        ),
    )

    candidates = ledger.accepted_for_followup(AcceptanceFollowupLedger(), include_drafted=False)

    assert len(candidates) == 1
    assert candidates[0].profile_url == "https://www.linkedin.com/in/public-lead"
    assert (
        candidates[0].sales_nav_profile_url
        == "https://www.linkedin.com/sales/lead/public-lead,NAME_SEARCH,x"
    )


def test_acceptance_followup_candidates_skip_historical_message_only_acceptance() -> None:
    ledger = AcceptanceLedger(
        invitations=[
            AcceptanceInvitation(
                run_id=_run_id(),
                run_date=date(2026, 6, 24),
                source="ASAP - Agency Owners Delivery",
                name="Weak Lead",
                profile_url="https://www.linkedin.com/sales/lead/weak-lead",
                sent_at=datetime(2026, 6, 24, tzinfo=UTC),
                latest_status=AcceptanceStatus.ACCEPTED,
                latest_checked_at=datetime(2026, 7, 2, tzinfo=UTC),
                history=[
                    AcceptanceOutcomeEvent(
                        at=datetime(2026, 7, 2, tzinfo=UTC),
                        status=AcceptanceStatus.ACCEPTED,
                        relationship="1st",
                        note="profile shows first-degree/message evidence",
                    )
                ],
            )
        ]
    )

    assert ledger.accepted_for_followup(AcceptanceFollowupLedger(), include_drafted=False) == []


def test_acceptance_invalidates_historical_message_only_acceptance() -> None:
    invitation = AcceptanceInvitation(
        run_id=_run_id(),
        run_date=date(2026, 6, 24),
        source="ASAP - Agency Owners Delivery",
        name="Weak Lead",
        profile_url="https://www.linkedin.com/sales/lead/weak-lead",
        sent_at=datetime(2026, 6, 24, tzinfo=UTC),
        latest_status=AcceptanceStatus.ACCEPTED,
        latest_checked_at=datetime(2026, 7, 2, tzinfo=UTC),
        history=[
            AcceptanceOutcomeEvent(
                at=datetime(2026, 7, 2, tzinfo=UTC),
                status=AcceptanceStatus.ACCEPTED,
                relationship="1st",
                note="profile shows first-degree/message evidence",
            )
        ],
    )
    ledger = AcceptanceLedger(invitations=[invitation])
    followups = AcceptanceFollowupLedger(
        drafts=[
            AcceptanceFollowupRecord(
                key=invitation.key(),
                id="afu_weak",
                source=invitation.source,
                name=invitation.name,
                profile_url=invitation.profile_url,
                accepted_at=datetime(2026, 7, 2, tzinfo=UTC),
                angle="general",
                draft="Hey Weak. Thanks for connecting.",
                status=AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                report_path="followups.md",
            )
        ]
    )

    invalidated_keys = set(ledger.invalidate_weak_message_acceptances())
    followup_count = followups.invalidate_acceptance_keys(invalidated_keys)

    assert invalidated_keys == {invitation.key()}
    assert followup_count == 1
    assert ledger.invitations[0].latest_status == AcceptanceStatus.INVALIDATED
    assert ledger.invitations[0].history[-1].status == AcceptanceStatus.INVALIDATED
    assert followups.drafts[0].status == AcceptanceFollowupStatus.INVALID_ACCEPTANCE
    assert followups.drafts[0].terminal() is True
    assert "invalidated weak message-based acceptance" in followups.drafts[0].warnings[0]


def test_acceptance_followups_exclude_sources_outside_allowed_mix() -> None:
    def record(
        name: str, source: str, status: AcceptanceFollowupStatus
    ) -> AcceptanceFollowupRecord:
        return AcceptanceFollowupRecord(
            key=f"{source}:{name}",
            id=f"afu_{name}",
            source=source,
            name=name,
            profile_url=f"https://www.linkedin.com/sales/lead/{name}",
            accepted_at=datetime(2026, 7, 2, tzinfo=UTC),
            angle="general",
            draft=f"Hey, {name}. Thanks for connecting.",
            status=status,
            report_path="followups.md",
        )

    ledger = AcceptanceFollowupLedger(
        drafts=[
            record(
                "allowed",
                "ASAP - Agency Owners Delivery",
                AcceptanceFollowupStatus.DRY_RUN_READY,
            ),
            record(
                "old-ready",
                "Network - Founder Operators (11-50)",
                AcceptanceFollowupStatus.DRY_RUN_READY,
            ),
            record(
                "old-classified",
                "Network - Founder Operators (11-50)",
                AcceptanceFollowupStatus.NOT_MESSAGEABLE,
            ),
            record(
                "old-sent",
                "Network - Founder Operators (11-50)",
                AcceptanceFollowupStatus.SENT,
            ),
        ]
    )

    updated = ledger.exclude_sources_not_in({"ASAP - Agency Owners Delivery"})

    assert updated == 2
    assert ledger.drafts[0].status == AcceptanceFollowupStatus.DRY_RUN_READY
    assert ledger.drafts[1].status == AcceptanceFollowupStatus.EXCLUDED
    assert ledger.drafts[2].status == AcceptanceFollowupStatus.EXCLUDED
    assert ledger.drafts[3].status == AcceptanceFollowupStatus.SENT
    assert ledger.drafts[1].terminal() is True
    assert "not in current ASAP source mix" in ledger.drafts[1].warnings[0]


def test_acceptance_dry_run_selection_skips_already_classified_records() -> None:
    def record(name: str, status: AcceptanceFollowupStatus) -> AcceptanceFollowupRecord:
        return AcceptanceFollowupRecord(
            key=f"source:{name}",
            id=f"lead-{name}",
            source="source",
            name=name,
            profile_url=f"https://www.linkedin.com/sales/lead/{name}",
            accepted_at=datetime(2026, 6, 20, tzinfo=UTC),
            angle="general",
            draft=f"Hey {name}. Thanks for connecting.",
            status=status,
            report_path="followups.md",
        )

    ledger = AcceptanceFollowupLedger(
        drafts=[
            record("drafted", AcceptanceFollowupStatus.DRAFTED),
            record("not-messageable", AcceptanceFollowupStatus.NOT_MESSAGEABLE),
            record("blocked", AcceptanceFollowupStatus.BLOCKED),
            record("failed", AcceptanceFollowupStatus.SEND_FAILED),
            record("ready", AcceptanceFollowupStatus.DRY_RUN_READY),
            record("sent", AcceptanceFollowupStatus.SENT),
        ]
    )

    assert [record.name for record in ledger.needs_dry_run(0)] == ["drafted"]
    assert [record.name for record in ledger.needs_dry_run(0, retry_classified=True)] == [
        "drafted",
        "not-messageable",
        "blocked",
        "failed",
    ]


def test_acceptance_followup_not_messageable_preserves_visible_actions() -> None:
    result = AcceptanceFollowupSendResult.model_validate(
        {
            "dryRun": True,
            "url": "https://www.linkedin.com/sales/lead/abc",
            "messageLength": 128,
            "status": "not-messageable",
            "reason": "no visible Message or InMail action",
            "visibleActions": [
                {
                    "label": "Connect",
                    "ariaLabel": "Connect",
                    "disabled": False,
                    "tagName": "button",
                    "role": None,
                },
                {
                    "label": "Save",
                    "ariaLabel": "",
                    "disabled": False,
                    "tagName": "button",
                    "role": None,
                },
            ],
        }
    )

    diagnostics = acceptance_followup_diagnostics(result)
    note = acceptance_followup_result_note(result)

    assert diagnostics["visible_actions"] == (
        '[{"ariaLabel":"Connect","disabled":false,"label":"Connect",'
        '"role":null,"tagName":"button"},{"ariaLabel":"","disabled":false,'
        '"label":"Save","role":null,"tagName":"button"}]'
    )
    assert note is not None
    assert "no visible Message or InMail action" in note
    assert '"label":"Connect"' in note


def test_acceptance_followup_conversation_exists_is_terminal() -> None:
    result = AcceptanceFollowupSendResult.model_validate(
        {
            "dryRun": True,
            "url": "https://www.linkedin.com/in/existing-thread",
            "messageLength": 128,
            "status": "conversation-exists",
            "reason": "existing LinkedIn conversation history is visible",
            "conversationCheck": {
                "exists": True,
                "selector": ".msg-s-message-list__event",
                "visibleCount": 1,
            },
        }
    )

    assert acceptance_followup_status_for_result(result) == (
        AcceptanceFollowupStatus.CONVERSATION_EXISTS
    )
    diagnostics = acceptance_followup_diagnostics(result)
    note = acceptance_followup_result_note(result)
    assert diagnostics["conversation"] == (
        '{"exists":true,"selector":".msg-s-message-list__event","visibleCount":1}'
    )
    assert note is not None
    assert "existing LinkedIn conversation history is visible" in note


def test_acceptance_followup_composer_missing_preserves_message_container_diagnostics() -> None:
    result = AcceptanceFollowupSendResult.model_validate(
        {
            "dryRun": False,
            "url": "https://www.linkedin.com/in/missing-composer",
            "messageLength": 128,
            "status": "composer-missing",
            "messageContainers": {
                "targetName": "Missing Composer",
                "composerCount": 1,
                "containers": [
                    {
                        "textPreview": "New message Missing Composer",
                        "hasTargetName": True,
                        "actions": [{"label": "Send", "disabled": True}],
                    }
                ],
            },
        }
    )

    diagnostics = acceptance_followup_diagnostics(result)
    note = acceptance_followup_result_note(result)
    assert '"composerCount":1' in diagnostics["message_containers"]
    assert note is not None
    assert "message_containers" in note


def test_acceptance_draft_markdown_labels_public_and_sales_nav_profiles() -> None:
    candidate = AcceptedDraftCandidate(
        run_id=_run_id(),
        run_date=date(2026, 7, 2),
        source="Network - Founder Operators (11-50)",
        name="Accepted Lead",
        profile_url="https://www.linkedin.com/in/accepted-lead",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token",
        sent_at=datetime(2026, 7, 1, tzinfo=UTC),
        accepted_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    report = DraftReport(
        items=[
            DraftItem(
                candidate=candidate,
                angle="general",
                draft="Hey, Accepted. Thanks for connecting.",
                evidence=["Public web URL: https://au.linkedin.com/in/accepted-lead/"],
            )
        ]
    )

    rendered = render_draft_markdown(report)

    assert "- LinkedIn profile: https://www.linkedin.com/in/accepted-lead" in rendered
    assert (
        "- Sales Nav profile: https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token"
        in rendered
    )


def test_acceptance_followup_template_routing_is_source_first() -> None:
    assert choose_angle(
        "ASAP - Agency Owners Delivery", "AI Product Leader", "Acme AI"
    ) == (AcceptedFollowupTemplateKey.AGENCY, "project or overflow support ask for Acme AI")
    assert choose_angle(
        "ASAP - Contract Recruiters Staffing", "Founder", "Hiring Co"
    ) == (AcceptedFollowupTemplateKey.RECRUITER, "contract-role availability ask for Hiring Co")
    assert choose_angle(
        "ASAP - Strategy Consultants Implementation Partners",
        "Strategy Advisor",
        "Strategy Co",
    ) == (
        AcceptedFollowupTemplateKey.ADVISOR,
        "AI and workflow implementation support ask for Strategy Co",
    )
    assert choose_angle(
        "ASAP - AI Advisors Implementation Partners", "AI Advisor", "Old Strategy Co"
    ) == (
        AcceptedFollowupTemplateKey.ADVISOR,
        "AI and workflow implementation support ask for Old Strategy Co",
    )
    assert choose_angle(
        "ASAP - Vertical Proof Buyers", "Founder", "Proof Co"
    ) == (AcceptedFollowupTemplateKey.GENERAL, "product-engineering support ask for Proof Co")
    assert choose_angle(
        "Unknown List", "Talent Acquisition Partner", "Search Co"
    ) == (AcceptedFollowupTemplateKey.RECRUITER, "contract-role availability ask for Search Co")


def test_general_accepted_followup_uses_low_friction_relevant_cta() -> None:
    draft = general_accepted_followup_draft("Sam", "Acme AI")

    assert "Are you the right person to ask" in draft
    assert "would be useful at Acme AI?" in draft
    assert "HC Studio LLC" not in draft
    assert "resume" not in draft.lower()


def test_accepted_followup_templates_omit_hc_studio_and_frame_advisor_benefit() -> None:
    drafts = [
        general_accepted_followup_draft("Sam", "Acme AI"),
        agency_accepted_followup_draft("Jordan", "Acme Studio"),
        recruiter_accepted_followup_draft("Riley"),
        advisor_accepted_followup_draft("Morgan"),
    ]

    assert all("HC Studio LLC" not in draft for draft in drafts)
    agency = drafts[1]
    assert (
        "I'm a full-stack product engineer that works across web and mobile products."
        in agency
    )
    advisor = drafts[-1]
    assert "turn AI and workflow strategy into working systems" in advisor
    assert "automations, decision-support tools, integrations, and reporting" in advisor
    assert "make client implementation easier to deliver" in advisor
    assert "Would that be helpful for the type of strategy work you do?" in advisor
    assert "Are you the right person" not in advisor
