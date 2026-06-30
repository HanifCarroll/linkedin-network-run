from __future__ import annotations

import pytest

from packages.linkedin_common import (
    URLCanonicalizationError,
    canonical_linkedin_profile_identity,
    canonicalize_linkedin_post_url,
    canonicalize_linkedin_profile_url,
    canonicalize_sales_nav_lead_url,
    canonicalize_sales_profile_url,
    sales_profile_urn_to_lead_url,
)


def test_public_profile_url_strips_tracking_and_recent_activity_path() -> None:
    assert (
        canonicalize_linkedin_profile_url(
            "https://www.linkedin.com/in/jane-doe/recent-activity/comments/?trk=public_profile"
        )
        == "https://www.linkedin.com/in/jane-doe"
    )


def test_sales_nav_lead_url_uses_stable_profile_id() -> None:
    left = "https://www.linkedin.com/sales/lead/abc123,NAME_SEARCH,token-one?_ntb=session"
    right = "https://linkedin.com/sales/lead/abc123,SEARCH,token-two"

    assert canonicalize_sales_nav_lead_url(left) == canonicalize_sales_nav_lead_url(right)
    assert canonicalize_sales_nav_lead_url(left) == "https://www.linkedin.com/sales/lead/abc123"


def test_sales_profile_urn_to_lead_url_preserves_navigation_tuple() -> None:
    urn = "urn:li:fs_salesProfile:(ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt)"

    assert (
        sales_profile_urn_to_lead_url(urn)
        == "https://www.linkedin.com/sales/lead/ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt"
    )


def test_post_url_canonicalizes_feed_and_public_activity_forms() -> None:
    assert (
        canonicalize_linkedin_post_url(
            "https://www.linkedin.com/posts/hanif_activity-7340000000000000000-abcd/?trk=feed"
        )
        == "https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000"
    )
    assert (
        canonicalize_linkedin_post_url(
            "https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000/?utm_source=x"
        )
        == "https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000"
    )


def test_profile_url_dispatches_public_and_sales_nav_urls() -> None:
    assert (
        canonicalize_sales_profile_url("linkedin.com/in/jane-doe/?trk=comments")
        == "https://www.linkedin.com/in/jane-doe"
    )
    assert (
        canonicalize_sales_profile_url("https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token")
        == "https://www.linkedin.com/sales/lead/abc"
    )


def test_profile_identity_matches_sales_nav_url_and_urn() -> None:
    urn = "urn:li:fs_salesProfile:(abc123,NAME_SEARCH,token-two)"

    assert (
        canonical_linkedin_profile_identity(
            "https://www.linkedin.com/sales/lead/abc123,SEARCH,token-one",
            urn,
        )
        == "https://www.linkedin.com/sales/lead/abc123"
    )
    assert canonical_linkedin_profile_identity(None, urn) == (
        "https://www.linkedin.com/sales/lead/abc123"
    )


def test_url_helpers_reject_unsupported_shapes() -> None:
    with pytest.raises(URLCanonicalizationError):
        canonicalize_linkedin_profile_url("https://www.linkedin.com/company/example/")
    with pytest.raises(URLCanonicalizationError):
        canonicalize_linkedin_post_url("https://example.com/feed/update/urn:li:activity:1")
    with pytest.raises(URLCanonicalizationError):
        sales_profile_urn_to_lead_url("urn:li:member:abc")
