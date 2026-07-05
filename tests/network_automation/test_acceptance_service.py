from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from apps.network_automation.acceptance_service import (
    acceptance_apply_research_decisions,
    acceptance_collect_codex_draft_workers,
    acceptance_collect_codex_research_workers,
    acceptance_draft_followups,
    acceptance_draft_reviewed_followups,
    acceptance_export_message_queue,
    acceptance_export_research_queue,
    acceptance_finalize_codex_draft_workers,
    acceptance_import,
    acceptance_launch_codex_draft_workers,
    acceptance_launch_codex_research_workers,
    acceptance_retry_send_followup,
    acceptance_send_followup,
    acceptance_send_ready_followups,
)
from apps.network_automation.browser import BrowserClient, FixtureBrowserClient
from apps.network_automation.models import (
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceLedger,
    AcceptanceStatus,
    AcceptedFollowupTemplateKey,
    CandidateEvent,
    CandidateObservation,
    CandidateStatus,
    DraftStrategy,
)
from apps.network_automation.store import Store

from .helpers import (
    FIXTURES,
    SequenceFollowupBrowser,
    _run_id,
)


def _store_with_accepted_duplicate_lead(tmp_path: Path) -> Store:
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
    return store


def _export_duplicate_message_queue(tmp_path: Path) -> tuple[Store, Path, dict[str, Any]]:
    store = _store_with_accepted_duplicate_lead(tmp_path)
    queue_path = tmp_path / "research-queue.json"
    acceptance_export_research_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=1,
        include_drafted=False,
    )
    packet = json.loads(queue_path.read_text())
    packet["items"][0]["decision"] = {
        "status": "research_ready",
        "confidence": "high",
        "person_summary": "Duplicate Lead runs product work at Acme AI.",
        "company_name": "Acme AI",
        "company_summary": "Acme AI builds workflow automation products for service teams.",
        "official_company_url": "https://www.acme-ai.example",
        "evidence_urls": [
            "https://www.linkedin.com/in/duplicate-lead",
            "https://www.acme-ai.example",
        ],
        "notes": "Identity and company match reviewed evidence.",
        "warnings": [],
    }
    decisions_path = tmp_path / "research-decisions.json"
    decisions_path.write_text(json.dumps(packet), encoding="utf-8")
    reviewed_path = tmp_path / "reviewed-research.json"
    acceptance_apply_research_decisions(
        store,
        input_path=decisions_path,
        out=reviewed_path,
    )
    message_queue_path = tmp_path / "message-queue.json"
    acceptance_export_message_queue(
        store,
        reviewed_research=reviewed_path,
        out=message_queue_path,
        markdown_out=None,
        include_drafted=False,
        offset=0,
        limit=10,
    )
    message_packet = json.loads(message_queue_path.read_text())
    return store, message_queue_path, message_packet


def test_acceptance_import_downgrades_mismatched_identity(tmp_path: Path) -> None:
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

    acceptance_import(store, FIXTURES / "acceptance_outcomes_mismatch.json")

    updated = store.load_acceptance_ledger()
    assert updated.invitations[0].latest_status == AcceptanceStatus.UNKNOWN
    assert "downgraded to unknown" in (updated.invitations[0].history[0].note or "")


def test_acceptance_drafts_and_followup_send_guards(tmp_path: Path) -> None:
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
    lead_ledger = store.load_lead_ledger()
    lead_record = lead_ledger.upsert_observation(
        CandidateObservation(
            source="ASAP - Agency Owners Delivery",
            index=1,
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup",
            menu_state="connectable",
        )
    )
    lead_ledger.approve(lead_record.lead_key, "agency owner with delivery work")
    store.save_lead_ledger(lead_ledger)
    report_path = tmp_path / "followups.md"

    output = acceptance_draft_followups(
        store,
        research=FIXTURES / "accepted_research.json",
        out=report_path,
        include_drafted=False,
        strategy=DraftStrategy.ASAP_CONTRACT_V1,
    )

    assert "accepted follow-up drafts: 1" in output
    rendered = report_path.read_text()
    assert "Hey, Duplicate. Thanks for connecting." in rendered
    assert "works across web and mobile products" in rendered
    assert "project overflow, prototypes, and AI-enabled product builds" in rendered
    assert "Are you the right person to ask about this kind of project support?" in rendered
    assert "HC Studio LLC" not in rendered
    assert "- Template: `agency`" in rendered
    assert "Person does: Duplicate Lead is listed as AI Product Leader at Acme AI." in rendered
    assert (
        "Company does: Acme AI: Acme AI builds workflow automation products for service teams."
        in rendered
    )
    assert "Why this draft fits:" in rendered
    assert "Company website: https://www.acme-ai.example" in rendered
    assert "Original connection source: ASAP - Agency Owners Delivery" in rendered
    assert "Original connection approval: agency owner with delivery work" in rendered
    review_packet = json.loads(report_path.with_suffix(".review.json").read_text())
    assert review_packet["items"][0]["person_does"] == (
        "Duplicate Lead is listed as AI Product Leader at Acme AI."
    )
    assert review_packet["items"][0]["company_website_url"] == "https://www.acme-ai.example"
    assert review_packet["items"][0]["research"]["company_profile"]["name"] == "Acme AI"
    followups = store.load_acceptance_followup_ledger()
    record = followups.drafts[0]
    assert record.template_key == AcceptedFollowupTemplateKey.AGENCY
    assert record.person_does == "Duplicate Lead is listed as AI Product Leader at Acme AI."
    assert record.company_website_url == "https://www.acme-ai.example"
    assert record.profile_url == "https://www.linkedin.com/in/duplicate-lead"
    assert record.sales_nav_profile_url == "https://www.linkedin.com/sales/lead/dup"
    with pytest.raises(ValueError, match="real sends require dry_run_ready"):
        acceptance_send_followup(
            store,
            FixtureBrowserClient(followup_result=FIXTURES / "followup_sent.json"),
            record_id=record.id,
            dry_run=False,
            preview_fill=False,
            allow_send=True,
        )

    preview = acceptance_send_followup(
        store,
        FixtureBrowserClient(followup_result=FIXTURES / "followup_preview.json"),
        record_id=record.id,
        dry_run=True,
        preview_fill=True,
        allow_send=False,
    )

    assert "status=preview-filled" in preview
    assert store.load_acceptance_followup_ledger().drafts[0].status.value == "dry_run_ready"


def test_acceptance_export_research_queue_writes_agent_packet(tmp_path: Path) -> None:
    store = _store_with_accepted_duplicate_lead(tmp_path)
    queue_path = tmp_path / "research-queue.json"

    output = acceptance_export_research_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=1,
        include_drafted=False,
    )

    assert "exported 1 accepted research queue item" in output
    packet = json.loads(queue_path.read_text())
    item = packet["items"][0]
    assert item["candidate"]["name"] == "Duplicate Lead"
    assert item["decision"]["status"] == "needs_review"
    assert item["decision"]["person_summary"] is None
    assert item["decision"]["proposed_message"] is None
    markdown = queue_path.with_suffix(".md").read_text()
    assert "Decision fields to fill" in markdown
    assert "`proposed_message`" in markdown
    assert "Drafting prompt for `proposed_message`" in markdown
    assert "Core prompt for Codex drafting" in markdown
    assert "start with what Hanif does" in markdown
    assert "Does that kind of cleanup come up much" in markdown
    assert "Do not ask to send a resume" in markdown
    assert "Acceptance note: lead page shows 1st-degree relationship" in markdown


def test_acceptance_reviewed_research_decisions_draft_followups(tmp_path: Path) -> None:
    store = _store_with_accepted_duplicate_lead(tmp_path)
    queue_path = tmp_path / "research-queue.json"
    acceptance_export_research_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=1,
        include_drafted=False,
    )
    packet = json.loads(queue_path.read_text())
    packet["items"][0]["decision"] = {
        "status": "ready_for_draft",
        "confidence": "high",
        "person_summary": "Duplicate Lead runs product work at Acme AI.",
        "company_name": "Acme AI",
        "company_summary": "Acme AI builds workflow automation products for service teams.",
        "official_company_url": "https://www.acme-ai.example",
        "evidence_urls": [
            "https://www.linkedin.com/in/duplicate-lead",
            "https://www.acme-ai.example",
        ],
        "notes": "Identity and company match reviewed evidence.",
        "warnings": [],
        "proposed_message": (
            "Hey, Duplicate. Thanks for connecting.\n\n"
            "I build web apps and internal tools for teams that are stuck with "
            "spreadsheets, manual handoffs, or half-working automations.\n\n"
            "Acme AI's work on workflow automation for service teams seems adjacent "
            "to what I do: turning messy workflows into tools people can actually use."
            "\n\n"
            "Does that kind of cleanup come up much with the teams you work with?"
        ),
    }
    decisions_path = tmp_path / "research-decisions.json"
    decisions_path.write_text(json.dumps(packet), encoding="utf-8")
    reviewed_path = tmp_path / "reviewed-research.json"

    applied = acceptance_apply_research_decisions(
        store,
        input_path=decisions_path,
        out=reviewed_path,
    )
    report_path = tmp_path / "reviewed-followups.md"
    drafted = acceptance_draft_reviewed_followups(
        store,
        reviewed_research=reviewed_path,
        out=report_path,
        include_drafted=False,
        strategy=DraftStrategy.ASAP_CONTRACT_V1,
        review_out=None,
    )

    assert "1 ready" in applied
    assert "drafts from reviewed research: 1" in drafted
    rendered = report_path.read_text()
    assert "Duplicate Lead runs product work at Acme AI." in rendered
    assert "Acme AI builds workflow automation products" in rendered
    assert "Official company URL: https://www.acme-ai.example" in rendered
    assert "turning messy workflows into tools people can actually use" in rendered
    review_packet = json.loads(report_path.with_suffix(".review.json").read_text())
    assert review_packet["items"][0]["reviewed_research"]["confidence"] == "high"
    assert (
        review_packet["items"][0]["draft"]
        == packet["items"][0]["decision"]["proposed_message"]
    )
    record = store.load_acceptance_followup_ledger().drafts[0]
    assert record.company_website_url == "https://www.acme-ai.example"
    assert record.research_path == str(reviewed_path)
    assert record.draft == packet["items"][0]["decision"]["proposed_message"]


def test_acceptance_research_ready_can_export_message_queue(tmp_path: Path) -> None:
    _, message_queue_path, message_packet = _export_duplicate_message_queue(tmp_path)

    item = message_packet["items"][0]
    assert item["decision"]["status"] == "ready_for_draft"
    assert item["decision"]["proposed_message"] is None
    assert item["decision"]["person_summary"] == "Duplicate Lead runs product work at Acme AI."
    markdown = message_queue_path.with_suffix(".md").read_text()
    assert "Accepted Follow-Up Message Queue" in markdown
    assert "Message fields to fill" in markdown
    assert "start with what Hanif does" in markdown
    assert "Does that kind of cleanup come up much" in markdown


def test_acceptance_launch_codex_research_workers_writes_source_bundle_and_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, message_queue_path, message_packet = _export_duplicate_message_queue(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []
    fetched: list[str] = []

    def fake_fetch(url: str, *, timeout_seconds: float) -> dict[str, object]:
        fetched.append(url)
        return {
            "ok": True,
            "status_code": 200,
            "url": url,
            "final_url": url,
            "content_type": "text/html",
            "html": (
                "<html><body>Duplicate Lead helps teams automate service workflows.</body></html>"
            ),
            "text": "Duplicate Lead helps teams automate service workflows.",
            "warning": None,
        }

    class FakeProcess:
        pid = 5151

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        "apps.network_automation.acceptance_service.fetch_source_material", fake_fetch
    )
    monkeypatch.setattr(
        "apps.network_automation.acceptance_service.subprocess.Popen", fake_popen
    )
    jobs_dir = tmp_path / "research-jobs"
    sources_dir = tmp_path / "source-bundles"

    output = acceptance_launch_codex_research_workers(
        store,
        research_queue=message_queue_path,
        jobs_dir=jobs_dir,
        sources_dir=sources_dir,
        codex_bin="/usr/local/bin/codex",
        cwd=Path("/Users/hanifcarroll/projects/linkedin-tools"),
        model="gpt-5.5",
        reasoning_effort="xhigh",
        offset=0,
        limit=1,
        force=False,
        fetch_timeout_seconds=3.0,
    )

    item = message_packet["items"][0]
    job_dir = jobs_dir / item["followup_id"]
    source_manifest = sources_dir / item["followup_id"] / "sources.json"
    prompt = (job_dir / "packet.md").read_text()
    assert "launched 1 Codex research worker" in output
    assert "Read the local source files listed in the source bundle" in prompt
    assert "`research_ready` requires `confidence` = `high`" in prompt
    assert str(source_manifest) in prompt
    assert "Return `candidate_key` exactly as:" in prompt
    assert json.loads(source_manifest.read_text())["sources"][0]["text_path"]
    assert fetched == [
        "https://www.linkedin.com/in/duplicate-lead",
        "https://www.acme-ai.example",
    ]
    context = json.loads((job_dir / "context.json").read_text())
    assert context["sources_path"] == str(source_manifest)
    schema = json.loads((job_dir / "schema.json").read_text())
    assert schema["properties"]["research_evidence"]["items"]["required"] == [
        "evidence_id",
        "source_url",
        "claim",
        "relevance",
        "source_excerpt",
    ]
    job = json.loads((job_dir / "job.json").read_text())
    assert job["pid"] == 5151
    assert job["context_path"] == str(job_dir / "context.json")
    assert job["sources_path"] == str(source_manifest)
    command, kwargs = calls[0]
    assert command[:2] == ["/usr/local/bin/codex", "exec"]
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("-m") + 1] == "gpt-5.5"
    assert kwargs["start_new_session"] is True


def test_acceptance_collect_codex_research_workers_writes_research_decisions(
    tmp_path: Path,
) -> None:
    store, message_queue_path, message_packet = _export_duplicate_message_queue(tmp_path)
    item = message_packet["items"][0]
    job_dir = tmp_path / "research-jobs" / item["followup_id"]
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(
        json.dumps(
            {
                "candidate_key": item["candidate_key"],
                "status": "research_ready",
                "confidence": "high",
                "person_summary": "Duplicate Lead runs product work at Acme AI.",
                "company_name": "Acme AI",
                "company_summary": (
                    "Acme AI builds workflow automation products for service teams."
                ),
                "official_company_url": "https://www.acme-ai.example",
                "evidence_urls": ["https://www.acme-ai.example"],
                "research_evidence": [
                    {
                        "evidence_id": "E1",
                        "source_url": "https://www.acme-ai.example",
                        "claim": "Acme AI builds workflow automation products.",
                        "relevance": "Direct bridge to internal tooling cleanup.",
                        "source_excerpt": (
                            "Workflow automation software for service operations."
                        ),
                    }
                ],
                "notes": "The source bundle supports a workflow automation bridge.",
                "warnings": [],
                "template_key": "agency",
                "angle": "workflow automation bridge",
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "collected-research-decisions.json"

    output = acceptance_collect_codex_research_workers(
        store,
        research_queue=message_queue_path,
        jobs_dir=tmp_path / "research-jobs",
        out=out,
        offset=0,
        limit=1,
    )

    assert "1 research ready" in output
    decision = json.loads(out.read_text())["decisions"][0]
    assert decision["status"] == "research_ready"
    assert decision["research_evidence"][0]["evidence_id"] == "E1"
    assert decision["template_key"] == "agency"


def test_acceptance_collect_codex_research_workers_blocks_when_all_pending(
    tmp_path: Path,
) -> None:
    store, message_queue_path, _ = _export_duplicate_message_queue(tmp_path)
    out = tmp_path / "pending-research-decisions.json"

    with pytest.raises(RuntimeError, match="no completed Codex research worker"):
        acceptance_collect_codex_research_workers(
            store,
            research_queue=message_queue_path,
            jobs_dir=tmp_path / "research-jobs",
            out=out,
            offset=0,
            limit=1,
        )

    assert not out.exists()


def test_acceptance_launch_codex_draft_workers_writes_minimal_packet_and_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, message_queue_path, message_packet = _export_duplicate_message_queue(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        pid = 4242

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        "apps.network_automation.acceptance_service.subprocess.Popen", fake_popen
    )
    jobs_dir = tmp_path / "draft-jobs"

    output = acceptance_launch_codex_draft_workers(
        store,
        message_queue=message_queue_path,
        jobs_dir=jobs_dir,
        codex_bin="/usr/local/bin/codex",
        cwd=Path("/Users/hanifcarroll/projects/linkedin-tools"),
        model="gpt-5.5",
        reasoning_effort="xhigh",
        offset=0,
        limit=0,
        force=False,
    )

    item = message_packet["items"][0]
    job_dir = jobs_dir / item["followup_id"]
    prompt = (job_dir / "packet.md").read_text()
    assert "launched 1 Codex draft worker" in output
    assert "Use only the context below" in prompt
    assert "Context about the person:" in prompt
    assert "Duplicate Lead runs product work at Acme AI." in prompt
    assert "Context about the company:" in prompt
    assert "Acme AI builds workflow automation products for service teams." in prompt
    assert "Return `candidate_key` exactly as:" in prompt
    assert "LinkedIn profile:" not in prompt
    assert "Do not:" not in prompt
    assert "force a message if the connection is weak" not in prompt
    schema = json.loads((job_dir / "schema.json").read_text())
    assert schema["properties"]["status"]["enum"] == ["ready_for_draft", "needs_review"]
    job = json.loads((job_dir / "job.json").read_text())
    assert job["pid"] == 4242
    command, kwargs = calls[0]
    assert command[:2] == ["/usr/local/bin/codex", "exec"]
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("-m") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="xhigh"' in command
    assert command[-1] == "-"
    assert kwargs["start_new_session"] is True


def test_acceptance_collect_codex_draft_workers_writes_message_decisions(
    tmp_path: Path,
) -> None:
    store, message_queue_path, message_packet = _export_duplicate_message_queue(tmp_path)
    item = message_packet["items"][0]
    job_dir = tmp_path / "draft-jobs" / item["followup_id"]
    job_dir.mkdir(parents=True)
    message = (
        "Hey, Duplicate. Thanks for connecting.\n\n"
        "I build web apps and internal tools for teams that are stuck with "
        "spreadsheets, manual handoffs, or half-working automations.\n\n"
        "Acme AI's work on workflow automation for service teams seems adjacent "
        "to what I do: turning messy workflows into tools people can actually use."
        "\n\n"
        "Does that kind of cleanup come up much with the teams you work with?"
    )
    (job_dir / "result.json").write_text(
        json.dumps(
            {
                "candidate_key": item["candidate_key"],
                "status": "ready_for_draft",
                "message": message,
                "reason": "The company research ties directly to workflow automation.",
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "message-decisions.json"

    output = acceptance_collect_codex_draft_workers(
        store,
        message_queue=message_queue_path,
        jobs_dir=tmp_path / "draft-jobs",
        out=out,
        offset=0,
        limit=0,
    )

    assert "1 ready" in output
    decisions = json.loads(out.read_text())["decisions"]
    assert decisions[0]["status"] == "ready_for_draft"
    assert decisions[0]["proposed_message"] == message
    assert "Draft worker:" in decisions[0]["notes"]


def test_acceptance_collect_codex_draft_workers_blocks_when_all_pending(
    tmp_path: Path,
) -> None:
    store, message_queue_path, _ = _export_duplicate_message_queue(tmp_path)
    out = tmp_path / "message-decisions.json"

    with pytest.raises(RuntimeError, match="no completed Codex draft worker"):
        acceptance_collect_codex_draft_workers(
            store,
            message_queue=message_queue_path,
            jobs_dir=tmp_path / "draft-jobs",
            out=out,
            offset=0,
            limit=0,
        )

    assert not out.exists()


def test_acceptance_finalize_codex_draft_workers_waits_then_writes_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, message_queue_path, message_packet = _export_duplicate_message_queue(tmp_path)
    item = message_packet["items"][0]
    jobs_dir = tmp_path / "draft-jobs"
    message = (
        "Hey, Duplicate. Thanks for connecting.\n\n"
        "I build web apps and internal tools for teams that are stuck with "
        "spreadsheets, manual handoffs, or half-working automations.\n\n"
        "Acme AI's work on workflow automation for service teams seems adjacent "
        "to what I do: turning messy workflows into tools people can actually use."
        "\n\n"
        "Does that kind of cleanup come up much with the teams you work with?"
    )
    calls: list[list[str]] = []
    slept = False

    class FakeProcess:
        pid = 6262

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append(command)
        return FakeProcess()

    def fake_sleep(_seconds: float) -> None:
        nonlocal slept
        slept = True
        job_dir = jobs_dir / item["followup_id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "result.json").write_text(
            json.dumps(
                {
                    "candidate_key": item["candidate_key"],
                    "status": "ready_for_draft",
                    "message": message,
                    "reason": "The reviewed company research supports the bridge.",
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "apps.network_automation.acceptance_service.subprocess.Popen", fake_popen
    )
    monkeypatch.setattr("apps.network_automation.acceptance_service.time.sleep", fake_sleep)

    output = acceptance_finalize_codex_draft_workers(
        store,
        message_queue=message_queue_path,
        jobs_dir=jobs_dir,
        message_decisions_out=tmp_path / "message-decisions.json",
        reviewed_research_out=tmp_path / "reviewed-research.json",
        draft_out=tmp_path / "followups.md",
        review_out=tmp_path / "followups.review.json",
        codex_bin="/usr/local/bin/codex",
        cwd=Path("/Users/hanifcarroll/projects/linkedin-tools"),
        model="gpt-5.5",
        reasoning_effort="xhigh",
        offset=0,
        limit=0,
        force=False,
        wait_seconds=5.0,
        poll_seconds=0.1,
        include_drafted=False,
        strategy=DraftStrategy.ASAP_CONTRACT_V1,
    )

    assert slept is True
    assert calls[0][:2] == ["/usr/local/bin/codex", "exec"]
    assert "finalized accepted message queue" in output
    assert "1 ready, 0 needs review, 0 pending" in output
    assert "stopped before dry-run/send for review" in output
    decisions = json.loads((tmp_path / "message-decisions.json").read_text())
    assert decisions["decisions"][0]["proposed_message"] == message
    reviewed = json.loads((tmp_path / "reviewed-research.json").read_text())
    assert reviewed["decisions"][0]["status"] == "ready_for_draft"
    assert "turning messy workflows into tools people can actually use" in (
        tmp_path / "followups.md"
    ).read_text()
    review_packet = json.loads((tmp_path / "followups.review.json").read_text())
    assert review_packet["items"][0]["draft"] == message


def test_acceptance_finalize_codex_draft_workers_reports_pending_after_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, message_queue_path, _ = _export_duplicate_message_queue(tmp_path)
    calls: list[list[str]] = []

    class FakeProcess:
        pid = 7373

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append(command)
        return FakeProcess()

    monkeypatch.setattr(
        "apps.network_automation.acceptance_service.subprocess.Popen", fake_popen
    )

    with pytest.raises(RuntimeError, match=r"after waiting 0 second\(s\); 1 pending"):
        acceptance_finalize_codex_draft_workers(
            store,
            message_queue=message_queue_path,
            jobs_dir=tmp_path / "draft-jobs",
            message_decisions_out=tmp_path / "message-decisions.json",
            reviewed_research_out=tmp_path / "reviewed-research.json",
            draft_out=tmp_path / "followups.md",
            review_out=tmp_path / "followups.review.json",
            codex_bin="/usr/local/bin/codex",
            cwd=Path("/Users/hanifcarroll/projects/linkedin-tools"),
            model="gpt-5.5",
            reasoning_effort="xhigh",
            offset=0,
            limit=0,
            force=False,
            wait_seconds=0,
            poll_seconds=0.1,
            include_drafted=False,
            strategy=DraftStrategy.ASAP_CONTRACT_V1,
        )

    assert calls[0][:2] == ["/usr/local/bin/codex", "exec"]
    assert not (tmp_path / "message-decisions.json").exists()
    assert not (tmp_path / "followups.md").exists()


def test_acceptance_apply_research_decisions_rejects_weak_ready_decision(
    tmp_path: Path,
) -> None:
    store = _store_with_accepted_duplicate_lead(tmp_path)
    queue_path = tmp_path / "research-queue.json"
    acceptance_export_research_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=1,
        include_drafted=False,
    )
    packet = json.loads(queue_path.read_text())
    packet["items"][0]["decision"] = {
        "status": "ready_for_draft",
        "confidence": "low",
        "person_summary": "Duplicate Lead runs product work at Acme AI.",
        "company_name": "Acme AI",
        "company_summary": "Acme AI builds workflow automation products for service teams.",
        "official_company_url": "https://www.acme-ai.example",
        "evidence_urls": ["https://www.acme-ai.example"],
        "proposed_message": (
            "Hey, Duplicate. Thanks for connecting.\n\n"
            "I build web apps and internal tools for teams stuck with spreadsheets, "
            "manual handoffs, or half-working automations.\n\n"
            "Acme AI's workflow automation work seems adjacent to what I do: turning "
            "messy workflows into tools people can actually use.\n\n"
            "Does that kind of cleanup come up much with the teams you work with?"
        ),
    }
    decisions_path = tmp_path / "weak-decisions.json"
    decisions_path.write_text(json.dumps(packet), encoding="utf-8")

    with pytest.raises(ValueError, match="confidence=high"):
        acceptance_apply_research_decisions(
            store,
            input_path=decisions_path,
            out=tmp_path / "reviewed-research.json",
        )


def test_acceptance_apply_research_decisions_requires_ready_proposed_message(
    tmp_path: Path,
) -> None:
    store = _store_with_accepted_duplicate_lead(tmp_path)
    queue_path = tmp_path / "research-queue.json"
    acceptance_export_research_queue(
        store,
        out=queue_path,
        markdown_out=None,
        offset=0,
        limit=1,
        include_drafted=False,
    )
    packet = json.loads(queue_path.read_text())
    packet["items"][0]["decision"] = {
        "status": "ready_for_draft",
        "confidence": "high",
        "person_summary": "Duplicate Lead runs product work at Acme AI.",
        "company_name": "Acme AI",
        "company_summary": "Acme AI builds workflow automation products for service teams.",
        "official_company_url": "https://www.acme-ai.example",
        "evidence_urls": ["https://www.acme-ai.example"],
    }
    decisions_path = tmp_path / "missing-message-decisions.json"
    decisions_path.write_text(json.dumps(packet), encoding="utf-8")

    with pytest.raises(ValueError, match="missing proposed_message"):
        acceptance_apply_research_decisions(
            store,
            input_path=decisions_path,
            out=tmp_path / "reviewed-research.json",
        )


def _followup_record(
    record_id: str, name: str, status: AcceptanceFollowupStatus
) -> AcceptanceFollowupRecord:
    return AcceptanceFollowupRecord(
        key=f"source|{name}|https://www.linkedin.com/in/{record_id}",
        id=record_id,
        source="source",
        name=name,
        profile_url=f"https://www.linkedin.com/in/{record_id}",
        accepted_at=datetime(2026, 6, 20, tzinfo=UTC),
        angle="general",
        draft=f"Hey, {name}. Thanks for connecting.",
        status=status,
        report_path="followups.md",
    )


def test_acceptance_send_ready_followups_prints_summary_table(tmp_path: Path) -> None:
    store = Store(tmp_path)
    records = [
        _followup_record("afu_one", "Ready One", AcceptanceFollowupStatus.DRY_RUN_READY),
        _followup_record("afu_two", "Ready Two", AcceptanceFollowupStatus.DRY_RUN_READY),
    ]
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=records))
    browser = SequenceFollowupBrowser(
        tmp_path,
        [FIXTURES / "followup_sent.json", FIXTURES / "followup_sent.json"],
    )

    output = acceptance_send_ready_followups(
        store, cast(BrowserClient, browser), limit=2, allow_send=True
    )

    assert "Accepted follow-up send summary" in output
    assert "Ready One" in output
    assert "Ready Two" in output
    assert "sent" in output
    assert [call["allow_send"] for call in browser.calls] == [True, True]
    assert [record.status for record in store.load_acceptance_followup_ledger().drafts] == [
        AcceptanceFollowupStatus.SENT,
        AcceptanceFollowupStatus.SENT,
    ]


def test_acceptance_retry_send_followup_dry_runs_then_sends(tmp_path: Path) -> None:
    store = Store(tmp_path)
    record = _followup_record("afu_retry", "Retry Lead", AcceptanceFollowupStatus.SEND_FAILED)
    store.save_acceptance_followup_ledger(AcceptanceFollowupLedger(drafts=[record]))
    browser = SequenceFollowupBrowser(
        tmp_path,
        [FIXTURES / "followup_preview.json", FIXTURES / "followup_sent.json"],
    )

    output = acceptance_retry_send_followup(
        store,
        cast(BrowserClient, browser),
        record_id="afu_retry",
        allow_send=True,
    )

    assert "status=preview-filled dry_run=True" in output
    assert "status=sent-clicked dry_run=False" in output
    assert "Accepted follow-up send summary" in output
    assert "Retry Lead" in output
    calls = browser.calls
    assert [(call["dry_run"], call["allow_send"]) for call in calls] == [
        (True, False),
        (False, True),
    ]
    assert store.load_acceptance_followup_ledger().drafts[0].status == (
        AcceptanceFollowupStatus.SENT
    )


def test_acceptance_draft_followups_explains_zero_new_drafts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    report_path = tmp_path / "followups.md"

    output = acceptance_draft_followups(
        store,
        research=None,
        out=report_path,
        include_drafted=False,
        strategy=DraftStrategy.ASAP_CONTRACT_V1,
    )

    assert "accepted follow-up drafts: 0" in output
    assert "no newly accepted connections need first-message drafts" in output
    assert "No newly accepted connections need first-message drafts." in report_path.read_text()
