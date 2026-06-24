from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from apps.network_automation.browser import FixtureBrowserClient
from apps.network_automation.cli import main as network_main
from apps.network_automation.models import (
    AcceptanceLedger,
    AcceptanceStatus,
    CandidateEvent,
    CandidateStatus,
    DraftStrategy,
    RunState,
    default_sources,
)
from apps.network_automation.old_state import inspect_old_state
from apps.network_automation.service import (
    acceptance_draft_followups,
    acceptance_import,
    acceptance_send_followup,
    finish_run,
    import_audit,
    import_capture_path,
    pending_cleanup_finish,
    pending_cleanup_import_audit,
    pending_cleanup_import_capture,
    pending_cleanup_record_withdraw_result,
    pending_cleanup_start,
    pending_cleanup_withdraw_next,
    send_guarded,
    send_next,
    start_run,
)
from apps.network_automation.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "network_automation"


def test_default_source_mix_matches_current_contract() -> None:
    sources = default_sources(30)
    assert [(source.name, source.target) for source in sources[:5]] == [
        ("ASAP - Agency Owners Delivery", 9),
        ("ASAP - Contract Recruiters Staffing", 7),
        ("ASAP - Startup CTO Eng Leaders", 6),
        ("ASAP - High-Intent SaaS AI Founders", 5),
        ("ASAP - Vertical Proof Buyers", 3),
    ]
    assert sources[5].name == "FO - Founders - Urgent"
    assert sources[5].fallback is True


def test_capture_import_dedupes_and_derives_salesnav_profile_url(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=22, run_date=date(2026, 6, 24), force=True)

    message = import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)

    run = store.load_run()
    assert message == "imported 2 candidate observations"
    assert [observation.name for observation in run.observations] == ["Duplicate Lead", "URN Lead"]
    assert run.observations[0].profile_url == "https://www.linkedin.com/sales/lead/dup,SEARCH,y"
    assert run.observations[1].profile_url == (
        "https://www.linkedin.com/sales/lead/"
        "ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt"
    )
    plan = run.operator_plan()
    assert plan.action == "send-candidate"
    assert plan.name == "Duplicate Lead"
    resume_url = run.capture_cursors["ASAP - Agency Owners Delivery"].resume_url
    assert resume_url is not None
    assert resume_url.endswith("page=2")


def test_guarded_connection_send_preserves_real_send_gate(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    browser = FixtureBrowserClient(send_result=FIXTURES / "send_pending.json")

    with pytest.raises(RuntimeError, match="real guarded sends require --allow-send"):
        send_guarded(store, browser, dry_run=False, allow_send=False)

    output = send_next(store, browser, dry_run=False, allow_send=True)

    run = store.load_run()
    assert "recorded pending" in output
    assert run.verified_count() == 1
    assert run.state == RunState.FINAL_RECONCILE


def test_finish_requires_matching_audit_delta_and_seeds_acceptance(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    send_next(
        store,
        FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
        dry_run=False,
        allow_send=True,
    )
    import_audit(store, FIXTURES / "audit_100.json")
    with pytest.raises(RuntimeError, match="final audit delta"):
        finish_run(store)
    import_audit(store, FIXTURES / "audit_101.json")

    report = finish_run(store)

    assert "State: `Done`" in report
    ledger = store.load_acceptance_ledger()
    assert len(ledger.invitations) == 1
    assert ledger.invitations[0].name == "Duplicate Lead"


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
    assert "project overflow, prototypes, and AI-enabled product builds" in rendered
    followups = store.load_acceptance_followup_ledger()
    record = followups.drafts[0]
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


def test_pending_cleanup_honors_threshold_and_audit_backed_finish(tmp_path: Path) -> None:
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=1, threshold_days=14, force=True)
    pending_cleanup_import_capture(store, FIXTURES / "pending_capture.json")
    run = store.load_pending()
    next_pending = run.next_eligible_observation()
    assert next_pending is not None
    assert next_pending.name == "Stale Invite"

    dry = pending_cleanup_withdraw_next(
        store,
        FixtureBrowserClient(withdraw_result=FIXTURES / "withdraw_dry_run.json"),
        dry_run=True,
        allow_withdraw=False,
    )
    assert "dry_run=True" in dry
    assert store.load_pending().withdrawn_count() == 0

    pending_cleanup_record_withdraw_result(store, FIXTURES / "withdraw_result.json")
    pending_cleanup_import_audit(store, FIXTURES / "audit_200.json")
    with pytest.raises(RuntimeError, match="final audit delta"):
        pending_cleanup_finish(store)
    pending_cleanup_import_audit(store, FIXTURES / "audit_199.json")
    report = pending_cleanup_finish(store)
    assert "State: `Done`" in report
    assert "Stale Invite" in report


def test_cli_namespace_runs_network_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert network_main(["--state-dir", str(tmp_path), "start", "--target", "1", "--force"]) == 0
    assert network_main(["--state-dir", str(tmp_path), "plan", "--json"]) == 0
    output = capsys.readouterr().out
    assert "started run" in output
    assert '"action": "capture-source"' in output


def test_old_state_inspection_is_read_only(tmp_path: Path) -> None:
    old_store = Store(tmp_path)
    start_run(old_store, target=1, run_date=date(2026, 6, 24), force=True)
    active = old_store.active_path
    before = active.stat().st_mtime_ns

    snapshot = inspect_old_state(tmp_path)

    assert snapshot.active_run is not None
    assert active.stat().st_mtime_ns == before


def _run_id() -> uuid.UUID:
    return uuid.uuid4()
