from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

import apps.network_automation.cli as network_cli
import apps.network_automation.service as network_service
from apps.network_automation.browser import (
    SECURITY_VERIFICATION_SELECTOR,
    FixtureBrowserClient,
    PlaywrightBrowserClient,
    PlaywriterBrowserClient,
    _apply_salesnav_api_state,
    _capture_salesnav_api_response,
    _classify_menu_labels,
    _classify_page,
)
from apps.network_automation.cli import main as network_main
from apps.network_automation.models import (
    AcceptanceCheckCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceLedger,
    AcceptanceOutcomeArtifact,
    AcceptanceStatus,
    AcceptedDraftCandidate,
    AcceptedFollowupTemplateKey,
    AcceptedResearchArtifact,
    BrowserSessionState,
    CandidateEvent,
    CandidateObservation,
    CandidateStatus,
    DraftStrategy,
    PendingCandidateObservation,
    PendingCapture,
    PendingWithdrawResult,
    RunState,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendResult,
    SavedSearchArtifact,
    choose_angle,
    default_sources,
    general_accepted_followup_draft,
    source_yield_report,
)
from apps.network_automation.old_state import inspect_old_state
from apps.network_automation.reports import render_report
from apps.network_automation.service import (
    acceptance_draft_followups,
    acceptance_import,
    acceptance_send_followup,
    finish_run,
    import_audit,
    import_capture_path,
    pending_cleanup_finish,
    pending_cleanup_import_audit,
    pending_cleanup_import_capture,
    pending_cleanup_record_withdraw_result,
    pending_cleanup_start,
    pending_cleanup_withdraw_next,
    record_audit,
    record_candidate,
    send_guarded,
    send_next,
    start_run,
)
from apps.network_automation.store import Store, read_model
from packages.linkedin_browser import BrowserBlockKind, ManagedChromeSession

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "network_automation"


def test_cli_help_documents_browser_backend_selection() -> None:
    help_text = network_cli.build_parser().format_help()

    assert "default: Playwriter" in help_text
    assert "LINKEDIN_TOOLS_PLAYWRITER_SESSION=<id>" in help_text
    assert "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY=<key>" in help_text
    assert "LINKEDIN_TOOLS_BROWSER_BACKEND=playwright" in help_text
    assert "Playwriter <method> is not ported yet" in help_text


class FakeLiveBrowserClient:
    instances: ClassVar[list[FakeLiveBrowserClient]] = []
    acceptance_status: ClassVar[str] = "accepted"

    def __init__(
        self,
        *,
        out_dir: Path,
        cdp_url: str | None = None,
        max_load_more: int = 260,
        withdraw_timeout_seconds: float = 90.0,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.cdp_url = cdp_url
        self.max_load_more = max_load_more
        self.withdraw_timeout_seconds = withdraw_timeout_seconds
        self.calls: list[str] = []
        FakeLiveBrowserClient.instances.append(self)

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        self.calls.append(f"send:{candidate.name}:dry={dry_run}:allow={allow_send}")
        return (
            read_model(FIXTURES / "send_pending.json", SalesNavSendResult),
            str(self.out_dir / "send-result.json"),
        )

    def capture_salesnav(
        self,
        *,
        source: str,
        url: str | None = None,
        pages: int = 1,
        limit: int = 25,
        stop_after_connectable: int = 0,
        only_connectable: bool = False,
        row_scroll_delay_ms: int = 250,
    ) -> tuple[SalesNavCapture, str]:
        self.calls.append(
            f"capture:{source}:pages={pages}:limit={limit}:only={only_connectable}:url={url}"
        )
        _ = stop_after_connectable, row_scroll_delay_ms
        return (
            read_model(FIXTURES / "capture.json", SalesNavCapture),
            str(self.out_dir / "capture-page.json"),
        )

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        self.calls.append(f"audit:load_more={load_more}")
        return (
            read_model(FIXTURES / "audit_101.json", SalesNavAudit),
            str(self.out_dir / "audit.json"),
        )

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        self.calls.append(f"saved-searches:{url}")
        artifact = SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "url": url,
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    }
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def check_acceptance_outcomes(
        self,
        *,
        candidates: list[AcceptanceCheckCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptanceOutcomeArtifact, str]:
        self.calls.append(
            f"acceptance-check:{len(candidates)}:offset={offset}:limit={limit}:delay={delay_ms}"
        )
        selected = candidates[offset : offset + limit] if limit else candidates[offset:]
        artifact = AcceptanceOutcomeArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "input": str(input_path),
                "count": len(selected),
                "offset": offset,
                "limit": limit,
                "totalCandidates": len(candidates),
                "complete": True,
                "rows": [
                    {
                        "source": candidate.source,
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "status": FakeLiveBrowserClient.acceptance_status,
                        "checkedAt": "2026-06-24T12:00:00Z",
                        "relationship": (
                            "1st"
                            if FakeLiveBrowserClient.acceptance_status == "accepted"
                            else None
                        ),
                        "evidence": candidate.name,
                        "note": (
                            "fixture"
                            if FakeLiveBrowserClient.acceptance_status == "accepted"
                            else "security-verification-present"
                        ),
                    }
                    for candidate in selected
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        public_web: bool = True,
        max_web_results: int = 5,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        self.calls.append(
            "accepted-research:"
            f"{len(candidates)}:offset={offset}:limit={limit}:web={public_web}:"
            f"max={max_web_results}:delay={delay_ms}"
        )
        selected = candidates[offset : offset + limit] if limit else candidates[offset:]
        artifact = AcceptedResearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "rows": [
                    {
                        "source": candidate.source,
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "salesNav": {
                            "name": candidate.name,
                            "title": "Founder",
                            "company": "Example Co",
                            "url": candidate.profile_url,
                        },
                        "web": {"query": candidate.name, "results": [], "warnings": []},
                    }
                    for candidate in selected
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        self.calls.append(f"pending-capture:load_more={load_more}:threshold={threshold_days}")
        artifact = PendingCapture.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "rows": [
                    {
                        "index": 0,
                        "name": "Stale Invite",
                        "profileUrl": "https://www.linkedin.com/in/stale",
                        "ageText": "Sent 3 weeks ago",
                        "ageDays": 21,
                        "eligible": True,
                        "rowText": "Stale Invite Sent 3 weeks ago Withdraw",
                    }
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        self.calls.append(
            f"followup:{record.name}:dry={dry_run}:preview={preview_fill}:allow={allow_send}"
        )
        return (
            read_model(FIXTURES / "followup_preview.json", AcceptanceFollowupSendResult),
            str(self.out_dir / f"{record.id}.json"),
        )

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        self.calls.append(f"withdraw:{candidate.name}:dry={dry_run}:allow={allow_withdraw}")
        return (
            read_model(FIXTURES / "withdraw_result.json", PendingWithdrawResult),
            str(self.out_dir / "withdraw-result.json"),
        )


class ZeroThenNextSourceBrowserClient(FakeLiveBrowserClient):
    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        self.calls.append(f"saved-searches:{url}")
        artifact = SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "url": url,
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    },
                    {
                        "savedSearchId": "def",
                        "name": "ASAP - Contract Recruiters Staffing",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=def",
                    },
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def capture_salesnav(
        self,
        *,
        source: str,
        url: str | None = None,
        pages: int = 1,
        limit: int = 25,
        stop_after_connectable: int = 0,
        only_connectable: bool = False,
        row_scroll_delay_ms: int = 250,
    ) -> tuple[SalesNavCapture, str]:
        self.calls.append(
            f"capture:{source}:pages={pages}:limit={limit}:only={only_connectable}:url={url}"
        )
        _ = stop_after_connectable, row_scroll_delay_ms
        if source == "ASAP - Agency Owners Delivery":
            artifact = SalesNavCapture.model_validate(
                {
                    "capturedAt": "2026-06-24T12:00:00Z",
                    "source": source,
                    "url": url or "",
                    "resumeUrl": url or "",
                    "rawRowCount": 5,
                    "outputRowCount": 0,
                    "stateCounts": {"unknown": 5},
                    "rows": [],
                }
            )
            return artifact, str(self.out_dir / f"{_safe_file_stem(source)}-empty.json")
        artifact = SalesNavCapture.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "source": source,
                "url": url or "",
                "resumeUrl": url or "",
                "rawRowCount": 1,
                "outputRowCount": 1,
                "stateCounts": {"connectable": 1},
                "rows": [
                    {
                        "index": 1,
                        "name": f"{source} Lead",
                        "profileUrl": f"https://www.linkedin.com/sales/lead/{_safe_file_stem(source)}",
                        "menuState": "connectable",
                        "menuLabels": [{"text": "Connect"}],
                    }
                ],
            }
        )
        return artifact, str(self.out_dir / f"{_safe_file_stem(source)}-capture.json")


def _safe_file_stem(value: str) -> str:
    return value.lower().replace(" ", "-").replace("/", "-")


class _ClassifyLocator:
    def __init__(
        self,
        *,
        count: int = 0,
        visible: bool = False,
        box: dict[str, float] | None = None,
    ) -> None:
        self._count = count
        self._visible = visible
        self._box = box

    async def count(self) -> int:
        return self._count

    def nth(self, _index: int) -> _ClassifyLocator:
        return self

    async def is_visible(self) -> bool:
        return self._visible

    async def bounding_box(self) -> dict[str, float] | None:
        return self._box


class _ClassifyPage:
    def __init__(self, locator: _ClassifyLocator) -> None:
        self.url = "https://www.linkedin.com/sales/lead/abc"
        self._locator = locator

    def locator(self, selector: str) -> _ClassifyLocator:
        if selector == SECURITY_VERIFICATION_SELECTOR:
            return self._locator
        return _ClassifyLocator()


class FakeSalesNavApiResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    async def json(self) -> object:
        return self.payload


def test_default_source_mix_matches_current_contract() -> None:
    sources = default_sources(30)
    assert [(source.name, source.target) for source in sources[:5]] == [
        ("ASAP - Agency Owners Delivery", 9),
        ("ASAP - Contract Recruiters Staffing", 7),
        ("ASAP - Startup CTO Eng Leaders", 6),
        ("ASAP - High-Intent SaaS AI Founders", 5),
        ("ASAP - Vertical Proof Buyers", 3),
    ]
    assert sources[5].name == "FO - Founders - Urgent"
    assert sources[5].fallback is True


def test_menu_classifier_handles_linkedin_pending_dash() -> None:
    assert (
        _classify_menu_labels([{"text": "Connect — Pending", "disabled": True}])
        == "already-pending"
    )


@pytest.mark.asyncio
async def test_page_classifier_ignores_hidden_security_verification_iframe() -> None:
    classification = await _classify_page(
        _ClassifyPage(_ClassifyLocator(count=1, visible=False, box=None))
    )

    assert classification.kind is BrowserBlockKind.CLEAR
    assert classification.reason == "clear"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("visible", "box"),
    [
        (True, None),
        (False, {"width": 320.0, "height": 240.0}),
    ],
)
async def test_page_classifier_blocks_visible_security_verification_iframe(
    visible: bool,
    box: dict[str, float] | None,
) -> None:
    classification = await _classify_page(
        _ClassifyPage(_ClassifyLocator(count=1, visible=visible, box=box))
    )

    assert classification.kind is BrowserBlockKind.SECURITY_CHALLENGE
    assert classification.reason == "security-verification-present"


@pytest.mark.asyncio
async def test_salesnav_api_response_enriches_capture_rows() -> None:
    api_rows_by_urn: dict[str, dict[str, Any]] = {}
    api_state: dict[str, Any] = {"enabled": True, "responses": 0, "rows": 0, "errors": []}
    scroll_urn = "urn:li:fs_salesProfile:(abc,NAME_SEARCH,token)"

    await _capture_salesnav_api_response(
        FakeSalesNavApiResponse(
            {
                "elements": [
                    {
                        "entityUrn": scroll_urn,
                        "fullName": "Ada Lovelace",
                        "pendingInvitation": False,
                        "degree": 2,
                        "saved": False,
                        "viewed": True,
                        "openLink": "/sales/lead/abc,NAME_SEARCH,token",
                    }
                ]
            }
        ),
        api_rows_by_urn=api_rows_by_urn,
        api_state=api_state,
    )
    row: dict[str, Any] = {
        "scrollUrn": scroll_urn,
        "profileUrl": None,
        "menuState": "not-opened",
        "menuLabels": [],
    }

    classified = _apply_salesnav_api_state(row, api_rows_by_urn)

    assert classified is True
    assert api_state["responses"] == 1
    assert api_state["rows"] == 1
    assert row["apiState"] == api_rows_by_urn[scroll_urn]
    assert row["profileUrl"] == "https://www.linkedin.com/sales/lead/abc,NAME_SEARCH,token"
    assert row["menuState"] == "connectable"
    assert row["menuLabels"][0]["tag"] == "API"


def test_capture_import_dedupes_and_derives_salesnav_profile_url(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=22, run_date=date(2026, 6, 24), force=True)

    message = import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)

    run = store.load_run()
    assert message == "imported 2 candidate observations"
    assert [observation.name for observation in run.observations] == ["Duplicate Lead", "URN Lead"]
    assert run.observations[0].profile_url == "https://www.linkedin.com/sales/lead/dup,SEARCH,y"
    assert run.observations[1].profile_url == (
        "https://www.linkedin.com/sales/lead/"
        "ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt"
    )
    plan = run.operator_plan()
    assert plan.action == "send-candidate"
    assert plan.name == "Duplicate Lead"
    resume_url = run.capture_cursors["ASAP - Agency Owners Delivery"].resume_url
    assert resume_url is not None
    assert resume_url.endswith("page=2")


def test_cli_drain_stale_candidates_delegates_to_python_app(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = "ASAP - Agency Owners Delivery"
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    record_candidate(store, source=source, name="Sent Lead", status=CandidateStatus.PENDING)
    run = store.load_run()
    run.observations.append(
        CandidateObservation(
            source=source,
            index=1,
            name="Stale Lead",
            profile_url="https://www.linkedin.com/sales/lead/stale,NAME_SEARCH,x",
            menu_state="connectable",
        )
    )
    store.save_run(run)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "drain-stale-candidates",
            "--source",
            source,
        ]
    )

    assert exit_code == 0
    assert "auto-skipped 1 stale queued candidates" in capsys.readouterr().out
    drained = [
        event
        for event in store.load_run().candidates
        if event.name == "Stale Lead" and event.status == CandidateStatus.SKIPPED
    ]
    assert len(drained) == 1


def test_cli_top_up_reconcile_confirms_durable_shortfall_with_fixtures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    start_run(store, target=2, run_date=date(2026, 6, 24), force=True)
    record_audit(store, 100, "starting count")
    record_candidate(
        store,
        source="ASAP - Agency Owners Delivery",
        name="Original Send",
        status=CandidateStatus.PENDING,
    )
    record_audit(store, 100, "short final audit")
    run = store.load_run()
    run.observations.append(
        CandidateObservation(
            source="FO - Founders - Urgent",
            index=1,
            name="Top Up Candidate",
            profile_url="https://www.linkedin.com/sales/lead/topup,NAME_SEARCH,x",
            menu_state="connectable",
        )
    )
    store.save_run(run)
    send_result = tmp_path / "top-up-send.json"
    send_result.write_text(
        json.dumps(
            {
                "candidate": {
                    "source": "FO - Founders - Urgent",
                    "name": "Top Up Candidate",
                    "profileUrl": "https://www.linkedin.com/sales/lead/topup,NAME_SEARCH,x",
                },
                "status": "pending-provisional",
                "send": {"clicked": True},
            }
        ),
        encoding="utf-8",
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "top-up-reconcile",
            "--allow-send",
            "--max-attempts",
            "1",
            "--delay-ms",
            "0",
            "--no-fallback-capture",
            "--fixture-send-result",
            str(send_result),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "top-up send status: pending-provisional" in output
    assert "confirmation status: pending; verified 2/2" in output
    run = store.load_run()
    assert any(
        event.name == "Top Up Candidate" and event.status == CandidateStatus.PENDING
        for event in run.candidates
    )


def test_report_surfaces_reconciliation_shortfall_after_top_ups(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=3, run_date=date(2026, 6, 28), force=True)
    record_audit(store, 100, "starting count")
    for index in range(3):
        record_candidate(
            store,
            source="ASAP - Agency Owners Delivery",
            name=f"Verified {index}",
            status=CandidateStatus.PENDING,
        )
    run = store.load_run()
    run.candidates.append(
        CandidateEvent(
            at=datetime(2026, 6, 28, tzinfo=UTC),
            source="FO - Founders - Urgent",
            name="Top Up Candidate",
            status=CandidateStatus.AUDIT_TOP_UP,
        )
    )
    store.save_run(run)
    record_audit(store, 102, "short final audit")

    report = render_report(store.load_run())

    assert "- Expected final audit: People (103)" in report
    assert "- Sent-page audit shortfall: 1" in report
    assert "- Audit top-ups recorded: 1" in report
    assert "- Recorded invite events minus audited delta: 2" in report
    assert "Sent-page delta is now a pending-queue sanity check" in report


def test_report_names_uncertain_send_recovery_for_active_audit_gap(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=2, run_date=date(2026, 6, 29), force=True)
    record_audit(store, 100, "starting count")
    run = store.load_run()
    run.candidates.append(
        CandidateEvent(
            at=datetime(2026, 6, 29, tzinfo=UTC),
            source="ASAP - Agency Owners Delivery",
            name="Verified Lead",
            status=CandidateStatus.PENDING,
        ),
    )
    store.save_run(run)
    record_audit(store, 100, "fresh audit after uncertain clicked-send")
    run = store.load_run()
    run.state = RunState.SENDING
    store.save_run(run)

    report = render_report(store.load_run())

    assert "- Recorded invite events minus audited delta: 1" in report
    assert "Uncertain send recovery: pause further sends" in report


def test_finish_error_names_current_reconcile_command(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 28), force=True)
    record_audit(store, 100, "starting count")

    with pytest.raises(RuntimeError, match="durable confirmed sends are 0/1"):
        finish_run(store)



def test_guarded_connection_send_preserves_real_send_gate(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    browser = FixtureBrowserClient(send_result=FIXTURES / "send_pending.json")

    with pytest.raises(RuntimeError, match="real guarded sends require --allow-send"):
        send_guarded(store, browser, dry_run=False, allow_send=False)

    output = send_next(store, browser, dry_run=False, allow_send=True)

    run = store.load_run()
    assert "recorded pending" in output
    assert run.verified_count() == 1
    assert run.state == RunState.FINAL_RECONCILE


def test_send_next_records_reverted_connect_after_durable_check(tmp_path: Path) -> None:
    FakeLiveBrowserClient.instances.clear()
    FakeLiveBrowserClient.acceptance_status = "connectable"
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 29), force=True)
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    browser = FakeLiveBrowserClient(out_dir=tmp_path / "browser")

    output = send_next(
        store,
        browser,
        dry_run=False,
        allow_send=True,
        confirm_delay_ms=0,
    )

    run = store.load_run()
    assert "recorded pending-provisional" in output
    assert "confirmation status: reverted-connect; verified 0/1" in output
    assert run.verified_count() == 0
    assert run.real_send_attempt_count() == 1
    assert run.candidates[-1].status == CandidateStatus.REVERTED_CONNECT


def test_source_yield_report_prioritizes_email_required_skips(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=3, run_date=date(2026, 6, 30), force=True)
    source = "ASAP - Agency Owners Delivery"
    for index in range(3):
        record_candidate(
            store,
            source=source,
            name=f"Email Required {index}",
            profile_url=f"https://www.linkedin.com/sales/lead/email-{index}",
            status=CandidateStatus.SKIPPED,
            note="salesnav-send-one stopped on email-required invite flow",
        )

    stats = next(item for item in source_yield_report(store.load_run()) if item.source == source)

    assert stats.email_required_skips == 3
    assert (
        stats.recommendation
        == "high-email-required: capture more candidates before retrying source"
    )


def test_source_yield_report_prioritizes_non_durable_send_attempts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=3, run_date=date(2026, 6, 30), force=True)
    source = "ASAP - Agency Owners Delivery"
    for index in range(3):
        record_candidate(
            store,
            source=source,
            name=f"Reverted {index}",
            profile_url=f"https://www.linkedin.com/sales/lead/reverted-{index}",
            status=CandidateStatus.REVERTED_CONNECT,
            note="durable confirmation connectable again; invite not durable",
        )

    stats = next(item for item in source_yield_report(store.load_run()) if item.source == source)

    assert stats.reverted_connect_count == 3
    assert (
        stats.recommendation
        == "not-durable: pause source until send confirmation behavior is understood"
    )


def test_finish_uses_durable_confirmation_and_seeds_acceptance(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    send_next(
        store,
        FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
        dry_run=False,
        allow_send=True,
    )
    import_audit(store, FIXTURES / "audit_100.json")

    report = finish_run(store)

    assert "State: `Done`" in report
    ledger = store.load_acceptance_ledger()
    assert len(ledger.invitations) == 1
    assert ledger.invitations[0].name == "Duplicate Lead"


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
    assert "project overflow, prototypes, and AI-enabled product builds" in rendered
    assert "Are you the right person to ask about this kind of project support?" in rendered
    assert "- Template: `agency`" in rendered
    followups = store.load_acceptance_followup_ledger()
    record = followups.drafts[0]
    assert record.template_key == AcceptedFollowupTemplateKey.AGENCY
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


def test_acceptance_followup_template_routing_is_source_first() -> None:
    assert choose_angle(
        "ASAP - Agency Owners Delivery", "AI Product Leader", "Acme AI"
    ) == (AcceptedFollowupTemplateKey.AGENCY, "project or overflow support ask for Acme AI")
    assert choose_angle(
        "ASAP - Contract Recruiters Staffing", "Founder", "Hiring Co"
    ) == (AcceptedFollowupTemplateKey.RECRUITER, "contract-role availability ask for Hiring Co")
    assert choose_angle(
        "ASAP - Vertical Proof Buyers", "Founder", "Proof Co"
    ) == (AcceptedFollowupTemplateKey.GENERAL, "product-engineering support ask for Proof Co")
    assert choose_angle(
        "Unknown List", "Talent Acquisition Partner", "Search Co"
    ) == (AcceptedFollowupTemplateKey.RECRUITER, "contract-role availability ask for Search Co")


def test_general_accepted_followup_uses_low_friction_relevant_cta() -> None:
    draft = general_accepted_followup_draft("Sam", "Acme AI")

    assert "Are you the right person to ask" in draft
    assert "would be useful at Acme AI?" in draft
    assert "resume" not in draft.lower()


def test_pending_cleanup_honors_threshold_and_audit_backed_finish(tmp_path: Path) -> None:
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=1, threshold_days=14, force=True)
    pending_cleanup_import_capture(store, FIXTURES / "pending_capture.json")
    run = store.load_pending()
    next_pending = run.next_eligible_observation()
    assert next_pending is not None
    assert next_pending.name == "Stale Invite"

    dry = pending_cleanup_withdraw_next(
        store,
        FixtureBrowserClient(withdraw_result=FIXTURES / "withdraw_dry_run.json"),
        dry_run=True,
        allow_withdraw=False,
    )
    assert "dry_run=True" in dry
    assert store.load_pending().withdrawn_count() == 0

    pending_cleanup_record_withdraw_result(store, FIXTURES / "withdraw_result.json")
    pending_cleanup_import_audit(store, FIXTURES / "audit_200.json")
    with pytest.raises(RuntimeError, match="final audit delta"):
        pending_cleanup_finish(store)
    pending_cleanup_import_audit(store, FIXTURES / "audit_199.json")
    report = pending_cleanup_finish(store)
    assert "State: `Done`" in report
    assert "Stale Invite" in report


def test_pending_cleanup_loads_legacy_month_threshold(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.pending_active_path.write_text(
        json.dumps(
            {
                "id": "74965971-cea3-4949-9cc7-549d1aec07e9",
                "date": "2026-06-21",
                "max_withdrawals": 75,
                "threshold_months": 2,
                "state": "Withdrawing",
                "observations": [],
                "withdrawals": [],
            }
        )
    )

    run = store.load_pending()

    assert run.threshold_days == 60
    assert run.threshold_months == 2


def test_pending_withdraw_browser_timeout_writes_failed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PlaywrightBrowserClient(
        out_dir=tmp_path,
        withdraw_timeout_seconds=0.001,
    )

    async def slow_withdraw(
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        _ = candidate, dry_run, allow_withdraw
        await asyncio.sleep(60)
        raise AssertionError("timeout did not fire")

    monkeypatch.setattr(client, "_withdraw_pending", slow_withdraw)
    candidate = PendingCandidateObservation(
        index=0,
        name="Stale Invite",
        profile_url="https://www.linkedin.com/in/stale",
        age_text="Sent 2 weeks ago",
        eligible=True,
    )

    try:
        result, path = client.withdraw_pending(candidate, dry_run=True, allow_withdraw=False)
    finally:
        client.close()

    assert result.status == "timeout"
    assert "timed out" in str(result.detail)
    assert Path(path).exists()


def test_playwriter_acceptance_followup_uses_script_and_preserves_guards(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, dict[str, Any]]] = []
    record = AcceptanceFollowupRecord(
        key="source:lead",
        id="lead-1",
        source="source",
        name="Accepted Lead",
        profile_url="https://www.linkedin.com/sales/lead/abc",
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
                    "source": record.source,
                },
                "dryRun": config["dryRun"],
                "url": record.profile_url,
                "messageLength": len(record.draft),
                "status": "dry-run-messageable",
            },
        )

    client._run_script = fake_run_script  # type: ignore[method-assign]

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


def test_playwriter_pending_capture_and_withdraw_use_scripts(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, dict[str, Any]]] = []
    candidate = PendingCandidateObservation(
        index=0,
        name="Stale Invite",
        profile_url="https://www.linkedin.com/in/stale",
        age_text="Sent 2 weeks ago",
        eligible=True,
    )
    client = PlaywriterBrowserClient(out_dir=tmp_path, session="test", playwriter_bin="playwriter")

    def fake_run_script(script: Path, config: dict[str, Any]) -> None:
        calls.append((script, config))
        if script.name == "pending_capture.js":
            _write_fake_artifact(
                Path(config["out"]),
                {
                    "capturedAt": "2026-06-30T00:00:00Z",
                    "rows": [
                        {
                            "index": 0,
                            "name": candidate.name,
                            "profileUrl": candidate.profile_url,
                            "ageText": candidate.age_text,
                            "ageDays": 14,
                            "eligible": True,
                        }
                    ],
                },
            )
        else:
            _write_fake_artifact(
                Path(config["out"]),
                {
                    "candidate": {
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "ageText": candidate.age_text,
                    },
                    "status": "dry-run-withdrawable",
                    "detail": {"rowText": candidate.name},
                },
            )

    client._run_script = fake_run_script  # type: ignore[method-assign]

    capture, capture_path = client.capture_pending_invitations(
        load_more=3,
        threshold_days=14,
        out=tmp_path / "pending-capture.json",
    )
    with pytest.raises(RuntimeError, match="real withdrawal requires allow_withdraw"):
        client.withdraw_pending(candidate, dry_run=False, allow_withdraw=False)
    withdraw, withdraw_path = client.withdraw_pending(
        candidate,
        dry_run=True,
        allow_withdraw=False,
    )

    assert capture.rows[0].name == "Stale Invite"
    assert Path(capture_path).exists()
    assert withdraw.status == "dry-run-withdrawable"
    assert Path(withdraw_path).exists()
    assert [call[0].name for call in calls] == ["pending_capture.js", "pending_withdraw.js"]
    assert calls[0][1]["loadMore"] == 3
    assert calls[1][1]["candidate"]["name"] == "Stale Invite"
    assert calls[1][1]["allowWithdraw"] is False


def test_playwriter_send_connection_requires_allow_send(tmp_path: Path) -> None:
    client = PlaywriterBrowserClient(
        out_dir=tmp_path,
        session="test-session",
        playwriter_bin="/bin/echo",
    )
    candidate = CandidateObservation(
        source="Saved search",
        index=0,
        name="Example Lead",
        profile_url="https://www.linkedin.com/sales/lead/abc,def,ghi",
        menu_state="connectable",
    )

    with pytest.raises(RuntimeError, match="real send requires allow_send=True"):
        client.send_connection(candidate, dry_run=False, allow_send=False)


def test_playwriter_network_methods_parse_script_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = PlaywriterBrowserClient(
        out_dir=tmp_path,
        session="test-session",
        playwriter_bin="/bin/echo",
    )
    scripts: list[str] = []

    def fake_run_script(script: Path, config: dict[str, Any]) -> None:
        scripts.append(script.name)
        out = Path(config["out"])
        if script.name == "salesnav_send.js":
            payload = {
                "candidate": {
                    "source": config["candidate"]["source"],
                    "name": config["candidate"]["name"],
                    "profileUrl": config["candidate"]["profile_url"],
                },
                "status": "dry-run-connectable",
                "send": None,
            }
        elif script.name == "salesnav_capture.js":
            payload = {
                "capturedAt": "2026-06-30T00:00:00Z",
                "source": config["source"],
                "url": config["url"],
                "stateCounts": {"connectable": 1},
                "rawRowCount": 1,
                "outputRowCount": 1,
                "rows": [
                    {
                        "index": 0,
                        "name": "Example Lead",
                        "profileUrl": "https://www.linkedin.com/sales/lead/abc,def,ghi",
                        "menuState": "connectable",
                    }
                ],
            }
        elif script.name == "salesnav_audit.js":
            payload = {"peopleCount": 7, "recentNames": ["Example Lead"]}
        elif script.name == "salesnav_saved_searches.js":
            payload = {
                "url": config["url"],
                "searches": [
                    {
                        "savedSearchId": "123",
                        "name": "Founders",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=123",
                    }
                ],
            }
        else:
            raise AssertionError(f"unexpected script {script}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(client, "_run_script", fake_run_script)
    candidate = CandidateObservation(
        source="Saved search",
        index=0,
        name="Example Lead",
        profile_url="https://www.linkedin.com/sales/lead/abc,def,ghi",
        menu_state="connectable",
    )

    send, send_path = client.send_connection(candidate, dry_run=True, allow_send=False)
    capture, capture_path = client.capture_salesnav(
        source="Saved search",
        url="https://www.linkedin.com/sales/search/people",
    )
    audit, audit_path = client.audit_sent_invitations(load_more=2)
    saved, saved_path = client.resolve_saved_searches(
        url="https://www.linkedin.com/sales/search/people",
        out=tmp_path / "saved-searches.json",
    )

    assert send.status == "dry-run-connectable"
    assert capture.rows[0].menu_state == "connectable"
    assert audit.people_count == 7
    assert saved.searches[0].saved_search_id == "123"
    assert Path(send_path).name == "001-send-result.json"
    assert Path(capture_path).name == "001-capture-page.json"
    assert Path(audit_path).name == "001-audit.json"
    assert Path(saved_path).name == "saved-searches.json"
    assert scripts == [
        "salesnav_send.js",
        "salesnav_capture.js",
        "salesnav_audit.js",
        "salesnav_saved_searches.js",
    ]


def test_cli_namespace_runs_network_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert network_main(["--state-dir", str(tmp_path), "start", "--target", "1", "--force"]) == 0
    assert network_main(["--state-dir", str(tmp_path), "plan", "--json"]) == 0
    output = capsys.readouterr().out
    assert "started run" in output
    assert '"action": "capture-source"' in output


def test_cli_send_next_uses_live_browser_when_fixture_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    out_dir = tmp_path / "send-browser"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "send-next",
            "--allow-send",
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].out_dir == out_dir
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "send:Duplicate Lead:dry=False:allow=True",
        "acceptance-check:1:offset=0:limit=1:delay=0",
    ]
    assert store.load_run().verified_count() == 1


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


def test_cli_pending_withdraw_next_uses_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    monkeypatch.setenv("LINKEDIN_TOOLS_BROWSER_BACKEND", "playwright")
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=1, threshold_days=14, force=True)
    pending_cleanup_import_capture(store, FIXTURES / "pending_capture.json")
    out_dir = tmp_path / "withdraw-browser"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "pending-cleanup",
            "withdraw-next",
            "--allow-withdraw",
            "--out-dir",
            str(out_dir),
            "--max-load-more",
            "7",
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].out_dir == out_dir
    assert FakeLiveBrowserClient.instances[-1].max_load_more == 7
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "withdraw:Stale Invite:dry=False:allow=True"
    ]
    assert store.load_pending().withdrawn_count() == 1


def test_cli_saved_searches_uses_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    monkeypatch.setenv("LINKEDIN_TOOLS_BROWSER_BACKEND", "playwright")
    out = tmp_path / "saved-searches.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "saved-searches",
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "saved-searches:https://www.linkedin.com/sales/search/people"
    ]
    payload = json.loads(out.read_text())
    assert payload["searches"][0]["name"] == "ASAP - Agency Owners Delivery"


def test_cli_browser_session_start_records_persistent_cdp_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_start_session(config: object, *, start_url: str) -> ManagedChromeSession:
        _ = config, start_url
        return ManagedChromeSession(
            pid=12345,
            port=45678,
            cdp_url="http://127.0.0.1:45678",
            user_data_dir=tmp_path / "profile",
            profile_name="LinkedIn",
        )

    monkeypatch.setattr(network_service, "start_managed_chrome_cdp_session", fake_start_session)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "browser-session",
            "start",
            "--url",
            "https://www.linkedin.com/sales/search/people",
            "--force",
        ]
    )

    assert exit_code == 0
    state = read_model(Store(tmp_path).browser_session_path, BrowserSessionState)
    assert state.pid == 12345
    assert state.cdp_url == "http://127.0.0.1:45678"
    assert state.start_url == "https://www.linkedin.com/sales/search/people"


def test_cli_saved_searches_attaches_to_persistent_browser_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    monkeypatch.setenv("LINKEDIN_TOOLS_BROWSER_BACKEND", "playwright")
    monkeypatch.setattr(
        network_cli,
        "browser_session_cdp_url",
        lambda store: "http://127.0.0.1:45678",
    )
    out = tmp_path / "saved-searches.json"

    exit_code = network_main(["--state-dir", str(tmp_path), "saved-searches", "--out", str(out)])

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].cdp_url == "http://127.0.0.1:45678"
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "saved-searches:https://www.linkedin.com/sales/search/people"
    ]


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
            "--no-public-web",
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "accepted-research:1:offset=0:limit=0:web=False:max=5:delay=500"
    ]
    assert (out_dir / "accepted-candidates.json").exists()
    assert (out_dir / "accepted-research.json").exists()
    assert store.load_acceptance_followup_ledger().drafts[0].name == "Duplicate Lead"


def test_cli_pending_capture_uses_live_browser_and_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=1, threshold_days=14, force=True)
    audit_exit = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "pending-cleanup",
            "audit",
            "--load-more",
            "2",
        ]
    )
    assert audit_exit == 0
    assert FakeLiveBrowserClient.instances[-1].calls == ["audit:load_more=2"]
    assert store.load_pending().start_audit == 101

    out = tmp_path / "pending-capture.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "pending-cleanup",
            "capture",
            "--load-more",
            "3",
            "--threshold-weeks",
            "2",
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].calls == ["pending-capture:load_more=3:threshold=14"]
    observation = store.load_pending().next_eligible_observation()
    assert observation is not None
    assert observation.name == "Stale Invite"


def test_cli_pending_run_session_reuses_one_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=2, threshold_days=14, force=True)
    out = tmp_path / "pending-capture.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "pending-cleanup",
            "run-session",
            "--capture-load-more",
            "3",
            "--threshold-weeks",
            "2",
            "--out",
            str(out),
            "--withdraw-limit",
            "1",
            "--allow-withdraw",
        ]
    )

    assert exit_code == 0
    assert len(FakeLiveBrowserClient.instances) == 1
    assert FakeLiveBrowserClient.instances[0].calls == [
        "audit:load_more=0",
        "pending-capture:load_more=3:threshold=14",
        "withdraw:Stale Invite:dry=True:allow=False",
        "withdraw:Stale Invite:dry=False:allow=True",
        "audit:load_more=0",
    ]
    run = store.load_pending()
    assert run.start_audit == 101
    assert run.latest_audit == 101
    assert run.withdrawn_count() == 1


def test_cli_network_run_session_reuses_one_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    saved_searches = tmp_path / "saved-searches.json"
    out_dir = tmp_path / "network-session"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--target",
            "1",
            "--max-real-sends",
            "1",
            "--force",
            "--saved-searches",
            str(saved_searches),
            "--allow-send",
            "--audit-attempts",
            "1",
            "--audit-delay-ms",
            "0",
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert len(FakeLiveBrowserClient.instances) == 1
    assert FakeLiveBrowserClient.instances[0].out_dir == out_dir
    assert FakeLiveBrowserClient.instances[0].calls == [
        "audit:load_more=0",
        "saved-searches:https://www.linkedin.com/sales/search/people",
        (
            "capture:ASAP - Agency Owners Delivery:pages=3:limit=18:only=True:"
            "url=https://www.linkedin.com/sales/search/people?savedSearchId=abc"
        ),
        "send:Duplicate Lead:dry=False:allow=True",
        "acceptance-check:1:offset=0:limit=1:delay=0",
        "audit:load_more=0",
    ]
    store = Store(tmp_path)
    assert store.load_run().verified_count() == 1


def test_cli_network_run_session_exhausts_repeated_zero_capture_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeLiveBrowserClient.instances.clear()
    monkeypatch.setattr(network_cli, "PlaywriterBrowserClient", ZeroThenNextSourceBrowserClient)
    monkeypatch.setattr(network_cli, "PlaywrightBrowserClient", ZeroThenNextSourceBrowserClient)
    saved_searches = tmp_path / "saved-searches.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--target",
            "1",
            "--max-real-sends",
            "1",
            "--force",
            "--saved-searches",
            str(saved_searches),
            "--allow-send",
            "--audit-attempts",
            "1",
            "--audit-delay-ms",
            "0",
            "--max-steps",
            "8",
            "--out-dir",
            str(tmp_path / "network-session"),
        ]
    )

    assert exit_code == 0
    run = Store(tmp_path).load_run()
    assert run.sources[0].name == "ASAP - Agency Owners Delivery"
    assert run.sources[0].exhausted is True
    assert any(
        "3 consecutive captures imported 0 usable candidates" in note for note in run.notes
    )
    assert run.verified_count() == 1
    calls = ZeroThenNextSourceBrowserClient.instances[0].calls
    assert sum(
        call.startswith("capture:ASAP - Agency Owners Delivery") for call in calls
    ) == 3
    assert any(call.startswith("capture:ASAP - Contract Recruiters Staffing") for call in calls)
    assert any(call.startswith("send:ASAP - Contract Recruiters Staffing Lead") for call in calls)


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
            "--no-public-web",
            "--out-dir",
            str(browser_out_dir),
        ]
    )

    assert exit_code == 0
    assert len(FakeLiveBrowserClient.instances) == 1
    assert FakeLiveBrowserClient.instances[0].out_dir == browser_out_dir
    assert FakeLiveBrowserClient.instances[0].calls == [
        "acceptance-check:1:offset=0:limit=1:delay=750",
        "accepted-research:1:offset=0:limit=0:web=False:max=5:delay=500",
    ]
    assert json.loads(outcomes.read_text())["rows"][0]["status"] == "accepted"
    assert draft_report.exists()
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
            "--no-public-web",
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[0].calls == [
        "acceptance-check:2:offset=1:limit=1:delay=750",
        "accepted-research:1:offset=0:limit=0:web=False:max=5:delay=500",
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


def test_cli_capture_reconcile_and_reservoir_capture_use_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    capture_out = tmp_path / "capture-browser"

    capture_exit = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "capture",
            "--url",
            "https://www.linkedin.com/sales/search/people?savedSearchId=1",
            "--only-connectable",
            "--pages",
            "2",
            "--limit",
            "4",
            "--out-dir",
            str(capture_out),
        ]
    )

    assert capture_exit == 0
    assert FakeLiveBrowserClient.instances[-1].out_dir == capture_out
    assert (
        "capture:ASAP - Agency Owners Delivery:pages=2:limit=4:only=True"
        in (FakeLiveBrowserClient.instances[-1].calls[0])
    )
    assert [observation.name for observation in store.load_run().observations] == [
        "Duplicate Lead",
        "URN Lead",
    ]

    import_audit(store, FIXTURES / "audit_100.json")
    audit_out = tmp_path / "audit-browser"
    reconcile_exit = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "reconcile-audit",
            "--attempts",
            "1",
            "--delay-ms",
            "0",
            "--out-dir",
            str(audit_out),
        ]
    )
    assert reconcile_exit == 0
    assert FakeLiveBrowserClient.instances[-1].out_dir == audit_out
    assert store.load_run().state != RunState.DONE

    reservoir_out = tmp_path / "reservoir-browser"
    reservoir_exit = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "reservoir",
            "capture",
            "--source",
            "ASAP - Agency Owners Delivery",
            "--url",
            "https://www.linkedin.com/sales/search/people?savedSearchId=1",
            "--only-connectable",
            "--out-dir",
            str(reservoir_out),
        ]
    )
    assert reservoir_exit == 0
    assert FakeLiveBrowserClient.instances[-1].out_dir == reservoir_out
    assert [observation.name for observation in store.load_reservoir().observations] == [
        "Duplicate Lead",
        "URN Lead",
    ]


def test_old_state_inspection_is_read_only(tmp_path: Path) -> None:
    old_store = Store(tmp_path)
    start_run(old_store, target=1, run_date=date(2026, 6, 24), force=True)
    active = old_store.active_path
    before = active.stat().st_mtime_ns

    snapshot = inspect_old_state(tmp_path)

    assert snapshot.active_run is not None
    assert active.stat().st_mtime_ns == before


def _write_fake_artifact(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(model, "model_dump"):
        payload = model.model_dump(mode="json", by_alias=False)
    else:
        payload = model
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _run_id() -> uuid.UUID:
    return uuid.uuid4()


def _install_fake_live_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeLiveBrowserClient.instances.clear()
    FakeLiveBrowserClient.acceptance_status = "accepted"
    monkeypatch.setattr(network_cli, "PlaywriterBrowserClient", FakeLiveBrowserClient)
    monkeypatch.setattr(network_cli, "PlaywrightBrowserClient", FakeLiveBrowserClient)
