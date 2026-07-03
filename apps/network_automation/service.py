"""Controller operations for the network automation CLI."""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .browser import BrowserClient
from .models import (
    AcceptanceCheckCandidate,
    AcceptanceStatus,
    CandidateEvent,
    CandidateObservation,
    CandidateStatus,
    LeadLedger,
    LeadReviewCandidate,
    LeadReviewDecisionArtifact,
    LeadReviewPacket,
    LeadStatus,
    PendingCapture,
    PendingCleanupState,
    PendingWithdrawResult,
    Run,
    RunState,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendResult,
    SavedSearchRow,
    SourceCaptureCursor,
    SourcePlan,
    SourceScanProgress,
    apply_audit,
    apply_pending_audit,
    capture_state_count,
    drain_stale_connectable_candidates,
    fill_run_from_reservoir,
    import_capture,
    import_capture_into_reservoir,
    import_pending_capture,
    is_send_noop_status,
    is_uncertain_send_status,
    lead_key_for_observation,
    lead_key_for_values,
    low_yield_source_names,
    new_pending_cleanup_run,
    new_run,
    now_utc,
    record_pending_withdraw_result,
    record_send_result,
    record_top_up_send_result,
    source_repeated_send_noop,
    sources_for_per_source_target,
    target_for_per_source_target,
)
from .reports import (
    format_delta,
    render_pending_report,
    render_report,
)
from .send_ledger import (
    _append_send_ledger_event,
    network_sends_summary,
    sync_send_ledger_from_history,
)
from .state_db import (
    NetworkStateDbStatus,
    NetworkStateMigrationSummary,
    migrate_json_ledgers,
    preview_json_migration,
)
from .store import Store, read_model, write_json_atomic
from .suppression import skip_outreach_suppressed_observations

__all__ = [
    "network_sends_summary",
    "sync_send_ledger_from_history",
]

DEFAULT_CONFIRM_SEND_OUT_DIR = Path("/tmp/linkedin-network-run-confirm-send")


def start_run(
    store: Store,
    *,
    target: int = 30,
    run_date: object | None = None,
    force: bool = False,
    max_real_sends: int | None = None,
    per_source_target: int | None = None,
    allow_fallback_sources: bool = True,
    source_names: Sequence[str] | None = None,
) -> str:
    if store.active_path.exists() and not force:
        raise RuntimeError("an active run already exists; use --force to replace it")

    parsed_date = run_date if isinstance(run_date, date) else None
    sources = None
    carry_over_shortfall = True
    explicit_source_names = [name.strip() for name in source_names or [] if name.strip()]
    if len(explicit_source_names) != len(set(explicit_source_names)):
        raise ValueError("--source values must be unique")
    if explicit_source_names and per_source_target is None:
        raise ValueError("--source requires --per-source-target")
    if per_source_target is not None:
        if per_source_target < 0:
            raise ValueError("--per-source-target must be >= 0")
        if explicit_source_names:
            target = per_source_target * len(explicit_source_names)
            sources = [
                SourcePlan(name=name, target=per_source_target)
                for name in explicit_source_names
            ]
            sources.append(SourcePlan(name="FO - Founders - Urgent", target=0, fallback=True))
        else:
            target = target_for_per_source_target(per_source_target)
            sources = sources_for_per_source_target(per_source_target)
        carry_over_shortfall = False
    run = new_run(
        target,
        parsed_date,
        max_real_sends,
        sources=sources,
        allow_fallback_sources=allow_fallback_sources,
        carry_over_shortfall=carry_over_shortfall,
    )
    store.save_run(run)
    store.append_event(
        run,
        "start",
        {
            "target": target,
            "per_source_target": per_source_target,
            "source_names": explicit_source_names,
            "allow_fallback_sources": allow_fallback_sources,
            "carry_over_shortfall": carry_over_shortfall,
        },
    )
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


def saved_search_row_for_source(path: Path, source: str) -> SavedSearchRow | None:
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
        return SavedSearchRow.model_validate(item)
    return None


def seed_run_source_progress(store: Store, saved_searches: Path) -> str:
    run = store.load_run()
    progress = store.load_source_progress()
    seeded: list[str] = []
    ended: list[str] = []
    for source in run.sources:
        row = saved_search_row_for_source(saved_searches, source.name)
        if row is None:
            continue
        existing = progress.sources.get(source.name)
        if (
            existing is None
            or existing.saved_search_id != row.saved_search_id
            or existing.saved_search_url != row.view_url
        ):
            progress.sources[source.name] = SourceScanProgress(
                source=source.name,
                saved_search_id=row.saved_search_id,
                saved_search_url=row.view_url,
                last_note="saved search initialized or changed",
            )
            continue
        if existing.end_of_results:
            source.exhausted = True
            ended.append(source.name)
            continue
        if existing.next_url:
            run.capture_cursors[source.name] = SourceCaptureCursor(
                source=source.name,
                saved_search_id=row.saved_search_id,
                saved_search_url=row.view_url,
                resume_url=existing.next_url,
                next_url=existing.next_url,
                last_scanned_url=existing.last_scanned_url,
                start_url=existing.last_started_url,
                end_of_results=False,
            )
            seeded.append(source.name)
    if seeded or ended:
        details: list[str] = []
        if seeded:
            details.append("seeded source progress: " + ", ".join(seeded))
        if ended:
            details.append("source progress already at end: " + ", ".join(ended))
        run.notes.extend(details)
    store.save_run(run)
    store.save_source_progress(progress)
    return (
        "source progress seeded"
        f"; resumed={len(seeded)}"
        f"; ended={len(ended)}"
    )


def reset_source_progress(store: Store, sources: Sequence[str]) -> str:
    if not sources:
        raise ValueError("at least one source is required")
    progress = store.load_source_progress()
    removed: list[str] = []
    missing: list[str] = []
    for source in sources:
        if progress.sources.pop(source, None) is None:
            missing.append(source)
        else:
            removed.append(source)
    store.save_source_progress(progress)

    updated_run_sources: list[str] = []
    if store.active_path.exists():
        run = store.load_run()
        for source in sources:
            run.capture_cursors.pop(source, None)
        for source_config in run.sources:
            if source_config.name in sources and source_config.exhausted:
                source_config.exhausted = False
                updated_run_sources.append(source_config.name)
        if updated_run_sources:
            run.notes.append("reset source progress: " + ", ".join(updated_run_sources))
            run.mark_updated()
            store.save_run(run)
            store.append_event(
                run,
                "reset-source-progress",
                {
                    "sources": list(sources),
                    "removed": removed,
                    "missing": missing,
                    "updated_run_sources": updated_run_sources,
                },
            )
    return (
        "source progress reset"
        f"; removed={len(removed)}"
        f"; missing={len(missing)}"
        f"; active_sources_reopened={len(updated_run_sources)}"
    )


def update_source_progress_after_capture(
    store: Store,
    *,
    source: str,
    saved_searches: Path | None,
    capture: SalesNavCapture,
    imported: int,
) -> None:
    progress = store.load_source_progress()
    row = saved_search_row_for_source(saved_searches, source) if saved_searches else None
    existing = progress.sources.get(source)
    zero_streak = existing.zero_usable_capture_streak if existing else 0
    zero_streak = 0 if imported > 0 else zero_streak + 1
    progress.sources[source] = SourceScanProgress(
        source=source,
        saved_search_id=(
            row.saved_search_id if row else (existing.saved_search_id if existing else None)
        ),
        saved_search_url=row.view_url if row else (existing.saved_search_url if existing else None),
        next_url=capture.next_url,
        last_scanned_url=capture.last_scanned_url or capture.url or capture.resume_url,
        last_started_url=capture.start_url,
        end_of_results=capture.end_of_results,
        zero_usable_capture_streak=zero_streak,
        last_raw_row_count=capture.raw_row_count or 0,
        last_output_row_count=capture.output_row_count or 0,
        last_connectable_count=capture_state_count(capture, "connectable"),
        last_already_pending_count=capture_state_count(capture, "already-pending"),
        last_state_counts=capture.state_counts,
        last_note=(
            "end of results"
            if capture.end_of_results
            else "advanced to next_url"
            if capture.next_url
            else "no next_url recorded"
        ),
    )
    store.save_source_progress(progress)


def sync_lead_ledger_from_observations(
    ledger: LeadLedger, observations: list[CandidateObservation]
) -> int:
    synced = 0
    for observation in observations:
        ledger.upsert_observation(observation)
        synced += 1
    return synced


def sync_lead_ledger_from_run(store: Store, run: Run) -> tuple[LeadLedger, int]:
    ledger = store.load_lead_ledger()
    synced = sync_lead_ledger_from_observations(ledger, run.observations)
    store.save_lead_ledger(ledger)
    return ledger, synced


def _lead_ledger_suppression_status(record_status: LeadStatus) -> CandidateStatus | None:
    if record_status == LeadStatus.PENDING:
        return CandidateStatus.ALREADY_PENDING
    if record_status in {
        LeadStatus.SKIPPED,
        LeadStatus.SENT,
        LeadStatus.CONNECTED,
        LeadStatus.BLOCKED,
    }:
        return CandidateStatus.SKIPPED
    return None


def apply_lead_ledger_suppression(run: Run, ledger: LeadLedger) -> list[CandidateEvent]:
    events: list[CandidateEvent] = []
    for observation in run.observations:
        if observation.menu_state != "connectable":
            continue
        if run.has_candidate_event_for_observation(observation):
            continue
        record = ledger.get_for_observation(observation)
        if record is None:
            continue
        status = _lead_ledger_suppression_status(record.status)
        if status is None:
            continue
        reason = f"lead ledger suppression: status={record.status.value}"
        if record.status_reason:
            reason += f"; reason={record.status_reason}"
        event = CandidateEvent(
            at=now_utc(),
            source=observation.source,
            name=observation.name,
            profile_url=observation.profile_url,
            status=status,
            note=reason,
        )
        run.candidates.append(event)
        events.append(event)
    if events:
        run.mark_updated()
    return events


def _is_public_linkedin_profile_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value.strip())
    host = parsed.netloc.casefold()
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme in {"http", "https"}
        and host in {"linkedin.com", "www.linkedin.com"}
        and len(parts) >= 2
        and parts[0] == "in"
    )


def _lead_send_blockers(
    record: Any | None, observation: CandidateObservation | None = None
) -> list[str]:
    public_profile_url = None
    search_url = None
    if record is not None:
        public_profile_url = record.public_profile_url
        search_url = record.search_url
    if not public_profile_url and observation is not None:
        public_profile_url = observation.public_profile_url
    if not search_url and observation is not None:
        search_url = observation.search_url
    if _is_public_linkedin_profile_url(public_profile_url):
        return []
    if search_url:
        return []
    return [
        "missing exact public LinkedIn /in/ URL and captured Sales Nav search row URL; "
        "capture or backfill public_profile_url before approval/send"
    ]


def _lead_is_approved_for_send(ledger: LeadLedger, observation: CandidateObservation) -> bool:
    record = ledger.get_for_observation(observation)
    return (
        record is not None
        and record.status == LeadStatus.APPROVED
        and not _lead_send_blockers(record, observation)
    )


def next_approved_connectable_observation(
    run: Run, ledger: LeadLedger, source: str | None = None
) -> CandidateObservation | None:
    target_source = source
    if target_source is None:
        next_source = run.next_source()
        if next_source is None:
            return None
        target_source = next_source.name
    if run.source_is_filled_or_closed(target_source):
        return None
    for observation in run.observations:
        if (
            observation.source == target_source
            and observation.menu_state == "connectable"
            and not run.has_candidate_event_for_observation(observation)
            and _lead_is_approved_for_send(ledger, observation)
        ):
            return observation
    return None


def next_approved_top_up_observation(
    run: Run, ledger: LeadLedger
) -> CandidateObservation | None:
    for observation in run.observations:
        if (
            run.source_is_fallback(observation.source)
            and observation.menu_state == "connectable"
            and not run.has_top_up_blocking_event_for_observation(observation)
            and _lead_is_approved_for_send(ledger, observation)
        ):
            return observation
    for observation in run.observations:
        if (
            observation.menu_state == "connectable"
            and not run.has_top_up_blocking_event_for_observation(observation)
            and _lead_is_approved_for_send(ledger, observation)
        ):
            return observation
    return None


def reviewable_observations(
    run: Run, ledger: LeadLedger, source: str | None = None
) -> list[CandidateObservation]:
    reviewable: list[CandidateObservation] = []
    for observation in run.observations:
        if source is not None and observation.source != source:
            continue
        if observation.menu_state != "connectable":
            continue
        if run.has_candidate_event_for_observation(observation):
            continue
        record = ledger.get_for_observation(observation)
        if record is None:
            record = ledger.upsert_observation(observation)
        if record.status == LeadStatus.NEW:
            reviewable.append(observation)
    return reviewable


def public_profile_url_blocked_observations(
    run: Run, ledger: LeadLedger, source: str | None = None
) -> list[CandidateObservation]:
    blocked: list[CandidateObservation] = []
    for observation in run.observations:
        if source is not None and observation.source != source:
            continue
        if observation.menu_state != "connectable":
            continue
        if run.has_candidate_event_for_observation(observation):
            continue
        record = ledger.get_for_observation(observation)
        if record is None or record.status != LeadStatus.APPROVED:
            continue
        if _lead_send_blockers(record, observation):
            blocked.append(observation)
    return blocked


def build_lead_review_packet(
    run: Run, ledger: LeadLedger, source: str | None = None
) -> LeadReviewPacket:
    candidates: list[LeadReviewCandidate] = []
    for observation in reviewable_observations(run, ledger, source):
        record = ledger.get_for_observation(observation)
        if record is None:
            record = ledger.upsert_observation(observation)
        candidates.append(
            LeadReviewCandidate(
                lead_key=lead_key_for_observation(observation),
                source=observation.source,
                name=observation.name,
                profile_url=observation.profile_url,
                public_profile_url=record.public_profile_url,
                search_url=observation.search_url,
                send_blockers=_lead_send_blockers(record, observation),
                captured_at=observation.captured_at,
                menu_state=observation.menu_state,
                menu_labels=list(observation.menu_labels),
                text=observation.text,
                links=list(observation.links),
                current_status=record.status,
                status_reason=record.status_reason,
                approved_reason=record.approved_reason,
            )
        )
    return LeadReviewPacket(source=source, candidates=candidates)


def render_lead_review_markdown(packet: LeadReviewPacket) -> str:
    lines = [
        "# LinkedIn Candidate Review",
        "",
        f"- Generated: `{packet.generated_at.isoformat()}`",
        f"- Source: `{packet.source or 'all sources'}`",
        f"- Candidates: `{len(packet.candidates)}`",
        "",
        "Decision artifact shape:",
        "",
        "```json",
        '{ "decisions": [',
        '  { "lead_key": "<lead key>", "status": "approved", "reason": "<why>" },',
        '  { "lead_key": "<lead key>", "status": "skipped", "reason": "<why>" }',
        "] }",
        "```",
        "",
    ]
    for index, candidate in enumerate(packet.candidates, start=1):
        lines.extend(
            [
                f"## {index}. {candidate.name}",
                "",
                f"- Lead key: `{candidate.lead_key}`",
                f"- Source: `{candidate.source}`",
                f"- Status: `{candidate.current_status.value}`",
                f"- Profile URL: `{candidate.profile_url or ''}`",
                f"- Public profile URL: `{candidate.public_profile_url or ''}`",
                f"- Captured search URL: `{candidate.search_url or ''}`",
                f"- Menu: `{candidate.menu_state}`",
            ]
        )
        if candidate.send_blockers:
            lines.append("- Send blockers: " + "; ".join(candidate.send_blockers))
        if candidate.menu_labels:
            labels = ", ".join(f"`{label}`" for label in candidate.menu_labels)
            lines.append("- Menu labels: " + labels)
        if candidate.status_reason:
            lines.append(f"- Status reason: {candidate.status_reason}")
        if candidate.text:
            readable_text = "\n".join(
                line.strip() for line in candidate.text.splitlines() if line.strip()
            )
            lines.extend(
                ["", "Readable row text:", "", "```text", readable_text, "```"]
            )
        if candidate.links:
            lines.extend(["", "Links:"])
            for link in candidate.links:
                if isinstance(link, dict):
                    text = link.get("text") or link.get("aria") or ""
                    href = link.get("href") or ""
                    lines.append(f"- {text}: {href}".strip())
                else:
                    lines.append(f"- {link}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_lead_review_packet(packet: LeadReviewPacket, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, packet.model_dump(mode="json", by_alias=False))
    markdown_path = out.with_suffix(".md")
    markdown_path.write_text(render_lead_review_markdown(packet), encoding="utf-8")
    return markdown_path


def lead_review_decisions_path(packet_path: Path) -> Path:
    return packet_path.with_name(packet_path.stem + "-decisions.json")


def _shell_command(parts: list[str | Path]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def lead_review_next_commands(
    store: Store,
    review_out: Path,
    *,
    saved_searches: Path | None = None,
    allow_fallback_sources: bool = True,
) -> list[str]:
    decisions_path = lead_review_decisions_path(review_out)
    resume_parts: list[str | Path] = [
        "uv",
        "run",
        "linkedin-tools",
        "network",
        "--state-dir",
        store.dir,
        "run-session",
        "--resume",
        "--out-dir",
        review_out.parent,
    ]
    if saved_searches is not None:
        resume_parts.extend(["--saved-searches", saved_searches])
    if not allow_fallback_sources:
        resume_parts.append("--no-fallback")
    resume_parts.append("--allow-send")
    return [
        f"edit decisions: {decisions_path}",
        _shell_command(
            [
                "uv",
                "run",
                "linkedin-tools",
                "network",
                "--state-dir",
                store.dir,
                "apply-lead-decisions",
                decisions_path,
            ]
        ),
        _shell_command(resume_parts),
    ]


def render_next_command_block(commands: list[str]) -> str:
    lines = ["next commands:"]
    lines.extend(f"{index}. {command}" for index, command in enumerate(commands, start=1))
    return "\n".join(lines)


def review_candidates(
    store: Store,
    *,
    source: str | None,
    out: Path,
    as_json: bool = False,
    next_commands: list[str] | None = None,
) -> str:
    run = store.load_run()
    ledger, synced = sync_lead_ledger_from_run(store, run)
    suppressed = apply_lead_ledger_suppression(run, ledger)
    if suppressed:
        store.save_run(run)
    packet = build_lead_review_packet(run, ledger, source)
    store.save_lead_ledger(ledger)
    markdown_path = write_lead_review_packet(packet, out)
    decisions_path = lead_review_decisions_path(out)
    next_commands = next_commands or lead_review_next_commands(store, out)
    store.append_event(
        run,
        "lead-review-packet",
        {
            "source": source,
            "out": str(out),
            "markdown": str(markdown_path),
            "decisions": str(decisions_path),
            "candidates": len(packet.candidates),
            "synced": synced,
            "suppressed": len(suppressed),
        },
    )
    payload = {
        "packet_path": str(out),
        "markdown_path": str(markdown_path),
        "decisions_path": str(decisions_path),
        "candidate_count": len(packet.candidates),
        "synced": synced,
        "suppressed": len(suppressed),
        "next_commands": next_commands,
        "apply_command": next_commands[1],
        "send_command": next_commands[2],
    }
    if as_json:
        return json.dumps(payload, indent=2)
    return (
        f"lead review packet: {len(packet.candidates)} candidate(s) written to {out}; "
        f"markdown={markdown_path}; decisions={decisions_path}; "
        f"synced={synced}; suppressed={len(suppressed)}\n"
        f"{render_next_command_block(next_commands)}"
    )


def apply_lead_review_decisions(store: Store, path: Path) -> str:
    artifact = read_model(path, LeadReviewDecisionArtifact)
    run = store.load_run()
    ledger, synced = sync_lead_ledger_from_run(store, run)
    changed: list[str] = []
    for decision in artifact.decisions:
        record = ledger.require(decision.lead_key)
        if decision.status == LeadStatus.APPROVED:
            blockers = _lead_send_blockers(record)
            if blockers:
                raise ValueError(
                    f"cannot approve {record.name}: " + "; ".join(blockers)
                )
            record = ledger.approve(decision.lead_key, decision.reason)
        elif decision.status == LeadStatus.SKIPPED:
            record = ledger.skip(decision.lead_key, decision.reason)
        elif decision.status == LeadStatus.BLOCKED:
            record = ledger.block(decision.lead_key, decision.reason)
        else:
            raise ValueError(
                "lead review decisions only support approved, skipped, or blocked; "
                f"got {decision.status.value}"
            )
        changed.append(f"{record.name}:{record.status.value}")
    suppressed = apply_lead_ledger_suppression(run, ledger)
    store.save_lead_ledger(ledger)
    store.save_run(run)
    store.append_event(
        run,
        "lead-review-decisions",
        {
            "path": str(path),
            "decisions": len(artifact.decisions),
            "changed": changed,
            "synced": synced,
            "suppressed": len(suppressed),
        },
    )
    return (
        f"applied {len(artifact.decisions)} lead review decision(s); "
        f"synced={synced}; suppressed={len(suppressed)}"
    )


def set_lead_public_profile_url(store: Store, lead_key: str, public_profile_url: str) -> str:
    if not _is_public_linkedin_profile_url(public_profile_url):
        raise ValueError("public profile URL must be an exact LinkedIn /in/ URL")
    ledger = store.load_lead_ledger()
    record = ledger.require(lead_key)
    record.public_profile_url = public_profile_url
    ledger.leads[lead_key] = record
    store.save_lead_ledger(ledger)
    updated_observations = 0
    if store.active_path.exists():
        run = store.load_run()
        for observation in run.observations:
            if lead_key_for_observation(observation) == lead_key:
                observation.public_profile_url = public_profile_url
                updated_observations += 1
        if updated_observations:
            run.mark_updated()
            store.save_run(run)
        store.append_event(
            run,
            "set-lead-public-profile-url",
            {
                "lead_key": lead_key,
                "public_profile_url": public_profile_url,
                "updated_observations": updated_observations,
            },
        )
    return (
        f"updated public profile URL for {record.name}; "
        f"observations={updated_observations}"
    )


def _candidate_event_lead_key(event: CandidateEvent) -> str:
    return lead_key_for_values(event.profile_url or event.public_profile_url, None, event.name)


def retry_failed_lead(store: Store, lead_key: str, reason: str | None = None) -> str:
    run = store.load_run()
    if run.state in {RunState.NEEDS_REAUDIT, RunState.DONE, RunState.BLOCKED}:
        raise RuntimeError(f"run state {run.state.value} cannot retry failed leads")
    ledger = store.load_lead_ledger()
    record = ledger.require(lead_key)
    if record.status != LeadStatus.APPROVED:
        raise RuntimeError(
            f"lead {record.name} must be approved before retry; status={record.status.value}"
        )
    blockers = _lead_send_blockers(record)
    if blockers:
        raise RuntimeError(f"lead {record.name} is not send-ready: " + "; ".join(blockers))
    matching_events = [
        event for event in run.candidates if _candidate_event_lead_key(event) == lead_key
    ]
    delivered_or_in_flight = [
        event
        for event in matching_events
        if event.status
        in {
            CandidateStatus.PENDING_PROVISIONAL,
            CandidateStatus.PENDING,
            CandidateStatus.ACCEPTED,
            CandidateStatus.ALREADY_PENDING,
            CandidateStatus.AUDIT_TOP_UP,
        }
    ]
    if delivered_or_in_flight:
        statuses = ", ".join(event.status.value for event in delivered_or_in_flight)
        raise RuntimeError(
            f"lead {record.name} has delivered or in-flight candidate event(s): {statuses}"
        )
    failed_events = [event for event in matching_events if event.status == CandidateStatus.FAILED]
    if not failed_events:
        raise RuntimeError(f"lead {record.name} has no failed candidate event to retry")
    run.candidates = [
        event
        for event in run.candidates
        if not (
            _candidate_event_lead_key(event) == lead_key
            and event.status == CandidateStatus.FAILED
        )
    ]
    run.mark_updated()
    store.save_run(run)
    store.append_event(
        run,
        "retry-failed-lead",
        {
            "lead_key": lead_key,
            "name": record.name,
            "reason": reason,
            "removed_failed_events": failed_events,
        },
    )
    return (
        f"cleared {len(failed_events)} failed candidate event(s) for {record.name}; "
        "lead remains approved"
    )


def apply_candidate_event_to_lead_ledger(store: Store, event: CandidateEvent) -> None:
    ledger = store.load_lead_ledger()
    ledger.apply_candidate_event(event)
    store.save_lead_ledger(ledger)


def _with_ledger_public_profile_url(
    observation: CandidateObservation, ledger: LeadLedger
) -> CandidateObservation:
    record = ledger.get_for_observation(observation)
    if record and record.public_profile_url and not observation.public_profile_url:
        return observation.model_copy(update={"public_profile_url": record.public_profile_url})
    return observation


def apply_candidate_events_to_lead_ledger(store: Store, events: list[CandidateEvent]) -> None:
    if not events:
        return
    ledger = store.load_lead_ledger()
    for event in events:
        ledger.apply_candidate_event(event)
    store.save_lead_ledger(ledger)


def _review_needed_message(run: Run, ledger: LeadLedger) -> str:
    public_url_blocked = public_profile_url_blocked_observations(run, ledger)
    if public_url_blocked:
        names = ", ".join(observation.name for observation in public_url_blocked[:5])
        suffix = "..." if len(public_url_blocked) > 5 else ""
        return (
            f"{len(public_url_blocked)} approved candidate(s) are blocked from send "
            "because public_profile_url is missing or invalid and search_url is missing: "
            f"{names}{suffix}"
        )
    reviewable = reviewable_observations(run, ledger)
    if reviewable:
        return (
            f"no approved connectable candidate available; {len(reviewable)} candidate(s) "
            "need review via review-candidates and apply-lead-decisions"
        )
    return "no approved connectable candidate available"


def require_saved_search_coverage(store: Store, saved_searches: Path) -> str:
    run = store.load_run()
    missing: list[str] = []
    checked = 0
    for source in run.sources:
        if source.fallback or source.exhausted or source.target <= 0:
            continue
        checked += 1
        cursor = run.capture_cursors.get(source.name)
        if cursor and cursor.resume_url:
            continue
        if resolve_network_source_url(saved_searches, source.name) is None:
            missing.append(source.name)
    if missing:
        note = (
            "saved-search coverage missing for targeted source(s): "
            + ", ".join(missing)
        )
        run.state = RunState.BLOCKED
        run.notes.append(note)
        run.mark_updated()
        store.save_run(run)
        store.append_event(run, "saved-search-coverage-blocked", {"missing": missing})
        raise RuntimeError(note)
    return f"saved-search coverage ok for {checked} targeted source(s)"


def network_run_session(
    store: Store,
    browser: BrowserClient,
    *,
    target: int,
    max_real_sends: int | None,
    force: bool,
    resume: bool,
    per_source_target: int | None,
    allow_fallback_sources: bool,
    saved_searches_url: str,
    saved_searches_out: Path,
    refresh_saved_searches: bool,
    audit_attempts: int,
    audit_delay_ms: int,
    allow_send: bool,
    max_steps: int,
    finish: bool,
    confirm_delay_ms: int = 5000,
    confirm_out_dir: Path = DEFAULT_CONFIRM_SEND_OUT_DIR,
    review_out: Path = Path("/tmp/linkedin-network-session/lead-review-candidates.json"),
    source_names: Sequence[str] | None = None,
    emit: Callable[[str], None] | None = None,
) -> str:
    messages: list[str] = []

    def add(message: str) -> None:
        messages.append(message)
        if emit is not None:
            emit(message)

    if resume:
        run = store.load_run()
        if not allow_fallback_sources and run.allow_fallback_sources:
            run.allow_fallback_sources = False
            run.mark_updated()
            store.save_run(run)
            store.append_event(run, "disable-fallback-sources", {})
        add(f"resumed run {run.id} for {run.date.isoformat()}")
        if refresh_saved_searches or not saved_searches_out.exists():
            add(
                "refreshing saved-search index"
                if refresh_saved_searches
                else "capturing saved-search index"
            )
            add(
                capture_saved_searches(
                    browser,
                    url=saved_searches_url,
                    out=saved_searches_out,
                )
            )
            add(seed_run_source_progress(store, saved_searches_out))
    else:
        add(
            start_run(
                store,
                target=target,
                force=force,
                max_real_sends=max_real_sends,
                per_source_target=per_source_target,
                allow_fallback_sources=allow_fallback_sources,
                source_names=source_names,
            )
        )
        add("auditing sent-page count before run")
        add(reconcile_audit(store, browser, attempts=1, delay_ms=0, finish=False))
        if saved_searches_out.exists() and not refresh_saved_searches:
            add(f"using saved searches from {saved_searches_out}")
        else:
            add(
                "refreshing saved-search index"
                if refresh_saved_searches
                else "capturing saved-search index"
            )
            add(
                capture_saved_searches(
                    browser,
                    url=saved_searches_url,
                    out=saved_searches_out,
                )
            )
        add(seed_run_source_progress(store, saved_searches_out))
    add(require_saved_search_coverage(store, saved_searches_out))
    zero_capture_streaks: dict[str, int] = {}
    for _ in range(max_steps):
        plan = store.load_run().operator_plan_with_reservoir(store.load_reservoir())
        add(f"plan: {plan.action}")
        if plan.action == "source-exhausted":
            if not plan.source:
                raise RuntimeError("source-exhausted plan did not include source")
            add(
                source_exhausted(
                    store,
                    plan.source,
                    note=plan.reason or "source cursor is already at end of results",
                )
            )
            continue
        if plan.action == "use-reservoir":
            if not plan.source:
                raise RuntimeError("use-reservoir plan did not include source")
            add(reservoir_fill_run(store, source=plan.source, limit=None))
            continue
        if plan.action == "capture-source":
            if plan.source is None or plan.capture is None:
                raise RuntimeError("capture-source plan did not include source/capture details")
            source_url = plan.resume_url or resolve_network_source_url(
                saved_searches_out, plan.source
            )
            if source_url is None:
                raise RuntimeError(f"network source URL missing for source {plan.source}")
            before_imported = len(store.load_run().observations)
            add(
                f"capturing source {plan.source}: pages={plan.capture.pages}; "
                f"stop_after_connectable={plan.capture.stop_after_connectable}"
            )
            capture_message = capture_source(
                store,
                browser,
                source=plan.source,
                url=source_url,
                saved_searches=saved_searches_out,
                pages=plan.capture.pages,
                limit=0,
                stop_after_connectable=plan.capture.stop_after_connectable,
                only_connectable=True,
                row_scroll_delay_ms=250,
            )
            add(capture_message)
            after_run = store.load_run()
            imported = len(after_run.observations) - before_imported
            if imported > 0:
                zero_capture_streaks[plan.source] = 0
                run_after_capture = store.load_run()
                ledger_after_capture = store.load_lead_ledger()
                if reviewable_observations(run_after_capture, ledger_after_capture, plan.source):
                    commands = lead_review_next_commands(
                        store,
                        review_out,
                        saved_searches=saved_searches_out,
                        allow_fallback_sources=allow_fallback_sources,
                    )
                    add(
                        review_candidates(
                            store,
                            source=plan.source,
                            out=review_out,
                            next_commands=commands,
                        )
                    )
                    add("stopped: lead review required before connection requests")
                    break
            else:
                streak = zero_capture_streaks.get(plan.source, 0) + 1
                zero_capture_streaks[plan.source] = streak
                cursor = after_run.capture_cursors.get(plan.source)
                if cursor and cursor.end_of_results:
                    note = (
                        "reached end of saved-search results with no usable candidates; "
                        "carrying remaining quota forward"
                    )
                    add(source_exhausted(store, plan.source, note=note))
            continue
        if plan.action == "send-candidate":
            run_for_send = store.load_run()
            ledger_for_send = store.load_lead_ledger()
            if (
                next_approved_connectable_observation(run_for_send, ledger_for_send)
                is None
                and reviewable_observations(run_for_send, ledger_for_send)
            ):
                commands = lead_review_next_commands(
                    store,
                    review_out,
                    saved_searches=saved_searches_out,
                    allow_fallback_sources=allow_fallback_sources,
                )
                add(
                    review_candidates(
                        store,
                        source=None,
                        out=review_out,
                        next_commands=commands,
                    )
                )
                add("stopped: lead review required before connection requests")
                break
            if not allow_send:
                add("stopped: pass --allow-send for real network sends")
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
                    emit=emit,
                )
            )
            continue
        if plan.action in {"reaudit", "final-audit"}:
            add(
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
                    add(finish_run(store))
                else:
                    raise RuntimeError(
                        f"durable confirmed sends are {run.verified_count()}/{run.target}; "
                        "continue normal guarded sends before finishing"
                    )
            break
        add(f"stopped: {plan.reason or plan.action}")
        break
    else:
        add(f"stopped: max steps {max_steps} reached")
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
    apply_candidate_events_to_lead_ledger(store, [event, *drained])
    _append_send_ledger_event(
        store,
        run,
        event,
        event_kind="record",
        confirmed_at=event.at,
    )
    store.append_event(run, "record", event)
    if drained:
        store.append_event(run, "drain-stale-candidates", {"events": drained})
    return f"recorded {status.value}; verified {run.verified_count()}/{run.target}"


def network_state_db_status(store: Store, *, as_json: bool = False) -> str:
    status = store.state_db.status()
    if as_json:
        return json.dumps(status.to_json_dict(), indent=2)
    return render_network_state_db_status(status)


def network_state_migrate_sqlite(
    store: Store, *, apply: bool, as_json: bool = False
) -> str:
    if apply:
        summary = migrate_json_ledgers(
            store.state_db,
            acceptance_ledger_path=store.acceptance_ledger_path,
            acceptance_followup_ledger_path=store.acceptance_followup_ledger_path,
            send_ledger_path=store.send_ledger_path,
        )
    else:
        summary = preview_json_migration(
            database_path=store.database_path,
            acceptance_ledger_path=store.acceptance_ledger_path,
            acceptance_followup_ledger_path=store.acceptance_followup_ledger_path,
            send_ledger_path=store.send_ledger_path,
        )
    if as_json:
        return json.dumps(summary.to_json_dict(), indent=2)
    return render_network_state_migration_summary(summary)


def render_network_state_db_status(status: NetworkStateDbStatus) -> str:
    return "\n".join(
        [
            "# Network State DB",
            f"- Path: {status.database_path}",
            f"- Exists: {'yes' if status.exists else 'no'}",
            "- Applied migrations: "
            + (", ".join(str(item) for item in status.applied_migrations) or "none"),
            f"- Acceptance invitations: {status.acceptance_invitations}",
            f"- Acceptance outcome events: {status.acceptance_outcome_events}",
            f"- Acceptance follow-ups: {status.acceptance_followups}",
            f"- Acceptance follow-up attempts: {status.acceptance_followup_attempts}",
            f"- Send ledger entries: {status.send_ledger_entries}",
            "- Canonical ledgers: "
            f"acceptance={'yes' if status.canonical_acceptance_ledger else 'no'}, "
            f"followups={'yes' if status.canonical_acceptance_followups else 'no'}, "
            f"sends={'yes' if status.canonical_send_ledger else 'no'}",
        ]
    )


def render_network_state_migration_summary(
    summary: NetworkStateMigrationSummary,
) -> str:
    mode = "dry-run" if summary.dry_run else "applied"
    lines = [
        f"SQLite migration {mode}: {summary.database_path}",
        f"- Acceptance invitations: {summary.acceptance_invitations}",
        f"- Acceptance outcome events: {summary.acceptance_outcome_events}",
        f"- Acceptance follow-ups: {summary.acceptance_followups}",
        f"- Acceptance follow-up attempts: {summary.acceptance_followup_attempts}",
        f"- Send ledger entries: {summary.send_ledger_entries}",
    ]
    if summary.warnings:
        lines.append("- Warnings:")
        lines.extend(f"  - {warning}" for warning in summary.warnings)
    return "\n".join(lines)


def record_send_result_from_path(store: Store, path: Path) -> str:
    run = store.load_run()
    if run.state == RunState.NEEDS_REAUDIT:
        raise RuntimeError("run is in NEEDS_REAUDIT; record a fresh sent-page audit first")
    result = read_model(path, SalesNavSendResult)
    event = record_send_result(run, result, str(path))
    drained = drain_stale_connectable_candidates(run)
    store.save_run(run)
    apply_candidate_events_to_lead_ledger(store, [event, *drained])
    _append_send_ledger_event(
        store,
        run,
        event,
        event_kind="record-send-result",
        result_path=str(path),
        confirmed_at=None if event.status == CandidateStatus.PENDING_PROVISIONAL else event.at,
    )
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
        profile_url=event.public_profile_url or event.profile_url,
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
    apply_candidate_event_to_lead_ledger(store, candidate)
    confirmed_at = now_utc()
    _append_send_ledger_event(
        store,
        run,
        candidate,
        event_kind="confirm-send-result",
        result_path=str(path),
        confirmed_at=confirmed_at,
    )
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
    emit: Callable[[str], None] | None = None,
) -> str:
    messages: list[str] = []

    def add(message: str) -> None:
        messages.append(message)
        if emit is not None:
            emit(message)

    run = store.load_run()
    if run.state == RunState.NEEDS_REAUDIT:
        raise RuntimeError("run is in NEEDS_REAUDIT; record a fresh sent-page audit before sending")
    if allow_send and run.real_send_capacity_remaining() == 0:
        raise RuntimeError(
            f"real-send cap reached: {run.real_send_attempt_count()}/{run.max_real_sends} "
            "real send attempts"
        )
    ledger, _synced = sync_lead_ledger_from_run(store, run)
    lead_suppressed = apply_lead_ledger_suppression(run, ledger)
    suppressed = skip_outreach_suppressed_observations(run)
    for event in suppressed:
        ledger.apply_candidate_event(event)
    if lead_suppressed or suppressed:
        store.save_lead_ledger(ledger)
        store.save_run(run)
    if lead_suppressed:
        store.append_event(run, "lead-ledger-suppression", {"events": lead_suppressed})
    if suppressed:
        store.append_event(run, "cross-workflow-suppression", {"events": suppressed})
    candidate = next_approved_connectable_observation(run, ledger)
    if candidate is None:
        raise RuntimeError(_review_needed_message(run, ledger))
    candidate = _with_ledger_public_profile_url(candidate, ledger)
    if dry_run or not allow_send:
        add(f"dry-running candidate: {candidate.name}")
    else:
        add(f"sending candidate: {candidate.name}")
    result, path = browser.send_connection(
        candidate, dry_run=dry_run or not allow_send, allow_send=allow_send
    )
    add(f"send status: {result.status}")
    if allow_send and not dry_run and not no_record:
        run = store.load_run()
        event = record_send_result(run, result, path)
        drained = drain_stale_connectable_candidates(run)
        store.save_run(run)
        apply_candidate_events_to_lead_ledger(store, [event, *drained])
        _append_send_ledger_event(
            store,
            run,
            event,
            event_kind="record-send-result",
            result_path=path,
            confirmed_at=None if event.status == CandidateStatus.PENDING_PROVISIONAL else event.at,
        )
        store.append_event(run, "record-send-result", {"path": path, "event": event})
        if drained:
            store.append_event(run, "drain-stale-candidates", {"events": drained})
        add(f"send result: {path}; recorded {event.status.value}")
        if event.status == CandidateStatus.PENDING_PROVISIONAL:
            add(f"confirming provisional send: {event.name}")
            add(
                confirm_provisional_send(
                    store,
                    browser,
                    event,
                    delay_ms=confirm_delay_ms,
                    out_dir=confirm_out_dir,
                )
            )
        return "\n".join(messages)
    add(f"send result: {path}; dry_run={dry_run or not allow_send}")
    return "\n".join(messages)


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
    emit: Callable[[str], None] | None = None,
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

    def add(message: str) -> None:
        messages.append(message)
        if emit is not None:
            emit(message)

    while attempts < max_attempts:
        run = store.load_run()
        if run.state == RunState.NEEDS_REAUDIT:
            raise RuntimeError("run entered NEEDS_REAUDIT; import a fresh audit before continuing")
        ledger, _synced = sync_lead_ledger_from_run(store, run)
        lead_suppressed = apply_lead_ledger_suppression(run, ledger)
        drained = drain_stale_connectable_candidates(run)
        suppressed = skip_outreach_suppressed_observations(run)
        for event in [*drained, *suppressed]:
            ledger.apply_candidate_event(event)
        if lead_suppressed or drained or suppressed:
            store.save_lead_ledger(ledger)
            store.save_run(run)
        if lead_suppressed:
            store.append_event(run, "lead-ledger-suppression", {"events": lead_suppressed})
        if drained:
            store.append_event(run, "drain-stale-candidates", {"events": drained})
        if suppressed:
            store.append_event(run, "cross-workflow-suppression", {"events": suppressed})
        next_source = run.next_source()
        if next_source is None or next_source.name != source:
            break
        if run.real_send_capacity_remaining() == 0:
            raise RuntimeError(
                f"real-send cap reached: {run.real_send_attempt_count()}/{run.max_real_sends} "
                "real send attempts"
            )
        candidate = next_approved_connectable_observation(run, ledger, source)
        if candidate is None:
            if reviewable_observations(run, ledger, source):
                raise RuntimeError(_review_needed_message(run, ledger))
            break
        candidate = _with_ledger_public_profile_url(candidate, ledger)
        attempts += 1
        if dry_run or not single_pass:
            add(f"dry-running candidate: {candidate.name}")
            dry_result, dry_path = browser.send_connection(
                candidate, dry_run=True, allow_send=False
            )
            add(f"dry-run status: {dry_result.status}")
            if dry_result.status != "dry-run-connectable":
                if not no_record:
                    run = store.load_run()
                    event = record_send_result(run, dry_result, dry_path)
                    store.save_run(run)
                    apply_candidate_event_to_lead_ledger(store, event)
                    store.append_event(
                        run, "record-send-result", {"path": dry_path, "event": event}
                    )
                continue
            if dry_run:
                break
        run = store.load_run()
        add(f"sending candidate: {candidate.name}")
        result, path = browser.send_connection(candidate, dry_run=False, allow_send=True)
        add(f"send status: {result.status}")
        if no_record:
            break
        event = record_send_result(run, result, path)
        drained = drain_stale_connectable_candidates(run)
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
        apply_candidate_events_to_lead_ledger(store, [event, *drained])
        _append_send_ledger_event(
            store,
            run,
            event,
            event_kind="record-send-result",
            result_path=path,
            confirmed_at=None if event.status == CandidateStatus.PENDING_PROVISIONAL else event.at,
        )
        store.append_event(run, "record-send-result", {"path": path, "event": event})
        if drained:
            store.append_event(run, "drain-stale-candidates", {"events": drained})
        if event.status == CandidateStatus.PENDING_PROVISIONAL:
            add(f"confirming provisional send: {event.name}")
            add(
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
        ledger, _synced = sync_lead_ledger_from_run(store, run)
        lead_suppressed = apply_lead_ledger_suppression(run, ledger)
        suppressed = skip_outreach_suppressed_observations(run)
        for event in suppressed:
            ledger.apply_candidate_event(event)
        if lead_suppressed or suppressed:
            store.save_lead_ledger(ledger)
            store.save_run(run)
        if lead_suppressed:
            store.append_event(run, "lead-ledger-suppression", {"events": lead_suppressed})
        if suppressed:
            store.append_event(run, "cross-workflow-suppression", {"events": suppressed})
        candidate = next_approved_top_up_observation(run, ledger)
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
            run = store.load_run()
            ledger = store.load_lead_ledger()
            candidate = next_approved_top_up_observation(run, ledger)
        if candidate is None:
            raise RuntimeError(_review_needed_message(store.load_run(), ledger))
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
        apply_candidate_event_to_lead_ledger(store, event)
        _append_send_ledger_event(
            store,
            run,
            event,
            event_kind="record-send-result",
            result_path=result_path,
            confirmed_at=None if event.status == CandidateStatus.PENDING_PROVISIONAL else event.at,
        )
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
    ledger, synced = sync_lead_ledger_from_run(store, run)
    suppressed = skip_outreach_suppressed_observations(run)
    for event in suppressed:
        ledger.apply_candidate_event(event)
    lead_suppressed = apply_lead_ledger_suppression(run, ledger)
    drained = drain_stale_connectable_candidates(run)
    for event in drained:
        ledger.apply_candidate_event(event)
    store.save_lead_ledger(ledger)
    store.save_run(run)
    store.append_event(
        run,
        "import-capture",
        {
            "path": str(path),
            "imported": imported,
            "only_connectable": only_connectable,
            "lead_synced": synced,
            "lead_suppressed": len(lead_suppressed),
        },
    )
    if suppressed:
        store.append_event(run, "cross-workflow-suppression", {"events": suppressed})
    if lead_suppressed:
        store.append_event(run, "lead-ledger-suppression", {"events": lead_suppressed})
    if drained:
        store.append_event(run, "drain-stale-candidates", {"events": drained})
    total_suppressed = len(suppressed) + len(lead_suppressed)
    suffix = f"; suppressed {total_suppressed}" if total_suppressed else ""
    return f"imported {imported} candidate observations{suffix}"


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
    ledger, synced = sync_lead_ledger_from_run(store, run)
    suppressed = skip_outreach_suppressed_observations(run)
    for event in suppressed:
        ledger.apply_candidate_event(event)
    lead_suppressed = apply_lead_ledger_suppression(run, ledger)
    drained = drain_stale_connectable_candidates(run)
    for event in drained:
        ledger.apply_candidate_event(event)
    store.save_lead_ledger(ledger)
    store.save_run(run)
    update_source_progress_after_capture(
        store,
        source=capture_source_name,
        saved_searches=saved_searches,
        capture=capture,
        imported=imported,
    )
    store.append_event(
        run,
        "capture",
        {
            "path": path,
            "source": capture_source_name,
            "imported": imported,
            "only_connectable": only_connectable,
            "lead_synced": synced,
            "lead_suppressed": len(lead_suppressed),
        },
    )
    if suppressed:
        store.append_event(run, "cross-workflow-suppression", {"events": suppressed})
    if lead_suppressed:
        store.append_event(run, "lead-ledger-suppression", {"events": lead_suppressed})
    if drained:
        store.append_event(run, "drain-stale-candidates", {"events": drained})
    total_suppressed = len(suppressed) + len(lead_suppressed)
    return (
        f"captured {imported} candidate observations from {capture_source_name}"
        f"{f'; suppressed {total_suppressed}' if total_suppressed else ''}; out={path}"
    )


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
    if force and run.verified_count() < run.target:
        run.notes.append(
            f"force-finished incomplete run with durable confirmed sends "
            f"{run.verified_count()}/{run.target}"
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
    ledger, synced = sync_lead_ledger_from_run(store, run)
    suppressed = skip_outreach_suppressed_observations(run)
    for event in suppressed:
        ledger.apply_candidate_event(event)
    lead_suppressed = apply_lead_ledger_suppression(run, ledger)
    store.save_lead_ledger(ledger)
    store.save_run(run)
    store.save_reservoir(reservoir)
    store.append_event(
        run,
        "reservoir-fill-run",
        {
            "source": fill_source,
            "imported": imported,
            "suppressed": len(suppressed),
            "lead_synced": synced,
            "lead_suppressed": len(lead_suppressed),
        },
    )
    if suppressed:
        store.append_event(run, "cross-workflow-suppression", {"events": suppressed})
    if lead_suppressed:
        store.append_event(run, "lead-ledger-suppression", {"events": lead_suppressed})
    total_suppressed = len(suppressed) + len(lead_suppressed)
    suffix = f"; suppressed {total_suppressed}" if total_suppressed else ""
    return f"filled active run with {imported} reservoir candidates{suffix}"


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


def resolve_network_source_url(path: Path | None, source: str) -> str | None:
    if path is None:
        return None
    return resolve_saved_search_url(path, source)


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


def record_top_up_result_from_path(store: Store, path: Path, note: str | None = None) -> str:
    run = store.load_run()
    result = read_model(path, SalesNavSendResult)
    event = record_top_up_send_result(run, result, str(path), note)
    store.save_run(run)
    apply_candidate_event_to_lead_ledger(store, event)
    _append_send_ledger_event(
        store,
        run,
        event,
        event_kind="record-top-up-result",
        result_path=str(path),
        confirmed_at=None if event.status == CandidateStatus.PENDING_PROVISIONAL else event.at,
    )
    store.append_event(run, "record-top-up-result", {"path": str(path), "event": event})
    return (
        f"recorded top-up result as {event.status.value}; "
        f"row-level verified remains {run.verified_count()}/{run.target}"
    )


def _delta_suffix(delta: int | None) -> str:
    if delta is None:
        return ""
    return f", audited delta {delta}"
