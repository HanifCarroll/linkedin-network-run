"""Live browser adapter for recruiter/agency LinkedIn messages."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any, TypeVar

from apps.network_automation.browser import (
    _classify_page,
    _escape,
    _fill_composer,
    _fill_subject_if_present,
    _find_composer,
    _find_message_action,
    _find_send_message_button,
    _locator_count,
    _locator_disabled,
    _locator_visible,
    _medium_wait,
    _noop_async,
    _profile_name,
    _safe_stem,
    _same_sales_profile,
    _wait_for_load,
)
from apps.network_automation.store import write_json_atomic
from packages.linkedin_browser import (
    BrowserContextHandle,
    BrowserSession,
    PageReusePolicy,
    RealAction,
    RealActionApproval,
    close_browser_context_handle,
    guarded_click,
    open_linkedin_browser_context,
)
from packages.linkedin_salesnav import (
    CandidateIdentity,
    MessageActionCandidate,
    guarded_message_click,
)

from .send import MessageSendResult

DEFAULT_MESSAGE_OUT_DIR = Path("/tmp/recruiter-agency-outreach-message")
SALES_NAV_INMAIL_ACTION = "button[data-anchor-send-inmail]"
COMPOSER_WAIT_ATTEMPTS = 20
COMPOSER_WAIT_MS = 500
ResultT = TypeVar("ResultT")


class PlaywrightMessageBrowserClient:
    """Playwright-backed adapter for guarded recruiter/agency messages."""

    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_MESSAGE_OUT_DIR,
        context: Any | None = None,
        context_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self.out_dir = out_dir
        self._context = context
        self._context_factory = context_factory
        self._context_handle_ref: BrowserContextHandle | None = None
        self._playwright_manager: Any | None = None
        self._playwright: Any | None = None
        self._loop = asyncio.new_event_loop()
        self._counter = 0

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

    def send_message(
        self,
        config: Mapping[str, Any],
        *,
        dry_run: bool,
        allow_send: bool,
    ) -> tuple[MessageSendResult, str]:
        if not dry_run and not allow_send:
            raise RuntimeError("real send requires allow_send=True")
        return self._run(
            self._send_message(config, dry_run=dry_run, allow_send=allow_send)
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

    async def _page(self) -> Any:
        session = BrowserSession(
            await self._context_handle(),
            PageReusePolicy(
                preferred_url_fragments=(
                    "linkedin.com/sales/lead/",
                    "linkedin.com/sales/search/people",
                ),
                foreground=False,
            ),
        )
        return await session.page(
            preferred_url_fragments=(
                "linkedin.com/sales/lead/",
                "linkedin.com/sales/search/people",
            )
        )

    async def _send_message(
        self,
        config: Mapping[str, Any],
        *,
        dry_run: bool,
        allow_send: bool,
    ) -> tuple[MessageSendResult, str]:
        candidate = _candidate(config)
        message = str(config.get("message") or "").replace("\r\n", "\n").strip()
        subject = str(config.get("subject") or "").strip()
        if not candidate.get("profileUrl"):
            raise RuntimeError("candidate with profileUrl is required")
        if not message:
            raise RuntimeError("message is required")

        page = await self._page()
        await page.goto(str(candidate["profileUrl"]), wait_until="domcontentloaded", timeout=30000)
        await _wait_for_load(page)
        payload = _result_base(candidate, message, dry_run=dry_run, url=page.url)
        block = await _classify_page(page)
        if block.is_blocking:
            payload.update({"status": "blocked", "reason": block.reason})
            return self._write_result(str(candidate["id"]), payload)
        if not _same_sales_profile(str(candidate["profileUrl"]), page.url):
            payload.update({"status": "identity-mismatch", "reason": "loaded URL differs"})
            return self._write_result(str(candidate["id"]), payload)

        profile_name = await _profile_name(page)
        action = await _find_message_action(page)
        if action is None:
            payload["status"] = "not-messageable"
            return self._write_result(str(candidate["id"]), payload)

        safety_action = MessageActionCandidate(
            kind=action["kind"],
            action_label=action["label"],
            identity_label=profile_name or str(candidate["name"]),
            source="profile-actions",
            opened_page_url=page.url,
        )
        if dry_run:
            click_result = await guarded_message_click(
                CandidateIdentity(
                    name=str(candidate["name"]),
                    profile_url=str(candidate["profileUrl"]),
                    candidate_id=str(candidate["id"]),
                ),
                safety_action,
                lambda: _noop_async(),
                dry_run=True,
            )
            status = (
                "dry-run-messageable"
                if click_result.safety.status == "ok"
                else click_result.status
            )
            payload.update(
                {"status": status, "action": click_result.safety.__dict__}
            )
            return self._write_result(str(candidate["id"]), payload)

        action_click = await _click_message_action(page, action)
        composer = await _wait_for_message_composer(page)
        if composer is None:
            payload.update(
                {
                    "status": "composer-missing",
                    "action": safety_action.__dict__,
                    "actionClick": action_click,
                }
            )
            return self._write_result(str(candidate["id"]), payload)
        subject_fill = await _fill_subject_if_present(page, subject)
        body_fill = await _fill_composer(composer, message)
        payload.update(
            {
                "action": safety_action.__dict__,
                "actionClick": action_click,
                "composerSelector": composer["selector"],
                "subjectFill": subject_fill,
                "bodyFill": body_fill,
            }
        )
        send_button = await _find_send_message_button(page)
        if send_button is None:
            payload["status"] = "send-button-missing"
            return self._write_result(str(candidate["id"]), payload)

        async def click_send() -> None:
            await send_button.click(timeout=8000)

        guard = await guarded_click(
            RealAction.SEND_MESSAGE,
            click_send,
            label="Send",
            candidate_id=str(candidate["id"]),
            dry_run=False,
            approval=RealActionApproval(RealAction.SEND_MESSAGE, allow=allow_send),
        )
        await _medium_wait(page)
        payload.update({"status": "sent-clicked", "send": guard.__dict__})
        await _escape(page)
        return self._write_result(str(candidate["id"]), payload)

    def _write_result(
        self,
        stem: str,
        payload: dict[str, Any],
    ) -> tuple[MessageSendResult, str]:
        self._counter += 1
        path = self.out_dir / f"{self._counter:03d}-{_safe_stem(stem)}.json"
        write_json_atomic(path, payload)
        return MessageSendResult.from_mapping(payload), str(path)


async def _click_message_action(page: Any, action: Mapping[str, Any]) -> dict[str, Any]:
    inmail = page.locator(SALES_NAV_INMAIL_ACTION).first
    if (
        await _locator_count(inmail)
        and await _locator_visible(inmail)
        and not await _locator_disabled(inmail)
    ):
        box = await inmail.bounding_box()
        if box and box.get("width") and box.get("height"):
            x = max(1.0, min(8.0, float(box["width"]) - 1.0))
            y = max(1.0, min(float(box["height"]) / 2.0, float(box["height"]) - 1.0))
            await inmail.click(position={"x": x, "y": y}, timeout=8000)
            return {
                "method": "salesnav-inmail-padding-click",
                "selector": SALES_NAV_INMAIL_ACTION,
                "position": {"x": x, "y": y},
            }
        await inmail.click(timeout=8000)
        return {
            "method": "salesnav-inmail-default-click",
            "selector": SALES_NAV_INMAIL_ACTION,
        }

    locator = action["locator"]
    await locator.click(timeout=8000)
    return {
        "method": "generic-message-action-click",
        "label": str(action.get("label") or ""),
    }


async def _wait_for_message_composer(page: Any) -> dict[str, Any] | None:
    for _ in range(COMPOSER_WAIT_ATTEMPTS):
        composer = await _find_composer(page)
        if composer is not None:
            return composer
        await page.wait_for_timeout(COMPOSER_WAIT_MS)
    return await _find_composer(page)


def _candidate(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config.get("candidate")
    if not isinstance(raw, Mapping):
        raise RuntimeError("candidate is required")
    return dict(raw)


def _result_base(
    candidate: Mapping[str, Any],
    message: str,
    *,
    dry_run: bool,
    url: str,
) -> dict[str, Any]:
    return {
        "candidate": dict(candidate),
        "dryRun": dry_run,
        "url": url,
        "messageLength": len(message),
        "status": "unknown",
    }
