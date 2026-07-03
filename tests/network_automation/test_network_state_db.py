from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from apps.network_automation.cli import main as network_main
from apps.network_automation.models import (
    AcceptanceFollowupAttempt,
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceInvitation,
    AcceptanceLedger,
    AcceptanceOutcomeEvent,
    AcceptanceStatus,
    CandidateStatus,
    SendLedgerEntry,
)
from apps.network_automation.service import network_sends_summary
from apps.network_automation.state_db import NetworkStateDb
from apps.network_automation.store import Store, write_json_atomic


def test_network_state_db_schema_migration_is_idempotent(tmp_path: Path) -> None:
    state_db = NetworkStateDb(tmp_path)

    first = state_db.ensure_schema()
    second = state_db.ensure_schema()
    status = state_db.status()

    assert [migration.version for migration in first] == [1]
    assert second == []
    assert status.applied_migrations == (1,)
    assert status.database_path == tmp_path / "network.sqlite"


def test_json_acceptance_ledger_imports_once_without_duplicates(tmp_path: Path) -> None:
    store = Store(tmp_path)
    ledger = AcceptanceLedger(invitations=[_accepted_invitation("One Lead")])
    write_json_atomic(
        store.acceptance_ledger_path,
        ledger.model_dump(mode="json", by_alias=False),
    )

    store.migrate_json_ledgers()
    store.migrate_json_ledgers()

    status = store.state_db_status()
    hydrated = store.load_acceptance_ledger()
    assert status.acceptance_invitations == 1
    assert status.acceptance_outcome_events == 1
    assert hydrated.model_dump(mode="json") == ledger.model_dump(mode="json")


def test_followup_attempts_and_statuses_hydrate_from_sqlite(tmp_path: Path) -> None:
    store = Store(tmp_path)
    records = [
        _followup_record("afu_ready", AcceptanceFollowupStatus.DRY_RUN_READY),
        _followup_record("afu_conversation", AcceptanceFollowupStatus.CONVERSATION_EXISTS),
        _followup_record("afu_sent", AcceptanceFollowupStatus.SENT),
        _followup_record("afu_not_messageable", AcceptanceFollowupStatus.NOT_MESSAGEABLE),
    ]
    records[0].attempts.append(
        AcceptanceFollowupAttempt(
            at=datetime(2026, 7, 2, 13, 0, tzinfo=UTC),
            dry_run=True,
            status="preview-filled",
            result_url="https://www.linkedin.com/in/ready",
            note="preview filled",
            out_path="/tmp/ready.json",
            diagnostics={"body": '{"ok":true}'},
        )
    )
    records[2].sent_at = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    followups = AcceptanceFollowupLedger(drafts=records)
    write_json_atomic(
        store.acceptance_followup_ledger_path,
        followups.model_dump(mode="json", by_alias=False),
    )

    store.migrate_json_ledgers()

    hydrated = store.load_acceptance_followup_ledger()
    assert [record.status for record in hydrated.drafts] == [
        AcceptanceFollowupStatus.DRY_RUN_READY,
        AcceptanceFollowupStatus.CONVERSATION_EXISTS,
        AcceptanceFollowupStatus.SENT,
        AcceptanceFollowupStatus.NOT_MESSAGEABLE,
    ]
    assert hydrated.drafts[0].attempts[0].status == "preview-filled"
    assert hydrated.drafts[0].attempts[0].diagnostics == {"body": '{"ok":true}'}
    assert hydrated.drafts[2].sent_at == datetime(2026, 7, 2, 14, 0, tzinfo=UTC)


def test_network_sends_summary_matches_legacy_jsonl_after_sqlite_migration(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    entry = SendLedgerEntry(
        entry_id="entry-1",
        attempt_key="attempt-1",
        run_id=str(uuid.uuid4()),
        run_date=date(2026, 7, 2),
        source="ASAP - Agency Owners Delivery",
        name="Sent Lead",
        profile_url="https://www.linkedin.com/in/sent-lead",
        attempted_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
        confirmed_at=datetime(2026, 7, 2, 10, 1, tzinfo=UTC),
        status=CandidateStatus.PENDING,
        durable=True,
        reason="confirmed",
        event_kind="confirm-send-result",
        result_path="/tmp/confirm.json",
    )
    store.send_ledger_path.write_text(
        json.dumps(
            {
                "at": "2026-07-02T10:01:00+00:00",
                **entry.model_dump(mode="json", by_alias=False),
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    before = network_sends_summary(
        store,
        date_arg="2026-07-02",
        timezone_name="UTC",
    )
    store.migrate_json_ledgers()
    after = network_sends_summary(
        store,
        date_arg="2026-07-02",
        timezone_name="UTC",
    )

    assert before.durable_sent_count == 1
    assert after.durable_sent_count == before.durable_sent_count
    assert after.by_source == before.by_source
    assert after.entries[0].entry_id == "entry-1"
    assert after.ledger_path == str(store.database_path)


def test_store_send_ledger_append_imports_jsonl_once(tmp_path: Path) -> None:
    store = Store(tmp_path)
    existing = _send_entry("entry-1", "attempt-1", "Existing Lead")
    appended = _send_entry("entry-2", "attempt-2", "Appended Lead")
    store.send_ledger_path.write_text(
        json.dumps(
            {
                "at": "2026-07-02T10:01:00+00:00",
                **existing.model_dump(mode="json", by_alias=False),
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    assert store.append_send_ledger_entry(existing) is False
    assert store.append_send_ledger_entry(appended) is True
    assert store.append_send_ledger_entry(appended) is False

    status = store.state_db_status()
    entries = store.load_send_ledger_entries()
    assert status.send_ledger_entries == 2
    assert [entry.entry_id for entry in entries] == ["entry-1", "entry-2"]


def test_network_state_migrate_sqlite_cli_dry_run(capsys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store = Store(tmp_path)
    ledger = AcceptanceLedger(invitations=[_accepted_invitation("CLI Lead")])
    write_json_atomic(
        store.acceptance_ledger_path,
        ledger.model_dump(mode="json", by_alias=False),
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "state",
            "migrate-sqlite",
            "--dry-run",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["acceptance_invitations"] == 1
    assert payload["database_path"] == str(tmp_path / "network.sqlite")


def _accepted_invitation(name: str) -> AcceptanceInvitation:
    checked_at = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    return AcceptanceInvitation(
        run_id=uuid.uuid4(),
        run_date=date(2026, 6, 24),
        source="ASAP - Agency Owners Delivery",
        name=name,
        profile_url=f"https://www.linkedin.com/sales/lead/{name.lower().replace(' ', '-')}",
        public_profile_url=f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
        sent_at=datetime(2026, 6, 24, 12, 0, tzinfo=UTC),
        latest_status=AcceptanceStatus.ACCEPTED,
        latest_checked_at=checked_at,
        history=[
            AcceptanceOutcomeEvent(
                at=checked_at,
                status=AcceptanceStatus.ACCEPTED,
                relationship="1st",
                evidence=name,
                note="accepted",
            )
        ],
    )


def _followup_record(
    record_id: str,
    status: AcceptanceFollowupStatus,
) -> AcceptanceFollowupRecord:
    return AcceptanceFollowupRecord(
        key=f"source|{record_id}|https://www.linkedin.com/in/{record_id}",
        id=record_id,
        source="ASAP - Agency Owners Delivery",
        name=record_id.replace("_", " ").title(),
        profile_url=f"https://www.linkedin.com/in/{record_id}",
        sales_nav_profile_url=f"https://www.linkedin.com/sales/lead/{record_id}",
        accepted_at=datetime(2026, 7, 2, 12, 0, tzinfo=UTC),
        angle="product-engineering support ask",
        draft="Hey, thanks for connecting.",
        status=status,
        report_path="/tmp/followups.md",
    )


def _send_entry(entry_id: str, attempt_key: str, name: str) -> SendLedgerEntry:
    return SendLedgerEntry(
        entry_id=entry_id,
        attempt_key=attempt_key,
        run_id=str(uuid.uuid4()),
        run_date=date(2026, 7, 2),
        source="ASAP - Agency Owners Delivery",
        name=name,
        profile_url=f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
        attempted_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
        confirmed_at=datetime(2026, 7, 2, 10, 1, tzinfo=UTC),
        status=CandidateStatus.PENDING,
        durable=True,
        reason="confirmed",
        event_kind="confirm-send-result",
        result_path="/tmp/confirm.json",
    )
