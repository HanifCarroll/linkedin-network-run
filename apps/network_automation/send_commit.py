"""Commit send attempt mutations to run, lead, controller, and send ledgers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import (
    CandidateEvent,
    CandidateStatus,
    Run,
    SalesNavSendResult,
    drain_stale_connectable_candidates,
    now_utc,
    record_send_result,
    record_top_up_send_result,
)
from .send_ledger import _append_send_ledger_event
from .store import Store


@dataclass(slots=True)
class SendAttemptCommit:
    run: Run
    event: CandidateEvent
    drained: list[CandidateEvent]


class SendAttemptCommitter:
    """Persist send-attempt changes across the run and derived ledgers."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def commit_manual_record(
        self,
        run: Run,
        event: CandidateEvent,
        *,
        drain_stale: bool = True,
    ) -> SendAttemptCommit:
        drained = drain_stale_connectable_candidates(run) if drain_stale else []
        run.mark_updated()
        self.store.save_run(run)
        self._apply_candidate_events_to_lead_ledger([event, *drained])
        _append_send_ledger_event(
            self.store,
            run,
            event,
            event_kind="record",
            confirmed_at=event.at,
        )
        self.store.append_event(run, "record", event)
        self._append_drained_event(run, drained)
        return SendAttemptCommit(run=run, event=event, drained=drained)

    def commit_send_result(
        self,
        run: Run,
        result: SalesNavSendResult,
        result_path: str | Path,
        *,
        drain_stale: bool = True,
        append_send_ledger: bool = True,
        controller_payload_extra: dict[str, object] | None = None,
        before_save: Callable[[Run, CandidateEvent], None] | None = None,
    ) -> SendAttemptCommit:
        path = str(result_path)
        event = record_send_result(run, result, path)
        drained = drain_stale_connectable_candidates(run) if drain_stale else []
        if before_save is not None:
            before_save(run, event)
        self.store.save_run(run)
        self._apply_candidate_events_to_lead_ledger([event, *drained])
        if append_send_ledger:
            _append_send_ledger_event(
                self.store,
                run,
                event,
                event_kind="record-send-result",
                result_path=path,
                confirmed_at=(
                    None if event.status == CandidateStatus.PENDING_PROVISIONAL else event.at
                ),
            )
        payload: dict[str, object] = {"path": path, "event": event}
        if controller_payload_extra:
            payload.update(controller_payload_extra)
        self.store.append_event(run, "record-send-result", payload)
        self._append_drained_event(run, drained)
        return SendAttemptCommit(run=run, event=event, drained=drained)

    def commit_confirmation_result(
        self,
        run: Run,
        event: CandidateEvent,
        *,
        input_path: str | Path,
        outcome_path: str | Path,
        status: CandidateStatus,
        confirmation: str,
    ) -> SendAttemptCommit:
        run.mark_updated()
        self.store.save_run(run)
        self._apply_candidate_events_to_lead_ledger([event])
        _append_send_ledger_event(
            self.store,
            run,
            event,
            event_kind="confirm-send-result",
            result_path=str(outcome_path),
            confirmed_at=now_utc(),
        )
        self.store.append_event(
            run,
            "confirm-send-result",
            {
                "input": str(input_path),
                "out": str(outcome_path),
                "event": event,
                "status": status.value,
                "confirmation": confirmation,
            },
        )
        return SendAttemptCommit(run=run, event=event, drained=[])

    def commit_top_up_result(
        self,
        run: Run,
        result: SalesNavSendResult,
        result_path: str | Path,
        *,
        note: str | None = None,
    ) -> SendAttemptCommit:
        path = str(result_path)
        event = record_top_up_send_result(run, result, path, note)
        self.store.save_run(run)
        self._apply_candidate_events_to_lead_ledger([event])
        _append_send_ledger_event(
            self.store,
            run,
            event,
            event_kind="record-top-up-result",
            result_path=path,
            confirmed_at=None if event.status == CandidateStatus.PENDING_PROVISIONAL else event.at,
        )
        self.store.append_event(run, "record-top-up-result", {"path": path, "event": event})
        return SendAttemptCommit(run=run, event=event, drained=[])

    def _apply_candidate_events_to_lead_ledger(
        self, events: list[CandidateEvent]
    ) -> None:
        if not events:
            return
        ledger = self.store.load_lead_ledger()
        for event in events:
            ledger.apply_candidate_event(event)
        self.store.save_lead_ledger(ledger)

    def _append_drained_event(self, run: Run, drained: list[CandidateEvent]) -> None:
        if drained:
            self.store.append_event(run, "drain-stale-candidates", {"events": drained})
