"""File-backed state store for the network automation port.

This preserves the current controller's JSON state contracts while the shared
SQLite package is still a separate workstream dependency.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from packages.linkedin_common.paths import DEFAULT_STATE_ROOT

from .models import (
    AcceptanceFollowupLedger,
    AcceptanceHistorySeedSummary,
    AcceptanceLedger,
    CandidateEvent,
    CandidateReservoir,
    PendingCleanupRun,
    Run,
)

NETWORK_STATE_DIRNAME = "network-automation"
OLD_NETWORK_STATE_DIRNAME = "linkedin-network-run"


def default_state_dir() -> Path:
    return DEFAULT_STATE_ROOT / NETWORK_STATE_DIRNAME


def old_state_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / OLD_NETWORK_STATE_DIRNAME


class Store:
    def __init__(self, state_dir: Path | str | None = None) -> None:
        self.dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

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
        if not self.acceptance_ledger_path.exists():
            return AcceptanceLedger()
        return read_model(self.acceptance_ledger_path, AcceptanceLedger)

    def save_acceptance_ledger(self, ledger: AcceptanceLedger) -> None:
        write_model_atomic(self.acceptance_ledger_path, ledger)

    def load_acceptance_followup_ledger(self) -> AcceptanceFollowupLedger:
        if not self.acceptance_followup_ledger_path.exists():
            return AcceptanceFollowupLedger()
        return read_model(self.acceptance_followup_ledger_path, AcceptanceFollowupLedger)

    def save_acceptance_followup_ledger(self, ledger: AcceptanceFollowupLedger) -> None:
        write_model_atomic(self.acceptance_followup_ledger_path, ledger)

    def load_reservoir(self) -> CandidateReservoir:
        if not self.reservoir_path.exists():
            return CandidateReservoir()
        return read_model(self.reservoir_path, CandidateReservoir)

    def save_reservoir(self, reservoir: CandidateReservoir) -> None:
        write_model_atomic(self.reservoir_path, reservoir)

    def append_event(self, run: Run, kind: str, payload: object) -> None:
        append_jsonl(
            self.event_path(run), {"run_id": str(run.id), "kind": kind, "payload": payload}
        )

    def append_acceptance_event(self, kind: str, payload: object) -> None:
        append_jsonl(self.acceptance_event_path, {"kind": kind, "payload": payload})

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
    events: list[CandidateEvent] = []
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
        if entry.get("kind") not in {"record-send-result", "record-top-up-result"}:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict) or "event" not in payload:
            continue
        event = CandidateEvent.model_validate(payload["event"])
        if event.status.value in {"pending", "audit-top-up"}:
            events.append(event)
    return run_date, events


def read_only_snapshot[ModelT: BaseModel](
    path: Path, reader: Callable[[Path], ModelT]
) -> ModelT:
    before = path.stat().st_mtime_ns
    model = reader(path)
    after = path.stat().st_mtime_ns
    if before != after:
        raise RuntimeError(f"read-only import hook mutated {path}")
    return model
