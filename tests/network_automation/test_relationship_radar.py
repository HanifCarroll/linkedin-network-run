from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from apps.network_automation.models import (
    BUSINESS_SYSTEMS_WATCHLIST,
    FOUNDER_OWNER_BUYERS_LEAD_LIST,
    AcceptanceFollowupLedger,
    AcceptanceFollowupMessageCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceLeadListSaveResult,
    AcceptanceLeadListStatus,
    AcceptedDraftCandidate,
    CommercialContextReference,
    CommercialCriterionAssessment,
    CommercialCriterionEvidence,
    GreetingEligibilityStatus,
    RelationshipEnrichmentArtifact,
    RelationshipEnrichmentConfidence,
    RelationshipEnrichmentDecision,
    RelationshipEnrichmentDecisionStatus,
    RelationshipEnrichmentEvidence,
    RelationshipEnrichmentQueue,
    RelationshipEnrichmentQueueItem,
    RelationshipEnrichmentStatus,
    RelationshipPriority,
    RelationshipRole,
    RelationshipSignalType,
    RelationshipWatchlistDecision,
)
from apps.network_automation.relationship_radar import (
    RelationshipRadarLedger,
    RelationshipRadarRecord,
    _watchlist_recommendation,
    save_recommended_watchlist_leads,
    sync_relationship_radar_actions,
    update_relationship_radar,
)
from apps.network_automation.store import Store, read_model, write_json_atomic


def _candidate(*, name: str, source: str, accepted_at: datetime) -> AcceptedDraftCandidate:
    return AcceptedDraftCandidate(
        run_id=uuid.uuid4(),
        run_date=date(2026, 7, 13),
        source=source,
        name=name,
        profile_url=f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
        sent_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
        accepted_at=accepted_at,
    )


def _write_artifact(path: Path, artifact: RelationshipEnrichmentArtifact) -> None:
    write_json_atomic(path, artifact.model_dump(mode="json", by_alias=False))


def _commercial_context_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> CommercialContextReference:
    icp_path = tmp_path / "ICP.md"
    offers_path = tmp_path / "OFFERS.md"
    icp_path.write_text(
        "---\nstatus: active\nprofile_id: icp-v1\n---\n\n"
        "## Qualification Contract\n\n"
        "| Criterion ID | Qualification question | Evidence that can support a match |\n"
        "| --- | --- | --- |\n"
        "| `operational-friction` | Is there operational friction? | Source evidence. |\n",
        encoding="utf-8",
    )
    offers_path.write_text(
        "---\nstatus: active\nprofile_id: offers-v1\n---\n\n"
        "## Active Catalog\n\n"
        "| Offer ID | Offer | Status | Commercial shape |\n"
        "| --- | --- | --- | --- |\n"
        "| `business-systems-audit` | Business Systems Audit | active | Starts at $750. |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "apps.network_automation.commercial_context.DEFAULT_ICP_SOURCE_PATH",
        str(icp_path),
    )
    monkeypatch.setattr(
        "apps.network_automation.commercial_context.DEFAULT_OFFERS_SOURCE_PATH",
        str(offers_path),
    )
    return CommercialContextReference(
        icp_source_path=str(icp_path),
        offers_source_path=str(offers_path),
    )


def test_relationship_radar_is_cumulative_and_review_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    commercial_context = _commercial_context_fixture(tmp_path, monkeypatch)
    buyer = _candidate(
        name="Buyer One",
        source="Consulting - Founder Owner Buyers",
        accepted_at=generated_at,
    )
    recruiter = _candidate(
        name="Recruiter One",
        source="ASAP - Contract Recruiters Staffing",
        accepted_at=generated_at,
    )
    artifact = RelationshipEnrichmentArtifact(
        generated_at=generated_at,
        commercial_context=commercial_context,
        decisions=[
            RelationshipEnrichmentDecision(
                followup_id="followup-buyer",
                candidate_key="buyer-one",
                candidate=buyer,
                status=RelationshipEnrichmentDecisionStatus.ENRICHED,
                confidence=RelationshipEnrichmentConfidence.HIGH,
                person_summary="Founder of a growing services business.",
                company_name="Buyer Company",
                company_summary="The company is scaling delivery operations.",
                relationship_role=RelationshipRole.BUYER,
                priority=RelationshipPriority.HIGH,
                signal_type=RelationshipSignalType.COMPANY_SITE,
                visible_signal="The company added a second delivery team.",
                signal_url="https://buyer.example.com/about",
                followup_reason="Their delivery handoffs are becoming more complex.",
                next_useful_action="Review whether a workflow bottleneck audit would help.",
                permission_boundary="review_only",
                evidence_urls=["https://buyer.example.com/about"],
                research_evidence=[
                    RelationshipEnrichmentEvidence(
                        evidence_id="E1",
                        source_url="https://buyer.example.com/about",
                        claim="The company added a second delivery team.",
                        source_excerpt="We added a second delivery team.",
                    )
                ],
                commercial_context=commercial_context,
                criterion_evidence=[
                    CommercialCriterionEvidence(
                        criterion_id="operational-friction",
                        assessment=CommercialCriterionAssessment.MATCHED,
                        evidence_ids=["E1"],
                        explanation="Delivery complexity is established by the source.",
                    )
                ],
                unknowns=["Budget authority is not established."],
            ),
            RelationshipEnrichmentDecision(
                followup_id="followup-recruiter",
                candidate_key="recruiter-one",
                candidate=recruiter,
                status=RelationshipEnrichmentDecisionStatus.SKIP,
                relationship_role=RelationshipRole.HIRING_RECRUITER,
                priority=RelationshipPriority.PAUSE,
                signal_type=RelationshipSignalType.NONE,
                followup_reason="This is an employment-only relationship.",
                next_useful_action="Keep paused while the employment lane is paused.",
                permission_boundary="review_only",
                commercial_context=commercial_context,
                criterion_evidence=[
                    CommercialCriterionEvidence(
                        criterion_id="operational-friction",
                        assessment=CommercialCriterionAssessment.UNKNOWN,
                        evidence_ids=[],
                        explanation="Employment context does not establish buyer fit.",
                    )
                ],
                unknowns=["A separate consulting relationship is not established."],
            ),
        ],
    )
    reviewed_path = tmp_path / "reviewed-research.json"
    _write_artifact(reviewed_path, artifact)
    store = Store(tmp_path / "state")

    result = update_relationship_radar(
        store,
        enrichment=reviewed_path,
        out=None,
        markdown_out=None,
    )

    ledger_path = store.dir / "relationship-radar" / "ledger.json"
    ledger = read_model(ledger_path, RelationshipRadarLedger)
    records = {record.candidate_key: record for record in ledger.records}
    assert "active=1" in result
    assert "paused=1" in result
    assert records["buyer-one"].review_state == "active"
    assert records["buyer-one"].relationship_enrichment_status == (
        RelationshipEnrichmentStatus.CURRENT
    )
    assert records["buyer-one"].proposed_message is None
    assert records["buyer-one"].commercial_context is not None
    assert records["buyer-one"].commercial_context.icp_profile_id == "icp-v1"
    assert records["buyer-one"].commercial_context.offers_profile_id == "offers-v1"
    assert records["buyer-one"].commercial_context.offer_id == "business-systems-audit"
    assert records["buyer-one"].unknowns == ["Budget authority is not established."]
    assert records["recruiter-one"].review_state == "paused"
    assert all(record.permission_boundary == "review_only" for record in ledger.records)
    report = ledger_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "## Buyer relationships" in report
    assert "## Hiring and recruiter sources paused" in report
    assert "does not authorize connection requests" in report


    records["buyer-one"].manual_notes.append("Met at the July operations event.")
    write_json_atomic(ledger_path, ledger.model_dump(mode="json", by_alias=False))
    updated = artifact.model_copy(deep=True)
    updated.decisions = [updated.decisions[0]]
    updated.decisions[0].followup_reason = "A new delivery role makes the timing concrete."
    _write_artifact(reviewed_path, updated)

    update_relationship_radar(
        store,
        enrichment=reviewed_path,
        out=None,
        markdown_out=None,
    )

    refreshed = read_model(ledger_path, RelationshipRadarLedger)
    refreshed_records = {record.candidate_key: record for record in refreshed.records}
    assert len(refreshed.records) == 2
    assert refreshed_records["buyer-one"].manual_notes == ["Met at the July operations event."]
    assert (
        refreshed_records["buyer-one"].followup_reason
        == "A new delivery role makes the timing concrete."
    )

    store.save_acceptance_followup_ledger(
        AcceptanceFollowupLedger(
            drafts=[
                AcceptanceFollowupRecord(
                    key="buyer-one",
                    id="followup-buyer",
                    source="Consulting - Founder Owner Buyers",
                    name="Buyer One",
                    profile_url=buyer.profile_url,
                    accepted_at=generated_at,
                    draft=(
                        "Hey Buyer, thanks for connecting. Glad to be in each other’s "
                        "network, and I’m looking forward to following what you share here."
                    ),
                    relationship_role=RelationshipRole.BUYER,
                    relationship_priority=RelationshipPriority.HIGH,
                    greeting_eligibility_status=GreetingEligibilityStatus.ELIGIBLE,
                    relationship_enrichment_status=RelationshipEnrichmentStatus.CURRENT,
                    relationship_enriched_at=generated_at,
                    original_connection_approved_at=generated_at,
                    original_connection_approval_reason="Approved in the original packet.",
                    sales_nav_list_name=FOUNDER_OWNER_BUYERS_LEAD_LIST,
                    sales_nav_list_status=AcceptanceLeadListStatus.SAVED,
                    sales_nav_list_saved_at=generated_at,
                    status=AcceptanceFollowupStatus.SENT,
                    sent_at=generated_at,
                    report_path="followups.md",
                )
            ]
        )
    )

    sync_relationship_radar_actions(store, out=None, markdown_out=None)

    action_synced = read_model(ledger_path, RelationshipRadarLedger)
    action_record = next(
        record for record in action_synced.records if record.candidate_key == "buyer-one"
    )
    assert action_record.sales_nav_list_name == FOUNDER_OWNER_BUYERS_LEAD_LIST
    assert action_record.sales_nav_list_status == AcceptanceLeadListStatus.SAVED
    assert action_record.first_message_status == AcceptanceFollowupStatus.SENT
    assert action_record.first_message_sent_at == generated_at


def test_watchlist_requires_a_source_backed_buyer_and_operating_reason() -> None:
    candidate = _candidate(
        name="Watch Buyer",
        source="Consulting - Founder Owner Buyers",
        accepted_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )
    decision = RelationshipEnrichmentDecision(
        followup_id="followup-watch",
        candidate_key="watch-buyer",
        candidate=candidate,
        status=RelationshipEnrichmentDecisionStatus.ENRICHED,
        relationship_role=RelationshipRole.BUYER,
        priority=RelationshipPriority.HIGH,
        criterion_evidence=[
            CommercialCriterionEvidence(
                criterion_id="recurring-operational-work",
                assessment=CommercialCriterionAssessment.MATCHED,
                evidence_ids=["E1"],
                explanation="The public source describes recurring client delivery work.",
            )
        ],
    )

    recommendation, reason = _watchlist_recommendation(decision)

    assert recommendation == RelationshipWatchlistDecision.RECOMMENDED
    assert "recurring-operational-work" in reason

    unresolved = decision.model_copy(update={"criterion_evidence": []}, deep=True)
    recommendation, _ = _watchlist_recommendation(unresolved)
    assert recommendation == RelationshipWatchlistDecision.NEEDS_REVIEW


def test_watchlist_save_uses_only_the_exact_narrow_list(tmp_path: Path) -> None:
    store = Store(tmp_path)
    accepted_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    sales_nav_url = "https://www.linkedin.com/sales/lead/watch-buyer"
    followup = AcceptanceFollowupRecord(
        key="watch-buyer",
        id="followup-watch",
        source="Consulting - Founder Owner Buyers",
        name="Watch Buyer",
        profile_url=sales_nav_url,
        sales_nav_profile_url=None,
        accepted_at=accepted_at,
        draft="Welcome",
        report_path="welcome.md",
    )
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=[followup]))
    radar_path = store.dir / "relationship-radar" / "ledger.json"
    write_json_atomic(
        radar_path,
        RelationshipRadarLedger(
            records=[
                RelationshipRadarRecord(
                    candidate_key="watch-buyer",
                    followup_id="followup-watch",
                    name="Watch Buyer",
                    source=followup.source,
                    profile_url=followup.profile_url,
                    sales_nav_profile_url=sales_nav_url,
                    relationship_role=RelationshipRole.BUYER,
                    enrichment_decision_status=RelationshipEnrichmentDecisionStatus.ENRICHED,
                    relationship_enrichment_status=RelationshipEnrichmentStatus.CURRENT,
                    criterion_evidence=[
                        CommercialCriterionEvidence(
                            criterion_id="recurring-operational-work",
                            assessment=CommercialCriterionAssessment.MATCHED,
                            evidence_ids=["E1"],
                            explanation="Existing research established recurring delivery.",
                        )
                    ],
                    accepted_at=accepted_at,
                )
            ]
        ).model_dump(mode="json", by_alias=False),
    )

    sync_relationship_radar_actions(store, out=None, markdown_out=None)
    migrated = read_model(radar_path, RelationshipRadarLedger).records[0]
    assert migrated.watchlist_decision == RelationshipWatchlistDecision.RECOMMENDED
    assert migrated.watchlist_status == AcceptanceLeadListStatus.PENDING

    class WatchlistBrowser:
        def save_acceptance_lead_to_list(
            self,
            record: AcceptanceFollowupRecord,
            *,
            allow_save: bool,
        ) -> tuple[AcceptanceLeadListSaveResult, str]:
            assert allow_save is True
            assert record.sales_nav_list_name == BUSINESS_SYSTEMS_WATCHLIST
            assert record.sales_nav_profile_url == sales_nav_url
            return (
                AcceptanceLeadListSaveResult(
                    candidate=AcceptanceFollowupMessageCandidate(
                        id=record.id,
                        key=record.key,
                        name=record.name,
                        profile_url=record.profile_url or "",
                        sales_nav_profile_url=record.sales_nav_profile_url,
                        source=record.source,
                    ),
                    list_name=BUSINESS_SYSTEMS_WATCHLIST,
                    status=AcceptanceLeadListStatus.SAVED,
                    url=record.sales_nav_profile_url,
                ),
                str(tmp_path / "watchlist-save.json"),
            )

    result = save_recommended_watchlist_leads(
        store,
        WatchlistBrowser(),  # type: ignore[arg-type]
        limit=30,
        allow_save=True,
    )

    saved = read_model(radar_path, RelationshipRadarLedger).records[0]
    assert saved.watchlist_status == AcceptanceLeadListStatus.SAVED
    assert saved.watchlist_name == BUSINESS_SYSTEMS_WATCHLIST
    assert BUSINESS_SYSTEMS_WATCHLIST in result


def test_watchlist_save_skips_selection_ambiguity_and_continues(tmp_path: Path) -> None:
    store = Store(tmp_path)
    accepted_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    followups = [
        AcceptanceFollowupRecord(
            key=f"watch-buyer-{index}",
            id=f"followup-watch-{index}",
            source="Consulting - Founder Owner Buyers",
            name=name,
            profile_url=f"https://www.linkedin.com/in/watch-buyer-{index}",
            sales_nav_profile_url=f"https://www.linkedin.com/sales/lead/watch-buyer-{index}",
            accepted_at=accepted_at,
            draft="Welcome",
            report_path="welcome.md",
        )
        for index, name in enumerate(("Ambiguous Buyer", "Next Buyer"), start=1)
    ]
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=followups))
    radar_path = store.dir / "relationship-radar" / "ledger.json"
    write_json_atomic(
        radar_path,
        RelationshipRadarLedger(
            records=[
                RelationshipRadarRecord(
                    candidate_key=followup.key,
                    followup_id=followup.id,
                    name=followup.name,
                    source=followup.source,
                    profile_url=followup.profile_url,
                    sales_nav_profile_url=followup.sales_nav_profile_url,
                    relationship_role=RelationshipRole.BUYER,
                    enrichment_decision_status=RelationshipEnrichmentDecisionStatus.ENRICHED,
                    relationship_enrichment_status=RelationshipEnrichmentStatus.CURRENT,
                    criterion_evidence=[
                        CommercialCriterionEvidence(
                            criterion_id="recurring-operational-work",
                            assessment=CommercialCriterionAssessment.MATCHED,
                            evidence_ids=["E1"],
                            explanation="Existing research established recurring delivery.",
                        )
                    ],
                    accepted_at=accepted_at,
                )
                for followup in followups
            ]
        ).model_dump(mode="json", by_alias=False),
    )
    sync_relationship_radar_actions(store, out=None, markdown_out=None)

    class WatchlistBrowser:
        calls: list[str] = []

        def save_acceptance_lead_to_list(
            self,
            record: AcceptanceFollowupRecord,
            *,
            allow_save: bool,
        ) -> tuple[AcceptanceLeadListSaveResult, str]:
            is_first_call = not self.calls
            self.calls.append(record.name)
            status = (
                AcceptanceLeadListStatus.BLOCKED
                if is_first_call
                else AcceptanceLeadListStatus.SAVED
            )
            reason = (
                f"target lead list selection was ambiguous: {BUSINESS_SYSTEMS_WATCHLIST}"
                if status == AcceptanceLeadListStatus.BLOCKED
                else None
            )
            return (
                AcceptanceLeadListSaveResult(
                    candidate=AcceptanceFollowupMessageCandidate(
                        id=record.id,
                        key=record.key,
                        name=record.name,
                        profile_url=record.profile_url or "",
                        sales_nav_profile_url=record.sales_nav_profile_url,
                        source=record.source,
                    ),
                    list_name=BUSINESS_SYSTEMS_WATCHLIST,
                    status=status,
                    url=record.sales_nav_profile_url,
                    reason=reason,
                ),
                str(tmp_path / f"{record.id}.json"),
            )

    browser = WatchlistBrowser()
    result = save_recommended_watchlist_leads(
        store,
        browser,  # type: ignore[arg-type]
        limit=30,
        allow_save=True,
    )

    saved = read_model(radar_path, RelationshipRadarLedger).records
    assert len(browser.calls) == 2
    statuses = {record.name: record.watchlist_status for record in saved}
    assert statuses[browser.calls[0]] == AcceptanceLeadListStatus.BLOCKED
    assert statuses[browser.calls[1]] == AcceptanceLeadListStatus.SAVED
    assert "watchlist save skipped ambiguous candidate; continuing" in result


@pytest.mark.parametrize(
    ("criterion_evidence", "message"),
    [
        ([], "missing=.*operational-friction"),
        (
            [
                CommercialCriterionEvidence(
                    criterion_id="operational-friction",
                    assessment=CommercialCriterionAssessment.MATCHED,
                    evidence_ids=[],
                    explanation="The artifact claims a match without evidence.",
                )
            ],
            "matched/not_matched criterion has no evidence IDs",
        ),
    ],
)
def test_update_relationship_radar_rejects_handwritten_artifact_bypassing_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    criterion_evidence: list[CommercialCriterionEvidence],
    message: str,
) -> None:
    generated_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    commercial_context = _commercial_context_fixture(tmp_path, monkeypatch)
    candidate = _candidate(
        name="Bypass Buyer",
        source="Consulting - Founder Owner Buyers",
        accepted_at=generated_at,
    )
    artifact = RelationshipEnrichmentArtifact(
        generated_at=generated_at,
        commercial_context=commercial_context,
        decisions=[
            RelationshipEnrichmentDecision(
                followup_id="followup-bypass",
                candidate_key="bypass-buyer",
                candidate=candidate,
                status=RelationshipEnrichmentDecisionStatus.NEEDS_REVIEW,
                commercial_context=commercial_context,
                criterion_evidence=criterion_evidence,
                unknowns=["Qualification has not been assessed."],
            )
        ],
    )
    artifact_path = tmp_path / "handwritten-enrichment.json"
    _write_artifact(artifact_path, artifact)
    store = Store(tmp_path / "state")

    with pytest.raises(ValueError, match=message):
        update_relationship_radar(
            store,
            enrichment=artifact_path,
            out=None,
            markdown_out=None,
        )

    assert not (store.dir / "relationship-radar" / "ledger.json").exists()


def test_update_relationship_radar_rejects_incomplete_handwritten_enriched_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    commercial_context = _commercial_context_fixture(tmp_path, monkeypatch)
    candidate = _candidate(
        name="Incomplete Buyer",
        source="Consulting - Founder Owner Buyers",
        accepted_at=generated_at,
    )
    artifact = RelationshipEnrichmentArtifact(
        generated_at=generated_at,
        commercial_context=commercial_context,
        decisions=[
            RelationshipEnrichmentDecision(
                followup_id="followup-incomplete",
                candidate_key="incomplete-buyer",
                candidate=candidate,
                status=RelationshipEnrichmentDecisionStatus.ENRICHED,
                commercial_context=commercial_context,
                criterion_evidence=[
                    CommercialCriterionEvidence(
                        criterion_id="operational-friction",
                        assessment=CommercialCriterionAssessment.UNKNOWN,
                        evidence_ids=[],
                        explanation="The handcrafted artifact does not establish this fact.",
                    )
                ],
                unknowns=["All enrichment evidence is missing."],
            )
        ],
    )
    artifact_path = tmp_path / "incomplete-enriched.json"
    _write_artifact(artifact_path, artifact)
    store = Store(tmp_path / "state")

    with pytest.raises(ValueError, match="requires confidence=high"):
        update_relationship_radar(
            store,
            enrichment=artifact_path,
            out=None,
            markdown_out=None,
        )

    assert not (store.dir / "relationship-radar" / "ledger.json").exists()
