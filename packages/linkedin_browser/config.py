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
LINKEDIN_BROWSER_CHANNEL_ENV = "LINKEDIN_TOOLS_BROWSER_CHANNEL"
LINKEDIN_BROWSER_HEADLESS_ENV = "LINKEDIN_TOOLS_BROWSER_HEADLESS"


@dataclass(frozen=True)
class ChromeProfileConfig:
    """Configuration for a persistent Chrome context backed by a named profile."""

    user_data_dir: Path = DEFAULT_CHROME_USER_DATA_DIR
    profile_name: str = DEFAULT_BROWSER_PROFILE_NAME
    channel: str | None = "chrome"
    headless: bool = False
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
            channel=self.channel,
            headless=self.headless,
            extra_args=tuple(args),
        )


def chrome_profile_from_env(environ: Mapping[str, str] | None = None) -> ChromeProfileConfig:
    source = environ if environ is not None else os.environ
    user_data_dir = _user_data_dir_for_mode(source)
    profile_name = source.get(LINKEDIN_PROFILE_NAME_ENV, DEFAULT_BROWSER_PROFILE_NAME)
    channel = _browser_channel(source.get(LINKEDIN_BROWSER_CHANNEL_ENV, "chrome"))
    headless = _env_bool(source.get(LINKEDIN_BROWSER_HEADLESS_ENV), default=False)
    return ChromeProfileConfig(
        user_data_dir=user_data_dir,
        profile_name=profile_name,
        channel=channel,
        headless=headless,
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


def _browser_channel(value: str) -> str | None:
    normalized = value.strip().casefold()
    if normalized in {"", "bundled", "playwright", "default"}:
        return None
    return value.strip()


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _same_path(left: Path, right: Path) -> bool:
    return os.fspath(left.expanduser().resolve()) == os.fspath(right.expanduser().resolve())


def _safe_profile_dir(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return name or "Default"
