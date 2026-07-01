"""Browser-backed LinkedIn post comment extraction."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

from apps.comment_extractor.contracts import ExtractionResult
from apps.comment_extractor.linkedin_post_comments import (
    write_raw_comments_jsonl,
)
from apps.opportunity_intel.contracts import CommentEvidence
from apps.opportunity_intel.imports import write_comment_csv
from apps.opportunity_intel.sources import load_query_pack
from apps.opportunity_intel.store import OpportunityStore
from packages.linkedin_browser.artifacts import ArtifactWriter
from packages.linkedin_browser.sessions import BrowserSession, PageReusePolicy
from packages.linkedin_common.progress import ProgressReporter

SCROLL_BY_SCRIPT = "(pixels) => window.scrollBy(0, pixels)"
SCROLL_STATE_SCRIPT = """
() => {
  const root = document.documentElement;
  const body = document.body;
  return {
    scrollY: window.scrollY || window.pageYOffset || 0,
    scrollHeight: Math.max(root?.scrollHeight || 0, body?.scrollHeight || 0),
    innerHeight: window.innerHeight || root?.clientHeight || 0,
  };
}
"""
SCROLL_PROGRESS_THRESHOLD_PX = 8

MORE_COMMENTS_PATTERN = re.compile(
    r"^(load|show|view|see) (more|previous) comments?$",
    re.IGNORECASE,
)
MORE_REPLIES_PATTERN = re.compile(
    r"^(load|show|view|see) (more|previous)? ?repl(?:y|ies)$",
    re.IGNORECASE,
)
LIVE_COMMENT_ROOT_SELECTOR = (
    '.comments-comment-entity[data-id^="urn:li:comment:"],'
    '[componentkey^="replaceableComment_urn:li:comment:"]'
)
LIVE_COMMENT_TEXT_SELECTOR = (
    ".comments-comment-item__main-content,"
    "[data-comment-text],"
    "[data-test-comment-text],"
    "[data-test-comment-content]"
)
LIVE_COMMENT_PROFILE_SELECTOR = (
    'a.comments-comment-meta__description-container[href*="/in/"],'
    'a.comments-comment-meta__image-link[href*="/in/"],'
    'a.comments-comment-meta__description-container[href*="/company/"],'
    'a.comments-comment-meta__image-link[href*="/company/"],'
    'a[href^="/in/"],'
    'a[href^="/company/"],'
    'a[href*="linkedin.com/in/"],'
    'a[href*="linkedin.com/company/"]'
)
LIVE_COMMENT_NAME_SELECTOR = ".comments-comment-meta__description-title"
LIVE_COMMENT_HEADLINE_SELECTOR = ".comments-comment-meta__description-subtitle"
LIVE_POST_AUTHOR_SELECTOR = (
    ".update-components-actor__title .visually-hidden,"
    ".feed-shared-actor__title .visually-hidden"
)
LIVE_POST_TEXT_SELECTOR = (
    ".feed-shared-update-v2__description .update-components-text span.break-words,"
    ".update-components-update-v2__commentary span.break-words"
)
PLAYWRITER_BIN_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BIN"
PLAYWRITER_SESSION_ENV = "LINKEDIN_TOOLS_PLAYWRITER_SESSION"
PLAYWRITER_BROWSER_KEY_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY"


@dataclass(frozen=True)
class BrowserSafetyLimits:
    max_scrolls: int = 6
    max_comment_control_clicks: int = 12
    max_reply_control_clicks: int = 8
    navigation_timeout_ms: int = 30_000
    action_timeout_ms: int = 5_000
    settle_ms: int = 750
    max_runtime_seconds: int = 90
    max_no_progress_passes: int = 2


@dataclass(frozen=True)
class ScrollState:
    scroll_y: float
    scroll_height: float
    inner_height: float


@dataclass(frozen=True)
class CommentExpansionStats:
    stop_reason: str
    scrolls_performed: int
    comment_control_clicks: int
    reply_control_clicks: int
    visible_comment_nodes: int
    runtime_seconds: float
    no_progress_passes: int
    max_no_progress_passes: int


@dataclass(frozen=True)
class BrowserExtractionInput:
    post_url: str
    source_id: str
    query_id: str
    source_kind: str = "known_post"
    source_url: str = ""
    search_query: str = ""


@dataclass(frozen=True)
class PostMetadata:
    author_name: str
    text: str


@dataclass(frozen=True)
class BrowserExtractionResult:
    run_id: str
    status: str
    raw_comments_path: Path
    html_artifact_path: Path
    comments_found: int
    warnings: tuple[str, ...]
    stop_reason: str
    expansion_stats: CommentExpansionStats


@dataclass(frozen=True)
class BrowserQueueResult:
    processed: int
    succeeded: int
    failed: int
    skipped: int
    manifest_path: Path
    checkpoint_path: Path
    provider_csv_path: Path | None


@dataclass(frozen=True)
class BrowserPreflightResult:
    ready: bool
    profile_name: str
    user_data_dir: Path
    profile_dir: Path
    profile_root_exists: bool
    profile_dir_exists: bool
    browser_checked: bool
    warnings: tuple[str, ...]

    def to_json_object(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "profile_name": self.profile_name,
            "user_data_dir": str(self.user_data_dir),
            "profile_dir": str(self.profile_dir),
            "profile_root_exists": self.profile_root_exists,
            "profile_dir_exists": self.profile_dir_exists,
            "browser_checked": self.browser_checked,
            "warnings": list(self.warnings),
        }


class BrowserExtractionError(RuntimeError):
    def __init__(self, stop_reason: str, message: str) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason


def run_browser_preflight(
    *,
    check_browser: bool = False,
) -> BrowserPreflightResult:
    return asyncio.run(browser_preflight(check_browser=check_browser))


async def browser_preflight(
    *,
    check_browser: bool = False,
) -> BrowserPreflightResult:
    warnings: list[str] = []
    if check_browser:
        try:
            _CommentExtractorPlaywriterClient(out_dir=Path.cwd()).preflight()
        except Exception as exc:
            warnings.append(f"browser_check_failed:{type(exc).__name__}")
    return BrowserPreflightResult(
        ready=not warnings,
        profile_name="Playwriter",
        user_data_dir=Path(),
        profile_dir=Path(),
        profile_root_exists=True,
        profile_dir_exists=True,
        browser_checked=check_browser,
        warnings=tuple(warnings),
    )


def extract_post_comments_from_url(
    *,
    input_row: BrowserExtractionInput,
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    progress: ProgressReporter | None = None,
) -> BrowserExtractionResult:
    return asyncio.run(
        extract_post_comments_from_url_async(
            input_row=input_row,
            output_dir=output_dir,
            store=store,
            limits=limits,
            progress=progress,
        )
    )


async def extract_post_comments_from_url_async(
    *,
    input_row: BrowserExtractionInput,
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    progress: ProgressReporter | None = None,
) -> BrowserExtractionResult:
    run_id = store.start_extraction_run(
        post_url=input_row.post_url,
        source_id=input_row.source_id,
        query_id=input_row.query_id,
        source_kind=input_row.source_kind,
        source_url=input_row.source_url,
        search_query=input_row.search_query,
        browser_profile="Playwriter",
        safety_limits=asdict(limits),
    )
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    writer = ArtifactWriter(run_dir)
    html_path = run_dir / "post.html"
    raw_path = run_dir / "raw_comments.jsonl"
    warnings: tuple[str, ...] = ()
    status = "extracted"
    comments_found = 0
    stop_reason = ""
    expansion_stats: CommentExpansionStats | None = None
    try:
        post_result = _extract_post_comments_with_playwriter(
            input_row=input_row,
            run_id=run_id,
            run_dir=run_dir,
            writer=writer,
            store=store,
            limits=limits,
            progress=progress,
        )
        html_path = post_result.html_artifact_path
        raw_path = post_result.raw_comments_path
        warnings = post_result.warnings
        comments_found = post_result.comments_found
        status = post_result.status
        stop_reason = post_result.stop_reason
        expansion_stats = post_result.expansion_stats
    except Exception as exc:
        stop_reason = _stop_reason_for_exception(exc)
        store.record_error(
            run_id=run_id,
            post_url=input_row.post_url,
            error_type=type(exc).__name__,
            message=str(exc),
            retryable=True,
        )
        store.finish_extraction_run(
            run_id,
            status="failed",
            comments_found=0,
            failures=1,
            warning_count=0,
            retry_recommendation="Inspect browser artifacts and rerun with lower safety limits",
        )
        if progress is not None:
            progress.emit(
                "comment_post_failed",
                post_url=input_row.post_url,
                stop_reason=stop_reason,
                error=type(exc).__name__,
            )
        raise
    if expansion_stats is None:
        raise RuntimeError("browser extraction completed without expansion stats")
    return BrowserExtractionResult(
        run_id=run_id,
        status=status,
        raw_comments_path=raw_path,
        html_artifact_path=html_path,
        comments_found=comments_found,
        warnings=warnings,
        stop_reason=stop_reason,
        expansion_stats=expansion_stats,
    )


def extract_post_comments_from_url_queue(
    *,
    input_rows: Sequence[BrowserExtractionInput],
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    provider_csv_path: Path | None = None,
    progress: ProgressReporter | None = None,
) -> BrowserQueueResult:
    return asyncio.run(
        extract_post_comments_from_url_queue_async(
            input_rows=input_rows,
            output_dir=output_dir,
            store=store,
            limits=limits,
            provider_csv_path=provider_csv_path,
            progress=progress,
        )
    )


async def extract_post_comments_from_url_queue_async(
    *,
    input_rows: Sequence[BrowserExtractionInput],
    output_dir: Path,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    provider_csv_path: Path | None = None,
    progress: ProgressReporter | None = None,
) -> BrowserQueueResult:
    reporter = progress or ProgressReporter(enabled=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "extract_url_queue_manifest.jsonl"
    checkpoint_path = output_dir / "extract_url_queue_checkpoint.json"
    processed_manifest_urls = _read_manifest_post_urls(manifest_path)
    queued_rows = tuple(
        (index, input_row)
        for index, input_row in enumerate(input_rows, start=1)
        if input_row.post_url not in processed_manifest_urls
    )
    skipped = len(input_rows) - len(queued_rows)
    processed = 0
    succeeded = 0
    failed = 0
    reporter.emit(
        "comment_queue_start",
        total=len(input_rows),
        remaining=len(queued_rows),
        skipped=skipped,
        output_dir=output_dir,
        provider_csv=provider_csv_path or "",
    )
    client = _CommentExtractorPlaywriterClient(out_dir=output_dir)
    for original_index, input_row in queued_rows:
        processed += 1
        reporter.emit(
            "comment_post_start",
            index=original_index,
            total=len(input_rows),
            remaining_index=processed,
            remaining_total=len(queued_rows),
            source_id=input_row.source_id,
            query_id=input_row.query_id,
            post_url=input_row.post_url,
        )
        run_id = store.start_extraction_run(
            post_url=input_row.post_url,
            source_id=input_row.source_id,
            query_id=input_row.query_id,
            source_kind=input_row.source_kind,
            source_url=input_row.source_url,
            search_query=input_row.search_query,
            browser_profile="Playwriter",
            safety_limits=asdict(limits),
        )
        run_dir = output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        writer = ArtifactWriter(run_dir)
        try:
            result = _extract_post_comments_with_playwriter(
                client=client,
                input_row=input_row,
                run_id=run_id,
                run_dir=run_dir,
                writer=writer,
                store=store,
                limits=limits,
                progress=reporter,
            )
        except Exception as exc:
            failed += 1
            stop_reason = _stop_reason_for_exception(exc)
            reporter.emit(
                "comment_post_failed",
                index=original_index,
                total=len(input_rows),
                remaining_index=processed,
                remaining_total=len(queued_rows),
                post_url=input_row.post_url,
                stop_reason=stop_reason,
                error=type(exc).__name__,
            )
            store.record_error(
                run_id=run_id,
                post_url=input_row.post_url,
                error_type=type(exc).__name__,
                message=str(exc),
                retryable=True,
            )
            store.finish_extraction_run(
                run_id,
                status="failed",
                comments_found=0,
                failures=1,
                warning_count=0,
                retry_recommendation="Inspect browser artifacts and rerun with lower safety limits",
            )
            _append_jsonl(
                manifest_path,
                {
                    "post_url": input_row.post_url,
                    "run_id": run_id,
                    "status": "failed",
                    "comments_found": 0,
                    "stop_reason": stop_reason,
                    "error_type": type(exc).__name__,
                },
            )
        else:
            succeeded += 1
            reporter.emit(
                "comment_post_done",
                index=original_index,
                total=len(input_rows),
                remaining_index=processed,
                remaining_total=len(queued_rows),
                post_url=input_row.post_url,
                comments_found=result.comments_found,
                stop_reason=result.stop_reason,
            )
            if provider_csv_path is not None:
                write_comment_csv(provider_csv_path, store.export_comments())
            _append_jsonl(
                manifest_path,
                {
                    "post_url": input_row.post_url,
                    "run_id": run_id,
                    "status": result.status,
                    "comments_found": result.comments_found,
                    "stop_reason": result.stop_reason,
                    "scrolls_performed": result.expansion_stats.scrolls_performed,
                    "comment_control_clicks": result.expansion_stats.comment_control_clicks,
                    "reply_control_clicks": result.expansion_stats.reply_control_clicks,
                    "runtime_seconds": result.expansion_stats.runtime_seconds,
                    "raw_comments_path": str(result.raw_comments_path),
                    "provider_csv_path": (
                        str(provider_csv_path) if provider_csv_path is not None else ""
                    ),
                },
            )
        _write_json_atomic(
            checkpoint_path,
            {
                "processed": processed,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "remaining": len(queued_rows) - processed,
                "total": len(input_rows),
                "provider_csv_path": str(provider_csv_path) if provider_csv_path else "",
            },
        )
    reporter.emit(
        "comment_queue_done",
        processed=processed,
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        manifest=manifest_path,
    )
    return BrowserQueueResult(
        processed=processed,
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        manifest_path=manifest_path,
        checkpoint_path=checkpoint_path,
        provider_csv_path=provider_csv_path,
    )


def _read_manifest_post_urls(manifest_path: Path) -> frozenset[str]:
    if not manifest_path.exists():
        return frozenset()
    post_urls: set[str] = set()
    with manifest_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            clean_line = line.strip()
            if not clean_line:
                continue
            payload = json.loads(clean_line)
            if not isinstance(payload, dict):
                raise ValueError(f"manifest line {line_number} must be a JSON object")
            post_url = payload.get("post_url")
            if not isinstance(post_url, str) or not post_url.strip():
                raise ValueError(f"manifest line {line_number} missing text post_url")
            post_urls.add(post_url)
    return frozenset(post_urls)


class _CommentExtractorPlaywriterClient:
    def __init__(
        self,
        *,
        out_dir: Path,
        session: str | None = None,
        browser_key: str | None = None,
        playwriter_bin: str | None = None,
    ) -> None:
        self.out_dir = out_dir
        self._session = session or os.environ.get(PLAYWRITER_SESSION_ENV)
        self._browser_key = browser_key or os.environ.get(PLAYWRITER_BROWSER_KEY_ENV)
        self._playwriter_bin = playwriter_bin or _playwriter_bin()

    @property
    def session(self) -> str:
        if self._session is None:
            self._session = self._create_session()
        return self._session

    def preflight(self) -> None:
        self._run_script(
            _playwriter_preflight_script(),
            {"out": str(self.out_dir / "preflight.json")},
        )

    def extract(
        self,
        *,
        input_row: BrowserExtractionInput,
        run_id: str,
        run_dir: Path,
        limits: BrowserSafetyLimits,
    ) -> dict[str, object]:
        out = run_dir / "playwriter-extraction.json"
        config = {
            "input": asdict(input_row),
            "runId": run_id,
            "runDir": str(run_dir),
            "limits": asdict(limits),
            "selectors": {
                "commentRoot": LIVE_COMMENT_ROOT_SELECTOR,
                "commentText": LIVE_COMMENT_TEXT_SELECTOR,
                "commentProfile": LIVE_COMMENT_PROFILE_SELECTOR,
                "commentName": LIVE_COMMENT_NAME_SELECTOR,
                "commentHeadline": LIVE_COMMENT_HEADLINE_SELECTOR,
                "postAuthor": LIVE_POST_AUTHOR_SELECTOR,
                "postText": LIVE_POST_TEXT_SELECTOR,
            },
            "out": str(out),
        }
        self._run_script(_playwriter_extract_script(), config)
        payload = json.loads(out.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Playwriter comment extraction output must be a JSON object")
        return payload

    def _create_session(self) -> str:
        command = [self._playwriter_bin, "session", "new"]
        if self._browser_key:
            command.extend(["--browser", self._browser_key])
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        match = re.search(r"Session\s+(\S+)\s+created", result.stdout)
        if not match:
            raise RuntimeError(f"could not parse Playwriter session id from: {result.stdout}")
        return match.group(1)

    def _run_script(self, script: Path, config: Mapping[str, object]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        config_path, staged_out, final_out = _stage_playwriter_config(config)
        script_config = dict(config)
        if staged_out is not None:
            script_config["out"] = str(staged_out)
        _write_json_atomic(config_path, script_config)
        _run_playwriter_command(
            [
                self._playwriter_bin,
                "-s",
                self.session,
                "-e",
                f"state.linkedinToolsConfigPath = {json.dumps(str(config_path))}",
            ]
        )
        _run_playwriter_command(
            [self._playwriter_bin, "-s", self.session, "-f", str(script), "--timeout", "120000"],
        )
        if staged_out is not None and final_out is not None:
            if not _wait_for_path(staged_out):
                raise RuntimeError(
                    "Playwriter comment extractor script did not write an output artifact; "
                    f"expected {staged_out}"
                )
            final_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_out), str(final_out))


def _extract_post_comments_with_playwriter(
    *,
    input_row: BrowserExtractionInput,
    run_id: str,
    run_dir: Path,
    writer: ArtifactWriter,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    progress: ProgressReporter | None = None,
    client: _CommentExtractorPlaywriterClient | None = None,
) -> BrowserExtractionResult:
    playwriter = client or _CommentExtractorPlaywriterClient(out_dir=run_dir)
    artifact = playwriter.extract(
        input_row=input_row,
        run_id=run_id,
        run_dir=run_dir,
        limits=limits,
    )
    status = _string_value(artifact.get("status"))
    if status != "extracted":
        raise BrowserExtractionError(
            _string_value(artifact.get("stop_reason")) or _string_value(artifact.get("blocker")),
            _string_value(artifact.get("error")) or f"Playwriter status={status!r}",
        )
    html_path = Path(_string_value(artifact.get("html_path")) or run_dir / "post.html")
    raw_rows = artifact.get("rows")
    post_metadata = _post_metadata_from_artifact(artifact.get("post_metadata"))
    extraction = _comments_from_page_rows(
        raw_rows,
        input_row=input_row,
        post_metadata=post_metadata,
    )
    expansion_stats = _expansion_stats_from_artifact(artifact.get("expansion"))
    warnings = (
        *_list_of_strings(artifact.get("warnings")),
        *extraction.warnings,
    )
    store.record_artifact(run_id=run_id, kind="html", path=html_path)
    screenshot_path = _string_value(artifact.get("screenshot_path"))
    if screenshot_path:
        store.record_artifact(run_id=run_id, kind="screenshot", path=Path(screenshot_path))
    raw_path = write_raw_comments_jsonl(extraction.comments, run_dir)
    store.record_artifact(
        run_id=run_id,
        kind="raw_comments",
        path=raw_path,
        metadata={"comment_count": len(extraction.comments)},
    )
    store.persist_comments(
        run_id=run_id,
        comments=extraction.comments,
        query_pack=load_query_pack(),
    )
    summary_ref = writer.write_json(
        "summary",
        {
            "run_id": run_id,
            "post_url": input_row.post_url,
            "comments_found": len(extraction.comments),
            "stop_reason": expansion_stats.stop_reason,
            "scrolls_performed": expansion_stats.scrolls_performed,
            "comment_control_clicks": expansion_stats.comment_control_clicks,
            "reply_control_clicks": expansion_stats.reply_control_clicks,
            "runtime_seconds": expansion_stats.runtime_seconds,
            "warnings": list(warnings),
            "safety_limits": asdict(limits),
            "expansion": asdict(expansion_stats),
            "browser_backend": "playwriter",
        },
    )
    store.record_artifact(run_id=run_id, kind="summary", path=summary_ref.path)
    store.finish_extraction_run(
        run_id,
        status="extracted",
        comments_found=len(extraction.comments),
        failures=0,
        warning_count=len(warnings),
        retry_recommendation="No retry needed" if extraction.comments else "Review HTML artifact",
    )
    _emit_progress(
        progress,
        "comment_expand_done",
        stop_reason=expansion_stats.stop_reason,
        scrolls=expansion_stats.scrolls_performed,
        comment_clicks=expansion_stats.comment_control_clicks,
        reply_clicks=expansion_stats.reply_control_clicks,
        comments=expansion_stats.visible_comment_nodes,
        runtime_seconds=expansion_stats.runtime_seconds,
    )
    return BrowserExtractionResult(
        run_id=run_id,
        status="extracted",
        raw_comments_path=raw_path,
        html_artifact_path=html_path,
        comments_found=len(extraction.comments),
        warnings=warnings,
        stop_reason=expansion_stats.stop_reason,
        expansion_stats=expansion_stats,
    )


async def _extract_post_comments_with_page(
    *,
    page: Any,
    input_row: BrowserExtractionInput,
    run_id: str,
    run_dir: Path,
    writer: ArtifactWriter,
    store: OpportunityStore,
    limits: BrowserSafetyLimits,
    progress: ProgressReporter | None = None,
) -> BrowserExtractionResult:
    html_path = run_dir / "post.html"
    try:
        await page.goto(
            input_row.post_url,
            wait_until="domcontentloaded",
            timeout=limits.navigation_timeout_ms,
        )
    except TimeoutError as exc:
        raise BrowserExtractionError("navigation_timeout", str(exc)) from exc
    expansion_stats = await _expand_visible_comment_controls(
        page,
        limits,
        progress=progress,
    )
    html = await page.content()
    html_path.write_text(html, encoding="utf-8")
    store.record_artifact(run_id=run_id, kind="html", path=html_path)
    screenshot_warnings = await _capture_optional_screenshot(
        page=page,
        run_id=run_id,
        writer=writer,
        store=store,
    )
    extraction = await extract_comments_from_page(page=page, input_row=input_row)
    warnings = (*screenshot_warnings, *extraction.warnings)
    raw_path = write_raw_comments_jsonl(extraction.comments, run_dir)
    store.record_artifact(
        run_id=run_id,
        kind="raw_comments",
        path=raw_path,
        metadata={"comment_count": len(extraction.comments)},
    )
    store.persist_comments(
        run_id=run_id,
        comments=extraction.comments,
        query_pack=load_query_pack(),
    )
    summary_ref = writer.write_json(
        "summary",
        {
            "run_id": run_id,
            "post_url": input_row.post_url,
            "comments_found": len(extraction.comments),
            "stop_reason": expansion_stats.stop_reason,
            "scrolls_performed": expansion_stats.scrolls_performed,
            "comment_control_clicks": expansion_stats.comment_control_clicks,
            "reply_control_clicks": expansion_stats.reply_control_clicks,
            "runtime_seconds": expansion_stats.runtime_seconds,
            "warnings": list(warnings),
            "safety_limits": asdict(limits),
            "expansion": asdict(expansion_stats),
        },
    )
    store.record_artifact(run_id=run_id, kind="summary", path=summary_ref.path)
    store.finish_extraction_run(
        run_id,
        status="extracted",
        comments_found=len(extraction.comments),
        failures=0,
        warning_count=len(warnings),
        retry_recommendation="No retry needed" if extraction.comments else "Review HTML artifact",
    )
    return BrowserExtractionResult(
        run_id=run_id,
        status="extracted",
        raw_comments_path=raw_path,
        html_artifact_path=html_path,
        comments_found=len(extraction.comments),
        warnings=warnings,
        stop_reason=expansion_stats.stop_reason,
        expansion_stats=expansion_stats,
    )


async def _capture_optional_screenshot(
    *,
    page: Any,
    run_id: str,
    writer: ArtifactWriter,
    store: OpportunityStore,
) -> tuple[str, ...]:
    try:
        screenshot = await writer.screenshot(page, "post-comments", full_page=True)
    except Exception as exc:
        return (f"screenshot_capture_failed:{type(exc).__name__}",)
    store.record_artifact(run_id=run_id, kind="screenshot", path=screenshot.path)
    return ()


async def extract_comments_from_page(
    *,
    page: Any,
    input_row: BrowserExtractionInput,
) -> ExtractionResult:
    post_metadata = await _extract_post_metadata(page)
    rows = await page.locator(LIVE_COMMENT_ROOT_SELECTOR).evaluate_all(
        """
        (nodes, selectors) => {
          const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
          const nodeText = (node) => {
            if (!node) return "";
            const parts = [];
            const visit = (current) => {
              if (current.nodeType === Node.TEXT_NODE) {
                parts.push(current.nodeValue || "");
                return;
              }
              if (current.nodeType !== Node.ELEMENT_NODE) return;
              if (current.tagName === "BR") {
                parts.push("\\n");
                return;
              }
              current.childNodes.forEach(visit);
            };
            visit(node);
            return clean(parts.join(""));
          };
          const selectText = (node, selector) => nodeText(node.querySelector(selector));
          const [profileSelector, textSelector, nameSelector, headlineSelector] = selectors;
          return nodes.map((node) => {
            const profile = node.querySelector(profileSelector);
            const time = node.querySelector("time");
            const name = selectText(node, nameSelector) || clean(profile?.textContent);
            return {
              comment_id: node.getAttribute("data-id") || node.getAttribute("componentkey") || "",
              commenter_headline: selectText(node, headlineSelector),
              commenter_name: name,
              commenter_profile_url: profile?.getAttribute("href") || "",
              comment_text: selectText(node, textSelector),
              commented_at: time?.getAttribute("datetime") || clean(time?.textContent),
            };
          });
        }
        """,
        arg=[
            LIVE_COMMENT_PROFILE_SELECTOR,
            LIVE_COMMENT_TEXT_SELECTOR,
            LIVE_COMMENT_NAME_SELECTOR,
            LIVE_COMMENT_HEADLINE_SELECTOR,
        ],
    )
    return _comments_from_page_rows(
        rows,
        input_row=input_row,
        post_metadata=post_metadata,
    )


async def _extract_post_metadata(page: Any) -> PostMetadata:
    payload = await page.evaluate(
        """
        (selectors) => {
          const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
          const nodeText = (node) => {
            if (!node) return "";
            const parts = [];
            const visit = (current) => {
              if (current.nodeType === Node.TEXT_NODE) {
                parts.push(current.nodeValue || "");
                return;
              }
              if (current.nodeType !== Node.ELEMENT_NODE) return;
              if (current.tagName === "BR") {
                parts.push("\\n");
                return;
              }
              current.childNodes.forEach(visit);
            };
            visit(node);
            return clean(parts.join(""));
          };
          const [authorSelector, textSelector] = selectors;
          return {
            author_name: nodeText(document.querySelector(authorSelector)),
            text: nodeText(document.querySelector(textSelector)),
          };
        }
        """,
        [LIVE_POST_AUTHOR_SELECTOR, LIVE_POST_TEXT_SELECTOR],
    )
    if not isinstance(payload, dict):
        raise ValueError("live post metadata extraction must return an object")
    return PostMetadata(
        author_name=_clean_text(_string_value(payload.get("author_name"))),
        text=_clean_text(_string_value(payload.get("text"))),
    )


def _comments_from_page_rows(
    rows: object,
    *,
    input_row: BrowserExtractionInput,
    post_metadata: PostMetadata | None = None,
) -> ExtractionResult:
    if not isinstance(rows, list):
        raise ValueError("live comment extraction must return a list")
    metadata = post_metadata or PostMetadata(author_name="", text="")
    comments: list[CommentEvidence] = []
    warnings: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError("live comment extraction rows must be objects")
        comment = CommentEvidence(
            query_id=input_row.query_id,
            source_id=input_row.source_id,
            source_kind=input_row.source_kind,
            source_url=input_row.source_url or input_row.post_url,
            search_query=input_row.search_query,
            post_url=input_row.post_url,
            post_author_name=metadata.author_name,
            post_text=metadata.text,
            comment_id=_string_value(row.get("comment_id")),
            comment_url="",
            commenter_name=_clean_text(_string_value(row.get("commenter_name"))),
            commenter_profile_url=_canonical_linkedin_url(
                _string_value(row.get("commenter_profile_url"))
            ),
            commenter_headline=_clean_text(_string_value(row.get("commenter_headline"))),
            commenter_company="",
            relationship="",
            comment_text=_clean_text(_string_value(row.get("comment_text"))),
            commented_at=_clean_text(_string_value(row.get("commented_at"))),
        )
        comment_warnings = _live_comment_warnings(comment)
        warnings.extend(f"comment_{index}:{warning}" for warning in comment_warnings)
        comments.append(replace(comment, warnings=comment_warnings))
    if not comments:
        warnings.append("no_live_linkedin_comment_nodes_found")
    return ExtractionResult(comments=tuple(comments), warnings=tuple(warnings))


async def _expand_visible_comment_controls(
    page: Any,
    limits: BrowserSafetyLimits,
    *,
    progress: ProgressReporter | None = None,
) -> CommentExpansionStats:
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    deadline = started_at + limits.max_runtime_seconds
    max_no_progress_passes = max(1, limits.max_no_progress_passes)
    remaining_comment_clicks = max(0, limits.max_comment_control_clicks)
    remaining_reply_clicks = max(0, limits.max_reply_control_clicks)
    comment_clicks = 0
    reply_clicks = 0
    scrolls = 0
    no_progress_passes = 0
    stop_reason = "max_scrolls_reached"
    previous_comments = await _count_visible_comments(page)
    previous_scroll = await _read_scroll_state(page)
    visible_comments = previous_comments

    for pass_number in range(1, max(0, limits.max_scrolls) + 1):
        if loop.time() >= deadline:
            stop_reason = "max_runtime_reached"
            break
        try:
            comment_clicked = await _click_controls(
                page,
                MORE_COMMENTS_PATTERN,
                remaining_comment_clicks,
            )
            remaining_comment_clicks -= comment_clicked
            comment_clicks += comment_clicked
            reply_clicked = await _click_controls(
                page,
                MORE_REPLIES_PATTERN,
                remaining_reply_clicks,
            )
            remaining_reply_clicks -= reply_clicked
            reply_clicks += reply_clicked
            await _scroll_page_down(page, 1800)
            scrolls += 1
            await page.wait_for_timeout(limits.settle_ms)
        except TimeoutError:
            stop_reason = "action_timeout"
            break

        visible_comments = await _count_visible_comments(page)
        current_scroll = await _read_scroll_state(page)
        new_comments = max(0, visible_comments - previous_comments)
        scroll_changed = _scroll_changed(previous_scroll, current_scroll)
        pass_had_progress = (
            new_comments > 0 or comment_clicked > 0 or reply_clicked > 0 or scroll_changed
        )
        no_progress_passes = 0 if pass_had_progress else no_progress_passes + 1
        _emit_progress(
            progress,
            "comment_expand_pass",
            pass_number=pass_number,
            comments=visible_comments,
            new_comments=new_comments,
            comment_clicks=comment_clicked,
            reply_clicks=reply_clicked,
            scroll_changed=str(scroll_changed).lower(),
            no_progress_passes=no_progress_passes,
        )
        previous_comments = visible_comments
        previous_scroll = current_scroll
        if loop.time() >= deadline:
            stop_reason = "max_runtime_reached"
            break
        if no_progress_passes >= max_no_progress_passes:
            stop_reason = "no_more_content"
            break

    stats = CommentExpansionStats(
        stop_reason=stop_reason,
        scrolls_performed=scrolls,
        comment_control_clicks=comment_clicks,
        reply_control_clicks=reply_clicks,
        visible_comment_nodes=visible_comments,
        runtime_seconds=round(loop.time() - started_at, 3),
        no_progress_passes=no_progress_passes,
        max_no_progress_passes=max_no_progress_passes,
    )
    _emit_progress(
        progress,
        "comment_expand_done",
        stop_reason=stats.stop_reason,
        scrolls=stats.scrolls_performed,
        comment_clicks=stats.comment_control_clicks,
        reply_clicks=stats.reply_control_clicks,
        comments=stats.visible_comment_nodes,
        runtime_seconds=stats.runtime_seconds,
    )
    return stats


async def _scroll_page_down(page: Any, pixels: int) -> None:
    await page.evaluate(SCROLL_BY_SCRIPT, pixels)


async def _click_controls(page: Any, pattern: re.Pattern[str], limit: int) -> int:
    if limit <= 0:
        return 0
    locator = page.get_by_role("button", name=pattern)
    try:
        count = min(await locator.count(), limit)
    except TimeoutError:
        return 0
    clicked = 0
    for index in range(count):
        button = locator.nth(index)
        try:
            if await button.is_visible() and await button.is_enabled():
                await button.click()
                clicked += 1
                await page.wait_for_timeout(250)
        except TimeoutError:
            continue
    return clicked


async def _count_visible_comments(page: Any) -> int:
    try:
        return int(await page.locator(LIVE_COMMENT_ROOT_SELECTOR).count())
    except TimeoutError:
        return 0


async def _read_scroll_state(page: Any) -> ScrollState:
    try:
        payload = await page.evaluate(SCROLL_STATE_SCRIPT)
    except TimeoutError:
        return ScrollState(scroll_y=0.0, scroll_height=0.0, inner_height=0.0)
    if not isinstance(payload, dict):
        return ScrollState(scroll_y=0.0, scroll_height=0.0, inner_height=0.0)
    return ScrollState(
        scroll_y=_float_value(payload.get("scrollY")),
        scroll_height=_float_value(payload.get("scrollHeight")),
        inner_height=_float_value(payload.get("innerHeight")),
    )


def _scroll_changed(previous: ScrollState, current: ScrollState) -> bool:
    return (
        abs(current.scroll_y - previous.scroll_y) >= SCROLL_PROGRESS_THRESHOLD_PX
        or abs(current.scroll_height - previous.scroll_height) >= SCROLL_PROGRESS_THRESHOLD_PX
    )


def _emit_progress(
    progress: ProgressReporter | None,
    event: str,
    **fields: object,
) -> None:
    if progress is not None:
        progress.emit(event, **fields)


async def _reusable_page(context: Any) -> Any:
    fragments = (
        "linkedin.com/posts/",
        "linkedin.com/feed/update/",
        "linkedin.com",
    )
    session = BrowserSession(
        context,
        PageReusePolicy(preferred_url_fragments=fragments, foreground=False),
    )
    return cast(Any, await session.page(preferred_url_fragments=fragments))


def _expansion_stats_from_artifact(value: object) -> CommentExpansionStats:
    if not isinstance(value, dict):
        raise ValueError("Playwriter expansion artifact must be an object")
    return CommentExpansionStats(
        stop_reason=_string_value(value.get("stop_reason")) or "unknown",
        scrolls_performed=int(_float_value(value.get("scrolls_performed"))),
        comment_control_clicks=int(_float_value(value.get("comment_control_clicks"))),
        reply_control_clicks=int(_float_value(value.get("reply_control_clicks"))),
        visible_comment_nodes=int(_float_value(value.get("visible_comment_nodes"))),
        runtime_seconds=_float_value(value.get("runtime_seconds")),
        no_progress_passes=int(_float_value(value.get("no_progress_passes"))),
        max_no_progress_passes=int(_float_value(value.get("max_no_progress_passes"))),
    )


def _post_metadata_from_artifact(value: object) -> PostMetadata:
    if not isinstance(value, dict):
        return PostMetadata(author_name="", text="")
    return PostMetadata(
        author_name=_clean_text(_string_value(value.get("author_name"))),
        text=_clean_text(_string_value(value.get("text"))),
    )


def _list_of_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _playwriter_bin() -> str:
    configured = os.environ.get(PLAYWRITER_BIN_ENV)
    if configured:
        return configured
    default = Path.home() / ".bun/bin/playwriter"
    if default.exists():
        return str(default)
    resolved = shutil.which("playwriter")
    if resolved:
        return resolved
    raise RuntimeError("Playwriter binary was not found; set LINKEDIN_TOOLS_PLAYWRITER_BIN")


def _playwriter_script_dir() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts"


def _playwriter_extract_script() -> Path:
    return _playwriter_script_dir() / "extract_post_comments.js"


def _playwriter_preflight_script() -> Path:
    return _playwriter_script_dir() / "preflight.js"


def _stage_playwriter_config(config: Mapping[str, object]) -> tuple[Path, Path | None, Path | None]:
    staging_dir = Path(tempfile.gettempdir()) / "linkedin-tools-playwriter"
    staging_dir.mkdir(parents=True, exist_ok=True)
    final_out = Path(str(config["out"])) if config.get("out") else None
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", final_out.stem if final_out else "comments")
    config_path = staging_dir / f"{stem}-config.json"
    staged_out = staging_dir / f"{stem}-out.json" if final_out is not None else None
    return config_path, staged_out, final_out


def _wait_for_path(path: Path, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def _run_playwriter_command(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        )
        raise RuntimeError(
            f"Playwriter command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )


def _string_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _float_value(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _canonical_linkedin_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/"):
        href = "https://www.linkedin.com" + href
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.endswith("linkedin.com"):
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _live_comment_warnings(comment: CommentEvidence) -> tuple[str, ...]:
    warnings: list[str] = []
    if not comment.comment_id:
        warnings.append("comment_id_missing")
    if not comment.commenter_profile_url:
        warnings.append("commenter_profile_url_missing")
    if not comment.commenter_name:
        warnings.append("commenter_name_missing")
    if not comment.comment_text:
        warnings.append("comment_text_missing_explicit_selector")
    return tuple(warnings)


def _stop_reason_for_exception(exc: Exception) -> str:
    if isinstance(exc, BrowserExtractionError):
        return exc.stop_reason
    if isinstance(exc, TimeoutError):
        return "action_timeout"
    if isinstance(exc, Exception):
        return "browser_error"
    return "browser_error"


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def write_preflight_artifact(
    *,
    store: OpportunityStore,
    result: BrowserPreflightResult,
) -> Path:
    run_id = store.start_extraction_run(
        post_url="",
        source_id="preflight",
        query_id="preflight",
        source_kind="preflight",
        source_url="",
        search_query="",
        browser_profile=result.profile_name,
        safety_limits={},
        status="preflight",
    )
    output_dir = store.artifact_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "browser_preflight.json"
    path.write_text(json.dumps(result.to_json_object(), indent=2, sort_keys=True) + "\n")
    store.record_artifact(
        run_id=run_id,
        kind="browser_preflight",
        path=path,
        status="ok" if result.ready else "warning",
        retryable_error=";".join(result.warnings),
    )
    store.finish_extraction_run(
        run_id,
        status="preflight_passed" if result.ready else "preflight_warning",
        comments_found=0,
        failures=0 if result.ready else 1,
        warning_count=len(result.warnings),
        retry_recommendation="No retry needed" if result.ready else "Fix browser setup warnings",
    )
    return path
