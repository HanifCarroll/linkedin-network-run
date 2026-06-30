"""Controller operations for the network automation CLI."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packages.linkedin_browser import ChromeProfileConfig, start_managed_chrome_cdp_session

from .browser import BrowserClient
from .models import (
    AcceptanceCheckCandidate,
    AcceptanceOutcomeArtifact,
    AcceptanceStatus,
    AcceptedDraftCandidate,
    AcceptedResearchArtifact,
    BrowserSessionState,
    CandidateEvent,
    CandidateStatus,
    DraftStrategy,
    PendingCapture,
    PendingCleanupState,
    PendingWithdrawResult,
    RunState,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendResult,
    acceptance_followup_id,
    apply_acceptance_followup_send_result,
    apply_audit,
    apply_pending_audit,
    build_draft_report,
    candidate_key,
    drain_stale_connectable_candidates,
    fill_run_from_reservoir,
    import_capture,
    import_capture_into_reservoir,
    import_pending_capture,
    is_send_noop_status,
    is_uncertain_send_status,
    low_yield_source_names,
    new_pending_cleanup_run,
    new_run,
    now_utc,
    record_pending_withdraw_result,
    record_send_result,
    record_top_up_send_result,
    render_draft_markdown,
    source_repeated_send_noop,
    validate_acceptance_followup_can_send,
)
from .reports import (
    format_delta,
    render_acceptance_report,
    render_pending_report,
    render_report,
)
from .store import Store, read_model, write_json_atomic

DEFAULT_CONFIRM_SEND_OUT_DIR = Path("/tmp/linkedin-network-run-confirm-send")
ZERO_CAPTURE_EXHAUSTION_STREAK = 3


def start_run(
    store: Store,
    *,
    target: int = 30,
    run_date: object | None = None,
    force: bool = False,
    max_real_sends: int | None = None,
) -> str:
    if store.active_path.exists() and not force:
        raise RuntimeError("an active run already exists; use --force to replace it")
    from datetime import date

    parsed_date = run_date if isinstance(run_date, date) else None
    run = new_run(target, parsed_date, max_real_sends)
    store.save_run(run)
    store.append_event(run, "start", {"target": target})
    next_source = run.next_source()
    suffix = f"; next source: {next_source.name}" if next_source else ""
    return f"started run {run.id} for {run.date.isoformat()} with target {target}{suffix}"


def record_audit(store: Store, people_count: int, note: str | None = None) -> str:
    run = store.load_run()
    apply_audit(run, people_count, note)
    store.save_run(run)
    store.append_event(run, "audit", {"people_count": people_count, "delta": run.audited_delta()})
    return f"audit recorded: People ({people_count}){_delta_suffix(run.audited_delta())}"


def import_audit(store: Store, path: Path) -> str:
    run = store.load_run()
    audit = read_model(path, SalesNavAudit)
    note = "imported audit; recent_names=" + ", ".join(audit.recent_names)
    apply_audit(run, audit.people_count, note)
    store.save_run(run)
    store.append_event(run, "import-audit", {"path": str(path), "people_count": audit.people_count})
    return f"audit imported: People ({audit.people_count}){_delta_suffix(run.audited_delta())}"


def capture_saved_searches(browser: BrowserClient, *, url: str, out: Path) -> str:
    artifact, path = browser.resolve_saved_searches(url=url, out=out)
    return f"captured {len(artifact.searches)} saved searches to {path}"


def browser_session_start(
    store: Store,
    *,
    config: ChromeProfileConfig,
    start_url: str,
    force: bool,
) -> str:
    existing = _load_browser_session_state(store)
    if existing is not None and _browser_session_is_usable(existing):
        if not force:
            return (
                f"browser session already running: pid={existing.pid} "
                f"cdp_url={existing.cdp_url}"
            )
        browser_session_stop(store)
    launched = start_managed_chrome_cdp_session(config, start_url=start_url)
    state = BrowserSessionState(
        pid=launched.pid,
        port=launched.port,
        cdp_url=launched.cdp_url,
        user_data_dir=str(launched.user_data_dir),
        profile_name=launched.profile_name,
        start_url=start_url,
    )
    write_json_atomic(store.browser_session_path, state.model_dump(mode="json", by_alias=False))
    return f"browser session started: pid={state.pid} cdp_url={state.cdp_url}"


def browser_session_status(store: Store, *, as_json: bool = False) -> str:
    state = _load_browser_session_state(store)
    payload: dict[str, object]
    if state is None:
        payload = {"configured": False, "alive": False, "reachable": False}
    else:
        command = _process_command(state.pid)
        payload = {
            "configured": True,
            "alive": _browser_session_pid_matches(state, command),
            "reachable": _is_local_port_reachable(state.port),
            "pid": state.pid,
            "port": state.port,
            "cdp_url": state.cdp_url,
            "user_data_dir": state.user_data_dir,
            "profile_name": state.profile_name,
            "start_url": state.start_url,
            "started_at": state.started_at.isoformat(),
        }
    if as_json:
        return json.dumps(payload, indent=2)
    if not payload["configured"]:
        return "browser session: not started"
    state_text = "usable" if payload["alive"] and payload["reachable"] else "not usable"
    return (
        f"browser session: {state_text}; pid={payload['pid']} "
        f"cdp_url={payload['cdp_url']}"
    )


def browser_session_stop(store: Store) -> str:
    state = _load_browser_session_state(store)
    if state is None:
        return "browser session: not started"
    command = _process_command(state.pid)
    stopped = False
    if _browser_session_pid_matches(state, command):
        os.kill(state.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not _pid_alive(state.pid):
                stopped = True
                break
            time.sleep(0.25)
        if not stopped and _pid_alive(state.pid):
            os.kill(state.pid, signal.SIGKILL)
            stopped = True
    store.browser_session_path.unlink(missing_ok=True)
    if stopped:
        return f"browser session stopped: pid={state.pid}"
    return "browser session record removed; process was not running or did not match"


def browser_session_cdp_url(store: Store) -> str | None:
    state = _load_browser_session_state(store)
    if state is None:
        return None
    if _browser_session_is_usable(state):
        return state.cdp_url
    return None


def _load_browser_session_state(store: Store) -> BrowserSessionState | None:
    if not store.browser_session_path.exists():
        return None
    return read_model(store.browser_session_path, BrowserSessionState)


def _browser_session_is_usable(state: BrowserSessionState) -> bool:
    return _browser_session_pid_matches(state, _process_command(state.pid)) and (
        _is_local_port_reachable(state.port)
    )


def _browser_session_pid_matches(state: BrowserSessionState, command: str) -> bool:
    if not command:
        return False
    return (
        "Google Chrome" in command
        and f"--remote-debugging-port={state.port}" in command
        and state.user_data_dir in command
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_local_port_reachable(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def network_run_session(
    store: Store,
    browser: BrowserClient,
    *,
    target: int,
    max_real_sends: int | None,
    force: bool,
    saved_searches_url: str,
    saved_searches_out: Path,
    audit_attempts: int,
    audit_delay_ms: int,
    allow_send: bool,
    max_steps: int,
    finish: bool,
    confirm_delay_ms: int = 5000,
    confirm_out_dir: Path = DEFAULT_CONFIRM_SEND_OUT_DIR,
) -> str:
    messages = [
        start_run(store, target=target, force=force, max_real_sends=max_real_sends),
        reconcile_audit(store, browser, attempts=1, delay_ms=0, finish=False),
        capture_saved_searches(
            browser,
            url=saved_searches_url,
            out=saved_searches_out,
        ),
    ]
    zero_capture_streaks: dict[str, int] = {}
    for _ in range(max_steps):
        plan = store.load_run().operator_plan_with_reservoir(store.load_reservoir())
        messages.append(f"plan: {plan.action}")
        if plan.action == "use-reservoir":
            if not plan.source:
                raise RuntimeError("use-reservoir plan did not include source")
            messages.append(reservoir_fill_run(store, source=plan.source, limit=None))
            continue
        if plan.action == "capture-source":
            if plan.source is None or plan.capture is None:
                raise RuntimeError("capture-source plan did not include source/capture details")
            source_url = plan.resume_url or resolve_saved_search_url(
                saved_searches_out, plan.source
            )
            if source_url is None:
                raise RuntimeError(f"saved search URL missing for source {plan.source}")
            before_imported = len(store.load_run().observations)
            capture_message = capture_source(
                store,
                browser,
                source=plan.source,
                url=source_url,
                saved_searches=None,
                pages=plan.capture.pages,
                limit=18,
                stop_after_connectable=plan.capture.stop_after_connectable,
                only_connectable=True,
                row_scroll_delay_ms=250,
            )
            messages.append(capture_message)
            after_run = store.load_run()
            imported = len(after_run.observations) - before_imported
            if imported > 0:
                zero_capture_streaks[plan.source] = 0
            else:
                streak = zero_capture_streaks.get(plan.source, 0) + 1
                zero_capture_streaks[plan.source] = streak
                if streak >= ZERO_CAPTURE_EXHAUSTION_STREAK:
                    note = (
                        f"{streak} consecutive captures imported 0 usable candidates; "
                        "carrying remaining quota forward"
                    )
                    messages.append(source_exhausted(store, plan.source, note=note))
            continue
        if plan.action == "send-candidate":
            if not allow_send:
                messages.append("stopped: pass --allow-send for real network sends")
                break
            messages.append(
                send_guarded(
                    store,
                    browser,
                    dry_run=False,
                    allow_send=True,
                    max_attempts=30,
                    single_pass=True,
                    no_record=False,
                    confirm_delay_ms=confirm_delay_ms,
                    confirm_out_dir=confirm_out_dir,
                )
            )
            continue
        if plan.action in {"reaudit", "final-audit"}:
            messages.append(
                reconcile_audit(
                    store,
                    browser,
                    attempts=audit_attempts,
                    delay_ms=audit_delay_ms,
                    finish=False,
                )
            )
            if finish:
                run = store.load_run()
                if run.verified_count() >= run.target:
                    messages.append(finish_run(store))
                else:
                    raise RuntimeError(
                        f"durable confirmed sends are {run.verified_count()}/{run.target}; "
                        "continue normal guarded sends before finishing"
                    )
            break
        messages.append(f"stopped: {plan.reason or plan.action}")
        break
    else:
        messages.append(f"stopped: max steps {max_steps} reached")
    return "\n".join(messages)


def reconcile_audit(
    store: Store,
    browser: BrowserClient,
    *,
    attempts: int = 3,
    delay_ms: int = 5000,
    finish: bool = False,
) -> str:
    attempts = max(1, attempts)
    latest_delta: int | None = None
    messages: list[str] = []
    for attempt in range(1, attempts + 1):
        audit, path = browser.audit_sent_invitations(load_more=0)
        run = store.load_run()
        apply_audit(run, audit.people_count, f"reconcile audit attempt {attempt}/{attempts}")
        latest_delta = run.audited_delta()
        store.save_run(run)
        store.append_event(
            run,
            "reconcile-audit",
            {
                "attempt": attempt,
                "path": path,
                "people_count": audit.people_count,
                "delta": latest_delta,
                "finished": False,
            },
        )
        messages.append(
            f"reconcile audit {attempt}/{attempts}: People ({audit.people_count}), "
            f"delta {format_delta(latest_delta)}; out={path}"
        )
        if latest_delta == run.target:
            break
        if attempt < attempts and delay_ms > 0:
            time.sleep(delay_ms / 1000)
    if finish:
        messages.append(finish_run(store))
    return "\n".join(messages + [render_report(store.load_run())])


def record_candidate(
    store: Store,
    *,
    source: str,
    name: str,
    status: CandidateStatus,
    profile_url: str | None = None,
    note: str | None = None,
) -> str:
    run = store.load_run()
    if run.state == RunState.NEEDS_REAUDIT:
        raise RuntimeError("run is in NEEDS_REAUDIT; record a fresh sent-page audit first")
    if status in {CandidateStatus.PENDING, CandidateStatus.ACCEPTED}:
        for candidate in run.candidates:
            if (
                candidate.status in {CandidateStatus.PENDING, CandidateStatus.ACCEPTED}
                and candidate.name == name
                and candidate.profile_url == profile_url
            ):
                raise RuntimeError(f"candidate already recorded as delivered: {name}")
    event = CandidateEvent(
        source=source,
        name=name,
        profile_url=profile_url,
        status=status,
        note=note,
    )
    run.candidates.append(event)
    run.state = RunState.FINAL_RECONCILE if run.verified_count() >= run.target else RunState.SENDING
    drained = drain_stale_connectable_candidates(run)
    run.mark_updated()
    store.save_run(run)
    store.append_event(run, "record", event)
    if drained:
        store.append_event(run, "drain-stale-candidates", {"events": drained})
    return f"recorded {status.value}; verified {run.verified_count()}/{run.target}"


def record_send_result_from_path(store: Store, path: Path) -> str:
    run = store.load_run()
    if run.state == RunState.NEEDS_REAUDIT:
        raise RuntimeError("run is in NEEDS_REAUDIT; record a fresh sent-page audit first")
    result = read_model(path, SalesNavSendResult)
    event = record_send_result(run, result, str(path))
    drained = drain_stale_connectable_candidates(run)
    store.save_run(run)
    store.append_event(run, "record-send-result", {"path": str(path), "event": event})
    if drained:
        store.append_event(run, "drain-stale-candidates", {"events": drained})
    return (
        f"recorded send result as {event.status.value}; "
        f"verified {run.verified_count()}/{run.target}"
    )


def confirm_provisional_send(
    store: Store,
    browser: BrowserClient,
    event: CandidateEvent,
    *,
    delay_ms: int = 5000,
    out_dir: Path = DEFAULT_CONFIRM_SEND_OUT_DIR,
) -> str:
    if event.status != CandidateStatus.PENDING_PROVISIONAL:
        return f"confirmation skipped: {event.status.value}"
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)
    run = store.load_run()
    candidate = _find_matching_provisional_event(run.candidates, event)
    if candidate is None:
        raise RuntimeError(f"provisional send not found for confirmation: {event.name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{len(run.candidates):03d}-{_safe_artifact_stem(event.name)}"
    input_path = out_dir / f"{stem}-candidate.json"
    outcome_path = out_dir / f"{stem}-outcome.json"
    check_candidate = AcceptanceCheckCandidate(
        run_id=str(run.id),
        run_date=run.date,
        source=event.source,
        name=event.name,
        profile_url=event.profile_url,
        sent_at=event.at,
        latest_status=AcceptanceStatus.SENT,
        latest_checked_at=None,
    )
    write_json_atomic(input_path, [check_candidate.model_dump(mode="json")])
    artifact, path = browser.check_acceptance_outcomes(
        candidates=[check_candidate],
        input_path=input_path,
        out=outcome_path,
        offset=0,
        limit=1,
        delay_ms=0,
    )
    row = artifact.rows[0] if artifact.rows else None
    final_status, status_note, blocked = _candidate_status_from_confirmation(row)
    candidate.status = final_status
    candidate.note = "; ".join(
        part
        for part in (
            candidate.note,
            f"durable confirmation {status_note}",
            f"outcome={path}",
        )
        if part
    )
    if blocked:
        run.state = RunState.BLOCKED
        run.notes.append(f"durable confirmation blocked for {event.name}: {status_note}")
    elif run.state not in {RunState.DONE, RunState.BLOCKED}:
        run.state = (
            RunState.FINAL_RECONCILE if run.verified_count() >= run.target else RunState.SENDING
        )
    run.mark_updated()
    store.save_run(run)
    store.append_event(
        run,
        "confirm-send-result",
        {
            "input": str(input_path),
            "out": path,
            "event": candidate,
            "status": final_status.value,
            "confirmation": status_note,
        },
    )
    return (
        f"confirmation status: {final_status.value}; "
        f"verified {run.verified_count()}/{run.target}"
    )


def _find_matching_provisional_event(
    candidates: list[CandidateEvent], event: CandidateEvent
) -> CandidateEvent | None:
    for candidate in reversed(candidates):
        if (
            candidate.status == CandidateStatus.PENDING_PROVISIONAL
            and candidate.source == event.source
            and candidate.name == event.name
            and candidate.profile_url == event.profile_url
        ):
            return candidate
    return None


def _candidate_status_from_confirmation(row: object | None) -> tuple[CandidateStatus, str, bool]:
    if row is None:
        return CandidateStatus.FAILED, "missing confirmation row", False
    status = getattr(row, "status", None)
    note = getattr(row, "note", None) or ""
    if status == AcceptanceStatus.PENDING:
        return CandidateStatus.PENDING, "pending", False
    if status == AcceptanceStatus.ACCEPTED:
        return CandidateStatus.ACCEPTED, "accepted", False
    if status == AcceptanceStatus.CONNECTABLE:
        return CandidateStatus.REVERTED_CONNECT, "connectable again; invite not durable", False
    if status == AcceptanceStatus.BLOCKED:
        return CandidateStatus.FAILED, f"blocked: {note or 'blocked'}", True
    value = getattr(status, "value", str(status))
    return CandidateStatus.FAILED, f"{value}: {note}".strip(), False


def _safe_artifact_stem(value: str) -> str:
    stem = "".join(char.lower() if char.isalnum() else "-" for char in value)
    stem = "-".join(part for part in stem.split("-") if part)
    return stem[:80] or "candidate"


def drain_stale_candidates(store: Store, source: str | None = None) -> str:
    run = store.load_run()
    drained = drain_stale_connectable_candidates(run, source)
    store.save_run(run)
    store.append_event(
        run,
        "drain-stale-candidates",
        {"source": source, "events": drained},
    )
    return f"auto-skipped {len(drained)} stale queued candidates"


def send_next(
    store: Store,
    browser: BrowserClient,
    *,
    dry_run: bool,
    allow_send: bool,
    no_record: bool = False,
    confirm_delay_ms: int = 5000,
    confirm_out_dir: Path = DEFAULT_CONFIRM_SEND_OUT_DIR,
) -> str:
    run = store.load_run()
    if run.state == RunState.NEEDS_REAUDIT:
        raise RuntimeError("run is in NEEDS_REAUDIT; record a fresh sent-page audit before sending")
    if allow_send and run.real_send_capacity_remaining() == 0:
        raise RuntimeError(
            f"real-send cap reached: {run.real_send_attempt_count()}/{run.max_real_sends} "
            "real send attempts"
        )
    candidate = run.next_connectable_observation()
    if candidate is None:
        raise RuntimeError("no unrecorded connectable candidate available")
    result, path = browser.send_connection(
        candidate, dry_run=dry_run or not allow_send, allow_send=allow_send
    )
    if allow_send and not dry_run and not no_record:
        run = store.load_run()
        event = record_send_result(run, result, path)
        drained = drain_stale_connectable_candidates(run)
        store.save_run(run)
        store.append_event(run, "record-send-result", {"path": path, "event": event})
        if drained:
            store.append_event(run, "drain-stale-candidates", {"events": drained})
        messages = [f"send result: {path}; recorded {event.status.value}"]
        if event.status == CandidateStatus.PENDING_PROVISIONAL:
            messages.append(
                confirm_provisional_send(
                    store,
                    browser,
                    event,
                    delay_ms=confirm_delay_ms,
                    out_dir=confirm_out_dir,
                )
            )
        return "\n".join(messages)
    return f"send result: {path}; dry_run={dry_run or not allow_send}"


def send_guarded(
    store: Store,
    browser: BrowserClient,
    *,
    dry_run: bool,
    allow_send: bool,
    max_attempts: int = 30,
    single_pass: bool = False,
    no_record: bool = False,
    confirm_delay_ms: int = 5000,
    confirm_out_dir: Path = DEFAULT_CONFIRM_SEND_OUT_DIR,
) -> str:
    if not dry_run and not allow_send:
        raise RuntimeError("real guarded sends require --allow-send")
    run = store.load_run()
    if run.state == RunState.NEEDS_REAUDIT:
        raise RuntimeError("run is in NEEDS_REAUDIT; record a fresh sent-page audit before sending")
    next_source = run.next_source()
    if next_source is None:
        raise RuntimeError("no active source available for guarded send")
    source = next_source.name
    attempts = 0
    messages: list[str] = []
    while attempts < max_attempts:
        run = store.load_run()
        if run.state == RunState.NEEDS_REAUDIT:
            raise RuntimeError("run entered NEEDS_REAUDIT; import a fresh audit before continuing")
        drained = drain_stale_connectable_candidates(run)
        if drained:
            store.save_run(run)
            store.append_event(run, "drain-stale-candidates", {"events": drained})
        next_source = run.next_source()
        if next_source is None or next_source.name != source:
            break
        if run.real_send_capacity_remaining() == 0:
            raise RuntimeError(
                f"real-send cap reached: {run.real_send_attempt_count()}/{run.max_real_sends} "
                "real send attempts"
            )
        candidate = run.next_connectable_observation_for_source(source)
        if candidate is None:
            break
        attempts += 1
        if dry_run or not single_pass:
            dry_result, dry_path = browser.send_connection(
                candidate, dry_run=True, allow_send=False
            )
            messages.append(f"dry-run status: {dry_result.status}")
            if dry_result.status != "dry-run-connectable":
                if not no_record:
                    run = store.load_run()
                    event = record_send_result(run, dry_result, dry_path)
                    store.save_run(run)
                    store.append_event(
                        run, "record-send-result", {"path": dry_path, "event": event}
                    )
                continue
            if dry_run:
                break
        run = store.load_run()
        result, path = browser.send_connection(candidate, dry_run=False, allow_send=True)
        messages.append(f"send status: {result.status}")
        if no_record:
            break
        event = record_send_result(run, result, path)
        drain_stale_connectable_candidates(run)
        if result.status == "blocked":
            run.state = RunState.BLOCKED
            run.notes.append(f"guarded send blocked for {event.name}: {result.status}")
        elif is_uncertain_send_status(result.status):
            run.state = RunState.NEEDS_REAUDIT
            run.notes.append(
                f"guarded send stopped after uncertain status for {event.name}: {result.status}"
            )
            if is_send_noop_status(result.status) and source_repeated_send_noop(
                run, event.source, 3
            ):
                for source_plan in run.sources:
                    if source_plan.name == event.source:
                        source_plan.exhausted = True
                        break
                store.append_event(
                    run,
                    "source-exhausted",
                    {"source": event.source, "via": "send-guarded-clicked-send-noop"},
                )
        store.save_run(run)
        store.append_event(run, "record-send-result", {"path": path, "event": event})
        if event.status == CandidateStatus.PENDING_PROVISIONAL:
            messages.append(
                confirm_provisional_send(
                    store,
                    browser,
                    event,
                    delay_ms=confirm_delay_ms,
                    out_dir=confirm_out_dir,
                )
            )
            run = store.load_run()
        if is_uncertain_send_status(result.status):
            raise RuntimeError(
                f"guarded send stopped on uncertain status {result.status}; "
                "import a fresh sent-page audit before continuing"
            )
    return "\n".join(messages) if messages else "guarded send had no candidate to process"


def top_up_reconcile(
    store: Store,
    browser: BrowserClient,
    *,
    max_attempts: int = 20,
    delay_ms: int = 1000,
    allow_send: bool = False,
    finish: bool = False,
    fallback_source: str = "FO - Founders - Urgent",
    fallback_url: str | None = None,
    saved_searches: Path | None = None,
    fallback_pages: int = 5,
    fallback_stop_after_connectable: int = 10,
    fallback_limit: int = 18,
    fallback_row_scroll_delay_ms: int = 250,
    no_fallback_capture: bool = False,
) -> str:
    if not allow_send:
        raise RuntimeError("top-up reconciliation can send real invites; pass --allow-send")
    attempts = max(1, max_attempts)
    messages: list[str] = []
    for attempt in range(1, attempts + 1):
        run = store.load_run()
        if run.verified_count() >= run.target:
            messages.append("durable confirmed target already met; no top-up needed")
            if finish and run.state != RunState.DONE:
                messages.append(finish_run(store))
            break
        if run.real_send_capacity_remaining() == 0:
            raise RuntimeError(
                f"real-send cap reached: {run.real_send_attempt_count()}/{run.max_real_sends} "
                "real send attempts"
            )
        candidate = run.next_top_up_observation()
        if candidate is None and not no_fallback_capture:
            messages.append(
                capture_source(
                    store,
                    browser,
                    source=fallback_source,
                    url=fallback_url,
                    saved_searches=saved_searches,
                    pages=fallback_pages,
                    limit=fallback_limit,
                    stop_after_connectable=fallback_stop_after_connectable,
                    only_connectable=True,
                    row_scroll_delay_ms=fallback_row_scroll_delay_ms,
                )
            )
            candidate = store.load_run().next_top_up_observation()
        if candidate is None:
            raise RuntimeError("no distinct connectable candidate available for top-up")
        messages.append(
            f"top-up attempt {attempt}/{attempts}: {candidate.name} ({candidate.source})"
        )
        result, result_path = browser.send_connection(
            candidate,
            dry_run=False,
            allow_send=True,
        )
        run = store.load_run()
        event = record_send_result(run, result, result_path)
        store.save_run(run)
        store.append_event(
            run,
            "record-send-result",
            {"path": result_path, "event": event, "via": "top-up-reconcile"},
        )
        messages.append(f"top-up send status: {result.status}")
        if event.status == CandidateStatus.PENDING_PROVISIONAL:
            messages.append(
                confirm_provisional_send(
                    store,
                    browser,
                    event,
                    delay_ms=delay_ms,
                    out_dir=Path("/tmp/linkedin-network-run-top-up-confirm-send"),
                )
            )
            run = store.load_run()
        if run.verified_count() >= run.target:
            if finish:
                messages.append(finish_run(store))
            break
        messages.append("top-up has not reached durable target yet; trying next candidate")
    run = store.load_run()
    if finish and run.state != RunState.DONE:
        raise RuntimeError(
            f"durable confirmed sends are {run.verified_count()}/{run.target}; "
            "top-up did not finish within the requested attempt limit"
        )
    return "\n".join(messages + [render_report(run)])


def import_capture_path(store: Store, path: Path, only_connectable: bool = False) -> str:
    run = store.load_run()
    capture = read_model(path, SalesNavCapture)
    imported = import_capture(run, capture, only_connectable)
    drained = drain_stale_connectable_candidates(run)
    store.save_run(run)
    store.append_event(
        run,
        "import-capture",
        {"path": str(path), "imported": imported, "only_connectable": only_connectable},
    )
    if drained:
        store.append_event(run, "drain-stale-candidates", {"events": drained})
    return f"imported {imported} candidate observations"


def capture_source(
    store: Store,
    browser: BrowserClient,
    *,
    source: str | None,
    url: str | None,
    saved_searches: Path | None,
    pages: int,
    limit: int,
    stop_after_connectable: int,
    only_connectable: bool,
    row_scroll_delay_ms: int,
) -> str:
    run = store.load_run()
    next_source = run.next_source()
    capture_source_name = source or (next_source.name if next_source else None)
    if capture_source_name is None:
        raise RuntimeError("no source provided and no active run source available")
    cursor = run.capture_cursors.get(capture_source_name)
    resolved_url = resolve_capture_url(
        explicit_url=url,
        saved_searches=saved_searches,
        source=capture_source_name,
        cursor_url=cursor.resume_url if cursor else None,
    )
    capture, path = browser.capture_salesnav(
        source=capture_source_name,
        url=resolved_url,
        pages=pages,
        limit=limit,
        stop_after_connectable=stop_after_connectable,
        only_connectable=only_connectable,
        row_scroll_delay_ms=row_scroll_delay_ms,
    )
    run = store.load_run()
    imported = import_capture(run, capture, only_connectable)
    drained = drain_stale_connectable_candidates(run)
    store.save_run(run)
    store.append_event(
        run,
        "capture",
        {
            "path": path,
            "source": capture_source_name,
            "imported": imported,
            "only_connectable": only_connectable,
        },
    )
    if drained:
        store.append_event(run, "drain-stale-candidates", {"events": drained})
    return f"captured {imported} candidate observations from {capture_source_name}; out={path}"


def source_exhausted(store: Store, source: str, note: str | None = None) -> str:
    run = store.load_run()
    for source_plan in run.sources:
        if source_plan.name == source:
            source_plan.exhausted = True
            if note:
                run.notes.append(f"source exhausted: {source}: {note}")
            run.mark_updated()
            store.save_run(run)
            store.append_event(run, "source-exhausted", {"source": source})
            return "marked source exhausted"
    raise RuntimeError(f"unknown source: {source}")


def needs_reaudit(store: Store, reason: str) -> str:
    run = store.load_run()
    run.state = RunState.NEEDS_REAUDIT
    run.notes.append("needs re-audit: " + reason)
    run.mark_updated()
    store.save_run(run)
    store.append_event(run, "needs-reaudit", {"reason": reason})
    return "run paused in NEEDS_REAUDIT; record a fresh People (N) audit before sending"


def resume_blocked(store: Store, reason: str) -> str:
    run = store.load_run()
    if run.state != RunState.BLOCKED:
        raise RuntimeError(f"run is not blocked; current state is {run.state.value}")
    run.blocked_resume_at = now_utc()
    run.state = RunState.NEEDS_REAUDIT
    run.notes.append("blocked run resume requested: " + reason)
    run.mark_updated()
    store.save_run(run)
    store.append_event(run, "resume-blocked", {"reason": reason})
    return "blocked run resumed; import a fresh sent-page audit before sending"


def finish_run(store: Store, *, force: bool = False) -> str:
    run = store.load_run()
    delta = run.audited_delta()
    if not force and run.verified_count() < run.target:
        raise RuntimeError(
            f"durable confirmed sends are {run.verified_count()}/{run.target}; "
            "continue normal guarded sends before finishing"
        )
    run.state = RunState.DONE
    run.mark_updated()
    store.save_run(run)
    ledger = store.load_acceptance_ledger()
    seeded = ledger.upsert_from_run(run)
    store.save_acceptance_ledger(ledger)
    store.append_event(
        run,
        "finish",
        {
            "audited_delta": delta,
            "durable_confirmed": run.verified_count(),
            "acceptance_seeded": seeded,
        },
    )
    store.append_acceptance_event("seed-from-finish", {"run_id": str(run.id), "seeded": seeded})
    return render_report(run) + f"\nacceptance ledger seeded: {seeded} new invitations"


def tune_sources(
    store: Store, *, min_raw_rows: int, max_connectable_yield: float, apply: bool
) -> str:
    run = store.load_run()
    low_yield = low_yield_source_names(run, min_raw_rows, max_connectable_yield)
    if apply:
        for source_plan in run.sources:
            if source_plan.name in low_yield:
                source_plan.exhausted = True
        for source in low_yield:
            run.notes.append(
                f"source tuned low-yield: {source}; threshold raw>={min_raw_rows}, "
                f"connectable_yield<={max_connectable_yield:.3f}"
            )
        run.mark_updated()
        store.save_run(run)
        store.append_event(
            run,
            "tune-sources",
            {
                "min_raw_rows": min_raw_rows,
                "max_connectable_yield": max_connectable_yield,
                "exhausted": low_yield,
            },
        )
    return "low-yield sources: " + (", ".join(low_yield) if low_yield else "none")


def reservoir_import_capture(store: Store, path: Path, only_connectable: bool = False) -> str:
    capture = read_model(path, SalesNavCapture)
    reservoir = store.load_reservoir()
    imported = import_capture_into_reservoir(reservoir, capture, only_connectable)
    store.save_reservoir(reservoir)
    return (
        f"reservoir imported {imported} candidate observations; total {len(reservoir.observations)}"
    )


def reservoir_capture(
    store: Store,
    browser: BrowserClient,
    *,
    source: str,
    url: str | None,
    saved_searches: Path | None,
    pages: int,
    limit: int,
    stop_after_connectable: int,
    only_connectable: bool,
    row_scroll_delay_ms: int,
) -> str:
    resolved_url = resolve_capture_url(
        explicit_url=url,
        saved_searches=saved_searches,
        source=source,
        cursor_url=None,
    )
    capture, path = browser.capture_salesnav(
        source=source,
        url=resolved_url,
        pages=pages,
        limit=limit,
        stop_after_connectable=stop_after_connectable,
        only_connectable=only_connectable,
        row_scroll_delay_ms=row_scroll_delay_ms,
    )
    reservoir = store.load_reservoir()
    imported = import_capture_into_reservoir(reservoir, capture, only_connectable)
    store.save_reservoir(reservoir)
    return (
        f"reservoir captured {imported} candidate observations from {source}; "
        f"total {len(reservoir.observations)}; out={path}"
    )


def reservoir_fill_run(store: Store, *, source: str | None = None, limit: int | None = None) -> str:
    run = store.load_run()
    reservoir = store.load_reservoir()
    next_source = run.next_source()
    fill_source = source or (next_source.name if next_source else None)
    if fill_source is None:
        raise RuntimeError("no source provided and no active run source available")
    quota = run.source_quota(fill_source) or 0
    fill_limit = (
        limit
        if limit is not None
        else quota - min(quota, run.source_verified_count(fill_source)) + 3
    )
    imported = fill_run_from_reservoir(run, reservoir, fill_source, fill_limit)
    store.save_run(run)
    store.save_reservoir(reservoir)
    store.append_event(run, "reservoir-fill-run", {"source": fill_source, "imported": imported})
    return f"filled active run with {imported} reservoir candidates"


def reservoir_clear(store: Store, source: str | None = None) -> str:
    reservoir = store.load_reservoir()
    before = len(reservoir.observations)
    if source:
        reservoir.observations = [
            observation for observation in reservoir.observations if observation.source != source
        ]
    else:
        reservoir.observations = []
    reservoir.updated_at = now_utc()
    store.save_reservoir(reservoir)
    return f"removed {before - len(reservoir.observations)} reservoir candidates"


def resolve_capture_url(
    *,
    explicit_url: str | None,
    saved_searches: Path | None,
    source: str,
    cursor_url: str | None,
) -> str | None:
    if explicit_url:
        return explicit_url
    if cursor_url:
        return cursor_url
    if saved_searches is None:
        return None
    resolved = resolve_saved_search_url(saved_searches, source)
    if resolved:
        return resolved
    raise RuntimeError(
        f"no URL for source {source}; pass --url or provide a saved-searches artifact"
    )


def resolve_saved_search_url(path: Path, source: str) -> str | None:
    if not path.exists():
        return None
    data: Any = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"saved searches artifact must be a JSON object: {path}")
    searches = data.get("searches", data.get("savedSearches"))
    if not isinstance(searches, list):
        raise ValueError(f"saved searches artifact has no searches array: {path}")
    for item in searches:
        if not isinstance(item, dict) or item.get("name") != source:
            continue
        view_url = item.get("viewUrl", item.get("view_url"))
        if isinstance(view_url, str) and view_url.strip():
            return view_url
    return None


def acceptance_seed(store: Store, *, include_unfinished: bool = False) -> str:
    run = store.load_run()
    if not include_unfinished and run.state != RunState.DONE:
        raise RuntimeError(
            "active run is not Done; pass --include-unfinished to seed provisional sends"
        )
    ledger = store.load_acceptance_ledger()
    seeded = ledger.upsert_from_run(run)
    store.save_acceptance_ledger(ledger)
    store.append_acceptance_event(
        "seed", {"run_id": str(run.id), "seeded": seeded, "include_unfinished": include_unfinished}
    )
    return f"acceptance ledger seeded: {seeded} new invitations"


def acceptance_seed_history(store: Store) -> str:
    ledger = store.load_acceptance_ledger()
    summary = store.seed_acceptance_from_history(ledger)
    store.save_acceptance_ledger(ledger)
    store.append_acceptance_event("seed-history", summary)
    return (
        f"acceptance ledger history seeded: {summary.seeded} new invitations from "
        f"{summary.run_logs} run logs ({summary.sent_events} sent events scanned)"
    )


def acceptance_export(
    store: Store, *, min_age_days: int, max_age_days: int | None, out: Path
) -> str:
    ledger = store.load_acceptance_ledger()
    candidates = [
        AcceptanceCheckCandidate(
            run_id=str(invitation.run_id),
            run_date=invitation.run_date,
            source=invitation.source,
            name=invitation.name,
            profile_url=invitation.profile_url,
            sent_at=invitation.sent_at,
            latest_status=invitation.latest_status,
            latest_checked_at=invitation.latest_checked_at,
        )
        for invitation in ledger.eligible_for_check(min_age_days, max_age_days)
    ]
    write_json_atomic(out, [candidate.model_dump(mode="json") for candidate in candidates])
    store.append_acceptance_event(
        "export",
        {
            "path": str(out),
            "min_age_days": min_age_days,
            "max_age_days": max_age_days,
            "count": len(candidates),
        },
    )
    return f"exported {len(candidates)} acceptance-check candidates to {out}"


def acceptance_import(store: Store, path: Path) -> str:
    artifact = read_model(path, AcceptanceOutcomeArtifact)
    ledger = store.load_acceptance_ledger()
    summary = ledger.import_outcomes(artifact)
    store.save_acceptance_ledger(ledger)
    store.append_acceptance_event("import", {"path": str(path), "summary": summary})
    return (
        f"imported acceptance outcomes: {summary.rows} rows, "
        f"{summary.matched} matched, {summary.unmatched} unmatched"
    )


def acceptance_check(
    store: Store,
    browser: BrowserClient,
    *,
    input_path: Path,
    out: Path,
    offset: int,
    limit: int,
    delay_ms: int,
) -> str:
    candidates = load_acceptance_check_candidates(input_path)
    artifact, path = browser.check_acceptance_outcomes(
        candidates=candidates,
        input_path=input_path,
        out=out,
        offset=offset,
        limit=limit,
        delay_ms=delay_ms,
    )
    store.append_acceptance_event(
        "check",
        {
            "input": str(input_path),
            "out": path,
            "count": len(artifact.rows),
            "offset": offset,
            "limit": limit,
            "complete": artifact.complete,
        },
    )
    statuses: dict[str, int] = {}
    for row in artifact.rows:
        statuses[row.status.value] = statuses.get(row.status.value, 0) + 1
    return (
        f"acceptance outcomes: {len(artifact.rows)} rows written to {path}; "
        f"statuses={json.dumps(statuses, sort_keys=True)}"
    )


def acceptance_report(
    store: Store, *, min_age_days: int, max_age_days: int | None, as_json: bool = False
) -> str:
    ledger = store.load_acceptance_ledger()
    report = ledger.report(min_age_days, max_age_days)
    if as_json:
        import json

        return json.dumps(report.model_dump(mode="json"), indent=2)
    return render_acceptance_report(report)


def acceptance_run_daily_session(
    store: Store,
    browser_factory: Callable[[], BrowserClient],
    *,
    min_age_days: int,
    max_age_days: int | None,
    candidates_out: Path,
    outcomes_out: Path,
    chunk_dir: Path,
    chunk_size: int,
    check_delay_ms: int,
    draft_followups: bool,
    followup_out: Path | None,
    followup_research_out_dir: Path | None,
    include_drafted: bool,
    strategy: DraftStrategy,
    public_web: bool,
    max_web_results: int,
    research_delay_ms: int,
) -> str:
    messages = [
        acceptance_seed_history(store),
        acceptance_export(
            store,
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            out=candidates_out,
        ),
    ]
    candidates = load_acceptance_check_candidates(candidates_out)
    if not candidates:
        messages.append("no acceptance-check candidates; browser not opened")
        messages.append(
            acceptance_report(
                store,
                min_age_days=min_age_days,
                max_age_days=max_age_days,
                as_json=False,
            )
        )
        return "\n".join(messages)

    browser = browser_factory()
    try:
        check_messages = _acceptance_check_and_import_chunks(
            store,
            browser,
            candidates=candidates,
            candidates_out=candidates_out,
            outcomes_out=outcomes_out,
            chunk_dir=chunk_dir,
            chunk_size=chunk_size,
            delay_ms=check_delay_ms,
        )
        messages.extend(check_messages)
        if any(message.startswith("stopped:") for message in check_messages):
            messages.append(
                acceptance_report(
                    store,
                    min_age_days=min_age_days,
                    max_age_days=max_age_days,
                    as_json=False,
                )
            )
            return "\n".join(messages)
        if draft_followups:
            messages.append(
                acceptance_draft_followups(
                    store,
                    research=None,
                    out=followup_out,
                    include_drafted=include_drafted,
                    strategy=strategy,
                    browser=browser,
                    research_out_dir=followup_research_out_dir,
                    public_web=public_web,
                    max_web_results=max_web_results,
                    delay_ms=research_delay_ms,
                )
            )
    finally:
        close = getattr(browser, "close", None)
        if callable(close):
            close()
    messages.append(
        acceptance_report(
            store,
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            as_json=False,
        )
    )
    return "\n".join(messages)


def _acceptance_check_and_import_chunks(
    store: Store,
    browser: BrowserClient,
    *,
    candidates: list[AcceptanceCheckCandidate],
    candidates_out: Path,
    outcomes_out: Path,
    chunk_dir: Path,
    chunk_size: int,
    delay_ms: int,
) -> list[str]:
    chunk_size = max(1, chunk_size)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    messages: list[str] = []
    chunk_paths: list[Path] = []
    blockers: list[str] = []
    for offset in range(0, len(candidates), chunk_size):
        limit = min(chunk_size, len(candidates) - offset)
        chunk_path = chunk_dir / f"chunk-{offset}.json"
        if chunk_path.exists():
            existing = read_model(chunk_path, AcceptanceOutcomeArtifact)
            existing_blocked_rows = [
                row
                for row in existing.rows
                if str(getattr(row, "status", "")).lower() == "blocked"
            ]
            if (
                existing.complete is True
                and existing.input == str(candidates_out)
                and existing.offset == offset
                and existing.limit == limit
                and existing.total_candidates == len(candidates)
                and len(existing.rows) == limit
                and not existing_blocked_rows
            ):
                store.append_acceptance_event(
                    "run-daily-session-check-reuse",
                    {
                        "input": str(candidates_out),
                        "out": str(chunk_path),
                        "offset": offset,
                        "limit": limit,
                        "candidates": len(candidates),
                    },
                )
                messages.append(f"reused complete acceptance chunk: {chunk_path}")
                chunk_paths.append(chunk_path)
                continue
        store.append_acceptance_event(
            "run-daily-session-check-start",
            {
                "input": str(candidates_out),
                "out": str(chunk_path),
                "offset": offset,
                "limit": limit,
                "candidates": len(candidates),
            },
        )
        messages.append(
            acceptance_check(
                store,
                browser,
                input_path=candidates_out,
                out=chunk_path,
                offset=offset,
                limit=limit,
                delay_ms=delay_ms,
            )
        )
        artifact = read_model(chunk_path, AcceptanceOutcomeArtifact)
        chunk_paths.append(chunk_path)
        if artifact.complete is not True:
            blockers.append(f"{chunk_path} is incomplete")
        if len(artifact.rows) != limit:
            blockers.append(f"{chunk_path} has {len(artifact.rows)}/{limit} rows")
        blocked_rows = [
            row
            for row in artifact.rows
            if str(getattr(row, "status", "")).lower() == "blocked"
        ]
        if blocked_rows:
            blockers.append(f"{chunk_path} has {len(blocked_rows)} blocked rows")
    if blockers:
        store.append_acceptance_event(
            "run-daily-session-blocked",
            {"reason": "incomplete chunks", "blockers": blockers},
        )
        messages.append("stopped: " + "; ".join(blockers))
        return messages

    rows = [
        row
        for chunk_path in chunk_paths
        for row in read_model(chunk_path, AcceptanceOutcomeArtifact).rows
    ]
    if len(rows) != len(candidates):
        store.append_acceptance_event(
            "run-daily-session-blocked",
            {
                "reason": "merged row count mismatch",
                "rows": len(rows),
                "candidates": len(candidates),
            },
        )
        messages.append(
            f"stopped: merged acceptance row count {len(rows)} "
            f"does not equal candidate count {len(candidates)}"
        )
        return messages

    merged = AcceptanceOutcomeArtifact(
        captured_at=now_utc().isoformat(),
        input=str(candidates_out),
        count=len(rows),
        offset=0,
        limit=0,
        total_candidates=len(candidates),
        complete=True,
        rows=rows,
    )
    write_json_atomic(outcomes_out, merged.model_dump(mode="json", by_alias=False))
    store.append_acceptance_event(
        "run-daily-session-merge",
        {
            "candidates": len(candidates),
            "rows": len(rows),
            "chunks": [str(path) for path in chunk_paths],
            "out": str(outcomes_out),
        },
    )
    messages.append(f"merged acceptance outcomes: {len(rows)} rows to {outcomes_out}")
    messages.append(acceptance_import(store, outcomes_out))
    return messages


def acceptance_draft_followups(
    store: Store,
    *,
    research: Path | None,
    out: Path | None,
    include_drafted: bool,
    strategy: DraftStrategy,
    browser: BrowserClient | None = None,
    research_out_dir: Path | None = None,
    public_web: bool = True,
    max_web_results: int = 5,
    delay_ms: int = 500,
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    candidates = ledger.accepted_for_followup(followups, include_drafted)
    report_path = out or store.default_acceptance_followup_report_path()
    generated_research: Path | None = None
    if candidates and research is None:
        if browser is None:
            raise RuntimeError("--session is required when --research is not provided")
        generated_dir = research_out_dir or (store.dir / "acceptance-followups" / "research")
        generated_dir.mkdir(parents=True, exist_ok=True)
        candidates_path = generated_dir / "accepted-candidates.json"
        generated_research = generated_dir / "accepted-research.json"
        write_json_atomic(
            candidates_path,
            [candidate.model_dump(mode="json", by_alias=False) for candidate in candidates],
        )
        browser.research_accepted_candidates(
            candidates=candidates,
            input_path=candidates_path,
            out=generated_research,
            offset=0,
            limit=0,
            public_web=public_web,
            max_web_results=max_web_results,
            delay_ms=delay_ms,
        )
        research = generated_research
    artifact = read_model(research, AcceptedResearchArtifact) if research else None
    report = build_draft_report(
        candidates, artifact, strategy, str(research) if research is not None else None
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_draft_markdown(report))
    recorded = followups.record_report(
        report, str(report_path), str(research) if research else None
    )
    store.save_acceptance_followup_ledger(followups)
    store.append_acceptance_event(
        "draft-followups",
        {
            "report_path": str(report_path),
            "research_path": str(research) if research else None,
            "draft_count": len(report.items),
            "recorded": recorded,
            "strategy": strategy.value,
            "include_drafted": include_drafted,
            "public_web": public_web,
            "max_web_results": max_web_results,
            "generated_research": str(generated_research) if generated_research else None,
        },
    )
    suffix = f"; research artifact: {research}" if research else ""
    if not report.items:
        return (
            f"accepted follow-up drafts: 0 written to {report_path}; "
            "no newly accepted connections need first-message drafts"
        )
    return f"accepted follow-up drafts: {len(report.items)} written to {report_path}{suffix}"


def acceptance_research(
    store: Store,
    browser: BrowserClient,
    *,
    input_path: Path,
    out: Path,
    offset: int,
    limit: int,
    public_web: bool,
    max_web_results: int,
    delay_ms: int,
) -> str:
    candidates = load_accepted_draft_candidates(input_path)
    artifact, path = browser.research_accepted_candidates(
        candidates=candidates,
        input_path=input_path,
        out=out,
        offset=offset,
        limit=limit,
        public_web=public_web,
        max_web_results=max_web_results,
        delay_ms=delay_ms,
    )
    store.append_acceptance_event(
        "research",
        {
            "input": str(input_path),
            "out": path,
            "count": len(artifact.rows),
            "offset": offset,
            "limit": limit,
            "public_web": public_web,
            "max_web_results": max_web_results,
        },
    )
    return f"accepted research: {len(artifact.rows)} rows written to {path}"


def acceptance_export_followup_candidates(
    store: Store, *, out: Path, include_drafted: bool
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    candidates = ledger.accepted_for_followup(followups, include_drafted)
    write_json_atomic(
        out, [candidate.model_dump(mode="json", by_alias=False) for candidate in candidates]
    )
    store.append_acceptance_event(
        "export-followup-candidates",
        {"out": str(out), "count": len(candidates), "include_drafted": include_drafted},
    )
    return f"exported {len(candidates)} accepted follow-up candidates to {out}"


def acceptance_send_followup(
    store: Store,
    browser: BrowserClient,
    *,
    record_id: str,
    dry_run: bool,
    preview_fill: bool,
    allow_send: bool,
) -> str:
    ledger = store.load_acceptance_followup_ledger()
    index = ledger.find_by_id(record_id)
    if index is None:
        raise RuntimeError(f"unknown acceptance follow-up id {record_id!r}")
    effective_dry_run = dry_run or preview_fill or not allow_send
    validate_acceptance_followup_can_send(ledger.drafts[index], effective_dry_run, allow_send)
    result, out_path = browser.send_acceptance_followup(
        ledger.drafts[index],
        dry_run=effective_dry_run,
        preview_fill=preview_fill,
        allow_send=allow_send,
    )
    apply_acceptance_followup_send_result(ledger.drafts[index], result, out_path)
    store.save_acceptance_followup_ledger(ledger)
    store.append_acceptance_event(
        "send-followup",
        {
            "id": record_id,
            "name": ledger.drafts[index].name,
            "status": result.status,
            "dry_run": effective_dry_run,
            "preview_fill": preview_fill,
            "out": out_path,
        },
    )
    return (
        f"accepted_followup={record_id} status={result.status} "
        f"dry_run={effective_dry_run} out={out_path}"
    )


def acceptance_send_ready_followups(
    store: Store, browser: BrowserClient, *, limit: int, allow_send: bool
) -> str:
    if not allow_send:
        raise RuntimeError("send-ready-followups requires --allow-send")
    ledger = store.load_acceptance_followup_ledger()
    ready = ledger.ready(limit)
    if not ready:
        return "no accepted follow-ups are ready to send"
    messages = [
        acceptance_send_followup(
            store,
            browser,
            record_id=record.id,
            dry_run=False,
            preview_fill=False,
            allow_send=True,
        )
        for record in ready
    ]
    return "\n".join(messages)


def acceptance_dry_run_followups(store: Store, browser: BrowserClient, *, limit: int) -> str:
    ledger = store.load_acceptance_followup_ledger()
    pending = ledger.needs_dry_run(limit)
    if not pending:
        return "no accepted follow-ups need a dry-run check"
    messages = [
        acceptance_send_followup(
            store,
            browser,
            record_id=record.id,
            dry_run=True,
            preview_fill=False,
            allow_send=False,
        )
        for record in pending
    ]
    return "\n".join(messages)


def pending_cleanup_start(
    store: Store,
    *,
    max_withdrawals: int = 75,
    threshold_days: int = 14,
    threshold_months: int = 0,
    force: bool = False,
) -> str:
    if store.pending_active_path.exists() and not force:
        raise RuntimeError(
            "an active pending-cleanup run already exists; use --force to replace it"
        )
    run = new_pending_cleanup_run(
        max_withdrawals=max_withdrawals,
        threshold_days=threshold_days,
        threshold_months=threshold_months,
    )
    store.save_pending(run)
    store.append_pending_event(
        run,
        "start",
        {
            "max_withdrawals": max_withdrawals,
            "threshold_months": run.threshold_months,
            "threshold_days": run.threshold_days,
        },
    )
    return (
        f"started pending cleanup {run.id} for {run.date.isoformat()}; "
        f"cap {max_withdrawals}, threshold {run.threshold_days} days"
    )


def pending_cleanup_import_audit(store: Store, path: Path) -> str:
    run = store.load_pending()
    audit = read_model(path, SalesNavAudit)
    note = "imported audit; recent_names=" + ", ".join(audit.recent_names)
    apply_pending_audit(run, audit.people_count, note)
    store.save_pending(run)
    store.append_pending_event(
        run, "import-audit", {"path": str(path), "people_count": audit.people_count}
    )
    return (
        f"pending audit imported: People ({audit.people_count}){_delta_suffix(run.audited_delta())}"
    )


def pending_cleanup_audit(
    store: Store,
    browser: BrowserClient,
    *,
    load_more: int,
) -> str:
    audit, path = browser.audit_sent_invitations(load_more=load_more)
    run = store.load_pending()
    note = "browser audit; recent_names=" + ", ".join(audit.recent_names)
    apply_pending_audit(run, audit.people_count, note)
    store.save_pending(run)
    store.append_pending_event(
        run, "audit", {"path": path, "people_count": audit.people_count}
    )
    return (
        f"pending audit: People ({audit.people_count}) from {path}"
        f"{_delta_suffix(run.audited_delta())}"
    )


def pending_cleanup_import_capture(store: Store, path: Path) -> str:
    run = store.load_pending()
    capture = read_model(path, PendingCapture)
    imported = import_pending_capture(run, capture)
    run.state = PendingCleanupState.WITHDRAWING
    run.mark_updated()
    store.save_pending(run)
    store.append_pending_event(run, "import-capture", {"path": str(path), "imported": imported})
    return f"imported {imported} pending invitation observations"


def pending_cleanup_capture(
    store: Store,
    browser: BrowserClient,
    *,
    load_more: int,
    threshold_days: int,
    out: Path,
) -> str:
    artifact, path = browser.capture_pending_invitations(
        load_more=load_more,
        threshold_days=threshold_days,
        out=out,
    )
    run = store.load_pending()
    imported = import_pending_capture(run, artifact)
    run.state = PendingCleanupState.WITHDRAWING
    run.mark_updated()
    store.save_pending(run)
    store.append_pending_event(run, "capture", {"path": path, "imported": imported})
    return (
        f"pending capture: {len(artifact.rows)} rows written to {path}; "
        f"imported {imported} observations"
    )


def pending_cleanup_record_withdraw_result(store: Store, path: Path) -> str:
    run = store.load_pending()
    result = read_model(path, PendingWithdrawResult)
    event = record_pending_withdraw_result(run, result, str(path))
    store.save_pending(run)
    store.append_pending_event(run, "record-withdraw-result", {"path": str(path), "event": event})
    return (
        f"recorded withdraw result as {event.status.value}; "
        f"withdrawn {run.withdrawn_count()}/{run.max_withdrawals}"
    )


def pending_cleanup_withdraw_next(
    store: Store,
    browser: BrowserClient,
    *,
    dry_run: bool,
    allow_withdraw: bool,
    no_record: bool = False,
) -> str:
    run = store.load_pending()
    if allow_withdraw and run.withdraw_capacity_remaining() == 0:
        raise RuntimeError(
            f"withdrawal cap reached: {run.withdrawn_count()}/{run.max_withdrawals} withdrawals"
        )
    candidate = run.next_eligible_observation()
    if candidate is None:
        raise RuntimeError("no unrecorded eligible stale invitation available")
    result, path = browser.withdraw_pending(
        candidate, dry_run=dry_run or not allow_withdraw, allow_withdraw=allow_withdraw
    )
    if allow_withdraw and not dry_run and not no_record:
        run = store.load_pending()
        event = record_pending_withdraw_result(run, result, path)
        store.save_pending(run)
        store.append_pending_event(run, "record-withdraw-result", {"path": path, "event": event})
        return f"withdraw result: {path}; recorded {event.status.value}"
    return f"withdraw result: {path}; dry_run={dry_run or not allow_withdraw}"


def pending_cleanup_run_session(
    store: Store,
    browser: BrowserClient,
    *,
    audit_load_more: int,
    capture_load_more: int,
    threshold_days: int,
    capture_out: Path,
    withdraw_limit: int,
    allow_withdraw: bool,
    dry_run_first: bool = True,
    finish: bool = False,
) -> str:
    messages: list[str] = [
        pending_cleanup_audit(store, browser, load_more=audit_load_more)
    ]
    captured = False
    starting_withdrawn_count = store.load_pending().withdrawn_count()
    real_withdraw_attempts = 0
    while True:
        run = store.load_pending()
        plan = run.operator_plan()
        messages.append(f"plan: {plan.action}")
        if plan.action == "capture-more":
            if captured:
                if store.load_pending().withdrawn_count() > starting_withdrawn_count:
                    messages.append(
                        pending_cleanup_audit(store, browser, load_more=audit_load_more)
                    )
                    if finish:
                        messages.append(pending_cleanup_finish(store))
                messages.append("stopped: capture imported no eligible stale invitation")
                break
            messages.append(
                pending_cleanup_capture(
                    store,
                    browser,
                    load_more=capture_load_more,
                    threshold_days=threshold_days,
                    out=capture_out,
                )
            )
            captured = True
            continue
        if plan.action == "withdraw-candidate":
            if real_withdraw_attempts >= withdraw_limit:
                if store.load_pending().withdrawn_count() > starting_withdrawn_count:
                    messages.append(
                        pending_cleanup_audit(store, browser, load_more=audit_load_more)
                    )
                    if finish:
                        messages.append(pending_cleanup_finish(store))
                messages.append(f"stopped: withdraw limit {withdraw_limit} reached")
                break
            if dry_run_first:
                messages.append(
                    pending_cleanup_withdraw_next(
                        store,
                        browser,
                        dry_run=True,
                        allow_withdraw=False,
                    )
                )
            if not allow_withdraw:
                messages.append("stopped: pass --allow-withdraw for real withdrawals")
                break
            before_count = store.load_pending().withdrawn_count()
            messages.append(
                pending_cleanup_withdraw_next(
                    store,
                    browser,
                    dry_run=False,
                    allow_withdraw=True,
                )
            )
            real_withdraw_attempts += 1
            after_run = store.load_pending()
            latest = after_run.withdrawals[-1] if after_run.withdrawals else None
            if after_run.withdrawn_count() == before_count:
                status = latest.status.value if latest is not None else "missing-result"
                messages.append(f"stopped: withdrawal did not verify as withdrawn ({status})")
                break
            continue
        if plan.action in {"final-audit", "reaudit"}:
            messages.append(pending_cleanup_audit(store, browser, load_more=audit_load_more))
            if finish:
                messages.append(pending_cleanup_finish(store))
            break
        messages.append(f"stopped: unhandled plan action {plan.action}")
        break
    return "\n".join(messages)


def pending_cleanup_finish(store: Store, *, force: bool = False) -> str:
    run = store.load_pending()
    expected_delta = -run.withdrawn_count()
    delta = run.audited_delta()
    if not force and delta != expected_delta:
        raise RuntimeError(
            f"final audit delta is {format_delta(delta)}, expected {expected_delta}; "
            "import a fresh audit or use --force"
        )
    run.state = PendingCleanupState.DONE
    run.mark_updated()
    store.save_pending(run)
    store.append_pending_event(run, "finish", {"audited_delta": delta})
    return render_pending_report(run)


def load_fixture_browser(
    *,
    send_result: Path | None = None,
    capture: Path | None = None,
    audit: Path | None = None,
    followup_result: Path | None = None,
    withdraw_result: Path | None = None,
) -> BrowserClient:
    from .browser import FixtureBrowserClient

    return FixtureBrowserClient(
        send_result=send_result,
        capture=capture,
        audit=audit,
        followup_result=followup_result,
        withdraw_result=withdraw_result,
    )


def followup_id_for_candidate(source: str, name: str, profile_url: str | None) -> str:
    return acceptance_followup_id(candidate_key(source, name, profile_url))


def record_top_up_result_from_path(store: Store, path: Path, note: str | None = None) -> str:
    run = store.load_run()
    result = read_model(path, SalesNavSendResult)
    event = record_top_up_send_result(run, result, str(path), note)
    store.save_run(run)
    store.append_event(run, "record-top-up-result", {"path": str(path), "event": event})
    return (
        f"recorded top-up result as {event.status.value}; "
        f"row-level verified remains {run.verified_count()}/{run.target}"
    )


def load_acceptance_check_candidates(path: Path) -> list[AcceptanceCheckCandidate]:
    return [
        AcceptanceCheckCandidate.model_validate(item)
        for item in _load_json_list(path, "acceptance candidates")
    ]


def load_accepted_draft_candidates(path: Path) -> list[AcceptedDraftCandidate]:
    return [
        AcceptedDraftCandidate.model_validate(item)
        for item in _load_json_list(path, "accepted draft candidates")
    ]


def _load_json_list(path: Path, label: str) -> list[object]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{label} artifact must be a JSON array: {path}")
    return data


def _delta_suffix(delta: int | None) -> str:
    if delta is None:
        return ""
    return f", audited delta {delta}"
