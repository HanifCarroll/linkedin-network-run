from __future__ import annotations

import argparse
import ast
import csv
import io
import json
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from apps.comment_extractor.browser import (
    SCROLL_BY_SCRIPT,
    BrowserExtractionInput,
    BrowserSafetyLimits,
    PostMetadata,
    _capture_optional_screenshot,
    _comment_extraction_cdp_url,
    _comments_from_page_rows,
    _expand_visible_comment_controls,
    _read_manifest_post_urls,
)
from apps.comment_extractor.cli import main as comments_main
from apps.comment_extractor.contracts import PostHTMLInput
from apps.comment_extractor.linkedin_post_comments import (
    EXPLICIT_COMMENT_SELECTORS,
    extract_comments_from_html_file,
    write_raw_comments_jsonl,
)
from apps.compat import OPPORTUNITY_APP_COMMANDS, OPPORTUNITY_COMMANDS
from apps.opportunity_intel.cli import build_parser
from apps.opportunity_intel.cli import main as opportunity_main
from apps.opportunity_intel.contracts import (
    CANONICAL_COMMENT_COLUMNS,
    CommentEvidence,
    RankLevel,
    SourceKind,
)
from apps.opportunity_intel.experiments import evaluate_gate, run_source_experiment
from apps.opportunity_intel.imports import read_comment_csv, write_comment_csv
from apps.opportunity_intel.normalization import normalize_and_dedupe
from apps.opportunity_intel.post_discovery import PostCandidate, discover_posts_from_registry
from apps.opportunity_intel.ranking import rank_comment
from apps.opportunity_intel.search_capture import (
    COPY_CAPTURE_INSTALL_SCRIPT,
    COPY_CAPTURE_READ_SCRIPT,
    COPY_CAPTURE_RESTORE_SCRIPT,
    COPY_CAPTURE_TIMEOUT_MS,
    PostCopyCaptureError,
    _copy_post_url_from_menu,
    plan_search_capture,
    search_capture_url,
)
from apps.opportunity_intel.sources import (
    DEFAULT_QUERY_PACK_PATH,
    DEFAULT_SOURCE_REGISTRY_PATH,
    load_query_pack,
    load_source_registry,
    validate_registry_against_queries,
)
from apps.opportunity_intel.store import OpportunityStore, stable_comment_key
from packages.linkedin_common.progress import ProgressReporter

FIXTURE_DIR = Path("tests/fixtures/opportunity_intel")


def test_source_registry_and_query_pack_validate() -> None:
    registry = load_source_registry()
    query_pack = load_query_pack()

    validate_registry_against_queries(registry, query_pack)

    assert registry.contract_version == "opportunity-source-registry.v1"
    assert query_pack.contract_version == "opportunity-comment-signal-queries.v1"
    assert len(registry.sources) == 38
    assert len(query_pack.queries) == 6
    assert {
        "creator_bill_yost",
        "product_retool",
        "competitor_revops_consultants",
        "pain_dashboard_decision_support",
    } <= {source.source_id for source in registry.sources}
    assert registry.require_source("product_retool").source_kind is SourceKind.COMPANY_PAGE


def test_v0_source_batch_generates_actionable_post_queue() -> None:
    candidates = discover_posts_from_registry(load_source_registry())

    assert len(candidates) >= 100
    assert any(
        candidate.source_id == "known_high_signal_post_engagement"
        and candidate.reason == "known_post_url"
        and candidate.post_url.startswith("https://www.linkedin.com/posts/")
        for candidate in candidates
    )
    assert any(
        candidate.source_id == "creator_bill_yost"
        and candidate.reason == "watchlist_search"
        and candidate.source_url == "https://www.linkedin.com/in/billyost"
        and candidate.search_query
        for candidate in candidates
    )
    assert any(
        candidate.source_id == "product_retool"
        and candidate.reason == "company_page_posts"
        and candidate.source_url == "https://www.linkedin.com/company/tryretool/posts/"
        for candidate in candidates
    )
    assert any(
        candidate.source_id == "product_retool"
        and candidate.reason == "company_page_search"
        and candidate.search_query
        and candidate.source_url == "https://www.linkedin.com/company/tryretool/posts/"
        for candidate in candidates
    )
    assert any(
        candidate.source_id == "pain_dashboard_decision_support"
        and candidate.reason == "search_query"
        and candidate.source_url.startswith(
            "https://www.linkedin.com/search/results/content/?keywords="
        )
        for candidate in candidates
    )


def test_company_page_capture_exports_post_queue_from_saved_html(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "company-post-queue.csv"

    assert (
        opportunity_main(
            [
                "company-post-capture",
                "--source-id",
                "product_retool",
                "--html",
                str(FIXTURE_DIR / "company_page_posts.html"),
                "--out",
                str(output_path),
            ]
        )
        == 0
    )

    assert "rows=4" in capsys.readouterr().out
    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 4
    assert {row["post_url"] for row in rows} == {
        "https://www.linkedin.com/posts/tryretool_internal-tools-dashboard-activity-7450000000000000001-abcd",
        "https://www.linkedin.com/feed/update/urn:li:activity:7450000000000000002",
    }
    assert {row["query_id"] for row in rows} == {
        "internal_tools_dashboard_pain",
        "product_engineering_build_need",
    }
    assert {row["reason"] for row in rows} == {"company_page_post_url"}
    assert {row["source_url"] for row in rows} == {
        "https://www.linkedin.com/company/tryretool/posts/"
    }


def test_search_capture_plans_search_rows_without_guessing_post_urls() -> None:
    known = PostCandidate(
        source_id="known_high_signal_post_engagement",
        source_kind="known_post",
        query_id="internal_tools_dashboard_pain",
        post_url="https://www.linkedin.com/posts/valiotti_i-need-a-dashboard-activity-1-abcd",
        source_url="https://www.linkedin.com/posts/valiotti_i-need-a-dashboard-activity-1-abcd",
        search_query="",
        priority=100,
        reason="known_post_url",
    )
    search = PostCandidate(
        source_id="pain_dashboard_requests",
        source_kind="linkedin_search",
        query_id="internal_tools_dashboard_pain",
        post_url="",
        source_url="https://www.linkedin.com/search/results/content/?keywords=dashboard",
        search_query='"I need a dashboard"',
        priority=90,
        reason="search_query",
    )
    watchlist = PostCandidate(
        source_id="creator_bill_yost",
        source_kind="watchlist",
        query_id="internal_tools_dashboard_pain",
        post_url="",
        source_url="https://www.linkedin.com/in/billyost",
        search_query='"Bill Yost" dashboard spreadsheet',
        priority=96,
        reason="watchlist_search",
    )

    plan = plan_search_capture((known, search, watchlist))

    assert plan.known_posts == (known,)
    assert [item.candidate.source_id for item in plan.search_inputs] == [
        "pain_dashboard_requests",
        "creator_bill_yost",
    ]
    assert plan.search_inputs[0].capture_url == search.source_url
    assert plan.search_inputs[1].capture_url == (
        "https://www.linkedin.com/search/results/content/?keywords="
        "%22Bill+Yost%22+dashboard+spreadsheet"
    )
    assert not plan.skipped_rows


def test_search_capture_url_requires_explicit_search_input() -> None:
    candidate = PostCandidate(
        source_id="empty",
        source_kind="watchlist",
        query_id="internal_tools_dashboard_pain",
        post_url="",
        source_url="https://www.linkedin.com/in/someone",
        search_query="",
        priority=1,
        reason="watchlist_search",
    )

    assert search_capture_url(candidate) == ""


@pytest.mark.asyncio
async def test_search_capture_copy_link_uses_page_clipboard_intercept() -> None:
    copied_url = (
        "https://www.linkedin.com/posts/dataflip-co_hranalytics-powerbi-"
        "datadrivenhr-activity-7327939484082159616---Bv"
        "?utm_source=share&utm_medium=member_desktop"
    )
    page = _CopyInterceptPage(copied_url=copied_url)
    menu_button = _CopyInterceptButton()

    result = await _copy_post_url_from_menu(
        page=page,  # type: ignore[arg-type]
        menu_button=menu_button,
    )

    assert result == copied_url
    assert menu_button.clicked
    assert page.menu_item.clicked
    assert page.role_requests == [("menuitem", "^Copy link to post$")]
    assert page.wait_for_function_calls == [
        (
            "() => window.__linkedinPostCopyCapture?.writes?.length > 0",
            COPY_CAPTURE_TIMEOUT_MS,
        )
    ]
    assert page.evaluate_calls == [
        COPY_CAPTURE_INSTALL_SCRIPT,
        COPY_CAPTURE_READ_SCRIPT,
        COPY_CAPTURE_RESTORE_SCRIPT,
    ]


@pytest.mark.asyncio
async def test_search_capture_copy_link_fails_when_no_page_clipboard_write() -> None:
    page = _CopyInterceptPage(copied_url="", wait_times_out=True)

    with pytest.raises(PostCopyCaptureError, match="did not call navigator\\.clipboard"):
        await _copy_post_url_from_menu(
            page=page,  # type: ignore[arg-type]
            menu_button=_CopyInterceptButton(),
        )

    assert page.evaluate_calls == [
        COPY_CAPTURE_INSTALL_SCRIPT,
        COPY_CAPTURE_RESTORE_SCRIPT,
    ]


def test_progress_reporter_writes_status_lines() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(stream=stream)

    reporter.emit("search_start", index=1, total=3, query="dashboard work")

    output = stream.getvalue()
    assert output.startswith("progress ")
    assert "search_start" in output
    assert "index=1" in output
    assert "query=dashboard work" in output


def test_comment_extractor_writes_raw_comments_jsonl(tmp_path: Path) -> None:
    html_path = FIXTURE_DIR / "linkedin_post_comments.html"
    result = extract_comments_from_html_file(
        PostHTMLInput(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:7350000000000000001/",
            html_path=html_path,
            source_id="known_high_signal_post_engagement",
            query_id="known_high_signal_post_engagement",
        )
    )

    output_path = write_raw_comments_jsonl(result.comments, tmp_path)
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert output_path.name == "raw_comments.jsonl"
    assert len(rows) == 2
    assert rows[0]["contract_version"] == "raw_comments.v1"
    assert rows[0]["commenter_name"] == "Ava Founder"
    assert rows[0]["commenter_profile_url"] == "https://www.linkedin.com/in/ava-founder"
    assert "internal tool spreadsheet tracker" in rows[0]["comment_text"]
    assert EXPLICIT_COMMENT_SELECTORS == (
        '[componentkey^="replaceableComment_urn:li:comment:"]',
        '[data-id^="urn:li:comment:"]',
    )


def test_live_page_comment_rows_map_to_actual_comment_contract() -> None:
    result = _comments_from_page_rows(
        [
            {
                "comment_id": "urn:li:comment:(activity:1,comment:101)",
                "commenter_name": "Ava Founder",
                "commenter_profile_url": (
                    "https://www.linkedin.com/in/ava-founder/?miniProfileUrn=abc"
                ),
                "commenter_headline": "Founder at Ava Ops",
                "comment_text": (
                    "We need help turning our internal tool spreadsheet tracker "
                    "into a real dashboard this quarter."
                ),
                "commented_at": "2026-06-24T12:00:00Z",
            }
        ],
        input_row=BrowserExtractionInput(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:1/",
            source_id="known_high_signal_post_engagement",
            query_id="known_high_signal_post_engagement",
        ),
        post_metadata=PostMetadata(
            author_name="Ava Author",
            text="Operators discussing internal tools and dashboards.",
        ),
    )

    assert not result.warnings
    assert len(result.comments) == 1
    comment = result.comments[0]
    assert comment.commenter_name == "Ava Founder"
    assert comment.commenter_profile_url == "https://www.linkedin.com/in/ava-founder"
    assert comment.commenter_headline == "Founder at Ava Ops"
    assert "real dashboard" in comment.comment_text
    assert comment.post_author_name == "Ava Author"
    assert comment.post_text == "Operators discussing internal tools and dashboards."


def test_live_page_comment_rows_preserve_company_commenter_urls() -> None:
    result = _comments_from_page_rows(
        [
            {
                "comment_id": "urn:li:comment:(activity:1,comment:202)",
                "commenter_name": "Ava Ops",
                "commenter_profile_url": (
                    "https://www.linkedin.com/company/ava-ops/?miniCompanyUrn=abc"
                ),
                "commenter_headline": "1,204 followers",
                "comment_text": "We see this dashboard problem constantly.",
                "commented_at": "2026-06-24T12:00:00Z",
            }
        ],
        input_row=BrowserExtractionInput(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:1/",
            source_id="known_high_signal_post_engagement",
            query_id="known_high_signal_post_engagement",
        ),
        post_metadata=PostMetadata(
            author_name="Ava Author",
            text="Operators discussing internal tools and dashboards.",
        ),
    )

    assert not result.warnings
    assert len(result.comments) == 1
    assert result.comments[0].commenter_profile_url == (
        "https://www.linkedin.com/company/ava-ops"
    )


@pytest.mark.asyncio
async def test_live_comment_expansion_uses_dom_scroll_instead_of_mouse_wheel() -> None:
    page = _DomScrollOnlyPage()

    stats = await _expand_visible_comment_controls(
        page,  # type: ignore[arg-type]
        BrowserSafetyLimits(
            max_scrolls=1,
            max_comment_control_clicks=1,
            max_reply_control_clicks=0,
            settle_ms=25,
        ),
    )

    assert stats.scrolls_performed == 1
    assert (SCROLL_BY_SCRIPT, 1800) in page.evaluate_calls
    assert page.waits == [25]


@pytest.mark.asyncio
async def test_live_comment_expansion_stops_after_no_progress() -> None:
    stream = io.StringIO()
    page = _AdaptiveExpansionPage(
        comment_counts=(0, 3, 3),
        scroll_states=(
            {"scrollY": 0, "scrollHeight": 2000, "innerHeight": 900},
            {"scrollY": 900, "scrollHeight": 3000, "innerHeight": 900},
            {"scrollY": 900, "scrollHeight": 3000, "innerHeight": 900},
        ),
    )

    stats = await _expand_visible_comment_controls(
        page,  # type: ignore[arg-type]
        BrowserSafetyLimits(
            max_scrolls=6,
            max_comment_control_clicks=12,
            max_reply_control_clicks=8,
            settle_ms=1,
            max_no_progress_passes=1,
        ),
        progress=ProgressReporter(stream=stream),
    )

    assert stats.stop_reason == "no_more_content"
    assert stats.scrolls_performed == 2
    assert stats.visible_comment_nodes == 3
    output = stream.getvalue()
    assert "comment_expand_pass" in output
    assert "new_comments=3" in output
    assert "stop_reason=no_more_content" in output


@pytest.mark.asyncio
async def test_live_comment_expansion_uses_global_click_budgets() -> None:
    page = _AdaptiveExpansionPage(
        comment_counts=(0, 0, 0, 0),
        scroll_states=(
            {"scrollY": 0, "scrollHeight": 2000, "innerHeight": 900},
            {"scrollY": 900, "scrollHeight": 3000, "innerHeight": 900},
            {"scrollY": 1800, "scrollHeight": 4000, "innerHeight": 900},
            {"scrollY": 2700, "scrollHeight": 5000, "innerHeight": 900},
        ),
        comment_button_count=10,
        reply_button_count=10,
    )

    stats = await _expand_visible_comment_controls(
        page,  # type: ignore[arg-type]
        BrowserSafetyLimits(
            max_scrolls=3,
            max_comment_control_clicks=3,
            max_reply_control_clicks=2,
            settle_ms=1,
            max_no_progress_passes=10,
        ),
    )

    assert stats.stop_reason == "max_scrolls_reached"
    assert stats.comment_control_clicks == 3
    assert stats.reply_control_clicks == 2
    assert page.comment_clicks == 3
    assert page.reply_clicks == 2


@pytest.mark.asyncio
async def test_optional_screenshot_failure_returns_warning_without_artifact() -> None:
    store = _ArtifactRecordingStore()

    warnings = await _capture_optional_screenshot(
        page=object(),  # type: ignore[arg-type]
        run_id="run_1",
        writer=_FailingScreenshotWriter(),  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
    )

    assert warnings == ("screenshot_capture_failed:Error",)
    assert store.artifacts == []


def test_comment_extraction_disables_implicit_cdp_attachment() -> None:
    assert _comment_extraction_cdp_url(None) == ""
    assert _comment_extraction_cdp_url(" ws://127.0.0.1:19988/cdp ") == ("ws://127.0.0.1:19988/cdp")


def test_provider_csv_snapshot_preserves_incremental_persisted_comments(
    tmp_path: Path,
) -> None:
    store = OpportunityStore(tmp_path / "state")
    provider_csv = tmp_path / "provider-comments.csv"
    first = _comment_fixture(
        index=1,
        text="We need help with an internal tool dashboard for our ops team.",
    )
    second = _comment_fixture(
        index=2,
        text="Our manual tracker needs to become a dashboard this quarter.",
    )

    for comment in (first, second):
        run_id = store.start_extraction_run(
            post_url=comment.post_url,
            source_id=comment.source_id,
            query_id=comment.query_id,
            source_kind=comment.source_kind,
            source_url=comment.source_url,
            search_query=comment.search_query,
            browser_profile="LinkedIn",
            safety_limits={},
        )
        store.persist_comments(
            run_id=run_id,
            comments=(comment,),
            query_pack=load_query_pack(),
        )
        write_comment_csv(provider_csv, store.export_comments())

    result = read_comment_csv(provider_csv, load_query_pack())
    assert len(result.valid_comments) == 2
    assert not result.rejected_rows


def test_saved_html_extraction_persists_sqlite_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_dir = tmp_path / "state"
    out_dir = tmp_path / "comments"

    assert (
        comments_main(
            [
                "extract",
                "--post-url",
                "https://www.linkedin.com/feed/update/urn:li:activity:7350000000000000001/",
                "--html",
                str(FIXTURE_DIR / "linkedin_post_comments.html"),
                "--source-id",
                "known_high_signal_post_engagement",
                "--query-id",
                "known_high_signal_post_engagement",
                "--state-dir",
                str(state_dir),
                "--out-dir",
                str(out_dir),
            ]
        )
        == 0
    )

    assert "raw comments:" in capsys.readouterr().out
    store = OpportunityStore(state_dir)
    rows = store.fetch_all(
        """
        SELECT c.comment_key, r.rank_level, r.rank_points, r.problem_fit,
               r.buying_signal, r.buyer_fit, r.actionability, r.immediacy
        FROM comments c
        JOIN rankings r ON r.comment_key = c.comment_key
        ORDER BY r.rank_points DESC
        """
    )
    assert len(rows) == 2
    assert rows[0]["rank_level"] == "strong"
    assert rows[0]["rank_points"] == 12
    assert rows[0]["problem_fit"] == 4
    assert rows[0]["buying_signal"] == 2
    assert rows[0]["buyer_fit"] == 2
    assert rows[0]["actionability"] == 2
    assert rows[0]["immediacy"] == 2
    artifacts = store.fetch_all("SELECT kind FROM extraction_artifacts ORDER BY kind")
    assert {row["kind"] for row in artifacts} == {"html", "raw_comments"}


def test_opportunity_preflight_syncs_source_batch_without_collecting_comments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_dir = tmp_path / "state"

    assert opportunity_main(["preflight", "--state-dir", str(state_dir), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["recommend_only"] is True
    assert payload["sources"] == 38
    assert payload["post_candidates"] >= 100
    assert Path(payload["artifact_path"]).exists()
    store = OpportunityStore(state_dir)
    assert store.fetch_all("SELECT COUNT(*) AS count FROM sources")[0]["count"] == 38
    assert store.fetch_all("SELECT COUNT(*) AS count FROM posts")[0]["count"] >= 100
    assert store.fetch_all("SELECT COUNT(*) AS count FROM comments")[0]["count"] == 0


def test_provider_csv_aliases_normalize_to_actual_comment_contract(tmp_path: Path) -> None:
    csv_path = tmp_path / "provider.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "query",
                "source",
                "source_type",
                "linkedin_post_url",
                "name",
                "profile_url",
                "headline",
                "company",
                "text",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "query": "internal_tools_dashboard_pain",
                "source": "manual_actual_comment_import",
                "source_type": "manual_csv",
                "linkedin_post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1/",
                "name": "Ava Founder",
                "profile_url": "https://www.linkedin.com/in/ava-founder/",
                "headline": "Founder",
                "company": "Ava Ops",
                "text": "We need help with an internal tool dashboard for our ops team.",
            }
        )

    result = read_comment_csv(csv_path, load_query_pack())

    assert not result.rejected_rows
    assert result.valid_comments[0].commenter_name == "Ava Founder"
    assert result.valid_comments[0].comment_text.startswith("We need help")


def test_provider_csv_cleans_exact_adjacent_duplicate_post_author(tmp_path: Path) -> None:
    csv_path = tmp_path / "provider.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COMMENT_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "query_id": "internal_tools_dashboard_pain",
                "source_id": "manual_actual_comment_import",
                "source_kind": "manual_csv",
                "source_url": "",
                "search_query": "",
                "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1/",
                "post_author_name": "Ava FounderAva Founder",
                "post_text": "",
                "comment_id": "urn:li:comment:1",
                "comment_url": "",
                "commenter_name": "Buyer One",
                "commenter_profile_url": "https://www.linkedin.com/in/buyer-one/",
                "commenter_headline": "Founder",
                "commenter_company": "Buyer Co",
                "relationship": "",
                "comment_text": "We need help with an internal tool dashboard for our ops team.",
                "commented_at": "2026-06-24T12:00:00Z",
            }
        )

    result = read_comment_csv(csv_path, load_query_pack())

    assert not result.rejected_rows
    assert result.valid_comments[0].post_author_name == "Ava Founder"


def test_gate_enforces_100_row_batch_proof(tmp_path: Path) -> None:
    comments_csv = tmp_path / "comments_99.csv"
    _write_comment_fixture_csv(comments_csv, count=99, direct_buyer_count=35)
    import_result = read_comment_csv(comments_csv, load_query_pack())
    deduped = normalize_and_dedupe(import_result.valid_comments)
    query = load_query_pack().require_query("internal_tools_dashboard_pain")
    ranked = tuple(rank_comment(comment, query) for comment in deduped.comments)

    gate = evaluate_gate(ranked)

    assert not gate.passed
    assert "minimum_valid_comments_not_met" in gate.failed_reasons


def test_fixture_backed_experiment_writes_required_artifacts(tmp_path: Path) -> None:
    comments_csv = tmp_path / "comments_100.csv"
    _write_comment_fixture_csv(comments_csv, count=100, direct_buyer_count=35)

    artifacts = run_source_experiment(
        comments_csv_path=comments_csv,
        output_dir=tmp_path / "runs",
        source_registry_path=DEFAULT_SOURCE_REGISTRY_PATH,
        query_pack_path=DEFAULT_QUERY_PACK_PATH,
        run_id="fixture-run",
    )

    gate_payload = json.loads(artifacts.gate_path.read_text(encoding="utf-8"))
    report = artifacts.source_report_path.read_text(encoding="utf-8")

    assert gate_payload["passed"] is True
    assert gate_payload["valid_comment_count"] == 100
    assert gate_payload["warm_hot_count"] == 35
    assert artifacts.calibration_template_path.exists()
    assert artifacts.calibration_report_path.exists()
    assert artifacts.source_decision_path.exists()
    assert artifacts.action_plan_path.exists()
    assert artifacts.run_history_path.exists()
    assert artifacts.review_queue_csv_path.exists()
    assert artifacts.review_queue_jsonl_path.exists()
    assert "Opportunity Source Experiment Report" in report


def test_opportunity_cli_covers_compatibility_command_surface() -> None:
    parser = build_parser()
    command_names = _parser_command_names(parser)
    expected = set(OPPORTUNITY_COMMANDS) - {"import-legacy-state"}

    assert OPPORTUNITY_APP_COMMANDS == expected
    assert expected <= command_names


def test_opportunity_cli_smoke_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert opportunity_main(["status", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["recommend_only"] is True

    provider_template = tmp_path / "provider-template.csv"
    assert opportunity_main(["provider-export-csv", "--out", str(provider_template)]) == 0
    capsys.readouterr()
    with provider_template.open(newline="", encoding="utf-8") as handle:
        assert csv.DictReader(handle).fieldnames == list(CANONICAL_COMMENT_COLUMNS)

    post_queue = tmp_path / "post-queue.csv"
    assert opportunity_main(["prepare-batch", "--out", str(post_queue)]) == 0
    assert "post queue:" in capsys.readouterr().out
    with post_queue.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"source_id", "query_id", "post_url", "search_query"} <= set(rows[0])


def test_opportunity_cli_spike_and_artifact_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    comments_csv = tmp_path / "comments_100.csv"
    runs_dir = tmp_path / "runs"
    _write_comment_fixture_csv(comments_csv, count=100, direct_buyer_count=35)

    assert (
        opportunity_main(
            [
                "run-spike",
                "--comments-csv",
                str(comments_csv),
                "--out-dir",
                str(runs_dir),
                "--run-id",
                "spike",
            ]
        )
        == 0
    )
    assert "source report:" in capsys.readouterr().out

    assert opportunity_main(["gate-report", "--run-dir", str(runs_dir / "spike")]) == 0
    gate_payload = json.loads(capsys.readouterr().out)
    assert gate_payload["valid_comment_count"] == 100

    assert opportunity_main(["action-plan", "--run-dir", str(runs_dir / "spike")]) == 0
    assert "# Action Plan" in capsys.readouterr().out


def test_import_signals_persists_comments_to_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    comments_csv = tmp_path / "comments.csv"
    state_dir = tmp_path / "opportunity-state"
    _write_comment_fixture_csv(comments_csv, count=3, direct_buyer_count=1)

    assert (
        opportunity_main(
            [
                "import-signals",
                "--comments-csv",
                str(comments_csv),
                "--state-dir",
                str(state_dir),
                "--run-id",
                "import_test",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["imported_comments"] == 3

    store = OpportunityStore(state_dir)
    assert store.fetch_all("SELECT COUNT(*) AS count FROM comments")[0]["count"] == 3
    assert store.fetch_all("SELECT COUNT(*) AS count FROM rankings")[0]["count"] == 3
    assert store.fetch_all("SELECT COUNT(*) AS count FROM sources")[0]["count"] > 0


def test_prefilter_post_queue_keeps_only_measured_comment_rich_posts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    post_queue = tmp_path / "post-queue.csv"
    manifest = tmp_path / "extract_url_queue_manifest.jsonl"
    filtered_queue = tmp_path / "filtered-post-queue.csv"
    metrics = tmp_path / "prefilter-metrics.csv"
    fieldnames = (
        "source_id",
        "source_kind",
        "query_id",
        "post_url",
        "source_url",
        "search_query",
        "priority",
        "reason",
    )
    with post_queue.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for label in ("keep", "low", "missing", "failed"):
            writer.writerow(
                {
                    "source_id": "linkedin_search_dashboard",
                    "source_kind": "linkedin_search",
                    "query_id": "internal_tools_dashboard_pain",
                    "post_url": f"https://www.linkedin.com/posts/{label}",
                    "source_url": "https://www.linkedin.com/search/results/content/",
                    "search_query": '"I need a dashboard"',
                    "priority": "100",
                    "reason": "search_query",
                }
            )
    manifest.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "post_url": "https://www.linkedin.com/posts/keep",
                    "run_id": "run_keep",
                    "status": "extracted",
                    "comments_found": 12,
                },
                {
                    "post_url": "https://www.linkedin.com/posts/low",
                    "run_id": "run_low",
                    "status": "extracted",
                    "comments_found": 2,
                },
                {
                    "post_url": "https://www.linkedin.com/posts/failed",
                    "run_id": "run_failed",
                    "status": "failed",
                    "comments_found": 0,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    assert (
        opportunity_main(
            [
                "prefilter-post-queue",
                "--post-queue",
                str(post_queue),
                "--manifest",
                str(manifest),
                "--out",
                str(filtered_queue),
                "--metrics-out",
                str(metrics),
                "--min-comments",
                "10",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_candidates"] == 4
    assert payload["measured_candidates"] == 3
    assert payload["kept_candidates"] == 1
    assert payload["missing_metric_candidates"] == 1

    with filtered_queue.open(newline="", encoding="utf-8") as handle:
        filtered_rows = list(csv.DictReader(handle))
    assert [row["post_url"] for row in filtered_rows] == [
        "https://www.linkedin.com/posts/keep"
    ]

    with metrics.open(newline="", encoding="utf-8") as handle:
        metric_rows = list(csv.DictReader(handle))
    reasons = {row["post_url"]: row["prefilter_reason"] for row in metric_rows}
    assert reasons["https://www.linkedin.com/posts/keep"] == "comments_found_met_threshold"
    assert reasons["https://www.linkedin.com/posts/low"] == "comments_found_below_10"
    assert reasons["https://www.linkedin.com/posts/missing"] == "missing_extraction_metric"
    assert reasons["https://www.linkedin.com/posts/failed"] == "extraction_failed"


def test_extract_url_queue_resume_reads_processed_manifest_urls(tmp_path: Path) -> None:
    manifest = tmp_path / "extract_url_queue_manifest.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "post_url": "https://www.linkedin.com/posts/done",
                    "run_id": "run_done",
                    "status": "extracted",
                    "comments_found": 12,
                },
                {
                    "post_url": "https://www.linkedin.com/posts/failed",
                    "run_id": "run_failed",
                    "status": "failed",
                    "comments_found": 0,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    assert _read_manifest_post_urls(manifest) == frozenset(
        {
            "https://www.linkedin.com/posts/done",
            "https://www.linkedin.com/posts/failed",
        }
    )
    assert _read_manifest_post_urls(tmp_path / "missing.jsonl") == frozenset()


def test_ranker_rejects_recruiting_and_job_seeker_noise(tmp_path: Path) -> None:
    comments_csv = tmp_path / "noise.csv"
    _write_comment_fixture_csv(comments_csv, count=1, direct_buyer_count=0)
    comment = read_comment_csv(comments_csv, load_query_pack()).valid_comments[0]

    ranked = rank_comment(comment, load_query_pack().require_query(comment.query_id))

    assert ranked.rank_level is RankLevel.REJECT
    assert "not buyer" in ranked.reject_reasons or "job seeker" in ranked.reject_reasons


def test_ranker_uses_requested_buyer_signal_dimensions() -> None:
    comment = CommentEvidence(
        query_id="known_high_signal_post_engagement",
        source_id="manual_actual_comment_import",
        source_kind="manual_csv",
        source_url="https://www.linkedin.com/search/results/content/",
        search_query="",
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:1/",
        post_author_name="",
        post_text="",
        comment_id="urn:li:comment:1",
        comment_url="",
        commenter_name="Ava Founder",
        commenter_profile_url="https://www.linkedin.com/in/ava-founder/",
        commenter_headline="Founder",
        commenter_company="Ava Ops",
        relationship="",
        comment_text=(
            "We need help turning our spreadsheet tracker into an internal tool "
            "dashboard this quarter. Who can help build this?"
        ),
        commented_at="2026-06-24T12:00:00Z",
    )

    ranked = rank_comment(comment, load_query_pack().require_query(comment.query_id))

    assert ranked.rank_level is RankLevel.STRONG
    assert ranked.rank_points == 15
    assert (
        ranked.problem_fit,
        ranked.buying_signal,
        ranked.buyer_fit,
        ranked.actionability,
        ranked.immediacy,
    ) == (4, 4, 3, 2, 2)
    assert stable_comment_key(comment).startswith("comment_")


def test_ranker_caps_commentary_without_buyer_need() -> None:
    comment = CommentEvidence(
        query_id="internal_tools_dashboard_pain",
        source_id="known_high_signal_post_engagement",
        source_kind="known_post",
        source_url="https://www.linkedin.com/posts/example",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="Ava Author",
        post_text="Dashboard requests often hide business problems.",
        comment_id="urn:li:comment:1",
        comment_url="",
        commenter_name="Ava Founder",
        commenter_profile_url="https://www.linkedin.com/in/ava-founder/",
        commenter_headline="Founder at Ava Ops",
        commenter_company="Ava Ops",
        relationship="",
        comment_text=(
            "There is an underlying business problem behind every "
            "\"I need a dashboard\" request."
        ),
        commented_at="2026-06-24T12:00:00Z",
    )

    ranked = rank_comment(comment, load_query_pack().require_query(comment.query_id))

    assert ranked.rank_level is RankLevel.IRRELEVANT
    assert ranked.buying_signal == 0


def test_ranker_rejects_service_provider_pitch_comments() -> None:
    query = load_query_pack().require_query("internal_tools_dashboard_pain")
    seller_pitch = CommentEvidence(
        query_id=query.query_id,
        source_id="product_n8n",
        source_kind="linkedin_search",
        source_url="https://www.linkedin.com/search/results/content/",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="Ava Buyer",
        post_text="Looking for AI automation collaborators.",
        comment_id="urn:li:comment:1",
        comment_url="",
        commenter_name="Nitin Builder",
        commenter_profile_url="https://www.linkedin.com/in/nitin-builder/",
        commenter_headline=(
            "Senior Full Stack Developer | AI Developer | "
            "n8n Workflow automation | Available Immediately"
        ),
        commenter_company="",
        relationship="",
        comment_text=(
            "Your search for an AI Automation Expert caught my eye. "
            "I specialize in designing robust automation solutions. "
            "I’d love to discuss how my expertise can support your upcoming projects. "
            "Portfolio: https://example.com GitHub: https://github.com/example"
        ),
        commented_at="2026-06-24T12:00:00Z",
    )
    buyer_need = CommentEvidence(
        query_id=query.query_id,
        source_id="product_n8n",
        source_kind="linkedin_search",
        source_url="https://www.linkedin.com/search/results/content/",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="Ava Buyer",
        post_text="AI automation lessons.",
        comment_id="urn:li:comment:2",
        comment_url="",
        commenter_name="Riley Ops",
        commenter_profile_url="https://www.linkedin.com/in/riley-ops/",
        commenter_headline="Head of Operations",
        commenter_company="Riley Health",
        relationship="",
        comment_text=(
            "We need help replacing our spreadsheet workflow with an internal "
            "tool this quarter. Who can help build this?"
        ),
        commented_at="2026-06-24T12:00:00Z",
    )

    seller_ranked = rank_comment(seller_pitch, query)
    buyer_ranked = rank_comment(buyer_need, query)

    assert seller_ranked.rank_level is RankLevel.IRRELEVANT
    assert "vendor" in seller_ranked.reject_reasons
    assert buyer_ranked.rank_level is RankLevel.STRONG


def test_ranker_does_not_treat_generic_replace_or_budgets_as_buying_signal() -> None:
    query = load_query_pack().require_query("internal_tools_dashboard_pain")
    replace_people = CommentEvidence(
        query_id=query.query_id,
        source_id="product_make",
        source_kind="linkedin_search",
        source_url="https://www.linkedin.com/search/results/content/",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="Ava Author",
        post_text="Automation tradeoffs.",
        comment_id="urn:li:comment:1",
        comment_url="",
        commenter_name="Morgan Ops",
        commenter_profile_url="https://www.linkedin.com/in/morgan-ops/",
        commenter_headline="Senior Operations Leader",
        commenter_company="Morgan Health",
        relationship="",
        comment_text=(
            "Automation is not here to replace people. It frees people up "
            "to focus on work that requires judgment."
        ),
        commented_at="2026-06-24T12:00:00Z",
    )
    budgets_commentary = CommentEvidence(
        query_id=query.query_id,
        source_id="pain_spreadsheet_operations",
        source_kind="linkedin_search",
        source_url="https://www.linkedin.com/search/results/content/",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="Ava Author",
        post_text="Spreadsheet governance.",
        comment_id="urn:li:comment:2",
        comment_url="",
        commenter_name="Jordan Tech",
        commenter_profile_url="https://www.linkedin.com/in/jordan-tech/",
        commenter_headline="Technology Leader",
        commenter_company="Jordan IT",
        relationship="",
        comment_text=(
            "Most organizations focus on managing projects, budgets, and delivery. "
            "Reducing spreadsheet dependency can be a game changer."
        ),
        commented_at="2026-06-24T12:00:00Z",
    )

    replace_ranked = rank_comment(replace_people, query)
    budgets_ranked = rank_comment(budgets_commentary, query)

    assert replace_ranked.rank_level is RankLevel.IRRELEVANT
    assert replace_ranked.positive_signals == ()
    assert budgets_ranked.rank_level is RankLevel.IRRELEVANT
    assert budgets_ranked.positive_signals == ()


def test_ranker_does_not_treat_generic_pain_commentary_as_buying_signal() -> None:
    query = load_query_pack().require_query("internal_tools_dashboard_pain")
    commentary = CommentEvidence(
        query_id=query.query_id,
        source_id="competitor_automation_agencies",
        source_kind="linkedin_search",
        source_url="https://www.linkedin.com/search/results/content/",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="Ava Author",
        post_text="Finance workflow automation.",
        comment_id="urn:li:comment:1",
        comment_url="",
        commenter_name="Aditya CEO",
        commenter_profile_url="https://www.linkedin.com/in/aditya-ceo/",
        commenter_headline="CEO",
        commenter_company="Qilin Lab",
        relationship="",
        comment_text=(
            "This hits a real pain point. Most automation projects fail in "
            "the handoff between business context and technical implementation."
        ),
        commented_at="2026-06-24T12:00:00Z",
    )

    ranked = rank_comment(commentary, query)

    assert ranked.rank_level is RankLevel.IRRELEVANT
    assert ranked.buying_signal == 0


def test_ranker_rejects_post_author_replies_and_dashboard_music_noise() -> None:
    query = load_query_pack().require_query("internal_tools_dashboard_pain")
    author_reply = CommentEvidence(
        query_id=query.query_id,
        source_id="creator_bill_yost",
        source_kind="watchlist",
        source_url="https://www.linkedin.com/in/billyost",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="🏴‍☠️ Bill Yost",
        post_text="Dashboard advice.",
        comment_id="urn:li:comment:1",
        comment_url="",
        commenter_name="Bill Yost",
        commenter_profile_url="https://www.linkedin.com/in/billyost/",
        commenter_headline="People Analytics",
        commenter_company="",
        relationship="",
        comment_text=(
            "Yes, this is a great add. I need to include it. "
            "My dashboards now have links to our metrics glossary."
        ),
        commented_at="2026-06-24T12:00:00Z",
    )
    music_joke = CommentEvidence(
        query_id=query.query_id,
        source_id="creator_bill_yost",
        source_kind="watchlist",
        source_url="https://www.linkedin.com/in/billyost",
        search_query="",
        post_url="https://www.linkedin.com/posts/example",
        post_author_name="Bill Yost",
        post_text="Dashboard Confessionals.",
        comment_id="urn:li:comment:2",
        comment_url="",
        commenter_name="Taylor Smith",
        commenter_profile_url="https://www.linkedin.com/in/taylor-smith/",
        commenter_headline="Technical Product Support",
        commenter_company="",
        relationship="",
        comment_text=(
            "Wait but will Dashboard Confessional be there? "
            "I need to know if anyone else is still listening to the soundtrack."
        ),
        commented_at="2026-06-24T12:00:00Z",
    )

    assert rank_comment(author_reply, query).rank_level is RankLevel.IRRELEVANT
    assert "post author" in rank_comment(author_reply, query).reject_reasons
    assert rank_comment(music_joke, query).rank_level is RankLevel.IRRELEVANT
    assert "entertainment_noise" in rank_comment(music_joke, query).reject_reasons


def test_opportunity_and_comment_modules_do_not_import_action_modules() -> None:
    prohibited_modules = (
        "apps.network_automation",
        "apps.recruiter_agency_outreach",
        "packages.linkedin_salesnav.messages",
    )
    prohibited_action_terms = ("send", "connect", "withdraw")
    for package_dir in (Path("apps/opportunity_intel"), Path("apps/comment_extractor")):
        for path in package_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith(prohibited_modules)
                if isinstance(node, ast.ImportFrom) and node.module is not None:
                    assert not node.module.startswith(prohibited_modules)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    lower_name = node.name.casefold()
                    assert not any(term in lower_name for term in prohibited_action_terms)


def _parser_command_names(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:
        action_any = cast(Any, action)
        choices = getattr(action_any, "choices", None)
        if isinstance(choices, dict):
            return set(choices)
    raise AssertionError("parser has no subcommands")


class _DomScrollOnlyPage:
    def __init__(self) -> None:
        self.mouse = _NoWheelMouse()
        self.evaluate_calls: list[tuple[str, int]] = []
        self.waits: list[int] = []

    def get_by_role(self, _role: str, *, name: Any) -> _ButtonLocator:
        return _ButtonLocator(page=None, kind="comment", count=0)

    def locator(self, _selector: str) -> _EmptyCountLocator:
        return _EmptyCountLocator()

    async def evaluate(self, expression: str, arg: int | None = None) -> dict[str, int] | None:
        if arg is None:
            return {"scrollY": 0, "scrollHeight": 0, "innerHeight": 0}
        self.evaluate_calls.append((expression, arg))
        return None

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class _NoWheelMouse:
    async def wheel(self, _delta_x: int, _delta_y: int) -> None:
        raise AssertionError("live comment expansion should not use mouse wheel input")


class _AdaptiveExpansionPage:
    def __init__(
        self,
        *,
        comment_counts: tuple[int, ...],
        scroll_states: tuple[dict[str, int], ...],
        comment_button_count: int = 0,
        reply_button_count: int = 0,
    ) -> None:
        self.comment_counts = comment_counts
        self.scroll_states = scroll_states
        self.comment_count_index = 0
        self.scroll_state_index = 0
        self.comment_button_count = comment_button_count
        self.reply_button_count = reply_button_count
        self.comment_clicks = 0
        self.reply_clicks = 0
        self.scroll_calls: list[int] = []
        self.waits: list[int] = []

    def locator(self, _selector: str) -> _CommentCountLocator:
        return _CommentCountLocator(self)

    def get_by_role(self, _role: str, *, name: Any) -> _ButtonLocator:
        pattern = getattr(name, "pattern", "")
        kind = "comment" if "comments?" in pattern else "reply"
        count = self.comment_button_count if kind == "comment" else self.reply_button_count
        return _ButtonLocator(page=self, kind=kind, count=count)

    async def evaluate(self, _expression: str, arg: int | None = None) -> dict[str, int] | None:
        if arg is not None:
            self.scroll_calls.append(arg)
            return None
        index = min(self.scroll_state_index, len(self.scroll_states) - 1)
        self.scroll_state_index += 1
        return self.scroll_states[index]

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    def next_comment_count(self) -> int:
        index = min(self.comment_count_index, len(self.comment_counts) - 1)
        self.comment_count_index += 1
        return self.comment_counts[index]

    def record_click(self, kind: str) -> None:
        if kind == "comment":
            self.comment_clicks += 1
        else:
            self.reply_clicks += 1


class _EmptyCountLocator:
    async def count(self) -> int:
        return 0


class _CommentCountLocator:
    def __init__(self, page: _AdaptiveExpansionPage) -> None:
        self.page = page

    async def count(self) -> int:
        return self.page.next_comment_count()


class _ButtonLocator:
    def __init__(
        self,
        *,
        page: _AdaptiveExpansionPage | None,
        kind: str,
        count: int,
    ) -> None:
        self.page = page
        self.kind = kind
        self._count = count

    async def count(self) -> int:
        return self._count

    def nth(self, _index: int) -> _ButtonLocator:
        return self

    async def is_visible(self) -> bool:
        return True

    async def is_enabled(self) -> bool:
        return True

    async def click(self) -> None:
        if self.page is not None:
            self.page.record_click(self.kind)


class _CopyInterceptPage:
    def __init__(self, *, copied_url: str, wait_times_out: bool = False) -> None:
        self.copied_url = copied_url
        self.wait_times_out = wait_times_out
        self.evaluate_calls: list[str] = []
        self.wait_for_function_calls: list[tuple[str, int]] = []
        self.role_requests: list[tuple[str, str]] = []
        self.menu_item = _CopyInterceptMenuItem()

    async def evaluate(self, expression: str) -> str | None:
        self.evaluate_calls.append(expression)
        if expression == COPY_CAPTURE_READ_SCRIPT:
            return self.copied_url
        return None

    async def wait_for_function(self, expression: str, *, timeout: int) -> None:
        self.wait_for_function_calls.append((expression, timeout))
        if self.wait_times_out:
            raise PlaywrightTimeoutError("timed out")

    def get_by_role(self, role: str, *, name: Any) -> _CopyInterceptMenuItem:
        pattern = getattr(name, "pattern", str(name))
        self.role_requests.append((role, pattern))
        return self.menu_item


class _CopyInterceptButton:
    def __init__(self) -> None:
        self.clicked = False
        self.dispatched_events: list[str] = []

    async def click(self, *, timeout: int | None = None) -> None:
        self.clicked = True

    async def dispatch_event(self, event: str) -> None:
        self.dispatched_events.append(event)


class _CopyInterceptMenuItem:
    def __init__(self) -> None:
        self.clicked = False

    async def click(self) -> None:
        self.clicked = True


class _FailingScreenshotWriter:
    async def screenshot(self, *_args: Any, **_kwargs: Any) -> object:
        from playwright.async_api import Error as PlaywrightError

        raise PlaywrightError("Unable to capture screenshot")


class _ArtifactRecordingStore:
    def __init__(self) -> None:
        self.artifacts: list[tuple[str, str, Path]] = []

    def record_artifact(self, *, run_id: str, kind: str, path: Path) -> None:
        self.artifacts.append((run_id, kind, path))


def _comment_fixture(*, index: int, text: str) -> CommentEvidence:
    return CommentEvidence(
        query_id="internal_tools_dashboard_pain",
        source_id="manual_actual_comment_import",
        source_kind="manual_csv",
        source_url="https://www.linkedin.com/search/results/content/",
        search_query='"internal tool" "need help"',
        post_url=f"https://www.linkedin.com/feed/update/urn:li:activity:{index}/",
        post_author_name="Post Author",
        post_text="Operators discussing dashboard work.",
        comment_id=f"urn:li:comment:{index}",
        comment_url="",
        commenter_name=f"Buyer {index}",
        commenter_profile_url=f"https://www.linkedin.com/in/buyer-{index}/",
        commenter_headline="Founder",
        commenter_company="Acme Ops",
        relationship="",
        comment_text=text,
        commented_at="2026-06-24T12:00:00Z",
    )


def _write_comment_fixture_csv(path: Path, *, count: int, direct_buyer_count: int) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_COMMENT_COLUMNS)
        writer.writeheader()
        for index in range(count):
            direct_buyer = index < direct_buyer_count
            writer.writerow(
                {
                    "query_id": "internal_tools_dashboard_pain",
                    "source_id": "manual_actual_comment_import",
                    "source_kind": "manual_csv",
                    "source_url": "https://www.linkedin.com/search/results/content/",
                    "search_query": '"internal tool" "need help"',
                    "post_url": (
                        "https://www.linkedin.com/feed/update/"
                        f"urn:li:activity:735000000000000{index:04d}/"
                    ),
                    "post_author_name": "Post Author",
                    "post_text": "Operators discussing dashboard work.",
                    "comment_id": f"urn:li:comment:{index}",
                    "comment_url": "",
                    "commenter_name": f"Person {index}",
                    "commenter_profile_url": f"https://www.linkedin.com/in/person-{index}/",
                    "commenter_headline": "Founder" if direct_buyer else "Student",
                    "commenter_company": "Acme Ops" if direct_buyer else "",
                    "relationship": "",
                    "comment_text": (
                        "We need help with an internal tool dashboard "
                        "for our ops team this quarter."
                        if direct_buyer
                        else "I am a student looking for a job and liked this dashboard example."
                    ),
                    "commented_at": "2026-06-24T12:00:00Z",
                }
            )
