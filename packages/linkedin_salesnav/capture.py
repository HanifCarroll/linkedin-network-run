"""Sales Navigator capture request contracts and artifact parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from .models import MenuLabel, SalesNavCaptureArtifact, SalesNavCaptureRow


@dataclass(frozen=True)
class CaptureRequest:
    url: str | None = None
    source: str | None = None
    pages: int = 1
    limit: int = 25
    stop_after_connectable: int = 0
    only_connectable: bool = False
    open_menus: bool = False
    use_api_state: bool = True


def load_capture_artifact(path: Path) -> SalesNavCaptureArtifact:
    return capture_artifact_from_mapping(_load_mapping(path))


def capture_artifact_from_mapping(payload: Mapping[str, object]) -> SalesNavCaptureArtifact:
    rows_value = payload.get("rows", [])
    if not isinstance(rows_value, list):
        raise ValueError("capture artifact rows must be a list")
    rows = tuple(_capture_row_from_mapping(_ensure_mapping(item)) for item in rows_value)
    return SalesNavCaptureArtifact(
        schema_version=_int_value(payload.get("schemaVersion"), default=1),
        captured_at=_datetime_value(payload.get("capturedAt")),
        url=_str_value(payload.get("url"), default=""),
        resume_url=_optional_str(payload.get("resumeUrl")),
        source=_optional_str(payload.get("source")),
        menu_inspection=_optional_str(payload.get("menuInspection")),
        raw_row_count=_int_value(payload.get("rawRowCount"), default=len(rows)),
        output_row_count=_int_value(payload.get("outputRowCount"), default=len(rows)),
        rows=rows,
    )


def _capture_row_from_mapping(payload: Mapping[str, object]) -> SalesNavCaptureRow:
    visible_state = _optional_mapping(payload.get("visibleState"))
    labels_value = payload.get("menuLabels", [])
    if not isinstance(labels_value, list):
        raise ValueError("capture row menuLabels must be a list")
    labels = tuple(_menu_label_from_mapping(_ensure_mapping(item)) for item in labels_value)
    return SalesNavCaptureRow(
        index=_int_value(payload.get("index"), default=0),
        name=_optional_str(payload.get("name")),
        profile_url=_optional_str(payload.get("profileUrl")),
        menu_state=_str_value(payload.get("menuState"), default="unknown"),
        page_number=_optional_int(payload.get("pageNumber")),
        global_index=_optional_int(payload.get("globalIndex")),
        scroll_urn=_optional_str(payload.get("scrollUrn")),
        visible_saved=_optional_bool(visible_state.get("saved") if visible_state else None),
        visible_viewed=_optional_bool(visible_state.get("viewed") if visible_state else None),
        visible_has_message=_optional_bool(
            visible_state.get("hasMessage") if visible_state else None
        ),
        visible_has_save=_optional_bool(visible_state.get("hasSave") if visible_state else None),
        menu_labels=labels,
    )


def _menu_label_from_mapping(payload: Mapping[str, object]) -> MenuLabel:
    return MenuLabel(
        index=_int_value(payload.get("index"), default=0),
        text=_optional_str(payload.get("text")),
        aria=_optional_str(payload.get("aria")),
        tag=_optional_str(payload.get("tag")),
        href=_optional_str(payload.get("href")),
        disabled=_bool_value(payload.get("disabled"), default=False),
    )


def _load_mapping(path: Path) -> Mapping[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data: object = json.load(handle)
    return _ensure_mapping(data)


def _ensure_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("expected JSON object")
    return cast(Mapping[str, object], value)


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    return _ensure_mapping(value)


def _str_value(value: object, *, default: str) -> str:
    return value if isinstance(value, str) else default


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_value(value: object, *, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bool_value(value: object, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _datetime_value(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None
