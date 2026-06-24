from __future__ import annotations

from apps.cli import APP_NAMES, main
from packages.linkedin_browser import DEFAULT_BROWSER_PROFILE_NAME
from packages.linkedin_common import APP_NAME


def test_top_level_cli_namespaces_are_registered() -> None:
    assert APP_NAMES == ("network", "recruiter-agency", "opportunity", "comments", "ui")
    assert main(["network"]) == 0


def test_shared_defaults_match_prd() -> None:
    assert APP_NAME == "linkedin-tools"
    assert DEFAULT_BROWSER_PROFILE_NAME == "LinkedIn"
