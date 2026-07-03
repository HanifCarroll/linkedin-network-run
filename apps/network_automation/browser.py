"""Browser adapter interfaces and Playwriter implementation for guarded network actions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from packages.linkedin_browser import playwriter as playwriter_module
from packages.linkedin_browser.playwriter import PlaywriterRunner

from .models import (
    AcceptanceCheckCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceOutcomeArtifact,
    AcceptedDraftCandidate,
    AcceptedResearchArtifact,
    CandidateObservation,
    PendingCandidateObservation,
    PendingCapture,
    PendingWithdrawResult,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendResult,
    SavedSearchArtifact,
)
from .store import read_model, write_json_atomic

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


class AcceptanceOutcomeBrowser(Protocol):
    def check_acceptance_outcomes(
        self,
        *,
        candidates: list[AcceptanceCheckCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptanceOutcomeArtifact, str]: ...


class AcceptedResearchBrowser(Protocol):
    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]: ...


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


class PendingWithdrawBrowser(Protocol):
    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]: ...


class BrowserClient(
    ConnectionSendBrowser,
    SalesNavCaptureBrowser,
    SentInvitationAuditBrowser,
    SavedSearchBrowser,
    AcceptanceOutcomeBrowser,
    AcceptedResearchBrowser,
    PendingInvitationCaptureBrowser,
    AcceptanceFollowupBrowser,
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
        acceptance_outcomes: Path | None = None,
        accepted_research: Path | None = None,
        pending_capture: Path | None = None,
        followup_result: Path | None = None,
        withdraw_result: Path | None = None,
    ) -> None:
        self.send_result = send_result
        self.capture = capture
        self.audit = audit
        self.saved_searches = saved_searches
        self.acceptance_outcomes = acceptance_outcomes
        self.accepted_research = accepted_research
        self.pending_capture = pending_capture
        self.followup_result = followup_result
        self.withdraw_result = withdraw_result

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
            raise RuntimeError("audit fixture was not provided")
        return read_model(self.audit, SalesNavAudit), str(self.audit)

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        _ = url, out
        if self.saved_searches is None:
            raise RuntimeError("saved-search fixture was not provided")
        return read_model(self.saved_searches, SavedSearchArtifact), str(self.saved_searches)

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
        _ = input_path, delay_ms
        if self.acceptance_outcomes is None:
            selected = candidates[offset : offset + limit] if limit else candidates[offset:]
            payload = {
                "capturedAt": _now_iso(),
                "input": str(input_path),
                "count": len(selected),
                "offset": offset,
                "limit": limit,
                "totalCandidates": len(candidates),
                "complete": True,
                "rows": [
                    {
                        "source": candidate.source,
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "status": "pending",
                        "checkedAt": _now_iso(),
                        "relationship": None,
                        "evidence": "fixture synthesized pending confirmation",
                        "note": "fixture synthesized pending confirmation",
                    }
                    for candidate in selected
                ],
            }
            write_json_atomic(out, payload)
            return read_model(out, AcceptanceOutcomeArtifact), str(out)
        return read_model(self.acceptance_outcomes, AcceptanceOutcomeArtifact), str(
            self.acceptance_outcomes
        )

    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        _ = candidates, input_path, out, offset, limit, delay_ms
        if self.accepted_research is None:
            raise RuntimeError("accepted-research fixture was not provided")
        return read_model(self.accepted_research, AcceptedResearchArtifact), str(
            self.accepted_research
        )

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        _ = load_more, threshold_days, out
        if self.pending_capture is None:
            raise RuntimeError("pending-capture fixture was not provided")
        return read_model(self.pending_capture, PendingCapture), str(self.pending_capture)

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
        config = {
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "input": str(input_path),
            "out": str(out),
            "offset": offset,
            "limit": limit,
            "delayMs": delay_ms,
        }
        self._run_script(_playwriter_acceptance_outcomes_script(), config)
        return read_model(out, AcceptanceOutcomeArtifact), str(out)

    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        config = {
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "input": str(input_path),
            "out": str(out),
            "offset": offset,
            "limit": limit,
            "delayMs": delay_ms,
        }
        self._run_script(_playwriter_accepted_research_script(), config)
        return read_model(out, AcceptedResearchArtifact), str(out)

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

    def _run_script(self, script: Path, config: dict[str, Any]) -> None:
        self._runner.run_script(
            script,
            config,
            output_missing_message="Playwriter browser script did not write an output artifact",
            out_dir=self.out_dir,
            progress=True,
        )

    def _next_output_path(self, stem: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.out_dir.glob(f"*-{_safe_stem(stem)}.json"))
        next_index = len(existing) + 1
        return self.out_dir / f"{next_index:03d}-{_safe_stem(stem)}.json"


def _playwriter_script_dir() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts"


def _playwriter_acceptance_outcomes_script() -> Path:
    return _playwriter_script_dir() / "acceptance_outcomes.js"


def _playwriter_accepted_research_script() -> Path:
    return _playwriter_script_dir() / "accepted_research.js"


def _playwriter_acceptance_followup_send_script() -> Path:
    return _playwriter_script_dir() / "acceptance_followup_send.js"


def _playwriter_pending_capture_script() -> Path:
    return _playwriter_script_dir() / "pending_capture.js"


def _playwriter_pending_withdraw_script() -> Path:
    return _playwriter_script_dir() / "pending_withdraw.js"


def _playwriter_salesnav_send_script() -> Path:
    return _playwriter_script_dir() / "salesnav_send.js"


def _playwriter_salesnav_capture_script() -> Path:
    return _playwriter_script_dir() / "salesnav_capture.js"


def _playwriter_salesnav_audit_script() -> Path:
    return _playwriter_script_dir() / "salesnav_audit.js"


def _playwriter_salesnav_saved_searches_script() -> Path:
    return _playwriter_script_dir() / "salesnav_saved_searches.js"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-") or "artifact"
