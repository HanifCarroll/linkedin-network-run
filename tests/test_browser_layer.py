from __future__ import annotations

from pathlib import Path

import pytest

from packages.linkedin_browser import (
    DEFAULT_BROWSER_PROFILE_NAME,
    ArtifactWriter,
    BrowserBlockKind,
    BrowserSession,
    BrowserStateEvidence,
    ChromeProfileConfig,
    PageReusePolicy,
    RealAction,
    RealActionApproval,
    UnsafeRealActionError,
    choose_reusable_page,
    chrome_profile_from_env,
    classify_browser_state,
    guarded_click,
)
from packages.linkedin_browser.playwright import (
    launch_linkedin_chrome,
    open_linkedin_browser_context,
)


class FakePage:
    def __init__(self, url: str) -> None:
        self._url = url
        self.closed = False
        self.fronted = False
        self.screenshot_path: str | None = None

    @property
    def url(self) -> str:
        return self._url

    async def bring_to_front(self) -> None:
        self.fronted = True

    async def close(self) -> None:
        self.closed = True

    async def screenshot(self, *, path: str, full_page: bool = True) -> bytes:
        self.screenshot_path = path
        Path(path).write_bytes(b"png")
        return b"png"


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self._pages = pages
        self.created_pages = 0

    @property
    def pages(self) -> list[FakePage]:
        return self._pages

    async def new_page(self) -> FakePage:
        self.created_pages += 1
        page = FakePage("about:blank")
        self._pages.append(page)
        return page


class ProfileLockedChromium:
    async def launch_persistent_context(self, **kwargs: object) -> object:
        _ = kwargs
        raise RuntimeError(
            "BrowserType.launch_persistent_context: Opening in existing browser session."
        )


class ProfileLockedPlaywright:
    chromium = ProfileLockedChromium()


class FakeCdpBrowser:
    def __init__(self) -> None:
        self.contexts = [FakeContext([FakePage("https://www.linkedin.com/sales/lead/abc")])]


class FakeCdpChromium:
    def __init__(self) -> None:
        self.connected_url: str | None = None
        self.launched = False

    async def connect_over_cdp(self, url: str, *, timeout: int) -> FakeCdpBrowser:
        _ = timeout
        self.connected_url = url
        return FakeCdpBrowser()

    async def launch_persistent_context(self, **kwargs: object) -> object:
        _ = kwargs
        self.launched = True
        raise AssertionError("CDP attach should avoid profile launch")


class FakeCdpPlaywright:
    def __init__(self) -> None:
        self.chromium = FakeCdpChromium()


def test_profile_config_defaults_to_linkedin_profile() -> None:
    config = chrome_profile_from_env({})
    assert config.profile_name == DEFAULT_BROWSER_PROFILE_NAME
    assert config.user_data_dir.name == "Chrome"
    assert "--profile-directory=LinkedIn" in config.launch_args()


def test_profile_config_can_be_overridden() -> None:
    config = chrome_profile_from_env(
        {
            "LINKEDIN_TOOLS_CHROME_USER_DATA_DIR": "/tmp/chrome",
            "LINKEDIN_TOOLS_CHROME_PROFILE_NAME": "LinkedIn Test",
        }
    )
    assert config == ChromeProfileConfig(
        user_data_dir=Path("/tmp/chrome"),
        profile_name="LinkedIn Test",
    )


@pytest.mark.asyncio
async def test_launch_linkedin_chrome_explains_locked_profile() -> None:
    with pytest.raises(RuntimeError, match="Chrome profile is already open"):
        await launch_linkedin_chrome(
            ProfileLockedPlaywright(),  # type: ignore[arg-type]
            ChromeProfileConfig(profile_name="LinkedIn"),
        )


@pytest.mark.asyncio
async def test_open_linkedin_browser_context_prefers_cdp_attach() -> None:
    playwright = FakeCdpPlaywright()

    handle = await open_linkedin_browser_context(
        playwright,  # type: ignore[arg-type]
        cdp_url="ws://127.0.0.1:19988/cdp",
    )

    assert handle.close_context is False
    assert handle.context.pages[0].url == "https://www.linkedin.com/sales/lead/abc"
    assert playwright.chromium.connected_url == "ws://127.0.0.1:19988/cdp"
    assert playwright.chromium.launched is False


def test_choose_reusable_page_prefers_salesnav_page() -> None:
    sales = FakePage("https://www.linkedin.com/sales/lead/abc")
    other = FakePage("https://example.com")
    assert choose_reusable_page([other, sales], ("linkedin.com/sales/lead/",)) is sales


@pytest.mark.asyncio
async def test_browser_session_reuses_page_and_can_close_surplus() -> None:
    selected = FakePage("https://www.linkedin.com/sales/search/people")
    stale = FakePage("https://www.linkedin.com/feed/")
    context = FakeContext([selected, stale])
    session = BrowserSession(context, PageReusePolicy(keep_pages=1))

    page = await session.page(close_surplus=True)

    assert page is selected
    assert selected.fronted is True
    assert stale.closed is True
    assert context.created_pages == 0


@pytest.mark.asyncio
async def test_guarded_click_dry_run_does_not_click() -> None:
    clicked = False

    async def click() -> None:
        nonlocal clicked
        clicked = True

    result = await guarded_click(RealAction.SEND_CONNECTION, click, label="Connect")

    assert result.status == "dry-run-send-connection"
    assert result.clicked is False
    assert clicked is False


@pytest.mark.asyncio
async def test_guarded_click_requires_real_action_approval() -> None:
    async def click() -> None:
        raise AssertionError("click should not run")

    with pytest.raises(UnsafeRealActionError):
        await guarded_click(
            RealAction.SEND_MESSAGE,
            click,
            label="Send",
            dry_run=False,
            approval=None,
        )


@pytest.mark.asyncio
async def test_guarded_click_runs_with_matching_approval() -> None:
    clicked = False

    async def click() -> None:
        nonlocal clicked
        clicked = True

    result = await guarded_click(
        RealAction.WITHDRAW_INVITATION,
        click,
        label="Withdraw",
        dry_run=False,
        approval=RealActionApproval(RealAction.WITHDRAW_INVITATION, allow=True),
    )

    assert result.status == "withdraw-invitation-clicked"
    assert result.clicked is True
    assert clicked is True


def test_blocked_state_classifies_rate_limit_and_checkpoint() -> None:
    assert (
        classify_browser_state(BrowserStateEvidence(http_status=429)).kind
        is BrowserBlockKind.RATE_LIMITED
    )
    assert (
        classify_browser_state(
            BrowserStateEvidence(url="https://www.linkedin.com/checkpoint/challenge")
        ).kind
        is BrowserBlockKind.CHECKPOINT
    )


@pytest.mark.asyncio
async def test_artifact_writer_writes_json_and_screenshot(tmp_path: Path) -> None:
    page = FakePage("https://www.linkedin.com")
    writer = ArtifactWriter(tmp_path)

    refs = await writer.debug_bundle(name="blocked state", payload={"status": "blocked"}, page=page)

    assert [ref.kind for ref in refs] == ["json", "screenshot"]
    assert (tmp_path / "blocked-state.json").exists()
    assert (tmp_path / "blocked-state.png").exists()
