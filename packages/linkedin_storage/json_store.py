"""JSONL and CSV stream helpers for capture, import, and review files."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel


def _jsonable_row(row: BaseModel | Mapping[str, object]) -> dict[str, object]:
    if isinstance(row, BaseModel):
        return row.model_dump(mode="json")
    return dict(row)


def write_jsonl(path: Path, rows: Iterable[BaseModel | Mapping[str, object]]) -> int:
    """Write rows as newline-delimited JSON and return the number written."""

    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_jsonable_row(row), sort_keys=True))
            handle.write("\n")
            count += 1
    return count


def read_jsonl_dicts(path: Path) -> list[dict[str, object]]:
    """Read newline-delimited JSON objects from disk."""

    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def read_jsonl_models[TModel: BaseModel](path: Path, model: type[TModel]) -> list[TModel]:
    """Read newline-delimited JSON objects and validate them with a Pydantic model."""

    return [model.model_validate(row) for row in read_jsonl_dicts(path)]


def write_csv_rows(
    path: Path,
    rows: Iterable[Mapping[str, object | None]],
    *,
    fieldnames: Sequence[str],
) -> int:
    """Write dictionaries to CSV with explicit columns."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames)
    field_set = set(fields)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            extra = set(row) - field_set
            if extra:
                raise ValueError(
                    f"CSV row contains columns not declared in fieldnames: {sorted(extra)}"
                )
            writer.writerow(
                {field: "" if row.get(field) is None else row.get(field, "") for field in fields}
            )
            count += 1
    return count


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV file as dictionaries with empty strings for missing cell values."""

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} does not have a CSV header")
        for raw_row in reader:
            if None in raw_row:
                raise ValueError(f"{path}:{reader.line_num} has more cells than header columns")
            rows.append({field: raw_row.get(field) or "" for field in reader.fieldnames})
    return rows
