"""LinkedIn URL canonicalization helpers."""

from __future__ import annotations

import re
from urllib.parse import SplitResult, quote, unquote, urlsplit, urlunsplit


class URLCanonicalizationError(ValueError):
    """Raised when a URL cannot be canonicalized by an explicit LinkedIn rule."""


_LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}
_ACTIVITY_URN_RE = re.compile(r"^urn:li:activity:(?P<activity_id>\d+)$")
_POST_ACTIVITY_SEGMENT_RE = re.compile(r"(?:^|_)activity-(?P<activity_id>\d+)(?:-|$)")
_SALES_PROFILE_URN_PREFIX = "urn:li:fs_salesProfile:"


def _coerce_url(value: str) -> str:
    text = value.strip()
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("linkedin.com/") or text.startswith("www.linkedin.com/"):
        return f"https://{text}"
    if text.startswith("/"):
        return f"https://www.linkedin.com{text}"
    return text


def _parse_linkedin_url(value: str) -> SplitResult:
    text = _coerce_url(value)
    if not text:
        raise URLCanonicalizationError("LinkedIn URL is empty")
    parsed = urlsplit(text)
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.scheme not in {"http", "https"} or hostname not in _LINKEDIN_HOSTS:
        raise URLCanonicalizationError(f"not a supported LinkedIn URL: {value!r}")
    return parsed


def _canonical_url(path: str) -> str:
    return urlunsplit(("https", "www.linkedin.com", path.rstrip("/"), "", ""))


def canonicalize_linkedin_profile_url(value: str) -> str:
    """Canonicalize a public LinkedIn ``/in/<slug>`` profile URL."""

    parsed = _parse_linkedin_url(value)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if len(segments) < 2 or segments[0] != "in":
        raise URLCanonicalizationError(f"not a public LinkedIn profile URL: {value!r}")
    slug = segments[1].strip()
    if not slug or "/" in slug:
        raise URLCanonicalizationError(f"invalid LinkedIn profile slug: {value!r}")
    tail = segments[2:]
    if tail and tail[0] != "recent-activity":
        raise URLCanonicalizationError(f"unsupported public profile URL path: {value!r}")
    return _canonical_url(f"/in/{quote(slug, safe='-_.~')}")


def canonicalize_linkedin_post_url(value: str) -> str:
    """Canonicalize supported LinkedIn post URLs to a feed activity URL."""

    parsed = _parse_linkedin_url(value)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if len(segments) >= 3 and segments[0] == "feed" and segments[1] == "update":
        match = _ACTIVITY_URN_RE.match(segments[2])
        if match is not None:
            return _canonical_url(f"/feed/update/urn:li:activity:{match.group('activity_id')}")
    if segments and segments[0] == "posts":
        for segment in segments[1:]:
            match = _POST_ACTIVITY_SEGMENT_RE.search(segment)
            if match is not None:
                return _canonical_url(f"/feed/update/urn:li:activity:{match.group('activity_id')}")
    raise URLCanonicalizationError(f"not a supported LinkedIn post URL: {value!r}")


def canonicalize_sales_nav_lead_url(value: str) -> str:
    """Canonicalize a Sales Navigator lead URL to its stable profile id URL."""

    parsed = _parse_linkedin_url(value)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if len(segments) != 3 or segments[0] != "sales" or segments[1] != "lead":
        raise URLCanonicalizationError(f"not a Sales Navigator lead URL: {value!r}")
    lead_tuple = segments[2].strip()
    profile_id = lead_tuple.split(",", 1)[0].strip()
    if not profile_id:
        raise URLCanonicalizationError(f"Sales Navigator lead profile id is empty: {value!r}")
    return _canonical_url(f"/sales/lead/{quote(profile_id, safe='-_.~')}")


def canonicalize_sales_profile_url(value: str) -> str:
    """Canonicalize a public profile URL or Sales Navigator lead URL."""

    parsed = _parse_linkedin_url(value)
    if parsed.path.startswith("/sales/lead/"):
        return canonicalize_sales_nav_lead_url(value)
    if parsed.path.startswith("/in/"):
        return canonicalize_linkedin_profile_url(value)
    raise URLCanonicalizationError(f"not a supported LinkedIn profile URL: {value!r}")


def sales_profile_urn_to_lead_url(value: str) -> str:
    """Convert a captured Sales Navigator profile URN into a navigable lead URL."""

    text = value.strip()
    if not text.startswith(_SALES_PROFILE_URN_PREFIX):
        raise URLCanonicalizationError(f"not a Sales Navigator profile URN: {value!r}")
    payload = text[len(_SALES_PROFILE_URN_PREFIX) :]
    if not payload.startswith("(") or not payload.endswith(")"):
        raise URLCanonicalizationError(f"malformed Sales Navigator profile URN: {value!r}")
    parts = [part.strip() for part in payload[1:-1].split(",")]
    if len(parts) != 3 or any(part == "" for part in parts):
        raise URLCanonicalizationError(f"malformed Sales Navigator profile URN tuple: {value!r}")
    lead_path = ",".join(quote(part, safe="-_.~") for part in parts)
    return _canonical_url(f"/sales/lead/{lead_path}")
