"""Small stderr progress reporter for long-running CLI flows."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO


@dataclass(frozen=True)
class ProgressReporter:
    stream: TextIO = sys.stderr
    enabled: bool = True

    def emit(self, event: str, **fields: object) -> None:
        if not self.enabled:
            return
        parts = ["progress", _now_iso(), event]
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"{key}={_format_value(value)}")
        print(" ".join(parts), file=self.stream, flush=True)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_value(value: object) -> str:
    text = str(value).replace("\n", " ").replace("\t", " ").strip()
    return text if text else '""'
