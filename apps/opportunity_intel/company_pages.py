"""Company page post-link extraction."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

from apps.opportunity_intel.contracts import OpportunityContractError, SourceDefinition, SourceKind
from apps.opportunity_intel.post_discovery import (
    PostCandidate,
    linkedin_company_posts_url,
    prioritize_posts,
)


class CompanyPagePostHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.post_urls: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = _href_from_attrs(attrs)
        post_url = canonicalize_linkedin_post_url(href)
        if not post_url or post_url in self._seen:
            return
        self._seen.add(post_url)
        self.post_urls.append(post_url)


def extract_post_urls_from_company_page_html(html: str) -> tuple[str, ...]:
    parser = CompanyPagePostHTMLParser()
    parser.feed(html)
    return tuple(parser.post_urls)


def extract_company_page_post_candidates_from_html_file(
    *,
    source: SourceDefinition,
    html_path: Path,
) -> tuple[PostCandidate, ...]:
    if source.source_kind is not SourceKind.COMPANY_PAGE:
        raise OpportunityContractError(f"{source.source_id} is not a company_page source")
    post_urls = extract_post_urls_from_company_page_html(html_path.read_text(encoding="utf-8"))
    source_url = linkedin_company_posts_url(source.urls[0]) if source.urls else ""
    candidates = [
        PostCandidate(
            source_id=source.source_id,
            source_kind=source.source_kind.value,
            query_id=query_id,
            post_url=post_url,
            source_url=source_url,
            search_query="",
            priority=source.priority,
            reason="company_page_post_url",
        )
        for post_url in post_urls
        for query_id in source.query_ids
    ]
    return prioritize_posts(tuple(candidates))


def canonicalize_linkedin_post_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/"):
        href = "https://www.linkedin.com" + href
    parsed = urlparse(href)
    if not parsed.netloc.endswith("linkedin.com"):
        return ""
    path = unquote(parsed.path).rstrip("/")
    if path.startswith("/posts/") or path.startswith("/feed/update/urn:li:activity:"):
        return urlunparse(("https", "www.linkedin.com", path, "", "", ""))
    return ""


def _href_from_attrs(attrs: list[tuple[str, str | None]]) -> str:
    for name, value in attrs:
        if name.casefold() == "href":
            return value or ""
    return ""
