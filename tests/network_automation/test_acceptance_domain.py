from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from apps.network_automation.models import (
    AcceptanceDailyRun,
    AcceptanceEvidenceGrade,
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceFollowupStatus,
    AcceptanceInvitation,
    AcceptanceLedger,
    AcceptanceObservationPrecision,
    AcceptanceOutcomeArtifact,
    AcceptanceOutcomeEvent,
    AcceptanceRelationshipStatus,
    AcceptanceReport,
    AcceptanceSourceReport,
    AcceptanceStatus,
    CandidateEvent,
    CandidateStatus,
    acceptance_followup_diagnostics,
    acceptance_followup_result_note,
    acceptance_followup_status_for_result,
    accepted_welcome_message,
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
        "- Current enough: Daily acceptance reconciliation appears current for this "
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
        "- Current enough: Daily acceptance reconciliation should check 2 candidate(s) "
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


def test_acceptance_quarantine_preserves_only_source_grade_acceptances() -> None:
    observed_at = datetime(2026, 7, 2, tzinfo=UTC)
    legacy = AcceptanceInvitation(
        run_id=_run_id(),
        run_date=date(2026, 6, 24),
        source="source",
        name="Legacy Lead",
        profile_url="https://www.linkedin.com/sales/lead/legacy-lead",
        sent_at=datetime(2026, 6, 24, tzinfo=UTC),
        latest_status=AcceptanceStatus.ACCEPTED,
        latest_checked_at=observed_at,
        first_observed_accepted_at=observed_at,
        history=[
            AcceptanceOutcomeEvent(
                at=observed_at,
                status=AcceptanceStatus.ACCEPTED,
                relationship="1st",
                evidence="generic visible page text",
                note="lead page shows 1st-degree relationship",
            )
        ],
    )
    confirmed = legacy.model_copy(
        update={
            "name": "Controller Lead",
            "profile_url": "https://www.linkedin.com/sales/lead/controller-lead",
            "history": [
                AcceptanceOutcomeEvent(
                    at=observed_at,
                    status=AcceptanceStatus.ACCEPTED,
                    relationship="1st",
                    note="durably confirmed accepted during send",
                )
            ],
        },
        deep=True,
    )
    ledger = AcceptanceLedger(invitations=[legacy, confirmed])

    quarantined = ledger.quarantine_unverified_acceptances(at=observed_at)

    assert quarantined == [legacy.key()]
    assert legacy.latest_status == AcceptanceStatus.INVALIDATED
    assert legacy.acceptance_evidence_grade == AcceptanceEvidenceGrade.LEGACY_UNVERIFIED
    assert legacy.current_relationship_status == AcceptanceRelationshipStatus.UNKNOWN
    assert confirmed.latest_status == AcceptanceStatus.ACCEPTED
    assert (
        confirmed.acceptance_evidence_grade
        == AcceptanceEvidenceGrade.CONTROLLER_CONFIRMED
    )
    assert (
        confirmed.current_relationship_status
        == AcceptanceRelationshipStatus.FIRST_DEGREE
    )


def test_reconciliation_retries_legacy_row_when_required_top_card_was_unavailable() -> None:
    observed_at = datetime(2026, 7, 22, tzinfo=UTC)
    invitation = AcceptanceInvitation(
        run_id=_run_id(),
        run_date=date(2026, 6, 24),
        source="source",
        name="Retry Lead",
        profile_url="https://www.linkedin.com/sales/lead/retry-lead",
        sent_at=datetime(2026, 6, 24, tzinfo=UTC),
        latest_status=AcceptanceStatus.UNKNOWN,
        latest_checked_at=observed_at,
        acceptance_evidence_grade=AcceptanceEvidenceGrade.LEGACY_UNVERIFIED,
        current_relationship_status=AcceptanceRelationshipStatus.UNKNOWN,
        current_relationship_observed_at=observed_at,
        history=[
            AcceptanceOutcomeEvent(
                at=observed_at,
                status=AcceptanceStatus.UNKNOWN,
                evidence=(
                    '{"contractVersion":"acceptance-relationship-v2",'
                    '"topCardCount":0}'
                ),
                contract_version="acceptance-relationship-v2",
                note="no definitive acceptance state found",
            )
        ],
    )

    ledger = AcceptanceLedger(invitations=[invitation])
    candidates = ledger.legacy_acceptances_for_reconciliation()
    cleared = ledger.clear_unavailable_relationship_observations()

    assert candidates == [invitation]
    assert cleared == [invitation.key()]
    assert invitation.current_relationship_observed_at is None


def test_acceptance_import_records_first_observed_transition_once() -> None:
    sent_at = datetime(2026, 7, 19, 12, tzinfo=UTC)
    first_check = datetime(2026, 7, 20, 12, tzinfo=UTC)
    accepted_check = datetime(2026, 7, 21, 12, tzinfo=UTC)
    later_check = datetime(2026, 7, 22, 12, tzinfo=UTC)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 7, 19),
        CandidateEvent(
            at=sent_at,
            source="source",
            name="Daily Lead",
            profile_url="https://www.linkedin.com/sales/lead/daily-lead",
            status=CandidateStatus.PENDING,
        ),
    )

    for checked_at, status, relationship, note in (
        (first_check, "pending", None, "invitation is still pending"),
        (accepted_check, "accepted", "1st", "lead page shows 1st-degree relationship"),
        (later_check, "accepted", "1st", "lead page shows 1st-degree relationship"),
    ):
        ledger.import_outcomes(
            AcceptanceOutcomeArtifact.model_validate(
                {
                    "rows": [
                        {
                            "source": "source",
                            "name": "Daily Lead",
                            "profileUrl": "https://www.linkedin.com/sales/lead/daily-lead",
                            "status": status,
                            "checkedAt": checked_at.isoformat(),
                            "relationship": relationship,
                            "evidence": (
                                '{"contractVersion":"acceptance-relationship-v2",'
                                '"expectedLeadId":"daily-lead",'
                                '"loadedLeadId":"daily-lead",'
                                '"identityMatched":true,"firstDegreeCount":1}'
                                if relationship
                                else "Pending"
                            ),
                            "evidenceGrade": (
                                "structured_first_degree" if relationship else None
                            ),
                            "contractVersion": (
                                "acceptance-relationship-v2" if relationship else None
                            ),
                            "expectedLeadId": "daily-lead" if relationship else None,
                            "loadedLeadId": "daily-lead" if relationship else None,
                            "note": note,
                        }
                    ]
                }
            )
        )

    invitation = ledger.invitations[0]
    assert invitation.first_observed_accepted_at == accepted_check
    assert invitation.last_observed_unaccepted_at == first_check
    assert (
        invitation.acceptance_observation_precision
        == AcceptanceObservationPrecision.DAILY_SCAN
    )


def test_acceptance_report_groups_durable_transitions_and_daily_coverage() -> None:
    accepted_at = datetime(2026, 7, 21, 3, tzinfo=UTC)
    invitation = AcceptanceInvitation(
        run_id=_run_id(),
        run_date=date(2026, 7, 19),
        source="source",
        name="Daily Lead",
        profile_url="https://www.linkedin.com/sales/lead/daily-lead",
        sent_at=datetime(2026, 7, 19, tzinfo=UTC),
        latest_status=AcceptanceStatus.ACCEPTED,
        latest_checked_at=accepted_at,
        first_observed_accepted_at=accepted_at,
        acceptance_observation_precision=AcceptanceObservationPrecision.DAILY_SCAN,
        history=[
            AcceptanceOutcomeEvent(
                at=accepted_at,
                status=AcceptanceStatus.ACCEPTED,
                relationship="1st",
                evidence_grade=AcceptanceEvidenceGrade.STRUCTURED_FIRST_DEGREE,
                contract_version="acceptance-relationship-v2",
                note="exact structured first-degree relationship evidence",
            )
        ],
    )
    daily_run = AcceptanceDailyRun(
        started_at=datetime(2026, 7, 20, 23, 55, tzinfo=UTC),
        completed_at=datetime(2026, 7, 21, 3, 5, tzinfo=UTC),
        local_date=date(2026, 7, 21),
        timezone="America/Argentina/Buenos_Aires",
        min_age_days=1,
        max_age_days=45,
        eligible=10,
        checked=10,
        newly_confirmed_accepted=1,
        remaining_unresolved=9,
        coverage_complete=True,
    )

    report = AcceptanceLedger(invitations=[invitation]).report(
        0,
        None,
        daily_runs=[daily_run],
        daily_days=30,
        daily_timezone="America/Argentina/Buenos_Aires",
        current=datetime(2026, 7, 21, 12, tzinfo=UTC),
    )

    assert report.daily[-1].date == date(2026, 7, 21)
    assert report.daily[-1].newly_confirmed_accepted == 1
    assert report.daily[-1].coverage_complete is True
    assert report.daily[-1].checked == 10
    assert report.daily_windows[0].newly_confirmed_accepted == 1
    assert report.daily_windows[0].missing_or_incomplete_days == 6


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


def test_acceptance_dry_run_selection_skips_already_classified_records() -> None:
    def record(name: str, status: AcceptanceFollowupStatus) -> AcceptanceFollowupRecord:
        return AcceptanceFollowupRecord(
            key=f"source:{name}",
            id=f"lead-{name}",
            source="source",
            name=name,
            profile_url=f"https://www.linkedin.com/sales/lead/{name}",
            accepted_at=datetime(2026, 6, 20, tzinfo=UTC),
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
            "profileIdentity": {
                "matched": True,
                "expectedLeadId": "abc",
                "loadedLeadId": "abc",
            },
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
    assert diagnostics["profile_identity"] == (
        '{"expectedLeadId":"abc","loadedLeadId":"abc","matched":true}'
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


def test_acceptance_followup_requires_confirmation_before_sent() -> None:
    clicked = AcceptanceFollowupSendResult.model_validate(
        {
            "dryRun": False,
            "status": "send-confirmation-missing",
            "transactionId": "tx-123",
            "messageSha256": "abc123",
            "send": {"status": "clicked"},
            "sendConfirmation": {"confirmed": False, "exactMatchCount": 0},
        }
    )
    confirmed = clicked.model_copy(
        update={
            "status": "sent-confirmed",
            "send_confirmation": {"confirmed": True, "exactMatchCount": 1},
        }
    )

    assert acceptance_followup_status_for_result(clicked) == (
        AcceptanceFollowupStatus.POSSIBLE_SEND
    )
    assert acceptance_followup_status_for_result(confirmed) == AcceptanceFollowupStatus.SENT
    diagnostics = acceptance_followup_diagnostics(clicked)
    assert diagnostics["send_confirmation"] == '{"confirmed":false,"exactMatchCount":0}'
    assert "send_confirmation" in (acceptance_followup_result_note(clicked) or "")


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


def test_accepted_welcome_message_matches_confirmed_copy() -> None:
    assert accepted_welcome_message("Sam") == (
        "Hey Sam, thanks for connecting. Glad to be in each other’s network, "
        "and I’m looking forward to following what you share here."
    )
