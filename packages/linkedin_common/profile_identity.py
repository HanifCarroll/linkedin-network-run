"""Shared LinkedIn profile identity helpers for cross-workflow suppression."""

from __future__ import annotations

from .urls import (
    URLCanonicalizationError,
    canonicalize_sales_profile_url,
    sales_profile_urn_to_lead_url,
)


def linkedin_profile_identity_keys(
    profile_url: str | None = None,
    sales_profile_urn: str | None = None,
) -> set[str]:
    keys: set[str] = set()
    canonical_url = canonical_linkedin_profile_identity(profile_url, sales_profile_urn)
    if canonical_url:
        keys.add(canonical_url)
    return keys


def canonical_linkedin_profile_identity(
    profile_url: str | None = None,
    sales_profile_urn: str | None = None,
) -> str | None:
    for value in _candidate_identity_values(profile_url, sales_profile_urn):
        try:
            return canonicalize_sales_profile_url(value)
        except URLCanonicalizationError:
            continue
    return None


def _candidate_identity_values(
    profile_url: str | None,
    sales_profile_urn: str | None,
) -> list[str]:
    values: list[str] = []
    if profile_url and profile_url.strip():
        values.append(profile_url.strip())
    if sales_profile_urn and sales_profile_urn.strip():
        try:
            values.append(sales_profile_urn_to_lead_url(sales_profile_urn))
        except URLCanonicalizationError:
            pass
    return values
