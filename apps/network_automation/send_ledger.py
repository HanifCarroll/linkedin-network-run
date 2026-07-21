"""SQLite/JSONL send ledger synchronization and summaries."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    DURABLE_SEND_LEDGER_STATUSES,
    SEND_LEDGER_STATUSES,
    CandidateEvent,
    CandidateStatus,
    Run,
    SendLedgerEntry,
    SendLedgerSummary,
    candidate_counts_as_real_send,
    now_utc,
    send_ledger_attempt_key,
    send_ledger_entry_id,
)
from .store import Store


def network_sends_summary(
    store: Store,
    *,
    date_arg: str = "today",
    timezone_name: str = "local",
    sync_history: bool = False,
    run_id: str | None = None,
) -> SendLedgerSummary:
    synced_entries = 0
    history_logs_scanned = 0
    if sync_history:
        synced_entries, history_logs_scanned = sync_send_ledger_from_history(store)
    timezone = _resolve_send_summary_timezone(timezone_name)
    summary_date = _parse_send_summary_date(date_arg, timezone)
    latest_entries = _latest_send_ledger_entries(store.load_send_ledger_entries())
    if run_id is None:
        entries_for_date = [
            entry
            for entry in latest_entries
            if entry.attempted_at.astimezone(timezone).date() == summary_date
        ]
    else:
        entries_for_date = [entry for entry in latest_entries if entry.run_id == run_id]
    entries_for_date.sort(key=lambda entry: (entry.attempted_at, entry.name, entry.source))
    durable_entries = [entry for entry in entries_for_date if entry.durable]
    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for entry in entries_for_date:
        by_status[entry.status.value] = by_status.get(entry.status.value, 0) + 1
        if entry.durable:
            by_source[entry.source] = by_source.get(entry.source, 0) + 1
    return SendLedgerSummary(
        date=summary_date,
        timezone=timezone_name,
        ledger_path=str(store.send_ledger_storage_path()),
        durable_sent_count=len(durable_entries),
        by_source=dict(sorted(by_source.items())),
        by_status=dict(sorted(by_status.items())),
        provisional_count=sum(
            1 for entry in entries_for_date if entry.status == CandidateStatus.PENDING_PROVISIONAL
        ),
        failed_count=sum(1 for entry in entries_for_date if entry.status == CandidateStatus.FAILED),
        reverted_count=sum(
            1 for entry in entries_for_date if entry.status == CandidateStatus.REVERTED_CONNECT
        ),
        top_up_count=sum(
            1 for entry in entries_for_date if entry.status == CandidateStatus.AUDIT_TOP_UP
        ),
        synced_entries=synced_entries,
        history_logs_scanned=history_logs_scanned,
        entries=entries_for_date,
    )


def sync_send_ledger_from_history(store: Store) -> tuple[int, int]:
    appended = 0
    logs_scanned = 0
    for path in sorted(store.dir.glob("*.jsonl")):
        try:
            run_id = str(path.stem)
            import uuid

            uuid.UUID(run_id)
        except ValueError:
            continue
        logs_scanned += 1
        run_date: date | None = None
        for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"parsing {path} line {line_number}: {exc}") from exc
            if str(entry.get("run_id")) != run_id:
                continue
            entry_at = _event_log_timestamp(entry)
            if run_date is None and entry_at is not None:
                run_date = entry_at.date()
            ledger_entry = _send_ledger_entry_from_history_entry(
                run_id=run_id,
                run_date=run_date,
                entry=entry,
                entry_at=entry_at,
            )
            if ledger_entry is not None and store.append_send_ledger_entry(ledger_entry):
                appended += 1
    return appended, logs_scanned


def _latest_send_ledger_entries(entries: list[SendLedgerEntry]) -> list[SendLedgerEntry]:
    latest: dict[str, SendLedgerEntry] = {}
    for entry in entries:
        existing = latest.get(entry.attempt_key)
        if existing is None or _send_ledger_sort_key(entry) >= _send_ledger_sort_key(existing):
            latest[entry.attempt_key] = entry
    return list(latest.values())


def _send_ledger_sort_key(entry: SendLedgerEntry) -> tuple[datetime, int]:
    event_order = {
        "record-send-result": 1,
        "record-top-up-result": 1,
        "record": 2,
        "confirm-send-result": 3,
    }
    return (
        entry.confirmed_at or entry.attempted_at,
        event_order.get(entry.event_kind, 0),
    )


def _resolve_send_summary_timezone(timezone_name: str) -> tzinfo:
    if timezone_name == "local":
        return datetime.now().astimezone().tzinfo or UTC
    if timezone_name.upper() in {"UTC", "Z"}:
        return UTC
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone {timezone_name!r}") from exc


def _parse_send_summary_date(value: str, timezone: tzinfo) -> date:
    if value == "today":
        return datetime.now(timezone).date()
    if value == "yesterday":
        return datetime.now(timezone).date() - timedelta(days=1)
    return date.fromisoformat(value)


def _event_log_timestamp(entry: dict[str, object]) -> datetime | None:
    at_value = entry.get("at")
    if not isinstance(at_value, str) or not at_value:
        return None
    return datetime.fromisoformat(at_value.replace("Z", "+00:00"))


def _send_ledger_entry_from_history_entry(
    *,
    run_id: str,
    run_date: date | None,
    entry: dict[str, object],
    entry_at: datetime | None,
) -> SendLedgerEntry | None:
    kind = str(entry.get("kind"))
    payload = entry.get("payload")
    event, result_path = _candidate_event_from_history_payload(kind, payload)
    if event is None:
        return None
    confirmed_at = None
    if kind == "confirm-send-result":
        confirmed_at = entry_at or now_utc()
    elif event.status != CandidateStatus.PENDING_PROVISIONAL:
        confirmed_at = event.at
    return _send_ledger_entry_for_event(
        run_id=run_id,
        run_date=run_date,
        event=event,
        event_kind=kind,
        result_path=result_path,
        confirmed_at=confirmed_at,
    )


def _candidate_event_from_history_payload(
    kind: str, payload: object
) -> tuple[CandidateEvent | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    if kind == "record":
        return CandidateEvent.model_validate(payload), None
    if kind not in {"record-send-result", "record-top-up-result", "confirm-send-result"}:
        return None, None
    event_payload = payload.get("event")
    if not isinstance(event_payload, dict):
        return None, None
    result_path = payload.get("path")
    if result_path is None:
        result_path = payload.get("out")
    return CandidateEvent.model_validate(event_payload), (
        str(result_path) if result_path is not None else None
    )


def _append_send_ledger_event(
    store: Store,
    run: Run,
    event: CandidateEvent,
    *,
    event_kind: str,
    result_path: str | None = None,
    confirmed_at: datetime | None = None,
) -> None:
    entry = _send_ledger_entry_for_event(
        run_id=str(run.id),
        run_date=run.date,
        event=event,
        event_kind=event_kind,
        result_path=result_path,
        confirmed_at=confirmed_at,
    )
    if entry is not None:
        store.append_send_ledger_entry(entry)


def _send_ledger_entry_for_event(
    *,
    run_id: str,
    run_date: date | None,
    event: CandidateEvent,
    event_kind: str,
    result_path: str | None,
    confirmed_at: datetime | None,
) -> SendLedgerEntry | None:
    if not _candidate_event_belongs_in_send_ledger(event, event_kind):
        return None
    attempt_key = send_ledger_attempt_key(run_id, event)
    durable = event.status in DURABLE_SEND_LEDGER_STATUSES
    return SendLedgerEntry(
        entry_id=send_ledger_entry_id(attempt_key, event.status, event_kind),
        attempt_key=attempt_key,
        run_id=run_id,
        run_date=run_date,
        source=event.source,
        name=event.name,
        profile_url=event.profile_url,
        public_profile_url=event.public_profile_url,
        attempted_at=event.at,
        confirmed_at=confirmed_at,
        status=event.status,
        durable=durable,
        reason=event.note,
        event_kind=event_kind,
        result_path=result_path,
    )


def _candidate_event_belongs_in_send_ledger(
    event: CandidateEvent, event_kind: str
) -> bool:
    if event.status not in SEND_LEDGER_STATUSES:
        return False
    if event_kind == "record":
        return event.status in DURABLE_SEND_LEDGER_STATUSES
    if event.status == CandidateStatus.FAILED:
        return candidate_counts_as_real_send(event)
    return event.status in SEND_LEDGER_STATUSES
