from __future__ import annotations

import json

import pytest

from apps.cli import main
from packages.linkedin_browser.incident import (
    ActiveLinkedInIncidentError,
    IncidentKind,
    active_incident,
    assert_no_active_incident,
    browser_operation_lock,
    clear_incident,
    detect_fatal_incident,
    open_incident,
)


def test_incident_gate_requires_explicit_manual_clearance() -> None:
    incident = open_incident(
        kind=IncidentKind.UNUSUAL_ACTIVITY,
        source="test",
        summary="LinkedIn unusual-activity warning",
    )

    with pytest.raises(ActiveLinkedInIncidentError, match=str(incident.id)):
        assert_no_active_incident()
    with pytest.raises(RuntimeError, match="confirmed account access"):
        clear_incident(
            reason="warning cleared",
            account_access_confirmed=False,
            warning_cleared_confirmed=True,
        )

    cleared = clear_incident(
        reason="manual account review completed",
        account_access_confirmed=True,
        warning_cleared_confirmed=True,
    )

    assert cleared.active is False
    assert active_incident() is None


def test_incident_gate_accumulates_fatal_observations() -> None:
    first = open_incident(
        kind=IncidentKind.RATE_LIMIT,
        source="test",
        summary="LinkedIn returned HTTP 429",
    )
    second = open_incident(
        kind=IncidentKind.LOGIN_REQUIRED,
        source="test",
        summary="LinkedIn login required",
    )

    assert second.id == first.id
    assert [item.kind for item in second.observations] == [
        IncidentKind.RATE_LIMIT,
        IncidentKind.LOGIN_REQUIRED,
    ]


def test_detect_fatal_incident_uses_explicit_signals() -> None:
    assert detect_fatal_incident({"httpStatus": 429}) == (
        IncidentKind.RATE_LIMIT,
        "LinkedIn returned HTTP 429",
    )
    assert detect_fatal_incident({"note": "login required"}) == (
        IncidentKind.LOGIN_REQUIRED,
        "LinkedIn login required",
    )
    assert detect_fatal_incident({"status": "failed", "note": "ordinary timeout"}) is None


def test_browser_operation_lock_rejects_overlap() -> None:
    with browser_operation_lock("first"):
        with pytest.raises(RuntimeError, match="another LinkedIn browser operation"):
            with browser_operation_lock("second"):
                raise AssertionError("overlapping operation should not start")


def test_incident_cli_status_and_guarded_clear(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "incident",
                "open",
                "--kind",
                "manual-pause",
                "--source",
                "test",
                "--summary",
                "paused",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["incident", "status", "--json"]) == 2
    status = json.loads(capsys.readouterr().out)
    assert status["active"] is True

    with pytest.raises(RuntimeError, match="confirmed account access"):
        main(["incident", "clear", "--reason", "not enough"])

    assert (
        main(
            [
                "incident",
                "clear",
                "--reason",
                "manual review complete",
                "--confirm-account-access",
                "--confirm-warning-cleared",
            ]
        )
        == 0
    )
