"""Browser adapter interfaces for guarded network actions."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import (
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    CandidateObservation,
    PendingCandidateObservation,
    PendingWithdrawResult,
    SalesNavCapture,
    SalesNavSendResult,
)
from .store import read_model


class BrowserClient(Protocol):
    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]: ...

    def capture_salesnav(self, *, source: str) -> tuple[SalesNavCapture, str]: ...

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]: ...

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]: ...


class UnavailableBrowserClient:
    """Default until the browser workstream provides Python Playwright primitives."""

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        _ = candidate, dry_run, allow_send
        raise RuntimeError("browser client is not configured")

    def capture_salesnav(self, *, source: str) -> tuple[SalesNavCapture, str]:
        _ = source
        raise RuntimeError("browser client is not configured")

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        _ = record, dry_run, preview_fill, allow_send
        raise RuntimeError("browser client is not configured")

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        _ = candidate, dry_run, allow_withdraw
        raise RuntimeError("browser client is not configured")


class FixtureBrowserClient:
    """Fixture-backed browser adapter used by parity tests."""

    def __init__(
        self,
        *,
        send_result: Path | None = None,
        capture: Path | None = None,
        followup_result: Path | None = None,
        withdraw_result: Path | None = None,
    ) -> None:
        self.send_result = send_result
        self.capture = capture
        self.followup_result = followup_result
        self.withdraw_result = withdraw_result

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        _ = candidate, dry_run, allow_send
        if self.send_result is None:
            raise RuntimeError("send fixture was not provided")
        return read_model(self.send_result, SalesNavSendResult), str(self.send_result)

    def capture_salesnav(self, *, source: str) -> tuple[SalesNavCapture, str]:
        _ = source
        if self.capture is None:
            raise RuntimeError("capture fixture was not provided")
        return read_model(self.capture, SalesNavCapture), str(self.capture)

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        _ = record, dry_run, preview_fill, allow_send
        if self.followup_result is None:
            raise RuntimeError("follow-up fixture was not provided")
        return read_model(self.followup_result, AcceptanceFollowupSendResult), str(
            self.followup_result
        )

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        _ = candidate, dry_run, allow_withdraw
        if self.withdraw_result is None:
            raise RuntimeError("withdraw fixture was not provided")
        return read_model(self.withdraw_result, PendingWithdrawResult), str(self.withdraw_result)
