"""Persistent, review-only relationship radar for accepted LinkedIn connections."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import Field

from .browser import BrowserClient
from .commercial_context import validate_relationship_enrichment_commercial_contract
from .models import (
    BUSINESS_SYSTEMS_WATCHLIST,
    AcceptanceFollowupRecord,
    AcceptanceFollowupStatus,
    AcceptanceLeadListAttempt,
    AcceptanceLeadListSaveResult,
    AcceptanceLeadListStatus,
    AppModel,
    CommercialContextReference,
    CommercialCriterionAssessment,
    CommercialCriterionEvidence,
    GreetingEligibilityStatus,
    RelationshipEnrichmentArtifact,
    RelationshipEnrichmentDecision,
    RelationshipEnrichmentDecisionStatus,
    RelationshipEnrichmentEvidence,
    RelationshipEnrichmentStatus,
    RelationshipPriority,
    RelationshipRole,
    RelationshipSignalType,
    RelationshipWatchlistDecision,
    clean_inline,
    now_utc,
    validate_relationship_enrichment_artifact,
)
from .store import Store, read_model, write_json_atomic


class RelationshipRadarRecord(AppModel):
    candidate_key: str
    followup_id: str
    name: str
    source: str
    profile_url: str | None = None
    sales_nav_profile_url: str | None = None
    company_name: str | None = None
    person_summary: str | None = None
    company_summary: str | None = None
    relationship_role: RelationshipRole | None = None
    priority: RelationshipPriority | None = None
    enrichment_decision_status: RelationshipEnrichmentDecisionStatus
    relationship_enrichment_status: RelationshipEnrichmentStatus = (
        RelationshipEnrichmentStatus.MISSING
    )
    greeting_eligibility_status: GreetingEligibilityStatus | None = None
    signal_type: RelationshipSignalType = RelationshipSignalType.NONE
    visible_signal: str | None = None
    signal_url: str | None = None
    followup_reason: str | None = None
    next_useful_action: str | None = None
    permission_boundary: str = "review_only"
    watchlist_decision: RelationshipWatchlistDecision = (
        RelationshipWatchlistDecision.NEEDS_REVIEW
    )
    watchlist_reason: str | None = None
    watchlist_name: str = BUSINESS_SYSTEMS_WATCHLIST
    watchlist_status: AcceptanceLeadListStatus | None = None
    watchlist_saved_at: datetime | None = None
    watchlist_attempts: list[AcceptanceLeadListAttempt] = Field(default_factory=list)
    proposed_message: str | None = None
    evidence_urls: list[str] = Field(default_factory=list)
    research_evidence: list[RelationshipEnrichmentEvidence] = Field(default_factory=list)
    commercial_context: CommercialContextReference | None = None
    criterion_evidence: list[CommercialCriterionEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    accepted_at: datetime
    enriched_at: datetime | None = None
    browser_investigated_at: datetime | None = None
    browser_investigation_status: RelationshipEnrichmentDecisionStatus | None = None
    updated_at: datetime = Field(default_factory=now_utc)
    last_useful_interaction: str | None = None
    manual_notes: list[str] = Field(default_factory=list)
    sales_nav_list_name: str | None = None
    sales_nav_list_status: AcceptanceLeadListStatus | None = None
    sales_nav_list_saved_at: datetime | None = None
    first_message_status: AcceptanceFollowupStatus | None = None
    first_message_sent_at: datetime | None = None
    original_connection_approved_at: datetime | None = None
    original_connection_approval_reason: str | None = None

    @property
    def review_state(self) -> str:
        if self.permission_boundary != "review_only":
            return "blocked"
        if self.relationship_enrichment_status in {
            RelationshipEnrichmentStatus.MISSING,
            RelationshipEnrichmentStatus.STALE,
        }:
            return "needs_enrichment"
        if self.relationship_enrichment_status == RelationshipEnrichmentStatus.NEEDS_REVIEW:
            return "needs_review"
        if self.relationship_enrichment_status == RelationshipEnrichmentStatus.NOT_APPLICABLE:
            return "paused"
        if self.relationship_role in {
            RelationshipRole.HIRING_RECRUITER,
            RelationshipRole.OTHER,
            None,
        }:
            return "paused"
        if not self.followup_reason or not self.next_useful_action:
            return "needs_review"
        return "active"


class RelationshipRadarLedger(AppModel):
    generated_at: datetime = Field(default_factory=now_utc)
    records: list[RelationshipRadarRecord] = Field(default_factory=list)


def update_relationship_radar(
    store: Store,
    *,
    enrichment: Path,
    out: Path | None,
    markdown_out: Path | None,
) -> str:
    artifact = read_model(enrichment, RelationshipEnrichmentArtifact)
    validate_relationship_enrichment_commercial_contract(artifact)
    validate_relationship_enrichment_artifact(artifact)
    ledger_path = out or (store.dir / "relationship-radar" / "ledger.json")
    if ledger_path.exists():
        ledger = read_model(ledger_path, RelationshipRadarLedger)
    else:
        ledger = RelationshipRadarLedger()

    existing = {record.candidate_key: record for record in ledger.records}
    followup_ledger = store.load_acceptance_followup_ledger()
    followups = {record.key: record for record in followup_ledger.drafts}
    for decision in artifact.decisions:
        previous = existing.get(decision.candidate_key)
        record = _record_from_decision(
            decision,
            artifact.generated_at,
            previous,
            followups.get(decision.candidate_key),
        )
        existing[record.candidate_key] = record
        followup = followups.get(record.candidate_key)
        if followup is not None:
            followup.relationship_enrichment_status = record.relationship_enrichment_status
            followup.relationship_enriched_at = record.enriched_at
            followup.updated_at = artifact.generated_at

    store.save_acceptance_followup_ledger(followup_ledger)

    ledger = RelationshipRadarLedger(records=sorted(existing.values(), key=_record_sort_key))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ledger_path, ledger.model_dump(mode="json", by_alias=False))
    report_path = markdown_out or ledger_path.with_suffix(".md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_relationship_radar_markdown(ledger), encoding="utf-8")

    counts: dict[str, int] = {}
    for record in ledger.records:
        counts[record.review_state] = counts.get(record.review_state, 0) + 1
    store.append_acceptance_event(
        "update-relationship-radar",
        {
            "enrichment": str(enrichment),
            "ledger_path": str(ledger_path),
            "report_path": str(report_path),
            "updated": len(artifact.decisions),
            "records": len(ledger.records),
            "counts": counts,
            "permission_boundary": "review_only",
        },
    )
    return (
        f"relationship radar updated: {len(artifact.decisions)} enriched, "
        f"{len(ledger.records)} total; active={counts.get('active', 0)}, "
        f"needs_enrichment={counts.get('needs_enrichment', 0)}, "
        f"needs_review={counts.get('needs_review', 0)}, "
        f"paused={counts.get('paused', 0)}; ledger: {ledger_path}; "
        f"report: {report_path}; stopped before any LinkedIn action"
    )


def apply_browser_investigation(
    store: Store,
    *,
    queue: Path,
    enrichment: Path,
    out: Path | None,
    markdown_out: Path | None,
) -> str:
    """Apply one complete, queue-bound, read-only browser investigation batch."""

    from .models import RelationshipEnrichmentQueue

    packet = read_model(queue, RelationshipEnrichmentQueue)
    artifact = read_model(enrichment, RelationshipEnrichmentArtifact)
    if artifact.source_path != str(queue):
        raise ValueError(
            "browser investigation artifact source_path must exactly match the queue path"
        )
    queued = {item.candidate_key: item for item in packet.items}
    decided = {decision.candidate_key: decision for decision in artifact.decisions}
    if len(decided) != len(artifact.decisions):
        raise ValueError("browser investigation artifact contains duplicate candidate keys")
    if set(decided) != set(queued):
        missing = sorted(set(queued) - set(decided))
        unexpected = sorted(set(decided) - set(queued))
        raise ValueError(
            "browser investigation must cover the exact exported queue; "
            f"missing={missing}; unexpected={unexpected}"
        )
    for key, decision in decided.items():
        item = queued[key]
        if (
            decision.followup_id != item.followup_id
            or decision.candidate != item.candidate
            or decision.permission_boundary != "review_only"
        ):
            raise ValueError(f"browser investigation identity mismatch for {key}")

    summary = update_relationship_radar(
        store,
        enrichment=enrichment,
        out=out,
        markdown_out=markdown_out,
    )
    ledger_path = out or (store.dir / "relationship-radar" / "ledger.json")
    ledger = read_model(ledger_path, RelationshipRadarLedger)
    for record in ledger.records:
        applied_decision = decided.get(record.candidate_key)
        if applied_decision is None:
            continue
        record.browser_investigated_at = artifact.generated_at
        record.browser_investigation_status = applied_decision.status
        record.updated_at = artifact.generated_at
    ledger.generated_at = artifact.generated_at
    write_json_atomic(ledger_path, ledger.model_dump(mode="json", by_alias=False))
    report_path = markdown_out or ledger_path.with_suffix(".md")
    report_path.write_text(render_relationship_radar_markdown(ledger), encoding="utf-8")
    store.append_acceptance_event(
        "apply-browser-investigation",
        {
            "queue": str(queue),
            "enrichment": str(enrichment),
            "records": len(artifact.decisions),
            "permission_boundary": "review_only",
        },
    )
    return f"browser investigation applied: {len(artifact.decisions)} record(s); {summary}"


def sync_relationship_radar_actions(
    store: Store,
    *,
    out: Path | None,
    markdown_out: Path | None,
) -> str:
    ledger_path = out or (store.dir / "relationship-radar" / "ledger.json")
    ledger = (
        read_model(ledger_path, RelationshipRadarLedger)
        if ledger_path.exists()
        else RelationshipRadarLedger()
    )
    followups = {record.key: record for record in store.load_acceptance_followup_ledger().drafts}
    existing_keys = {record.candidate_key for record in ledger.records}
    synced = 0
    records: list[RelationshipRadarRecord] = []
    for record in ledger.records:
        watchlist_decision, watchlist_reason = _watchlist_recommendation_values(
            status=record.enrichment_decision_status,
            relationship_role=record.relationship_role,
            priority=record.priority,
            criterion_evidence=record.criterion_evidence,
        )
        watchlist_status = record.watchlist_status
        if watchlist_decision == RelationshipWatchlistDecision.RECOMMENDED:
            watchlist_status = watchlist_status or AcceptanceLeadListStatus.PENDING
        elif watchlist_status == AcceptanceLeadListStatus.PENDING:
            watchlist_status = None
        updates: dict[str, object] = {
            "watchlist_decision": watchlist_decision,
            "watchlist_reason": watchlist_reason,
            "watchlist_name": BUSINESS_SYSTEMS_WATCHLIST,
            "watchlist_status": watchlist_status,
        }
        followup = followups.get(record.candidate_key)
        if followup is not None:
            updates.update(_action_state(followup))
            synced += 1
        records.append(record.model_copy(update=updates))
    for candidate_key, followup in followups.items():
        if candidate_key in existing_keys:
            continue
        if followup.greeting_eligibility_status != GreetingEligibilityStatus.ELIGIBLE:
            continue
        records.append(_record_from_followup(followup))
        synced += 1
    updated = RelationshipRadarLedger(records=sorted(records, key=_record_sort_key))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(ledger_path, updated.model_dump(mode="json", by_alias=False))
    report_path = markdown_out or ledger_path.with_suffix(".md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_relationship_radar_markdown(updated), encoding="utf-8")
    store.append_acceptance_event(
        "sync-relationship-radar-actions",
        {
            "ledger_path": str(ledger_path),
            "report_path": str(report_path),
            "synced": synced,
            "records": len(updated.records),
        },
    )
    return (
        f"relationship radar action state synced: {synced}/{len(updated.records)} records; "
        f"ledger: {ledger_path}; report: {report_path}"
    )


def save_recommended_watchlist_leads(
    store: Store,
    browser: BrowserClient,
    *,
    limit: int,
    allow_save: bool,
) -> str:
    """Save source-backed buyer recommendations to the exact narrow watchlist."""

    if limit <= 0:
        raise ValueError("limit must be > 0")
    if not allow_save:
        raise RuntimeError("watchlist saves require --allow-save")
    ledger_path = store.dir / "relationship-radar" / "ledger.json"
    ledger = read_model(ledger_path, RelationshipRadarLedger)
    followups = {record.key: record for record in store.load_acceptance_followup_ledger().drafts}
    pending = [
        record
        for record in ledger.records
        if record.watchlist_decision == RelationshipWatchlistDecision.RECOMMENDED
        and record.watchlist_status
        not in {AcceptanceLeadListStatus.SAVED, AcceptanceLeadListStatus.ALREADY_SAVED}
    ][:limit]
    if not pending:
        return f"watchlist save idle: no pending recommendations for {BUSINESS_SYSTEMS_WATCHLIST!r}"

    messages: list[str] = []
    saved = 0
    for record in pending:
        followup = followups.get(record.candidate_key)
        if followup is None:
            raise RuntimeError(f"watchlist candidate {record.followup_id} has no welcome record")
        if not record.sales_nav_profile_url:
            raise ValueError(
                f"watchlist candidate {record.followup_id} has no Sales Navigator profile URL"
            )
        browser_record = followup.model_copy(
            update={
                "sales_nav_list_name": BUSINESS_SYSTEMS_WATCHLIST,
                "sales_nav_list_status": record.watchlist_status,
            }
        )
        result, out_path = browser.save_acceptance_lead_to_list(
            browser_record,
            allow_save=True,
        )
        _validate_watchlist_result(record, result)
        current = now_utc()
        record.watchlist_attempts.append(
            AcceptanceLeadListAttempt(
                at=current,
                status=result.status,
                list_name=result.list_name,
                result_url=result.url,
                reason=result.reason,
                out_path=out_path,
            )
        )
        record.watchlist_status = result.status
        record.updated_at = current
        if result.status in {
            AcceptanceLeadListStatus.SAVED,
            AcceptanceLeadListStatus.ALREADY_SAVED,
        }:
            record.watchlist_saved_at = current
            saved += 1
        write_json_atomic(ledger_path, ledger.model_dump(mode="json", by_alias=False))
        ledger_path.with_suffix(".md").write_text(
            render_relationship_radar_markdown(ledger), encoding="utf-8"
        )
        messages.append(
            f"watchlist={BUSINESS_SYSTEMS_WATCHLIST!r} candidate={record.followup_id} "
            f"status={result.status.value} out={out_path}"
        )
        if result.status not in {
            AcceptanceLeadListStatus.SAVED,
            AcceptanceLeadListStatus.ALREADY_SAVED,
        }:
            if _is_skippable_watchlist_selection_ambiguity(result):
                messages.append("watchlist save skipped ambiguous candidate; continuing")
                continue
            messages.append("watchlist save stopped before the next candidate")
            break

    store.append_acceptance_event(
        "save-relationship-watchlist-leads",
        {
            "list": BUSINESS_SYSTEMS_WATCHLIST,
            "selected": len(pending),
            "saved": saved,
            "limit": limit,
        },
    )
    return "\n".join(messages)


def _is_skippable_watchlist_selection_ambiguity(
    result: AcceptanceLeadListSaveResult,
) -> bool:
    return (
        result.status == AcceptanceLeadListStatus.BLOCKED
        and result.reason is not None
        and result.reason.startswith("target lead list selection was ambiguous:")
    )


def _validate_watchlist_result(
    record: RelationshipRadarRecord,
    result: AcceptanceLeadListSaveResult,
) -> None:
    if result.list_name != BUSINESS_SYSTEMS_WATCHLIST:
        raise RuntimeError(
            f"watchlist controller returned {result.list_name!r}; "
            f"expected {BUSINESS_SYSTEMS_WATCHLIST!r}"
        )
    if result.candidate is None or (
        result.candidate.id != record.followup_id
        or result.candidate.key != record.candidate_key
        or result.candidate.name != record.name
        or result.candidate.source != record.source
    ):
        raise RuntimeError("watchlist controller result did not match the requested candidate")


def _record_from_decision(
    decision: RelationshipEnrichmentDecision,
    enriched_at: datetime,
    previous: RelationshipRadarRecord | None,
    followup: AcceptanceFollowupRecord | None,
) -> RelationshipRadarRecord:
    candidate = decision.candidate
    if decision.permission_boundary != "review_only":
        raise ValueError(f"relationship radar requires review_only permission for {candidate.name}")
    warnings = list(decision.warnings)
    if decision.relationship_role is None:
        warnings.append("Relationship role was not classified; Hanif review is required.")
    if decision.signal_type == RelationshipSignalType.LINKEDIN_POST and not decision.signal_url:
        warnings.append("LinkedIn post signal is missing its exact post URL.")
    action_state = (
        _action_state(followup)
        if followup
        else {
            "sales_nav_list_name": previous.sales_nav_list_name if previous else None,
            "sales_nav_list_status": previous.sales_nav_list_status if previous else None,
            "sales_nav_list_saved_at": previous.sales_nav_list_saved_at if previous else None,
            "first_message_status": previous.first_message_status if previous else None,
            "first_message_sent_at": previous.first_message_sent_at if previous else None,
            "greeting_eligibility_status": (
                previous.greeting_eligibility_status if previous else None
            ),
            "original_connection_approved_at": (
                previous.original_connection_approved_at if previous else None
            ),
            "original_connection_approval_reason": (
                previous.original_connection_approval_reason if previous else None
            ),
            "proposed_message": previous.proposed_message if previous else None,
        }
    )
    if decision.status == RelationshipEnrichmentDecisionStatus.ENRICHED:
        enrichment_status = RelationshipEnrichmentStatus.CURRENT
    elif decision.status == RelationshipEnrichmentDecisionStatus.NEEDS_REVIEW:
        enrichment_status = RelationshipEnrichmentStatus.NEEDS_REVIEW
    elif decision.relationship_role in {
        RelationshipRole.HIRING_RECRUITER,
        RelationshipRole.OTHER,
        None,
    }:
        enrichment_status = RelationshipEnrichmentStatus.NOT_APPLICABLE
    else:
        enrichment_status = RelationshipEnrichmentStatus.NEEDS_REVIEW
    watchlist_decision, watchlist_reason = _watchlist_recommendation(decision)
    previous_watchlist_status = previous.watchlist_status if previous else None
    return RelationshipRadarRecord(
        candidate_key=decision.candidate_key,
        followup_id=decision.followup_id,
        name=candidate.name,
        source=candidate.source,
        profile_url=candidate.profile_url,
        sales_nav_profile_url=candidate.sales_nav_profile_url,
        company_name=decision.company_name,
        person_summary=decision.person_summary,
        company_summary=decision.company_summary,
        relationship_role=decision.relationship_role,
        priority=decision.priority,
        enrichment_decision_status=decision.status,
        relationship_enrichment_status=enrichment_status,
        signal_type=decision.signal_type,
        visible_signal=decision.visible_signal,
        signal_url=decision.signal_url,
        followup_reason=decision.followup_reason,
        next_useful_action=decision.next_useful_action,
        permission_boundary=decision.permission_boundary,
        watchlist_decision=watchlist_decision,
        watchlist_reason=watchlist_reason,
        watchlist_name=BUSINESS_SYSTEMS_WATCHLIST,
        watchlist_status=(
            previous_watchlist_status
            if previous_watchlist_status is not None
            else AcceptanceLeadListStatus.PENDING
            if watchlist_decision == RelationshipWatchlistDecision.RECOMMENDED
            else None
        ),
        watchlist_saved_at=(previous.watchlist_saved_at if previous else None),
        watchlist_attempts=list(previous.watchlist_attempts) if previous else [],
        evidence_urls=list(decision.evidence_urls),
        research_evidence=list(decision.research_evidence),
        commercial_context=decision.commercial_context,
        criterion_evidence=list(decision.criterion_evidence),
        unknowns=list(decision.unknowns),
        warnings=warnings,
        accepted_at=candidate.accepted_at,
        enriched_at=enriched_at,
        browser_investigated_at=(previous.browser_investigated_at if previous else None),
        browser_investigation_status=(
            previous.browser_investigation_status if previous else None
        ),
        last_useful_interaction=(previous.last_useful_interaction if previous else None),
        manual_notes=list(previous.manual_notes) if previous else [],
        **action_state,
    )


def _watchlist_recommendation(
    decision: RelationshipEnrichmentDecision,
) -> tuple[RelationshipWatchlistDecision, str]:
    return _watchlist_recommendation_values(
        status=decision.status,
        relationship_role=decision.relationship_role,
        priority=decision.priority,
        criterion_evidence=decision.criterion_evidence,
    )


def _watchlist_recommendation_values(
    *,
    status: RelationshipEnrichmentDecisionStatus,
    relationship_role: RelationshipRole | None,
    priority: RelationshipPriority | None,
    criterion_evidence: list[CommercialCriterionEvidence],
) -> tuple[RelationshipWatchlistDecision, str]:
    if status == RelationshipEnrichmentDecisionStatus.NEEDS_REVIEW:
        return (
            RelationshipWatchlistDecision.NEEDS_REVIEW,
            "Research is incomplete or ambiguous; do not save to the buyer watchlist.",
        )
    if status != RelationshipEnrichmentDecisionStatus.ENRICHED:
        return (
            RelationshipWatchlistDecision.NOT_RECOMMENDED,
            "Research did not support an active consulting-buyer relationship.",
        )
    if relationship_role != RelationshipRole.BUYER:
        return (
            RelationshipWatchlistDecision.NOT_RECOMMENDED,
            "The source-backed role is not a direct buyer for Hanif's services.",
        )
    if priority == RelationshipPriority.PAUSE:
        return (
            RelationshipWatchlistDecision.NOT_RECOMMENDED,
            "The relationship is explicitly paused.",
        )
    criteria = {item.criterion_id: item.assessment for item in criterion_evidence}
    if criteria.get("engagement-model-fit") == CommercialCriterionAssessment.NOT_MATCHED:
        return (
            RelationshipWatchlistDecision.NOT_RECOMMENDED,
            "Source evidence shows that the active engagement model does not fit.",
        )
    supporting_ids = {
        "recurring-operational-work",
        "concrete-workflow-problem",
        "material-consequence",
        "engagement-model-fit",
    }
    matched = sorted(
        criterion_id
        for criterion_id in supporting_ids
        if criteria.get(criterion_id) == CommercialCriterionAssessment.MATCHED
    )
    if not matched:
        return (
            RelationshipWatchlistDecision.NEEDS_REVIEW,
            "The person may be a buyer, but public evidence does not yet establish "
            "operating relevance worth monitoring.",
        )
    return (
        RelationshipWatchlistDecision.RECOMMENDED,
        "Source-backed buyer and operating relevance: " + ", ".join(matched) + ".",
    )


def _action_state(record: AcceptanceFollowupRecord) -> dict[str, object]:
    return {
        "greeting_eligibility_status": record.greeting_eligibility_status,
        "original_connection_approved_at": record.original_connection_approved_at,
        "original_connection_approval_reason": (record.original_connection_approval_reason),
        "sales_nav_list_name": record.sales_nav_list_name,
        "sales_nav_list_status": record.sales_nav_list_status,
        "sales_nav_list_saved_at": record.sales_nav_list_saved_at,
        "first_message_status": record.status,
        "first_message_sent_at": record.sent_at,
        "proposed_message": record.draft,
    }


def _record_from_followup(record: AcceptanceFollowupRecord) -> RelationshipRadarRecord:
    return RelationshipRadarRecord(
        candidate_key=record.key,
        followup_id=record.id,
        name=record.name,
        source=record.source,
        profile_url=record.profile_url,
        sales_nav_profile_url=record.sales_nav_profile_url,
        relationship_role=record.relationship_role,
        priority=record.relationship_priority,
        enrichment_decision_status=RelationshipEnrichmentDecisionStatus.NEEDS_REVIEW,
        relationship_enrichment_status=(
            record.relationship_enrichment_status or RelationshipEnrichmentStatus.MISSING
        ),
        greeting_eligibility_status=record.greeting_eligibility_status,
        accepted_at=record.accepted_at,
        enriched_at=record.relationship_enriched_at,
        proposed_message=record.draft,
        original_connection_approved_at=record.original_connection_approved_at,
        original_connection_approval_reason=record.original_connection_approval_reason,
        sales_nav_list_name=record.sales_nav_list_name,
        sales_nav_list_status=record.sales_nav_list_status,
        sales_nav_list_saved_at=record.sales_nav_list_saved_at,
        first_message_status=record.status,
        first_message_sent_at=record.sent_at,
        warnings=["Relationship enrichment has not run yet."],
    )


def _record_sort_key(record: RelationshipRadarRecord) -> tuple[int, int, float, str]:
    state_order = {
        "active": 0,
        "needs_enrichment": 1,
        "needs_review": 2,
        "paused": 3,
        "blocked": 4,
    }
    priority_order = {
        RelationshipPriority.HIGH: 0,
        RelationshipPriority.NORMAL: 1,
        RelationshipPriority.LOW: 2,
        RelationshipPriority.PAUSE: 3,
        None: 4,
    }
    return (
        state_order.get(record.review_state, 4),
        priority_order[record.priority],
        -record.updated_at.timestamp(),
        record.name.casefold(),
    )


def render_relationship_radar_markdown(ledger: RelationshipRadarLedger) -> str:
    sections = [
        ("Buyer relationships", RelationshipRole.BUYER),
        ("Referral partners", RelationshipRole.REFERRAL_PARTNER),
        ("Hiring and recruiter sources paused", RelationshipRole.HIRING_RECRUITER),
        ("Other or unclassified", RelationshipRole.OTHER),
        ("Needs role review", None),
    ]
    active = sum(record.review_state == "active" for record in ledger.records)
    needs_enrichment = sum(record.review_state == "needs_enrichment" for record in ledger.records)
    needs_review = sum(record.review_state == "needs_review" for record in ledger.records)
    paused = sum(record.review_state == "paused" for record in ledger.records)
    watchlist_recommended = sum(
        record.watchlist_decision == RelationshipWatchlistDecision.RECOMMENDED
        for record in ledger.records
    )
    watchlist_saved = sum(
        record.watchlist_status
        in {AcceptanceLeadListStatus.SAVED, AcceptanceLeadListStatus.ALREADY_SAVED}
        for record in ledger.records
    )
    lines = [
        "# LinkedIn Relationship Radar",
        "",
        f"- Generated: `{ledger.generated_at.isoformat()}`",
        f"- Records: `{len(ledger.records)}`",
        f"- Active: `{active}`",
        f"- Needs enrichment: `{needs_enrichment}`",
        f"- Needs review: `{needs_review}`",
        f"- Paused: `{paused}`",
        f"- Watchlist recommended: `{watchlist_recommended}`",
        f"- Watchlist saved: `{watchlist_saved}`",
        f"- Watchlist: `{BUSINESS_SYSTEMS_WATCHLIST}`",
        "- Permission boundary: `review_only`",
        "",
        "This report tracks greeting action state separately from relationship enrichment. "
        "It may recommend source-backed buyers for the narrow Sales Navigator watchlist. "
        "Only the separate guarded watchlist command may save those exact recommendations. "
        "This report does not authorize connection requests, comments, or messages.",
    ]
    for heading, role in sections:
        records = [record for record in ledger.records if record.relationship_role == role]
        if not records:
            continue
        lines.extend(["", f"## {heading}"])
        for record in records:
            lines.extend(_render_record(record))
    return "\n".join(lines) + "\n"


def _render_record(record: RelationshipRadarRecord) -> list[str]:
    greeting_eligibility = (
        record.greeting_eligibility_status.value if record.greeting_eligibility_status else ""
    )
    lines = [
        "",
        f"### {record.name}",
        f"- State: `{record.review_state}`",
        f"- Priority: `{record.priority.value if record.priority else ''}`",
        f"- Source: `{record.source}`",
        f"- Company: {clean_inline(record.company_name)}",
        f"- Accepted: `{record.accepted_at.isoformat()}`",
        f"- Profile: {record.profile_url or record.sales_nav_profile_url or ''}",
        f"- Greeting eligibility: `{greeting_eligibility}`",
        f"- Relationship enrichment: `{record.relationship_enrichment_status.value}`",
        f"- Watchlist decision: `{record.watchlist_decision.value}`",
        f"- Watchlist reason: {clean_inline(record.watchlist_reason)}",
        f"- Watchlist: `{record.watchlist_name}`",
        "- Watchlist status: `"
        + (record.watchlist_status.value if record.watchlist_status else "")
        + "`",
    ]
    if record.enriched_at:
        lines.append(f"- Enriched: `{record.enriched_at.isoformat()}`")
    if record.browser_investigated_at:
        lines.append(
            f"- Browser investigated: `{record.browser_investigated_at.isoformat()}`"
        )
        lines.append(
            "- Browser investigation result: `"
            + (
                record.browser_investigation_status.value
                if record.browser_investigation_status
                else ""
            )
            + "`"
        )
    if record.commercial_context:
        context = record.commercial_context
        lines.extend(
            [
                f"- ICP profile: `{context.icp_profile_id}` ({context.icp_source_path})",
                f"- Offers profile: `{context.offers_profile_id}` "
                f"({context.offers_source_path})",
                f"- Evaluated offer: `{context.offer_id}`",
            ]
        )
    if record.original_connection_approved_at:
        approved_at = record.original_connection_approved_at.isoformat()
        lines.append(f"- Original connection approved: `{approved_at}`")
    if record.original_connection_approval_reason:
        lines.append(
            "- Original connection approval: "
            + clean_inline(record.original_connection_approval_reason)
        )
    if record.sales_nav_list_name:
        lines.append(f"- Sales Navigator list: `{record.sales_nav_list_name}`")
        lines.append(
            "- Sales Navigator list status: `"
            + (record.sales_nav_list_status.value if record.sales_nav_list_status else "")
            + "`"
        )
    if record.first_message_status:
        lines.append(f"- First message status: `{record.first_message_status.value}`")
    if record.first_message_sent_at:
        lines.append(f"- First message sent: `{record.first_message_sent_at.isoformat()}`")
    if record.visible_signal:
        lines.append(
            f"- Visible signal (`{record.signal_type.value}`): "
            + clean_inline(record.visible_signal)
        )
    if record.signal_url:
        lines.append("- Signal URL: " + record.signal_url)
    if record.followup_reason:
        lines.append("- Follow-up reason: " + clean_inline(record.followup_reason))
    if record.next_useful_action:
        lines.append("- Next useful action: " + clean_inline(record.next_useful_action))
    lines.append(f"- Permission boundary: `{record.permission_boundary}`")
    if record.evidence_urls:
        lines.append("- Evidence URLs:")
        lines.extend("  - " + url for url in record.evidence_urls)
    if record.criterion_evidence:
        lines.append("- Commercial criteria:")
        for criterion in record.criterion_evidence:
            evidence_ids = ", ".join(criterion.evidence_ids) or "none"
            lines.append(
                f"  - `{criterion.criterion_id}`: `{criterion.assessment.value}`; "
                f"evidence `{evidence_ids}`; {clean_inline(criterion.explanation)}"
            )
    if record.unknowns:
        lines.append("- Unknowns:")
        lines.extend("  - " + clean_inline(value) for value in record.unknowns)
    if record.warnings:
        lines.append("- Warnings:")
        lines.extend("  - " + clean_inline(warning) for warning in record.warnings)
    if record.proposed_message:
        lines.extend(["", "Approved greeting:", ""])
        lines.extend("> " + line if line else ">" for line in record.proposed_message.splitlines())
    return lines
