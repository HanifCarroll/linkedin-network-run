"""Small logging setup helpers shared by CLIs and app services."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


class JsonLogFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "ts": datetime.now(UTC).isoformat(),
        }
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra", None)
        if isinstance(extra, Mapping):
            payload["extra"] = dict(extra)
        return json.dumps(payload, sort_keys=True)


def configure_logging(*, level: int = logging.INFO, json_logs: bool = False) -> None:
    """Configure root logging once for command-line entrypoints."""

    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger without configuring global logging."""

    return logging.getLogger(name)
