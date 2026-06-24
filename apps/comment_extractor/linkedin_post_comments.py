"""Explicit-selector LinkedIn post comment extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from apps.comment_extractor.contracts import RAW_COMMENTS_FILENAME, ExtractionResult, PostHTMLInput
from apps.opportunity_intel.contracts import CommentEvidence

EXPLICIT_COMMENT_SELECTORS: tuple[str, ...] = (
    '[componentkey^="replaceableComment_urn:li:comment:"]',
    '[data-id^="urn:li:comment:"]',
)

COMMENT_TEXT_MARKERS: tuple[str, ...] = (
    "data-comment-text",
    "data-test-comment-text",
    "data-test-comment-content",
)


@dataclass
class _ElementState:
    comment_root: bool = False
    comment_text: bool = False
    profile_link: bool = False


@dataclass
class _CommentBuffer:
    root_attrs: dict[str, str]
    text_parts: list[str] = field(default_factory=list)
    profile_href: str = ""
    name_parts: list[str] = field(default_factory=list)
    comment_url: str = ""


class LinkedInPostCommentHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.comments: list[_CommentBuffer] = []
        self.warnings: list[str] = []
        self._current: _CommentBuffer | None = None
        self._stack: list[_ElementState] = []
        self._comment_text_depth = 0
        self._profile_link_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = _attrs_to_dict(attrs)
        if self._current is None and _is_comment_root(attr_map):
            self._current = _CommentBuffer(root_attrs=attr_map)
            self._stack.append(_ElementState(comment_root=True))
            return
        if self._current is None:
            return

        is_comment_text = _is_comment_text_node(attr_map)
        href = attr_map.get("href", "")
        is_profile_link = tag == "a" and _is_profile_href(href)
        if is_profile_link and not self._current.profile_href:
            self._current.profile_href = _canonicalize_linkedin_url(href)
        if tag == "a" and not self._current.comment_url:
            self._current.comment_url = _comment_permalink(href)
        if is_comment_text:
            self._comment_text_depth += 1
        if is_profile_link:
            self._profile_link_depth += 1
        self._stack.append(
            _ElementState(comment_text=is_comment_text, profile_link=is_profile_link)
        )

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._comment_text_depth > 0:
            self._current.text_parts.append(data)
        if self._profile_link_depth > 0:
            self._current.name_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        _ = tag
        if self._current is None or not self._stack:
            return
        state = self._stack.pop()
        if state.comment_text:
            self._comment_text_depth -= 1
        if state.profile_link:
            self._profile_link_depth -= 1
        if state.comment_root:
            self.comments.append(self._current)
            self._current = None


def extract_comments_from_html(
    *,
    html: str,
    post_url: str,
    source_id: str,
    query_id: str,
    source_kind: str = "known_post",
    source_url: str = "",
    search_query: str = "",
) -> ExtractionResult:
    parser = LinkedInPostCommentHTMLParser()
    parser.feed(html)
    comments: list[CommentEvidence] = []
    warnings = list(parser.warnings)
    for index, buffer in enumerate(parser.comments, start=1):
        comment_warnings = _comment_warnings(buffer)
        warnings.extend(f"comment_{index}:{warning}" for warning in comment_warnings)
        comments.append(
            CommentEvidence(
                query_id=query_id,
                source_id=source_id,
                source_kind=source_kind,
                source_url=source_url or post_url,
                search_query=search_query,
                post_url=post_url,
                post_author_name="",
                post_text="",
                comment_id=_comment_id(buffer.root_attrs),
                comment_url=buffer.comment_url,
                commenter_name=_clean_text("".join(buffer.name_parts)),
                commenter_profile_url=buffer.profile_href,
                commenter_headline="",
                commenter_company="",
                relationship="",
                comment_text=_clean_text("".join(buffer.text_parts)),
                commented_at="",
                warnings=comment_warnings,
            )
        )
    if not comments:
        warnings.append("no_explicit_linkedin_comment_nodes_found")
    return ExtractionResult(comments=tuple(comments), warnings=tuple(warnings))


def extract_comments_from_html_file(input_row: PostHTMLInput) -> ExtractionResult:
    html = input_row.html_path.read_text(encoding="utf-8")
    return extract_comments_from_html(
        html=html,
        post_url=input_row.post_url,
        source_id=input_row.source_id,
        query_id=input_row.query_id,
        source_kind=input_row.source_kind,
        source_url=input_row.source_url,
        search_query=input_row.search_query,
    )


def write_raw_comments_jsonl(
    comments: tuple[CommentEvidence, ...], output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / RAW_COMMENTS_FILENAME
    with output_path.open("w", encoding="utf-8") as handle:
        for comment in comments:
            handle.write(json.dumps(comment.to_json_object(), sort_keys=True) + "\n")
    return output_path


def _attrs_to_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.casefold(): value or "" for name, value in attrs}


def _is_comment_root(attrs: dict[str, str]) -> bool:
    component_key = attrs.get("componentkey", "")
    data_id = attrs.get("data-id", "")
    return component_key.startswith("replaceableComment_urn:li:comment:") or data_id.startswith(
        "urn:li:comment:"
    )


def _is_comment_text_node(attrs: dict[str, str]) -> bool:
    return any(marker in attrs for marker in COMMENT_TEXT_MARKERS)


def _is_profile_href(href: str) -> bool:
    if href.startswith("/in/"):
        return True
    parsed = urlparse(href)
    return parsed.scheme in {"http", "https"} and parsed.netloc.endswith(
        "linkedin.com"
    ) and parsed.path.startswith("/in/")


def _canonicalize_linkedin_url(href: str) -> str:
    if href.startswith("/"):
        href = "https://www.linkedin.com" + href
    parsed = urlparse(href)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _comment_permalink(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(href)
    if parsed.netloc.endswith("linkedin.com") and "comment" in parsed.path:
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
    return ""


def _comment_id(attrs: dict[str, str]) -> str:
    data_id = attrs.get("data-id", "")
    if data_id:
        return data_id
    return attrs.get("componentkey", "")


def _comment_warnings(buffer: _CommentBuffer) -> tuple[str, ...]:
    warnings: list[str] = []
    if not buffer.profile_href:
        warnings.append("commenter_profile_url_missing")
    if not _clean_text("".join(buffer.name_parts)):
        warnings.append("commenter_name_missing")
    if not _clean_text("".join(buffer.text_parts)):
        warnings.append("comment_text_missing_explicit_selector")
    return tuple(warnings)


def _clean_text(text: str) -> str:
    return text.replace("\u00a0", " ").strip()
