from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from apps.cli import APP_NAMES, main
from packages.linkedin_browser import DEFAULT_BROWSER_PROFILE_NAME
from packages.linkedin_common import APP_NAME


def test_top_level_cli_namespaces_are_registered(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert APP_NAMES == ("network", "recruiter-agency", "opportunity", "comments", "ui")
    assert main(["network", "--state-dir", str(tmp_path), "start", "--target", "1"]) == 0
    capsys.readouterr()
    assert main(["network", "--state-dir", str(tmp_path), "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == 1


def test_top_level_cli_dispatches_opportunity_contracts(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["opportunity", "validate-contracts"]) == 0
    assert "validated" in capsys.readouterr().out


def test_shared_defaults_match_prd() -> None:
    assert APP_NAME == "linkedin-tools"
    assert DEFAULT_BROWSER_PROFILE_NAME == "LinkedIn"


def test_package_data_includes_runtime_assets() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert package_data["apps.opportunity_intel"] == ["data/*.json"]
    assert "templates/*.html" in package_data["apps.review_ui"]
    assert "static/*.css" in package_data["apps.review_ui"]
