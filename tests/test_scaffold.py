from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from apps.cli import APP_NAMES, main
from apps.cutover.automation_audit import AUTOMATION_SPECS
from packages.linkedin_common import APP_NAME


def test_top_level_cli_namespaces_are_registered(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert APP_NAMES == (
        "network",
        "recruiter-agency",
        "opportunity",
        "comments",
        "ui",
        "cutover",
    )
    assert main(["network", "--state-dir", str(tmp_path), "start", "--target", "1"]) == 0
    capsys.readouterr()
    assert main(["network", "--state-dir", str(tmp_path), "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"] == 1


def test_top_level_cli_dispatches_opportunity_contracts(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["opportunity", "validate-contracts"]) == 0
    assert "validated" in capsys.readouterr().out


def test_cutover_automation_audit_reports_pre_cutover_prompts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_prompt_set(tmp_path, "old")

    assert main(["cutover", "audit-automations", "--root", str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert {item["status"] for item in payload["audits"]} == {"pre_cutover"}


def test_cutover_automation_audit_accepts_post_cutover_expectation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_prompt_set(tmp_path, "new")

    assert (
        main(
            [
                "cutover",
                "audit-automations",
                "--root",
                str(tmp_path),
                "--expect",
                "post-cutover",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert {item["status"] for item in payload["audits"]} == {"post_cutover"}


def test_cutover_automation_audit_fails_wrong_expectation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_prompt_set(tmp_path, "old")

    assert (
        main(
            [
                "cutover",
                "audit-automations",
                "--root",
                str(tmp_path),
                "--expect",
                "post-cutover",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "automation prompt cutover audit failed" in captured.err
    assert "`pre_cutover`" in captured.out


def test_cutover_automation_audit_fails_partial_post_cutover_prompt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_prompt_set(tmp_path, "new")
    first_spec = AUTOMATION_SPECS[0]
    partial_prompt = first_spec.new_markers[0]
    (tmp_path / first_spec.relative_path).write_text(
        "\n".join(
            [
                "version = 1",
                f'id = "{first_spec.automation_id}"',
                f"prompt = {json.dumps(partial_prompt)}",
                'status = "ACTIVE"',
                "",
            ]
        )
    )

    assert main(["cutover", "audit-automations", "--root", str(tmp_path)]) == 1

    captured = capsys.readouterr()
    assert "automation prompt cutover audit failed" in captured.err
    assert "Application Support/linkedin-tools/network-automation" in captured.out


def test_cutover_automation_edit_plan_outputs_exact_replacements(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_prompt_set(tmp_path, "old")

    assert (
        main(
            [
                "cutover",
                "plan-automation-edits",
                "--root",
                str(tmp_path),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    plans = {item["automation_id"]: item for item in payload["plans"]}

    network_plan = plans["linkedin-network"]
    assert network_plan["current_status"] == "pre_cutover"
    assert (
        network_plan["state_dir"]
        == "$HOME/Library/Application Support/linkedin-tools/network-automation"
    )
    network_replacements = network_plan["command_replacements"]
    assert any("salesnav-audit.js" in item["old"] for item in network_replacements)
    assert any("reconcile-audit --session auto" in item["new"] for item in network_replacements)

    recruiter_plan = plans["recruiter-agency-outreach-daily"]
    assert (
        recruiter_plan["state_dir"]
        == "$HOME/Library/Application Support/linkedin-tools/recruiter-agency-outreach"
    )
    assert any("go build" in item for item in recruiter_plan["remove_instructions"])
    assert any(
        "uv run linkedin-tools recruiter-agency" in item["new"]
        for item in recruiter_plan["command_replacements"]
    )


def test_shared_defaults_match_prd() -> None:
    assert APP_NAME == "linkedin-tools"


def test_package_data_includes_runtime_assets() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert package_data["apps.network_automation"] == ["playwriter_scripts/*.js"]
    assert package_data["apps.comment_extractor"] == ["playwriter_scripts/*.js"]
    assert package_data["apps.opportunity_intel"] == [
        "data/*.json",
        "playwriter_scripts/*.js",
    ]
    assert package_data["apps.recruiter_agency_outreach"] == ["playwriter_scripts/*.js"]
    assert "templates/*.html" in package_data["apps.review_ui"]
    assert "static/*.css" in package_data["apps.review_ui"]


def _write_prompt_set(root: Path, marker_set: str) -> None:
    for spec in AUTOMATION_SPECS:
        path = root / spec.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        markers = spec.old_markers if marker_set == "old" else spec.new_markers
        prompt = "\n".join(markers)
        path.write_text(
            "\n".join(
                [
                    "version = 1",
                    f'id = "{spec.automation_id}"',
                    f"prompt = {json.dumps(prompt)}",
                    'status = "ACTIVE"',
                    "",
                ]
            )
        )
