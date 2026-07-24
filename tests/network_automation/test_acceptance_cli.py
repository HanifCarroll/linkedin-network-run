from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.network_automation.browser import PlaywriterBrowserClient
from apps.network_automation.cli import build_parser
from apps.network_automation.cli import main as network_main
from apps.network_automation.models import (
    FOUNDER_OWNER_BUYERS_LEAD_LIST,
    FOUNDER_OWNER_BUYERS_SOURCE,
    AcceptanceEvidenceGrade,
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceInvitation,
    AcceptanceLeadListStatus,
    AcceptanceLedger,
    AcceptanceOutcomeEvent,
    AcceptanceRelationshipStatus,
    AcceptanceStatus,
    CandidateEvent,
    CandidateStatus,
    GreetingEligibilityStatus,
    RelationshipRole,
    accepted_welcome_message,
)
from apps.network_automation.store import Store

from .helpers import (
    FIXTURES,
    FakeLiveBrowserClient,
    _install_fake_live_browser,
    _run_id,
    _write_fake_artifact,
)


def _seed_pending_invitation(store: Store, name: str = "Duplicate Lead") -> None:
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name=name,
            profile_url=f"https://www.linkedin.com/sales/lead/{name.lower().replace(' ', '-')}",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)


def test_cli_acceptance_report_emits_daily_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pending_invitation(Store(tmp_path))

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "report",
            "--daily-days",
            "7",
            "--timezone",
            "UTC",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["daily_timezone"] == "UTC"
    assert len(report["daily"]) == 7
    assert report["daily"][-1]["coverage_complete"] is None
    assert report["daily_windows"] == [
        {
            "days": 7,
            "newly_confirmed_accepted": 0,
            "per_calendar_day": 0.0,
            "complete_days": 0,
            "missing_or_incomplete_days": 7,
        }
    ]


def test_cli_invalidate_weak_message_acceptances_is_guarded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
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
    store.save_acceptance_ledger(AcceptanceLedger(invitations=[invitation]))
    store.save_acceptance_followup_ledger(
        AcceptanceFollowupLedger(
            drafts=[
                AcceptanceFollowupRecord(
                    key=invitation.key(),
                    id="afu_weak",
                    source=invitation.source,
                    name=invitation.name,
                    profile_url=invitation.profile_url,
                    accepted_at=datetime(2026, 7, 2, tzinfo=UTC),
                    draft=accepted_welcome_message("Weak"),
                    status=AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                    report_path="greetings.md",
                )
            ]
        )
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "invalidate-weak-message-acceptances",
            "--sample-limit",
            "1",
        ]
    )
    assert exit_code == 0
    assert "dry-run: 1 invitation(s), 1 follow-up draft(s)" in capsys.readouterr().out
    assert store.load_acceptance_ledger().invitations[0].latest_status == (
        AcceptanceStatus.ACCEPTED
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "invalidate-weak-message-acceptances",
            "--apply",
            "--sample-limit",
            "1",
        ]
    )
    assert exit_code == 0
    assert "applied: 1 invitation(s), 1 follow-up draft(s)" in capsys.readouterr().out
    assert store.load_acceptance_ledger().invitations[0].latest_status == (
        AcceptanceStatus.INVALIDATED
    )
    assert store.load_acceptance_followup_ledger().drafts[0].status == (
        AcceptanceFollowupStatus.INVALID_ACCEPTANCE
    )


@pytest.mark.skip(reason="profile-by-profile acceptance scanning was removed")
def test_cli_acceptance_check_uses_live_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_live_browser(monkeypatch)
    candidates = tmp_path / "candidates.json"
    out = tmp_path / "outcomes.json"
    candidates.write_text(
        json.dumps(
            [
                {
                    "run_id": str(_run_id()),
                    "run_date": "2026-06-24",
                    "source": "ASAP - Agency Owners Delivery",
                    "name": "Duplicate Lead",
                    "profile_url": "https://www.linkedin.com/sales/lead/dup",
                    "sent_at": "2026-06-16T12:00:00Z",
                    "latest_status": "sent",
                    "latest_checked_at": None,
                }
            ]
        )
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "check",
            "--in",
            str(candidates),
            "--out",
            str(out),
            "--limit",
            "1",
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "acceptance-check:1:offset=0:limit=1:delay=500"
    ]
    assert json.loads(out.read_text())["rows"][0]["status"] == "accepted"


@pytest.mark.skip(reason="replaced by two-list baseline and delta reconciliation")
def test_cli_daily_session_is_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    _seed_pending_invitation(store)
    candidates = tmp_path / "acceptance-candidates.json"
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"
    browser_out_dir = tmp_path / "acceptance-session"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(candidates),
            "--outcomes-out",
            str(outcomes),
            "--chunk-dir",
            str(chunks),
            "--chunk-size",
            "1",
            "--out-dir",
            str(browser_out_dir),
        ]
    )

    assert exit_code == 0
    assert len(FakeLiveBrowserClient.instances) == 1
    assert FakeLiveBrowserClient.instances[0].calls == [
        "acceptance-check:1:offset=0:limit=1:delay=750"
    ]
    assert json.loads(outcomes.read_text())["rows"][0]["status"] == "accepted"
    assert store.load_acceptance_followup_ledger().drafts == []
    daily_runs = store.load_acceptance_daily_runs()
    assert len(daily_runs) == 1
    assert daily_runs[0].coverage_complete is True
    assert daily_runs[0].eligible == 1
    assert daily_runs[0].checked == 1
    assert daily_runs[0].newly_confirmed_accepted == 1
    output = capsys.readouterr().out
    assert "acceptance check: chunk 1/1 completed with 1 row(s)" in output
    assert "daily acceptance coverage: complete" in output


@pytest.mark.skip(reason="replaced by exact two-list reconciliation")
def test_cli_reconciles_legacy_acceptance_and_restores_followup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_status = "accepted"
    store = Store(tmp_path)
    accepted_at = datetime(2026, 6, 17, tzinfo=UTC)
    invitation = AcceptanceInvitation(
        run_id=_run_id(),
        run_date=date(2026, 6, 10),
        source=FOUNDER_OWNER_BUYERS_SOURCE,
        name="Legacy Buyer",
        profile_url="https://www.linkedin.com/sales/lead/legacy-buyer",
        sent_at=datetime(2026, 6, 10, tzinfo=UTC),
        latest_status=AcceptanceStatus.ACCEPTED,
        latest_checked_at=accepted_at,
        first_observed_accepted_at=accepted_at,
        history=[
            AcceptanceOutcomeEvent(
                at=accepted_at,
                status=AcceptanceStatus.ACCEPTED,
                relationship="1st",
                evidence="generic visible page text",
                note="lead page shows 1st-degree relationship",
            )
        ],
    )
    store.save_acceptance_ledger(AcceptanceLedger(invitations=[invitation]))
    store.save_acceptance_followup_ledger(
        AcceptanceFollowupLedger(
            drafts=[
                AcceptanceFollowupRecord(
                    key=invitation.key(),
                    id="afu_legacy_buyer",
                    source=invitation.source,
                    name=invitation.name,
                    profile_url=invitation.profile_url,
                    accepted_at=accepted_at,
                    draft=accepted_welcome_message("Legacy"),
                    greeting_eligibility_status=GreetingEligibilityStatus.ELIGIBLE,
                    report_path="welcome.md",
                )
            ]
        )
    )
    candidates = tmp_path / "reconciliation" / "candidates.json"
    outcomes = tmp_path / "reconciliation" / "outcomes.json"
    chunks = tmp_path / "reconciliation" / "chunks"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "reconcile-acceptances",
            "--candidates-out",
            str(candidates),
            "--outcomes-out",
            str(outcomes),
            "--chunk-dir",
            str(chunks),
            "--chunk-size",
            "1",
        ]
    )

    assert exit_code == 0
    updated = store.load_acceptance_ledger().invitations[0]
    followup = store.load_acceptance_followup_ledger().drafts[0]
    assert updated.latest_status == AcceptanceStatus.ACCEPTED
    assert (
        updated.acceptance_evidence_grade
        == AcceptanceEvidenceGrade.STRUCTURED_FIRST_DEGREE
    )
    assert (
        updated.current_relationship_status
        == AcceptanceRelationshipStatus.FIRST_DEGREE
    )
    assert followup.status == AcceptanceFollowupStatus.DRAFTED
    assert json.loads(candidates.read_text())[0]["reconciliation"] is True
    assert (
        "acceptance reconciliation complete: checked=1, restored_followups=1, remaining=0"
        in capsys.readouterr().out
    )


@pytest.mark.skip(reason="profile-by-profile chunk retries were removed")
def test_cli_daily_session_retries_then_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_failures_remaining = 3
    store = Store(tmp_path)
    _seed_pending_invitation(store, "Retry Lead")
    outcomes = tmp_path / "acceptance-outcomes.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(tmp_path / "acceptance-candidates.json"),
            "--outcomes-out",
            str(outcomes),
            "--chunk-dir",
            str(tmp_path / "chunks"),
            "--chunk-size",
            "1",
        ]
    )

    assert exit_code == 0
    assert "attempt 4/4" in capsys.readouterr().out
    assert outcomes.exists()
    assert FakeLiveBrowserClient.instances[0].recoveries == 3


@pytest.mark.skip(reason="profile API scanning was removed")
def test_cli_daily_session_stops_immediately_on_profile_api_rate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_rate_limited = True
    store = Store(tmp_path)
    _seed_pending_invitation(store, "Rate Limited Lead")

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(tmp_path / "acceptance-candidates.json"),
            "--outcomes-out",
            str(tmp_path / "acceptance-outcomes.json"),
            "--chunk-dir",
            str(tmp_path / "chunks"),
            "--chunk-size",
            "1",
        ]
    )

    assert exit_code == 0
    browser = FakeLiveBrowserClient.instances[0]
    assert len(browser.calls) == 1
    assert browser.recoveries == 0
    output = capsys.readouterr().out
    assert "profile API rate limited at HTTP 429" in output
    assert "retry_cursor=0" in output
    daily_run = store.load_acceptance_daily_runs()[0]
    assert daily_run.coverage_complete is False
    assert daily_run.blocker is not None
    assert "HTTP 429" in daily_run.blocker


@pytest.mark.skip(reason="profile-by-profile chunk reuse was removed")
def test_cli_daily_session_does_not_reuse_chunk_for_different_candidate_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    candidates = tmp_path / "acceptance-candidates.json"
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"

    for name in ("First Lead", "Second Lead"):
        _seed_pending_invitation(store, name)
        assert (
            network_main(
                [
                    "--state-dir",
                    str(tmp_path),
                    "acceptance",
                    "run-daily-session",
                    "--candidates-out",
                    str(candidates),
                    "--outcomes-out",
                    str(outcomes),
                    "--chunk-dir",
                    str(chunks),
                    "--chunk-size",
                    "1",
                ]
            )
            == 0
        )

    output = capsys.readouterr().out
    assert len(FakeLiveBrowserClient.instances) == 2
    assert "reused complete acceptance chunk" not in output
    assert json.loads(outcomes.read_text())["rows"][0]["name"] == "Second Lead"


@pytest.mark.skip(reason="profile-by-profile chunks were removed")
def test_cli_daily_session_stops_on_blocked_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_status = "blocked"
    store = Store(tmp_path)
    _seed_pending_invitation(store, "Blocked Lead")
    outcomes = tmp_path / "acceptance-outcomes.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(tmp_path / "acceptance-candidates.json"),
            "--outcomes-out",
            str(outcomes),
            "--chunk-dir",
            str(tmp_path / "chunks"),
            "--chunk-size",
            "1",
        ]
    )

    assert exit_code == 0
    assert "chunk-0.json has 1 blocked rows" in capsys.readouterr().out
    assert not outcomes.exists()
    assert store.load_acceptance_followup_ledger().drafts == []
    daily_runs = store.load_acceptance_daily_runs()
    assert len(daily_runs) == 1
    assert daily_runs[0].coverage_complete is False
    assert daily_runs[0].eligible == 1
    assert daily_runs[0].checked == 0
    assert daily_runs[0].blocker is not None
