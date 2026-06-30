"""Live browser adapter for Sales Navigator account captures."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from apps.network_automation.browser import (
    _click_next_results_page,
    _ignore_errors,
    _safe_stem,
    _short_wait,
    _wait_for_load,
)
from apps.network_automation.store import write_json_atomic
from packages.linkedin_browser import (
    BrowserContextHandle,
    BrowserSession,
    ChromeProfileConfig,
    PageReusePolicy,
    close_browser_context_handle,
    open_linkedin_browser_context,
)

DEFAULT_ACCOUNT_CAPTURE_OUT_DIR = Path("/tmp/recruiter-agency-outreach-account-capture")
ResultT = TypeVar("ResultT")


class PlaywrightAccountCaptureClient:
    """Playwright-backed Sales Navigator company/account capture."""

    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_ACCOUNT_CAPTURE_OUT_DIR,
        context: Any | None = None,
        context_factory: Callable[[], Awaitable[Any]] | None = None,
        chrome_profile_config: ChromeProfileConfig | None = None,
    ) -> None:
        self.out_dir = out_dir
        self._context = context
        self._context_factory = context_factory
        self._chrome_profile_config = chrome_profile_config
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

    def capture_accounts(
        self,
        *,
        source: str,
        url: str | None = None,
        pages: int = 1,
        limit: int = 25,
    ) -> tuple[dict[str, Any], str]:
        return self._run(
            self._capture_accounts(source=source, url=url, pages=pages, limit=limit)
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
        )
        self._context = self._context_handle_ref.context
        return self._context

    async def _page(self) -> Any:
        session = BrowserSession(
            await self._context_handle(),
            PageReusePolicy(
                preferred_url_fragments=(
                    "linkedin.com/sales/search/company",
                    "linkedin.com/sales/company",
                ),
                foreground=False,
            ),
        )
        return await session.page(
            preferred_url_fragments=(
                "linkedin.com/sales/search/company",
                "linkedin.com/sales/company",
            )
        )

    async def _capture_accounts(
        self,
        *,
        source: str,
        url: str | None,
        pages: int,
        limit: int,
    ) -> tuple[dict[str, Any], str]:
        page = await self._page()
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await _wait_for_load(page)
        all_rows: list[dict[str, Any]] = []
        page_summaries: list[dict[str, Any]] = []
        for page_number in range(1, max(1, pages) + 1):
            await _short_wait(page)
            await _ignore_errors(
                page.wait_for_function(
                    "() => document.querySelectorAll(\"a[href*='/sales/company/']\").length > 0",
                    timeout=30000,
                )
            )
            page_summaries.append({"url": page.url, "pageLabel": None})
            rows = await _capture_account_rows(page, limit, page_number)
            for row in rows:
                row["globalIndex"] = len(all_rows)
                row["accountUrl"] = _absolute_linkedin_url(row.get("accountUrl"))
                row["accountId"] = row.get("accountId") or _sales_company_id(
                    row.get("accountUrl")
                )
                all_rows.append(row)
            if page_number < pages and not await _click_next_results_page(page):
                break
        payload = {
            "schemaVersion": 1,
            "capturedAt": _now_iso(),
            "url": page.url,
            "resumeUrl": page.url,
            "source": source,
            "page": page_summaries[-1] if page_summaries else None,
            "pages": page_summaries,
            "captureOptions": {"limit": limit, "pages": pages},
            "rawRowCount": len(all_rows),
            "outputRowCount": len(all_rows),
            "rows": all_rows,
        }
        return self._write_capture(source, payload)

    def _write_capture(
        self,
        source: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        self._counter += 1
        path = self.out_dir / f"{self._counter:03d}-{_safe_stem(source)}-accounts.json"
        write_json_atomic(path, payload)
        return payload, str(path)


async def _capture_account_rows(page: Any, limit: int, page_number: int) -> list[dict[str, Any]]:
    rows = await page.evaluate(
        """({ limit, pageNumber }) => {
          const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
          const rows = Array.from(document.querySelectorAll(
            "li.artdeco-list__item, div[role='listitem'], div[data-x--search-result]"
          ))
            .filter((row) => row.querySelector("a[href*='/sales/company/']"));
          const selected = [];
          for (const row of rows) {
            if (selected.length >= limit) break;
            selected.push(row);
          }
          return selected.map((row, index) => {
              const links = Array.from(row.querySelectorAll('a')).map((link, linkIndex) => ({
                index: linkIndex,
                text: clean(link.textContent || ''),
                aria: link.getAttribute('aria-label'),
                href: link.href || null,
                id: link.id || null,
              }));
              const accountLink = links.find((link) =>
                link.href && link.href.includes('/sales/company/')
              ) || null;
              const websiteLink = links.find((link) => {
                if (!link.href) return false;
                return /^https?:\\/\\//i.test(link.href) && !/linkedin\\.com/i.test(link.href);
              }) || null;
              const name =
                clean(row.querySelector("[data-anonymize='company-name']")?.textContent || '')
                || clean(accountLink?.text || '')
                || clean(accountLink?.aria || '').replace(/^View company\\s+/i, '')
                || null;
              return {
                index,
                pageNumber,
                name,
                text: row.textContent || '',
                accountUrl: accountLink?.href || null,
                accountId: accountLink?.href?.match(/\\/sales\\/company\\/([^/?#]+)/)?.[1] || null,
                website: websiteLink?.href || null,
                industry: null,
                headcount: null,
                location: null,
                links,
              };
            });
        }""",
        {"limit": limit, "pageNumber": page_number},
    )
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def _absolute_linkedin_url(url: object) -> str | None:
    value = str(url or "").strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return "https://www.linkedin.com" + (value if value.startswith("/") else "/" + value)


def _sales_company_id(url: object) -> str | None:
    value = str(url or "")
    marker = "/sales/company/"
    if marker not in value:
        return None
    return value.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0].split("/", 1)[0]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
