"""Acceptance tracking and accepted follow-up workflows."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from .browser import BrowserClient
from .models import (
    WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE,
    AcceptanceCheckCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceOutcomeArtifact,
    AcceptedDraftCandidate,
    AcceptedFollowupReviewItem,
    AcceptedFollowupReviewPacket,
    AcceptedResearchArtifact,
    AcceptedResearchRow,
    DraftReport,
    DraftStrategy,
    LeadLedger,
    RunState,
    acceptance_followup_id,
    accepted_followup_candidate_key,
    apply_acceptance_followup_send_result,
    build_draft_report,
    candidate_key,
    lead_key_for_values,
    now_utc,
    render_draft_markdown,
    validate_acceptance_followup_can_send,
)
from .reports import render_acceptance_report
from .store import Store, read_model, write_json_atomic


def add_lead_review_context_to_draft_report(
    report: DraftReport, lead_ledger: LeadLedger
) -> None:
    for item in report.items:
        candidate = item.candidate
        lead_key = lead_key_for_values(
            candidate.sales_nav_profile_url or candidate.profile_url,
            None,
            candidate.name,
        )
        record = lead_ledger.leads.get(lead_key)
        if record is None:
            continue
        if record.first_source:
            item.evidence.append(f"Original connection source: {record.first_source}")
        if record.approved_reason:
            item.evidence.append(
                f"Original connection approval: {record.approved_reason}"
            )


def accepted_research_by_key(
    artifact: AcceptedResearchArtifact | None,
) -> dict[str, AcceptedResearchRow]:
    if artifact is None:
        return {}
    return {
        candidate_key(row.source, row.name, row.sales_nav_profile_url or row.profile_url): row
        for row in artifact.rows
    }


def build_accepted_followup_review_packet(
    report: DraftReport,
    artifact: AcceptedResearchArtifact | None,
    *,
    report_path: Path,
    research_path: Path | None,
) -> AcceptedFollowupReviewPacket:
    research_rows = accepted_research_by_key(artifact)
    items: list[AcceptedFollowupReviewItem] = []
    for item in report.items:
        key = accepted_followup_candidate_key(item.candidate)
        items.append(
            AcceptedFollowupReviewItem(
                followup_id=acceptance_followup_id(key),
                candidate=item.candidate,
                template_key=item.template_key,
                angle=item.angle,
                draft=item.draft,
                person_does=item.person_does,
                company_does=item.company_does,
                message_fit=item.message_fit,
                company_profile_url=item.company_profile_url,
                company_website_url=item.company_website_url,
                evidence=list(item.evidence),
                warnings=list(item.warnings),
                research=research_rows.get(key),
            )
        )
    return AcceptedFollowupReviewPacket(
        report_path=str(report_path),
        research_path=str(research_path) if research_path else None,
        items=items,
    )


def render_accepted_followup_review_markdown(packet: AcceptedFollowupReviewPacket) -> str:
    lines = [
        "# Accepted Follow-Up Draft Review",
        "",
        f"- Generated: `{packet.generated_at.isoformat()}`",
        f"- Report: `{packet.report_path}`",
        f"- Research: `{packet.research_path or ''}`",
        f"- Drafts: `{len(packet.items)}`",
        "",
        "Review these drafts before running any dry-run or send command.",
    ]
    for item in packet.items:
        lines.extend(
            [
                "",
                "## " + item.candidate.name,
                f"- Follow-up ID: `{item.followup_id}`",
                f"- Source: `{item.candidate.source}`",
                f"- Template: `{item.template_key.value}`",
                "- Best angle: " + item.angle,
            ]
        )
        if item.person_does:
            lines.append("- Person does: " + item.person_does)
        if item.company_does:
            lines.append("- Company does: " + item.company_does)
        if item.message_fit:
            lines.append("- Why this draft fits: " + item.message_fit)
        if item.company_profile_url:
            lines.append("- Company profile: " + item.company_profile_url)
        if item.company_website_url:
            lines.append("- Company website: " + item.company_website_url)
        if item.evidence:
            lines.append("- Evidence:")
            lines.extend("  - " + evidence for evidence in item.evidence)
        if item.warnings:
            lines.append("- Warnings:")
            lines.extend("  - " + warning for warning in item.warnings)
        lines.extend(["", "Draft:", ""])
        lines.extend("> " + line if line else ">" for line in item.draft.splitlines())
    return "\n".join(lines) + "\n"


def write_accepted_followup_review_packet(
    packet: AcceptedFollowupReviewPacket, out: Path
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out, packet.model_dump(mode="json", by_alias=False))
    markdown_path = out.with_suffix(".md")
    markdown_path.write_text(render_accepted_followup_review_markdown(packet), encoding="utf-8")
    return markdown_path


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


def acceptance_invalidate_weak_message_acceptances(
    store: Store, *, apply: bool, sample_limit: int = 10
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    invitations = ledger.weak_message_acceptances()
    keys = {invitation.key() for invitation in invitations}
    followup_records = followups.invalidatable_for_acceptance_keys(keys)
    followup_status_by_key = {record.key: record.status.value for record in followups.drafts}

    if apply and invitations:
        invalidated_keys = set(ledger.invalidate_weak_message_acceptances())
        followup_count = followups.invalidate_acceptance_keys(invalidated_keys)
        store.save_acceptance_ledger(ledger)
        store.save_acceptance_followup_ledger(followups)
        store.append_acceptance_event(
            "invalidate-weak-message-acceptances",
            {
                "apply": True,
                "reason": WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE,
                "invitations": len(invalidated_keys),
                "followups": followup_count,
                "sample": [
                    {
                        "name": invitation.name,
                        "source": invitation.source,
                        "profile_url": invitation.profile_url,
                    }
                    for invitation in invitations[: max(0, sample_limit)]
                ],
            },
        )
        mode = "applied"
        followup_count_for_output = followup_count
    else:
        mode = "dry-run"
        followup_count_for_output = len(followup_records)

    lines = [
        (
            f"weak acceptance invalidation {mode}: {len(invitations)} invitation(s), "
            f"{followup_count_for_output} follow-up draft(s)"
        )
    ]
    if sample_limit > 0 and invitations:
        lines.append("sample:")
        for invitation in invitations[:sample_limit]:
            followup_status = followup_status_by_key.get(invitation.key(), "none")
            lines.append(
                "- "
                f"{invitation.name} | {invitation.source} | "
                f"followup={followup_status} | {invitation.profile_url or 'no profile URL'}"
            )
    if not apply and invitations:
        lines.append("rerun with --apply to update acceptance and follow-up ledgers")
    return "\n".join(lines)


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
    followup_review_out: Path | None,
    followup_research_out_dir: Path | None,
    include_drafted: bool,
    strategy: DraftStrategy,
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
                    delay_ms=research_delay_ms,
                    review_out=followup_review_out,
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
        try:
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
        except Exception as exc:
            blocker = (
                f"{chunk_path} failed during acceptance check "
                f"(offset={offset}, limit={limit}, candidates={len(candidates)}): {exc}"
            )
            store.append_acceptance_event(
                "run-daily-session-blocked",
                {
                    "reason": "acceptance chunk check failed",
                    "blockers": [blocker],
                    "input": str(candidates_out),
                    "out": str(chunk_path),
                    "offset": offset,
                    "limit": limit,
                    "candidates": len(candidates),
                },
            )
            messages.append("stopped: " + blocker)
            return messages
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
    delay_ms: int = 500,
    research_offset: int = 0,
    research_limit: int = 0,
    review_out: Path | None = None,
) -> str:
    ledger = store.load_acceptance_ledger()
    followups = store.load_acceptance_followup_ledger()
    candidates = ledger.accepted_for_followup(followups, include_drafted)
    if research_limit > 0:
        report_candidates = candidates[research_offset : research_offset + research_limit]
    elif research_offset > 0:
        report_candidates = candidates[research_offset:]
    else:
        report_candidates = candidates
    report_path = out or store.default_acceptance_followup_report_path()
    generated_research: Path | None = None
    if report_candidates and research is None:
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
            offset=research_offset,
            limit=research_limit,
            delay_ms=delay_ms,
        )
        research = generated_research
    artifact = read_model(research, AcceptedResearchArtifact) if research else None
    report = build_draft_report(
        report_candidates, artifact, strategy, str(research) if research is not None else None
    )
    add_lead_review_context_to_draft_report(report, store.load_lead_ledger())
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_draft_markdown(report))
    review_path = review_out or report_path.with_suffix(".review.json")
    review_packet = build_accepted_followup_review_packet(
        report,
        artifact,
        report_path=report_path,
        research_path=research,
    )
    review_markdown_path = write_accepted_followup_review_packet(review_packet, review_path)
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
            "generated_research": str(generated_research) if generated_research else None,
            "review_path": str(review_path),
            "review_markdown_path": str(review_markdown_path),
        },
    )
    suffix = f"; research artifact: {research}" if research else ""
    suffix += f"; review packet: {review_path}"
    if not report.items:
        return (
            f"accepted follow-up drafts: 0 written to {report_path}; "
            "no newly accepted connections need first-message drafts; "
            f"review packet: {review_path}"
        )
    return (
        f"accepted follow-up drafts: {len(report.items)} written to {report_path}{suffix}; "
        "stopped before dry-run/send for review"
    )


def acceptance_research(
    store: Store,
    browser: BrowserClient,
    *,
    input_path: Path,
    out: Path,
    offset: int,
    limit: int,
    delay_ms: int,
) -> str:
    candidates = load_accepted_draft_candidates(input_path)
    artifact, path = browser.research_accepted_candidates(
        candidates=candidates,
        input_path=input_path,
        out=out,
        offset=offset,
        limit=limit,
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


def acceptance_retry_send_followup(
    store: Store,
    browser: BrowserClient,
    *,
    record_id: str,
    allow_send: bool,
) -> str:
    if not allow_send:
        raise RuntimeError("retry-send-followup requires --allow-send")
    messages = [
        acceptance_send_followup(
            store,
            browser,
            record_id=record_id,
            dry_run=True,
            preview_fill=False,
            allow_send=False,
        )
    ]
    ledger = store.load_acceptance_followup_ledger()
    index = ledger.find_by_id(record_id)
    if index is None:
        raise RuntimeError(f"unknown acceptance follow-up id {record_id!r}")
    record = ledger.drafts[index]
    if record.status != AcceptanceFollowupStatus.DRY_RUN_READY:
        return "\n".join(
            messages
            + [
                "retry-send-followup stopped: dry-run did not make the follow-up ready",
                _render_acceptance_followup_send_table([record]),
            ]
        )
    messages.append(
        acceptance_send_followup(
            store,
            browser,
            record_id=record_id,
            dry_run=False,
            preview_fill=False,
            allow_send=True,
        )
    )
    final_ledger = store.load_acceptance_followup_ledger()
    final_index = final_ledger.find_by_id(record_id)
    final_record = final_ledger.drafts[final_index] if final_index is not None else record
    return "\n".join(messages + ["", _render_acceptance_followup_send_table([final_record])])


def acceptance_send_ready_followups(
    store: Store, browser: BrowserClient, *, limit: int, allow_send: bool
) -> str:
    if not allow_send:
        raise RuntimeError("send-ready-followups requires --allow-send")
    ledger = store.load_acceptance_followup_ledger()
    ready = ledger.ready(limit)
    if not ready:
        return "no accepted follow-ups are ready to send"
    messages = []
    sent_records: list[AcceptanceFollowupRecord] = []
    for record in ready:
        messages.append(
            acceptance_send_followup(
                store,
                browser,
                record_id=record.id,
                dry_run=False,
                preview_fill=False,
                allow_send=True,
            )
        )
        current_ledger = store.load_acceptance_followup_ledger()
        current_index = current_ledger.find_by_id(record.id)
        if current_index is not None:
            sent_records.append(current_ledger.drafts[current_index])
    return "\n".join(messages + ["", _render_acceptance_followup_send_table(sent_records)])


def _render_acceptance_followup_send_table(records: Sequence[AcceptanceFollowupRecord]) -> str:
    if not records:
        return "No accepted follow-ups were processed."
    rows = [
        [
            record.name,
            record.id,
            record.status.value,
            record.sent_at.isoformat() if record.sent_at else "",
        ]
        for record in records
    ]
    headers = ["Name", "ID", "Status", "Sent at"]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def render_row(values: Sequence[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    divider = "-+-".join("-" * width for width in widths)
    return "\n".join(["Accepted follow-up send summary", render_row(headers), divider] + [
        render_row(row) for row in rows
    ])


def acceptance_dry_run_followups(
    store: Store, browser: BrowserClient, *, limit: int, retry_classified: bool = False
) -> str:
    ledger = store.load_acceptance_followup_ledger()
    pending = ledger.needs_dry_run(limit, retry_classified=retry_classified)
    if not pending:
        return "no drafted accepted follow-ups need a dry-run check"
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


def followup_id_for_candidate(source: str, name: str, profile_url: str | None) -> str:
    return acceptance_followup_id(candidate_key(source, name, profile_url))


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
