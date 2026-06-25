"""Browser-backed LinkedIn post comment extraction."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from apps.comment_extractor.contracts import ExtractionResult
from apps.comment_extractor.linkedin_post_comments import (
    write_raw_comments_jsonl,
)
from apps.opportunity_intel.contracts import CommentEvidence
from apps.opportunity_intel.imports import write_comment_csv
from apps.opportunity_intel.sources import load_query_pack
from apps.opportunity_intel.store import OpportunityStore
from packages.linkedin_browser.artifacts import ArtifactWriter
from packages.linkedin_browser.config import ChromeProfileConfig, chrome_profile_from_env
from packages.linkedin_browser.playwright import (
    close_browser_context_handle,
    open_linkedin_browser_context,
)

MORE_COMMENTS_PATTERN = re.compile(
    r"^(load|show|view|see) (more|previous) comments?$",
    re.IGNORECASE,
)
MORE_REPLIES_PATTERN = re.compile(
    r"^(load|show|view|see) (more|previous)? ?repl(?:y|ies)$",
    re.IGNORECASE,
)
LIVE_COMMENT_ROOT_SELECTOR = (
    '.comments-comment-entity[data-id^="urn:li:comment:"],'
    '[componentkey^="replaceableComment_urn:li:comment:"]'
)
LIVE_COMMENT_TEXT_SELECTOR = (
    ".comments-comment-item__main-content,"
    "[data-comment-text],"
    "[data-test-comment-text],"
    "[data-test-comment-content]"
)
LIVE_COMMENT_PROFILE_SELECTOR = (
    'a.comments-comment-meta__description-container[href*="/in/"],'
    'a.comments-comment-meta__image-link[href*="/in/"],'
    'a[href^="/in/"],'
    'a[href*="linkedin.com/in/"]'
)
LIVE_COMMENT_NAME_SELECTOR = ".comments-comment-meta__description-title"
LIVE_COMMENT_HEADLINE_SELECTOR = ".comments-comment-meta__description-subtitle"


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
class BrowserQueueResult:
    processed: int
    succeeded: int
    failed: int
    manifest_path: Path
    checkpoint_path: Path
    provider_csv_path: Path | None


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
                    cdp_url=_comment_extraction_cdp_url(cdp_url),
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
    status = "extracted"
    comments_found = 0
    handle: Any | None = None
    try:
        async with async_playwright() as playwright:
            handle = await open_linkedin_browser_context(
                playwright,
                selected,
                cdp_url=_comment_extraction_cdp_url(cdp_url),
            )
            page = await _reusable_page(handle.context)
            page.set_default_timeout(limits.action_timeout_ms)
            post_result = await _extract_post_comments_with_page(
                page=page,
                input_row=input_row,
                run_id=run_id,
                run_dir=run_dir,
                writer=writer,
                store=store,
                limits=limits,
            )
            html_path = post_result.html_artifact_path
            raw_path = post_result.raw_comments_path
            warnings = post_result.warnings
            comments_found = post_result.comments_found
            status = post_result.status
            await _close_context_handle(handle)
            handle = None
    except Exception as exc:
        if handle is not None:
            await _close_context_handle(handle)
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
        status=status,
        raw_comments_path=raw_path,
        html_artifact_path=html_path,
        comments_found=comments_found,
        warnings=warnings,
    )


def extract_post_comments_from_url_queue(
    *,
    input_rows: Sequence[BrowserExtractionInput],
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    provider_csv_path: Path | None = None,
    config: ChromeProfileConfig | None = None,
    cdp_url: str | None = None,
) -> BrowserQueueResult:
    return asyncio.run(
        extract_post_comments_from_url_queue_async(
            input_rows=input_rows,
            output_dir=output_dir,
            store=store,
            limits=limits,
            provider_csv_path=provider_csv_path,
            config=config,
            cdp_url=cdp_url,
        )
    )


async def extract_post_comments_from_url_queue_async(
    *,
    input_rows: Sequence[BrowserExtractionInput],
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    provider_csv_path: Path | None = None,
    config: ChromeProfileConfig | None = None,
    cdp_url: str | None = None,
) -> BrowserQueueResult:
    selected = config or chrome_profile_from_env()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "extract_url_queue_manifest.jsonl"
    checkpoint_path = output_dir / "extract_url_queue_checkpoint.json"
    processed = 0
    succeeded = 0
    failed = 0
    handle: Any | None = None
    try:
        async with async_playwright() as playwright:
            handle = await open_linkedin_browser_context(
                playwright,
                selected,
                cdp_url=_comment_extraction_cdp_url(cdp_url),
            )
            page = await _reusable_page(handle.context)
            page.set_default_timeout(limits.action_timeout_ms)
            for input_row in input_rows:
                processed += 1
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
                try:
                    result = await _extract_post_comments_with_page(
                        page=page,
                        input_row=input_row,
                        run_id=run_id,
                        run_dir=run_dir,
                        writer=writer,
                        store=store,
                        limits=limits,
                    )
                except Exception as exc:
                    failed += 1
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
                        retry_recommendation=(
                            "Inspect browser artifacts and rerun with lower safety limits"
                        ),
                    )
                    _append_jsonl(
                        manifest_path,
                        {
                            "post_url": input_row.post_url,
                            "run_id": run_id,
                            "status": "failed",
                            "comments_found": 0,
                            "error_type": type(exc).__name__,
                        },
                    )
                else:
                    succeeded += 1
                    if provider_csv_path is not None:
                        write_comment_csv(provider_csv_path, store.export_comments())
                    _append_jsonl(
                        manifest_path,
                        {
                            "post_url": input_row.post_url,
                            "run_id": run_id,
                            "status": result.status,
                            "comments_found": result.comments_found,
                            "raw_comments_path": str(result.raw_comments_path),
                            "provider_csv_path": (
                                str(provider_csv_path) if provider_csv_path is not None else ""
                            ),
                        },
                    )
                _write_json_atomic(
                    checkpoint_path,
                    {
                        "processed": processed,
                        "succeeded": succeeded,
                        "failed": failed,
                        "total": len(input_rows),
                        "provider_csv_path": (
                            str(provider_csv_path) if provider_csv_path is not None else ""
                        ),
                    },
                )
            await _close_context_handle(handle)
            handle = None
    finally:
        if handle is not None:
            await _close_context_handle(handle)
    return BrowserQueueResult(
        processed=processed,
        succeeded=succeeded,
        failed=failed,
        manifest_path=manifest_path,
        checkpoint_path=checkpoint_path,
        provider_csv_path=provider_csv_path,
    )


async def _extract_post_comments_with_page(
    *,
    page: Page,
    input_row: BrowserExtractionInput,
    run_id: str,
    run_dir: Path,
    writer: ArtifactWriter,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
) -> BrowserExtractionResult:
    html_path = run_dir / "post.html"
    await page.goto(
        input_row.post_url,
        wait_until="domcontentloaded",
        timeout=limits.navigation_timeout_ms,
    )
    await _expand_visible_comment_controls(page, limits)
    html = await page.content()
    html_path.write_text(html, encoding="utf-8")
    store.record_artifact(run_id=run_id, kind="html", path=html_path)
    screenshot_warnings = await _capture_optional_screenshot(
        page=page,
        run_id=run_id,
        writer=writer,
        store=store,
    )
    extraction = await extract_comments_from_page(page=page, input_row=input_row)
    warnings = (*screenshot_warnings, *extraction.warnings)
    raw_path = write_raw_comments_jsonl(extraction.comments, run_dir)
    store.record_artifact(
        run_id=run_id,
        kind="raw_comments",
        path=raw_path,
        metadata={"comment_count": len(extraction.comments)},
    )
    store.persist_comments(
        run_id=run_id,
        comments=extraction.comments,
        query_pack=load_query_pack(),
    )
    summary_ref = writer.write_json(
        "summary",
        {
            "run_id": run_id,
            "post_url": input_row.post_url,
            "comments_found": len(extraction.comments),
            "warnings": list(warnings),
            "safety_limits": asdict(limits),
        },
    )
    store.record_artifact(run_id=run_id, kind="summary", path=summary_ref.path)
    store.finish_extraction_run(
        run_id,
        status="extracted",
        comments_found=len(extraction.comments),
        failures=0,
        warning_count=len(warnings),
        retry_recommendation="No retry needed" if extraction.comments else "Review HTML artifact",
    )
    return BrowserExtractionResult(
        run_id=run_id,
        status="extracted",
        raw_comments_path=raw_path,
        html_artifact_path=html_path,
        comments_found=len(extraction.comments),
        warnings=warnings,
    )


async def _capture_optional_screenshot(
    *,
    page: Page,
    run_id: str,
    writer: ArtifactWriter,
    store: OpportunityStore,
) -> tuple[str, ...]:
    try:
        screenshot = await writer.screenshot(page, "post-comments", full_page=True)
    except PlaywrightError as exc:
        return (f"screenshot_capture_failed:{type(exc).__name__}",)
    store.record_artifact(run_id=run_id, kind="screenshot", path=screenshot.path)
    return ()


async def extract_comments_from_page(
    *,
    page: Page,
    input_row: BrowserExtractionInput,
) -> ExtractionResult:
    rows = await page.locator(LIVE_COMMENT_ROOT_SELECTOR).evaluate_all(
        """
        (nodes, selectors) => {
          const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
          const selectText = (node, selector) => clean(node.querySelector(selector)?.textContent);
          const [profileSelector, textSelector, nameSelector, headlineSelector] = selectors;
          return nodes.map((node) => {
            const profile = node.querySelector(profileSelector);
            const time = node.querySelector("time");
            const name = selectText(node, nameSelector) || clean(profile?.textContent);
            return {
              comment_id: node.getAttribute("data-id") || node.getAttribute("componentkey") || "",
              commenter_headline: selectText(node, headlineSelector),
              commenter_name: name,
              commenter_profile_url: profile?.getAttribute("href") || "",
              comment_text: selectText(node, textSelector),
              commented_at: time?.getAttribute("datetime") || clean(time?.textContent),
            };
          });
        }
        """,
        arg=[
            LIVE_COMMENT_PROFILE_SELECTOR,
            LIVE_COMMENT_TEXT_SELECTOR,
            LIVE_COMMENT_NAME_SELECTOR,
            LIVE_COMMENT_HEADLINE_SELECTOR,
        ],
    )
    return _comments_from_page_rows(rows, input_row=input_row)


def _comments_from_page_rows(
    rows: object,
    *,
    input_row: BrowserExtractionInput,
) -> ExtractionResult:
    if not isinstance(rows, list):
        raise ValueError("live comment extraction must return a list")
    comments: list[CommentEvidence] = []
    warnings: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError("live comment extraction rows must be objects")
        comment = CommentEvidence(
            query_id=input_row.query_id,
            source_id=input_row.source_id,
            source_kind=input_row.source_kind,
            source_url=input_row.source_url or input_row.post_url,
            search_query=input_row.search_query,
            post_url=input_row.post_url,
            post_author_name="",
            post_text="",
            comment_id=_string_value(row.get("comment_id")),
            comment_url="",
            commenter_name=_clean_text(_string_value(row.get("commenter_name"))),
            commenter_profile_url=_canonical_linkedin_url(
                _string_value(row.get("commenter_profile_url"))
            ),
            commenter_headline=_clean_text(_string_value(row.get("commenter_headline"))),
            commenter_company="",
            relationship="",
            comment_text=_clean_text(_string_value(row.get("comment_text"))),
            commented_at=_clean_text(_string_value(row.get("commented_at"))),
        )
        comment_warnings = _live_comment_warnings(comment)
        warnings.extend(f"comment_{index}:{warning}" for warning in comment_warnings)
        comments.append(replace(comment, warnings=comment_warnings))
    if not comments:
        warnings.append("no_live_linkedin_comment_nodes_found")
    return ExtractionResult(comments=tuple(comments), warnings=tuple(warnings))


async def _expand_visible_comment_controls(page: Page, limits: BrowserSafetyLimits) -> None:
    deadline = asyncio.get_running_loop().time() + limits.max_runtime_seconds
    for _ in range(limits.max_scrolls):
        if asyncio.get_running_loop().time() >= deadline:
            break
        await _click_controls(page, MORE_COMMENTS_PATTERN, limits.max_comment_control_clicks)
        await _click_controls(page, MORE_REPLIES_PATTERN, limits.max_reply_control_clicks)
        await _scroll_page_down(page, 1800)
        await page.wait_for_timeout(limits.settle_ms)


async def _scroll_page_down(page: Page, pixels: int) -> None:
    await page.evaluate("(pixels) => window.scrollBy(0, pixels)", pixels)


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


async def _reusable_page(context: Any) -> Page:
    for page in context.pages:
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed) and is_closed():
            continue
        return cast(Page, page)
    return cast(Page, await context.new_page())


async def _close_context_handle(handle: Any) -> None:
    await close_browser_context_handle(handle)


def _comment_extraction_cdp_url(cdp_url: str | None) -> str:
    if cdp_url is None:
        return ""
    return cdp_url.strip()


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _canonical_linkedin_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/"):
        href = "https://www.linkedin.com" + href
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.endswith("linkedin.com"):
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _live_comment_warnings(comment: CommentEvidence) -> tuple[str, ...]:
    warnings: list[str] = []
    if not comment.comment_id:
        warnings.append("comment_id_missing")
    if not comment.commenter_profile_url:
        warnings.append("commenter_profile_url_missing")
    if not comment.commenter_name:
        warnings.append("commenter_name_missing")
    if not comment.comment_text:
        warnings.append("comment_text_missing_explicit_selector")
    return tuple(warnings)


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


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
