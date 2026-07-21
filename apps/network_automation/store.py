"""File-backed state store for the network automation port.

This preserves the current controller's JSON state contracts while the shared
SQLite package is still a separate workstream dependency.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from packages.linkedin_common.paths import DEFAULT_STATE_ROOT

from .models import (
    AcceptanceDailyRun,
    AcceptanceFollowupLedger,
    AcceptanceHistorySeedSummary,
    AcceptanceLedger,
    CandidateEvent,
    CandidateReservoir,
    CandidateStatus,
    LeadLedger,
    PendingCleanupRun,
    Run,
    SendLedgerEntry,
    SourceScanProgressLedger,
    candidate_key,
)
from .state_db import NetworkStateDb, NetworkStateDbStatus, NetworkStateMigrationSummary

NETWORK_STATE_DIRNAME = "network-automation"
OLD_NETWORK_STATE_DIRNAME = "linkedin-network-run"

__all__ = [
    "NetworkStateDbStatus",
    "NetworkStateMigrationSummary",
    "Store",
    "default_state_dir",
    "old_state_dir",
    "read_model",
    "read_only_snapshot",
    "sent_events_from_controller_log",
    "write_json_atomic",
]


def default_state_dir() -> Path:
    return DEFAULT_STATE_ROOT / NETWORK_STATE_DIRNAME


def old_state_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / OLD_NETWORK_STATE_DIRNAME


class Store:
    def __init__(self, state_dir: Path | str | None = None) -> None:
        self.dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_db = NetworkStateDb(self.dir)

    @property
    def active_path(self) -> Path:
        return self.dir / "active.json"

    @property
    def pending_active_path(self) -> Path:
        return self.dir / "pending-cleanup-active.json"

    @property
    def acceptance_ledger_path(self) -> Path:
        return self.dir / "acceptance-ledger.json"

    @property
    def acceptance_followup_ledger_path(self) -> Path:
        return self.dir / "acceptance-followups.json"

    @property
    def acceptance_followup_reports_dir(self) -> Path:
        return self.dir / "acceptance-followups"

    @property
    def acceptance_event_path(self) -> Path:
        return self.dir / "acceptance-events.jsonl"

    @property
    def reservoir_path(self) -> Path:
        return self.dir / "candidate-reservoir.json"

    @property
    def source_progress_path(self) -> Path:
        return self.dir / "source-progress.json"

    @property
    def lead_ledger_path(self) -> Path:
        return self.dir / "lead-ledger.json"

    @property
    def send_ledger_path(self) -> Path:
        return self.dir / "send-ledger.jsonl"

    @property
    def database_path(self) -> Path:
        return self._state_db.path

    @property
    def controller_lock_path(self) -> Path:
        return self.dir / "controller.lock"

    @contextmanager
    def controller_lock(self, operation: str) -> Iterator[None]:
        """Prevent concurrent state-changing controller processes for this state dir."""

        lock_file = self.controller_lock_path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                lock_file.seek(0)
                owner = lock_file.read().strip() or "owner details unavailable"
                raise RuntimeError(
                    f"another controller process is active for {self.dir}: {owner}"
                ) from exc
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "operation": operation,
                    },
                    sort_keys=True,
                )
            )
            lock_file.flush()
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    def state_db_status(self) -> NetworkStateDbStatus:
        return self._state_db.status()

    def preview_json_migration(self) -> NetworkStateMigrationSummary:
        ledger, followups, send_entries, warnings = self._load_json_migration_sources()
        return self._migration_summary(
            dry_run=True,
            ledger=ledger,
            followups=followups,
            send_entries=send_entries,
            warnings=warnings,
        )

    def migrate_json_ledgers(self) -> NetworkStateMigrationSummary:
        ledger, followups, send_entries, warnings = self._load_json_migration_sources()
        if ledger is not None:
            self._state_db.import_acceptance_ledger(ledger)
        if followups is not None:
            self._state_db.import_acceptance_followup_ledger(followups)
        if send_entries:
            self._import_send_ledger_entries(send_entries)
        return self._migration_summary(
            dry_run=False,
            ledger=ledger,
            followups=followups,
            send_entries=send_entries,
            warnings=warnings,
        )

    def send_ledger_storage_path(self) -> Path:
        if self._state_db.has_send_ledger():
            return self.database_path
        return self.send_ledger_path

    def _load_json_migration_sources(
        self,
    ) -> tuple[
        AcceptanceLedger | None,
        AcceptanceFollowupLedger | None,
        list[SendLedgerEntry],
        list[str],
    ]:
        warnings: list[str] = []
        ledger = None
        if self.acceptance_ledger_path.exists():
            ledger = AcceptanceLedger.model_validate_json(
                self.acceptance_ledger_path.read_text()
            )
        else:
            warnings.append(f"missing acceptance ledger JSON: {self.acceptance_ledger_path}")

        followups = None
        if self.acceptance_followup_ledger_path.exists():
            followups = AcceptanceFollowupLedger.model_validate_json(
                self.acceptance_followup_ledger_path.read_text()
            )
        else:
            warnings.append(
                f"missing acceptance follow-up ledger JSON: {self.acceptance_followup_ledger_path}"
            )

        send_entries = self._load_send_ledger_entries_jsonl()
        if not self.send_ledger_path.exists():
            warnings.append(f"missing send ledger JSONL: {self.send_ledger_path}")
        return ledger, followups, send_entries, warnings

    def _migration_summary(
        self,
        *,
        dry_run: bool,
        ledger: AcceptanceLedger | None,
        followups: AcceptanceFollowupLedger | None,
        send_entries: list[SendLedgerEntry],
        warnings: Sequence[str],
    ) -> NetworkStateMigrationSummary:
        return NetworkStateMigrationSummary(
            database_path=self.database_path,
            dry_run=dry_run,
            acceptance_invitations=len(ledger.invitations) if ledger else 0,
            acceptance_outcome_events=sum(
                len(item.history) for item in ledger.invitations
            )
            if ledger
            else 0,
            acceptance_followups=len(followups.drafts) if followups else 0,
            acceptance_followup_attempts=sum(
                len(item.attempts) for item in followups.drafts
            )
            if followups
            else 0,
            send_ledger_entries=len(send_entries),
            warnings=tuple(warnings),
        )

    def default_acceptance_followup_report_path(self) -> Path:
        from .models import today

        return self.acceptance_followup_reports_dir / f"{today().isoformat()}.md"

    def event_path(self, run: Run) -> Path:
        return self.dir / f"{run.id}.jsonl"

    def pending_event_path(self, run: PendingCleanupRun) -> Path:
        return self.dir / f"pending-cleanup-{run.id}.jsonl"

    def load_run(self) -> Run:
        run = read_model(self.active_path, Run)
        run.normalize()
        return run

    def save_run(self, run: Run) -> None:
        run.normalize()
        write_model_atomic(self.active_path, run)

    def load_pending(self) -> PendingCleanupRun:
        run = read_model(self.pending_active_path, PendingCleanupRun)
        run.normalize()
        return run

    def save_pending(self, run: PendingCleanupRun) -> None:
        run.normalize()
        write_model_atomic(self.pending_active_path, run)

    def load_acceptance_ledger(self) -> AcceptanceLedger:
        if self._state_db.has_acceptance_ledger():
            return self._state_db.load_acceptance_ledger()
        if not self.acceptance_ledger_path.exists():
            return AcceptanceLedger()
        return read_model(self.acceptance_ledger_path, AcceptanceLedger)

    def save_acceptance_ledger(self, ledger: AcceptanceLedger) -> None:
        self._state_db.replace_acceptance_ledger(ledger)

    def load_acceptance_daily_runs(self) -> list[AcceptanceDailyRun]:
        return self._state_db.load_acceptance_daily_runs()

    def append_acceptance_daily_run(self, run: AcceptanceDailyRun) -> bool:
        return self._state_db.append_acceptance_daily_run(run)

    def load_acceptance_followup_ledger(self) -> AcceptanceFollowupLedger:
        if self._state_db.has_acceptance_followups():
            return self._state_db.load_acceptance_followup_ledger()
        if not self.acceptance_followup_ledger_path.exists():
            return AcceptanceFollowupLedger()
        return read_model(self.acceptance_followup_ledger_path, AcceptanceFollowupLedger)

    def save_acceptance_followup_ledger(self, ledger: AcceptanceFollowupLedger) -> None:
        self._state_db.replace_acceptance_followup_ledger(ledger)

    def load_reservoir(self) -> CandidateReservoir:
        if not self.reservoir_path.exists():
            return CandidateReservoir()
        return read_model(self.reservoir_path, CandidateReservoir)

    def save_reservoir(self, reservoir: CandidateReservoir) -> None:
        write_model_atomic(self.reservoir_path, reservoir)

    def load_source_progress(self) -> SourceScanProgressLedger:
        if not self.source_progress_path.exists():
            return SourceScanProgressLedger()
        return read_model(self.source_progress_path, SourceScanProgressLedger)

    def save_source_progress(self, progress: SourceScanProgressLedger) -> None:
        write_model_atomic(self.source_progress_path, progress)

    def load_lead_ledger(self) -> LeadLedger:
        if not self.lead_ledger_path.exists():
            return LeadLedger()
        return read_model(self.lead_ledger_path, LeadLedger)

    def save_lead_ledger(self, ledger: LeadLedger) -> None:
        write_model_atomic(self.lead_ledger_path, ledger)

    def load_send_ledger_entries(self) -> list[SendLedgerEntry]:
        if self._state_db.has_send_ledger():
            return self._state_db.load_send_ledger_entries()
        return self._load_send_ledger_entries_jsonl()

    def _load_send_ledger_entries_jsonl(self) -> list[SendLedgerEntry]:
        if not self.send_ledger_path.exists():
            return []
        entries: list[SendLedgerEntry] = []
        for line_number, raw_line in enumerate(
            self.send_ledger_path.read_text().splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entries.append(SendLedgerEntry.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(
                    f"parsing {self.send_ledger_path} line {line_number}: {exc}"
                ) from exc
        return entries

    def append_send_ledger_entry(self, entry: SendLedgerEntry) -> bool:
        self._ensure_send_ledger_sqlite()
        if entry.entry_id in self._sqlite_send_ledger_entry_ids():
            return False
        return self._state_db.append_send_ledger_entry(entry)

    def import_send_ledger_entries(self, entries: Sequence[SendLedgerEntry]) -> int:
        return self._import_send_ledger_entries(entries)

    def _ensure_send_ledger_sqlite(self) -> None:
        if self._state_db.has_send_ledger() or not self.send_ledger_path.exists():
            return
        self._import_send_ledger_entries(
            self._load_send_ledger_entries_jsonl(),
            existing_ids=set(),
        )

    def _import_send_ledger_entries(
        self,
        entries: Sequence[SendLedgerEntry],
        *,
        existing_ids: set[str] | None = None,
    ) -> int:
        seen_ids = (
            set(existing_ids)
            if existing_ids is not None
            else self._sqlite_send_ledger_entry_ids()
        )
        new_entries: list[SendLedgerEntry] = []
        for entry in entries:
            if entry.entry_id in seen_ids:
                continue
            seen_ids.add(entry.entry_id)
            new_entries.append(entry)
        if not new_entries:
            return 0
        return self._state_db.import_send_ledger_entries(new_entries)

    def _sqlite_send_ledger_entry_ids(self) -> set[str]:
        if not self._state_db.has_send_ledger():
            return set()
        return {entry.entry_id for entry in self._state_db.load_send_ledger_entries()}

    def append_event(self, run: Run, kind: str, payload: object) -> None:
        append_jsonl(
            self.event_path(run), {"run_id": str(run.id), "kind": kind, "payload": payload}
        )

    def append_acceptance_event(self, kind: str, payload: object) -> None:
        append_jsonl(self.acceptance_event_path, {"kind": kind, "payload": payload})
        self._state_db.append_event("acceptance", kind, payload)

    def append_pending_event(self, run: PendingCleanupRun, kind: str, payload: object) -> None:
        append_jsonl(
            self.pending_event_path(run), {"run_id": str(run.id), "kind": kind, "payload": payload}
        )

    def seed_acceptance_from_history(
        self, ledger: AcceptanceLedger
    ) -> AcceptanceHistorySeedSummary:
        summary = AcceptanceHistorySeedSummary()
        if not self.dir.exists():
            return summary
        for path in sorted(self.dir.glob("*.jsonl")):
            try:
                run_id = path.stem
                # Pending cleanup and acceptance logs are not run UUID logs.
                import uuid

                parsed_run_id = uuid.UUID(run_id)
            except ValueError:
                continue
            run_date, events = sent_events_from_controller_log(path, run_id)
            if run_date is None:
                continue
            summary.run_logs += 1
            summary.sent_events += len(events)
            summary.seeded += ledger.upsert_from_events(parsed_run_id, run_date, events)
        return summary


def read_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(path.read_text())
    except OSError as exc:
        raise OSError(f"reading {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"parsing {path}: {exc}") from exc


def write_model_atomic(path: Path, model: BaseModel) -> None:
    write_json_atomic(path, model.model_dump(mode="json", by_alias=False))


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(to_jsonable(value), indent=2, sort_keys=False) + "\n")
    tmp.replace(path)


def append_jsonl(path: Path, value: object) -> None:
    from .models import now_utc

    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": now_utc().isoformat(), **as_mapping(value)}
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(to_jsonable(record), separators=(",", ":")) + "\n")


def as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    raise TypeError(f"expected mapping, got {type(value).__name__}")


def to_jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=False)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    return value


def sent_events_from_controller_log(
    path: Path, run_id: str
) -> tuple[date | None, list[CandidateEvent]]:
    latest_events_by_candidate: dict[str, CandidateEvent] = {}
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
        at_value = str(entry.get("at", ""))
        if run_date is None and at_value:
            run_date = date.fromisoformat(at_value[:10])
        if entry.get("kind") not in {
            "record-send-result",
            "record-top-up-result",
            "confirm-send-result",
        }:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict) or "event" not in payload:
            continue
        event = CandidateEvent.model_validate(payload["event"])
        latest_events_by_candidate[
            candidate_key(event.source, event.name, event.profile_url)
        ] = event
    durable_statuses = {
        CandidateStatus.PENDING,
        CandidateStatus.ACCEPTED,
        CandidateStatus.AUDIT_TOP_UP,
    }
    return run_date, [
        event for event in latest_events_by_candidate.values() if event.status in durable_statuses
    ]


def read_only_snapshot[ModelT: BaseModel](
    path: Path, reader: Callable[[Path], ModelT]
) -> ModelT:
    before = path.stat().st_mtime_ns
    model = reader(path)
    after = path.stat().st_mtime_ns
    if before != after:
        raise RuntimeError(f"read-only import hook mutated {path}")
    return model
