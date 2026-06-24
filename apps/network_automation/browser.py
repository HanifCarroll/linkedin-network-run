"""Browser adapter interfaces and Playwright implementation for guarded network actions."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from packages.linkedin_browser import (
    BrowserContextHandle,
    BrowserSession,
    PageReusePolicy,
    RealAction,
    RealActionApproval,
    classify_browser_state,
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
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    CandidateObservation,
    PendingCandidateObservation,
    PendingWithdrawResult,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendResult,
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
        followup_result: Path | None = None,
        withdraw_result: Path | None = None,
    ) -> None:
        self.send_result = send_result
        self.capture = capture
        self.audit = audit
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


class PlaywrightBrowserClient:
    """Playwright-backed browser client using the shared LinkedIn Chrome profile."""

    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_SEND_OUT_DIR,
        context: Any | None = None,
        context_factory: Callable[[], Awaitable[Any]] | None = None,
        max_load_more: int = 260,
    ) -> None:
        self.out_dir = out_dir
        self._context = context
        self._context_factory = context_factory
        self._context_handle_ref: BrowserContextHandle | None = None
        self._playwright_manager: Any | None = None
        self._playwright: Any | None = None
        self._loop = asyncio.new_event_loop()
        self._counter = 0
        self.max_load_more = max_load_more

    def close(self) -> None:
        async def _close() -> None:
            should_close_context = (
                self._context_handle_ref is None or self._context_handle_ref.close_context
            )
            if (
                should_close_context
                and self._context is not None
                and hasattr(self._context, "close")
            ):
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
        return self._run(
            self._capture_salesnav(
                source=source,
                url=url,
                pages=pages,
                limit=limit,
                stop_after_connectable=stop_after_connectable,
                only_connectable=only_connectable,
                row_scroll_delay_ms=row_scroll_delay_ms,
            )
        )

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        return self._run(self._audit_sent_invitations(load_more=load_more))

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
            self._withdraw_pending(candidate, dry_run=dry_run, allow_withdraw=allow_withdraw)
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
        self._context_handle_ref = await open_linkedin_browser_context(self._playwright)
        self._context = self._context_handle_ref.context
        return self._context

    async def _page(self, fragments: tuple[str, ...]) -> Any:
        session = BrowserSession(
            await self._context_handle(),
            PageReusePolicy(preferred_url_fragments=fragments),
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
        page = await self._page(("linkedin.com/sales/lead/", "linkedin.com/sales/search/people"))
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

            guard = await guarded_connection_request(
                identity,
                click_connect,
                dry_run=False,
                approval=RealActionApproval(RealAction.SEND_CONNECTION, allow=allow_send),
            )
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
                    "pending-verified"
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
        page = await self._page(("linkedin.com/sales/search/people", "linkedin.com/sales/lead/"))
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await _wait_for_load(page)
        all_rows: list[dict[str, Any]] = []
        page_summaries: list[dict[str, Any]] = []
        for page_number in range(1, max(1, pages) + 1):
            await _short_wait(page)
            page_summaries.append({"url": page.url, "pageLabel": None})
            row_locators = await page.locator(SALES_NAV_PEOPLE_RESULT_ROW).all()
            for row_index, row in enumerate(row_locators[:limit]):
                await _ignore_errors(row.scroll_into_view_if_needed())
                if row_scroll_delay_ms > 0:
                    await page.wait_for_timeout(row_scroll_delay_ms)
                item = await _capture_salesnav_row(row, row_index, len(all_rows), page_number)
                if item["profileUrl"] is None and item["scrollUrn"]:
                    item["profileUrl"] = sales_profile_urn_to_lead_url(item["scrollUrn"])
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
            "menuInspection": "opened-row-overflow-menus",
            "filters": {"onlyConnectable": only_connectable},
            "captureOptions": {
                "limit": limit,
                "pages": pages,
                "stopAfterConnectable": stop_after_connectable,
                "rowScrollDelayMs": row_scroll_delay_ms,
                "openMenus": True,
            },
            "stateCounts": _state_counts(all_rows),
            "rawRowCount": len(all_rows),
            "outputRowCount": len(output_rows),
            "rows": output_rows,
        }
        return self._write_result("capture-page", payload, SalesNavCapture)

    async def _audit_sent_invitations(self, *, load_more: int) -> tuple[SalesNavAudit, str]:
        page = await self._page(("linkedin.com/mynetwork/invitation-manager/sent", "linkedin.com"))
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
                "dry-run-messageable"
                if click_result.safety.status == "ok"
                else click_result.status
            )
            payload.update(
                {"status": status, "action": click_result.safety.__dict__}
            )
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
    evidence = BrowserStateEvidence(
        url=page.url,
        http_status=http_status,
        login_form_present=login > 0,
        checkpoint_present=checkpoint > 0,
    )
    return classify_browser_state(evidence)


def _response_status(response: Any | None) -> int | None:
    status = getattr(response, "status", None)
    return status if isinstance(status, int) else None


async def _wait_for_load(page: Any) -> None:
    await _ignore_errors(page.wait_for_load_state("domcontentloaded", timeout=10000))
    await _medium_wait(page)


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
    await trigger.click(timeout=8000)
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


async def _open_row_menu(page: Any, row: Any) -> dict[str, Any]:
    trigger = row.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first
    if not await _locator_count(trigger):
        return {"state": "missing-trigger", "labels": []}
    menu_id = await trigger.get_attribute("aria-controls")
    await trigger.click(timeout=5000)
    await _short_wait(page)
    menu = (
        page.locator(f"#{menu_id}").first
        if menu_id
        else page.locator("[data-popper-placement]").last
    )
    if not await _locator_count(menu):
        return {"state": "missing-menu", "labels": [], "menu_id": menu_id}
    labels = await _menu_labels(menu)
    await _escape(page)
    return {"state": _classify_menu_labels(labels), "labels": labels, "menu_id": menu_id}


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
    button = menu.get_by_text(re.compile(f"^{re.escape(label)}$", re.I)).first
    if not await _locator_count(button):
        raise RuntimeError(f"{label} menu item missing")
    await button.click(timeout=8000)


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


async def _locator_count(locator: Any) -> int:
    return int(await locator.count())


async def _locator_disabled(locator: Any) -> bool:
    return bool(await _ignore_errors(locator.is_disabled(), False))


async def _locator_visible(locator: Any) -> bool:
    return bool(await _ignore_errors(locator.is_visible(), False))


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


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-") or "artifact"
