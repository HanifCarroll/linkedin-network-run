"""Small deterministic helpers shared by the recruiter/agency port."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha1
from urllib.parse import urlparse, urlunparse


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def optional_clean(value: object) -> str | None:
    cleaned = clean_text(value)
    return cleaned or None


def first_name(name: str) -> str:
    parts = clean_text(name).split()
    return parts[0] if parts else "there"


def contains_any(value: str, *needles: str) -> bool:
    haystack = value.lower()
    return any(needle.lower() in haystack for needle in needles)


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def truncate_evidence(value: str, limit: int = 700) -> str:
    cleaned = clean_text(value)
    return cleaned[:limit] if len(cleaned) > limit else cleaned


def fnv32a(value: str) -> int:
    hash_value = 0x811C9DC5
    for byte in value.encode("utf-8"):
        hash_value ^= byte
        hash_value = (hash_value * 0x01000193) & 0xFFFFFFFF
    return hash_value


def short_sha_id(prefix: str, key: str, length: int = 12) -> str:
    return f"{prefix}_{sha1(key.encode('utf-8')).hexdigest()[:length]}"


def normalize_linkedin_url(value: str) -> str:
    trimmed = clean_text(value)
    parsed = urlparse(trimmed)
    if parsed.scheme and parsed.netloc:
        path = parsed.path
        if parsed.netloc.lower() in {"www.linkedin.com", "linkedin.com"}:
            if path.startswith("/sales/lead/"):
                profile_id = path.removeprefix("/sales/lead/").split(",", maxsplit=1)[0]
                if profile_id:
                    path = f"/sales/lead/{profile_id}"
        return urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/"), "", "", ""))
    head = trimmed.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    return head.rstrip("/")


def normalized_linkedin_account_url(value: object) -> str | None:
    raw = clean_text(value)
    if not raw:
        return None
    if raw.startswith("/"):
        raw = "https://www.linkedin.com" + raw
    return normalize_linkedin_url(raw)


def normalized_public_profile_url(value: object) -> str | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    if host != "linkedin.com" or not parsed.path.startswith("/in/"):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    slug = parts[1]
    return urlunparse((parsed.scheme, parsed.netloc, f"/in/{slug}/", "", "", ""))


def domain_from_website(value: object) -> str | None:
    raw = clean_text(value)
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    domain = host.lower().removeprefix("www.")
    if not domain or "linkedin.com" in domain:
        return None
    return domain


def normalize_email(value: object) -> str | None:
    cleaned = clean_text(value).lower()
    if not cleaned or "@" not in cleaned:
        return None
    return cleaned


def is_generic_inbox(email: str) -> bool:
    local = email.split("@", maxsplit=1)[0].lower()
    return local in {
        "info",
        "hello",
        "contact",
        "team",
        "support",
        "sales",
        "admin",
        "careers",
        "jobs",
    }


def sales_profile_urn_to_lead_url(urn: str) -> str | None:
    cleaned = clean_text(urn)
    start = cleaned.find("(")
    if start < 0 or not cleaned.endswith(")"):
        return None
    tuple_value = cleaned[start + 1 : -1]
    parts = [part.strip() for part in tuple_value.split(",")]
    if len(parts) != 3 or not all(parts):
        return None
    return f"https://www.linkedin.com/sales/lead/{parts[0]},{parts[1]},{parts[2]}"


def stable_lead_id(
    source: str, name: str, profile_url: str | None, sales_profile_urn: str | None
) -> str:
    key = f"{source.lower()}|{name.lower()}"
    if profile_url:
        key = normalize_linkedin_url(profile_url)
    elif sales_profile_urn:
        key = clean_text(sales_profile_urn)
    return f"lead_{fnv32a(key):08x}"


def stable_agency_account_id(
    source: str,
    name: str,
    account_url: str | None,
    domain: str | None,
) -> str:
    key = f"{source.lower()}|{name.lower()}"
    if account_url:
        key = normalize_linkedin_url(account_url)
    elif domain:
        key = domain.lower()
    return f"acct_{fnv32a(key):08x}"


def stable_agency_contact_candidate_id(key: str) -> str:
    return short_sha_id("agc", key)
