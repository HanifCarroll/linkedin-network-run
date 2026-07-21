from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from apps.network_automation.models import (
    AcceptanceLedger,
    AcceptanceStatus,
    CandidateEvent,
    CandidateStatus,
)
from apps.network_automation.store import Store


def test_history_seed_uses_latest_durable_confirmation(tmp_path: Path) -> None:
    store = Store(tmp_path)
    run_id = uuid.uuid4()
    sent_at = datetime(2026, 7, 14, 11, 37, tzinfo=UTC)
    profile_url = "https://www.linkedin.com/sales/lead/confirmed-lead,NAME_SEARCH,abc"
    public_profile_url = "https://www.linkedin.com/in/confirmed-lead"

    provisional = CandidateEvent(
        at=sent_at,
        source="Consulting - Founder Owner Buyers",
        name="Confirmed Lead",
        profile_url=profile_url,
        status=CandidateStatus.PENDING_PROVISIONAL,
    )
    pending = provisional.model_copy(
        update={"status": CandidateStatus.PENDING, "public_profile_url": public_profile_url}
    )
    accepted = provisional.model_copy(
        update={"status": CandidateStatus.ACCEPTED, "public_profile_url": public_profile_url}
    )
    entries = [
        ("2026-07-14T11:37:00+00:00", "record-send-result", provisional),
        ("2026-07-14T11:38:00+00:00", "confirm-send-result", pending),
        ("2026-07-15T05:03:00+00:00", "confirm-send-result", accepted),
    ]
    log_path = tmp_path / f"{run_id}.jsonl"
    log_path.write_text(
        "".join(
            json.dumps(
                {
                    "at": at,
                    "run_id": str(run_id),
                    "kind": kind,
                    "payload": {"event": event.model_dump(mode="json")},
                }
            )
            + "\n"
            for at, kind, event in entries
        ),
        encoding="utf-8",
    )

    ledger = AcceptanceLedger()
    summary = store.seed_acceptance_from_history(ledger)

    assert summary.run_logs == 1
    assert summary.sent_events == 1
    assert summary.seeded == 1
    assert len(ledger.invitations) == 1
    invitation = ledger.invitations[0]
    assert invitation.latest_status == AcceptanceStatus.ACCEPTED
    assert invitation.public_profile_url == public_profile_url
    assert invitation.sent_at == sent_at


def test_history_seed_excludes_send_reverted_after_confirmation(tmp_path: Path) -> None:
    store = Store(tmp_path)
    run_id = uuid.uuid4()
    sent_at = datetime(2026, 7, 14, 11, 37, tzinfo=UTC)
    provisional = CandidateEvent(
        at=sent_at,
        source="Consulting - Founder Owner Buyers",
        name="Reverted Lead",
        profile_url="https://www.linkedin.com/sales/lead/reverted-lead",
        status=CandidateStatus.PENDING_PROVISIONAL,
    )
    pending = provisional.model_copy(update={"status": CandidateStatus.PENDING})
    reverted = provisional.model_copy(update={"status": CandidateStatus.REVERTED_CONNECT})
    entries = [
        ("2026-07-14T11:37:00+00:00", "record-send-result", provisional),
        ("2026-07-14T11:38:00+00:00", "confirm-send-result", pending),
        ("2026-07-15T05:03:00+00:00", "confirm-send-result", reverted),
    ]
    log_path = tmp_path / f"{run_id}.jsonl"
    log_path.write_text(
        "".join(
            json.dumps(
                {
                    "at": at,
                    "run_id": str(run_id),
                    "kind": kind,
                    "payload": {"event": event.model_dump(mode="json")},
                }
            )
            + "\n"
            for at, kind, event in entries
        ),
        encoding="utf-8",
    )

    ledger = AcceptanceLedger()
    summary = store.seed_acceptance_from_history(ledger)

    assert summary.run_logs == 1
    assert summary.sent_events == 0
    assert summary.seeded == 0
    assert ledger.invitations == []
