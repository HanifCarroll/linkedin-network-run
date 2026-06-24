"""Sales Navigator URL and profile URN helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SALES_LEAD_URL_RE = re.compile(r"/sales/lead/([^,/?#]+)")
_SALES_PROFILE_URN_RE = re.compile(r"\(([^,()]+),([^,()]+),([^,()]+)\)")


@dataclass(frozen=True)
class SalesProfile:
    profile_id: str
    auth_type: str
    auth_token: str

    def lead_url(self) -> str:
        return (
            "https://www.linkedin.com/sales/lead/"
            f"{self.profile_id},{self.auth_type},{self.auth_token}"
        )


def sales_profile_id_from_url(profile_url: str | None) -> str | None:
    if profile_url is None:
        return None
    match = _SALES_LEAD_URL_RE.search(profile_url)
    return match.group(1) if match else None


def sales_profile_urn_to_lead_url(urn: str | None) -> str | None:
    profile = parse_sales_profile_urn(urn)
    return profile.lead_url() if profile is not None else None


def parse_sales_profile_urn(urn: str | None) -> SalesProfile | None:
    if urn is None:
        return None
    match = _SALES_PROFILE_URN_RE.search(urn)
    if not match:
        return None
    return SalesProfile(
        profile_id=match.group(1),
        auth_type=match.group(2),
        auth_token=match.group(3),
    )
