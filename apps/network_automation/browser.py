"""Browser adapter interfaces and Playwriter implementation for guarded network actions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from packages.linkedin_browser import playwriter as playwriter_module
from packages.linkedin_browser.playwriter import PlaywriterRunner, StagingMode

from .models import (
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceLeadListSaveResult,
    AcceptanceListArtifact,
    AppModel,
    CandidateObservation,
    PendingCandidateObservation,
    PendingCapture,
    PendingWithdrawBatchResult,
    PendingWithdrawResult,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendResult,
    SavedSearchArtifact,
)
from .store import read_model

DEFAULT_SEND_OUT_DIR = Path("/tmp/linkedin-network-run-send-next")
DEFAULT_CAPTURE_OUT_DIR = Path("/tmp/linkedin-network-run-capture")
DEFAULT_AUDIT_OUT_DIR = Path("/tmp/linkedin-network-run-reconcile-audit")
DEFAULT_FOLLOWUP_OUT_DIR = Path("/tmp/linkedin-acceptance-followup-message")
DEFAULT_WITHDRAW_OUT_DIR = Path("/tmp/linkedin-pending-cleanup-withdraw-next")
PLAYWRITER_BIN_ENV = playwriter_module.PLAYWRITER_BIN_ENV
PLAYWRITER_BROWSER_KEY_ENV = playwriter_module.PLAYWRITER_BROWSER_KEY_ENV
PLAYWRITER_SESSION_ENV = playwriter_module.PLAYWRITER_SESSION_ENV


class ConnectionSendBrowser(Protocol):
    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]: ...


class SalesNavCaptureBrowser(Protocol):
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
    ) -> tuple[SalesNavCapture, str]: ...


class SentInvitationAuditBrowser(Protocol):
    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]: ...


class SavedSearchBrowser(Protocol):
    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]: ...


class AcceptanceListBrowser(Protocol):
    def capture_acceptance_lists(
        self,
        *,
        previous_watermark: list[str],
        out: Path,
        max_load_actions: int = 100,
        watermark_size: int = 25,
    ) -> tuple[AcceptanceListArtifact, str]: ...


class PendingInvitationCaptureBrowser(Protocol):
    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]: ...


class AcceptanceFollowupBrowser(Protocol):
    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]: ...


class AcceptanceLeadListBrowser(Protocol):
    def save_acceptance_lead_to_list(
        self,
        record: AcceptanceFollowupRecord,
        *,
        allow_save: bool,
    ) -> tuple[AcceptanceLeadListSaveResult, str]: ...


class PendingWithdrawBrowser(Protocol):
    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]: ...

    def withdraw_loaded_pending(
        self,
        *,
        limit: int,
        threshold_days: int,
        timeout_seconds: float,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawBatchResult, str]: ...


class BrowserClient(
    ConnectionSendBrowser,
    SalesNavCaptureBrowser,
    SentInvitationAuditBrowser,
    SavedSearchBrowser,
    AcceptanceListBrowser,
    PendingInvitationCaptureBrowser,
    AcceptanceFollowupBrowser,
    AcceptanceLeadListBrowser,
    PendingWithdrawBrowser,
    Protocol,
):
    """All-capability browser adapter for controller workflows."""


class FixtureBrowserClient:
    """Fixture-backed browser adapter used by parity tests."""

    def __init__(
        self,
        *,
        send_result: Path | None = None,
        capture: Path | None = None,
        audit: Path | None = None,
        saved_searches: Path | None = None,
        acceptance_lists: Path | None = None,
        pending_capture: Path | None = None,
        followup_result: Path | None = None,
        lead_list_result: Path | None = None,
        withdraw_result: Path | None = None,
        withdraw_batch_result: Path | None = None,
    ) -> None:
        self.send_result = send_result
        self.capture = capture
        self.audit = audit
        self.saved_searches = saved_searches
        self.acceptance_lists = acceptance_lists
        self.pending_capture = pending_capture
        self.followup_result = followup_result
        self.lead_list_result = lead_list_result
        self.withdraw_result = withdraw_result
        self.withdraw_batch_result = withdraw_batch_result

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        _ = candidate, dry_run, allow_send
        if self.send_result is None:
            raise RuntimeError("send fixture was not provided")
        return read_model(self.send_result, SalesNavSendResult), str(self.send_result)

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
        _ = source, url, pages, limit, stop_after_connectable, only_connectable, row_scroll_delay_ms
        if self.capture is None:
            raise RuntimeError("capture fixture was not provided")
        return read_model(self.capture, SalesNavCapture), str(self.capture)

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        _ = load_more
        if self.audit is None:
            return (
                SalesNavAudit.model_validate(
                    {
                        "peopleCount": 101,
                        "recentNames": ["Duplicate Lead"],
                        "loadedCount": 1,
                        "invitations": [{"name": "Duplicate Lead", "rowIndex": 0}],
                    }
                ),
                "fixture:synthesized-audit",
            )
        return read_model(self.audit, SalesNavAudit), str(self.audit)

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        _ = url, out
        if self.saved_searches is None:
            raise RuntimeError("saved-search fixture was not provided")
        return read_model(self.saved_searches, SavedSearchArtifact), str(self.saved_searches)

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        _ = load_more, threshold_days, out
        if self.pending_capture is None:
            raise RuntimeError("pending-capture fixture was not provided")
        return read_model(self.pending_capture, PendingCapture), str(self.pending_capture)

    def capture_acceptance_lists(
        self,
        *,
        previous_watermark: list[str],
        out: Path,
        max_load_actions: int = 100,
        watermark_size: int = 25,
    ) -> tuple[AcceptanceListArtifact, str]:
        _ = previous_watermark, out, max_load_actions, watermark_size
        if self.acceptance_lists is None:
            raise RuntimeError("acceptance-lists fixture was not provided")
        return read_model(self.acceptance_lists, AcceptanceListArtifact), str(
            self.acceptance_lists
        )

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

    def save_acceptance_lead_to_list(
        self,
        record: AcceptanceFollowupRecord,
        *,
        allow_save: bool,
    ) -> tuple[AcceptanceLeadListSaveResult, str]:
        _ = record, allow_save
        if self.lead_list_result is None:
            raise RuntimeError("lead-list fixture was not provided")
        return read_model(self.lead_list_result, AcceptanceLeadListSaveResult), str(
            self.lead_list_result
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

    def withdraw_loaded_pending(
        self,
        *,
        limit: int,
        threshold_days: int,
        timeout_seconds: float,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawBatchResult, str]:
        _ = limit, threshold_days, timeout_seconds, dry_run, allow_withdraw
        if self.withdraw_batch_result is None:
            raise RuntimeError("withdraw batch fixture was not provided")
        return read_model(self.withdraw_batch_result, PendingWithdrawBatchResult), str(
            self.withdraw_batch_result
        )


class PlaywriterBrowserClient:
    """Playwriter-backed browser client for LinkedIn UI actions."""

    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_SEND_OUT_DIR,
        session: str | None = None,
        browser_key: str | None = None,
        playwriter_bin: str | None = None,
    ) -> None:
        self.out_dir = out_dir
        self._runner = PlaywriterRunner(
            session=session,
            browser_key=browser_key,
            playwriter_bin=playwriter_bin,
            config_state_key="state.linkedinToolsConfigPath",
        )

    @property
    def session(self) -> str:
        return self._runner.session

    def close(self) -> None:
        return None

    def recover_after_failure(self) -> None:
        self._runner.reset_session()

    def capture_diagnostics(
        self,
        *,
        operation: str,
        error: str,
        expected_url: str | None,
        out: Path,
        screenshot_out: Path,
    ) -> tuple[dict[str, Any], str]:
        config = {
            "operation": operation,
            "error": error,
            "expectedUrl": expected_url,
            "out": str(out),
            "screenshotOut": str(screenshot_out),
        }
        self._run_script(_playwriter_browser_diagnostic_script(), config, staging="direct")
        payload = read_model(out, _BrowserDiagnosticArtifact)
        return payload.model_dump(mode="json", by_alias=True), str(out)

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        if not candidate.profile_url:
            raise RuntimeError("candidate profile_url is required for browser send")
        if not dry_run and not allow_send:
            raise RuntimeError("real send requires allow_send=True")
        out = self._next_output_path("send-result")
        config = {
            "candidate": candidate.model_dump(mode="json"),
            "dryRun": dry_run,
            "allowSend": allow_send,
            "out": str(out),
        }
        self._run_script(_playwriter_salesnav_send_script(), config)
        return read_model(out, SalesNavSendResult), str(out)

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
        out = self._next_output_path("capture-page")
        config = {
            "source": source,
            "url": url,
            "pages": pages,
            "limit": limit,
            "stopAfterConnectable": stop_after_connectable,
            "onlyConnectable": only_connectable,
            "rowScrollDelayMs": row_scroll_delay_ms,
            "out": str(out),
        }
        self._run_script(_playwriter_salesnav_capture_script(), config)
        return read_model(out, SalesNavCapture), str(out)

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        out = self._next_output_path("audit")
        config = {"loadMore": load_more, "out": str(out)}
        self._run_script(_playwriter_salesnav_audit_script(), config)
        return read_model(out, SalesNavAudit), str(out)

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        config = {"url": url, "out": str(out), "navigationTimeoutMs": 120000}
        self._run_script(_playwriter_salesnav_saved_searches_script(), config)
        return read_model(out, SavedSearchArtifact), str(out)

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        config = {
            "loadMore": load_more,
            "thresholdDays": threshold_days,
            "out": str(out),
        }
        self._run_script(_playwriter_pending_capture_script(), config)
        return read_model(out, PendingCapture), str(out)

    def capture_acceptance_lists(
        self,
        *,
        previous_watermark: list[str],
        out: Path,
        max_load_actions: int = 100,
        watermark_size: int = 25,
    ) -> tuple[AcceptanceListArtifact, str]:
        config = {
            "previousWatermark": previous_watermark,
            "maxLoadActions": max_load_actions,
            "watermarkSize": watermark_size,
            "out": str(out),
        }
        self._run_script(_playwriter_acceptance_lists_script(), config, staging="direct")
        return read_model(out, AcceptanceListArtifact), str(out)

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        if preview_fill and not dry_run:
            raise RuntimeError("preview_fill requires dry_run=True")
        if preview_fill and allow_send:
            raise RuntimeError("preview_fill cannot run with allow_send=True")
        if not dry_run and not allow_send:
            raise RuntimeError("real send requires allow_send=True")
        out = self._next_output_path(record.id)
        config = {
            "record": record.model_dump(mode="json"),
            "dryRun": dry_run,
            "previewFill": preview_fill,
            "allowSend": allow_send,
            "out": str(out),
        }
        self._run_script(_playwriter_acceptance_followup_send_script(), config)
        return read_model(out, AcceptanceFollowupSendResult), str(out)

    def save_acceptance_lead_to_list(
        self,
        record: AcceptanceFollowupRecord,
        *,
        allow_save: bool,
    ) -> tuple[AcceptanceLeadListSaveResult, str]:
        if not allow_save:
            raise RuntimeError("saving a lead to a Sales Navigator list requires allow_save=True")
        out = self._next_output_path(f"{record.id}-lead-list")
        config = {
            "record": record.model_dump(mode="json"),
            "allowSave": allow_save,
            "out": str(out),
        }
        self._run_script(_playwriter_acceptance_lead_list_script(), config)
        return read_model(out, AcceptanceLeadListSaveResult), str(out)

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        if not dry_run and not allow_withdraw:
            raise RuntimeError("real withdrawal requires allow_withdraw=True")
        out = self._next_output_path("withdraw-result")
        config = {
            "candidate": candidate.model_dump(mode="json"),
            "dryRun": dry_run,
            "allowWithdraw": allow_withdraw,
            "out": str(out),
        }
        self._run_script(_playwriter_pending_withdraw_script(), config)
        return read_model(out, PendingWithdrawResult), str(out)

    def withdraw_loaded_pending(
        self,
        *,
        limit: int,
        threshold_days: int,
        timeout_seconds: float,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawBatchResult, str]:
        if not dry_run and not allow_withdraw:
            raise RuntimeError("real withdrawal requires allow_withdraw=True")
        out = self._next_output_path("withdraw-loaded-result")
        config = {
            "limit": limit,
            "thresholdDays": threshold_days,
            "timeoutSeconds": timeout_seconds,
            "dryRun": dry_run,
            "allowWithdraw": allow_withdraw,
            "out": str(out),
        }
        self._run_script(_playwriter_pending_withdraw_loaded_script(), config)
        return read_model(out, PendingWithdrawBatchResult), str(out)

    def _run_script(
        self,
        script: Path,
        config: dict[str, Any],
        *,
        staging: StagingMode = "shared",
    ) -> None:
        self._runner.run_script(
            script,
            config,
            output_missing_message="Playwriter browser script did not write an output artifact",
            out_dir=self.out_dir,
            staging=staging,
            progress=True,
        )

    def _next_output_path(self, stem: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.out_dir.glob(f"*-{_safe_stem(stem)}.json"))
        next_index = len(existing) + 1
        return self.out_dir / f"{next_index:03d}-{_safe_stem(stem)}.json"


def _playwriter_script_dir() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts"


class _BrowserDiagnosticArtifact(AppModel):
    captured_at: str = Field(alias="capturedAt")
    operation: str
    expected_url: str | None = Field(default=None, alias="expectedUrl")
    current_url: str | None = Field(default=None, alias="currentUrl")
    screenshot_path: str | None = Field(default=None, alias="screenshotPath")
    page_classification: str | None = Field(default=None, alias="pageClassification")
    tabs: list[dict[str, Any]] = Field(default_factory=list)
    probes: dict[str, Any] = Field(default_factory=dict)
    dialogs: list[dict[str, Any]] = Field(default_factory=list)
    screenshot_error: str | None = Field(default=None, alias="screenshotError")
    error: str


def _playwriter_browser_diagnostic_script() -> Path:
    return _playwriter_script_dir() / "browser_diagnostic.js"


def _playwriter_acceptance_lists_script() -> Path:
    return _playwriter_script_dir() / "acceptance_lists.js"


def _playwriter_acceptance_followup_send_script() -> Path:
    return _playwriter_script_dir() / "acceptance_followup_send.js"


def _playwriter_acceptance_lead_list_script() -> Path:
    return _playwriter_script_dir() / "acceptance_lead_list.js"


def _playwriter_pending_capture_script() -> Path:
    return _playwriter_script_dir() / "pending_capture.js"


def _playwriter_pending_withdraw_script() -> Path:
    return _playwriter_script_dir() / "pending_withdraw.js"


def _playwriter_pending_withdraw_loaded_script() -> Path:
    return _playwriter_script_dir() / "pending_withdraw_loaded.js"


def _playwriter_salesnav_send_script() -> Path:
    return _playwriter_script_dir() / "salesnav_send.js"


def _playwriter_salesnav_capture_script() -> Path:
    return _playwriter_script_dir() / "salesnav_capture.js"


def _playwriter_salesnav_audit_script() -> Path:
    return _playwriter_script_dir() / "salesnav_audit.js"


def _playwriter_salesnav_saved_searches_script() -> Path:
    return _playwriter_script_dir() / "salesnav_saved_searches.js"


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-") or "artifact"
