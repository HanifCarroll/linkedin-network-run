"""Chrome profile configuration for LinkedIn browser automation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BROWSER_PROFILE_NAME = "LinkedIn"
DEFAULT_CHROME_USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
DEFAULT_AUTOMATION_CHROME_USER_DATA_DIR = (
    Path.home() / "Library" / "Application Support" / "linkedin-tools" / "chrome-automation"
)
MANAGED_CHROME_PROFILES_DIR = "managed-profiles"
DEFAULT_PLAYWRITER_CDP_URL = "ws://127.0.0.1:19988/cdp"
LINKEDIN_CDP_URL_ENV = "LINKEDIN_TOOLS_CDP_URL"
LINKEDIN_BROWSER_PROFILE_MODE_ENV = "LINKEDIN_TOOLS_BROWSER_PROFILE_MODE"
LINKEDIN_PROFILE_ENV = "LINKEDIN_TOOLS_CHROME_USER_DATA_DIR"
LINKEDIN_PROFILE_NAME_ENV = "LINKEDIN_TOOLS_CHROME_PROFILE_NAME"


@dataclass(frozen=True)
class ChromeProfileConfig:
    """Configuration for a persistent Chrome context backed by a named profile."""

    user_data_dir: Path = DEFAULT_CHROME_USER_DATA_DIR
    profile_name: str = DEFAULT_BROWSER_PROFILE_NAME
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def launch_args(self) -> list[str]:
        return [
            f"--profile-directory={self.profile_name}",
            *self.extra_args,
        ]

    def with_extra_args(self, args: Sequence[str]) -> ChromeProfileConfig:
        return ChromeProfileConfig(
            user_data_dir=self.user_data_dir,
            profile_name=self.profile_name,
            extra_args=tuple(args),
        )


def chrome_profile_from_env(environ: Mapping[str, str] | None = None) -> ChromeProfileConfig:
    source = environ if environ is not None else os.environ
    user_data_dir = _user_data_dir_for_mode(source)
    profile_name = source.get(LINKEDIN_PROFILE_NAME_ENV, DEFAULT_BROWSER_PROFILE_NAME)
    return ChromeProfileConfig(
        user_data_dir=user_data_dir,
        profile_name=profile_name,
    )


def chrome_profile_storage_dir(config: ChromeProfileConfig) -> Path:
    if _same_path(config.user_data_dir, DEFAULT_CHROME_USER_DATA_DIR):
        return config.user_data_dir / config.profile_name
    if config.profile_name.strip().casefold() == "default":
        return config.user_data_dir
    return (
        config.user_data_dir
        / MANAGED_CHROME_PROFILES_DIR
        / _safe_profile_dir(config.profile_name)
    )


def _user_data_dir_for_mode(source: Mapping[str, str]) -> Path:
    explicit_dir = source.get(LINKEDIN_PROFILE_ENV)
    if explicit_dir:
        return Path(explicit_dir)

    mode = source.get(LINKEDIN_BROWSER_PROFILE_MODE_ENV, "real").strip().casefold()
    if mode in {"", "real"}:
        return DEFAULT_CHROME_USER_DATA_DIR
    if mode == "automation":
        return DEFAULT_AUTOMATION_CHROME_USER_DATA_DIR
    if mode == "custom":
        raise ValueError(f"{LINKEDIN_PROFILE_ENV} is required when profile mode is custom")
    raise ValueError(f"{LINKEDIN_BROWSER_PROFILE_MODE_ENV} must be automation, real, or custom")


def _same_path(left: Path, right: Path) -> bool:
    return os.fspath(left.expanduser().resolve()) == os.fspath(right.expanduser().resolve())


def _safe_profile_dir(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return name or "Default"
