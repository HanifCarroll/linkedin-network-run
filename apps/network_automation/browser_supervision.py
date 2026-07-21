"""Controller-owned browser incident and bounded recovery contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .browser import BrowserClient
from .models import (
    AcceptanceCheckCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceLeadListSaveResult,
    AcceptanceOutcomeArtifact,
    BrowserIncident,
    BrowserIncidentStatus,
    BrowserRecoveryAction,
    BrowserRecoveryReceipt,
    BrowserRecoveryStatus,
    CandidateObservation,
    PendingCandidateObservation,
    PendingCapture,
    PendingWithdrawBatchResult,
    PendingWithdrawResult,
    RunState,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendCandidate,
    SalesNavSendResult,
    SavedSearchArtifact,
)
from .send_commit import SendAttemptCommitter
from .store import Store, read_model, write_json_atomic

T = TypeVar("T")
SENT_INVITATIONS_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"


class BrowserInspectionRequired(RuntimeError):
    """Raised after a browser failure has been persisted as a controller checkpoint."""


class SupervisedBrowserClient:
    """Wrap browser capabilities with durable failure evidence and safe recovery state."""

    def __init__(self, browser: BrowserClient, *, store: Store, out_dir: Path) -> None:
        self.browser = browser
        self.store = store
        self.out_dir = out_dir

    @property
    def session(self) -> str | None:
        return getattr(self.browser, "session", None)

    def close(self) -> None:
        close = getattr(self.browser, "close", None)
        if callable(close):
            close()

    def recover_after_failure(self) -> None:
        recover = getattr(self.browser, "recover_after_failure", None)
        if callable(recover):
            recover()

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        return self._call(
            operation="send_connection",
            possible_send=allow_send and not dry_run,
            expected_url=candidate.profile_url,
            source=candidate.source,
            candidate=candidate,
            action=lambda: self.browser.send_connection(
                candidate, dry_run=dry_run, allow_send=allow_send
            ),
        )

    def capture_salesnav(
        self,
        *,
        source: str,
        url: str | None = None,
        pages: int = 1,
        limit: int = 25,
        stop_after_connectable: int = 0,
        only_connectable: bool = False,
        row_scroll_delay_ms: int = 250,
    ) -> tuple[SalesNavCapture, str]:
        return self._call(
            operation="capture_salesnav",
            possible_send=False,
            expected_url=url,
            source=source,
            action=lambda: self.browser.capture_salesnav(
                source=source,
                url=url,
                pages=pages,
                limit=limit,
                stop_after_connectable=stop_after_connectable,
                only_connectable=only_connectable,
                row_scroll_delay_ms=row_scroll_delay_ms,
            ),
        )

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        return self._call(
            operation="audit_sent_invitations",
            possible_send=False,
            expected_url=SENT_INVITATIONS_URL,
            action=lambda: self.browser.audit_sent_invitations(load_more=load_more),
        )

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        return self._call(
            operation="resolve_saved_searches",
            possible_send=False,
            expected_url=url,
            action=lambda: self.browser.resolve_saved_searches(url=url, out=out),
        )

    def check_acceptance_outcomes(
        self,
        *,
        candidates: list[AcceptanceCheckCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptanceOutcomeArtifact, str]:
        first = candidates[offset] if candidates and offset < len(candidates) else None
        return self._call(
            operation="check_acceptance_outcomes",
            possible_send=False,
            expected_url=first.profile_url if first is not None else None,
            source=first.source if first is not None else None,
            action=lambda: self.browser.check_acceptance_outcomes(
                candidates=candidates,
                input_path=input_path,
                out=out,
                offset=offset,
                limit=limit,
                delay_ms=delay_ms,
            ),
        )

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        return self._call(
            operation="capture_pending_invitations",
            possible_send=False,
            expected_url=SENT_INVITATIONS_URL,
            action=lambda: self.browser.capture_pending_invitations(
                load_more=load_more, threshold_days=threshold_days, out=out
            ),
        )

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        return self._call(
            operation="send_acceptance_followup",
            possible_send=allow_send and not dry_run,
            expected_url=record.sales_nav_profile_url or record.profile_url,
            source=record.source,
            action=lambda: self.browser.send_acceptance_followup(
                record,
                dry_run=dry_run,
                preview_fill=preview_fill,
                allow_send=allow_send,
            ),
        )

    def save_acceptance_lead_to_list(
        self,
        record: AcceptanceFollowupRecord,
        *,
        allow_save: bool,
    ) -> tuple[AcceptanceLeadListSaveResult, str]:
        return self._call(
            operation="save_acceptance_lead_to_list",
            possible_send=allow_save,
            expected_url=record.sales_nav_profile_url or record.profile_url,
            source=record.source,
            action=lambda: self.browser.save_acceptance_lead_to_list(record, allow_save=allow_save),
        )

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        return self._call(
            operation="withdraw_pending",
            possible_send=allow_withdraw and not dry_run,
            expected_url=SENT_INVITATIONS_URL,
            action=lambda: self.browser.withdraw_pending(
                candidate, dry_run=dry_run, allow_withdraw=allow_withdraw
            ),
        )

    def withdraw_loaded_pending(
        self,
        *,
        limit: int,
        threshold_days: int,
        timeout_seconds: float,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawBatchResult, str]:
        return self._call(
            operation="withdraw_loaded_pending",
            possible_send=allow_withdraw and not dry_run,
            expected_url=SENT_INVITATIONS_URL,
            action=lambda: self.browser.withdraw_loaded_pending(
                limit=limit,
                threshold_days=threshold_days,
                timeout_seconds=timeout_seconds,
                dry_run=dry_run,
                allow_withdraw=allow_withdraw,
            ),
        )

    def _call(
        self,
        *,
        operation: str,
        possible_send: bool,
        expected_url: str | None,
        action: Callable[[], T],
        source: str | None = None,
        candidate: CandidateObservation | None = None,
    ) -> T:
        try:
            return action()
        except BrowserInspectionRequired:
            raise
        except BaseException as exc:
            requires_audit = possible_send or self.store.load_run().provisional_count() > 0
            if not isinstance(exc, Exception) and not requires_audit:
                raise
            incident = record_browser_incident(
                self.store,
                self.browser,
                out_dir=self.out_dir,
                operation=operation,
                possible_send=requires_audit,
                expected_url=expected_url,
                source=source,
                candidate=candidate,
                error=exc,
            )
            if requires_audit:
                guidance = "a real send may have occurred; re-audit without replacement"
            else:
                guidance = "inspect the owned Chrome tab and apply a recovery receipt"
            raise BrowserInspectionRequired(
                f"browser incident requires attention: {incident.incident_path}; {guidance}"
            ) from exc


def record_browser_incident(
    store: Store,
    browser: BrowserClient,
    *,
    out_dir: Path,
    operation: str,
    possible_send: bool,
    expected_url: str | None,
    source: str | None,
    candidate: CandidateObservation | None,
    error: BaseException,
) -> BrowserIncident:
    run = store.load_run()
    previous_state = run.state
    out_dir.mkdir(parents=True, exist_ok=True)
    index = len(run.browser_incidents) + 1
    stem = f"{index:03d}-browser-incident"
    incident_path = out_dir / f"{stem}.json"
    markdown_path = out_dir / f"{stem}.md"
    receipt_path = out_dir / f"{stem}-recovery-receipt.json"
    diagnostic_path = out_dir / f"{stem}-diagnostic.json"
    screenshot_path = out_dir / f"{stem}-diagnostic.png"
    diagnostic_error: str | None = None
    captured_diagnostic_path: str | None = None
    captured_screenshot_path: str | None = None

    capture_diagnostics = getattr(browser, "capture_diagnostics", None)
    if callable(capture_diagnostics):
        try:
            diagnostic, captured_path = capture_diagnostics(
                operation=operation,
                error=str(error),
                expected_url=expected_url,
                out=diagnostic_path,
                screenshot_out=screenshot_path,
            )
            captured_diagnostic_path = str(captured_path)
            screenshot_value = diagnostic.get("screenshotPath")
            if isinstance(screenshot_value, str) and screenshot_value:
                captured_screenshot_path = screenshot_value
        except Exception as diagnostic_exc:
            diagnostic_error = str(diagnostic_exc)
    else:
        diagnostic_error = "browser client does not expose capture_diagnostics"

    allowed_actions = [BrowserRecoveryAction.INSPECT_ONLY]
    status = (
        BrowserIncidentStatus.AUDIT_REQUIRED
        if possible_send
        else BrowserIncidentStatus.NEEDS_INSPECTION
    )
    if not possible_send:
        allowed_actions.extend(
            [
                BrowserRecoveryAction.RELOAD_PAGE,
                BrowserRecoveryAction.NAVIGATE_EXPECTED_URL,
                BrowserRecoveryAction.DISMISS_OBSTRUCTION,
                BrowserRecoveryAction.RECONNECT_SESSION,
                BrowserRecoveryAction.RETRY_OPERATION,
            ]
        )

    incident = BrowserIncident(
        operation=operation,
        previous_state=previous_state,
        possible_send=possible_send,
        expected_url=expected_url,
        source=source or (candidate.source if candidate is not None else None),
        candidate_name=candidate.name if candidate is not None else None,
        profile_url=candidate.profile_url if candidate is not None else None,
        error=str(error),
        incident_path=str(incident_path),
        markdown_path=str(markdown_path),
        receipt_path=str(receipt_path),
        diagnostic_path=captured_diagnostic_path,
        screenshot_path=captured_screenshot_path,
        diagnostic_error=diagnostic_error,
        allowed_actions=allowed_actions,
        status=status,
    )

    if possible_send and candidate is not None:
        synthetic = SalesNavSendResult(
            candidate=SalesNavSendCandidate(
                source=candidate.source,
                name=candidate.name,
                profile_url=candidate.profile_url,
            ),
            status="uncertain-browser-exception",
            send={"error": str(error), "incident": str(incident_path)},
            public_profile_url=candidate.public_profile_url,
        )
        SendAttemptCommitter(store).commit_send_result(
            run,
            synthetic,
            incident_path,
            controller_payload_extra={"browser_incident_id": str(incident.id)},
        )
        run = store.load_run()

    run.browser_incidents.append(incident)
    run.active_browser_incident_id = incident.id
    run.state = RunState.NEEDS_REAUDIT if possible_send else RunState.NEEDS_BROWSER_INSPECTION
    run.notes.append(
        f"browser incident during {operation}: {incident.incident_path}; "
        f"possible_send={'yes' if possible_send else 'no'}"
    )
    run.mark_updated()
    store.save_run(run)
    write_json_atomic(incident_path, incident.model_dump(mode="json"))
    markdown_path.write_text(render_browser_incident(incident), encoding="utf-8")
    store.append_event(run, "browser-incident", incident)
    return incident


def render_browser_incident(incident: BrowserIncident) -> str:
    actions = "\n".join(f"- `{action.value}`" for action in incident.allowed_actions)
    send_warning = (
        "A real send may have occurred. Do not apply a recovery receipt or replace the send; "
        "resume through sent-page audit reconciliation."
        if incident.possible_send
        else (
            "Use Chrome to inspect only the automation-owned tab. Perform only a leased action, "
            "capture before/after evidence, write a fresh receipt, apply it, then resume the "
            "same run."
        )
    )
    return "\n".join(
        [
            f"# Browser Incident {incident.id}",
            "",
            f"- Operation: `{incident.operation}`",
            f"- Previous state: `{incident.previous_state.value}`",
            f"- Possible send: `{'yes' if incident.possible_send else 'no'}`",
            f"- Expected URL: `{incident.expected_url or 'unknown'}`",
            f"- Candidate: `{incident.candidate_name or 'none'}`",
            f"- Error: `{incident.error}`",
            f"- Diagnostic: `{incident.diagnostic_path or 'unavailable'}`",
            f"- Screenshot: `{incident.screenshot_path or 'unavailable'}`",
            f"- Diagnostic error: `{incident.diagnostic_error or 'none'}`",
            f"- Lease id: `{incident.lease_id}`",
            f"- Receipt path: `{incident.receipt_path}`",
            "",
            "## Allowed Actions",
            "",
            actions,
            "",
            "## Boundary",
            "",
            send_warning,
            "",
            "## Recovery Receipt",
            "",
            "```json",
            json.dumps(
                {
                    "incident_id": str(incident.id),
                    "lease_id": str(incident.lease_id),
                    "status": "recovered",
                    "actions": ["reload_page"],
                    "before_artifacts": ["/absolute/path/before.png"],
                    "after_artifacts": ["/absolute/path/after.png"],
                    "current_url": incident.expected_url,
                    "evidence": (
                        "Specific visible evidence showing the obstruction and recovered state"
                    ),
                    "note": None,
                },
                indent=2,
            ),
            "```",
            "",
        ]
    )


def browser_inspection_status(store: Store) -> str:
    run = store.load_run()
    incident = run.active_browser_incident()
    if incident is None:
        return json.dumps({"state": run.state.value, "incident": None}, indent=2)
    return json.dumps(
        {"state": run.state.value, "incident": incident.model_dump(mode="json")},
        indent=2,
        default=str,
    )


def apply_browser_recovery_receipt(store: Store, path: Path) -> str:
    run = store.load_run()
    incident = run.active_browser_incident()
    if incident is None:
        raise ValueError("run has no active browser incident")
    if run.state != RunState.NEEDS_BROWSER_INSPECTION:
        raise ValueError(
            f"run state is {run.state.value}; only NeedsBrowserInspection accepts a "
            "recovery receipt"
        )
    if incident.possible_send:
        raise ValueError(
            "possible-send incidents require audit reconciliation, not recovery receipts"
        )
    receipt = read_model(path, BrowserRecoveryReceipt)
    if receipt.incident_id != incident.id:
        raise ValueError(
            f"receipt incident {receipt.incident_id} does not match active incident {incident.id}"
        )
    if receipt.lease_id != incident.lease_id:
        raise ValueError("recovery receipt lease id does not match the active controller lease")
    disallowed = [action for action in receipt.actions if action not in incident.allowed_actions]
    if disallowed:
        raise ValueError(
            "recovery receipt contains action(s) outside the lease: "
            + ", ".join(action.value for action in disallowed)
        )
    if not receipt.evidence.strip():
        raise ValueError("recovery receipt requires specific visible evidence")
    if receipt.status == BrowserRecoveryStatus.RECOVERED:
        if not receipt.before_artifacts or not receipt.after_artifacts:
            raise ValueError("recovered receipt requires before and after artifacts")
        missing = [
            artifact
            for artifact in [*receipt.before_artifacts, *receipt.after_artifacts]
            if not Path(artifact).is_file()
        ]
        if missing:
            raise ValueError("recovery receipt artifact does not exist: " + ", ".join(missing))
        incident.status = BrowserIncidentStatus.RECOVERED
        run.state = incident.previous_state
        run.active_browser_incident_id = None
    elif receipt.status == BrowserRecoveryStatus.UNRESOLVED:
        incident.status = BrowserIncidentStatus.UNRESOLVED
        run.state = RunState.NEEDS_BROWSER_INSPECTION
    else:
        incident.status = BrowserIncidentStatus.BLOCKED
        run.state = RunState.BLOCKED
        run.active_browser_incident_id = None
    incident.receipt = receipt
    run.mark_updated()
    store.save_run(run)
    store.append_event(
        run,
        "browser-recovery-receipt",
        {"path": str(path), "incident": incident, "state": run.state.value},
    )
    return (
        f"applied browser recovery receipt for {incident.id}: {receipt.status.value}; "
        f"run state {run.state.value}"
    )


def resolve_audited_browser_incident(store: Store, *, audit_path: Path) -> None:
    run = store.load_run()
    incident = run.active_browser_incident()
    if incident is None or not incident.possible_send:
        return
    if run.state == RunState.NEEDS_REAUDIT:
        return
    incident.status = (
        BrowserIncidentStatus.BLOCKED
        if run.state == RunState.BLOCKED
        else BrowserIncidentStatus.RECOVERED
    )
    run.active_browser_incident_id = None
    run.notes.append(
        f"possible-send browser incident {incident.id} resolved through audit {audit_path}"
    )
    run.mark_updated()
    store.save_run(run)
    store.append_event(
        run,
        "browser-incident-audit-resolution",
        {
            "incident_id": str(incident.id),
            "audit_path": str(audit_path),
            "status": incident.status.value,
        },
    )
