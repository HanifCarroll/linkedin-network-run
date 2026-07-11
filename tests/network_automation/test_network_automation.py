from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

import apps.network_automation.cli as network_cli
from apps.network_automation.browser import (
    FixtureBrowserClient,
    PlaywriterBrowserClient,
)
from apps.network_automation.cli import main as network_main
from apps.network_automation.models import (
    CandidateEvent,
    CandidateObservation,
    CandidateStatus,
    LeadStatus,
    PendingCandidateObservation,
    PendingCapture,
    PendingCleanupState,
    RunState,
    SalesNavCapture,
    SalesNavSendResult,
    SavedSearchArtifact,
    SourceCaptureCursor,
    SourceCursorStatus,
    SourceScanProgress,
    SourceScanProgressLedger,
    default_sources,
    lead_key_for_observation,
    parse_sent_age_days,
    parse_sent_age_months,
    record_send_result,
    source_yield_report,
    sources_for_per_source_target,
    target_for_per_source_target,
)
from apps.network_automation.old_state import inspect_old_state
from apps.network_automation.reports import render_report
from apps.network_automation.service import (
    apply_lead_review_decisions,
    capture_saved_searches,
    capture_source,
    confirm_provisional_send,
    finish_run,
    import_audit,
    import_capture_path,
    network_sends_summary,
    pending_cleanup_finish,
    pending_cleanup_import_audit,
    pending_cleanup_import_capture,
    pending_cleanup_record_withdraw_result,
    pending_cleanup_start,
    pending_cleanup_withdraw_next,
    reconcile_audit,
    record_audit,
    record_candidate,
    record_send_result_from_path,
    record_top_up_result_from_path,
    reset_source_progress,
    resolve_network_source_url,
    retry_failed_lead,
    review_candidates,
    seed_run_source_progress,
    send_guarded,
    send_next,
    set_lead_public_profile_url,
    start_run,
)
from apps.network_automation.store import Store
from apps.recruiter_agency_outreach.models import (
    Lead as OutreachLead,
)
from apps.recruiter_agency_outreach.models import (
    LeadStatus as OutreachLeadStatus,
)
from apps.recruiter_agency_outreach.models import (
    LeadType as OutreachLeadType,
)
from apps.recruiter_agency_outreach.models import (
    MessageStatus as OutreachMessageStatus,
)
from apps.recruiter_agency_outreach.models import (
    OutreachState,
)
from apps.recruiter_agency_outreach.storage import Store as OutreachStore

from .helpers import (
    FIXTURES,
    AcceptanceCandidateCapturingBrowser,
    CandidateCapturingBrowser,
    FakeLiveBrowserClient,
    ZeroThenNextSourceBrowserClient,
    _install_fake_live_browser,
    _safe_file_stem,
    _write_fake_artifact,
)


def test_cli_help_documents_browser_backend_selection() -> None:
    help_text = network_cli.build_parser().format_help()

    assert "Playwriter only" in help_text
    assert "LINKEDIN_TOOLS_PLAYWRITER_SESSION=<id>" in help_text
    assert "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY=<key>" in help_text


def _make_source_current(store: Store, source: str) -> None:
    run = store.load_run()
    for source_plan in run.sources:
        if source_plan.name == source:
            source_plan.exhausted = False
            store.save_run(run)
            return
        source_plan.exhausted = True
    raise AssertionError(f"unknown source: {source}")


def _approve_all_observed_leads(store: Store, reason: str = "test approved") -> None:
    run = store.load_run()
    ledger = store.load_lead_ledger()
    for observation in run.observations:
        if observation.public_profile_url is None:
            slug = _safe_file_stem(f"{observation.name}-{observation.index}")
            observation.public_profile_url = f"https://www.linkedin.com/in/{slug}"
        record = ledger.upsert_observation(observation)
        ledger.approve(record.lead_key, reason)
    store.save_run(run)
    store.save_lead_ledger(ledger)


def test_default_source_mix_matches_current_contract() -> None:
    sources = default_sources(30)
    assert [(source.name, source.target) for source in sources[:3]] == [
        ("ASAP - Contract Recruiters Staffing", 10),
        ("ASAP - Agency Owners Delivery", 10),
        ("ASAP - Strategy Consultants Implementation Partners", 10),
    ]
    assert sources[3].name == "FO - Founders - Urgent"
    assert sources[3].fallback is True


def test_per_source_target_sets_exact_primary_quotas_without_carryover_or_fallback(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)

    output = start_run(
        store,
        per_source_target=2,
        run_date=date(2026, 7, 2),
        force=True,
        allow_fallback_sources=False,
    )

    run = store.load_run()
    assert "target 6" in output
    assert target_for_per_source_target(2) == 6
    assert [source.target for source in sources_for_per_source_target(2)[:3]] == [2, 2, 2]
    assert run.target == 6
    assert run.max_real_sends == 6
    assert run.carry_over_shortfall is False
    assert run.allow_fallback_sources is False
    assert [(source.name, source.target) for source in run.sources[:3]] == [
        ("ASAP - Contract Recruiters Staffing", 2),
        ("ASAP - Agency Owners Delivery", 2),
        ("ASAP - Strategy Consultants Implementation Partners", 2),
    ]

    run.sources[0].exhausted = True
    run.sources[1].exhausted = True
    run.sources[2].exhausted = True
    store.save_run(run)

    exhausted = store.load_run()
    assert exhausted.source_quota("ASAP - Strategy Consultants Implementation Partners") == 2
    assert exhausted.next_source() is None


def test_per_source_target_can_use_explicit_source_names(tmp_path: Path) -> None:
    store = Store(tmp_path)

    output = start_run(
        store,
        per_source_target=2,
        run_date=date(2026, 7, 2),
        force=True,
        allow_fallback_sources=False,
        source_names=[
            "ASAP - Vertical Proof Buyers",
            "ASAP - High-Intent SaaS AI Founders",
        ],
    )

    run = store.load_run()
    assert "target 4" in output
    assert run.target == 4
    assert run.max_real_sends == 4
    assert run.carry_over_shortfall is False
    assert run.allow_fallback_sources is False
    assert [(source.name, source.target, source.fallback) for source in run.sources] == [
        ("ASAP - Vertical Proof Buyers", 2, False),
        ("ASAP - High-Intent SaaS AI Founders", 2, False),
        ("FO - Founders - Urgent", 0, True),
    ]


def test_network_source_url_uses_saved_searches_for_network_sources(tmp_path: Path) -> None:
    saved_searches = tmp_path / "saved-searches.json"
    saved_searches.write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "name": "ASAP - Contract Recruiters Staffing",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=def",
                    },
                    {
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    },
                    {
                        "name": "ASAP - Strategy Consultants Implementation Partners",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=ghi",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    contract_url = resolve_network_source_url(saved_searches, "ASAP - Contract Recruiters Staffing")
    agency_url = resolve_network_source_url(saved_searches, "ASAP - Agency Owners Delivery")
    advisor_url = resolve_network_source_url(
        saved_searches, "ASAP - Strategy Consultants Implementation Partners"
    )

    assert contract_url == "https://www.linkedin.com/sales/search/people?savedSearchId=def"
    assert agency_url == "https://www.linkedin.com/sales/search/people?savedSearchId=abc"
    assert advisor_url == "https://www.linkedin.com/sales/search/people?savedSearchId=ghi"


def test_seed_source_progress_resets_exhausted_source_with_fresh_results(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 3),
        force=True,
        allow_fallback_sources=False,
        source_names=["ASAP - Agency Owners Delivery"],
    )
    run = store.load_run()
    run.sources[0].exhausted = True
    store.save_run(run)
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                "ASAP - Agency Owners Delivery": SourceScanProgress(
                    source="ASAP - Agency Owners Delivery",
                    saved_search_id="abc",
                    saved_search_url="https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    next_url="https://www.linkedin.com/sales/search/people?page=2&savedSearchId=abc",
                    end_of_results=True,
                    last_note="end of results",
                )
            }
        )
    )
    saved_searches = tmp_path / "saved-searches.json"
    saved_searches.write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                        "freshUrl": (
                            "https://www.linkedin.com/sales/search/people?"
                            "lastViewedAt=1781957710070&savedSearchId=abc"
                        ),
                        "freshText": "503 new results since 6/20/2026",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = seed_run_source_progress(store, saved_searches)

    run = store.load_run()
    progress = store.load_source_progress().sources["ASAP - Agency Owners Delivery"]
    cursor = run.capture_cursors["ASAP - Agency Owners Delivery"]
    assert output == "source progress seeded; resumed=1; ended=0"
    assert run.sources[0].exhausted is False
    assert progress.end_of_results is False
    assert progress.next_url == cursor.resume_url
    assert cursor.resume_url is not None
    assert "lastViewedAt=1781957710070" in cursor.resume_url
    assert run.operator_plan().action == "capture-source"


def test_fresh_saved_search_end_resumes_full_saved_search(
    tmp_path: Path,
) -> None:
    source = "ASAP - Strategy Consultants Implementation Partners"
    view_url = "https://www.linkedin.com/sales/search/people?savedSearchId=ghi"
    fresh_url = (
        "https://www.linkedin.com/sales/search/people?"
        "lastViewedAt=1783083922996&savedSearchId=ghi"
    )
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 4),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.sources[0].exhausted = True
    store.save_run(run)
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                source: SourceScanProgress(
                    source=source,
                    saved_search_id="ghi",
                    saved_search_url=view_url,
                    end_of_results=True,
                    last_note="end of results",
                )
            }
        )
    )
    saved_searches = tmp_path / "saved-searches.json"
    saved_searches.write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "savedSearchId": "ghi",
                        "name": source,
                        "viewUrl": view_url,
                        "freshUrl": fresh_url,
                        "freshText": "15 new results since 7/3/2026",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    seed_run_source_progress(store, saved_searches)
    browser = FreshSliceEndBrowserClient(out_dir=tmp_path)

    output = capture_source(
        store,
        browser,
        source=source,
        url=None,
        saved_searches=saved_searches,
        pages=5,
        limit=0,
        stop_after_connectable=13,
        only_connectable=True,
        row_scroll_delay_ms=250,
    )

    run = store.load_run()
    progress = store.load_source_progress().sources[source]
    cursor = run.capture_cursors[source]
    assert "captured 1 candidate observations" in output
    assert browser.calls == [
        f"capture:{source}:pages=5:limit=0:only=True:url={fresh_url}"
    ]
    assert cursor.end_of_results is False
    assert cursor.resume_url == view_url
    assert cursor.next_url == view_url
    assert cursor.cursor_status == SourceCursorStatus.ADVANCED.value
    assert cursor.cursor_reason == "fresh saved-search results exhausted; resume full saved search"
    assert progress.end_of_results is False
    assert progress.next_url == view_url
    assert progress.last_started_url == fresh_url
    assert (
        progress.cursor_reason
        == "fresh saved-search results exhausted; resume full saved search"
    )


def test_seed_source_progress_rechecks_previous_run_end_without_fresh_results(
    tmp_path: Path,
) -> None:
    source = "ASAP - Strategy Consultants Implementation Partners"
    view_url = "https://www.linkedin.com/sales/search/people?savedSearchId=ghi"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 4),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.sources[0].exhausted = True
    run.created_at = datetime(2026, 7, 4, 9, 0, tzinfo=UTC)
    store.save_run(run)
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                source: SourceScanProgress(
                    source=source,
                    updated_at=datetime(2026, 7, 3, 13, 5, tzinfo=UTC),
                    saved_search_id="ghi",
                    saved_search_url=view_url,
                    next_url="https://www.linkedin.com/sales/search/people?page=2&savedSearchId=ghi",
                    last_scanned_url=(
                        "https://www.linkedin.com/sales/search/people?page=2&"
                        "savedSearchId=ghi"
                    ),
                    end_of_results=True,
                    last_note="end of results",
                )
            }
        )
    )
    saved_searches = tmp_path / "saved-searches.json"
    saved_searches.write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "savedSearchId": "ghi",
                        "name": source,
                        "viewUrl": view_url,
                        "freshUrl": None,
                        "freshText": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = seed_run_source_progress(store, saved_searches)

    run = store.load_run()
    progress = store.load_source_progress().sources[source]
    cursor = run.capture_cursors[source]
    assert output == "source progress seeded; resumed=1; ended=0"
    assert run.sources[0].exhausted is False
    assert progress.end_of_results is False
    assert progress.next_url == view_url
    assert progress.last_note == "previous-run end cursor recheck"
    assert cursor.resume_url == view_url
    assert cursor.cursor_reason == "previous-run end cursor recheck"
    assert run.operator_plan().action == "capture-source"


def test_seed_source_progress_keeps_current_run_end_closed_without_fresh_results(
    tmp_path: Path,
) -> None:
    source = "ASAP - Strategy Consultants Implementation Partners"
    view_url = "https://www.linkedin.com/sales/search/people?savedSearchId=ghi"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 4),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.created_at = datetime(2026, 7, 4, 9, 0, tzinfo=UTC)
    store.save_run(run)
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                source: SourceScanProgress(
                    source=source,
                    updated_at=datetime(2026, 7, 4, 9, 5, tzinfo=UTC),
                    saved_search_id="ghi",
                    saved_search_url=view_url,
                    end_of_results=True,
                    last_note="end of results",
                )
            }
        )
    )
    saved_searches = tmp_path / "saved-searches.json"
    saved_searches.write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "savedSearchId": "ghi",
                        "name": source,
                        "viewUrl": view_url,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = seed_run_source_progress(store, saved_searches)

    run = store.load_run()
    assert output == "source progress seeded; resumed=0; ended=1"
    assert run.sources[0].exhausted is True
    assert source not in run.capture_cursors
    assert run.operator_plan().action == "blocked"


def test_seed_source_progress_reopens_nonterminal_exhausted_source(
    tmp_path: Path,
) -> None:
    source = "ASAP - Agency Owners Delivery"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 3),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.sources[0].exhausted = True
    store.save_run(run)
    page_url = "https://www.linkedin.com/sales/search/people?page=2&savedSearchId=abc"
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                source: SourceScanProgress(
                    source=source,
                    saved_search_id="abc",
                    saved_search_url="https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    next_url=page_url,
                    last_scanned_url=page_url,
                    end_of_results=False,
                    cursor_status=SourceCursorStatus.STALLED_NAVIGATION.value,
                    last_note="saved-search cursor did not advance",
                )
            }
        )
    )
    saved_searches = tmp_path / "saved-searches.json"
    saved_searches.write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": source,
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = seed_run_source_progress(store, saved_searches)

    run = store.load_run()
    cursor = run.capture_cursors[source]
    assert output == "source progress seeded; resumed=1; ended=0"
    assert run.sources[0].exhausted is False
    assert cursor.resume_url == page_url
    assert cursor.next_url is None
    assert cursor.deferred_for_run is False
    assert run.operator_plan().action == "capture-source"


class _TransientSavedSearchBrowser(FakeLiveBrowserClient):
    def __init__(self, error: str, *, out_dir: Path) -> None:
        super().__init__(out_dir=out_dir)
        self.error = error
        self.saved_search_calls = 0

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        self.saved_search_calls += 1
        if self.saved_search_calls == 1:
            raise RuntimeError(self.error)
        artifact = SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-07-03T12:00:00Z",
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


class StalledCursorBrowserClient(FakeLiveBrowserClient):
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
        artifact = SalesNavCapture.model_validate(
            {
                "capturedAt": "2026-07-03T12:00:00Z",
                "source": source,
                "url": url or "",
                "resumeUrl": url or "",
                "nextUrl": url or "",
                "lastScannedUrl": url or "",
                "nextPageAvailable": True,
                "endOfResults": False,
                "rawRowCount": 1,
                "outputRowCount": 1,
                "stateCounts": {"connectable": 1},
                "rows": [
                    {
                        "index": 1,
                        "name": "Duplicate Lead",
                        "profileUrl": "https://www.linkedin.com/sales/lead/dup,SEARCH,y",
                        "menuState": "connectable",
                        "menuLabels": [{"text": "Connect"}],
                    }
                ],
            }
        )
        return artifact, str(self.out_dir / "stalled-capture.json")


class FreshSliceEndBrowserClient(FakeLiveBrowserClient):
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
        artifact = SalesNavCapture.model_validate(
            {
                "capturedAt": "2026-07-04T09:18:55Z",
                "source": source,
                "startUrl": url,
                "url": f"{url}&sessionId=test-session" if url else "",
                "lastScannedUrl": f"{url}&sessionId=test-session" if url else "",
                "nextUrl": None,
                "resumeUrl": None,
                "nextPageAvailable": False,
                "endOfResults": True,
                "cursorStatus": SourceCursorStatus.END_OF_RESULTS.value,
                "cursorReason": "next button unavailable before requested page count",
                "rawRowCount": 1,
                "outputRowCount": 1,
                "stateCounts": {"connectable": 1},
                "rows": [
                    {
                        "index": 1,
                        "name": "Strategy Lead",
                        "profileUrl": "https://www.linkedin.com/sales/lead/strategy,SEARCH,x",
                        "publicProfileUrl": "https://www.linkedin.com/in/strategy-lead",
                        "menuState": "connectable",
                        "menuLabels": [{"text": "Connect"}],
                    }
                ],
            }
        )
        return artifact, str(self.out_dir / "fresh-slice-end-capture.json")


class WrongPageCaptureBrowserClient(FakeLiveBrowserClient):
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
        artifact = SalesNavCapture.model_validate(
            {
                "capturedAt": "2026-07-03T12:00:00Z",
                "source": source,
                "url": "https://www.linkedin.com/sales/lead/not-a-search",
                "resumeUrl": url or "",
                "startUrl": url or "",
                "lastScannedUrl": "https://www.linkedin.com/sales/lead/not-a-search",
                "nextUrl": None,
                "nextPageAvailable": None,
                "endOfResults": False,
                "cursorStatus": SourceCursorStatus.WRONG_PAGE.value,
                "cursorReason": "expected Sales Navigator people search but reached lead page",
                "rawRowCount": 0,
                "outputRowCount": 0,
                "stateCounts": {},
                "rows": [],
            }
        )
        return artifact, str(self.out_dir / "wrong-page-capture.json")


@pytest.mark.parametrize(
    "error",
    [
        (
            "Playwriter command failed (1): saved-searches control missing; "
            "verify the automation browser is logged into Sales Navigator"
        ),
        "page.goto: net::ERR_ABORTED at https://www.linkedin.com/sales/search/people",
    ],
)
def test_capture_saved_searches_retries_transient_load_failures(
    tmp_path: Path,
    error: str,
) -> None:
    browser = _TransientSavedSearchBrowser(error, out_dir=tmp_path)

    output = capture_saved_searches(
        browser,
        url="https://www.linkedin.com/sales/search/people",
        out=tmp_path / "saved-searches.json",
        attempts=3,
        delay_ms=0,
    )

    assert browser.saved_search_calls == 2
    assert browser.recoveries == 1
    assert "saved-search capture attempt 1/3 failed" in output
    assert "resetting browser session and retrying" in output
    assert "captured 1 saved searches" in output


def test_capture_saved_searches_does_not_retry_login_blocker(tmp_path: Path) -> None:
    browser = _TransientSavedSearchBrowser(
        "saved searches blocked: login required",
        out_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="login required"):
        capture_saved_searches(
            browser,
            url="https://www.linkedin.com/sales/search/people",
            out=tmp_path / "saved-searches.json",
            attempts=3,
            delay_ms=0,
        )

    assert browser.saved_search_calls == 1
    assert browser.recoveries == 0


def test_capture_import_dedupes_and_derives_salesnav_profile_url(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=22, run_date=date(2026, 6, 24), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")

    message = import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)

    run = store.load_run()
    assert message == "imported 2 candidate observations"
    assert [observation.name for observation in run.observations] == ["Duplicate Lead", "URN Lead"]
    assert run.observations[0].profile_url == "https://www.linkedin.com/sales/lead/dup,SEARCH,y"
    assert run.observations[1].profile_url == (
        "https://www.linkedin.com/sales/lead/"
        "ACwAAACZuNoBDnWZnoEzJVGp-uptyWQSfIw87UM,NAME_SEARCH,HDgt"
    )
    ledger = store.load_lead_ledger()
    assert len(ledger.leads) == 2
    assert {record.status for record in ledger.leads.values()} == {LeadStatus.NEW}
    plan = run.operator_plan()
    assert plan.action == "send-candidate"
    assert plan.name == "Duplicate Lead"
    resume_url = run.capture_cursors["ASAP - Agency Owners Delivery"].resume_url
    assert resume_url is not None
    assert resume_url.endswith("page=2")


def test_capture_import_preserves_public_profile_url_for_normal_profile_fallback(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    capture_path = tmp_path / "capture-public-profile.json"
    capture_path.write_text(
        json.dumps(
            {
                "source": "ASAP - Agency Owners Delivery",
                "rows": [
                    {
                        "index": 1,
                        "name": "Public Lead",
                        "profileUrl": "https://www.linkedin.com/sales/lead/public-lead,NAME_SEARCH,x",
                        "publicProfileUrl": "https://www.linkedin.com/in/public-lead",
                        "menuState": "connectable",
                        "menuLabels": [{"text": "Connect"}],
                        "rowText": "Public Lead Founder Example Co",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    import_capture_path(store, capture_path, only_connectable=True)

    run = store.load_run()
    ledger = store.load_lead_ledger()
    assert run.observations[0].public_profile_url == "https://www.linkedin.com/in/public-lead"
    assert next(iter(ledger.leads.values())).public_profile_url == (
        "https://www.linkedin.com/in/public-lead"
    )


def test_lead_review_packet_and_decisions_gate_connection_sends(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=2, run_date=date(2026, 6, 24), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    run = store.load_run()
    run.observations[0].public_profile_url = "https://www.linkedin.com/in/duplicate-lead"
    run.observations[0].text = "Duplicate Lead Founder at Example Agency New York"
    run.observations[0].links = [
        {"text": "Duplicate Lead", "href": run.observations[0].profile_url}
    ]
    store.save_run(run)

    review_path = tmp_path / "review.json"
    output = review_candidates(store, source="ASAP - Agency Owners Delivery", out=review_path)

    packet = json.loads(review_path.read_text())
    assert "lead review packet: 2 candidate(s)" in output
    assert (tmp_path / "review.md").exists()
    assert [candidate["name"] for candidate in packet["candidates"]] == [
        "Duplicate Lead",
        "URN Lead",
    ]
    assert packet["candidates"][0]["text"] == (
        "Duplicate Lead Founder at Example Agency New York"
    )
    assert packet["candidates"][0]["public_profile_url"] == (
        "https://www.linkedin.com/in/duplicate-lead"
    )
    assert packet["candidates"][0]["send_blockers"] == []
    assert packet["candidates"][1]["search_url"]
    assert packet["candidates"][1]["send_blockers"] == []
    assert packet["candidates"][0]["links"][0]["text"] == "Duplicate Lead"
    plan = store.load_run().operator_plan()
    assert plan.action == "review-required"
    assert plan.review_checkpoint is not None
    assert str(plan.review_checkpoint.packet_id) == packet["packet_id"]
    assert plan.review_checkpoint.packet_path == str(review_path)
    assert plan.review_checkpoint.terminal is False

    with pytest.raises(RuntimeError, match="need review"):
        send_next(
            store,
            FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
            dry_run=False,
            allow_send=True,
        )

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "packet_id": packet["packet_id"],
                "decisions": [
                    {
                        "lead_key": packet["candidates"][0]["lead_key"],
                        "status": "approved",
                        "reason": "agency owner with delivery work",
                    },
                    {
                        "lead_key": packet["candidates"][1]["lead_key"],
                        "status": "skipped",
                        "reason": "not a fit",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    apply_output = apply_lead_review_decisions(store, decisions_path)

    ledger = store.load_lead_ledger()
    statuses = {record.name: record.status for record in ledger.leads.values()}
    skipped = [event for event in store.load_run().candidates if event.name == "URN Lead"]
    assert "applied 2 lead review decision(s)" in apply_output
    assert statuses == {
        "Duplicate Lead": LeadStatus.APPROVED,
        "URN Lead": LeadStatus.SKIPPED,
    }
    assert skipped[0].status == CandidateStatus.SKIPPED

    send_output = send_next(
        store,
        FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
        dry_run=False,
        allow_send=True,
        confirm_delay_ms=0,
    )

    assert "recorded pending-provisional" in send_output
    assert "send queued for final audit: Duplicate Lead" in send_output
    assert store.load_lead_ledger().leads[packet["candidates"][0]["lead_key"]].status == (
        LeadStatus.PENDING
    )


def test_lead_review_rejects_stale_and_incomplete_decision_artifacts(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=2, run_date=date(2026, 7, 10), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    review_path = tmp_path / "review.json"

    review_candidates(store, source="ASAP - Agency Owners Delivery", out=review_path)
    stale_packet = json.loads(review_path.read_text())
    review_candidates(store, source="ASAP - Agency Owners Delivery", out=review_path)
    current_packet = json.loads(review_path.read_text())

    assert stale_packet["packet_id"] != current_packet["packet_id"]
    stale_decisions_path = tmp_path / "stale-decisions.json"
    stale_decisions_path.write_text(
        json.dumps(
            {
                "packet_id": stale_packet["packet_id"],
                "decisions": [
                    {
                        "lead_key": candidate["lead_key"],
                        "status": "skipped",
                        "reason": "stale fixture decision",
                    }
                    for candidate in stale_packet["candidates"]
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="lead review packet mismatch"):
        apply_lead_review_decisions(store, stale_decisions_path)

    incomplete_decisions_path = tmp_path / "incomplete-decisions.json"
    incomplete_decisions_path.write_text(
        json.dumps(
            {
                "packet_id": current_packet["packet_id"],
                "decisions": [
                    {
                        "lead_key": current_packet["candidates"][0]["lead_key"],
                        "status": "skipped",
                        "reason": "only one fixture decision",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one decision for every candidate"):
        apply_lead_review_decisions(store, incomplete_decisions_path)

    run = store.load_run()
    assert run.lead_review_checkpoint is not None
    assert str(run.lead_review_checkpoint.packet_id) == current_packet["packet_id"]
    assert all(
        record.status == LeadStatus.NEW
        for record in store.load_lead_ledger().leads.values()
    )


def test_failed_send_can_top_up_through_second_review_packet_and_reach_done(
    tmp_path: Path,
) -> None:
    source = "ASAP - Agency Owners Delivery"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        source_names=[source],
        max_real_sends=1,
        run_date=date(2026, 7, 10),
        force=True,
        allow_fallback_sources=False,
    )
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    first_review_path = tmp_path / "first-review.json"
    review_candidates(store, source=source, out=first_review_path)
    first_packet = json.loads(first_review_path.read_text())
    first_decisions_path = tmp_path / "first-decisions.json"
    first_decisions_path.write_text(
        json.dumps(
            {
                "packet_id": first_packet["packet_id"],
                "decisions": [
                    {
                        "lead_key": candidate["lead_key"],
                        "status": "approved" if index == 0 else "skipped",
                        "reason": "first packet fixture decision",
                    }
                    for index, candidate in enumerate(first_packet["candidates"])
                ],
            }
        ),
        encoding="utf-8",
    )
    apply_lead_review_decisions(store, first_decisions_path)
    first_candidate = first_packet["candidates"][0]
    record_candidate(
        store,
        source=source,
        name=first_candidate["name"],
        profile_url=first_candidate["profile_url"],
        status=CandidateStatus.FAILED,
        note="first fixture send failed clearly and needs a top-up",
    )

    second_capture_path = tmp_path / "second-capture.json"
    _write_fake_artifact(
        second_capture_path,
        SalesNavCapture.model_validate(
            {
                "capturedAt": "2026-07-10T12:00:00Z",
                "source": source,
                "url": "https://www.linkedin.com/sales/search/people?page=3",
                "rawRowCount": 1,
                "outputRowCount": 1,
                "stateCounts": {"connectable": 1},
                "rows": [
                    {
                        "index": 4,
                        "name": "Second Packet Lead",
                        "profileUrl": "https://www.linkedin.com/sales/lead/second-packet",
                        "publicProfileUrl": "https://www.linkedin.com/in/second-packet-lead",
                        "menuState": "connectable",
                        "menuLabels": [{"text": "Connect"}],
                        "rowText": "Second Packet Lead Founder at Delivery Partners",
                    }
                ],
            }
        ),
    )
    import_capture_path(store, second_capture_path, only_connectable=True)
    second_review_path = tmp_path / "second-review.json"
    review_candidates(store, source=source, out=second_review_path)
    second_packet = json.loads(second_review_path.read_text())

    assert second_packet["packet_id"] != first_packet["packet_id"]
    assert [candidate["name"] for candidate in second_packet["candidates"]] == [
        "Second Packet Lead"
    ]
    second_decisions_path = tmp_path / "second-decisions.json"
    second_decisions_path.write_text(
        json.dumps(
            {
                "packet_id": second_packet["packet_id"],
                "decisions": [
                    {
                        "lead_key": second_packet["candidates"][0]["lead_key"],
                        "status": "approved",
                        "reason": "second packet fixture decision",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    apply_lead_review_decisions(store, second_decisions_path)
    second_candidate = second_packet["candidates"][0]
    record_candidate(
        store,
        source=source,
        name=second_candidate["name"],
        profile_url=second_candidate["profile_url"],
        status=CandidateStatus.PENDING,
        note="second verified fixture send",
    )

    finish_run(store)

    completed = store.load_run()
    assert completed.state == RunState.DONE
    assert completed.verified_count() == completed.target == 1
    assert completed.lead_review_checkpoint is None


def test_manual_durable_record_writes_send_ledger_summary(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)

    record_candidate(
        store,
        source="ASAP - Agency Owners Delivery",
        name="Manual Lead",
        profile_url="https://www.linkedin.com/in/manual-lead",
        status=CandidateStatus.PENDING,
        note="manual reconciliation",
    )

    event_date = store.load_send_ledger_entries()[0].attempted_at.date().isoformat()
    summary = network_sends_summary(store, date_arg=event_date, timezone_name="UTC")

    assert summary.durable_sent_count == 1
    assert summary.by_source == {"ASAP - Agency Owners Delivery": 1}
    assert summary.entries[0].name == "Manual Lead"
    assert summary.entries[0].durable is True


def test_record_send_result_from_path_commits_send_attempt_ledgers(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    result_path = FIXTURES / "send_pending.json"

    output = record_send_result_from_path(store, result_path)

    run = store.load_run()
    event = run.candidates[-1]
    entries = store.load_send_ledger_entries()
    lead_records = list(store.load_lead_ledger().leads.values())
    event_log = [
        json.loads(line) for line in store.event_path(run).read_text().splitlines()
    ]
    assert "recorded send result as pending-provisional" in output
    assert event.status == CandidateStatus.PENDING_PROVISIONAL
    assert entries[0].event_kind == "record-send-result"
    assert entries[0].result_path == str(result_path)
    assert entries[0].status == CandidateStatus.PENDING_PROVISIONAL
    assert lead_records[0].status == LeadStatus.PENDING
    assert event_log[-1]["kind"] == "record-send-result"
    assert event_log[-1]["payload"]["path"] == str(result_path)


def test_record_top_up_result_from_path_commits_top_up_event_kind(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    result_path = FIXTURES / "send_pending.json"

    output = record_top_up_result_from_path(store, result_path, note="audit shortfall")

    run = store.load_run()
    entries = store.load_send_ledger_entries()
    event_log = [
        json.loads(line) for line in store.event_path(run).read_text().splitlines()
    ]
    assert "recorded top-up result as pending-provisional" in output
    assert entries[0].event_kind == "record-top-up-result"
    assert entries[0].result_path == str(result_path)
    assert entries[0].status == CandidateStatus.PENDING_PROVISIONAL
    assert "audit shortfall" in (entries[0].reason or "")
    assert event_log[-1]["kind"] == "record-top-up-result"


def test_provisional_confirmation_collapses_send_ledger_to_durable_latest(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)

    send_next(
        store,
        FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
        dry_run=False,
        allow_send=True,
        confirm_delay_ms=0,
        confirm_out_dir=tmp_path / "confirm",
    )
    reconcile_audit(
        store,
        FixtureBrowserClient(),
        attempts=1,
        delay_ms=0,
    )

    entries = store.load_send_ledger_entries()
    event_date = entries[0].attempted_at.date().isoformat()
    summary = network_sends_summary(store, date_arg=event_date, timezone_name="UTC")

    assert {entry.status for entry in entries} == {
        CandidateStatus.PENDING_PROVISIONAL,
        CandidateStatus.PENDING,
    }
    assert summary.durable_sent_count == 1
    assert summary.provisional_count == 0
    assert summary.entries[0].status == CandidateStatus.PENDING


def test_send_guarded_commits_send_result_for_final_audit(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)

    output = send_guarded(
        store,
        FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
        dry_run=False,
        allow_send=True,
        single_pass=True,
        confirm_delay_ms=0,
        confirm_out_dir=tmp_path / "confirm",
    )

    run = store.load_run()
    entries = store.load_send_ledger_entries()
    event_log = [
        json.loads(line) for line in store.event_path(run).read_text().splitlines()
    ]
    assert "send queued for final audit: Duplicate Lead" in output
    assert [entry.event_kind for entry in entries] == ["record-send-result"]
    assert [entry.status for entry in entries] == [CandidateStatus.PENDING_PROVISIONAL]
    sent = next(candidate for candidate in run.candidates if candidate.name == "Duplicate Lead")
    assert sent.status == CandidateStatus.PENDING_PROVISIONAL
    event_kinds = [entry["kind"] for entry in event_log]
    assert "record-send-result" in event_kinds
    assert "confirm-send-result" not in event_kinds

    reconcile_output = reconcile_audit(
        store,
        FixtureBrowserClient(),
        attempts=1,
        delay_ms=0,
    )

    assert "audit-confirmed 1 send(s): Duplicate Lead" in reconcile_output
    assert store.load_run().verified_count() == 1


def test_send_ledger_history_sync_counts_confirmed_sends(tmp_path: Path) -> None:
    store = Store(tmp_path)
    run_id = str(uuid.uuid4())
    attempted_at = datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
    provisional = CandidateEvent(
        at=attempted_at,
        source="ASAP - Agency Owners Delivery",
        name="Synced Lead",
        profile_url="https://www.linkedin.com/in/synced-lead",
        status=CandidateStatus.PENDING_PROVISIONAL,
        note="salesnav-send-one saw immediate Connect - Pending",
    )
    confirmed = provisional.model_copy(
        update={
            "status": CandidateStatus.ACCEPTED,
            "note": (
                "salesnav-send-one saw immediate Connect - Pending; "
                "durable confirmation accepted"
            ),
        }
    )
    log_path = tmp_path / f"{run_id}.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-07-02T10:00:00+00:00",
                        "run_id": run_id,
                        "kind": "record-send-result",
                        "payload": {
                            "path": "/tmp/send.json",
                            "event": provisional.model_dump(mode="json"),
                        },
                    }
                ),
                json.dumps(
                    {
                        "at": "2026-07-02T10:01:00+00:00",
                        "run_id": run_id,
                        "kind": "confirm-send-result",
                        "payload": {
                            "out": "/tmp/confirm.json",
                            "event": confirmed.model_dump(mode="json"),
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = network_sends_summary(
        store,
        date_arg="2026-07-02",
        timezone_name="UTC",
        sync_history=True,
    )

    assert summary.synced_entries == 2
    assert summary.history_logs_scanned == 1
    assert summary.durable_sent_count == 1
    assert summary.entries[0].status == CandidateStatus.ACCEPTED


def test_lead_review_blocks_approval_without_public_profile_url_or_search_url(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    run = store.load_run()
    for observation in run.observations:
        observation.search_url = None
    store.save_run(run)
    ledger = store.load_lead_ledger()
    for record in ledger.leads.values():
        record.search_url = None
    store.save_lead_ledger(ledger)
    review_path = tmp_path / "review.json"
    review_candidates(store, source="ASAP - Agency Owners Delivery", out=review_path)
    packet = json.loads(review_path.read_text())
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "packet_id": packet["packet_id"],
                "decisions": [
                    {
                        "lead_key": candidate["lead_key"],
                        "status": "approved" if index == 0 else "skipped",
                        "reason": "looks relevant",
                    }
                    for index, candidate in enumerate(packet["candidates"])
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot approve Duplicate Lead"):
        apply_lead_review_decisions(store, decisions_path)

    ledger = store.load_lead_ledger()
    record = ledger.leads[packet["candidates"][0]["lead_key"]]
    record.status = LeadStatus.APPROVED
    ledger.leads[record.lead_key] = record
    store.save_lead_ledger(ledger)

    with pytest.raises(RuntimeError, match="public_profile_url is missing or invalid"):
        send_next(
            store,
            FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
            dry_run=False,
            allow_send=True,
        )


def test_send_next_uses_backfilled_public_profile_url(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    run = store.load_run()
    observation = run.observations[0]
    lead_key = lead_key_for_observation(observation)
    public_url = "https://www.linkedin.com/in/duplicate-lead"

    output = set_lead_public_profile_url(store, lead_key, public_url)

    assert "updated public profile URL for Duplicate Lead" in output
    ledger = store.load_lead_ledger()
    record = ledger.approve(lead_key, "fit")
    assert record.public_profile_url == public_url
    store.save_lead_ledger(ledger)
    run = store.load_run()
    run.observations[0].public_profile_url = None
    store.save_run(run)
    browser = CandidateCapturingBrowser()

    send_next(store, browser, dry_run=True, allow_send=False)

    assert browser.candidate is not None
    assert browser.candidate.public_profile_url == public_url


def test_send_guarded_uses_backfilled_public_profile_url(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    run = store.load_run()
    observation = run.observations[0]
    lead_key = lead_key_for_observation(observation)
    public_url = "https://www.linkedin.com/in/duplicate-lead"
    set_lead_public_profile_url(store, lead_key, public_url)
    ledger = store.load_lead_ledger()
    ledger.approve(lead_key, "fit")
    store.save_lead_ledger(ledger)
    run = store.load_run()
    run.observations[0].public_profile_url = None
    store.save_run(run)
    browser = CandidateCapturingBrowser()

    send_guarded(
        store,
        browser,
        dry_run=False,
        allow_send=True,
        single_pass=True,
        no_record=True,
    )

    assert browser.candidate is not None
    assert browser.candidate.public_profile_url == public_url


def test_retry_failed_lead_clears_failed_event_for_approved_lead(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)
    run = store.load_run()
    observation = run.observations[0]
    lead_key = lead_key_for_observation(observation)
    other_lead_key = lead_key_for_observation(run.observations[1])
    ledger = store.load_lead_ledger()
    ledger.skip(other_lead_key, "outside retry test")
    store.save_lead_ledger(ledger)
    run.candidates.append(
        CandidateEvent(
            source=observation.source,
            name=observation.name,
            profile_url=observation.profile_url,
            status=CandidateStatus.FAILED,
            note="durable confirmation unknown",
        )
    )
    store.save_run(run)

    with pytest.raises(RuntimeError, match="no approved connectable candidate"):
        send_next(store, CandidateCapturingBrowser(), dry_run=True, allow_send=False)

    output = retry_failed_lead(store, lead_key, "retry after public profile backfill")

    assert "cleared 1 failed candidate event" in output
    assert store.load_lead_ledger().leads[lead_key].status == LeadStatus.APPROVED
    assert all(
        event.status != CandidateStatus.FAILED for event in store.load_run().candidates
    )
    browser = CandidateCapturingBrowser()
    send_next(store, browser, dry_run=True, allow_send=False)
    assert browser.candidate is not None
    assert browser.candidate.name == observation.name


def test_confirmation_prefers_public_profile_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeLiveBrowserClient, "acceptance_status", "accepted")
    monkeypatch.setattr(FakeLiveBrowserClient, "audit_people_count", 100)
    monkeypatch.setattr(FakeLiveBrowserClient, "audit_recent_names", [])
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    event = CandidateEvent(
        source="ASAP - Agency Owners Delivery",
        name="Duplicate Lead",
        profile_url="https://www.linkedin.com/sales/lead/dup,SEARCH,y",
        public_profile_url="https://www.linkedin.com/in/duplicate-lead",
        status=CandidateStatus.PENDING_PROVISIONAL,
        note="pending",
    )
    run = store.load_run()
    run.candidates.append(event)
    store.save_run(run)
    browser = AcceptanceCandidateCapturingBrowser(tmp_path / "browser")

    confirm_provisional_send(store, browser, event, delay_ms=0, out_dir=tmp_path)

    assert browser.acceptance_candidates[0].profile_url == (
        "https://www.linkedin.com/in/duplicate-lead"
    )


def test_lead_ledger_suppression_preserves_blocked_status(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=2, run_date=date(2026, 6, 24), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    ledger = store.load_lead_ledger()
    urn_record = next(record for record in ledger.leads.values() if record.name == "URN Lead")
    ledger.block(urn_record.lead_key, "not a fit")
    store.save_lead_ledger(ledger)

    review_candidates(store, source="ASAP - Agency Owners Delivery", out=tmp_path / "review.json")

    updated = store.load_lead_ledger().leads[urn_record.lead_key]
    suppressed = [
        event
        for event in store.load_run().candidates
        if event.name == "URN Lead" and event.status == CandidateStatus.SKIPPED
    ]
    assert updated.status == LeadStatus.BLOCKED
    assert suppressed


def test_capture_import_skips_outreach_messaged_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outreach_store = OutreachStore(tmp_path / "outreach")
    outreach_store.save(
        OutreachState(
            leads=[
                OutreachLead(
                    id="outreach_dup",
                    source="outreach",
                    name="Duplicate Lead",
                    first_name="Duplicate",
                    lead_type=OutreachLeadType.AI_ADVISOR_IMPLEMENTATION_PARTNER,
                    status=OutreachLeadStatus.ELIGIBLE,
                    message_status=OutreachMessageStatus.SENT,
                    fit_score=80,
                    profile_url="https://www.linkedin.com/sales/lead/dup,NAME_SEARCH,token",
                )
            ]
        )
    )
    monkeypatch.setenv("LINKEDIN_TOOLS_RECRUITER_AGENCY_STATE_DIR", str(outreach_store.dir))

    store = Store(tmp_path / "network")
    start_run(store, target=22, run_date=date(2026, 6, 24), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")

    message = import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)

    run = store.load_run()
    skipped = [event for event in run.candidates if event.status == CandidateStatus.SKIPPED]
    assert "suppressed 1" in message
    assert len(skipped) == 1
    assert skipped[0].name == "Duplicate Lead"
    assert "cross-workflow suppression" in (skipped[0].note or "")
    next_candidate = run.next_connectable_observation()
    assert next_candidate is not None
    assert next_candidate.name == "URN Lead"


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
    _approve_all_observed_leads(store)
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
    audit_result = tmp_path / "top-up-audit.json"
    audit_result.write_text(
        json.dumps(
            {
                "peopleCount": 101,
                "recentNames": ["Top Up Candidate"],
                "loadedCount": 1,
                "invitations": [{"name": "Top Up Candidate", "rowIndex": 0}],
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
            "--fixture-audit-result",
            str(audit_result),
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


def test_report_surfaces_blocked_next_action_when_sources_exhausted(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    record_audit(store, 100, "starting count")
    run = store.load_run()
    for source in run.sources:
        source.exhausted = True
    store.save_run(run)

    report = render_report(store.load_run())

    assert "- State: `StartAudited`" in report
    assert (
        "- Next action: `blocked` (no connectable candidate and no available source)"
        in report
    )


def test_finish_error_names_current_reconcile_command(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 28), force=True)
    record_audit(store, 100, "starting count")

    with pytest.raises(RuntimeError, match="durable confirmed sends are 0/1"):
        finish_run(store)


def test_guarded_connection_send_preserves_real_send_gate(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)
    browser = FixtureBrowserClient(send_result=FIXTURES / "send_pending.json")

    with pytest.raises(RuntimeError, match="real guarded sends require --allow-send"):
        send_guarded(store, browser, dry_run=False, allow_send=False)

    output = send_next(store, browser, dry_run=False, allow_send=True, confirm_delay_ms=0)

    run = store.load_run()
    assert "recorded pending-provisional" in output
    assert "send queued for final audit: Duplicate Lead" in output
    assert run.verified_count() == 0
    assert run.real_send_attempt_count() == 1
    assert run.state == RunState.SENDING


def test_send_next_queues_provisional_for_final_audit(
    tmp_path: Path,
) -> None:
    FakeLiveBrowserClient.instances.clear()
    FakeLiveBrowserClient.acceptance_status = "connectable"
    FakeLiveBrowserClient.audit_people_count = 100
    FakeLiveBrowserClient.audit_recent_names = []
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 29), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)
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
    assert "send queued for final audit: Duplicate Lead" in output
    assert run.verified_count() == 0
    assert run.real_send_attempt_count() == 1
    assert run.state == RunState.SENDING
    sent = next(candidate for candidate in run.candidates if candidate.name == "Duplicate Lead")
    assert sent.status == CandidateStatus.PENDING_PROVISIONAL
    assert browser.calls == ["send:Duplicate Lead:dry=False:allow=True"]


def test_operator_plan_final_audit_after_target_send_attempts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 6), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)

    send_next(
        store,
        FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
        dry_run=False,
        allow_send=True,
        confirm_delay_ms=0,
    )

    run = store.load_run()
    assert run.verified_count() == 0
    assert run.real_send_attempt_count() == 1
    assert run.operator_plan().action == "final-audit"


def test_operator_plan_replaces_proven_failed_send_before_final_audit(tmp_path: Path) -> None:
    store = Store(tmp_path)
    source = "ASAP - Agency Owners Delivery"
    start_run(store, target=2, max_real_sends=2, run_date=date(2026, 7, 7), force=True)
    _make_source_current(store, source)
    run = store.load_run()
    run.candidates.extend(
        [
            CandidateEvent(
                source=source,
                name="Confirmed Lead",
                profile_url="https://www.linkedin.com/sales/lead/confirmed",
                status=CandidateStatus.PENDING,
                note="sent-page audit confirmed pending",
            ),
            CandidateEvent(
                source=source,
                name="Failed Lead",
                profile_url="https://www.linkedin.com/sales/lead/failed",
                status=CandidateStatus.FAILED,
                note=(
                    "salesnav-send-one saw immediate Connect - Pending; "
                    "sent-page audit did not confirm pending; previous_status=pending-provisional"
                ),
            ),
        ]
    )
    replacement = CandidateObservation(
        source=source,
        index=3,
        name="Replacement Lead",
        profile_url="https://www.linkedin.com/sales/lead/replacement",
        menu_state="connectable",
    )
    run.observations.append(replacement)
    store.save_run(run)
    ledger = store.load_lead_ledger()
    ledger.upsert_observation(replacement)
    ledger.approve(lead_key_for_observation(replacement), "replacement approved")
    store.save_lead_ledger(ledger)

    run = store.load_run()
    assert run.real_send_attempt_count() == 2
    assert run.active_send_count() == 1
    assert run.source_active_send_count(source) == 1
    plan = run.operator_plan()

    assert plan.action == "send-candidate"
    assert plan.name == "Replacement Lead"
    assert plan.real_send_capacity_remaining == 1

    run.candidates.append(
        CandidateEvent(
            source=source,
            name="Replacement Lead",
            profile_url="https://www.linkedin.com/sales/lead/replacement",
            status=CandidateStatus.PENDING_PROVISIONAL,
            note="send queued for final audit",
        )
    )
    store.save_run(run)

    assert store.load_run().operator_plan().action == "final-audit"


def test_send_next_final_audit_confirms_sent_page_before_public_profile(
    tmp_path: Path,
) -> None:
    FakeLiveBrowserClient.instances.clear()
    FakeLiveBrowserClient.acceptance_status = "connectable"
    FakeLiveBrowserClient.audit_people_count = 101
    FakeLiveBrowserClient.audit_recent_names = ["Duplicate Lead"]
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 6), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)
    browser = FakeLiveBrowserClient(out_dir=tmp_path / "browser")

    output = send_next(
        store,
        browser,
        dry_run=False,
        allow_send=True,
        confirm_delay_ms=0,
    )

    assert "send queued for final audit: Duplicate Lead" in output
    assert browser.calls == ["send:Duplicate Lead:dry=False:allow=True"]
    reconcile_output = reconcile_audit(store, browser, attempts=1, delay_ms=0)

    assert "audit-confirmed 1 send(s): Duplicate Lead" in reconcile_output
    assert browser.calls == [
        "send:Duplicate Lead:dry=False:allow=True",
        "audit:load_more=2",
    ]
    sent = next(
        candidate for candidate in store.load_run().candidates if candidate.name == "Duplicate Lead"
    )
    assert sent.status == CandidateStatus.PENDING


def test_send_guarded_keeps_provisional_until_final_audit(
    tmp_path: Path,
) -> None:
    FakeLiveBrowserClient.instances.clear()
    FakeLiveBrowserClient.acceptance_status = "connectable"
    FakeLiveBrowserClient.audit_people_count = 100
    FakeLiveBrowserClient.audit_recent_names = []
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 3), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)
    browser = FakeLiveBrowserClient(out_dir=tmp_path / "browser")

    output = send_guarded(
        store,
        browser,
        dry_run=False,
        allow_send=True,
        single_pass=True,
        confirm_delay_ms=0,
    )

    assert "send queued for final audit: Duplicate Lead" in output
    run = store.load_run()
    assert run.state == RunState.SENDING
    sent = next(candidate for candidate in run.candidates if candidate.name == "Duplicate Lead")
    assert sent.status == CandidateStatus.PENDING_PROVISIONAL


def test_reconcile_audit_promotes_recent_name_false_negatives(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=3, run_date=date(2026, 7, 3), force=True)
    record_audit(store, 100, "starting count")
    run = store.load_run()
    run.candidates.extend(
        [
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Provisional Lead",
                profile_url="https://www.linkedin.com/sales/lead/provisional",
                status=CandidateStatus.PENDING_PROVISIONAL,
                note="salesnav-send-one saw immediate Connect - Pending",
            ),
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Reverted Lead",
                profile_url="https://www.linkedin.com/sales/lead/reverted",
                status=CandidateStatus.REVERTED_CONNECT,
                note="connectable on public profile",
            ),
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Failed Lead",
                profile_url="https://www.linkedin.com/sales/lead/failed",
                status=CandidateStatus.FAILED,
                note="salesnav-send-one saw immediate Connect - Pending; unknown",
            ),
        ]
    )
    run.state = RunState.NEEDS_REAUDIT
    store.save_run(run)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "peopleCount": 103,
                "recentNames": ["Failed Lead", "Reverted Lead", "Provisional Lead"],
            }
        ),
        encoding="utf-8",
    )

    output = reconcile_audit(
        store,
        FixtureBrowserClient(audit=audit_path),
        attempts=1,
        delay_ms=0,
    )

    run = store.load_run()
    assert "audit-confirmed 3 send(s)" in output
    assert run.verified_count() == 3
    assert run.state == RunState.FINAL_RECONCILE
    assert {candidate.status for candidate in run.candidates} == {CandidateStatus.PENDING}
    entries = store.load_send_ledger_entries()
    assert len(entries) == 3
    assert all(entry.status == CandidateStatus.PENDING for entry in entries)


def test_reconcile_audit_matches_structured_public_profile_before_name(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=2, run_date=date(2026, 7, 6), force=True)
    record_audit(store, 100, "starting count")
    run = store.load_run()
    run.candidates.extend(
        [
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Duplicate Name",
                profile_url="https://www.linkedin.com/sales/lead/wrong",
                public_profile_url="https://www.linkedin.com/in/wrong-person",
                status=CandidateStatus.PENDING,
                note="already confirmed from prior audit",
            ),
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Duplicate Name",
                profile_url="https://www.linkedin.com/sales/lead/right",
                public_profile_url="https://www.linkedin.com/in/right-person",
                status=CandidateStatus.PENDING_PROVISIONAL,
                note="salesnav-send-one saw immediate Connect - Pending",
            ),
        ]
    )
    run.state = RunState.NEEDS_REAUDIT
    store.save_run(run)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "peopleCount": 101,
                "recentNames": ["Duplicate Name"],
                "loadedCount": 1,
                "invitations": [
                    {
                        "name": "Duplicate Name",
                        "publicProfileUrl": "https://www.linkedin.com/in/right-person",
                        "publicIdentifier": "right-person",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = reconcile_audit(
        store,
        FixtureBrowserClient(audit=audit_path),
        attempts=1,
        delay_ms=0,
    )

    run = store.load_run()
    assert "audit-confirmed 1 send(s): Duplicate Name" in output
    assert run.candidates[0].note == "already confirmed from prior audit"
    assert "sent-page audit confirmed pending" in (run.candidates[1].note or "")
    assert run.verified_count() == 2


def test_reconcile_audit_closes_stale_unconfirmed_provisional_send(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=3, run_date=date(2026, 7, 4), force=True)
    record_audit(store, 100, "starting count")
    run = store.load_run()
    run.candidates.extend(
        [
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Confirmed Lead",
                profile_url="https://www.linkedin.com/sales/lead/confirmed",
                status=CandidateStatus.PENDING,
                note="sent-page audit confirmed pending",
            ),
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Stale Provisional Lead",
                profile_url="https://www.linkedin.com/sales/lead/stale-provisional",
                status=CandidateStatus.PENDING_PROVISIONAL,
                note="salesnav-send-one saw immediate Connect - Pending; durable check required",
            ),
        ]
    )
    store.save_run(run)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "peopleCount": 101,
                "recentNames": ["Confirmed Lead"],
                "loadedCount": 2,
            }
        ),
        encoding="utf-8",
    )

    output = reconcile_audit(
        store,
        FixtureBrowserClient(audit=audit_path),
        attempts=1,
        delay_ms=0,
    )

    run = store.load_run()
    assert "closed 1 unconfirmed provisional send(s): Stale Provisional Lead" in output
    assert run.verified_count() == 1
    assert run.provisional_count() == 0
    assert run.candidates[-1].status == CandidateStatus.FAILED
    assert "sent-page audit did not confirm pending" in (run.candidates[-1].note or "")
    entries = store.load_send_ledger_entries()
    assert len(entries) == 1
    assert entries[0].name == "Stale Provisional Lead"
    assert entries[0].status == CandidateStatus.FAILED
    assert entries[0].durable is False
    lead_records = list(store.load_lead_ledger().leads.values())
    assert lead_records[0].status == LeadStatus.BLOCKED


def test_reconcile_audit_keeps_provisional_when_audit_is_too_shallow(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=3, run_date=date(2026, 7, 6), force=True)
    record_audit(store, 100, "starting count")
    run = store.load_run()
    run.candidates.extend(
        [
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Confirmed Lead",
                profile_url="https://www.linkedin.com/sales/lead/confirmed",
                status=CandidateStatus.PENDING,
                note="sent-page audit confirmed pending",
            ),
            CandidateEvent(
                source="ASAP - Agency Owners Delivery",
                name="Stale Provisional Lead",
                profile_url="https://www.linkedin.com/sales/lead/stale-provisional",
                status=CandidateStatus.PENDING_PROVISIONAL,
                note="salesnav-send-one saw immediate Connect - Pending; durable check required",
            ),
        ]
    )
    run.state = RunState.NEEDS_REAUDIT
    store.save_run(run)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "peopleCount": 101,
                "recentNames": ["Confirmed Lead"],
                "loadedCount": 1,
            }
        ),
        encoding="utf-8",
    )

    output = reconcile_audit(
        store,
        FixtureBrowserClient(audit=audit_path),
        attempts=1,
        delay_ms=0,
    )

    run = store.load_run()
    assert "loaded 1/2 recent invitation row(s)" in output
    assert run.provisional_count() == 1
    assert run.candidates[-1].status == CandidateStatus.PENDING_PROVISIONAL


def test_reconcile_audit_recovers_approved_observation_without_send_event(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 3), force=True)
    record_audit(store, 100, "starting count")
    run = store.load_run()
    run.observations.append(
        CandidateObservation(
            source="ASAP - Agency Owners Delivery",
            index=7,
            name="Interrupted Lead",
            profile_url="https://www.linkedin.com/sales/lead/interrupted",
            menu_state="connectable",
        )
    )
    store.save_run(run)
    _approve_all_observed_leads(store)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps({"peopleCount": 101, "recentNames": ["Interrupted Lead"]}),
        encoding="utf-8",
    )

    output = reconcile_audit(
        store,
        FixtureBrowserClient(audit=audit_path),
        attempts=1,
        delay_ms=0,
    )

    run = store.load_run()
    assert "audit-confirmed 1 send(s): Interrupted Lead" in output
    assert run.verified_count() == 1
    assert run.candidates[-1].status == CandidateStatus.PENDING
    assert "no recorded send event" in (run.candidates[-1].note or "")
    assert store.load_send_ledger_entries()[-1].durable is True


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
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)
    send_next(
        store,
        FixtureBrowserClient(send_result=FIXTURES / "send_pending.json"),
        dry_run=False,
        allow_send=True,
        confirm_delay_ms=0,
    )
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "peopleCount": 101,
                "recentNames": ["Duplicate Lead"],
                "loadedCount": 1,
                "invitations": [
                    {
                        "name": "Duplicate Lead",
                        "publicProfileUrl": "https://www.linkedin.com/in/duplicate-lead",
                        "publicIdentifier": "duplicate-lead",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    import_audit(store, audit_path)

    report = finish_run(store)

    assert "State: `Done`" in report
    ledger = store.load_acceptance_ledger()
    assert len(ledger.invitations) == 1
    assert ledger.invitations[0].name == "Duplicate Lead"


def test_force_finished_incomplete_report_is_explicit(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=2, run_date=date(2026, 7, 2), force=True)
    record_audit(store, 100)

    report = finish_run(store, force=True)

    assert "Completion status: run is closed incomplete" in report
    assert "force-finished incomplete run with durable confirmed sends 0/2" in report


def test_record_send_result_preserves_public_profile_url(tmp_path: Path) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 7, 2), force=True)
    run = store.load_run()
    result = SalesNavSendResult.model_validate(
        {
            "candidate": {
                "source": "ASAP - Agency Owners Delivery",
                "name": "Public Lead",
                "profileUrl": "https://www.linkedin.com/sales/lead/public-lead,NAME_SEARCH,x",
            },
            "status": "pending-provisional",
            "publicProfileUrl": "https://www.linkedin.com/in/public-lead",
        }
    )

    event = record_send_result(run, result, "/tmp/send-result.json")

    assert event.profile_url == "https://www.linkedin.com/sales/lead/public-lead,NAME_SEARCH,x"
    assert event.public_profile_url == "https://www.linkedin.com/in/public-lead"


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
    with pytest.raises(RuntimeError, match="final pending count did not match withdrawals"):
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


def test_pending_cleanup_parses_sub_day_ages_as_fresh() -> None:
    assert parse_sent_age_days("Sent 6 hours ago") == 0
    assert parse_sent_age_days("Sent 23 minutes ago") == 0
    assert parse_sent_age_days("Sent today") == 0
    assert parse_sent_age_days("Sent yesterday") == 1
    assert parse_sent_age_days("Sent 1 week ago") == 7
    assert parse_sent_age_days("Sent 2 weeks ago") == 14
    assert parse_sent_age_months("Sent 6 hours ago") == 0


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
        elif script.name == "pending_withdraw_loaded.js":
            _write_fake_artifact(
                Path(config["out"]),
                {
                    "status": "dry-run-withdrawable",
                    "results": [
                        {
                            "candidate": {
                                "name": candidate.name,
                                "profileUrl": candidate.profile_url,
                                "ageText": candidate.age_text,
                            },
                            "status": "dry-run-withdrawable",
                            "detail": {"source": "loaded-page-bottom"},
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

    client._run_script = fake_run_script  # type: ignore[method-assign, assignment]

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
    with pytest.raises(RuntimeError, match="real withdrawal requires allow_withdraw"):
        client.withdraw_loaded_pending(
            limit=1,
            threshold_days=14,
            dry_run=False,
            allow_withdraw=False,
        )
    batch, batch_path = client.withdraw_loaded_pending(
        limit=1,
        threshold_days=14,
        dry_run=True,
        allow_withdraw=False,
    )

    assert capture.rows[0].name == "Stale Invite"
    assert Path(capture_path).exists()
    assert withdraw.status == "dry-run-withdrawable"
    assert Path(withdraw_path).exists()
    assert batch.results[0].status == "dry-run-withdrawable"
    assert Path(batch_path).exists()
    assert [call[0].name for call in calls] == [
        "pending_capture.js",
        "pending_withdraw.js",
        "pending_withdraw_loaded.js",
    ]
    assert calls[0][1]["loadMore"] == 3
    assert calls[1][1]["candidate"]["name"] == "Stale Invite"
    assert calls[1][1]["allowWithdraw"] is False
    assert calls[2][1]["thresholdDays"] == 14


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
    configs_by_script: dict[str, dict[str, Any]] = {}

    def fake_run_script(script: Path, config: dict[str, Any]) -> None:
        scripts.append(script.name)
        configs_by_script[script.name] = config
        out = Path(config["out"])
        payload: dict[str, Any]
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
    assert configs_by_script["salesnav_saved_searches.js"]["navigationTimeoutMs"] == 120000
    assert scripts == [
        "salesnav_send.js",
        "salesnav_capture.js",
        "salesnav_audit.js",
        "salesnav_saved_searches.js",
    ]


def test_salesnav_send_script_waits_for_public_profile_send_button() -> None:
    script = (
        Path("apps/network_automation/playwriter_scripts/salesnav_send.js")
        .read_text(encoding="utf-8")
    )

    assert "const SEND_INVITATION_LABEL" in script
    assert "Date.now() + 6000" in script
    assert "Send without a note" in script
    assert "observedButtons" in script


def test_cli_namespace_runs_network_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert network_main(["--state-dir", str(tmp_path), "start", "--target", "1", "--force"]) == 0
    assert network_main(["--state-dir", str(tmp_path), "plan", "--json"]) == 0
    output = capsys.readouterr().out
    assert "started run" in output
    assert '"action": "capture-source"' in output


def test_cli_send_next_uses_live_browser_when_fixture_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    _approve_all_observed_leads(store)
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
    output = capsys.readouterr().out
    assert "sending candidate: Duplicate Lead" in output
    assert "send status: pending" in output
    assert FakeLiveBrowserClient.instances[-1].out_dir == out_dir
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "send:Duplicate Lead:dry=False:allow=True"
    ]
    run = store.load_run()
    assert run.real_send_attempt_count() == 1
    assert run.verified_count() == 0


def test_cli_pending_withdraw_next_uses_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
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
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].out_dir == out_dir
    assert FakeLiveBrowserClient.instances[-1].calls == [
        "withdraw:Stale Invite:dry=False:allow=True"
    ]
    assert store.load_pending().withdrawn_count() == 1


def test_cli_saved_searches_uses_live_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
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
    assert payload["searches"][0]["name"] == "ASAP - Contract Recruiters Staffing"


def test_cli_passes_explicit_playwriter_session_to_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "saved-searches",
            "--session",
            "18",
            "--out",
            str(tmp_path / "saved-searches.json"),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[-1].session == "18"


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
        "withdraw-loaded:limit=1:threshold=14:dry=True:allow=False",
        "withdraw-loaded:limit=1:threshold=14:dry=False:allow=True",
        "audit:load_more=0",
    ]
    run = store.load_pending()
    assert run.start_audit == 101
    assert run.latest_audit == 101
    assert run.withdrawn_count() == 1


def test_cli_pending_run_session_explains_finish_mismatch_after_clear_post_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)
    capture_calls = 0

    def fake_capture(
        self: FakeLiveBrowserClient, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        nonlocal capture_calls
        capture_calls += 1
        self.calls.append(f"pending-capture:load_more={load_more}:threshold={threshold_days}")
        row = (
            {
                "index": 0,
                "name": "Stale Invite",
                "profileUrl": "https://www.linkedin.com/in/stale",
                "ageText": "Sent 3 weeks ago",
                "ageDays": 21,
                "eligible": True,
                "rowText": "Stale Invite Sent 3 weeks ago Withdraw",
            }
            if capture_calls == 1
            else {
                "index": 0,
                "name": "Fresh Invite",
                "profileUrl": "https://www.linkedin.com/in/fresh",
                "ageText": "Sent 1 week ago",
                "ageDays": 7,
                "eligible": False,
                "rowText": "Fresh Invite Sent 1 week ago Withdraw",
            }
        )
        artifact = PendingCapture.model_validate(
            {
                "capturedAt": "2026-07-05T15:30:00Z",
                "visibleWithdrawCount": 1,
                "rows": [row],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    monkeypatch.setattr(FakeLiveBrowserClient, "capture_pending_invitations", fake_capture)
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=2, threshold_days=14, force=True)

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
            str(tmp_path / "pending-capture.json"),
            "--withdraw-limit",
            "1",
            "--allow-withdraw",
            "--finish",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Finished with count warning" in captured.out
    assert "stale invitations were cleared" in captured.out
    assert "verified 1 withdrawn; expected People (100)" in captured.out
    assert "with 0 stale invitations left" in captured.out
    assert FakeLiveBrowserClient.instances[0].calls == [
        "audit:load_more=0",
        "pending-capture:load_more=3:threshold=14",
        "withdraw-loaded:limit=1:threshold=14:dry=True:allow=False",
        "withdraw-loaded:limit=1:threshold=14:dry=False:allow=True",
        "audit:load_more=0",
        "pending-capture:load_more=3:threshold=14",
    ]
    run = store.load_pending()
    assert run.state == PendingCleanupState.FINAL_RECONCILE
    assert run.withdrawn_count() == 1
    assert any("Finished with count warning" in note for note in run.notes)


def test_cli_pending_run_session_explains_missing_visible_ages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)

    def fake_capture(
        self: FakeLiveBrowserClient, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        self.calls.append(f"pending-capture:load_more={load_more}:threshold={threshold_days}")
        artifact = PendingCapture.model_validate(
            {
                "capturedAt": "2026-07-05T07:02:36Z",
                "visibleWithdrawCount": 1,
                "warnings": [
                    "visible withdraw links were found, but no invitation age text was readable"
                ],
                "rows": [
                    {
                        "index": 0,
                        "name": "Unreadable Age",
                        "profileUrl": "https://www.linkedin.com/in/unreadable",
                        "ageText": None,
                        "eligible": False,
                        "rowText": "Unreadable Age Withdraw",
                    }
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    monkeypatch.setattr(FakeLiveBrowserClient, "capture_pending_invitations", fake_capture)
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=2, threshold_days=14, force=True)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "pending-cleanup",
            "run-session",
            "--out",
            str(tmp_path / "pending-capture.json"),
            "--allow-withdraw",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "visible withdraw links 1" in out
    assert "sent ages could not be read safely" in out
    assert FakeLiveBrowserClient.instances[0].calls == [
        "audit:load_more=0",
        "pending-capture:load_more=40:threshold=14",
    ]
    assert store.load_pending().withdrawn_count() == 0


def test_cli_pending_run_session_ignores_old_missing_ages_after_clean_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)

    def fake_capture(
        self: FakeLiveBrowserClient, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        self.calls.append(f"pending-capture:load_more={load_more}:threshold={threshold_days}")
        artifact = PendingCapture.model_validate(
            {
                "capturedAt": "2026-07-05T07:30:00Z",
                "visibleWithdrawCount": 1,
                "rows": [
                    {
                        "index": 0,
                        "name": "Fresh Invite",
                        "ageText": "Sent 6 hours ago",
                        "ageDays": 0,
                        "eligible": False,
                        "rowText": "Fresh Invite Sent 6 hours ago Withdraw",
                    }
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    monkeypatch.setattr(FakeLiveBrowserClient, "capture_pending_invitations", fake_capture)
    store = Store(tmp_path)
    pending_cleanup_start(store, max_withdrawals=2, threshold_days=14, force=True)
    old_missing_age = tmp_path / "old-missing-age.json"
    _write_fake_artifact(
        old_missing_age,
        {
            "capturedAt": "2026-07-05T07:00:00Z",
            "rows": [
                {
                    "index": 0,
                    "name": "Old Missing",
                    "ageText": None,
                    "eligible": False,
                    "rowText": "Old Missing Withdraw",
                }
            ],
        },
    )
    pending_cleanup_import_capture(store, old_missing_age)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "pending-cleanup",
            "run-session",
            "--out",
            str(tmp_path / "pending-capture.json"),
            "--allow-withdraw",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "sent ages could not be read safely" not in out
    assert "stopped: capture imported no eligible stale invitation" in out
    assert store.load_pending().withdrawn_count() == 0


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
            "--per-source-target",
            "1",
            "--source",
            "ASAP - Agency Owners Delivery",
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
        "audit:load_more=2",
        "saved-searches:https://www.linkedin.com/sales/search/people",
        (
            "capture:ASAP - Agency Owners Delivery:pages=3:limit=0:only=True:"
            "url=https://www.linkedin.com/sales/search/people?savedSearchId=abc"
        ),
    ]
    store = Store(tmp_path)
    assert store.load_run().verified_count() == 0
    review_packet = out_dir / "lead-review-candidates.json"
    assert review_packet.exists()
    assert json.loads(review_packet.read_text())["candidates"][0]["name"] == "Duplicate Lead"


def test_cli_network_run_session_uses_existing_saved_searches_on_new_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    saved_searches = tmp_path / "saved-searches.json"
    _write_fake_artifact(
        saved_searches,
        SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "url": "https://www.linkedin.com/sales/search/people",
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    }
                ],
            }
        ),
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--per-source-target",
            "1",
            "--source",
            "ASAP - Agency Owners Delivery",
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
            str(tmp_path / "network-session"),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[0].calls == [
        "audit:load_more=2",
        (
            "capture:ASAP - Agency Owners Delivery:pages=3:limit=0:only=True:"
            "url=https://www.linkedin.com/sales/search/people?savedSearchId=abc"
        ),
    ]


def test_cli_network_run_session_can_refresh_existing_saved_searches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    saved_searches = tmp_path / "saved-searches.json"
    _write_fake_artifact(
        saved_searches,
        SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-20T12:00:00Z",
                "url": "https://www.linkedin.com/sales/search/people",
                "searches": [
                    {
                        "savedSearchId": "stale",
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=stale",
                    }
                ],
            }
        ),
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--per-source-target",
            "1",
            "--source",
            "ASAP - Agency Owners Delivery",
            "--max-real-sends",
            "1",
            "--force",
            "--saved-searches",
            str(saved_searches),
            "--refresh-saved-searches",
            "--allow-send",
            "--audit-attempts",
            "1",
            "--audit-delay-ms",
            "0",
            "--out-dir",
            str(tmp_path / "network-session"),
        ]
    )

    assert exit_code == 0
    assert FakeLiveBrowserClient.instances[0].calls == [
        "audit:load_more=2",
        "saved-searches:https://www.linkedin.com/sales/search/people",
        (
            "capture:ASAP - Agency Owners Delivery:pages=3:limit=0:only=True:"
            "url=https://www.linkedin.com/sales/search/people?savedSearchId=abc"
        ),
    ]


def test_cli_network_run_session_resume_sends_after_review_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    saved_searches = tmp_path / "saved-searches.json"
    out_dir = tmp_path / "network-session"

    first_exit = network_main(
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

    assert first_exit == 0
    packet_path = out_dir / "lead-review-candidates.json"
    packet = json.loads(packet_path.read_text())
    store = Store(tmp_path)
    ledger = store.load_lead_ledger()
    ledger.leads[packet["candidates"][0]["lead_key"]].public_profile_url = (
        "https://www.linkedin.com/in/duplicate-lead"
    )
    store.save_lead_ledger(ledger)
    decisions_path = out_dir / "lead-review-candidates-decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "packet_id": packet["packet_id"],
                "decisions": [
                    {
                        "lead_key": candidate["lead_key"],
                        "status": "approved" if index == 0 else "skipped",
                        "reason": "fixture decision",
                    }
                    for index, candidate in enumerate(packet["candidates"])
                ]
            }
        ),
        encoding="utf-8",
    )
    assert "applied" in apply_lead_review_decisions(store, decisions_path)
    FakeLiveBrowserClient.instances.clear()

    resume_exit = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--resume",
            "--saved-searches",
            str(saved_searches),
            "--allow-send",
            "--audit-attempts",
            "1",
            "--audit-delay-ms",
            "0",
            "--out-dir",
            str(out_dir),
            "--max-steps",
            "4",
        ]
    )

    assert resume_exit == 0
    calls = FakeLiveBrowserClient.instances[0].calls
    assert calls[0] == "send:Duplicate Lead:dry=False:allow=True"
    assert "audit:load_more=2" in calls
    assert Store(tmp_path).load_run().verified_count() == 1


def test_cli_network_run_session_blocks_when_targeted_saved_search_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_fake_live_browser(monkeypatch)

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--target",
            "30",
            "--max-real-sends",
            "30",
            "--force",
            "--saved-searches",
            str(tmp_path / "saved-searches.json"),
            "--allow-send",
            "--audit-attempts",
            "1",
            "--audit-delay-ms",
            "0",
            "--out-dir",
            str(tmp_path / "network-session"),
        ]
    )

    captured = capsys.readouterr()
    run = Store(tmp_path).load_run()
    assert exit_code == 1
    assert run.state == RunState.BLOCKED
    assert "saved-search coverage missing" in captured.err
    assert "ASAP - Strategy Consultants Implementation Partners" in captured.err


def test_cli_review_candidates_json_reports_decision_and_next_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path)
    start_run(store, target=1, run_date=date(2026, 6, 24), force=True)
    _make_source_current(store, "ASAP - Agency Owners Delivery")
    import_capture_path(store, FIXTURES / "capture.json", only_connectable=True)
    review_out = tmp_path / "review.json"

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "review-candidates",
            "--out",
            str(review_out),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["packet_path"] == str(review_out)
    assert payload["action"] == "review-required"
    assert payload["terminal"] is False
    assert payload["packet_id"] == json.loads(review_out.read_text())["packet_id"]
    assert payload["markdown_path"] == str(review_out.with_suffix(".md"))
    assert payload["decisions_path"] == str(tmp_path / "review-decisions.json")
    assert "apply-lead-decisions" in payload["apply_command"]
    assert "run-session --resume" in payload["send_command"]
    assert "--max-real-sends 1" in payload["send_command"]
    assert payload["send_command"].endswith("--allow-send --finish")
    assert payload["next_commands"][0] == f"edit decisions: {tmp_path / 'review-decisions.json'}"


def test_cli_network_run_session_seeds_capture_from_durable_source_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    store = Store(tmp_path)
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                "ASAP - Contract Recruiters Staffing": SourceScanProgress(
                    source="ASAP - Contract Recruiters Staffing",
                    saved_search_id="def",
                    saved_search_url="https://www.linkedin.com/sales/search/people?savedSearchId=def",
                    next_url="https://www.linkedin.com/sales/search/people?page=4&savedSearchId=def",
                    last_scanned_url="https://www.linkedin.com/sales/search/people?page=3&savedSearchId=def",
                )
            }
        )
    )

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
            str(tmp_path / "saved-searches.json"),
            "--allow-send",
            "--audit-attempts",
            "1",
            "--audit-delay-ms",
            "0",
            "--out-dir",
            str(tmp_path / "network-session"),
        ]
    )

    assert exit_code == 0
    assert any(
        call.endswith(
            "url=https://www.linkedin.com/sales/search/people?page=4&savedSearchId=def"
        )
        for call in FakeLiveBrowserClient.instances[0].calls
        if call.startswith("capture:ASAP - Contract Recruiters Staffing")
    )
    run = store.load_run()
    assert any("seeded source progress" in note for note in run.notes)


def test_cli_network_run_session_resume_seeds_existing_saved_searches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_live_browser(monkeypatch)
    source = "ASAP - Agency Owners Delivery"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 3),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.state = RunState.SENDING
    run.sources[0].exhausted = True
    store.save_run(run)
    page_url = "https://www.linkedin.com/sales/search/people?page=2&savedSearchId=abc"
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                source: SourceScanProgress(
                    source=source,
                    saved_search_id="abc",
                    saved_search_url="https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    next_url=page_url,
                    last_scanned_url=page_url,
                    end_of_results=False,
                    cursor_status=SourceCursorStatus.STALLED_NAVIGATION.value,
                )
            }
        )
    )
    saved_searches = tmp_path / "saved-searches.json"
    _write_fake_artifact(
        saved_searches,
        SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-07-03T12:00:00Z",
                "url": "https://www.linkedin.com/sales/search/people",
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": source,
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    }
                ],
            }
        ),
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--resume",
            "--saved-searches",
            str(saved_searches),
            "--no-fallback",
            "--allow-send",
            "--max-steps",
            "1",
            "--out-dir",
            str(tmp_path / "network-session"),
        ]
    )

    assert exit_code == 0
    run = store.load_run()
    assert run.sources[0].exhausted is False
    assert any("seeded source progress" in note for note in run.notes)
    assert any(
        call.startswith(f"capture:{source}") and call.endswith(f"url={page_url}")
        for call in FakeLiveBrowserClient.instances[0].calls
    )


def test_reset_source_progress_reopens_active_source(tmp_path: Path) -> None:
    source = "ASAP - Vertical Proof Buyers"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 2),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.sources[0].exhausted = True
    run.capture_cursors[source] = SourceCaptureCursor(
        source=source,
        resume_url="https://www.linkedin.com/sales/search/people?page=2",
    )
    store.save_run(run)
    store.save_source_progress(
        SourceScanProgressLedger(
            sources={
                source: SourceScanProgress(
                    source=source,
                    saved_search_id="1975836745",
                    saved_search_url="https://www.linkedin.com/sales/search/people?savedSearchId=1975836745",
                    end_of_results=True,
                )
            }
        )
    )

    output = reset_source_progress(store, [source])

    assert output == "source progress reset; removed=1; missing=0; active_sources_reopened=1"
    assert source not in store.load_source_progress().sources
    reopened = store.load_run()
    assert reopened.sources[0].exhausted is False
    assert source not in reopened.capture_cursors
    assert "reset source progress: ASAP - Vertical Proof Buyers" in reopened.notes


def test_cli_network_run_session_defers_stalled_zero_import_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    StalledCursorBrowserClient.instances.clear()
    monkeypatch.setattr(network_cli, "PlaywriterBrowserClient", StalledCursorBrowserClient)
    source = "ASAP - Agency Owners Delivery"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 3),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.state = RunState.SENDING
    run.observations.append(
        CandidateObservation(
            source=source,
            index=1,
            name="Duplicate Lead",
            profile_url="https://www.linkedin.com/sales/lead/dup,SEARCH,y",
            menu_state="connectable",
        )
    )
    store.save_run(run)
    record_candidate(
        store,
        source=source,
        name="Duplicate Lead",
        profile_url="https://www.linkedin.com/sales/lead/dup,SEARCH,y",
        status=CandidateStatus.SKIPPED,
    )
    saved_searches = tmp_path / "saved-searches.json"
    _write_fake_artifact(
        saved_searches,
        SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-07-03T12:00:00Z",
                "url": "https://www.linkedin.com/sales/search/people",
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": source,
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    }
                ],
            }
        ),
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--resume",
            "--saved-searches",
            str(saved_searches),
            "--no-fallback",
            "--allow-send",
            "--max-steps",
            "3",
            "--out-dir",
            str(tmp_path / "network-session"),
        ]
    )

    assert exit_code == 0
    run = store.load_run()
    cursor = run.capture_cursors[source]
    assert run.sources[0].exhausted is False
    assert cursor.deferred_for_run is True
    assert cursor.cursor_status == SourceCursorStatus.STALLED_NAVIGATION.value
    assert any("source deferred for run" in note for note in run.notes)
    progress = store.load_source_progress().sources[source]
    assert progress.deferred_for_run is True
    assert progress.end_of_results is False
    assert run.operator_plan().action == "blocked"
    assert "deferred for this run" in (run.operator_plan().reason or "")
    stalled_calls = StalledCursorBrowserClient.instances[0].calls
    assert sum(call.startswith(f"capture:{source}") for call in stalled_calls) == 1


def test_cli_network_run_session_defers_wrong_page_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    WrongPageCaptureBrowserClient.instances.clear()
    monkeypatch.setattr(network_cli, "PlaywriterBrowserClient", WrongPageCaptureBrowserClient)
    source = "ASAP - Agency Owners Delivery"
    store = Store(tmp_path)
    start_run(
        store,
        per_source_target=1,
        run_date=date(2026, 7, 3),
        force=True,
        allow_fallback_sources=False,
        source_names=[source],
    )
    run = store.load_run()
    run.state = RunState.SENDING
    store.save_run(run)
    saved_searches = tmp_path / "saved-searches.json"
    _write_fake_artifact(
        saved_searches,
        SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-07-03T12:00:00Z",
                "url": "https://www.linkedin.com/sales/search/people",
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": source,
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    }
                ],
            }
        ),
    )

    exit_code = network_main(
        [
            "--state-dir",
            str(tmp_path),
            "run-session",
            "--resume",
            "--saved-searches",
            str(saved_searches),
            "--no-fallback",
            "--allow-send",
            "--max-steps",
            "3",
            "--out-dir",
            str(tmp_path / "network-session"),
        ]
    )

    assert exit_code == 0
    run = store.load_run()
    cursor = run.capture_cursors[source]
    assert run.sources[0].exhausted is False
    assert cursor.deferred_for_run is True
    assert cursor.cursor_status == SourceCursorStatus.WRONG_PAGE.value
    assert "people search" in (cursor.deferred_reason or "")


def test_cli_network_run_session_exhausts_source_at_end_of_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeLiveBrowserClient.instances.clear()
    monkeypatch.setattr(network_cli, "PlaywriterBrowserClient", ZeroThenNextSourceBrowserClient)
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
    assert run.sources[0].name == "ASAP - Contract Recruiters Staffing"
    assert run.sources[0].exhausted is True
    assert any(
        "reached end of saved-search results with no usable candidates" in note
        for note in run.notes
    )
    assert run.verified_count() == 0
    assert (tmp_path / "network-session" / "lead-review-candidates.json").exists()
    calls = ZeroThenNextSourceBrowserClient.instances[0].calls
    assert sum(
        call.startswith("capture:ASAP - Contract Recruiters Staffing") for call in calls
    ) == 1
    assert any(call.startswith("capture:ASAP - Agency Owners Delivery") for call in calls)
    assert not any(call.startswith("send:ASAP - Agency Owners Delivery Lead") for call in calls)


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
        "capture:ASAP - Contract Recruiters Staffing:pages=2:limit=4:only=True"
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
