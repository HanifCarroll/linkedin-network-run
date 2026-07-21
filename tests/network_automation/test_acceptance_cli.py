from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.network_automation.browser import PlaywriterBrowserClient
from apps.network_automation.cli import build_parser
from apps.network_automation.cli import main as network_main
from apps.network_automation.models import (
    FOUNDER_OWNER_BUYERS_LEAD_LIST,
    FOUNDER_OWNER_BUYERS_SOURCE,
    AcceptanceCheckCandidate,
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceInvitation,
    AcceptanceLeadListStatus,
    AcceptanceLedger,
    AcceptanceOutcomeEvent,
    AcceptanceStatus,
    CandidateEvent,
    CandidateStatus,
    GreetingEligibilityStatus,
    RelationshipRole,
    accepted_welcome_message,
)
from apps.network_automation.store import Store

from .helpers import (
    FakeLiveBrowserClient,
    _install_fake_live_browser,
    _run_id,
    _write_fake_artifact,
)


def _seed_pending_invitation(store: Store, name: str = "Duplicate Lead") -> None:
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name=name,
            profile_url=f"https://www.linkedin.com/sales/lead/{name.lower().replace(' ', '-')}",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)


def test_cli_acceptance_report_emits_daily_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_pending_invitation(Store(tmp_path))

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "report",
            "--daily-days",
            "7",
            "--timezone",
            "UTC",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["daily_timezone"] == "UTC"
    assert len(report["daily"]) == 7
    assert report["daily"][-1]["coverage_complete"] is None
    assert report["daily_windows"] == [
        {
            "days": 7,
            "newly_confirmed_accepted": 0,
            "per_calendar_day": 0.0,
            "complete_days": 0,
            "missing_or_incomplete_days": 7,
        }
    ]


def test_cli_invalidate_weak_message_acceptances_is_guarded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    invitation = AcceptanceInvitation(
        run_id=_run_id(),
        run_date=date(2026, 6, 24),
        source="ASAP - Agency Owners Delivery",
        name="Weak Lead",
        profile_url="https://www.linkedin.com/sales/lead/weak-lead",
        sent_at=datetime(2026, 6, 24, tzinfo=UTC),
        latest_status=AcceptanceStatus.ACCEPTED,
        latest_checked_at=datetime(2026, 7, 2, tzinfo=UTC),
        history=[
            AcceptanceOutcomeEvent(
                at=datetime(2026, 7, 2, tzinfo=UTC),
                status=AcceptanceStatus.ACCEPTED,
                relationship="1st",
                note="profile shows first-degree/message evidence",
            )
        ],
    )
    store.save_acceptance_ledger(AcceptanceLedger(invitations=[invitation]))
    store.save_acceptance_followup_ledger(
        AcceptanceFollowupLedger(
            drafts=[
                AcceptanceFollowupRecord(
                    key=invitation.key(),
                    id="afu_weak",
                    source=invitation.source,
                    name=invitation.name,
                    profile_url=invitation.profile_url,
                    accepted_at=datetime(2026, 7, 2, tzinfo=UTC),
                    draft=accepted_welcome_message("Weak"),
                    status=AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                    report_path="greetings.md",
                )
            ]
        )
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "invalidate-weak-message-acceptances",
            "--sample-limit",
            "1",
        ]
    )
    assert exit_code == 0
    assert "dry-run: 1 invitation(s), 1 follow-up draft(s)" in capsys.readouterr().out
    assert store.load_acceptance_ledger().invitations[0].latest_status == (
        AcceptanceStatus.ACCEPTED
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "invalidate-weak-message-acceptances",
            "--apply",
            "--sample-limit",
            "1",
        ]
    )
    assert exit_code == 0
    assert "applied: 1 invitation(s), 1 follow-up draft(s)" in capsys.readouterr().out
    assert store.load_acceptance_ledger().invitations[0].latest_status == (
        AcceptanceStatus.INVALIDATED
    )
    assert store.load_acceptance_followup_ledger().drafts[0].status == (
        AcceptanceFollowupStatus.INVALID_ACCEPTANCE
    )


def test_cli_exposes_only_current_enrichment_welcome_and_watchlist_commands() -> None:
    parser = build_parser()
    current = [
        "export-enrichment-queue",
        "export-browser-investigation-queue",
        "apply-browser-investigation",
        "launch-enrichment-workers",
        "collect-enrichment-workers",
        "prepare-welcome-messages",
        "run-welcome-messages",
        "save-watchlist-leads",
        "send-greeting",
        "dry-run-greetings",
        "send-ready-greetings",
    ]
    for command in current:
        args = ["acceptance", command]
        if command == "send-greeting":
            args.extend(["--id", "afu_test"])
        elif command == "export-browser-investigation-queue":
            args.extend(["--out", "/tmp/browser-queue.json"])
        elif command == "apply-browser-investigation":
            args.extend(
                [
                    "--queue",
                    "/tmp/browser-queue.json",
                    "--enrichment",
                    "/tmp/browser-decisions.json",
                ]
            )
        assert parser.parse_args(args).acceptance_command == command

    removed = [
        "draft-followups",
        "export-research-queue",
        "launch-codex-research-workers",
        "export-message-queue",
        "finalize-message-queue",
        "send-followup",
        "prepare-approved-greetings",
        "run-approved-greeting-pilot",
        "save-lead-to-list",
    ]
    for command in removed:
        with pytest.raises(SystemExit):
            parser.parse_args(["acceptance", command])


def test_playwriter_acceptance_check_requires_first_degree_not_message_label() -> None:
    script = Path("apps/network_automation/playwriter_scripts/acceptance_outcomes.js").read_text()

    assert r"\b1st\b|\bMessage\b" not in script
    assert 'trim() === "1st"' in script
    assert 'page.locator("body")' not in script
    assert "profile action controls show Connect" in script
    assert "config.progressOut" in script


def test_playwriter_acceptance_lead_list_uses_exact_sales_nav_lead_identity() -> None:
    script = Path(
        "apps/network_automation/playwriter_scripts/acceptance_lead_list.js"
    ).read_text()

    assert "const expectedLeadId = salesNavLeadId(profileUrl);" in script
    assert "const loadedLeadId = salesNavLeadId(activePage.url());" in script
    assert "if (loadedLeadId !== expectedLeadId)" in script
    assert "activePage.getByText(record.name" not in script
    assert r"/^(.+) saved\. Add to a custom list\.$/" in script
    assert r"/^Save (.+) as a lead\. Save to list\.$/" in script
    assert "multiple Sales Navigator Save or Saved actions were visible" in script
    assert "await waitForProfileSaveAction(activePage)" in script
    assert "await classifyBlock(activePage)" in script
    assert "await activePage.waitForTimeout(500)" in script
    assert "elapsedMs: saveActionWait.elapsedMs" in script
    assert '.locator("#hue-web-menu-outlet button")' in script
    assert "clickExactListSelection(activePage, listName, profileName)" in script
    assert "expectedProfileName: profileName" in script
    assert 'menuOutlet.querySelectorAll("button")' in script
    assert "visibleElement(button) && exactAddAction.test(ariaLabel)" in script
    assert "customListButtons.length > 0 ? customListButtons : matchingButtons" in script
    assert "containingMenu?.parentElement?.closest(\"li[role='menuitem']\")" in script
    assert 'selectionScope: "custom_lists"' not in script
    assert '"custom_lists" : "visible_exact_match"' in script
    assert "^Add ${escapeRegExp(expectedProfileName)} to " in script
    assert "exactNodes" not in script
    assert "match.buttonCount !== 1" in script
    assert "selectedByRemoveAction" in script
    assert "^Remove ${escapeRegExp(expectedProfileName)} from " in script
    assert "list with [0-9]+ leads?$" in script


def test_playwriter_acceptance_followup_uses_current_sales_nav_message_dom() -> None:
    script = Path(
        "apps/network_automation/playwriter_scripts/acceptance_followup_send.js"
    ).read_text()

    assert 'button[data-anchor-send-inmail]' in script
    assert "waitForProfileMessageAction(page, record.name, 15000)" in script
    assert "multiple exact Sales Navigator Message actions were visible" in script
    assert "exact Sales Navigator Message action did not become hittable" in script
    assert "textarea[name='message'][aria-label='Type your message here…']" in script
    assert "form[data-x-conversation-widget='compose-form']" in script
    assert "section.thread-container" in script
    assert "h2[aria-label^='Conversation with ']" in script
    assert ".evaluate((node) => node.value)" in script
    assert "article [aria-label^='Message from ']" in script
    assert "scanVisibleActions(page, /^(Message|InMail)" not in script
    assert "contenteditable='true'" not in script
    assert "actionCandidateScore" not in script


def test_playwriter_acceptance_check_uses_direct_output_staging(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any], str]] = []
    client = PlaywriterBrowserClient(out_dir=tmp_path, session="test", playwriter_bin="playwriter")

    def fake_run_script(
        script: Path,
        config: dict[str, Any],
        *,
        staging: str = "shared",
    ) -> None:
        calls.append((script.name, config, staging))
        _write_fake_artifact(
            Path(config["out"]),
            {
                "capturedAt": "2026-07-03T12:00:00Z",
                "input": config["input"],
                "count": 1,
                "offset": config["offset"],
                "limit": config["limit"],
                "totalCandidates": 1,
                "complete": True,
                "rows": [
                    {
                        "source": "ASAP - Agency Owners Delivery",
                        "name": "Direct Lead",
                        "profileUrl": "https://www.linkedin.com/sales/lead/direct",
                        "status": "accepted",
                        "checkedAt": "2026-07-03T12:00:00Z",
                        "relationship": "1st",
                        "evidence": "fixture",
                        "note": "fixture",
                    }
                ],
            },
        )

    client._run_script = fake_run_script  # type: ignore[method-assign]
    artifact, _ = client.check_acceptance_outcomes(
        candidates=[
            AcceptanceCheckCandidate(
                run_id=str(_run_id()),
                run_date=date(2026, 6, 24),
                source="ASAP - Agency Owners Delivery",
                name="Direct Lead",
                profile_url="https://www.linkedin.com/sales/lead/direct",
                sent_at=datetime(2026, 6, 24, tzinfo=UTC),
                latest_status=AcceptanceStatus.SENT,
                latest_checked_at=None,
            )
        ],
        input_path=tmp_path / "acceptance-candidates.json",
        out=tmp_path / "chunk-0.json",
        offset=0,
        limit=1,
    )

    assert artifact.rows[0].name == "Direct Lead"
    assert calls[0][0] == "acceptance_outcomes.js"
    assert calls[0][2] == "direct"


def test_cli_send_greeting_dry_run_uses_live_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    record = AcceptanceFollowupRecord(
        key="active-founder",
        id="afu_active",
        source=FOUNDER_OWNER_BUYERS_SOURCE,
        name="Active Founder",
        profile_url="https://www.linkedin.com/in/active-founder",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/active-founder",
        accepted_at=datetime(2026, 7, 13, tzinfo=UTC),
        draft=accepted_welcome_message("Active"),
        relationship_role=RelationshipRole.BUYER,
        greeting_eligibility_status=GreetingEligibilityStatus.ELIGIBLE,
        original_connection_approved_at=datetime(2026, 7, 12, tzinfo=UTC),
        original_connection_approval_reason="Approved in original connection review.",
        sales_nav_list_name=FOUNDER_OWNER_BUYERS_LEAD_LIST,
        sales_nav_list_status=AcceptanceLeadListStatus.SAVED,
        report_path="greetings.md",
    )
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=[record]))
    out_dir = tmp_path / "greeting-browser"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "send-greeting",
            "--id",
            record.id,
            "--dry-run",
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].out_dir == out_dir
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "followup:Active Founder:dry=True:preview=False:allow=False"
    ]
    assert store.load_acceptance_followup_ledger().drafts[0].status == (
        AcceptanceFollowupStatus.DRY_RUN_READY
    )


def test_cli_acceptance_check_uses_live_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_live_browser(monkeypatch)
    candidates = tmp_path / "candidates.json"
    out = tmp_path / "outcomes.json"
    candidates.write_text(
        json.dumps(
            [
                {
                    "run_id": str(_run_id()),
                    "run_date": "2026-06-24",
                    "source": "ASAP - Agency Owners Delivery",
                    "name": "Duplicate Lead",
                    "profile_url": "https://www.linkedin.com/sales/lead/dup",
                    "sent_at": "2026-06-16T12:00:00Z",
                    "latest_status": "sent",
                    "latest_checked_at": None,
                }
            ]
        )
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "check",
            "--in",
            str(candidates),
            "--out",
            str(out),
            "--limit",
            "1",
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "acceptance-check:1:offset=0:limit=1:delay=500"
    ]
    assert json.loads(out.read_text())["rows"][0]["status"] == "accepted"


def test_cli_daily_session_is_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    _seed_pending_invitation(store)
    candidates = tmp_path / "acceptance-candidates.json"
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"
    browser_out_dir = tmp_path / "acceptance-session"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(candidates),
            "--outcomes-out",
            str(outcomes),
            "--chunk-dir",
            str(chunks),
            "--chunk-size",
            "1",
            "--out-dir",
            str(browser_out_dir),
        ]
    )

    assert exit_code == 0
    assert len(FakeLiveBrowserClient.instances) == 1
    assert FakeLiveBrowserClient.instances[0].calls == [
        "acceptance-check:1:offset=0:limit=1:delay=750"
    ]
    assert json.loads(outcomes.read_text())["rows"][0]["status"] == "accepted"
    assert store.load_acceptance_followup_ledger().drafts == []
    daily_runs = store.load_acceptance_daily_runs()
    assert len(daily_runs) == 1
    assert daily_runs[0].coverage_complete is True
    assert daily_runs[0].eligible == 1
    assert daily_runs[0].checked == 1
    assert daily_runs[0].newly_confirmed_accepted == 1
    output = capsys.readouterr().out
    assert "acceptance check: chunk 1/1 completed with 1 row(s)" in output
    assert "daily acceptance coverage: complete" in output


def test_cli_daily_session_retries_then_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_failures_remaining = 3
    store = Store(tmp_path)
    _seed_pending_invitation(store, "Retry Lead")
    outcomes = tmp_path / "acceptance-outcomes.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(tmp_path / "acceptance-candidates.json"),
            "--outcomes-out",
            str(outcomes),
            "--chunk-dir",
            str(tmp_path / "chunks"),
            "--chunk-size",
            "1",
        ]
    )

    assert exit_code == 0
    assert "attempt 4/4" in capsys.readouterr().out
    assert outcomes.exists()
    assert FakeLiveBrowserClient.instances[0].recoveries == 3


def test_cli_daily_session_does_not_reuse_chunk_for_different_candidate_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    candidates = tmp_path / "acceptance-candidates.json"
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"

    for name in ("First Lead", "Second Lead"):
        _seed_pending_invitation(store, name)
        assert (
            network_main(
                [
                    "--state-dir",
                    str(tmp_path),
                    "acceptance",
                    "run-daily-session",
                    "--candidates-out",
                    str(candidates),
                    "--outcomes-out",
                    str(outcomes),
                    "--chunk-dir",
                    str(chunks),
                    "--chunk-size",
                    "1",
                ]
            )
            == 0
        )

    output = capsys.readouterr().out
    assert len(FakeLiveBrowserClient.instances) == 2
    assert "reused complete acceptance chunk" not in output
    assert json.loads(outcomes.read_text())["rows"][0]["name"] == "Second Lead"


def test_cli_daily_session_stops_on_blocked_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_status = "blocked"
    store = Store(tmp_path)
    _seed_pending_invitation(store, "Blocked Lead")
    outcomes = tmp_path / "acceptance-outcomes.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(tmp_path / "acceptance-candidates.json"),
            "--outcomes-out",
            str(outcomes),
            "--chunk-dir",
            str(tmp_path / "chunks"),
            "--chunk-size",
            "1",
        ]
    )

    assert exit_code == 0
    assert "chunk-0.json has 1 blocked rows" in capsys.readouterr().out
    assert not outcomes.exists()
    assert store.load_acceptance_followup_ledger().drafts == []
    daily_runs = store.load_acceptance_daily_runs()
    assert len(daily_runs) == 1
    assert daily_runs[0].coverage_complete is False
    assert daily_runs[0].eligible == 1
    assert daily_runs[0].checked == 0
    assert daily_runs[0].blocker is not None
