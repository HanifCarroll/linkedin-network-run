from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.network_automation.acceptance_service import (
    acceptance_apply_research_decisions,
    acceptance_draft_followups,
    acceptance_export_message_queue,
    acceptance_export_research_queue,
    acceptance_import,
)
from apps.network_automation.browser import PlaywriterBrowserClient
from apps.network_automation.cli import main as network_main
from apps.network_automation.models import (
    AcceptanceCheckCandidate,
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceInvitation,
    AcceptanceLedger,
    AcceptanceOutcomeArtifact,
    AcceptanceOutcomeEvent,
    AcceptanceStatus,
    CandidateEvent,
    CandidateStatus,
    DraftStrategy,
)
from apps.network_automation.store import Store

from .helpers import (
    FIXTURES,
    FakeLiveBrowserClient,
    _install_fake_live_browser,
    _run_id,
    _write_fake_artifact,
)


def test_cli_acceptance_invalidate_weak_message_acceptances_is_guarded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
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
                    angle="general",
                    draft="Hey Weak. Thanks for connecting.",
                    status=AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                    report_path="followups.md",
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
    dry_run = capsys.readouterr().out
    assert "weak acceptance invalidation dry-run: 1 invitation(s), 1 follow-up draft(s)" in dry_run
    assert store.load_acceptance_ledger().invitations[0].latest_status == AcceptanceStatus.ACCEPTED

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
    applied = capsys.readouterr().out
    assert "weak acceptance invalidation applied: 1 invitation(s), 1 follow-up draft(s)" in applied
    assert (
        store.load_acceptance_ledger().invitations[0].latest_status
        == AcceptanceStatus.INVALIDATED
    )
    assert (
        store.load_acceptance_followup_ledger().drafts[0].status
        == AcceptanceFollowupStatus.INVALID_ACCEPTANCE
    )
    event = json.loads(store.acceptance_event_path.read_text().strip().splitlines()[-1])
    assert event["kind"] == "invalidate-weak-message-acceptances"


def test_cli_acceptance_finalize_message_queue_waits_and_writes_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    acceptance_import(store, FIXTURES / "acceptance_outcomes.json")
    research_queue = tmp_path / "research-queue.json"
    acceptance_export_research_queue(
        store,
        out=research_queue,
        markdown_out=None,
        offset=0,
        limit=1,
        include_drafted=False,
    )
    packet = json.loads(research_queue.read_text())
    packet["items"][0]["decision"] = {
        "status": "research_ready",
        "confidence": "high",
        "person_summary": "Duplicate Lead runs product work at Acme AI.",
        "company_name": "Acme AI",
        "company_summary": "Acme AI builds workflow automation products for service teams.",
        "official_company_url": "https://www.acme-ai.example",
        "evidence_urls": ["https://www.acme-ai.example"],
        "notes": "Identity and company match reviewed evidence.",
        "warnings": [],
    }
    research_decisions = tmp_path / "research-decisions.json"
    research_decisions.write_text(json.dumps(packet), encoding="utf-8")
    reviewed_research = tmp_path / "reviewed-research.json"
    acceptance_apply_research_decisions(
        store,
        input_path=research_decisions,
        out=reviewed_research,
    )
    message_queue = tmp_path / "message-queue.json"
    acceptance_export_message_queue(
        store,
        reviewed_research=reviewed_research,
        out=message_queue,
        markdown_out=None,
        include_drafted=False,
        offset=0,
        limit=1,
    )
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        f"""#!{sys.executable}
import json
import re
import sys

out = sys.argv[sys.argv.index("-o") + 1]
prompt = sys.stdin.read()
match = re.search(r"Return `candidate_key` exactly as: `([^`]+)`", prompt)
candidate_key = match.group(1)
message = (
    "Hey, Duplicate. Thanks for connecting.\\n\\n"
    "I build web apps and internal tools for teams that are stuck with "
    "spreadsheets, manual handoffs, or half-working automations.\\n\\n"
    "Acme AI's work on workflow automation for service teams seems adjacent "
    "to what I do: turning messy workflows into tools people can actually use."
    "\\n\\n"
    "Does that kind of cleanup come up much with the teams you work with?"
)
with open(out, "w", encoding="utf-8") as handle:
    json.dump({{
        "candidate_key": candidate_key,
        "status": "ready_for_draft",
        "message": message,
        "reason": "The reviewed research supports the workflow bridge.",
        "warnings": [],
    }}, handle)
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "finalize-message-queue",
            "--message-queue",
            str(message_queue),
            "--jobs-dir",
            str(tmp_path / "draft-jobs"),
            "--message-decisions-out",
            str(tmp_path / "message-decisions.json"),
            "--reviewed-research-out",
            str(tmp_path / "reviewed-research-final.json"),
            "--out",
            str(tmp_path / "followups.md"),
            "--review-out",
            str(tmp_path / "followups.review.json"),
            "--codex-bin",
            str(fake_codex),
            "--cwd",
            str(tmp_path),
            "--wait-seconds",
            "5",
            "--poll-seconds",
            "0.1",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "finalized accepted message queue" in output
    assert "1 ready, 0 needs review, 0 pending" in output
    assert (tmp_path / "message-decisions.json").exists()
    assert (tmp_path / "reviewed-research-final.json").exists()
    assert "turning messy workflows into tools people can actually use" in (
        tmp_path / "followups.md"
    ).read_text()
    review_packet = json.loads((tmp_path / "followups.review.json").read_text())
    assert review_packet["items"][0]["candidate"]["name"] == "Duplicate Lead"


def test_playwriter_acceptance_check_requires_first_degree_not_message_label() -> None:
    script = Path("apps/network_automation/playwriter_scripts/acceptance_outcomes.js").read_text()

    assert r"\b1st\b|\bMessage\b" not in script
    assert r"\b1st\b" in script


def test_playwriter_acceptance_check_resumes_existing_partial_chunk(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the Playwriter acceptance script")

    script_path = Path(
        "apps/network_automation/playwriter_scripts/acceptance_outcomes.js"
    ).resolve()
    config_path = tmp_path / "config.json"
    out_path = tmp_path / "chunk-0.json"
    visited_path = tmp_path / "visited.json"
    candidates = [
        {
            "source": "ASAP - Agency Owners Delivery",
            "name": "First Lead",
            "profile_url": "https://www.linkedin.com/sales/lead/first",
        },
        {
            "source": "ASAP - Agency Owners Delivery",
            "name": "Second Lead",
            "profile_url": "https://www.linkedin.com/sales/lead/second",
        },
    ]
    config_path.write_text(
        json.dumps(
            {
                "candidates": candidates,
                "input": str(tmp_path / "acceptance-candidates.json"),
                "out": str(out_path),
                "offset": 0,
                "limit": 2,
                "delayMs": 0,
            }
        )
    )
    out_path.write_text(
        json.dumps(
            {
                "capturedAt": "2026-07-03T12:00:00Z",
                "input": str(tmp_path / "acceptance-candidates.json"),
                "count": 1,
                "offset": 0,
                "limit": 2,
                "totalCandidates": 2,
                "complete": False,
                "rows": [
                    {
                        "source": "ASAP - Agency Owners Delivery",
                        "name": "First Lead",
                        "profileUrl": "https://www.linkedin.com/sales/lead/first",
                        "status": "accepted",
                        "checkedAt": "2026-07-03T12:00:00Z",
                        "relationship": "1st",
                        "evidence": "existing partial row",
                        "note": "fixture",
                    }
                ],
            }
        )
    )
    runner_path = tmp_path / "run-acceptance-outcomes.js"
    runner_path.write_text(
        f"""
const fs = require("node:fs");
const script = fs.readFileSync({json.dumps(str(script_path))}, "utf8");
const visited = [];
const mockPage = {{
  _url: "about:blank",
  isClosed: () => false,
  url: () => mockPage._url,
  goto: async (url) => {{
    visited.push(url);
    mockPage._url = url;
  }},
  waitForTimeout: async () => null,
  locator: () => ({{
    count: async () => 0,
    nth: () => ({{ isVisible: async () => false }}),
    innerText: async () => "1st",
    evaluateAll: async () => [],
  }}),
}};
const state = {{ linkedinToolsConfigPath: {json.dumps(str(config_path))} }};
const context = {{
  pages: () => [mockPage],
  newPage: async () => mockPage,
}};
const waitForPageLoad = async () => null;
const AsyncFunction = Object.getPrototypeOf(async function () {{}}).constructor;

(async () => {{
  await new AsyncFunction("require", "state", "context", "page", "waitForPageLoad", script)(
    require,
    state,
    context,
    mockPage,
    waitForPageLoad
  );
  fs.writeFileSync({json.dumps(str(visited_path))}, JSON.stringify(visited));
}})().catch((error) => {{
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
}});
"""
    )

    subprocess.run([node, str(runner_path)], check=True, cwd=Path.cwd())

    assert json.loads(visited_path.read_text()) == [
        "https://www.linkedin.com/sales/lead/second"
    ]
    artifact = json.loads(out_path.read_text())
    assert artifact["complete"] is True
    assert artifact["count"] == 2
    assert artifact["rows"][0]["evidence"] == "existing partial row"
    assert artifact["rows"][1]["name"] == "Second Lead"


def test_playwriter_acceptance_check_uses_direct_output_staging(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any], str]] = []
    client = PlaywriterBrowserClient(out_dir=tmp_path, session="test", playwriter_bin="playwriter")

    def fake_run_script(
        script: Path, config: dict[str, Any], *, staging: str = "shared"
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


def test_playwriter_acceptance_followup_uses_script_and_preserves_guards(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, dict[str, Any]]] = []
    record = AcceptanceFollowupRecord(
        key="source:lead",
        id="lead-1",
        source="source",
        name="Accepted Lead",
        profile_url="https://www.linkedin.com/in/accepted-lead",
        sales_nav_profile_url="https://www.linkedin.com/sales/lead/abc",
        accepted_at=datetime(2026, 6, 20, tzinfo=UTC),
        angle="general",
        draft="Hey Accepted. Thanks for connecting.",
        report_path=str(tmp_path / "followups.md"),
    )
    client = PlaywriterBrowserClient(out_dir=tmp_path, session="test", playwriter_bin="playwriter")

    def fake_run_script(script: Path, config: dict[str, Any]) -> None:
        calls.append((script, config))
        _write_fake_artifact(
            Path(config["out"]),
            {
                "candidate": {
                    "id": record.id,
                    "key": record.key,
                    "name": record.name,
                    "profileUrl": record.profile_url,
                    "salesNavProfileUrl": record.sales_nav_profile_url,
                    "source": record.source,
                },
                "dryRun": config["dryRun"],
                "url": record.profile_url,
                "messageLength": len(record.draft),
                "status": "dry-run-messageable",
            },
        )

    client._run_script = fake_run_script  # type: ignore[method-assign, assignment]

    with pytest.raises(RuntimeError, match="real send requires allow_send"):
        client.send_acceptance_followup(
            record,
            dry_run=False,
            preview_fill=False,
            allow_send=False,
        )
    with pytest.raises(RuntimeError, match="preview_fill requires dry_run"):
        client.send_acceptance_followup(
            record,
            dry_run=False,
            preview_fill=True,
            allow_send=True,
        )

    result, path = client.send_acceptance_followup(
        record,
        dry_run=True,
        preview_fill=False,
        allow_send=False,
    )

    assert result.status == "dry-run-messageable"
    assert Path(path).exists()
    assert calls[0][0].name == "acceptance_followup_send.js"
    assert calls[0][1]["record"]["id"] == "lead-1"
    assert calls[0][1]["dryRun"] is True
    assert calls[0][1]["allowSend"] is False


def test_cli_acceptance_followup_dry_run_uses_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    acceptance_import(store, FIXTURES / "acceptance_outcomes.json")
    acceptance_draft_followups(
        store,
        research=FIXTURES / "accepted_research.json",
        out=tmp_path / "followups.md",
        include_drafted=False,
        strategy=DraftStrategy.ASAP_CONTRACT_V1,
    )
    record = store.load_acceptance_followup_ledger().drafts[0]
    out_dir = tmp_path / "followup-browser"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "send-followup",
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
        "followup:Duplicate Lead:dry=True:preview=False:allow=False"
    ]
    assert store.load_acceptance_followup_ledger().drafts[0].status.value == "dry_run_ready"


def test_cli_acceptance_check_uses_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
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
    event = json.loads(store.acceptance_event_path.read_text().strip().splitlines()[-1])
    assert event["kind"] == "check"


def test_cli_acceptance_draft_followups_can_generate_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    acceptance_import(store, FIXTURES / "acceptance_outcomes.json")
    out_dir = tmp_path / "generated-research"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "draft-followups",
            "--session",
            "auto",
            "--out-dir",
            str(out_dir),
            "--research-offset",
            "0",
            "--research-limit",
            "1",
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "accepted-research:1:offset=0:limit=1:delay=500"
    ]
    assert (out_dir / "accepted-candidates.json").exists()
    assert (out_dir / "accepted-research.json").exists()
    assert store.load_acceptance_followup_ledger().drafts[0].name == "Duplicate Lead"


def test_cli_acceptance_run_daily_session_reuses_one_live_browser_and_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    candidates = tmp_path / "acceptance-candidates.json"
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"
    draft_report = tmp_path / "followups.md"
    draft_out_dir = tmp_path / "accepted-followups"
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
            "--draft-report",
            str(draft_report),
            "--draft-out-dir",
            str(draft_out_dir),
            "--out-dir",
            str(browser_out_dir),
        ]
    )

    assert exit_code == 0
    assert len(FakeLiveBrowserClient.instances) == 1
    assert FakeLiveBrowserClient.instances[0].out_dir == browser_out_dir
    assert FakeLiveBrowserClient.instances[0].calls == [
        "acceptance-check:1:offset=0:limit=1:delay=750",
        "accepted-research:1:offset=0:limit=0:delay=500",
    ]
    assert json.loads(outcomes.read_text())["rows"][0]["status"] == "accepted"
    assert draft_report.exists()
    assert draft_report.with_suffix(".review.json").exists()
    assert (draft_out_dir / "accepted-candidates.json").exists()
    assert (draft_out_dir / "accepted-research.json").exists()
    assert store.load_acceptance_followup_ledger().drafts[0].name == "Duplicate Lead"
    events = [
        json.loads(line)
        for line in store.acceptance_event_path.read_text().splitlines()
        if line.strip()
    ]
    assert any(
        event["kind"] == "run-daily-session-check-start"
        and event["payload"]["offset"] == 0
        and event["payload"]["limit"] == 1
        and event["payload"]["out"] == str(chunks / "chunk-0.json")
        for event in events
    )


def test_cli_acceptance_run_daily_session_reuses_complete_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    for name in ("Already Checked", "Needs Check"):
        ledger.upsert_invitation(
            _run_id(),
            date(2026, 6, 24),
            CandidateEvent(
                at=datetime.now(UTC) - timedelta(days=8),
                source="ASAP - Agency Owners Delivery",
                name=name,
                profile_url=f"https://www.linkedin.com/sales/lead/{name.replace(' ', '-').lower()}",
                status=CandidateStatus.PENDING,
            ),
        )
    store.save_acceptance_ledger(ledger)
    candidates = tmp_path / "acceptance-candidates.json"
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"
    _write_fake_artifact(
        chunks / "chunk-0.json",
        AcceptanceOutcomeArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "input": str(candidates),
                "count": 1,
                "offset": 0,
                "limit": 1,
                "totalCandidates": 2,
                "complete": True,
                "rows": [
                    {
                        "source": "ASAP - Agency Owners Delivery",
                        "name": "Already Checked",
                        "profileUrl": "https://www.linkedin.com/sales/lead/already-checked",
                        "status": "accepted",
                        "checkedAt": "2026-06-24T12:00:00Z",
                        "relationship": "1st",
                        "evidence": "existing complete chunk",
                        "note": "fixture",
                    }
                ],
            }
        ),
    )

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
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[0].calls == [
        "acceptance-check:2:offset=1:limit=1:delay=750",
        "accepted-research:1:offset=0:limit=0:delay=500",
    ]
    assert json.loads(outcomes.read_text())["count"] == 2
    events = [
        json.loads(line)
        for line in store.acceptance_event_path.read_text().splitlines()
        if line.strip()
    ]
    assert any(
        event["kind"] == "run-daily-session-check-reuse"
        and event["payload"]["offset"] == 0
        for event in events
    )


def test_cli_acceptance_run_daily_session_retries_chunk_three_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_failures_remaining = 3
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Retry Lead",
            profile_url="https://www.linkedin.com/sales/lead/retry?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"

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
            str(chunks),
            "--chunk-size",
            "1",
            "--no-draft-followups",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "retrying acceptance chunk:" in output
    assert "attempt 4/4" in output
    assert outcomes.exists()
    assert FakeLiveBrowserClient.instances[0].calls == [
        "acceptance-check:1:offset=0:limit=1:delay=750",
        "acceptance-check:1:offset=0:limit=1:delay=750",
        "acceptance-check:1:offset=0:limit=1:delay=750",
        "acceptance-check:1:offset=0:limit=1:delay=750",
    ]
    assert FakeLiveBrowserClient.instances[0].recoveries == 3


def test_cli_acceptance_run_daily_session_stops_on_blocked_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.acceptance_status = "blocked"
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Blocked Lead",
            profile_url="https://www.linkedin.com/sales/lead/blocked?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"

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
            str(chunks),
            "--chunk-size",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "stopped:" in output
    assert "chunk-0.json has 1 blocked rows" in output
    assert not outcomes.exists()
    assert store.load_acceptance_followup_ledger().drafts == []


def test_cli_acceptance_run_daily_session_reports_chunk_check_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)
    FakeLiveBrowserClient.fail_acceptance_check = True
    store = Store(tmp_path)
    ledger = AcceptanceLedger()
    ledger.upsert_invitation(
        _run_id(),
        date(2026, 6, 24),
        CandidateEvent(
            at=datetime.now(UTC) - timedelta(days=8),
            source="ASAP - Agency Owners Delivery",
            name="Timeout Lead",
            profile_url="https://www.linkedin.com/sales/lead/timeout?_ntb=session",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_acceptance_ledger(ledger)
    outcomes = tmp_path / "acceptance-outcomes.json"
    chunks = tmp_path / "chunks"

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
            str(chunks),
            "--chunk-size",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "stopped:" in output
    assert "chunk-0.json failed during acceptance check" in output
    assert "after 4 attempt(s) (3 retries)" in output
    assert "offset=0, limit=1, candidates=1" in output
    assert "browser timed out" in output
    assert not outcomes.exists()


def test_cli_acceptance_run_daily_session_skips_browser_without_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fake_live_browser(monkeypatch)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "acceptance",
            "run-daily-session",
            "--candidates-out",
            str(tmp_path / "acceptance-candidates.json"),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances == []
    assert "no acceptance-check candidates; browser not opened" in capsys.readouterr().out
