"""Browser-backed LinkedIn search result post capture."""

from __future__ import annotations

import asyncio
import csv
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from apps.opportunity_intel.company_pages import canonicalize_linkedin_post_url
from apps.opportunity_intel.post_discovery import PostCandidate, prioritize_posts
from apps.opportunity_intel.post_prefilter import POST_QUEUE_COLUMNS, read_post_queue
from packages.linkedin_browser.config import ChromeProfileConfig, chrome_profile_from_env
from packages.linkedin_browser.playwright import (
    close_browser_context_handle,
    open_linkedin_browser_context,
)
from packages.linkedin_common.progress import ProgressReporter

POST_MENU_PATTERN = re.compile(r"open control menu for post by", re.IGNORECASE)
COPY_LINK_PATTERN = re.compile(r"^Copy link to post$", re.IGNORECASE)
COPY_CAPTURE_INSTALL_SCRIPT = """
() => {
  const clipboard = navigator.clipboard;
  if (!clipboard || typeof clipboard.writeText !== "function") {
    throw new Error("navigator.clipboard.writeText is unavailable");
  }
  const state = {
    writes: [],
    originalWriteText: clipboard.writeText.bind(clipboard),
  };
  const captureWriteText = async (text) => {
    state.writes.push(String(text));
    return undefined;
  };
  Object.defineProperty(clipboard, "writeText", {
    configurable: true,
    writable: true,
    value: captureWriteText,
  });
  window.__linkedinPostCopyCapture = state;
}
"""
COPY_CAPTURE_READ_SCRIPT = """
() => {
  const state = window.__linkedinPostCopyCapture;
  if (!state || !Array.isArray(state.writes) || state.writes.length === 0) {
    return "";
  }
  return state.writes.at(-1) || "";
}
"""
COPY_CAPTURE_RESTORE_SCRIPT = """
() => {
  const state = window.__linkedinPostCopyCapture;
  if (state && state.originalWriteText && navigator.clipboard) {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      writable: true,
      value: state.originalWriteText,
    });
  }
  delete window.__linkedinPostCopyCapture;
}
"""
COPY_CAPTURE_TIMEOUT_MS = 1_000


class PostCopyCaptureError(RuntimeError):
    """LinkedIn's copy action did not publish a post URL through the page API."""


@dataclass(frozen=True)
class SearchCaptureLimits:
    max_results_per_search: int = 50
    max_scrolls: int = 20
    scroll_pixels: int = 1800
    navigation_timeout_ms: int = 30_000
    action_timeout_ms: int = 5_000
    settle_ms: int = 1_000


@dataclass(frozen=True)
class SearchCaptureInput:
    candidate: PostCandidate
    capture_url: str


@dataclass(frozen=True)
class SearchCapturePlan:
    known_posts: tuple[PostCandidate, ...]
    search_inputs: tuple[SearchCaptureInput, ...]
    skipped_rows: tuple[PostCandidate, ...]


@dataclass(frozen=True)
class SearchCaptureResult:
    processed_searches: int
    known_posts: int
    captured_posts: int
    duplicate_posts: int
    failed_searches: int
    output_path: Path
    metrics_path: Path
    checkpoint_path: Path

    def to_json_object(self) -> dict[str, object]:
        payload = asdict(self)
        payload["output_path"] = str(self.output_path)
        payload["metrics_path"] = str(self.metrics_path)
        payload["checkpoint_path"] = str(self.checkpoint_path)
        return payload


def plan_search_capture(candidates: Sequence[PostCandidate]) -> SearchCapturePlan:
    known_posts: list[PostCandidate] = []
    search_inputs: list[SearchCaptureInput] = []
    skipped_rows: list[PostCandidate] = []
    seen_capture_urls: set[str] = set()
    for candidate in candidates:
        if candidate.post_url:
            known_posts.append(candidate)
            continue
        capture_url = search_capture_url(candidate)
        if capture_url:
            if capture_url in seen_capture_urls:
                skipped_rows.append(candidate)
                continue
            seen_capture_urls.add(capture_url)
            search_inputs.append(SearchCaptureInput(candidate=candidate, capture_url=capture_url))
        else:
            skipped_rows.append(candidate)
    return SearchCapturePlan(
        known_posts=tuple(known_posts),
        search_inputs=tuple(search_inputs),
        skipped_rows=tuple(skipped_rows),
    )


def search_capture_url(candidate: PostCandidate) -> str:
    if "linkedin.com/search/results" in candidate.source_url:
        return candidate.source_url
    if candidate.search_query:
        return "https://www.linkedin.com/search/results/content/?keywords=" + quote_plus(
            candidate.search_query
        )
    return ""


def capture_search_posts_from_queue(
    *,
    post_queue_path: Path,
    output_path: Path,
    metrics_path: Path,
    checkpoint_path: Path,
    limits: SearchCaptureLimits,
    cdp_url: str | None = None,
    config: ChromeProfileConfig | None = None,
    progress: ProgressReporter | None = None,
) -> SearchCaptureResult:
    return asyncio.run(
        capture_search_posts_from_queue_async(
            post_queue_path=post_queue_path,
            output_path=output_path,
            metrics_path=metrics_path,
            checkpoint_path=checkpoint_path,
            limits=limits,
            cdp_url=cdp_url,
            config=config,
            progress=progress,
        )
    )


async def capture_search_posts_from_queue_async(
    *,
    post_queue_path: Path,
    output_path: Path,
    metrics_path: Path,
    checkpoint_path: Path,
    limits: SearchCaptureLimits,
    cdp_url: str | None = None,
    config: ChromeProfileConfig | None = None,
    progress: ProgressReporter | None = None,
) -> SearchCaptureResult:
    candidates = read_post_queue(post_queue_path)
    plan = plan_search_capture(candidates)
    selected = config or chrome_profile_from_env()
    reporter = progress or ProgressReporter(enabled=False)
    seen_post_urls: set[str] = set()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    _initialize_post_queue_output(output_path)
    _initialize_jsonl(metrics_path)
    known_posts_written = _write_known_posts(
        output_path=output_path,
        metrics_path=metrics_path,
        known_posts=plan.known_posts,
        seen_post_urls=seen_post_urls,
    )
    _append_jsonl(
        metrics_path,
        {
            "event": "capture_start",
            "post_queue_path": str(post_queue_path),
            "known_posts": len(plan.known_posts),
            "search_inputs": len(plan.search_inputs),
            "skipped_rows": len(plan.skipped_rows),
            "limits": asdict(limits),
        },
    )
    reporter.emit(
        "search_capture_start",
        searches=len(plan.search_inputs),
        known_posts=len(plan.known_posts),
        output=output_path,
    )

    processed_searches = 0
    captured_posts = 0
    duplicate_posts = 0
    failed_searches = 0
    handle: Any | None = None
    try:
        async with async_playwright() as playwright:
            handle = await open_linkedin_browser_context(
                playwright,
                selected,
                cdp_url=cdp_url,
            )
            page = await _reusable_page(handle.context)
            page.set_default_timeout(limits.action_timeout_ms)
            for search_index, search_input in enumerate(plan.search_inputs, start=1):
                processed_searches += 1
                before_count = len(seen_post_urls)
                reporter.emit(
                    "search_start",
                    index=search_index,
                    total=len(plan.search_inputs),
                    source_id=search_input.candidate.source_id,
                    query_id=search_input.candidate.query_id,
                    query=search_input.candidate.search_query,
                )
                try:
                    single_result = await _capture_single_search(
                        page=page,
                        search_input=search_input,
                        output_path=output_path,
                        metrics_path=metrics_path,
                        seen_post_urls=seen_post_urls,
                        limits=limits,
                        reporter=reporter,
                    )
                except Exception as exc:
                    failed_searches += 1
                    _append_jsonl(
                        metrics_path,
                        {
                            "event": "search_failed",
                            "source_id": search_input.candidate.source_id,
                            "query_id": search_input.candidate.query_id,
                            "search_query": search_input.candidate.search_query,
                            "capture_url": search_input.capture_url,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                    reporter.emit(
                        "search_failed",
                        index=search_index,
                        total=len(plan.search_inputs),
                        source_id=search_input.candidate.source_id,
                        error=type(exc).__name__,
                    )
                else:
                    captured_posts += single_result["captured"]
                    duplicate_posts += single_result["duplicates"]
                    reporter.emit(
                        "search_done",
                        index=search_index,
                        total=len(plan.search_inputs),
                        source_id=search_input.candidate.source_id,
                        new_posts=len(seen_post_urls) - before_count,
                    )
                _write_checkpoint(
                    checkpoint_path,
                    {
                        "processed_searches": processed_searches,
                        "total_searches": len(plan.search_inputs),
                        "known_posts": known_posts_written,
                        "captured_posts": captured_posts,
                        "duplicate_posts": duplicate_posts,
                        "failed_searches": failed_searches,
                        "output_path": str(output_path),
                        "metrics_path": str(metrics_path),
                    },
                )
            await close_browser_context_handle(handle)
            handle = None
    finally:
        if handle is not None:
            await close_browser_context_handle(handle)

    result = SearchCaptureResult(
        processed_searches=processed_searches,
        known_posts=known_posts_written,
        captured_posts=captured_posts,
        duplicate_posts=duplicate_posts,
        failed_searches=failed_searches,
        output_path=output_path,
        metrics_path=metrics_path,
        checkpoint_path=checkpoint_path,
    )
    _append_jsonl(metrics_path, {"event": "capture_done", **result.to_json_object()})
    reporter.emit(
        "search_capture_done",
        processed_searches=processed_searches,
        captured_posts=captured_posts,
        failed_searches=failed_searches,
    )
    return result


async def _capture_single_search(
    *,
    page: Page,
    search_input: SearchCaptureInput,
    output_path: Path,
    metrics_path: Path,
    seen_post_urls: set[str],
    limits: SearchCaptureLimits,
    reporter: ProgressReporter,
) -> dict[str, int]:
    await page.goto(
        search_input.capture_url,
        wait_until="domcontentloaded",
        timeout=limits.navigation_timeout_ms,
    )
    await page.wait_for_timeout(limits.settle_ms)
    captured = 0
    duplicates = 0
    stale_scrolls = 0
    processed_menu_buttons = 0
    for scroll_index in range(1, limits.max_scrolls + 1):
        if captured >= limits.max_results_per_search:
            break
        buttons = page.get_by_role("button", name=POST_MENU_PATTERN)
        try:
            button_count = await buttons.count()
        except PlaywrightTimeoutError:
            button_count = 0
        reporter.emit(
            "search_scroll",
            source_id=search_input.candidate.source_id,
            scroll=scroll_index,
            menu_buttons=button_count,
            new_menu_buttons=max(0, button_count - processed_menu_buttons),
            captured=captured,
        )
        captured_before_scroll = captured
        for index in range(processed_menu_buttons, button_count):
            if captured >= limits.max_results_per_search:
                break
            try:
                copied_url = await _copy_post_url_from_menu(
                    page=page,
                    menu_button=buttons.nth(index),
                )
            except (PlaywrightError, PostCopyCaptureError) as exc:
                _append_capture_metric(
                    metrics_path=metrics_path,
                    event="copy_failed",
                    search_input=search_input,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                continue
            post_url = canonicalize_linkedin_post_url(copied_url)
            if not post_url:
                _append_capture_metric(
                    metrics_path=metrics_path,
                    event="copied_url_rejected",
                    search_input=search_input,
                    copied_url=copied_url,
                    reason="not_linkedin_post_url",
                )
                continue
            if post_url in seen_post_urls:
                duplicates += 1
                _append_capture_metric(
                    metrics_path=metrics_path,
                    event="duplicate_post",
                    search_input=search_input,
                    post_url=post_url,
                )
                continue
            seen_post_urls.add(post_url)
            captured += 1
            candidate = PostCandidate(
                source_id=search_input.candidate.source_id,
                source_kind=search_input.candidate.source_kind,
                query_id=search_input.candidate.query_id,
                post_url=post_url,
                source_url=search_input.candidate.source_url,
                search_query=search_input.candidate.search_query,
                priority=search_input.candidate.priority,
                reason="linkedin_search_copy_link",
            )
            _append_post_candidate(output_path, candidate)
            _append_capture_metric(
                metrics_path=metrics_path,
                event="post_captured",
                search_input=search_input,
                post_url=post_url,
            )
        processed_menu_buttons = max(processed_menu_buttons, button_count)
        if captured == captured_before_scroll:
            stale_scrolls += 1
        else:
            stale_scrolls = 0
        if stale_scrolls >= 3:
            break
        await page.evaluate("(pixels) => window.scrollBy(0, pixels)", limits.scroll_pixels)
        await page.wait_for_timeout(limits.settle_ms)
    return {"captured": captured, "duplicates": duplicates}


async def _copy_post_url_from_menu(
    *,
    page: Page,
    menu_button: Any,
) -> str:
    await page.evaluate(COPY_CAPTURE_INSTALL_SCRIPT)
    try:
        try:
            await menu_button.click(timeout=2_000)
        except PlaywrightTimeoutError:
            await menu_button.dispatch_event("click")
        menu_item = page.get_by_role("menuitem", name=COPY_LINK_PATTERN)
        await menu_item.click()
        try:
            await page.wait_for_function(
                "() => window.__linkedinPostCopyCapture?.writes?.length > 0",
                timeout=COPY_CAPTURE_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError as exc:
            raise PostCopyCaptureError(
                "LinkedIn copy action did not call navigator.clipboard.writeText"
            ) from exc
        copied_value = await page.evaluate(COPY_CAPTURE_READ_SCRIPT)
        if not isinstance(copied_value, str):
            raise PostCopyCaptureError("LinkedIn copy action produced a non-text post URL")
        copied_url = copied_value
        if not copied_url:
            raise PostCopyCaptureError("LinkedIn copy action produced an empty post URL")
        return copied_url
    finally:
        await page.evaluate(COPY_CAPTURE_RESTORE_SCRIPT)


async def _reusable_page(context: Any) -> Page:
    for page in context.pages:
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed) and is_closed():
            continue
        if "linkedin.com" in getattr(page, "url", ""):
            return cast(Page, page)
    for page in context.pages:
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed) and is_closed():
            continue
        return cast(Page, page)
    return cast(Page, await context.new_page())


def _write_known_posts(
    *,
    output_path: Path,
    metrics_path: Path,
    known_posts: Sequence[PostCandidate],
    seen_post_urls: set[str],
) -> int:
    written = 0
    for candidate in prioritize_posts(tuple(known_posts)):
        post_url = canonicalize_linkedin_post_url(candidate.post_url)
        if not post_url or post_url in seen_post_urls:
            continue
        seen_post_urls.add(post_url)
        _append_post_candidate(output_path, candidate)
        _append_jsonl(
            metrics_path,
            {
                "event": "known_post_copied",
                "source_id": candidate.source_id,
                "query_id": candidate.query_id,
                "post_url": post_url,
            },
        )
        written += 1
    return written


def _append_capture_metric(
    *,
    metrics_path: Path,
    event: str,
    search_input: SearchCaptureInput,
    post_url: str = "",
    copied_url: str = "",
    reason: str = "",
    error_type: str = "",
    message: str = "",
) -> None:
    _append_jsonl(
        metrics_path,
        {
            "event": event,
            "source_id": search_input.candidate.source_id,
            "source_kind": search_input.candidate.source_kind,
            "query_id": search_input.candidate.query_id,
            "source_url": search_input.candidate.source_url,
            "search_query": search_input.candidate.search_query,
            "capture_url": search_input.capture_url,
            "post_url": post_url,
            "copied_url": copied_url,
            "reason": reason,
            "error_type": error_type,
            "message": message,
        },
    )


def _initialize_post_queue_output(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=POST_QUEUE_COLUMNS)
        writer.writeheader()


def _append_post_candidate(path: Path, candidate: PostCandidate) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=POST_QUEUE_COLUMNS)
        writer.writerow(candidate.__dict__)


def _initialize_jsonl(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)
