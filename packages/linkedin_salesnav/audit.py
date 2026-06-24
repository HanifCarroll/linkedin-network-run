"""Sent invitation audit contracts and artifact parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

SENT_INVITATIONS_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"


@dataclass(frozen=True)
class AuditRequest:
    load_more: int = 0
    out: Path | None = None


@dataclass(frozen=True)
class AuditArtifact:
    captured_at: datetime | None
    url: str
    people_count: int
    recent_names: tuple[str, ...]
    sample: str | None = None


def load_audit_artifact(path: Path) -> AuditArtifact:
    with path.open("r", encoding="utf-8") as handle:
        data: object = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError("audit artifact must be a JSON object")
    payload = cast(Mapping[str, object], data)
    names = payload.get("recentNames", [])
    if not isinstance(names, list):
        raise ValueError("audit artifact recentNames must be a list")
    return AuditArtifact(
        captured_at=_datetime_value(payload.get("capturedAt")),
        url=_str_value(payload.get("url"), default=""),
        people_count=_int_value(payload.get("peopleCount")),
        recent_names=tuple(item for item in names if isinstance(item, str)),
        sample=_optional_str(payload.get("sample")),
    )


def _int_value(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("audit artifact peopleCount must be an integer")
    return value


def _str_value(value: object, *, default: str) -> str:
    return value if isinstance(value, str) else default


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _datetime_value(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None
