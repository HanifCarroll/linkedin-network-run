"""Browser adapter interfaces and Playwright implementation for guarded network actions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from pydantic import BaseModel

from packages.linkedin_browser import (
    BrowserContextHandle,
    BrowserSession,
    ChromeProfileConfig,
    PageReusePolicy,
    RealAction,
    RealActionApproval,
    classify_browser_state,
    close_browser_context_handle,
    guarded_click,
    open_linkedin_browser_context,
)
from packages.linkedin_browser.selectors import (
    LINKEDIN_DIALOG,
    MESSAGE_COMPOSER,
    SALES_NAV_MORE_ACTIONS_BUTTON,
    SALES_NAV_OPEN_ACTIONS_BUTTON,
    SALES_NAV_PEOPLE_RESULT_ROW,
    SALES_NAV_PROFILE_LINK,
)
from packages.linkedin_browser.state import BrowserStateEvidence
from packages.linkedin_salesnav import (
    CandidateIdentity,
    MessageActionCandidate,
    guarded_connection_request,
    guarded_message_click,
    guarded_withdraw_invitation,
    sales_profile_id_from_url,
    sales_profile_urn_to_lead_url,
)

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
SENT_INVITATIONS_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"
SEND_INVITATION_BUTTON = re.compile(r"^(Send Invitation|Send invite|Send now|Send)$", re.I)
MESSAGE_ACTION = re.compile(r"^(Message|InMail)\b", re.I)
SEND_MESSAGE_BUTTON = re.compile(r"^(Send|Send message)$", re.I)
PEOPLE_COUNT = re.compile(r"People \(([\d,]+)\)")
SALES_NAV_LEAD_SEARCH_API = re.compile(r"/sales-api/salesApiLeadSearch", re.I)
SALES_NAV_PROFILE_API = re.compile(r"/sales-api/salesApiProfiles", re.I)
SECURITY_VERIFICATION_SELECTOR = (
    "iframe#humanThirdPartyIframe,"
    "iframe[title='LinkedIn security verification'],"
    "iframe[src*='li.protechts.net']"
)
DISABLE_SALESNAV_API_CAPTURE_ENV = "LINKEDIN_TOOLS_DISABLE_SALESNAV_API_CAPTURE"
PLAYWRITER_BIN_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BIN"
PLAYWRITER_BROWSER_KEY_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY"
PLAYWRITER_SESSION_ENV = "LINKEDIN_TOOLS_PLAYWRITER_SESSION"
ResultT = TypeVar("ResultT")
ModelT = TypeVar("ModelT", bound=BaseModel)


class BrowserClient(Protocol):
    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]: ...

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

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]: ...

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]: ...

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

    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        public_web: bool = True,
        max_web_results: int = 5,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]: ...

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]: ...

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
        raise RuntimeError("browser client is not configured")

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        _ = load_more
        raise RuntimeError("browser client is not configured")

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        _ = url, out
        raise RuntimeError("browser client is not configured")

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
        _ = candidates, input_path, out, offset, limit, delay_ms
        raise RuntimeError("browser client is not configured")

    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        public_web: bool = True,
        max_web_results: int = 5,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        _ = candidates, input_path, out, offset, limit, public_web, max_web_results, delay_ms
        raise RuntimeError("browser client is not configured")

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        _ = load_more, threshold_days, out
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
        public_web: bool = True,
        max_web_results: int = 5,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        _ = candidates, input_path, out, offset, limit, public_web, max_web_results, delay_ms
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
        self._session = session or os.environ.get(PLAYWRITER_SESSION_ENV)
        self._browser_key = browser_key or os.environ.get(PLAYWRITER_BROWSER_KEY_ENV)
        self._playwriter_bin = playwriter_bin or _playwriter_bin()

    @property
    def session(self) -> str:
        if self._session is None:
            self._session = self._create_session()
        return self._session

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
        config = {"url": url, "out": str(out)}
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
        public_web: bool = True,
        max_web_results: int = 5,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        config = {
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "input": str(input_path),
            "out": str(out),
            "offset": offset,
            "limit": limit,
            "publicWeb": public_web,
            "maxWebResults": max_web_results,
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

    def _create_session(self) -> str:
        command = [self._playwriter_bin, "session", "new"]
        if self._browser_key:
            command.extend(["--browser", self._browser_key])
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        match = re.search(r"Session\s+(\S+)\s+created", result.stdout)
        if not match:
            raise RuntimeError(f"could not parse Playwriter session id from: {result.stdout}")
        return match.group(1)

    def _run_script(self, script: Path, config: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        config_path, staged_out, final_out = _stage_playwriter_config(config)
        script_config = dict(config)
        if staged_out is not None:
            script_config["out"] = str(staged_out)
        write_json_atomic(config_path, script_config)
        command = [
            self._playwriter_bin,
            "-s",
            self.session,
            "-e",
            f"state.linkedinToolsConfigPath = {json.dumps(str(config_path))}",
        ]
        _run_playwriter_command(command)
        _run_playwriter_command(
            [self._playwriter_bin, "-s", self.session, "-f", str(script), "--timeout", "120000"]
        )
        if staged_out is not None and final_out is not None:
            if not _wait_for_path(staged_out):
                raise RuntimeError(
                    "Playwriter browser script did not write an output artifact; "
                    f"expected {staged_out}"
                )
            final_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_out), str(final_out))

    def _next_output_path(self, stem: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.out_dir.glob(f"*-{_safe_stem(stem)}.json"))
        next_index = len(existing) + 1
        return self.out_dir / f"{next_index:03d}-{_safe_stem(stem)}.json"


class PlaywrightBrowserClient:
    """Playwright-backed browser client using the shared LinkedIn Chrome profile."""

    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_SEND_OUT_DIR,
        context: Any | None = None,
        context_factory: Callable[[], Awaitable[Any]] | None = None,
        cdp_url: str | None = None,
        chrome_profile_config: ChromeProfileConfig | None = None,
        capture_timeout_seconds: float | None = None,
        max_load_more: int = 260,
        withdraw_timeout_seconds: float = 90.0,
    ) -> None:
        self.out_dir = out_dir
        self._context = context
        self._context_factory = context_factory
        self._cdp_url = cdp_url
        self._chrome_profile_config = chrome_profile_config
        self._capture_timeout_seconds = capture_timeout_seconds
        self._context_handle_ref: BrowserContextHandle | None = None
        self._playwright_manager: Any | None = None
        self._playwright: Any | None = None
        self._loop = asyncio.new_event_loop()
        self._counter = 0
        self.max_load_more = max_load_more
        self.withdraw_timeout_seconds = withdraw_timeout_seconds

    def close(self) -> None:
        async def _close() -> None:
            if self._context_handle_ref is not None:
                await close_browser_context_handle(self._context_handle_ref)
            elif self._context is not None and hasattr(self._context, "close"):
                await self._context.close()
            if self._playwright is not None and hasattr(self._playwright, "stop"):
                await self._playwright.stop()
            elif self._playwright_manager is not None and hasattr(
                self._playwright_manager, "__aexit__"
            ):
                await self._playwright_manager.__aexit__(None, None, None)

        if not self._loop.is_closed():
            self._loop.run_until_complete(_close())
            self._loop.close()

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        if not dry_run and not allow_send:
            raise RuntimeError("real send requires allow_send=True")
        return self._run(self._send_connection(candidate, dry_run=dry_run, allow_send=allow_send))

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
        capture = self._capture_salesnav(
            source=source,
            url=url,
            pages=pages,
            limit=limit,
            stop_after_connectable=stop_after_connectable,
            only_connectable=only_connectable,
            row_scroll_delay_ms=row_scroll_delay_ms,
        )
        if self._capture_timeout_seconds is not None:
            capture = asyncio.wait_for(capture, timeout=self._capture_timeout_seconds)
        return self._run(capture)

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        return self._run(self._audit_sent_invitations(load_more=load_more))

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        return self._run(self._resolve_saved_searches(url=url, out=out))

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
        return self._run(
            self._check_acceptance_outcomes(
                candidates=candidates,
                input_path=input_path,
                out=out,
                offset=offset,
                limit=limit,
                delay_ms=delay_ms,
            )
        )

    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        public_web: bool = True,
        max_web_results: int = 5,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        return self._run(
            self._research_accepted_candidates(
                candidates=candidates,
                input_path=input_path,
                out=out,
                offset=offset,
                limit=limit,
                public_web=public_web,
                max_web_results=max_web_results,
                delay_ms=delay_ms,
            )
        )

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        return self._run(
            self._capture_pending_invitations(
                load_more=load_more,
                threshold_days=threshold_days,
                out=out,
            )
        )

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
        return self._run(
            self._send_acceptance_followup(
                record,
                dry_run=dry_run,
                preview_fill=preview_fill,
                allow_send=allow_send,
            )
        )

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        if not dry_run and not allow_withdraw:
            raise RuntimeError("real withdrawal requires allow_withdraw=True")
        return self._run(
            self._withdraw_pending_with_timeout(
                candidate,
                dry_run=dry_run,
                allow_withdraw=allow_withdraw,
            )
        )

    def _run(self, coroutine: Coroutine[Any, Any, ResultT]) -> ResultT:
        return self._loop.run_until_complete(coroutine)

    async def _context_handle(self) -> Any:
        if self._context is not None:
            return self._context
        if self._context_factory is not None:
            self._context = await self._context_factory()
            return self._context
        from playwright.async_api import async_playwright

        self._playwright_manager = async_playwright()
        self._playwright = await self._playwright_manager.start()
        self._context_handle_ref = await open_linkedin_browser_context(
            self._playwright,
            config=self._chrome_profile_config,
            cdp_url=self._cdp_url,
        )
        self._context = self._context_handle_ref.context
        return self._context

    async def _page(self, fragments: tuple[str, ...]) -> Any:
        session = BrowserSession(
            await self._context_handle(),
            PageReusePolicy(preferred_url_fragments=fragments, foreground=False),
        )
        return await session.page(preferred_url_fragments=fragments)

    async def _send_connection(
        self,
        candidate: CandidateObservation,
        *,
        dry_run: bool,
        allow_send: bool,
    ) -> tuple[SalesNavSendResult, str]:
        if not candidate.profile_url:
            raise RuntimeError("candidate profile_url is required for browser send")
        page = await self._page(("linkedin.com/sales/search/people", "linkedin.com/sales/lead/"))
        search_result = await self._send_connection_from_search_row(
            page,
            candidate,
            dry_run=dry_run,
            allow_send=allow_send,
        )
        if search_result is not None:
            return search_result
        response = await page.goto(
            candidate.profile_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await _wait_for_load(page)
        block = await _classify_page(page, http_status=_response_status(response))
        result_payload: dict[str, Any] = _send_result_base(candidate, dry_run=dry_run, url=page.url)
        if block.is_blocking:
            result_payload.update({"status": "blocked", "reason": block.reason})
            return self._write_result("send-result", result_payload, SalesNavSendResult)
        if not _same_sales_profile(candidate.profile_url, page.url):
            result_payload.update({"status": "identity-mismatch", "reason": "loaded URL differs"})
            return self._write_result("send-result", result_payload, SalesNavSendResult)
        menu = await _open_profile_actions_menu(page)
        result_payload["before"] = menu
        state = _classify_menu_labels(menu.get("labels", []))
        if state == "already-pending":
            result_payload["status"] = "already-pending"
        elif state != "connectable":
            result_payload["status"] = f"not-connectable:{state}"
        elif dry_run:
            result_payload["status"] = "dry-run-connectable"
        else:
            identity = CandidateIdentity(
                name=candidate.name,
                profile_url=candidate.profile_url,
                candidate_id=candidate.sales_profile_urn,
                sales_profile_urn=candidate.sales_profile_urn,
            )

            async def click_connect() -> None:
                await _click_menu_label(page, menu.get("menu_id"), "Connect")

            try:
                guard = await guarded_connection_request(
                    identity,
                    click_connect,
                    dry_run=False,
                    approval=RealActionApproval(RealAction.SEND_CONNECTION, allow=allow_send),
                )
            except RuntimeError as error:
                if str(error) != "Connect menu item missing":
                    raise
                result_payload["status"] = "not-connectable:missing-connect-menu"
                result_payload["after"] = {"state": "missing-connect-menu"}
                await _escape(page)
                return self._write_result("send-result", result_payload, SalesNavSendResult)
            await _short_wait(page)
            send = await _click_send_invitation(page, allow_send=allow_send)
            result_payload["send"] = {"guard": guard.__dict__, **send}
            if send["status"] != "clicked-send":
                result_payload["status"] = _send_status_from_send(send["status"])
                result_payload["after"] = {"state": send["status"]}
            else:
                await _medium_wait(page)
                after = await _open_profile_actions_menu(page)
                result_payload["after"] = after
                result_payload["status"] = (
                    "pending-provisional"
                    if _classify_menu_labels(after.get("labels", [])) == "already-pending"
                    else "unverified:clicked-send"
                )
        await _escape(page)
        return self._write_result("send-result", result_payload, SalesNavSendResult)

    async def _send_connection_from_search_row(
        self,
        page: Any,
        candidate: CandidateObservation,
        *,
        dry_run: bool,
        allow_send: bool,
    ) -> tuple[SalesNavSendResult, str] | None:
        if not candidate.profile_url:
            raise RuntimeError("candidate profile_url is required for browser send")
        profile_url = candidate.profile_url
        if "linkedin.com/sales/search/people" not in page.url:
            return None
        await _wait_for_salesnav_results(page)
        row = await _salesnav_result_row_for_candidate(page, candidate)
        if row is None:
            return None
        result_payload: dict[str, Any] = _send_result_base(candidate, dry_run=dry_run, url=page.url)
        block = await _classify_page(page)
        if block.is_blocking:
            result_payload.update({"status": "blocked", "reason": block.reason})
            return self._write_result("send-result", result_payload, SalesNavSendResult)
        menu = await _open_row_menu(page, row, close=False)
        result_payload["before"] = {"scope": "search-row", **menu}
        state = _classify_menu_labels(menu.get("labels", []))
        if state == "already-pending":
            result_payload["status"] = "already-pending"
            await _escape(page)
            return self._write_result("send-result", result_payload, SalesNavSendResult)
        if state != "connectable":
            result_payload["status"] = f"not-connectable:{state}"
            await _escape(page)
            return self._write_result("send-result", result_payload, SalesNavSendResult)
        if dry_run:
            result_payload["status"] = "dry-run-connectable"
            await _escape(page)
            return self._write_result("send-result", result_payload, SalesNavSendResult)

        identity = CandidateIdentity(
            name=candidate.name,
            profile_url=profile_url,
            candidate_id=candidate.sales_profile_urn,
            sales_profile_urn=candidate.sales_profile_urn,
        )

        async def click_connect() -> None:
            await _click_menu_label(page, menu.get("menu_id"), "Connect")

        guard = await guarded_connection_request(
            identity,
            click_connect,
            dry_run=False,
            approval=RealActionApproval(RealAction.SEND_CONNECTION, allow=allow_send),
        )
        await _short_wait(page)
        send = await _click_send_invitation(page, allow_send=allow_send)
        result_payload["send"] = {"guard": guard.__dict__, **send, "scope": "search-row"}
        if send["status"] != "clicked-send":
            result_payload["status"] = _send_status_from_send(send["status"])
            result_payload["after"] = {"state": send["status"], "scope": "search-row"}
        else:
            await _medium_wait(page)
            after = await _open_row_menu(page, row)
            result_payload["after"] = {"scope": "search-row", **after}
            result_payload["status"] = (
                "pending-provisional"
                if _classify_menu_labels(after.get("labels", [])) == "already-pending"
                else "unverified:clicked-send"
            )
        await _escape(page)
        return self._write_result("send-result", result_payload, SalesNavSendResult)

    async def _capture_salesnav(
        self,
        *,
        source: str,
        url: str | None,
        pages: int,
        limit: int,
        stop_after_connectable: int,
        only_connectable: bool,
        row_scroll_delay_ms: int,
    ) -> tuple[SalesNavCapture, str]:
        page = await self._page(
            (
                "linkedin.com/sales/search/people",
                "linkedin.com/sales/lead/",
                "linkedin.com",
            )
        )
        api_rows_by_urn: dict[str, dict[str, Any]] = {}
        api_state: dict[str, Any] = {"enabled": True, "responses": 0, "rows": 0, "errors": []}
        api_response_tasks: list[asyncio.Task[None]] = []

        def queue_api_response(response: Any) -> None:
            if not SALES_NAV_LEAD_SEARCH_API.search(str(getattr(response, "url", ""))):
                return
            api_response_tasks.append(
                asyncio.create_task(
                    _capture_salesnav_api_response(
                        response,
                        api_rows_by_urn=api_rows_by_urn,
                        api_state=api_state,
                    )
                )
            )

        api_capture_enabled = os.environ.get(DISABLE_SALESNAV_API_CAPTURE_ENV, "").strip() not in {
            "1",
            "true",
            "yes",
        }
        api_state["enabled"] = api_capture_enabled
        if api_capture_enabled:
            page.on("response", queue_api_response)
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await _wait_for_load(page)
            await _wait_for_salesnav_results(page)
        try:
            all_rows: list[dict[str, Any]] = []
            page_summaries: list[dict[str, Any]] = []
            for page_number in range(1, max(1, pages) + 1):
                await _short_wait(page)
                await _drain_api_response_tasks(api_response_tasks)
                page_summaries.append({"url": page.url, "pageLabel": None})
                row_locators = await page.locator(SALES_NAV_PEOPLE_RESULT_ROW).all()
                for row_index, row in enumerate(row_locators[:limit]):
                    await _ignore_errors(row.scroll_into_view_if_needed())
                    if row_scroll_delay_ms > 0:
                        await page.wait_for_timeout(row_scroll_delay_ms)
                    item = await _capture_salesnav_row(
                        row,
                        row_index,
                        len(all_rows),
                        page_number,
                    )
                    api_classified = _apply_salesnav_api_state(item, api_rows_by_urn)
                    if item["profileUrl"] is None and item["scrollUrn"]:
                        item["profileUrl"] = sales_profile_urn_to_lead_url(item["scrollUrn"])
                    if not api_classified:
                        menu = await _open_row_menu(page, row)
                        item["menuLabels"] = menu["labels"]
                        item["menuState"] = _classify_menu_labels(menu["labels"])
                    all_rows.append(item)
                    if (
                        stop_after_connectable > 0
                        and _count_state(all_rows, "connectable") >= stop_after_connectable
                    ):
                        break
                if (
                    stop_after_connectable > 0
                    and _count_state(all_rows, "connectable") >= stop_after_connectable
                ):
                    break
                if page_number < pages and not await _click_next_results_page(page):
                    break
                if page_number < pages:
                    await _wait_for_salesnav_results(page)
            await _drain_api_response_tasks(api_response_tasks)
            output_rows = (
                [row for row in all_rows if row["menuState"] == "connectable"]
                if only_connectable
                else all_rows
            )
            payload = {
                "schemaVersion": 1,
                "capturedAt": _now_iso(),
                "url": page.url,
                "resumeUrl": page.url,
                "source": source,
                "page": page_summaries[-1] if page_summaries else None,
                "pages": page_summaries,
                "menuInspection": "api-state-with-menu-fallback",
                "filters": {"onlyConnectable": only_connectable},
                "captureOptions": {
                    "limit": limit,
                    "pages": pages,
                    "stopAfterConnectable": stop_after_connectable,
                    "rowScrollDelayMs": row_scroll_delay_ms,
                    "openMenus": True,
                    "apiState": True,
                },
                "apiState": api_state,
                "stateCounts": _state_counts(all_rows),
                "rawRowCount": len(all_rows),
                "outputRowCount": len(output_rows),
                "rows": output_rows,
            }
            return self._write_result("capture-page", payload, SalesNavCapture)
        finally:
            await _drain_api_response_tasks(api_response_tasks)
            remove_listener = getattr(page, "remove_listener", None)
            if api_capture_enabled and callable(remove_listener):
                remove_listener("response", queue_api_response)

    async def _audit_sent_invitations(self, *, load_more: int) -> tuple[SalesNavAudit, str]:
        page = await self._page(
            (
                "linkedin.com/mynetwork/invitation-manager/sent",
                "linkedin.com/sales/search/people",
                "linkedin.com",
            )
        )
        await page.goto(SENT_INVITATIONS_URL, wait_until="domcontentloaded", timeout=45000)
        await _wait_for_load(page)
        for _ in range(max(0, load_more)):
            button = page.get_by_role("button", name=re.compile(r"^Load more$", re.I)).first
            if not await _locator_count(button) or await _locator_disabled(button):
                break
            await button.click(timeout=8000)
            await _medium_wait(page)
        workspace = page.locator("main#workspace").first
        text = await workspace.text_content(timeout=10000)
        count_match = PEOPLE_COUNT.search(text or "")
        if not count_match:
            raise RuntimeError("could not parse People (N) count from sent invitations page")
        names = await _sent_invitation_names(page)
        payload = {
            "capturedAt": _now_iso(),
            "url": page.url,
            "peopleCount": int(count_match.group(1).replace(",", "")),
            "recentNames": names[:100],
        }
        return self._write_result("audit", payload, SalesNavAudit)

    async def _resolve_saved_searches(
        self, *, url: str, out: Path
    ) -> tuple[SavedSearchArtifact, str]:
        page = await self._page(("linkedin.com/sales/search/people", "linkedin.com/sales"))
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await _wait_for_load(page)
        button = page.get_by_role("button", name=re.compile(r"Saved searches", re.I)).first
        if not await _locator_count(button):
            raise RuntimeError(
                "saved-searches button missing; verify the automation browser is logged "
                "into Sales Navigator with the expected LinkedIn profile"
            )
        try:
            await button.click(timeout=10000)
        except Exception:
            await button.evaluate("(element) => element.click()")
        await _medium_wait(page)
        searches = await page.locator("a[href*='savedSearchId=']").evaluate_all(
            """(anchors) => {
              const byId = new Map();
              for (const anchor of anchors) {
                const href = anchor.href;
                let savedSearchId = null;
                try {
                  savedSearchId = new URL(href).searchParams.get("savedSearchId");
                } catch {
                  savedSearchId = null;
                }
                if (!savedSearchId) continue;
                const text = String(anchor.textContent || "").replace(/\\s+/g, " ").trim();
                const aria = anchor.getAttribute("aria-label") || "";
                const ariaName = aria.match(
                  /(?:View |results for )(.+?)(?: lead saved search| since|$)/
                );
                const textPattern = new RegExp(
                  "(?:Go to \\\\d+[,\\\\dK+]* new results for |View )(.+?)" +
                  "(?: since | lead saved search|$)"
                );
                const textName = text.match(textPattern);
                const knownName = (ariaName || [])[1] || (textName || [])[1] || text || null;
                const existing = byId.get(savedSearchId) || {
                  savedSearchId,
                  name: knownName,
                  viewUrl: null,
                  freshUrl: null,
                  freshText: null,
                  rowText: text,
                };
                if (href.includes("lastViewedAt=")) {
                  existing.freshUrl = href;
                  existing.freshText = text;
                } else {
                  existing.viewUrl = href;
                }
                existing.name = existing.name || knownName;
                byId.set(savedSearchId, existing);
              }
              return Array.from(byId.values());
            }"""
        )
        payload = {
            "capturedAt": _now_iso(),
            "url": page.url,
            "searches": searches,
        }
        return self._write_exact_result(out, payload, SavedSearchArtifact)

    async def _check_acceptance_outcomes(
        self,
        *,
        candidates: list[AcceptanceCheckCandidate],
        input_path: Path,
        out: Path,
        offset: int,
        limit: int,
        delay_ms: int,
    ) -> tuple[AcceptanceOutcomeArtifact, str]:
        page = await self._page(("linkedin.com/sales/lead/", "linkedin.com/sales/search/people"))
        rows: list[dict[str, Any]] = []
        selected = _window(candidates, offset=offset, limit=limit)
        for candidate in selected:
            checked_at = _now_iso()
            try:
                outcome = await self._classify_acceptance_candidate(page, candidate)
                rows.append(
                    {
                        "source": candidate.source,
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "status": outcome["status"],
                        "checkedAt": checked_at,
                        "relationship": outcome.get("relationship"),
                        "evidence": outcome.get("evidence"),
                        "note": outcome.get("note"),
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "source": candidate.source,
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "status": "failed",
                        "checkedAt": checked_at,
                        "relationship": None,
                        "evidence": str(exc)[:1000],
                        "note": "browser check failed",
                    }
                )
            if delay_ms > 0:
                await page.wait_for_timeout(delay_ms)
            self._write_exact_result(
                out,
                _acceptance_artifact_payload(
                    input_path=input_path,
                    candidates=candidates,
                    rows=rows,
                    offset=offset,
                    limit=limit,
                    complete=False,
                ),
                AcceptanceOutcomeArtifact,
            )
        return self._write_exact_result(
            out,
            _acceptance_artifact_payload(
                input_path=input_path,
                candidates=candidates,
                rows=rows,
                offset=offset,
                limit=limit,
                complete=True,
            ),
            AcceptanceOutcomeArtifact,
        )

    async def _classify_acceptance_candidate(
        self, page: Any, candidate: AcceptanceCheckCandidate
    ) -> dict[str, Any]:
        if not candidate.profile_url:
            return {
                "status": "unknown",
                "relationship": None,
                "evidence": None,
                "note": "candidate has no Sales Navigator profile URL",
            }
        target_profile_id = sales_profile_id_from_url(candidate.profile_url)
        identity_task = asyncio.create_task(_wait_for_profile_identity(page, target_profile_id))
        response = await page.goto(
            candidate.profile_url,
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await _wait_for_load(page)
        block = await _classify_page(page, http_status=_response_status(response))
        profile_identity = await identity_task
        displayed_name = await _profile_name(page)
        identity = _identity_match(candidate.name, displayed_name, profile_identity)
        relationship = _relationship_from_degree(profile_identity.get("degree"))
        if block.is_blocking:
            return {
                "status": "blocked",
                "relationship": relationship,
                "evidence": _json_evidence({"identity": identity, "url": page.url}),
                "note": block.reason,
            }
        if not identity["matched"]:
            return {
                "status": "unknown",
                "relationship": relationship,
                "evidence": _json_evidence({"identity": identity, "url": page.url}),
                "note": "loaded lead identity did not match candidate",
            }
        if relationship == "1st":
            return {
                "status": "accepted",
                "relationship": relationship,
                "evidence": _json_evidence({"identity": identity, "url": page.url}),
                "note": "Sales Navigator profile API shows 1st-degree relationship",
            }
        menu = await _open_profile_actions_menu(page)
        menu_state = _classify_menu_labels(menu.get("labels", []))
        if menu_state == "already-pending":
            return {
                "status": "pending",
                "relationship": relationship,
                "evidence": _json_evidence({"menu": menu}),
                "note": "lead overflow menu shows pending invitation",
            }
        if menu_state == "connectable":
            return {
                "status": "connectable",
                "relationship": relationship,
                "evidence": _json_evidence({"menu": menu}),
                "note": "lead is connectable again",
            }
        return {
            "status": "unknown",
            "relationship": relationship,
            "evidence": _json_evidence({"identity": identity, "menu": menu}),
            "note": "could not classify acceptance state",
        }

    async def _research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int,
        limit: int,
        public_web: bool,
        max_web_results: int,
        delay_ms: int,
    ) -> tuple[AcceptedResearchArtifact, str]:
        sales_page = await self._page(("linkedin.com/sales/lead/", "linkedin.com/sales/search"))
        web_page = await self._page(("duckduckgo.com", "linkedin.com/sales/lead/"))
        rows: list[dict[str, Any]] = []
        selected = _window(candidates, offset=offset, limit=limit)
        for candidate in selected:
            sales_nav = await _extract_salesnav_research(sales_page, candidate)
            web = await _public_web_research(
                web_page,
                candidate,
                sales_nav,
                public_web=public_web,
                max_web_results=max_web_results,
            )
            rows.append(
                {
                    "source": candidate.source,
                    "name": candidate.name,
                    "profileUrl": candidate.profile_url,
                    "salesNav": sales_nav,
                    "web": web,
                    "warnings": [],
                }
            )
            if delay_ms > 0:
                await sales_page.wait_for_timeout(delay_ms)
            self._write_exact_result(
                out,
                _accepted_research_payload(
                    input_path=input_path,
                    candidates=candidates,
                    rows=rows,
                    offset=offset,
                    limit=limit,
                    complete=False,
                ),
                AcceptedResearchArtifact,
            )
        return self._write_exact_result(
            out,
            _accepted_research_payload(
                input_path=input_path,
                candidates=candidates,
                rows=rows,
                offset=offset,
                limit=limit,
                complete=True,
            ),
            AcceptedResearchArtifact,
        )

    async def _capture_pending_invitations(
        self, *, load_more: int, threshold_days: int, out: Path
    ) -> tuple[PendingCapture, str]:
        page = await self._page(("linkedin.com/mynetwork/invitation-manager/sent", "linkedin.com"))
        await page.goto(SENT_INVITATIONS_URL, wait_until="domcontentloaded", timeout=45000)
        await _wait_for_load(page)
        for _ in range(max(0, load_more)):
            button = page.get_by_role("button", name=re.compile(r"^Load more$", re.I)).first
            if not await _locator_count(button) or await _locator_disabled(button):
                break
            await button.click(timeout=8000)
            await _short_wait(page)
        people_count = await _sent_people_count(page)
        rows = await _pending_capture_rows(page, threshold_days=threshold_days)
        payload = {
            "capturedAt": _now_iso(),
            "url": page.url,
            "peopleCount": people_count,
            "thresholdDays": threshold_days,
            "rows": rows,
        }
        return self._write_exact_result(out, payload, PendingCapture)

    async def _send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        if not record.profile_url:
            raise RuntimeError("follow-up profile_url is required")
        page = await self._page(("linkedin.com/sales/lead/", "linkedin.com/sales/search/people"))
        await page.goto(record.profile_url, wait_until="domcontentloaded", timeout=30000)
        await _wait_for_load(page)
        payload = _followup_result_base(
            record,
            dry_run=dry_run,
            preview_fill=preview_fill,
            url=page.url,
        )
        block = await _classify_page(page)
        if block.is_blocking:
            payload.update({"status": "blocked", "reason": block.reason})
            return self._write_result(record.id, payload, AcceptanceFollowupSendResult)
        if not _same_sales_profile(record.profile_url, page.url):
            payload.update({"status": "identity-mismatch", "reason": "loaded URL differs"})
            return self._write_result(record.id, payload, AcceptanceFollowupSendResult)
        profile_name = await _profile_name(page)
        action = await _find_message_action(page)
        if action is None:
            payload["status"] = "not-messageable"
            return self._write_result(record.id, payload, AcceptanceFollowupSendResult)
        safety_action = MessageActionCandidate(
            kind=action["kind"],
            action_label=action["label"],
            identity_label=profile_name or record.name,
            source="profile-actions",
            opened_page_url=page.url,
        )
        if dry_run and not preview_fill:
            safety = guarded_message_click(
                CandidateIdentity(name=record.name, profile_url=record.profile_url),
                safety_action,
                lambda: _noop_async(),
                dry_run=True,
            )
            click_result = await safety
            status = (
                "dry-run-messageable" if click_result.safety.status == "ok" else click_result.status
            )
            payload.update({"status": status, "action": click_result.safety.__dict__})
            return self._write_result(record.id, payload, AcceptanceFollowupSendResult)
        await action["locator"].click(timeout=8000)
        await _medium_wait(page)
        composer = await _find_composer(page)
        if composer is None:
            payload.update({"status": "composer-missing", "action": safety_action.__dict__})
            return self._write_result(record.id, payload, AcceptanceFollowupSendResult)
        subject_fill = await _fill_subject_if_present(page, "")
        body_fill = await _fill_composer(composer, record.draft)
        payload.update(
            {
                "action": safety_action.__dict__,
                "composerSelector": composer["selector"],
                "subjectFill": subject_fill,
                "bodyFill": body_fill,
            }
        )
        if preview_fill:
            payload["status"] = "preview-filled"
            return self._write_result(record.id, payload, AcceptanceFollowupSendResult)
        send_button = await _find_send_message_button(page)
        if send_button is None:
            payload["status"] = "send-button-missing"
            return self._write_result(record.id, payload, AcceptanceFollowupSendResult)

        async def click_send() -> None:
            await send_button.click(timeout=8000)

        guard = await guarded_click(
            RealAction.SEND_MESSAGE,
            click_send,
            label="Send",
            candidate_id=record.id,
            dry_run=False,
            approval=RealActionApproval(RealAction.SEND_MESSAGE, allow=allow_send),
        )
        await _medium_wait(page)
        payload.update({"status": "sent-clicked", "send": guard.__dict__})
        return self._write_result(record.id, payload, AcceptanceFollowupSendResult)

    async def _withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        page = await self._page(("linkedin.com/mynetwork/invitation-manager/sent", "linkedin.com"))
        await page.goto(SENT_INVITATIONS_URL, wait_until="domcontentloaded", timeout=45000)
        await _wait_for_load(page)
        payload = _withdraw_result_base(candidate, dry_run=dry_run, url=page.url)
        if not candidate.eligible:
            payload.update({"status": "not-eligible", "reason": "candidate is not marked eligible"})
            return self._write_result("withdraw-result", payload, PendingWithdrawResult)
        found = await _find_pending_withdraw_link(page, candidate, self.max_load_more)
        if found is None:
            payload["status"] = "row-not-found"
        elif dry_run:
            payload.update({"status": "dry-run-withdrawable", "rowText": found["row_text"]})
        else:
            identity = CandidateIdentity(
                name=candidate.name,
                profile_url=candidate.profile_url or candidate.name,
            )

            async def click_withdraw() -> None:
                await found["link"].click(timeout=8000)

            guard = await guarded_withdraw_invitation(
                identity,
                click_withdraw,
                dry_run=False,
                approval=RealActionApproval(RealAction.WITHDRAW_INVITATION, allow=allow_withdraw),
            )
            await _short_wait(page)
            confirmed = await _click_confirm_withdraw(page)
            payload["detail"] = {"guard": guard.__dict__, "confirmed": confirmed}
            if not confirmed:
                payload["status"] = "confirm-button-missing"
            else:
                await _medium_wait(page)
                still_visible = bool(await _ignore_errors(found["link"].is_visible(), False))
                payload["status"] = "unverified" if still_visible else "withdrawn-verified"
        return self._write_result("withdraw-result", payload, PendingWithdrawResult)

    async def _withdraw_pending_with_timeout(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        try:
            return await asyncio.wait_for(
                self._withdraw_pending(
                    candidate,
                    dry_run=dry_run,
                    allow_withdraw=allow_withdraw,
                ),
                timeout=self.withdraw_timeout_seconds,
            )
        except TimeoutError:
            payload = _withdraw_result_base(candidate, dry_run=dry_run, url="")
            payload.update(
                {
                    "status": "timeout",
                    "detail": {
                        "timeoutSeconds": self.withdraw_timeout_seconds,
                        "reason": "pending withdrawal browser operation timed out",
                    },
                }
            )
            return self._write_result("withdraw-result", payload, PendingWithdrawResult)

    def _write_result(
        self,
        stem: str,
        payload: dict[str, Any],
        model: type[ModelT],
    ) -> tuple[ModelT, str]:
        self._counter += 1
        path = self.out_dir / f"{self._counter:03d}-{_safe_stem(stem)}.json"
        write_json_atomic(path, payload)
        return model.model_validate(payload), str(path)

    def _write_exact_result(
        self,
        path: Path,
        payload: dict[str, Any],
        model: type[ModelT],
    ) -> tuple[ModelT, str]:
        write_json_atomic(path, payload)
        return model.model_validate(payload), str(path)


def _playwriter_bin() -> str:
    configured = os.environ.get(PLAYWRITER_BIN_ENV)
    if configured:
        return configured
    default = Path.home() / ".bun/bin/playwriter"
    if default.exists():
        return str(default)
    resolved = shutil.which("playwriter")
    if resolved:
        return resolved
    raise RuntimeError("Playwriter binary was not found; set LINKEDIN_TOOLS_PLAYWRITER_BIN")


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


def _stage_playwriter_config(config: dict[str, Any]) -> tuple[Path, Path | None, Path | None]:
    staging_dir = Path(tempfile.gettempdir()) / "linkedin-tools-playwriter"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(Path(str(config.get("out") or "artifact.json")).stem)
    config_path = staging_dir / f"{stem}-config.json"
    final_out = Path(str(config["out"])) if config.get("out") else None
    staged_out = staging_dir / f"{stem}-out.json" if final_out is not None else None
    return config_path, staged_out, final_out


def _wait_for_path(path: Path, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def _run_playwriter_command(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = "\n".join(
            part
            for part in (
                result.stdout.strip(),
                result.stderr.strip(),
            )
            if part
        )
        raise RuntimeError(
            f"Playwriter command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )


def _send_result_base(
    candidate: CandidateObservation, *, dry_run: bool, url: str
) -> dict[str, Any]:
    return {
        "candidate": {
            "source": candidate.source,
            "name": candidate.name,
            "profileUrl": candidate.profile_url,
        },
        "dryRun": dry_run,
        "url": url,
        "status": "unknown",
    }


def _followup_result_base(
    record: AcceptanceFollowupRecord,
    *,
    dry_run: bool,
    preview_fill: bool,
    url: str,
) -> dict[str, Any]:
    return {
        "candidate": {
            "id": record.id,
            "key": record.key,
            "name": record.name,
            "profileUrl": record.profile_url,
            "source": record.source,
        },
        "dryRun": dry_run,
        "url": url,
        "messageLength": len(record.draft),
        "status": "unknown",
        "previewFill": preview_fill,
    }


def _withdraw_result_base(
    candidate: PendingCandidateObservation, *, dry_run: bool, url: str
) -> dict[str, Any]:
    return {
        "candidate": {
            "name": candidate.name,
            "profileUrl": candidate.profile_url,
            "ageText": candidate.age_text,
        },
        "dryRun": dry_run,
        "url": url,
        "status": "unknown",
    }


async def _classify_page(page: Any, *, http_status: int | None = None) -> Any:
    login = await _locator_count(
        page.locator("input[name='session_key'], form[action*='/uas/login']")
    )
    checkpoint = await _locator_count(page.locator("input[name='pin'], input[name='challengeId']"))
    security_verification = await _locator_has_visible_element(
        page.locator(SECURITY_VERIFICATION_SELECTOR)
    )
    evidence = BrowserStateEvidence(
        url=page.url,
        http_status=http_status,
        login_form_present=login > 0,
        checkpoint_present=checkpoint > 0,
        security_verification_present=security_verification,
    )
    return classify_browser_state(evidence)


def _response_status(response: Any | None) -> int | None:
    status = getattr(response, "status", None)
    return status if isinstance(status, int) else None


async def _wait_for_load(page: Any) -> None:
    await _ignore_errors(page.wait_for_load_state("domcontentloaded", timeout=10000))
    await _medium_wait(page)


async def _wait_for_salesnav_results(page: Any) -> None:
    await _ignore_errors(
        page.wait_for_function(
            """(selector) => document.querySelectorAll(selector).length > 5""",
            arg=SALES_NAV_PEOPLE_RESULT_ROW,
            timeout=10000,
        )
    )


async def _short_wait(page: Any) -> None:
    await page.wait_for_timeout(500)


async def _medium_wait(page: Any) -> None:
    await page.wait_for_timeout(1500)


async def _escape(page: Any) -> None:
    keyboard = getattr(page, "keyboard", None)
    if keyboard is not None:
        await _ignore_errors(keyboard.press("Escape"))


async def _open_profile_actions_menu(page: Any) -> dict[str, Any]:
    trigger = page.locator(SALES_NAV_OPEN_ACTIONS_BUTTON).first
    if not await _locator_count(trigger):
        trigger = page.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first
    if not await _locator_count(trigger):
        return {"state": "missing-trigger", "labels": []}
    menu_id = await trigger.get_attribute("aria-controls")
    await _click_readonly_menu_trigger(trigger, timeout=8000)
    await _short_wait(page)
    menu = (
        page.locator(f"#{menu_id}").first
        if menu_id
        else page.locator("[data-popper-placement]").last
    )
    if not await _locator_count(menu):
        return {"state": "missing-menu", "labels": [], "menu_id": menu_id}
    labels = await _menu_labels(menu)
    return {"state": _classify_menu_labels(labels), "labels": labels, "menu_id": menu_id}


async def _open_row_menu(page: Any, row: Any, *, close: bool = True) -> dict[str, Any]:
    trigger = row.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first
    if not await _locator_count(trigger):
        return {"state": "missing-trigger", "labels": []}
    menu_id = await trigger.get_attribute("aria-controls")
    await _click_readonly_menu_trigger(trigger, timeout=5000)
    await _short_wait(page)
    menu = (
        page.locator(f"#{menu_id}").first
        if menu_id
        else page.locator("[data-popper-placement]").last
    )
    if not await _locator_count(menu):
        return {"state": "missing-menu", "labels": [], "menu_id": menu_id}
    labels = await _menu_labels(menu)
    if close:
        await _escape(page)
    return {"state": _classify_menu_labels(labels), "labels": labels, "menu_id": menu_id}


async def _click_readonly_menu_trigger(trigger: Any, *, timeout: int) -> None:
    try:
        await trigger.click(timeout=timeout)
    except Exception:
        await trigger.evaluate("(element) => element.click()")


async def _salesnav_result_row_for_candidate(
    page: Any, candidate: CandidateObservation
) -> Any | None:
    if candidate.sales_profile_urn:
        row = page.locator(SALES_NAV_PEOPLE_RESULT_ROW).filter(
            has=page.locator(
                f"[data-scroll-into-view={json.dumps(candidate.sales_profile_urn)}]"
            )
        ).first
        if await _locator_count(row):
            return row
    profile_id = sales_profile_id_from_url(candidate.profile_url)
    if profile_id:
        row = page.locator(SALES_NAV_PEOPLE_RESULT_ROW).filter(
            has=page.locator(f'a[href*="/sales/lead/{profile_id},"]')
        ).first
        if await _locator_count(row):
            return row
    return None


async def _menu_labels(menu: Any) -> list[dict[str, Any]]:
    items = await menu.locator("button,a,[role=menuitem]").all()
    labels: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        text = _clean(await item.text_content())
        aria = await item.get_attribute("aria-label")
        disabled = await _locator_disabled(item)
        if text or aria:
            labels.append(
                {
                    "index": index,
                    "text": text or None,
                    "aria": aria,
                    "disabled": disabled,
                }
            )
    return labels


def _classify_menu_labels(labels: list[dict[str, Any]]) -> str:
    texts = [_clean(str(label.get("text") or label.get("aria") or "")) for label in labels]
    if any(re.fullmatch(r"(Connect\s*[-\u2013\u2014]\s*)?Pending", text, re.I) for text in texts):
        return "already-pending"
    if any(re.fullmatch(r"Connect", text, re.I) for text in texts):
        return "connectable"
    if any(re.search(r"email required|enter.*email", text, re.I) for text in texts):
        return "email-required"
    return "unknown"


async def _click_menu_label(page: Any, menu_id: Any, label: str) -> None:
    menu = (
        page.locator(f"#{menu_id}").first
        if menu_id
        else page.locator("[data-popper-placement]").last
    )
    items = await menu.locator("button,a,[role=menuitem]").all()
    for item in items:
        text = _clean(await item.text_content())
        aria = _clean(str(await item.get_attribute("aria-label") or ""))
        if re.fullmatch(re.escape(label), text, re.I) or re.fullmatch(
            re.escape(label), aria, re.I
        ):
            await _click_action_locator(page, item, timeout=8000)
            return
    raise RuntimeError(f"{label} menu item missing")


async def _click_action_locator(page: Any, locator: Any, *, timeout: int) -> None:
    try:
        await locator.click(timeout=timeout)
        return
    except Exception:
        await _ignore_errors(locator.scroll_into_view_if_needed(timeout=2000))
        box = await locator.bounding_box(timeout=2000)
        if not box:
            raise
        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


async def _click_send_invitation(page: Any, *, allow_send: bool) -> dict[str, Any]:
    email = page.locator("input[type='email'], input[name*='email' i]").first
    if await _locator_count(email):
        return {"status": "email-required"}
    dialog = (
        page.locator(LINKEDIN_DIALOG)
        .filter(has=page.get_by_role("button", name=SEND_INVITATION_BUTTON))
        .last
    )
    if not await _locator_count(dialog):
        return {"status": "send-button-missing"}
    button = dialog.get_by_role("button", name=SEND_INVITATION_BUTTON).last
    if not await _locator_count(button):
        return {"status": "send-button-missing"}
    if await _locator_disabled(button):
        return {"status": "send-button-disabled"}
    await guarded_click(
        RealAction.SEND_CONNECTION,
        lambda: button.click(timeout=8000),
        label="Send Invitation",
        dry_run=False,
        approval=RealActionApproval(RealAction.SEND_CONNECTION, allow=allow_send),
    )
    return {"status": "clicked-send", "label": "Send Invitation"}


def _send_status_from_send(status: str) -> str:
    if status in {"email-required", "blocked", "identity-mismatch"}:
        return status
    return f"unverified:{status}"


async def _capture_salesnav_row(
    row: Any,
    index: int,
    global_index: int,
    page_number: int,
) -> dict[str, Any]:
    profile = row.locator(SALES_NAV_PROFILE_LINK).first
    profile_url = await profile.get_attribute("href") if await _locator_count(profile) else None
    name_locator = row.locator("[data-anonymize='person-name']").first
    name = _clean(await name_locator.text_content()) if await _locator_count(name_locator) else None
    trigger = row.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first
    trigger_label = (
        await trigger.get_attribute("aria-label") if await _locator_count(trigger) else None
    )
    if name is None and trigger_label and trigger_label.startswith("See more actions for "):
        name = trigger_label.removeprefix("See more actions for ").strip() or None
    scroll_urn_locator = row.locator("[data-scroll-into-view]").first
    scroll_urn = (
        await scroll_urn_locator.get_attribute("data-scroll-into-view")
        if await _locator_count(scroll_urn_locator)
        else None
    )
    message_button = row.get_by_role("button", name=re.compile(r"^Message\b", re.I)).first
    save_button = row.get_by_role("button", name=re.compile(r"^Save\b", re.I)).first
    return {
        "index": index,
        "globalIndex": global_index,
        "pageNumber": page_number,
        "name": name,
        "profileUrl": _absolute_linkedin_url(profile_url),
        "scrollUrn": scroll_urn,
        "visibleState": {
            "hasMessage": bool(await _locator_count(message_button)),
            "hasSave": bool(await _locator_count(save_button)),
        },
        "menuLabels": [],
        "menuState": "not-opened",
        "links": [{"href": _absolute_linkedin_url(profile_url)}] if profile_url else [],
    }


async def _capture_salesnav_api_response(
    response: Any,
    *,
    api_rows_by_urn: dict[str, dict[str, Any]],
    api_state: dict[str, Any],
) -> None:
    try:
        payload = await response.json()
        api_state["responses"] = _int_count(api_state.get("responses")) + 1
        if not isinstance(payload, dict):
            api_state["rows"] = len(api_rows_by_urn)
            return
        elements = payload.get("elements")
        if not isinstance(elements, list):
            api_state["rows"] = len(api_rows_by_urn)
            return
        for element in elements:
            if not isinstance(element, dict):
                continue
            entity_urn = element.get("entityUrn")
            if not isinstance(entity_urn, str) or entity_urn == "":
                continue
            api_rows_by_urn[entity_urn] = {
                "entityUrn": entity_urn,
                "fullName": _optional_api_value(element.get("fullName")),
                "pendingInvitation": _optional_api_value(element.get("pendingInvitation")),
                "degree": _optional_api_value(element.get("degree")),
                "saved": _optional_api_value(element.get("saved")),
                "viewed": _optional_api_value(element.get("viewed")),
                "openLink": _optional_api_value(element.get("openLink")),
            }
        api_state["rows"] = len(api_rows_by_urn)
    except Exception as error:
        errors = api_state.setdefault("errors", [])
        if isinstance(errors, list):
            errors.append(str(error))


def _apply_salesnav_api_state(
    row: dict[str, Any],
    api_rows_by_urn: dict[str, dict[str, Any]],
) -> bool:
    scroll_urn = row.get("scrollUrn")
    api_row = api_rows_by_urn.get(scroll_urn) if isinstance(scroll_urn, str) else None
    if api_row is None:
        return False
    row["apiState"] = api_row
    if row.get("profileUrl") is None:
        row["profileUrl"] = _api_sales_profile_url(api_row) or sales_profile_urn_to_lead_url(
            scroll_urn
        )
    pending_invitation = api_row.get("pendingInvitation")
    degree = api_row.get("degree")
    if pending_invitation is True:
        row["menuState"] = "already-pending"
        row["menuLabels"] = [
            {
                "index": 0,
                "text": "Connect - Pending (API pendingInvitation)",
                "aria": None,
                "tag": "API",
                "disabled": False,
            }
        ]
        return True
    visible_state = row.get("visibleState")
    if isinstance(visible_state, dict) and visible_state.get("hasMessage") is True:
        return False
    if pending_invitation is False and (degree is None or degree == 2):
        row["menuState"] = "connectable"
        row["menuLabels"] = [
            {
                "index": 0,
                "text": "Connect (API pendingInvitation=false)",
                "aria": None,
                "tag": "API",
                "disabled": False,
            }
        ]
        return True
    return False


async def _drain_api_response_tasks(tasks: list[asyncio.Task[None]]) -> None:
    if not tasks:
        return
    pending = list(tasks)
    tasks.clear()
    await asyncio.gather(*pending, return_exceptions=True)


def _optional_api_value(value: object) -> object:
    if isinstance(value, str | bool | int | float) or value is None:
        return value
    return None


def _api_sales_profile_url(api_row: dict[str, Any]) -> str | None:
    open_link = api_row.get("openLink")
    if not isinstance(open_link, str) or "/sales/lead/" not in open_link:
        return None
    return _absolute_linkedin_url(open_link)


async def _click_next_results_page(page: Any) -> bool:
    button = page.get_by_role("button", name=re.compile(r"^Next$", re.I)).first
    if not await _locator_count(button) or await _locator_disabled(button):
        return False
    before = page.url
    await button.click(timeout=8000)
    await _medium_wait(page)
    return bool(page.url != before)


async def _sent_invitation_names(page: Any) -> list[str]:
    links = await page.locator("a[aria-label^='Withdraw invitation sent to']").all()
    names: list[str] = []
    for link in links:
        label = await link.get_attribute("aria-label")
        if label and label.startswith("Withdraw invitation sent to "):
            names.append(label.removeprefix("Withdraw invitation sent to ").strip())
    return names


async def _sent_people_count(page: Any) -> int | None:
    workspace = page.locator("main#workspace").first
    text = await workspace.text_content(timeout=10000)
    match = PEOPLE_COUNT.search(text or "")
    return int(match.group(1).replace(",", "")) if match else None


async def _pending_capture_rows(page: Any, *, threshold_days: int) -> list[dict[str, Any]]:
    links = await page.locator("a[aria-label^='Withdraw invitation sent to']").all()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, link in enumerate(links):
        label = await link.get_attribute("aria-label")
        aria_name = (
            label.removeprefix("Withdraw invitation sent to ").strip()
            if label and label.startswith("Withdraw invitation sent to ")
            else None
        )
        row = await _pending_row_payload(link)
        age_text = _sent_age_text(row["rowText"])
        name = aria_name or row["name"]
        if not name or not age_text:
            continue
        dedupe_key = (name, age_text)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        age_months = _parse_sent_age_months(age_text)
        age_days = _parse_sent_age_days(age_text)
        rows.append(
            {
                "index": index,
                "name": name,
                "profileUrl": row["profileUrl"],
                "ageText": age_text,
                "ageMonths": age_months,
                "ageDays": age_days,
                "eligible": age_days is not None and age_days >= threshold_days,
                "rowText": row["rowText"],
            }
        )
    return rows


async def _pending_row_payload(link: Any) -> dict[str, Any]:
    value = await link.evaluate(
        """(node) => {
          const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
          const label = node.getAttribute('aria-label') || '';
          const prefix = 'Withdraw invitation sent to ';
          const row = node.closest('[role="listitem"]');
          if (label.startsWith(prefix) && row) {
            const text = clean(row.textContent || '');
            const profileSelector = "a[href*='/in/'], a[href*='/sales/lead/']";
            const profile = Array.from(row.querySelectorAll(profileSelector))
              .find((anchor) => anchor.href && anchor !== node);
            return {
              rowText: text,
              name: label.replace(prefix, '').trim() || null,
              profileUrl: profile ? profile.href : null,
            };
          }
          return { rowText: clean(node.textContent || ''), name: null, profileUrl: null };
        }"""
    )
    return value if isinstance(value, dict) else {"rowText": "", "name": None, "profileUrl": None}


def _sent_age_text(value: str) -> str | None:
    match = re.search(
        r"Sent (?:today|yesterday|\d+ minutes? ago|\d+ hours? ago|\d+ days? ago|"
        r"\d+ weeks? ago|\d+ months? ago|\d+ years? ago)",
        value,
        re.I,
    )
    return match.group(0) if match else None


def _parse_sent_age_months(age_text: str) -> int | None:
    lower = age_text.lower()
    number = _first_number(lower) or 1
    if "year" in lower:
        return number * 12
    if "month" in lower:
        return number
    if any(marker in lower for marker in ("today", "minute", "hour", "day", "week")):
        return 0
    return None


def _parse_sent_age_days(age_text: str) -> int | None:
    lower = age_text.lower()
    if any(marker in lower for marker in ("today", "minute", "hour")):
        return 0
    number = _first_number(lower) or 1
    if "year" in lower:
        return number * 365
    if "month" in lower:
        return number * 30
    if "week" in lower:
        return number * 7
    if "yesterday" in lower:
        return 1
    if "day" in lower:
        return number
    return None


def _first_number(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


async def _profile_name(page: Any) -> str | None:
    locator = page.locator("[data-anonymize='person-name']").first
    if not await _locator_count(locator):
        return None
    return _clean(await locator.text_content()) or None


async def _find_message_action(page: Any) -> dict[str, Any] | None:
    actions = await page.locator("button,a,[role=button]").all()
    for action in actions:
        if await _locator_disabled(action) or not await _locator_visible(action):
            continue
        text = _clean(await action.text_content())
        aria = await action.get_attribute("aria-label")
        label = text or aria or ""
        if MESSAGE_ACTION.search(label):
            kind = "inmail" if label.lower().startswith("inmail") else "message"
            return {"locator": action, "label": label, "kind": kind}
    return None


async def _find_composer(page: Any) -> dict[str, Any] | None:
    for selector in (MESSAGE_COMPOSER, "div.msg-form__contenteditable[contenteditable='true']"):
        locator = page.locator(selector).last
        if await _locator_count(locator) and await _locator_visible(locator):
            return {"selector": selector, "locator": locator}
    return None


async def _fill_subject_if_present(page: Any, subject: str) -> dict[str, Any]:
    for selector in (
        "input[name='subject']",
        "input[placeholder*='Subject' i]",
        "input[aria-label*='Subject' i]",
    ):
        locator = page.locator(selector).last
        if await _locator_count(locator) and await _locator_visible(locator):
            await locator.fill(subject, timeout=8000)
            return {"filled": True, "selector": selector, "subject": subject}
    return {"filled": False}


async def _fill_composer(composer: dict[str, Any], message: str) -> dict[str, Any]:
    locator = composer["locator"]
    await locator.fill(message, timeout=8000)
    actual = await locator.text_content()
    return {
        "matched": _clean(actual) == _clean(message),
        "selector": composer["selector"],
        "expectedLength": len(message),
        "actualLength": len(actual or ""),
        "lineBreakCount": message.count("\n"),
    }


async def _find_send_message_button(page: Any) -> Any | None:
    button = page.get_by_role("button", name=SEND_MESSAGE_BUTTON).last
    if await _locator_count(button) and not await _locator_disabled(button):
        return button
    return None


async def _find_pending_withdraw_link(
    page: Any,
    candidate: PendingCandidateObservation,
    max_load_more: int,
) -> dict[str, Any] | None:
    for attempt in range(max(0, max_load_more) + 1):
        link = page.locator(
            f"a[aria-label={json.dumps('Withdraw invitation sent to ' + candidate.name)}]"
        ).first
        if await _locator_count(link):
            row_text = await _candidate_row_text(link, candidate)
            if candidate.age_text == "" or candidate.age_text in row_text:
                return {"link": link, "row_text": row_text}
        if attempt == max_load_more:
            break
        await page.evaluate(
            """() => {
              const node = document.querySelector('main#workspace')
                || document.scrollingElement
                || document.documentElement;
              node.scrollTop += Math.floor(node.clientHeight * 2.5);
            }"""
        )
        await _short_wait(page)
    return None


async def _candidate_row_text(link: Any, candidate: PendingCandidateObservation) -> str:
    value = await link.evaluate(
        """(node, input) => {
          const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
          let cursor = node;
          while (cursor && cursor !== document.body) {
            const value = clean(cursor.textContent || '');
            if (value.includes(input.name) && (!input.ageText || value.includes(input.ageText))) {
              return value;
            }
            cursor = cursor.parentElement;
          }
          return clean(node.textContent || '');
        }""",
        {"name": candidate.name, "ageText": candidate.age_text},
    )
    return value if isinstance(value, str) else ""


async def _click_confirm_withdraw(page: Any) -> bool:
    withdraw_button = page.get_by_role("button", name=re.compile(r"^Withdraw$", re.I))
    dialog = page.locator(LINKEDIN_DIALOG).filter(has=withdraw_button).last
    button = dialog.get_by_role("button", name=re.compile(r"^Withdraw$", re.I)).last
    if not await _locator_count(button):
        button = page.get_by_role("button", name=re.compile(r"^Withdraw$", re.I)).last
    if not await _locator_count(button):
        return False
    await button.click(timeout=8000)
    return True


async def _wait_for_profile_identity(page: Any, profile_id: str | None) -> dict[str, Any]:
    if not profile_id:
        return {}
    try:
        response = await page.wait_for_response(
            lambda item: (
                SALES_NAV_PROFILE_API.search(str(getattr(item, "url", ""))) is not None
                and f"profileId:{profile_id}" in str(getattr(item, "url", ""))
                and (_response_status(item) or 0) < 500
            ),
            timeout=8000,
        )
    except Exception:
        return {}
    payload = await _ignore_errors(response.json(), None)
    identity = _profile_identity_from_payload(payload)
    if identity:
        identity["responseUrl"] = str(getattr(response, "url", ""))[:260]
    return identity


def _profile_identity_from_payload(payload: object) -> dict[str, Any]:
    stack: list[object] = [payload]
    seen: set[int] = set()
    while stack:
        value = stack.pop()
        if not isinstance(value, dict):
            continue
        value_id = id(value)
        if value_id in seen:
            continue
        seen.add(value_id)
        full_name = _clean(
            str(
                value.get("fullName")
                or " ".join(
                    str(part) for part in (value.get("firstName"), value.get("lastName")) if part
                )
            )
        )
        if full_name:
            return {
                "fullName": full_name,
                "degree": value.get("degree")
                or value.get("relationship")
                or value.get("connectionDegree"),
            }
        stack.extend(child for child in value.values() if isinstance(child, dict | list))
        for child in value.values():
            if isinstance(child, list):
                stack.extend(child)
    return {}


def _identity_match(
    candidate_name: str, displayed_name: str | None, profile_identity: dict[str, Any]
) -> dict[str, Any]:
    api_name = profile_identity.get("fullName")
    api_matches = _names_compatible(candidate_name, api_name) if isinstance(api_name, str) else None
    displayed_matches = (
        _names_compatible(candidate_name, displayed_name) if displayed_name else None
    )
    return {
        "candidateName": candidate_name,
        "apiName": api_name if isinstance(api_name, str) else None,
        "displayedName": displayed_name,
        "apiMatches": api_matches,
        "displayedMatches": displayed_matches,
        "matched": api_matches is True or displayed_matches is True,
    }


def _names_compatible(candidate_name: str, loaded_name: str | None) -> bool:
    candidate_tokens = _name_tokens(candidate_name)
    loaded_tokens = _name_tokens(loaded_name or "")
    if not candidate_tokens or not loaded_tokens:
        return False
    loaded_text = " ".join(loaded_tokens)
    candidate_text = " ".join(candidate_tokens)
    if candidate_text in loaded_text:
        return True
    if len(candidate_tokens) == 1:
        return candidate_tokens[0] in loaded_tokens
    first = candidate_tokens[0]
    last = candidate_tokens[-1]
    if first not in loaded_tokens:
        return False
    if len(last) == 1:
        return any(token.startswith(last) for token in loaded_tokens)
    return last in loaded_tokens


def _name_tokens(value: str) -> list[str]:
    return [part for part in re.split(r"[^A-Za-z0-9]+", value.casefold()) if part]


def _relationship_from_degree(value: object) -> str | None:
    text = str(value or "").casefold()
    if text in {"1", "1st"} or "first" in text:
        return "1st"
    if text in {"2", "2nd"} or "second" in text:
        return "2nd"
    if text in {"3", "3rd"} or "third" in text:
        return "3rd"
    return None


async def _extract_salesnav_research(
    page: Any, candidate: AcceptedDraftCandidate
) -> dict[str, Any]:
    warnings: list[str] = []
    if not candidate.profile_url:
        return {"warnings": ["candidate has no Sales Navigator profile URL"]}
    await page.goto(candidate.profile_url, wait_until="domcontentloaded", timeout=45000)
    await _wait_for_load(page)
    block = await _classify_page(page)
    if block.is_blocking:
        warnings.append(f"Sales Navigator page blocked: {block.reason}")
    title = await _text_from_first(
        page,
        (
            "[data-anonymize='headline']",
            "[data-anonymize='title']",
        ),
    )
    company = await _text_from_first(
        page,
        (
            "[data-anonymize='company-name']",
            "[data-anonymize='company']",
        ),
    )
    if not title and not company:
        warnings.append("Sales Navigator title/company selectors did not produce evidence")
    return {
        "name": await _profile_name(page),
        "title": title,
        "company": company,
        "location": await _text_from_first(page, ("[data-anonymize='location']",)),
        "url": page.url,
        "warnings": warnings,
    }


async def _text_from_first(page: Any, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        locator = page.locator(selector).first
        if not await _locator_count(locator):
            continue
        text = _clean(await locator.text_content(timeout=1500))
        if text:
            return text
    return None


async def _public_web_research(
    page: Any,
    candidate: AcceptedDraftCandidate,
    sales_nav: dict[str, Any],
    *,
    public_web: bool,
    max_web_results: int,
) -> dict[str, Any]:
    if not public_web:
        return {"query": None, "results": [], "warnings": ["public web research disabled"]}
    query = _clean(
        " ".join(
            str(part)
            for part in (
                candidate.name,
                sales_nav.get("company"),
                sales_nav.get("title"),
                "contract hiring product engineering AI workflow",
            )
            if part
        )
    )
    if not query:
        return {
            "query": None,
            "results": [],
            "warnings": ["not enough evidence to build public web query"],
        }
    try:
        await page.goto(
            f"https://duckduckgo.com/html/?q={quote_plus(query)}",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await _wait_for_load(page)
        results = await page.locator(".result").evaluate_all(
            """(items, maxItems) => {
              const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
              const selected = [];
              for (const item of items) {
                if (maxItems > 0 && selected.length >= maxItems) break;
                const link = item.querySelector(".result__a");
                const snippet = item.querySelector(".result__snippet");
                const row = {
                  title: clean(link ? link.textContent : null) || null,
                  url: link ? link.getAttribute("href") : null,
                  snippet: clean(snippet ? snippet.textContent : null) || null,
                };
                if (row.title || row.url || row.snippet) selected.push(row);
              }
              return selected;
            }""",
            max_web_results,
        )
        return {
            "query": query,
            "results": [
                {
                    "title": row.get("title"),
                    "url": _normalize_duckduckgo_href(row.get("url")),
                    "snippet": row.get("snippet"),
                }
                for row in results
                if isinstance(row, dict)
            ],
            "warnings": [] if results else ["public web search returned no structured results"],
        }
    except Exception as exc:
        return {
            "query": query,
            "results": [],
            "warnings": [f"public web research failed: {exc}"],
        }


def _normalize_duckduckgo_href(href: object) -> str | None:
    if not isinstance(href, str) or not href:
        return None
    parsed = urlparse(href)
    params = parse_qs(parsed.query)
    uddg = params.get("uddg", [None])[0]
    return unquote(uddg) if uddg else href


def _window[T](items: list[T], *, offset: int, limit: int) -> list[T]:
    start = max(0, offset)
    if limit > 0:
        return items[start : start + limit]
    return items[start:]


def _acceptance_artifact_payload(
    *,
    input_path: Path,
    candidates: list[AcceptanceCheckCandidate],
    rows: list[dict[str, Any]],
    offset: int,
    limit: int,
    complete: bool,
) -> dict[str, Any]:
    return {
        "capturedAt": _now_iso(),
        "input": str(input_path),
        "count": len(rows),
        "offset": offset,
        "limit": limit,
        "totalCandidates": len(candidates),
        "complete": complete,
        "rows": rows,
    }


def _accepted_research_payload(
    *,
    input_path: Path,
    candidates: list[AcceptedDraftCandidate],
    rows: list[dict[str, Any]],
    offset: int,
    limit: int,
    complete: bool,
) -> dict[str, Any]:
    return {
        "capturedAt": _now_iso(),
        "input": str(input_path),
        "count": len(rows),
        "offset": offset,
        "limit": limit,
        "totalCandidates": len(candidates),
        "complete": complete,
        "rows": rows,
    }


def _json_evidence(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)[:1000]


async def _locator_count(locator: Any) -> int:
    return int(await locator.count())


async def _locator_disabled(locator: Any) -> bool:
    return bool(await _ignore_errors(locator.is_disabled(), False))


async def _locator_visible(locator: Any) -> bool:
    return bool(await _ignore_errors(locator.is_visible(), False))


async def _locator_has_visible_element(locator: Any) -> bool:
    count = await _locator_count(locator)
    for index in range(count):
        item = locator.nth(index)
        if await _locator_visible(item):
            return True
        box = await _ignore_errors(item.bounding_box(), None)
        if box and float(box.get("width", 0)) > 0 and float(box.get("height", 0)) > 0:
            return True
    return False


async def _ignore_errors[T](
    awaitable: Awaitable[T],
    default: T | None = None,
) -> T | None:
    try:
        return await awaitable
    except Exception:
        return default


async def _noop_async() -> None:
    return None


def _same_sales_profile(expected_url: str, actual_url: str) -> bool:
    expected = sales_profile_id_from_url(expected_url)
    actual = sales_profile_id_from_url(actual_url)
    return expected is not None and actual is not None and expected == actual


def _absolute_linkedin_url(url: str | None) -> str | None:
    if url is None or url == "":
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return "https://www.linkedin.com" + (url if url.startswith("/") else "/" + url)


def _state_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("menuState") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _count_state(rows: list[dict[str, Any]], state: str) -> int:
    return sum(1 for row in rows if row.get("menuState") == state)


def _int_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-") or "artifact"
