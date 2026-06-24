"""Shared filesystem paths for the LinkedIn tools monorepo."""

from __future__ import annotations

from pathlib import Path

APP_NAME = "linkedin-tools"
DEFAULT_STATE_ROOT = Path.home() / "Library" / "Application Support" / APP_NAME
