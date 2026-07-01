"""Browser-backed LinkedIn search result post capture."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus

from apps.opportunity_intel.company_pages import canonicalize_linkedin_post_url
from apps.opportunity_intel.post_discovery import PostCandidate, prioritize_posts
from apps.opportunity_intel.post_prefilter import POST_QUEUE_COLUMNS, read_post_queue
from packages.linkedin_browser.sessions import BrowserSession, PageReusePolicy
from packages.linkedin_common.progress import ProgressReporter

POST_MENU_PATTERN = re.compile(r"open control menu for post by", re.IGNORECASE)
COPY_LINK_PATTERN = re.compile(r"^Copy link to post$", re.IGNORECASE)
COPY_CAPTURE_INSTALL_SCRIPT = """
() => {
  const clipboard = navigator.clipboard;
  if (!clipboard || typeof clipboard.writeText !== "function") {
    throw new Error("navigator.clipboard.writeText is unavailable");
  }
  const state = {
    writes: [],
    originalWriteText: clipboard.writeText.bind(clipboard),
  };
  const captureWriteText = async (text) => {
    state.writes.push(String(text));
    return undefined;
  };
  Object.defineProperty(clipboard, "writeText", {
    configurable: true,
    writable: true,
    value: captureWriteText,
  });
  window.__linkedinPostCopyCapture = state;
}
"""
COPY_CAPTURE_READ_SCRIPT = """
() => {
  const state = window.__linkedinPostCopyCapture;
  if (!state || !Array.isArray(state.writes) || state.writes.length === 0) {
    return "";
  }
  return state.writes.at(-1) || "";
}
"""
COPY_CAPTURE_RESTORE_SCRIPT = """
() => {
  const state = window.__linkedinPostCopyCapture;
  if (state && state.originalWriteText && navigator.clipboard) {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      writable: true,
      value: state.originalWriteText,
    });
  }
  delete window.__linkedinPostCopyCapture;
}
"""
COPY_CAPTURE_TIMEOUT_MS = 1_000
PLAYWRITER_BIN_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BIN"
PLAYWRITER_SESSION_ENV = "LINKEDIN_TOOLS_PLAYWRITER_SESSION"
PLAYWRITER_BROWSER_KEY_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY"


class PostCopyCaptureError(RuntimeError):
    """LinkedIn's copy action did not publish a post URL through the page API."""


@dataclass(frozen=True)
class SearchCaptureLimits:
    max_results_per_search: int = 50
    max_scrolls: int = 20
    scroll_pixels: int = 1800
    navigation_timeout_ms: int = 30_000
    action_timeout_ms: int = 5_000
    settle_ms: int = 1_000


@dataclass(frozen=True)
class SearchCaptureInput:
    candidate: PostCandidate
    capture_url: str


@dataclass(frozen=True)
class SearchCapturePlan:
    known_posts: tuple[PostCandidate, ...]
    search_inputs: tuple[SearchCaptureInput, ...]
    skipped_rows: tuple[PostCandidate, ...]


@dataclass(frozen=True)
class SearchCaptureResult:
    processed_searches: int
    known_posts: int
    captured_posts: int
    duplicate_posts: int
    failed_searches: int
    output_path: Path
    metrics_path: Path
    checkpoint_path: Path

    def to_json_object(self) -> dict[str, object]:
        payload = asdict(self)
        payload["output_path"] = str(self.output_path)
        payload["metrics_path"] = str(self.metrics_path)
        payload["checkpoint_path"] = str(self.checkpoint_path)
        return payload


def plan_search_capture(candidates: Sequence[PostCandidate]) -> SearchCapturePlan:
    known_posts: list[PostCandidate] = []
    search_inputs: list[SearchCaptureInput] = []
    skipped_rows: list[PostCandidate] = []
    seen_capture_urls: set[str] = set()
    for candidate in candidates:
        if candidate.post_url:
            known_posts.append(candidate)
            continue
        capture_url = search_capture_url(candidate)
        if capture_url:
            if capture_url in seen_capture_urls:
                skipped_rows.append(candidate)
                continue
            seen_capture_urls.add(capture_url)
            search_inputs.append(SearchCaptureInput(candidate=candidate, capture_url=capture_url))
        else:
            skipped_rows.append(candidate)
    return SearchCapturePlan(
        known_posts=tuple(known_posts),
        search_inputs=tuple(search_inputs),
        skipped_rows=tuple(skipped_rows),
    )


def search_capture_url(candidate: PostCandidate) -> str:
    if "linkedin.com/search/results" in candidate.source_url:
        return candidate.source_url
    if candidate.search_query:
        return "https://www.linkedin.com/search/results/content/?keywords=" + quote_plus(
            candidate.search_query
        )
    return ""


def capture_search_posts_from_queue(
    *,
    post_queue_path: Path,
    output_path: Path,
    metrics_path: Path,
    checkpoint_path: Path,
    limits: SearchCaptureLimits,
    progress: ProgressReporter | None = None,
) -> SearchCaptureResult:
    return asyncio.run(
        capture_search_posts_from_queue_async(
            post_queue_path=post_queue_path,
            output_path=output_path,
            metrics_path=metrics_path,
            checkpoint_path=checkpoint_path,
            limits=limits,
            progress=progress,
        )
    )


async def capture_search_posts_from_queue_async(
    *,
    post_queue_path: Path,
    output_path: Path,
    metrics_path: Path,
    checkpoint_path: Path,
    limits: SearchCaptureLimits,
    progress: ProgressReporter | None = None,
) -> SearchCaptureResult:
    candidates = read_post_queue(post_queue_path)
    plan = plan_search_capture(candidates)
    reporter = progress or ProgressReporter(enabled=False)
    seen_post_urls: set[str] = set()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    _initialize_post_queue_output(output_path)
    _initialize_jsonl(metrics_path)
    known_posts_written = _write_known_posts(
        output_path=output_path,
        metrics_path=metrics_path,
        known_posts=plan.known_posts,
        seen_post_urls=seen_post_urls,
    )
    _append_jsonl(
        metrics_path,
        {
            "event": "capture_start",
            "post_queue_path": str(post_queue_path),
            "known_posts": len(plan.known_posts),
            "search_inputs": len(plan.search_inputs),
            "skipped_rows": len(plan.skipped_rows),
            "limits": asdict(limits),
        },
    )
    reporter.emit(
        "search_capture_start",
        searches=len(plan.search_inputs),
        known_posts=len(plan.known_posts),
        output=output_path,
    )

    processed_searches = 0
    captured_posts = 0
    duplicate_posts = 0
    failed_searches = 0
    client = _SearchCapturePlaywriterClient(out_dir=metrics_path.parent)
    for search_index, search_input in enumerate(plan.search_inputs, start=1):
        processed_searches += 1
        before_count = len(seen_post_urls)
        reporter.emit(
            "search_start",
            index=search_index,
            total=len(plan.search_inputs),
            source_id=search_input.candidate.source_id,
            query_id=search_input.candidate.query_id,
            query=search_input.candidate.search_query,
        )
        try:
            artifact = client.capture_search(search_input=search_input, limits=limits)
            single_result = _import_search_capture_artifact(
                artifact=artifact,
                search_input=search_input,
                output_path=output_path,
                metrics_path=metrics_path,
                seen_post_urls=seen_post_urls,
            )
        except Exception as exc:
            failed_searches += 1
            _append_jsonl(
                metrics_path,
                {
                    "event": "search_failed",
                    "source_id": search_input.candidate.source_id,
                    "query_id": search_input.candidate.query_id,
                    "search_query": search_input.candidate.search_query,
                    "capture_url": search_input.capture_url,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            reporter.emit(
                "search_failed",
                index=search_index,
                total=len(plan.search_inputs),
                source_id=search_input.candidate.source_id,
                error=type(exc).__name__,
            )
        else:
            captured_posts += single_result["captured"]
            duplicate_posts += single_result["duplicates"]
            reporter.emit(
                "search_done",
                index=search_index,
                total=len(plan.search_inputs),
                source_id=search_input.candidate.source_id,
                new_posts=len(seen_post_urls) - before_count,
            )
        _write_checkpoint(
            checkpoint_path,
            {
                "processed_searches": processed_searches,
                "total_searches": len(plan.search_inputs),
                "known_posts": known_posts_written,
                "captured_posts": captured_posts,
                "duplicate_posts": duplicate_posts,
                "failed_searches": failed_searches,
                "output_path": str(output_path),
                "metrics_path": str(metrics_path),
            },
        )

    result = SearchCaptureResult(
        processed_searches=processed_searches,
        known_posts=known_posts_written,
        captured_posts=captured_posts,
        duplicate_posts=duplicate_posts,
        failed_searches=failed_searches,
        output_path=output_path,
        metrics_path=metrics_path,
        checkpoint_path=checkpoint_path,
    )
    _append_jsonl(metrics_path, {"event": "capture_done", **result.to_json_object()})
    reporter.emit(
        "search_capture_done",
        processed_searches=processed_searches,
        captured_posts=captured_posts,
        failed_searches=failed_searches,
    )
    return result


class _SearchCapturePlaywriterClient:
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

    def capture_search(
        self,
        *,
        search_input: SearchCaptureInput,
        limits: SearchCaptureLimits,
    ) -> dict[str, object]:
        out = self._next_output_path("search-capture")
        config = {
            "candidate": search_input.candidate.__dict__,
            "captureUrl": search_input.capture_url,
            "limits": asdict(limits),
            "out": str(out),
        }
        self._run_script(_playwriter_search_capture_script(), config)
        payload = json.loads(out.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Playwriter search capture output must be a JSON object")
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
                    "Playwriter search capture did not write an output artifact; "
                    f"expected {staged_out}"
                )
            final_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_out), str(final_out))

    def _next_output_path(self, stem: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.out_dir.glob(f"*-{stem}.json"))
        return self.out_dir / f"{len(existing) + 1:03d}-{stem}.json"


def _import_search_capture_artifact(
    *,
    artifact: dict[str, object],
    search_input: SearchCaptureInput,
    output_path: Path,
    metrics_path: Path,
    seen_post_urls: set[str],
) -> dict[str, int]:
    status = artifact.get("status")
    if status != "captured":
        reason = _string_value(artifact.get("blocker")) or _string_value(artifact.get("error"))
        raise RuntimeError(reason or f"Playwriter search capture status={status!r}")
    captured = 0
    duplicates = 0
    for warning in _list_of_strings(artifact.get("warnings")):
        _append_capture_metric(
            metrics_path=metrics_path,
            event="capture_warning",
            search_input=search_input,
            reason=warning,
        )
    for raw_post_url in _list_of_strings(artifact.get("postUrls")):
        post_url = canonicalize_linkedin_post_url(raw_post_url)
        if not post_url:
            _append_capture_metric(
                metrics_path=metrics_path,
                event="copied_url_rejected",
                search_input=search_input,
                copied_url=raw_post_url,
                reason="not_linkedin_post_url",
            )
            continue
        if post_url in seen_post_urls:
            duplicates += 1
            _append_capture_metric(
                metrics_path=metrics_path,
                event="duplicate_post",
                search_input=search_input,
                post_url=post_url,
            )
            continue
        seen_post_urls.add(post_url)
        captured += 1
        candidate = PostCandidate(
            source_id=search_input.candidate.source_id,
            source_kind=search_input.candidate.source_kind,
            query_id=search_input.candidate.query_id,
            post_url=post_url,
            source_url=search_input.candidate.source_url,
            search_query=search_input.candidate.search_query,
            priority=search_input.candidate.priority,
            reason="linkedin_search_copy_link",
        )
        _append_post_candidate(output_path, candidate)
        _append_capture_metric(
            metrics_path=metrics_path,
            event="post_captured",
            search_input=search_input,
            post_url=post_url,
        )
    return {"captured": captured, "duplicates": duplicates}


async def _capture_single_search(
    *,
    page: Any,
    search_input: SearchCaptureInput,
    output_path: Path,
    metrics_path: Path,
    seen_post_urls: set[str],
    limits: SearchCaptureLimits,
    reporter: ProgressReporter,
) -> dict[str, int]:
    await page.goto(
        search_input.capture_url,
        wait_until="domcontentloaded",
        timeout=limits.navigation_timeout_ms,
    )
    await page.wait_for_timeout(limits.settle_ms)
    captured = 0
    duplicates = 0
    stale_scrolls = 0
    processed_menu_buttons = 0
    for scroll_index in range(1, limits.max_scrolls + 1):
        if captured >= limits.max_results_per_search:
            break
        buttons = page.get_by_role("button", name=POST_MENU_PATTERN)
        try:
            button_count = await buttons.count()
        except TimeoutError:
            button_count = 0
        reporter.emit(
            "search_scroll",
            source_id=search_input.candidate.source_id,
            scroll=scroll_index,
            menu_buttons=button_count,
            new_menu_buttons=max(0, button_count - processed_menu_buttons),
            captured=captured,
        )
        captured_before_scroll = captured
        for index in range(processed_menu_buttons, button_count):
            if captured >= limits.max_results_per_search:
                break
            try:
                copied_url = await _copy_post_url_from_menu(
                    page=page,
                    menu_button=buttons.nth(index),
                )
            except (Exception, PostCopyCaptureError) as exc:
                _append_capture_metric(
                    metrics_path=metrics_path,
                    event="copy_failed",
                    search_input=search_input,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                continue
            post_url = canonicalize_linkedin_post_url(copied_url)
            if not post_url:
                _append_capture_metric(
                    metrics_path=metrics_path,
                    event="copied_url_rejected",
                    search_input=search_input,
                    copied_url=copied_url,
                    reason="not_linkedin_post_url",
                )
                continue
            if post_url in seen_post_urls:
                duplicates += 1
                _append_capture_metric(
                    metrics_path=metrics_path,
                    event="duplicate_post",
                    search_input=search_input,
                    post_url=post_url,
                )
                continue
            seen_post_urls.add(post_url)
            captured += 1
            candidate = PostCandidate(
                source_id=search_input.candidate.source_id,
                source_kind=search_input.candidate.source_kind,
                query_id=search_input.candidate.query_id,
                post_url=post_url,
                source_url=search_input.candidate.source_url,
                search_query=search_input.candidate.search_query,
                priority=search_input.candidate.priority,
                reason="linkedin_search_copy_link",
            )
            _append_post_candidate(output_path, candidate)
            _append_capture_metric(
                metrics_path=metrics_path,
                event="post_captured",
                search_input=search_input,
                post_url=post_url,
            )
        processed_menu_buttons = max(processed_menu_buttons, button_count)
        if captured == captured_before_scroll:
            stale_scrolls += 1
        else:
            stale_scrolls = 0
        if stale_scrolls >= 3:
            break
        await page.evaluate("(pixels) => window.scrollBy(0, pixels)", limits.scroll_pixels)
        await page.wait_for_timeout(limits.settle_ms)
    return {"captured": captured, "duplicates": duplicates}


async def _copy_post_url_from_menu(
    *,
    page: Any,
    menu_button: Any,
) -> str:
    await page.evaluate(COPY_CAPTURE_INSTALL_SCRIPT)
    try:
        try:
            await menu_button.click(timeout=2_000)
        except TimeoutError:
            await menu_button.dispatch_event("click")
        menu_item = page.get_by_role("menuitem", name=COPY_LINK_PATTERN)
        await menu_item.click()
        try:
            await page.wait_for_function(
                "() => window.__linkedinPostCopyCapture?.writes?.length > 0",
                timeout=COPY_CAPTURE_TIMEOUT_MS,
            )
        except TimeoutError as exc:
            raise PostCopyCaptureError(
                "LinkedIn copy action did not call navigator.clipboard.writeText"
            ) from exc
        copied_value = await page.evaluate(COPY_CAPTURE_READ_SCRIPT)
        if not isinstance(copied_value, str):
            raise PostCopyCaptureError("LinkedIn copy action produced a non-text post URL")
        copied_url = copied_value
        if not copied_url:
            raise PostCopyCaptureError("LinkedIn copy action produced an empty post URL")
        return copied_url
    finally:
        await page.evaluate(COPY_CAPTURE_RESTORE_SCRIPT)


async def _reusable_page(context: Any) -> Any:
    fragments = (
        "linkedin.com/search/results/content",
        "linkedin.com/feed/",
        "linkedin.com",
    )
    session = BrowserSession(
        context,
        PageReusePolicy(preferred_url_fragments=fragments, foreground=False),
    )
    return cast(Any, await session.page(preferred_url_fragments=fragments))


def _write_known_posts(
    *,
    output_path: Path,
    metrics_path: Path,
    known_posts: Sequence[PostCandidate],
    seen_post_urls: set[str],
) -> int:
    written = 0
    for candidate in prioritize_posts(tuple(known_posts)):
        post_url = canonicalize_linkedin_post_url(candidate.post_url)
        if not post_url or post_url in seen_post_urls:
            continue
        seen_post_urls.add(post_url)
        _append_post_candidate(output_path, candidate)
        _append_jsonl(
            metrics_path,
            {
                "event": "known_post_copied",
                "source_id": candidate.source_id,
                "query_id": candidate.query_id,
                "post_url": post_url,
            },
        )
        written += 1
    return written


def _append_capture_metric(
    *,
    metrics_path: Path,
    event: str,
    search_input: SearchCaptureInput,
    post_url: str = "",
    copied_url: str = "",
    reason: str = "",
    error_type: str = "",
    message: str = "",
) -> None:
    _append_jsonl(
        metrics_path,
        {
            "event": event,
            "source_id": search_input.candidate.source_id,
            "source_kind": search_input.candidate.source_kind,
            "query_id": search_input.candidate.query_id,
            "source_url": search_input.candidate.source_url,
            "search_query": search_input.candidate.search_query,
            "capture_url": search_input.capture_url,
            "post_url": post_url,
            "copied_url": copied_url,
            "reason": reason,
            "error_type": error_type,
            "message": message,
        },
    )


def _initialize_post_queue_output(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=POST_QUEUE_COLUMNS)
        writer.writeheader()


def _append_post_candidate(path: Path, candidate: PostCandidate) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=POST_QUEUE_COLUMNS)
        writer.writerow(candidate.__dict__)


def _initialize_jsonl(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


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


def _playwriter_search_capture_script() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts" / "search_capture.js"


def _stage_playwriter_config(config: Mapping[str, object]) -> tuple[Path, Path | None, Path | None]:
    staging_dir = Path(tempfile.gettempdir()) / "linkedin-tools-playwriter"
    staging_dir.mkdir(parents=True, exist_ok=True)
    final_out = Path(str(config["out"])) if config.get("out") else None
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", final_out.stem if final_out else "search-capture")
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


def _list_of_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
