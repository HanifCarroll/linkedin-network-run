from __future__ import annotations

from pathlib import Path

import pytest

from packages.linkedin_common import CommentRecord
from packages.linkedin_storage import read_csv_rows, read_jsonl_models, write_csv_rows, write_jsonl


def test_jsonl_helpers_round_trip_pydantic_models(tmp_path: Path) -> None:
    path = tmp_path / "comments.jsonl"
    record = CommentRecord(
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:7340000000000000000/",
        comment_text="Need a better internal workflow.",
        commenter_name="Jane Doe",
        commenter_profile_url="https://www.linkedin.com/in/jane-doe/?trk=comments",
    )

    assert write_jsonl(path, [record]) == 1

    rows = read_jsonl_models(path, CommentRecord)
    assert rows == [record]


def test_jsonl_fixture_validates_to_comment_record() -> None:
    fixture = Path("tests/fixtures/shared_foundation/raw_comments.jsonl")

    rows = read_jsonl_models(fixture, CommentRecord)

    assert rows[0].commenter_profile_url == "https://www.linkedin.com/in/jane-doe"


def test_csv_helpers_round_trip_explicit_columns(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    rows = [{"name": "Jane", "note": "line one\nline two", "empty": None}]

    assert write_csv_rows(path, rows, fieldnames=["name", "note", "empty"]) == 1

    assert read_csv_rows(path) == [{"name": "Jane", "note": "line one\nline two", "empty": ""}]


def test_csv_writer_rejects_undeclared_columns(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="columns not declared"):
        write_csv_rows(
            tmp_path / "rows.csv",
            [{"name": "Jane", "extra": "not declared"}],
            fieldnames=["name"],
        )


def test_csv_fixture_uses_common_provider_contract() -> None:
    fixture = Path("tests/fixtures/shared_foundation/source_comments.csv")

    rows = read_csv_rows(fixture)

    assert rows[0]["comment_text"] == "We are still stitching this together in spreadsheets."
