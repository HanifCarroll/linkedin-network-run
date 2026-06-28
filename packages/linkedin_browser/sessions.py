"""Browser session and page reuse primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class PageProtocol(Protocol):
    @property
    def url(self) -> str: ...

    async def bring_to_front(self) -> None: ...

    async def close(self) -> None: ...


class BrowserContextProtocol(Protocol):
    @property
    def pages(self) -> Sequence[PageProtocol]: ...

    async def new_page(self) -> PageProtocol: ...


@dataclass(frozen=True)
class PageReusePolicy:
    preferred_url_fragments: tuple[str, ...] = (
        "linkedin.com/sales/search/people",
        "linkedin.com/sales/lead/",
        "linkedin.com/mynetwork/invitation-manager/sent",
        "linkedin.com",
    )
    keep_pages: int = 1
    foreground: bool = False


@dataclass
class BrowserSession:
    context: BrowserContextProtocol
    policy: PageReusePolicy = PageReusePolicy()

    async def page(
        self,
        *,
        preferred_url_fragments: Sequence[str] | None = None,
        close_surplus: bool = False,
    ) -> PageProtocol:
        fragments = tuple(preferred_url_fragments or self.policy.preferred_url_fragments)
        selected = choose_reusable_page(self.context.pages, fragments)
        if selected is None:
            selected = await self.context.new_page()
        if self.policy.foreground:
            await selected.bring_to_front()
        if close_surplus:
            await self.close_surplus_pages(selected)
        return selected

    async def close_surplus_pages(self, selected: PageProtocol) -> int:
        closed = 0
        candidates = [page for page in self.context.pages if page is not selected]
        for page in candidates[self.policy.keep_pages - 1 :]:
            await page.close()
            closed += 1
        return closed


def choose_reusable_page(
    pages: Sequence[PageProtocol],
    preferred_url_fragments: Sequence[str],
) -> PageProtocol | None:
    for fragment in preferred_url_fragments:
        for page in pages:
            if fragment in page.url:
                return page
    return pages[0] if pages else None
