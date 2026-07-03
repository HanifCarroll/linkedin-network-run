from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from apps.network_automation.acceptance_service import (
    acceptance_draft_followups,
    acceptance_import,
    acceptance_retry_send_followup,
    acceptance_send_followup,
    acceptance_send_ready_followups,
)
from apps.network_automation.browser import BrowserClient, FixtureBrowserClient
from apps.network_automation.models import (
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceLedger,
    AcceptanceStatus,
    AcceptedFollowupTemplateKey,
    CandidateEvent,
    CandidateObservation,
    CandidateStatus,
    DraftStrategy,
)
from apps.network_automation.store import Store

from .helpers import (
    FIXTURES,
    SequenceFollowupBrowser,
    _run_id,
)


def test_acceptance_import_downgrades_mismatched_identity(tmp_path: Path) -> None:
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)

    acceptance_import(store, FIXTURES / "acceptance_outcomes_mismatch.json")

    updated = store.load_acceptance_ledger()
    assert updated.invitations[0].latest_status == AcceptanceStatus.UNKNOWN
    assert "downgraded to unknown" in (updated.invitations[0].history[0].note or "")


def test_acceptance_drafts_and_followup_send_guards(tmp_path: Path) -> None:
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    acceptance_import(store, FIXTURES / "acceptance_outcomes.json")
    lead_ledger = store.load_lead_ledger()
    lead_record = lead_ledger.upsert_observation(
        CandidateObservation(
            source="ASAP - Agency Owners Delivery",
            index=1,
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup",
            menu_state="connectable",
        )
    )
    lead_ledger.approve(lead_record.lead_key, "agency owner with delivery work")
    store.save_lead_ledger(lead_ledger)
    report_path = tmp_path / "followups.md"

    output = acceptance_draft_followups(
        store,
        research=FIXTURES / "accepted_research.json",
        out=report_path,
        include_drafted=False,
        strategy=DraftStrategy.ASAP_CONTRACT_V1,
    )

    assert "accepted follow-up drafts: 1" in output
    rendered = report_path.read_text()
    assert "Hey, Duplicate. Thanks for connecting." in rendered
    assert "works across web and mobile products" in rendered
    assert "project overflow, prototypes, and AI-enabled product builds" in rendered
    assert "Are you the right person to ask about this kind of project support?" in rendered
    assert "HC Studio LLC" not in rendered
    assert "- Template: `agency`" in rendered
    assert "Person does: Duplicate Lead is listed as AI Product Leader at Acme AI." in rendered
    assert (
        "Company does: Acme AI: Acme AI builds workflow automation products for service teams."
        in rendered
    )
    assert "Why this draft fits:" in rendered
    assert "Company website: https://www.acme-ai.example" in rendered
    assert "Original connection source: ASAP - Agency Owners Delivery" in rendered
    assert "Original connection approval: agency owner with delivery work" in rendered
    review_packet = json.loads(report_path.with_suffix(".review.json").read_text())
    assert review_packet["items"][0]["person_does"] == (
        "Duplicate Lead is listed as AI Product Leader at Acme AI."
    )
    assert review_packet["items"][0]["company_website_url"] == "https://www.acme-ai.example"
    assert review_packet["items"][0]["research"]["company_profile"]["name"] == "Acme AI"
    followups = store.load_acceptance_followup_ledger()
    record = followups.drafts[0]
    assert record.template_key == AcceptedFollowupTemplateKey.AGENCY
    assert record.person_does == "Duplicate Lead is listed as AI Product Leader at Acme AI."
    assert record.company_website_url == "https://www.acme-ai.example"
    assert record.profile_url == "https://www.linkedin.com/in/duplicate-lead"
    assert record.sales_nav_profile_url == "https://www.linkedin.com/sales/lead/dup"
    with pytest.raises(ValueError, match="real sends require dry_run_ready"):
        acceptance_send_followup(
            store,
            FixtureBrowserClient(followup_result=FIXTURES / "followup_sent.json"),
            record_id=record.id,
            dry_run=False,
            preview_fill=False,
            allow_send=True,
        )

    preview = acceptance_send_followup(
        store,
        FixtureBrowserClient(followup_result=FIXTURES / "followup_preview.json"),
        record_id=record.id,
        dry_run=True,
        preview_fill=True,
        allow_send=False,
    )

    assert "status=preview-filled" in preview
    assert store.load_acceptance_followup_ledger().drafts[0].status.value == "dry_run_ready"


def _followup_record(
    record_id: str, name: str, status: AcceptanceFollowupStatus
) -> AcceptanceFollowupRecord:
    return AcceptanceFollowupRecord(
        key=f"source|{name}|https://www.linkedin.com/in/{record_id}",
        id=record_id,
        source="source",
        name=name,
        profile_url=f"https://www.linkedin.com/in/{record_id}",
        accepted_at=datetime(2026, 6, 20, tzinfo=UTC),
        angle="general",
        draft=f"Hey, {name}. Thanks for connecting.",
        status=status,
        report_path="followups.md",
    )


def test_acceptance_send_ready_followups_prints_summary_table(tmp_path: Path) -> None:
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
        store, cast(BrowserClient, browser), limit=2, allow_send=True
    )

    assert "Accepted follow-up send summary" in output
    assert "Ready One" in output
    assert "Ready Two" in output
    assert "sent" in output
    assert [call["allow_send"] for call in browser.calls] == [True, True]
    assert [record.status for record in store.load_acceptance_followup_ledger().drafts] == [
        AcceptanceFollowupStatus.SENT,
        AcceptanceFollowupStatus.SENT,
    ]


def test_acceptance_retry_send_followup_dry_runs_then_sends(tmp_path: Path) -> None:
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
    assert "Accepted follow-up send summary" in output
    assert "Retry Lead" in output
    calls = browser.calls
    assert [(call["dry_run"], call["allow_send"]) for call in calls] == [
        (True, False),
        (False, True),
    ]
    assert store.load_acceptance_followup_ledger().drafts[0].status == (
        AcceptanceFollowupStatus.SENT
    )


def test_acceptance_draft_followups_explains_zero_new_drafts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    report_path = tmp_path / "followups.md"

    output = acceptance_draft_followups(
        store,
        research=None,
        out=report_path,
        include_drafted=False,
        strategy=DraftStrategy.ASAP_CONTRACT_V1,
    )

    assert "accepted follow-up drafts: 0" in output
    assert "no newly accepted connections need first-message drafts" in output
    assert "No newly accepted connections need first-message drafts." in report_path.read_text()
