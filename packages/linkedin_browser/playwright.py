"""Playwright launch helpers for the LinkedIn Chrome profile."""

from __future__ import annotations

from playwright.async_api import BrowserContext, Playwright

from .config import ChromeProfileConfig, chrome_profile_from_env


async def launch_linkedin_chrome(
    playwright: Playwright,
    config: ChromeProfileConfig | None = None,
) -> BrowserContext:
    selected = config or chrome_profile_from_env()
    return await playwright.chromium.launch_persistent_context(
        user_data_dir=str(selected.user_data_dir),
        channel=selected.channel,
        headless=selected.headless,
        args=selected.launch_args(),
    )
