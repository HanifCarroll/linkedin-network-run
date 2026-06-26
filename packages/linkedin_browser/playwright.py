"""Playwright launch helpers for the LinkedIn Chrome profile."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Playwright

from .config import (
    DEFAULT_CHROME_USER_DATA_DIR,
    DEFAULT_PLAYWRITER_CDP_URL,
    LINKEDIN_CDP_URL_ENV,
    ChromeProfileConfig,
    chrome_profile_from_env,
    chrome_profile_storage_dir,
)

MANAGED_CHROME_CDP_STARTUP_TIMEOUT_SECONDS = 20.0
MANAGED_CHROME_CDP_CONNECT_TIMEOUT_MS = 10_000
MACOS_CHROME_EXECUTABLE = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
CHROME_DEFAULT_PROFILE_ERROR = (
    "Chrome 136+ does not allow Playwright to launch-control the default Chrome "
    "data directory with command-line remote debugging. Use "
    "LINKEDIN_TOOLS_BROWSER_PROFILE_MODE=automation for a dedicated installed-Chrome "
    "profile, or set LINKEDIN_TOOLS_CDP_URL to attach to an already-debuggable Chrome "
    "session."
)


@dataclass(frozen=True)
class BrowserContextHandle:
    context: BrowserContext
    close_context: bool
    browser: Browser | None = None
    managed_process: subprocess.Popen[str] | None = None


async def open_linkedin_browser_context(
    playwright: Playwright,
    config: ChromeProfileConfig | None = None,
    *,
    cdp_url: str | None = None,
) -> BrowserContextHandle:
    selected_cdp_url = _selected_cdp_url(cdp_url)
    if selected_cdp_url:
        try:
            browser = await playwright.chromium.connect_over_cdp(
                selected_cdp_url,
                timeout=1500,
                no_defaults=True,
            )
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
    selected = config or chrome_profile_from_env()
    return await launch_managed_chrome_cdp_context(playwright, selected)


async def close_browser_context_handle(handle: BrowserContextHandle) -> None:
    try:
        if handle.close_context:
            await handle.context.close()
        if handle.browser is not None:
            await handle.browser.close()
    finally:
        if handle.managed_process is not None:
            await asyncio.to_thread(_terminate_process, handle.managed_process)


async def launch_managed_chrome_cdp_context(
    playwright: Playwright,
    config: ChromeProfileConfig,
) -> BrowserContextHandle:
    if _is_default_chrome_user_data_dir(config.user_data_dir):
        raise RuntimeError(CHROME_DEFAULT_PROFILE_ERROR)
    port = _free_local_port()
    process = _launch_chrome_process(_managed_chrome_command(config, port))
    try:
        await asyncio.to_thread(
            _wait_for_local_port,
            port,
            process,
            MANAGED_CHROME_CDP_STARTUP_TIMEOUT_SECONDS,
        )
        browser = await playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}",
            timeout=MANAGED_CHROME_CDP_CONNECT_TIMEOUT_MS,
            no_defaults=True,
        )
        if not browser.contexts:
            raise RuntimeError("managed Chrome CDP session has no browser contexts")
        return BrowserContextHandle(
            context=browser.contexts[0],
            close_context=False,
            browser=browser,
            managed_process=process,
        )
    except Exception:
        await asyncio.to_thread(_terminate_process, process)
        raise


def _is_default_chrome_user_data_dir(path: os.PathLike[str] | str) -> bool:
    return os.fspath(Path(path).expanduser().resolve()) == os.fspath(
        DEFAULT_CHROME_USER_DATA_DIR.expanduser().resolve()
    )


def _managed_chrome_command(config: ChromeProfileConfig, port: int) -> list[str]:
    executable = _chrome_executable()
    user_data_dir = chrome_profile_storage_dir(config)
    return [
        str(executable),
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]


def _chrome_executable() -> Path:
    if MACOS_CHROME_EXECUTABLE.exists():
        return MACOS_CHROME_EXECUTABLE
    resolved = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if resolved:
        return Path(resolved)
    raise RuntimeError("Google Chrome executable was not found")


def _launch_chrome_process(command: Sequence[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _free_local_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_local_port(
    port: int,
    process: subprocess.Popen[str],
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(_chrome_process_exit_message(process))
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.25)
    raise RuntimeError(
        f"managed Chrome did not expose CDP port {port}. Chrome remote debugging may be "
        "disabled or unavailable on this machine; check chrome://policy for "
        "RemoteDebuggingAllowed, or set LINKEDIN_TOOLS_CDP_URL to an already-debuggable "
        "Chrome endpoint."
    )


def _chrome_process_exit_message(process: subprocess.Popen[str]) -> str:
    stderr = ""
    if process.stderr is not None:
        stderr = process.stderr.read()[-1000:]
    if stderr:
        return f"managed Chrome exited before CDP was ready: {stderr}"
    return "managed Chrome exited before CDP was ready"


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _selected_cdp_url(cdp_url: str | None) -> str:
    if cdp_url is not None:
        return cdp_url.strip()
    return os.environ.get(LINKEDIN_CDP_URL_ENV, DEFAULT_PLAYWRITER_CDP_URL).strip()
