"""Playwright launch helpers for the LinkedIn Chrome profile."""

from __future__ import annotations

import os
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Playwright

from .config import (
    DEFAULT_PLAYWRITER_CDP_URL,
    LINKEDIN_CDP_URL_ENV,
    ChromeProfileConfig,
    chrome_profile_from_env,
)


@dataclass(frozen=True)
class BrowserContextHandle:
    context: BrowserContext
    close_context: bool
    browser: Browser | None = None


async def open_linkedin_browser_context(
    playwright: Playwright,
    config: ChromeProfileConfig | None = None,
    *,
    cdp_url: str | None = None,
) -> BrowserContextHandle:
    selected_cdp_url = _selected_cdp_url(cdp_url)
    if selected_cdp_url:
        try:
            browser = await playwright.chromium.connect_over_cdp(selected_cdp_url, timeout=1500)
        except Exception as exc:
            if cdp_url or os.environ.get(LINKEDIN_CDP_URL_ENV):
                raise RuntimeError(
                    f"could not connect to LinkedIn browser CDP endpoint {selected_cdp_url}"
                ) from exc
        else:
            if not browser.contexts:
                raise RuntimeError(
                    f"LinkedIn browser CDP endpoint {selected_cdp_url} has no browser contexts"
                )
            return BrowserContextHandle(
                context=browser.contexts[0],
                close_context=False,
                browser=browser,
            )
    return BrowserContextHandle(
        context=await launch_linkedin_chrome(playwright, config),
        close_context=True,
    )


async def launch_linkedin_chrome(
    playwright: Playwright,
    config: ChromeProfileConfig | None = None,
) -> BrowserContext:
    selected = config or chrome_profile_from_env()
    try:
        if selected.channel is None:
            return await playwright.chromium.launch_persistent_context(
                user_data_dir=str(selected.user_data_dir),
                headless=selected.headless,
                args=selected.launch_args(),
            )
        return await playwright.chromium.launch_persistent_context(
            user_data_dir=str(selected.user_data_dir),
            channel=selected.channel,
            headless=selected.headless,
            args=selected.launch_args(),
        )
    except Exception as exc:
        message = str(exc)
        if (
            "Opening in existing browser session" in message
            or "profile is already in use" in message
        ):
            raise RuntimeError(
                "Chrome profile is already open, so Python Playwright cannot launch "
                "the persistent LinkedIn profile. Close Chrome windows using the "
                f"{selected.profile_name!r} profile before running live dry-runs, or "
                "launch an attachable browser session before using the Python browser client."
            ) from exc
        raise


def _selected_cdp_url(cdp_url: str | None) -> str:
    if cdp_url is not None:
        return cdp_url.strip()
    return os.environ.get(LINKEDIN_CDP_URL_ENV, DEFAULT_PLAYWRITER_CDP_URL).strip()
