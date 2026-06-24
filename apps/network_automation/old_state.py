"""Read-only hooks for old linkedin-network-run state."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .models import AcceptanceFollowupLedger, AcceptanceLedger, CandidateReservoir, Run
from .store import old_state_dir, read_model, read_only_snapshot


class OldStateSnapshot(BaseModel):
    state_dir: str
    active_run: Run | None = None
    acceptance_ledger: AcceptanceLedger | None = None
    acceptance_followups: AcceptanceFollowupLedger | None = None
    reservoir: CandidateReservoir | None = None
    warnings: list[str] = []


def inspect_old_state(state_dir: Path | None = None) -> OldStateSnapshot:
    root = state_dir or old_state_dir()
    warnings: list[str] = []

    def optional_model[T: BaseModel](path: Path, model: type[T]) -> T | None:
        if not path.exists():
            warnings.append(f"missing {path.name}")
            return None
        return read_only_snapshot(path, lambda target: read_model(target, model))

    return OldStateSnapshot(
        state_dir=str(root),
        active_run=optional_model(root / "active.json", Run),
        acceptance_ledger=optional_model(root / "acceptance-ledger.json", AcceptanceLedger),
        acceptance_followups=optional_model(
            root / "acceptance-followups.json", AcceptanceFollowupLedger
        ),
        reservoir=optional_model(root / "candidate-reservoir.json", CandidateReservoir),
        warnings=warnings,
    )
