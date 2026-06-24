from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.linkedin_common import (
    AppNamespace,
    CommentRecord,
    RunManifest,
    RunStatus,
    SourceRecord,
)
from packages.linkedin_common.schemas import PostRecord


def test_comment_record_requires_proof_fields_and_canonicalizes_urls() -> None:
    record = CommentRecord(
        post_url="https://www.linkedin.com/posts/hanif_activity-7340000000000000000-abcd/?trk=feed",
        comment_text="Can anyone recommend a better dashboard workflow?",
        commenter_name="Jane Doe",
        commenter_profile_url="https://www.linkedin.com/in/jane-doe/recent-activity/comments/",
    )

    assert record.post_url == "https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000"
    assert record.commenter_profile_url == "https://www.linkedin.com/in/jane-doe"


def test_comment_record_rejects_missing_comment_text() -> None:
    with pytest.raises(ValidationError):
        CommentRecord(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000",
            comment_text="",
            commenter_name="Jane Doe",
            commenter_profile_url="https://www.linkedin.com/in/jane-doe/",
        )


def test_source_record_is_strict() -> None:
    with pytest.raises(ValidationError):
        SourceRecord.model_validate(
            {
                "source_id": "operators",
                "source_type": "known_post",
                "label": "Operators",
                "hypothesis": "Operators complain in comments.",
                "priority": 10,
                "unknown": "not part of the contract",
            }
        )


def test_post_record_canonicalizes_post_url() -> None:
    record = PostRecord(
        post_id="post-1",
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000/?trk=feed",
        source_id="source-1",
        source_type="known_post",
        discovered_at=datetime(2026, 6, 24, tzinfo=UTC),
        priority_score=5,
    )

    assert record.post_url == "https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000"


def test_run_manifest_uses_shared_namespace_and_status() -> None:
    manifest = RunManifest(
        run_id="run_1",
        namespace=AppNamespace.OPPORTUNITY_INTEL,
        status=RunStatus.RUNNING,
        started_at=datetime(2026, 6, 24, tzinfo=UTC),
        counts={"raw_comments": 10},
    )

    assert manifest.namespace == AppNamespace.OPPORTUNITY_INTEL
    assert manifest.counts["raw_comments"] == 10
