"""Browser-backed LinkedIn post comment extraction."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from apps.comment_extractor.linkedin_post_comments import (
    extract_comments_from_html,
    write_raw_comments_jsonl,
)
from apps.opportunity_intel.sources import load_query_pack
from apps.opportunity_intel.store import OpportunityStore
from packages.linkedin_browser.artifacts import ArtifactWriter
from packages.linkedin_browser.config import ChromeProfileConfig, chrome_profile_from_env
from packages.linkedin_browser.playwright import open_linkedin_browser_context

MORE_COMMENTS_PATTERN = re.compile(
    r"^(load|show|view|see) (more|previous) comments?$",
    re.IGNORECASE,
)
MORE_REPLIES_PATTERN = re.compile(
    r"^(load|show|view|see) (more|previous)? ?repl(?:y|ies)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrowserSafetyLimits:
    max_scrolls: int = 6
    max_comment_control_clicks: int = 12
    max_reply_control_clicks: int = 8
    navigation_timeout_ms: int = 30_000
    action_timeout_ms: int = 5_000
    settle_ms: int = 750
    max_runtime_seconds: int = 90


@dataclass(frozen=True)
class BrowserExtractionInput:
    post_url: str
    source_id: str
    query_id: str
    source_kind: str = "known_post"
    source_url: str = ""
    search_query: str = ""


@dataclass(frozen=True)
class BrowserExtractionResult:
    run_id: str
    status: str
    raw_comments_path: Path
    html_artifact_path: Path
    comments_found: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class BrowserPreflightResult:
    ready: bool
    profile_name: str
    user_data_dir: Path
    profile_dir: Path
    profile_root_exists: bool
    profile_dir_exists: bool
    browser_checked: bool
    warnings: tuple[str, ...]

    def to_json_object(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "profile_name": self.profile_name,
            "user_data_dir": str(self.user_data_dir),
            "profile_dir": str(self.profile_dir),
            "profile_root_exists": self.profile_root_exists,
            "profile_dir_exists": self.profile_dir_exists,
            "browser_checked": self.browser_checked,
            "warnings": list(self.warnings),
        }


def run_browser_preflight(
    *,
    config: ChromeProfileConfig | None = None,
    check_browser: bool = False,
    cdp_url: str | None = None,
) -> BrowserPreflightResult:
    return asyncio.run(
        browser_preflight(config=config, check_browser=check_browser, cdp_url=cdp_url)
    )


async def browser_preflight(
    *,
    config: ChromeProfileConfig | None = None,
    check_browser: bool = False,
    cdp_url: str | None = None,
) -> BrowserPreflightResult:
    selected = config or chrome_profile_from_env()
    profile_dir = selected.user_data_dir / selected.profile_name
    warnings: list[str] = []
    root_exists = selected.user_data_dir.exists()
    profile_exists = profile_dir.exists()
    if not root_exists:
        warnings.append("chrome_user_data_dir_missing")
    if not profile_exists:
        warnings.append("chrome_profile_dir_missing")
    if check_browser:
        try:
            async with async_playwright() as playwright:
                handle = await open_linkedin_browser_context(
                    playwright,
                    selected,
                    cdp_url=cdp_url,
                )
                if handle.close_context:
                    await handle.context.close()
                if handle.browser is not None:
                    await handle.browser.close()
        except Exception as exc:
            warnings.append(f"browser_check_failed:{type(exc).__name__}")
    return BrowserPreflightResult(
        ready=not warnings,
        profile_name=selected.profile_name,
        user_data_dir=selected.user_data_dir,
        profile_dir=profile_dir,
        profile_root_exists=root_exists,
        profile_dir_exists=profile_exists,
        browser_checked=check_browser,
        warnings=tuple(warnings),
    )


def extract_post_comments_from_url(
    *,
    input_row: BrowserExtractionInput,
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    config: ChromeProfileConfig | None = None,
    cdp_url: str | None = None,
) -> BrowserExtractionResult:
    return asyncio.run(
        extract_post_comments_from_url_async(
            input_row=input_row,
            output_dir=output_dir,
            store=store,
            limits=limits,
            config=config,
            cdp_url=cdp_url,
        )
    )


async def extract_post_comments_from_url_async(
    *,
    input_row: BrowserExtractionInput,
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    config: ChromeProfileConfig | None = None,
    cdp_url: str | None = None,
) -> BrowserExtractionResult:
    selected = config or chrome_profile_from_env()
    run_id = store.start_extraction_run(
        post_url=input_row.post_url,
        source_id=input_row.source_id,
        query_id=input_row.query_id,
        source_kind=input_row.source_kind,
        source_url=input_row.source_url,
        search_query=input_row.search_query,
        browser_profile=selected.profile_name,
        safety_limits=asdict(limits),
    )
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    writer = ArtifactWriter(run_dir)
    html_path = run_dir / "post.html"
    raw_path = run_dir / "raw_comments.jsonl"
    warnings: tuple[str, ...] = ()
    try:
        async with async_playwright() as playwright:
            handle = await open_linkedin_browser_context(
                playwright,
                selected,
                cdp_url=cdp_url,
            )
            if handle.context.pages:
                page = handle.context.pages[0]
            else:
                page = await handle.context.new_page()
            page.set_default_timeout(limits.action_timeout_ms)
            await page.goto(
                input_row.post_url,
                wait_until="domcontentloaded",
                timeout=limits.navigation_timeout_ms,
            )
            await _expand_visible_comment_controls(page, limits)
            html = await page.content()
            html_path.write_text(html, encoding="utf-8")
            store.record_artifact(run_id=run_id, kind="html", path=html_path)
            screenshot = await writer.screenshot(page, "post-comments", full_page=True)
            store.record_artifact(run_id=run_id, kind="screenshot", path=screenshot.path)
            result = extract_comments_from_html(
                html=html,
                post_url=input_row.post_url,
                source_id=input_row.source_id,
                query_id=input_row.query_id,
                source_kind=input_row.source_kind,
                source_url=input_row.source_url,
                search_query=input_row.search_query,
            )
            warnings = result.warnings
            raw_path = write_raw_comments_jsonl(result.comments, run_dir)
            store.record_artifact(
                run_id=run_id,
                kind="raw_comments",
                path=raw_path,
                metadata={"comment_count": len(result.comments)},
            )
            store.persist_comments(
                run_id=run_id,
                comments=result.comments,
                query_pack=load_query_pack(),
            )
            summary_ref = writer.write_json(
                "summary",
                {
                    "run_id": run_id,
                    "post_url": input_row.post_url,
                    "comments_found": len(result.comments),
                    "warnings": list(result.warnings),
                    "safety_limits": asdict(limits),
                },
            )
            store.record_artifact(run_id=run_id, kind="summary", path=summary_ref.path)
            store.finish_extraction_run(
                run_id,
                status="extracted",
                comments_found=len(result.comments),
                failures=0,
                warning_count=len(result.warnings),
                retry_recommendation=(
                    "No retry needed" if result.comments else "Review HTML artifact"
                ),
            )
            if handle.close_context:
                await handle.context.close()
            if handle.browser is not None:
                await handle.browser.close()
    except Exception as exc:
        store.record_error(
            run_id=run_id,
            post_url=input_row.post_url,
            error_type=type(exc).__name__,
            message=str(exc),
            retryable=True,
        )
        store.finish_extraction_run(
            run_id,
            status="failed",
            comments_found=0,
            failures=1,
            warning_count=0,
            retry_recommendation="Inspect browser artifacts and rerun with lower safety limits",
        )
        raise
    return BrowserExtractionResult(
        run_id=run_id,
        status="extracted",
        raw_comments_path=raw_path,
        html_artifact_path=html_path,
        comments_found=sum(1 for _ in raw_path.read_text(encoding="utf-8").splitlines()),
        warnings=warnings,
    )


async def _expand_visible_comment_controls(page: Page, limits: BrowserSafetyLimits) -> None:
    deadline = asyncio.get_running_loop().time() + limits.max_runtime_seconds
    for _ in range(limits.max_scrolls):
        if asyncio.get_running_loop().time() >= deadline:
            break
        await _click_controls(page, MORE_COMMENTS_PATTERN, limits.max_comment_control_clicks)
        await _click_controls(page, MORE_REPLIES_PATTERN, limits.max_reply_control_clicks)
        await page.mouse.wheel(0, 1800)
        await page.wait_for_timeout(limits.settle_ms)


async def _click_controls(page: Page, pattern: re.Pattern[str], limit: int) -> None:
    locator = page.get_by_role("button", name=pattern)
    try:
        count = min(await locator.count(), limit)
    except PlaywrightTimeoutError:
        return
    for index in range(count):
        button = locator.nth(index)
        try:
            if await button.is_visible() and await button.is_enabled():
                await button.click()
                await page.wait_for_timeout(250)
        except PlaywrightTimeoutError:
            continue


def write_preflight_artifact(
    *,
    store: OpportunityStore,
    result: BrowserPreflightResult,
) -> Path:
    run_id = store.start_extraction_run(
        post_url="",
        source_id="preflight",
        query_id="preflight",
        source_kind="preflight",
        source_url="",
        search_query="",
        browser_profile=result.profile_name,
        safety_limits={},
        status="preflight",
    )
    output_dir = store.artifact_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "browser_preflight.json"
    path.write_text(json.dumps(result.to_json_object(), indent=2, sort_keys=True) + "\n")
    store.record_artifact(
        run_id=run_id,
        kind="browser_preflight",
        path=path,
        status="ok" if result.ready else "warning",
        retryable_error=";".join(result.warnings),
    )
    store.finish_extraction_run(
        run_id,
        status="preflight_passed" if result.ready else "preflight_warning",
        comments_found=0,
        failures=0 if result.ready else 1,
        warning_count=len(result.warnings),
        retry_recommendation="No retry needed" if result.ready else "Fix browser setup warnings",
    )
    return path
