from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from apps.network_automation.acceptance_service import (
    acceptance_collect_enrichment_workers,
    acceptance_export_browser_investigation_queue,
    acceptance_export_enrichment_queue,
    acceptance_import,
    acceptance_launch_enrichment_workers,
    acceptance_prepare_welcome_messages,
    acceptance_retry_send_followup,
    acceptance_run_welcome_messages,
    acceptance_send_ready_followups,
)
from apps.network_automation.browser import BrowserClient
from apps.network_automation.commercial_context import (
    read_qualification_contract_ids,
    validate_commercial_context_sources,
)
from apps.network_automation.models import (
    DURABLY_CONFIRMED_ACCEPTED_NOTE,
    FOUNDER_OWNER_BUYERS_LEAD_LIST,
    FOUNDER_OWNER_BUYERS_SOURCE,
    AcceptanceFollowupAttempt,
    AcceptanceFollowupLedger,
    AcceptanceFollowupMessageCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceFollowupStatus,
    AcceptanceInvitation,
    AcceptanceLeadListSaveResult,
    AcceptanceLeadListStatus,
    AcceptanceLedger,
    AcceptanceOutcomeEvent,
    AcceptanceStatus,
    AcceptedDraftCandidate,
    AcceptedGreetingEligibilityArtifact,
    CandidateObservation,
    CommercialContextReference,
    GreetingEligibilityStatus,
    RelationshipEnrichmentStatus,
    RelationshipPriority,
    RelationshipRole,
    accepted_followup_candidate_key,
    accepted_welcome_message,
    lead_key_for_values,
)
from apps.network_automation.relationship_radar import (
    RelationshipRadarLedger,
    sync_relationship_radar_actions,
)
from apps.network_automation.store import Store, read_model, write_json_atomic

from .helpers import FIXTURES, SequenceFollowupBrowser, _run_id


def _active_candidate(name: str = "Active Founder") -> AcceptedDraftCandidate:
    return AcceptedDraftCandidate(
        run_id=_run_id(),
        run_date=date(2026, 7, 13),
        source=FOUNDER_OWNER_BUYERS_SOURCE,
        name=name,
        profile_url=f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/active-founder",
        sent_at=datetime(2026, 7, 10, tzinfo=UTC),
        accepted_at=datetime(2026, 7, 13, tzinfo=UTC),
        relationship="1st",
    )


def _store_with_active_accepted_approval(
    tmp_path: Path,
) -> tuple[Store, AcceptedDraftCandidate]:
    store = Store(tmp_path)
    candidate = _active_candidate()
    invitation = AcceptanceInvitation(
        run_id=candidate.run_id,
        run_date=candidate.run_date,
        source=candidate.source,
        name=candidate.name,
        profile_url=candidate.sales_nav_profile_url,
        public_profile_url=candidate.profile_url,
        sent_at=candidate.sent_at,
        latest_status=AcceptanceStatus.ACCEPTED,
        latest_checked_at=candidate.accepted_at,
        history=[
            AcceptanceOutcomeEvent(
                at=candidate.accepted_at,
                status=AcceptanceStatus.ACCEPTED,
                relationship="1st",
                note=DURABLY_CONFIRMED_ACCEPTED_NOTE,
                evidence="Active Founder · 1st",
            )
        ],
    )
    store.save_acceptance_ledger(AcceptanceLedger(invitations=[invitation]))
    lead_ledger = store.load_lead_ledger()
    lead_record = lead_ledger.upsert_observation(
        CandidateObservation(
            source=candidate.source,
            index=1,
            name=candidate.name,
            profile_url=candidate.sales_nav_profile_url,
            public_profile_url=candidate.profile_url,
            menu_state="connectable",
            text="Active Founder, Founder at Active Co",
        )
    )
    lead_ledger.approve(
        lead_record.lead_key,
        "Founder of a growing services business with supported workflow fit.",
    )
    lead_record.approved_at = candidate.sent_at - timedelta(days=1)
    store.save_lead_ledger(lead_ledger)
    return store, candidate


def _export_active_enrichment_queue(tmp_path: Path) -> tuple[Store, Path, dict[str, object]]:
    store, _candidate = _store_with_active_accepted_approval(tmp_path)
    acceptance_prepare_welcome_messages(
        store,
        out=tmp_path / "eligibility.json",
        report_out=tmp_path / "greetings.md",
        limit=30,
    )
    sync_relationship_radar_actions(store, out=None, markdown_out=None)
    queue_path = tmp_path / "enrichment-queue.json"
    acceptance_export_enrichment_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=30,
        stale_after_days=30,
        prioritize_engagement=False,
    )
    packet = json.loads(queue_path.read_text())
    icp_path = tmp_path / "ICP.md"
    offers_path = tmp_path / "OFFERS.md"
    icp_path.write_text(
        "---\n"
        "status: active\n"
        "profile_id: icp-v1\n"
        "---\n\n"
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
    packet["commercial_context"]["icp_source_path"] = str(icp_path)
    packet["commercial_context"]["offers_source_path"] = str(offers_path)
    queue_path.write_text(json.dumps(packet), encoding="utf-8")
    return store, queue_path, packet


def _patch_commercial_source_paths(
    monkeypatch: pytest.MonkeyPatch,
    packet: dict[str, object],
) -> None:
    context = packet["commercial_context"]
    assert isinstance(context, dict)
    monkeypatch.setattr(
        "apps.network_automation.commercial_context.DEFAULT_ICP_SOURCE_PATH",
        context["icp_source_path"],
    )
    monkeypatch.setattr(
        "apps.network_automation.commercial_context.DEFAULT_OFFERS_SOURCE_PATH",
        context["offers_source_path"],
    )


def _commercial_context_fixture(
    tmp_path: Path,
    *,
    icp_frontmatter: str = "status: active\nprofile_id: icp-v1",
    qualification_rows: str = (
        "| `criterion-one` | Is criterion one supported? | Source evidence. |"
    ),
    offer_rows: str = (
        "| `business-systems-audit` | Business Systems Audit | active | Starts at $750. |"
    ),
) -> CommercialContextReference:
    icp_path = tmp_path / "ICP.md"
    offers_path = tmp_path / "OFFERS.md"
    icp_path.write_text(
        "---\n"
        f"{icp_frontmatter}\n"
        "---\n\n"
        "## Qualification Contract\n\n"
        "| Criterion ID | Qualification question | Evidence that can support a match |\n"
        "| --- | --- | --- |\n"
        f"{qualification_rows}\n",
        encoding="utf-8",
    )
    offers_path.write_text(
        "---\nstatus: active\nprofile_id: offers-v1\n---\n\n"
        "## Active Catalog\n\n"
        "| Offer ID | Offer | Status | Commercial shape |\n"
        "| --- | --- | --- | --- |\n"
        f"{offer_rows}\n",
        encoding="utf-8",
    )
    return CommercialContextReference(
        icp_source_path=str(icp_path),
        offers_source_path=str(offers_path),
    )


def _patch_context_paths(
    monkeypatch: pytest.MonkeyPatch,
    context: CommercialContextReference,
) -> None:
    monkeypatch.setattr(
        "apps.network_automation.commercial_context.DEFAULT_ICP_SOURCE_PATH",
        context.icp_source_path,
    )
    monkeypatch.setattr(
        "apps.network_automation.commercial_context.DEFAULT_OFFERS_SOURCE_PATH",
        context.offers_source_path,
    )


def test_validate_commercial_context_requires_exact_active_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _commercial_context_fixture(tmp_path)
    _patch_context_paths(monkeypatch, context)

    assert validate_commercial_context_sources(context) == ("criterion-one",)


@pytest.mark.parametrize(
    ("offer_rows", "message"),
    [
        (
            "| `business-systems-audit` | Business Systems Audit | paused | Paused. |",
            "offer business-systems-audit is not active: paused",
        ),
        (
            "| `business-systems-build` | Business Systems Build | active | Scoped later. |",
            "offer business-systems-audit is missing from the Offers Active Catalog",
        ),
    ],
)
def test_validate_commercial_context_requires_selected_offer_to_be_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offer_rows: str,
    message: str,
) -> None:
    context = _commercial_context_fixture(tmp_path, offer_rows=offer_rows)
    _patch_context_paths(monkeypatch, context)

    with pytest.raises(ValueError, match=message):
        validate_commercial_context_sources(context)


@pytest.mark.parametrize(
    ("icp_frontmatter", "message"),
    [
        ("profile_id: icp-v1", "frontmatter is missing status"),
        ("status: paused\nprofile_id: icp-v1", "profile is not active"),
        ("status: active\nprofile_id: icp-v2", "profile id mismatch"),
        ("status: active\nprofile_id:", "malformed profile_id"),
    ],
)
def test_validate_commercial_context_rejects_bad_profile_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    icp_frontmatter: str,
    message: str,
) -> None:
    context = _commercial_context_fixture(tmp_path, icp_frontmatter=icp_frontmatter)
    _patch_context_paths(monkeypatch, context)

    with pytest.raises(ValueError, match=message):
        validate_commercial_context_sources(context)


def test_validate_commercial_context_rejects_noncanonical_source_path(
    tmp_path: Path,
) -> None:
    context = _commercial_context_fixture(tmp_path)

    with pytest.raises(ValueError, match="ICP source path mismatch"):
        validate_commercial_context_sources(context)


def test_validate_commercial_context_rejects_missing_canonical_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _commercial_context_fixture(tmp_path)
    _patch_context_paths(monkeypatch, context)
    Path(context.offers_source_path).unlink()

    with pytest.raises(FileNotFoundError, match="offers source is missing"):
        validate_commercial_context_sources(context)


@pytest.mark.parametrize(
    ("qualification_rows", "message"),
    [
        ("", "has no criteria"),
        (
            "| `duplicate` | First? | Evidence. |\n"
            "| `duplicate` | Second? | Evidence. |",
            "duplicate criterion ID duplicate",
        ),
        ("| criterion-without-code | Question? | Evidence. |", "criterion ID is malformed"),
    ],
)
def test_qualification_contract_rejects_missing_duplicate_or_malformed_ids(
    tmp_path: Path,
    qualification_rows: str,
    message: str,
) -> None:
    context = _commercial_context_fixture(tmp_path, qualification_rows=qualification_rows)

    with pytest.raises(ValueError, match=message):
        read_qualification_contract_ids(Path(context.icp_source_path))


def test_prepare_welcome_messages_replaces_old_unsent_copy(
    tmp_path: Path,
) -> None:
    store, candidate = _store_with_active_accepted_approval(tmp_path)
    key = accepted_followup_candidate_key(candidate)
    store.save_acceptance_followup_ledger(
        AcceptanceFollowupLedger(
            drafts=[
                AcceptanceFollowupRecord(
                    key=key,
                    id="afu_existing",
                    source=candidate.source,
                    name=candidate.name,
                    profile_url=candidate.profile_url,
                    sales_nav_profile_url=candidate.sales_nav_profile_url,
                    accepted_at=candidate.accepted_at,
                    draft="Obsolete contextual copy.",
                    report_path="obsolete.md",
                )
            ]
        )
    )
    out = tmp_path / "approved.json"

    output = acceptance_prepare_welcome_messages(
        store,
        out=out,
        report_out=tmp_path / "greetings.md",
        limit=30,
    )

    approved = AcceptedGreetingEligibilityArtifact.model_validate_json(out.read_text())
    assert "1 eligible" in output
    assert approved.items[0].status == GreetingEligibilityStatus.ELIGIBLE
    assert approved.items[0].original_connection_source == FOUNDER_OWNER_BUYERS_SOURCE
    assert approved.items[0].proposed_message == accepted_welcome_message("Active")
    record = store.load_acceptance_followup_ledger().drafts[0]
    assert record.greeting_eligibility_status == GreetingEligibilityStatus.ELIGIBLE
    assert record.relationship_enrichment_status == RelationshipEnrichmentStatus.MISSING
    assert record.original_connection_approval_reason is not None
    assert record.draft == accepted_welcome_message("Active")


def test_prepare_welcome_messages_does_not_require_original_review_record(
    tmp_path: Path,
) -> None:
    store, candidate = _store_with_active_accepted_approval(tmp_path)
    lead_ledger = store.load_lead_ledger()
    lead_record = next(iter(lead_ledger.leads.values()))
    lead_record.approved_reason = None
    store.save_lead_ledger(lead_ledger)

    out = tmp_path / "blocked.json"
    acceptance_prepare_welcome_messages(
        store,
        out=out,
        report_out=tmp_path / "blocked.md",
        limit=30,
    )

    eligible = AcceptedGreetingEligibilityArtifact.model_validate_json(out.read_text())
    assert eligible.items[0].status == GreetingEligibilityStatus.ELIGIBLE
    assert eligible.items[0].original_connection_approval_reason is None
    assert store.load_acceptance_followup_ledger().drafts[0].draft == accepted_welcome_message(
        "Active"
    )

    store, candidate = _store_with_active_accepted_approval(tmp_path / "name-only")
    lead_ledger = store.load_lead_ledger()
    lead_record = next(iter(lead_ledger.leads.values()))
    lead_ledger.leads = {lead_key_for_values(None, None, candidate.name): lead_record}
    store.save_lead_ledger(lead_ledger)
    acceptance_prepare_welcome_messages(
        store,
        out=tmp_path / "name-only.json",
        report_out=tmp_path / "name-only.md",
        limit=30,
    )
    name_only = AcceptedGreetingEligibilityArtifact.model_validate_json(
        (tmp_path / "name-only.json").read_text()
    )
    assert name_only.items[0].status == GreetingEligibilityStatus.ELIGIBLE


class ApprovedPilotBrowser:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.calls: list[str] = []

    def save_acceptance_lead_to_list(
        self,
        record: AcceptanceFollowupRecord,
        *,
        allow_save: bool,
    ) -> tuple[AcceptanceLeadListSaveResult, str]:
        assert allow_save is True
        self.calls.append("save-list")
        return (
            AcceptanceLeadListSaveResult(
                candidate=AcceptanceFollowupMessageCandidate(
                    id=record.id,
                    key=record.key,
                    name=record.name,
                    profile_url=record.profile_url or record.sales_nav_profile_url or "",
                    sales_nav_profile_url=record.sales_nav_profile_url,
                    source=record.source,
                ),
                list_name=FOUNDER_OWNER_BUYERS_LEAD_LIST,
                status=AcceptanceLeadListStatus.SAVED,
                url=record.sales_nav_profile_url,
            ),
            str(self.out_dir / "lead-list.json"),
        )

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        _ = allow_send
        assert preview_fill is False
        self.calls.append("dry-run" if dry_run else "send")
        return (
            AcceptanceFollowupSendResult(
                candidate=AcceptanceFollowupMessageCandidate(
                    id=record.id,
                    key=record.key,
                    name=record.name,
                    profile_url=record.profile_url or record.sales_nav_profile_url or "",
                    sales_nav_profile_url=record.sales_nav_profile_url,
                    source=record.source,
                ),
                dry_run=dry_run,
                url=record.profile_url,
                message_length=len(record.draft),
                status="dry-run-messageable" if dry_run else "sent-clicked",
            ),
            str(self.out_dir / ("dry-run.json" if dry_run else "sent.json")),
        )


def test_welcome_run_dry_runs_then_sends_without_saving_a_list(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    record = AcceptanceFollowupRecord(
        key="active-founder-key",
        id="afu_active",
        source=FOUNDER_OWNER_BUYERS_SOURCE,
        name="Active Founder",
        profile_url="https://www.linkedin.com/in/active-founder",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/active-founder",
        accepted_at=datetime(2026, 7, 13, tzinfo=UTC),
        draft=accepted_welcome_message("Active"),
        relationship_role=RelationshipRole.BUYER,
        relationship_priority=RelationshipPriority.NORMAL,
        greeting_eligibility_status=GreetingEligibilityStatus.ELIGIBLE,
        relationship_enrichment_status=RelationshipEnrichmentStatus.MISSING,
        original_connection_approved_at=datetime(2026, 7, 9, tzinfo=UTC),
        original_connection_approval_reason="Approved in the original lead packet.",
        sales_nav_list_name=FOUNDER_OWNER_BUYERS_LEAD_LIST,
        sales_nav_list_status=AcceptanceLeadListStatus.PENDING,
        report_path="greetings.md",
    )
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=[record]))
    browser = ApprovedPilotBrowser(tmp_path)

    output = acceptance_run_welcome_messages(
        store,
        cast(BrowserClient, browser),
        run_limit=1,
        allow_send=True,
    )

    stored = store.load_acceptance_followup_ledger().drafts[0]
    assert browser.calls == ["dry-run", "send"]
    assert stored.sales_nav_list_status == AcceptanceLeadListStatus.PENDING
    assert stored.status == AcceptanceFollowupStatus.SENT
    assert "welcome-message run complete: 1 sent this run" in output


@pytest.mark.parametrize(
    "classified_status",
    [
        AcceptanceFollowupStatus.NOT_MESSAGEABLE,
        AcceptanceFollowupStatus.BLOCKED,
        AcceptanceFollowupStatus.SEND_FAILED,
    ],
)
def test_welcome_run_retries_only_prior_dry_run_classifications(
    tmp_path: Path,
    classified_status: AcceptanceFollowupStatus,
) -> None:
    store = Store(tmp_path)
    record = AcceptanceFollowupRecord(
        key="retryable-founder-key",
        id="afu_retryable",
        source=FOUNDER_OWNER_BUYERS_SOURCE,
        name="Retryable Founder",
        profile_url="https://www.linkedin.com/in/retryable-founder",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/retryable-founder",
        accepted_at=datetime(2026, 7, 13, tzinfo=UTC),
        draft=accepted_welcome_message("Retryable"),
        greeting_eligibility_status=GreetingEligibilityStatus.ELIGIBLE,
        status=classified_status,
        attempts=[
            AcceptanceFollowupAttempt(
                dry_run=True,
                status="composer-missing",
                out_path="prior-dry-run.json",
            )
        ],
        report_path="greetings.md",
    )
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=[record]))
    browser = ApprovedPilotBrowser(tmp_path)

    acceptance_run_welcome_messages(
        store,
        cast(BrowserClient, browser),
        run_limit=1,
        allow_send=True,
    )

    stored = store.load_acceptance_followup_ledger().drafts[0]
    assert browser.calls == ["dry-run", "send"]
    assert stored.status == AcceptanceFollowupStatus.SENT


def test_welcome_run_does_not_retry_a_failed_real_send_attempt(tmp_path: Path) -> None:
    store = Store(tmp_path)
    record = AcceptanceFollowupRecord(
        key="uncertain-founder-key",
        id="afu_uncertain",
        source=FOUNDER_OWNER_BUYERS_SOURCE,
        name="Uncertain Founder",
        profile_url="https://www.linkedin.com/in/uncertain-founder",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/uncertain-founder",
        accepted_at=datetime(2026, 7, 13, tzinfo=UTC),
        draft=accepted_welcome_message("Uncertain"),
        greeting_eligibility_status=GreetingEligibilityStatus.ELIGIBLE,
        status=AcceptanceFollowupStatus.SEND_FAILED,
        attempts=[
            AcceptanceFollowupAttempt(
                dry_run=False,
                status="send-confirmation-missing",
                out_path="prior-real-send.json",
            )
        ],
        report_path="greetings.md",
    )
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=[record]))
    browser = ApprovedPilotBrowser(tmp_path)

    output = acceptance_run_welcome_messages(
        store,
        cast(BrowserClient, browser),
        run_limit=1,
        allow_send=True,
    )

    assert browser.calls == []
    assert "requires review in status send_failed" in output


def test_welcome_run_handles_multiple_accepted_connections(
    tmp_path: Path,
) -> None:
    blocked = AcceptanceFollowupRecord(
        key="blocked-founder-key",
        id="afu_blocked",
        source=FOUNDER_OWNER_BUYERS_SOURCE,
        name="Blocked Founder",
        profile_url="https://www.linkedin.com/in/blocked-founder",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/blocked-founder",
        accepted_at=datetime(2026, 7, 13, tzinfo=UTC),
        draft=accepted_welcome_message("Blocked"),
        relationship_role=RelationshipRole.BUYER,
        relationship_priority=RelationshipPriority.NORMAL,
        greeting_eligibility_status=GreetingEligibilityStatus.ELIGIBLE,
        relationship_enrichment_status=RelationshipEnrichmentStatus.MISSING,
        original_connection_approved_at=datetime(2026, 7, 9, tzinfo=UTC),
        original_connection_approval_reason="Approved from sourced founder evidence.",
        sales_nav_list_name=FOUNDER_OWNER_BUYERS_LEAD_LIST,
        sales_nav_list_status=AcceptanceLeadListStatus.PENDING,
        report_path="greetings.md",
    )
    ready = blocked.model_copy(
        update={
            "key": "ready-founder-key",
            "id": "afu_ready",
            "name": "Ready Founder",
            "profile_url": "https://www.linkedin.com/in/ready-founder",
            "sales_nav_profile_url": "https://www.linkedin.com/sales/lead/ready-founder",
            "draft": accepted_welcome_message("Ready"),
        },
        deep=True,
    )
    store = Store(tmp_path)
    store.save_acceptance_followup_ledger(
        AcceptanceFollowupLedger(drafts=[blocked, ready])
    )

    browser = ApprovedPilotBrowser(tmp_path)
    output = acceptance_run_welcome_messages(
        store,
        cast(BrowserClient, browser),
        run_limit=2,
        allow_send=True,
    )

    ledger = store.load_acceptance_followup_ledger()
    assert browser.calls == ["dry-run", "send", "dry-run", "send"]
    assert ledger.drafts[0].status == AcceptanceFollowupStatus.SENT
    assert ledger.drafts[1].status == AcceptanceFollowupStatus.SENT
    assert "welcome-message run complete: 2 sent this run" in output


def test_acceptance_import_downgrades_mismatched_identity(tmp_path: Path) -> None:
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    invitation = AcceptanceInvitation(
        run_id=_run_id(),
        run_date=date(2026, 6, 24),
        source="ASAP - Agency Owners Delivery",
        name="Duplicate Lead",
        profile_url="https://www.linkedin.com/sales/lead/dup?_ntb=session",
        sent_at=datetime.now(UTC) - timedelta(days=8),
        latest_status=AcceptanceStatus.PENDING,
    )
    ledger.invitations.append(invitation)
    store.save_acceptance_ledger(ledger)

    acceptance_import(store, FIXTURES / "acceptance_outcomes_mismatch.json")

    updated = store.load_acceptance_ledger()
    assert updated.invitations[0].latest_status == AcceptanceStatus.UNKNOWN
    assert "downgraded to unknown" in (updated.invitations[0].history[0].note or "")


def test_enrichment_queue_only_refreshes_missing_or_stale(tmp_path: Path) -> None:
    store, queue_path, packet = _export_active_enrichment_queue(tmp_path)
    assert packet["items"][0]["enrichment_status"] == "missing"  # type: ignore[index]

    radar_path = store.dir / "relationship-radar" / "ledger.json"
    radar = read_model(radar_path, RelationshipRadarLedger)
    radar.records[0].relationship_enrichment_status = RelationshipEnrichmentStatus.CURRENT
    radar.records[0].enriched_at = datetime.now(UTC)
    write_json_atomic(radar_path, radar.model_dump(mode="json", by_alias=False))
    acceptance_export_enrichment_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=30,
        stale_after_days=30,
        prioritize_engagement=False,
    )
    assert json.loads(queue_path.read_text())["items"] == []

    radar.records[0].enriched_at = datetime.now(UTC) - timedelta(days=31)
    write_json_atomic(radar_path, radar.model_dump(mode="json", by_alias=False))
    acceptance_export_enrichment_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=30,
        stale_after_days=30,
        prioritize_engagement=False,
    )
    assert json.loads(queue_path.read_text())["items"][0]["enrichment_status"] == "stale"


def test_browser_investigation_queue_uses_its_own_cooldown(tmp_path: Path) -> None:
    store, _queue_path, _packet = _export_active_enrichment_queue(tmp_path)
    radar_path = store.dir / "relationship-radar" / "ledger.json"
    radar = read_model(radar_path, RelationshipRadarLedger)
    record = radar.records[0]
    record.relationship_enrichment_status = RelationshipEnrichmentStatus.NEEDS_REVIEW
    record.enriched_at = datetime.now(UTC)
    write_json_atomic(radar_path, radar.model_dump(mode="json", by_alias=False))
    browser_queue = tmp_path / "browser-investigation-queue.json"

    acceptance_export_browser_investigation_queue(
        store,
        out=browser_queue,
        markdown_out=None,
        limit=5,
        cooldown_days=30,
    )
    packet = json.loads(browser_queue.read_text())
    assert len(packet["items"]) == 1
    assert packet["items"][0]["enrichment_reason"] == "browser_needs_review"

    radar = read_model(radar_path, RelationshipRadarLedger)
    radar.records[0].browser_investigated_at = datetime.now(UTC)
    write_json_atomic(radar_path, radar.model_dump(mode="json", by_alias=False))
    acceptance_export_browser_investigation_queue(
        store,
        out=browser_queue,
        markdown_out=None,
        limit=5,
        cooldown_days=30,
    )
    assert json.loads(browser_queue.read_text())["items"] == []

    radar.records[0].browser_investigated_at = datetime.now(UTC) - timedelta(days=31)
    write_json_atomic(radar_path, radar.model_dump(mode="json", by_alias=False))
    acceptance_export_browser_investigation_queue(
        store,
        out=browser_queue,
        markdown_out=None,
        limit=5,
        cooldown_days=30,
    )
    assert len(json.loads(browser_queue.read_text())["items"]) == 1


def test_launch_enrichment_workers_writes_source_bundle_and_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, queue_path, packet = _export_active_enrichment_queue(tmp_path)
    _patch_commercial_source_paths(monkeypatch, packet)
    item = packet["items"][0]  # type: ignore[index]
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_fetch(url: str, *, timeout_seconds: float) -> dict[str, object]:
        _ = timeout_seconds
        return {
            "ok": True,
            "status_code": 200,
            "url": url,
            "final_url": url,
            "content_type": "text/html",
            "html": "<html><body>Active Founder runs Active Co.</body></html>",
            "text": "Active Founder runs Active Co.",
            "warning": None,
        }

    class FakeProcess:
        pid = 5151

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        "apps.network_automation.acceptance_service.fetch_source_material",
        fake_fetch,
    )
    monkeypatch.setattr(
        "apps.network_automation.acceptance_service.subprocess.Popen",
        fake_popen,
    )
    jobs_dir = tmp_path / "enrichment-jobs"
    sources_dir = tmp_path / "source-bundles"

    output = acceptance_launch_enrichment_workers(
        store,
        enrichment_queue=queue_path,
        jobs_dir=jobs_dir,
        sources_dir=sources_dir,
        codex_bin="/usr/local/bin/codex",
        cwd=Path("/Users/hanifcarroll/projects/linkedin-tools"),
        model="gpt-5.5",
        reasoning_effort="xhigh",
        offset=0,
        limit=1,
        force=False,
        fetch_timeout_seconds=3.0,
    )

    job_dir = jobs_dir / item["followup_id"]
    source_manifest = sources_dir / item["followup_id"] / "sources.json"
    prompt = (job_dir / "packet.md").read_text()
    assert "launched 1 Codex enrichment worker" in output
    assert "Your job is relationship enrichment" in prompt
    assert "not greeting eligibility or message drafting" in prompt
    assert "`enriched` requires `confidence` = `high`" in prompt
    assert "ICP profile `icp-v1`" in prompt
    assert "offers profile `offers-v1`" in prompt
    assert "offer `business-systems-audit`" in prompt
    assert "Do not classify from keyword or regex scores" in prompt
    assert str(source_manifest) in prompt
    assert json.loads(source_manifest.read_text())["sources"][0]["text_path"]
    schema = json.loads((job_dir / "schema.json").read_text())
    assert "template_key" not in schema["properties"]
    assert "proposed_message" not in schema["properties"]
    assert "commercial_context" in schema["properties"]
    assert "criterion_evidence" in schema["properties"]
    assert "unknowns" in schema["properties"]
    command, kwargs = calls[0]
    assert command[:2] == ["/usr/local/bin/codex", "exec"]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert kwargs["start_new_session"] is True


def _valid_enrichment_result(
    item: dict[str, object],
    packet: dict[str, object],
) -> dict[str, object]:
    return {
        "candidate_key": item["candidate_key"],
        "status": "enriched",
        "confidence": "high",
        "person_summary": "Active Founder runs product delivery at Active Co.",
        "company_name": "Active Co",
        "company_summary": "Active Co runs service delivery operations.",
        "official_company_url": "https://active.example.com",
        "evidence_urls": ["https://active.example.com"],
        "research_evidence": [
            {
                "evidence_id": "E1",
                "source_url": "https://active.example.com",
                "claim": "Active Co runs service delivery operations.",
                "relevance": "Relevant to workflow systems work.",
                "source_excerpt": "Service delivery operations for growing teams.",
            }
        ],
        "commercial_context": packet["commercial_context"],
        "criterion_evidence": [
            {
                "criterion_id": "operational-friction",
                "assessment": "matched",
                "evidence_ids": ["E1"],
                "explanation": "The source establishes a delivery-operations need.",
            }
        ],
        "unknowns": ["Budget authority is not established."],
        "warnings": [],
        "relationship_role": "buyer",
        "priority": "high",
        "signal_type": "company_site",
        "visible_signal": "Active Co is expanding service delivery.",
        "signal_url": "https://active.example.com",
        "followup_reason": "Delivery handoffs may need stronger systems.",
        "next_useful_action": "Review the relationship priority.",
        "permission_boundary": "review_only",
    }


def test_collect_enrichment_workers_writes_current_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, queue_path, packet = _export_active_enrichment_queue(tmp_path)
    _patch_commercial_source_paths(monkeypatch, packet)
    item = packet["items"][0]  # type: ignore[index]
    job_dir = tmp_path / "enrichment-jobs" / item["followup_id"]
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(
        json.dumps(
            {
                "candidate_key": item["candidate_key"],
                "status": "enriched",
                "confidence": "high",
                "person_summary": "Active Founder runs product delivery at Active Co.",
                "company_name": "Active Co",
                "company_summary": "Active Co runs service delivery operations.",
                "official_company_url": "https://active.example.com",
                "evidence_urls": ["https://active.example.com"],
                "research_evidence": [
                    {
                        "evidence_id": "E1",
                        "source_url": "https://active.example.com",
                        "claim": "Active Co runs service delivery operations.",
                        "relevance": "Relevant to workflow systems work.",
                        "source_excerpt": "Service delivery operations for growing teams.",
                    }
                ],
                "commercial_context": packet["commercial_context"],
                "criterion_evidence": [
                    {
                        "criterion_id": "operational-friction",
                        "assessment": "matched",
                        "evidence_ids": ["E1"],
                        "explanation": "The source establishes a delivery-operations need.",
                    }
                ],
                "unknowns": ["Budget authority is not established."],
                "warnings": [],
                "relationship_role": "buyer",
                "priority": "high",
                "signal_type": "company_site",
                "visible_signal": "Active Co is expanding service delivery.",
                "signal_url": "https://active.example.com",
                "followup_reason": "Delivery handoffs may need stronger systems.",
                "next_useful_action": "Review the relationship priority.",
                "permission_boundary": "review_only",
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "enrichment-decisions.json"

    output = acceptance_collect_enrichment_workers(
        store,
        enrichment_queue=queue_path,
        jobs_dir=tmp_path / "enrichment-jobs",
        out=out,
        offset=0,
        limit=1,
    )

    assert "1 enriched" in output
    decision = json.loads(out.read_text())["decisions"][0]
    assert decision["status"] == "enriched"
    assert decision["research_evidence"][0]["evidence_id"] == "E1"
    assert decision["commercial_context"]["icp_profile_id"] == "icp-v1"
    assert decision["commercial_context"]["offers_profile_id"] == "offers-v1"
    assert decision["commercial_context"]["offer_id"] == "business-systems-audit"
    assert decision["criterion_evidence"][0]["evidence_ids"] == ["E1"]
    assert decision["unknowns"] == ["Budget authority is not established."]
    assert "template_key" not in decision
    assert "proposed_message" not in decision


@pytest.mark.parametrize(
    ("criterion_evidence", "message"),
    [
        ([], "missing=.*operational-friction"),
        (
            [
                {
                    "criterion_id": "undeclared-criterion",
                    "assessment": "unknown",
                    "evidence_ids": [],
                    "explanation": "The source does not establish this fact.",
                }
            ],
            "undeclared=.*undeclared-criterion",
        ),
        (
            [
                {
                    "criterion_id": "operational-friction",
                    "assessment": "unknown",
                    "evidence_ids": [],
                    "explanation": "The source does not establish this fact.",
                },
                {
                    "criterion_id": "operational-friction",
                    "assessment": "unknown",
                    "evidence_ids": [],
                    "explanation": "The source does not establish this fact.",
                },
            ],
            "duplicate criterion IDs",
        ),
    ],
)
def test_collect_enrichment_workers_rejects_invalid_criterion_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    criterion_evidence: list[dict[str, object]],
    message: str,
) -> None:
    store, queue_path, packet = _export_active_enrichment_queue(tmp_path)
    _patch_commercial_source_paths(monkeypatch, packet)
    item = packet["items"][0]  # type: ignore[index]
    job_dir = tmp_path / "enrichment-jobs" / item["followup_id"]
    job_dir.mkdir(parents=True)
    result = _valid_enrichment_result(item, packet)
    result["criterion_evidence"] = criterion_evidence
    (job_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        acceptance_collect_enrichment_workers(
            store,
            enrichment_queue=queue_path,
            jobs_dir=tmp_path / "enrichment-jobs",
            out=tmp_path / "enrichment-decisions.json",
            offset=0,
            limit=1,
        )


def test_collect_enrichment_workers_blocks_when_all_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, queue_path, packet = _export_active_enrichment_queue(tmp_path)
    _patch_commercial_source_paths(monkeypatch, packet)
    out = tmp_path / "pending-enrichment.json"

    with pytest.raises(RuntimeError, match="no completed Codex enrichment worker"):
        acceptance_collect_enrichment_workers(
            store,
            enrichment_queue=queue_path,
            jobs_dir=tmp_path / "enrichment-jobs",
            out=out,
            offset=0,
            limit=1,
        )

    assert not out.exists()


def _followup_record(
    record_id: str,
    name: str,
    status: AcceptanceFollowupStatus,
) -> AcceptanceFollowupRecord:
    return AcceptanceFollowupRecord(
        key=f"source|{name}|https://www.linkedin.com/in/{record_id}",
        id=record_id,
        source="source",
        name=name,
        profile_url=f"https://www.linkedin.com/in/{record_id}",
        accepted_at=datetime(2026, 6, 20, tzinfo=UTC),
        draft=accepted_welcome_message(name.split()[0]),
        status=status,
        report_path="greetings.md",
    )


def test_send_ready_greetings_prints_summary_table(tmp_path: Path) -> None:
    store = Store(tmp_path)
    records = [
        _followup_record("afu_one", "Ready One", AcceptanceFollowupStatus.DRY_RUN_READY),
        _followup_record("afu_two", "Ready Two", AcceptanceFollowupStatus.DRY_RUN_READY),
    ]
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=records))
    browser = SequenceFollowupBrowser(
        tmp_path,
        [FIXTURES / "followup_sent.json", FIXTURES / "followup_sent.json"],
    )

    output = acceptance_send_ready_followups(
        store,
        cast(BrowserClient, browser),
        limit=2,
        allow_send=True,
    )

    assert "Accepted follow-up send summary" in output
    assert "Ready One" in output
    assert "Ready Two" in output
    assert [record.status for record in store.load_acceptance_followup_ledger().drafts] == [
        AcceptanceFollowupStatus.SENT,
        AcceptanceFollowupStatus.SENT,
    ]


def test_retry_send_greeting_dry_runs_then_sends(tmp_path: Path) -> None:
    store = Store(tmp_path)
    record = _followup_record("afu_retry", "Retry Lead", AcceptanceFollowupStatus.SEND_FAILED)
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=[record]))
    browser = SequenceFollowupBrowser(
        tmp_path,
        [FIXTURES / "followup_preview.json", FIXTURES / "followup_sent.json"],
    )

    output = acceptance_retry_send_followup(
        store,
        cast(BrowserClient, browser),
        record_id="afu_retry",
        allow_send=True,
    )

    assert "status=preview-filled dry_run=True" in output
    assert "status=sent-clicked dry_run=False" in output
    assert store.load_acceptance_followup_ledger().drafts[0].status == (
        AcceptanceFollowupStatus.SENT
    )
