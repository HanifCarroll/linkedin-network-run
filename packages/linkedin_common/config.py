"""Shared configuration contracts for local LinkedIn tool state."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .paths import DEFAULT_STATE_ROOT


class AppNamespace(StrEnum):
    """Durable state namespaces owned by the Python LinkedIn tools."""

    NETWORK = "network-automation"
    RECRUITER_AGENCY = "recruiter-agency-outreach"
    OPPORTUNITY_INTEL = "opportunity-intel"
    COMMENT_EXTRACTOR = "comment-extractor"
    REVIEW_UI = "review-ui"


_OLD_STATE_ROOTS: dict[AppNamespace, Path] = {
    AppNamespace.NETWORK: Path.home() / "Library" / "Application Support" / "linkedin-network-run",
    AppNamespace.RECRUITER_AGENCY: Path.home()
    / "Library"
    / "Application Support"
    / "recruiter-agency-outreach",
    AppNamespace.OPPORTUNITY_INTEL: Path("/tmp/linkedin-opportunity-signals"),
}


class AppConfig(BaseModel):
    """Resolved local config for one app namespace."""

    model_config = ConfigDict(extra="forbid")

    namespace: AppNamespace
    state_root: Path
    browser_profile_name: str = Field(default="LinkedIn", min_length=1)

    @classmethod
    def for_namespace(
        cls,
        namespace: AppNamespace,
        *,
        root: Path = DEFAULT_STATE_ROOT,
        browser_profile_name: str = "LinkedIn",
    ) -> AppConfig:
        return cls(
            namespace=namespace,
            state_root=state_root_for_namespace(namespace, root=root),
            browser_profile_name=browser_profile_name,
        )


def state_root_for_namespace(namespace: AppNamespace, *, root: Path = DEFAULT_STATE_ROOT) -> Path:
    """Return the new SQLite-backed state directory for an app namespace."""

    return root / namespace.value


def old_state_root(namespace: AppNamespace) -> Path | None:
    """Return the old read-only import root when a namespace has one."""

    return _OLD_STATE_ROOTS.get(namespace)
