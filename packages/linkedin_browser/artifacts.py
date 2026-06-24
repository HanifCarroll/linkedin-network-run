"""Artifact writers for browser captures and guarded actions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class ScreenshotPage(Protocol):
    async def screenshot(self, *, path: str, full_page: bool = True) -> bytes: ...


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    path: Path
    created_at: datetime


@dataclass(frozen=True)
class ArtifactWriter:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, payload: object) -> ArtifactRef:
        path = self.root / f"{_safe_name(name)}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
        return ArtifactRef(kind="json", path=path, created_at=_now())

    async def screenshot(
        self,
        page: ScreenshotPage,
        name: str,
        *,
        full_page: bool = True,
    ) -> ArtifactRef:
        path = self.root / f"{_safe_name(name)}.png"
        await page.screenshot(path=str(path), full_page=full_page)
        return ArtifactRef(kind="screenshot", path=path, created_at=_now())

    async def debug_bundle(
        self,
        *,
        name: str,
        payload: object,
        page: ScreenshotPage | None = None,
    ) -> list[ArtifactRef]:
        refs = [self.write_json(name, payload)]
        if page is not None:
            refs.append(await self.screenshot(page, name))
        return refs


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip())
    return cleaned.strip("-") or "artifact"


def _now() -> datetime:
    return datetime.now(UTC)
