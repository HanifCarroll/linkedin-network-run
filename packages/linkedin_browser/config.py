"""Chrome profile configuration for LinkedIn browser automation."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BROWSER_PROFILE_NAME = "LinkedIn"
DEFAULT_CHROME_USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
LINKEDIN_PROFILE_ENV = "LINKEDIN_TOOLS_CHROME_USER_DATA_DIR"
LINKEDIN_PROFILE_NAME_ENV = "LINKEDIN_TOOLS_CHROME_PROFILE_NAME"


@dataclass(frozen=True)
class ChromeProfileConfig:
    """Configuration for a persistent Chrome context backed by a named profile."""

    user_data_dir: Path = DEFAULT_CHROME_USER_DATA_DIR
    profile_name: str = DEFAULT_BROWSER_PROFILE_NAME
    channel: str = "chrome"
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
    user_data_dir = Path(source.get(LINKEDIN_PROFILE_ENV, str(DEFAULT_CHROME_USER_DATA_DIR)))
    profile_name = source.get(LINKEDIN_PROFILE_NAME_ENV, DEFAULT_BROWSER_PROFILE_NAME)
    return ChromeProfileConfig(user_data_dir=user_data_dir, profile_name=profile_name)
