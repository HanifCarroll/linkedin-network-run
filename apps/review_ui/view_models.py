"""Read models for the local LinkedIn tools review UI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from apps.network_automation.models import (
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    CandidateObservation,
    CandidateReservoir,
    PendingCandidateObservation,
    PendingCleanupRun,
    Run,
)
from apps.network_automation.reports import (
    format_delta,
    format_option,
    format_pending_threshold,
)
from apps.network_automation.store import Store as NetworkStore
from apps.opportunity_intel.post_discovery import discover_posts_from_registry
from apps.opportunity_intel.sources import load_source_registry
from apps.opportunity_intel.store import OpportunityStore, post_candidate_key
from apps.recruiter_agency_outreach.dashboard import (
    AgencyPoolLeadCounts,
    agency_pool_lead_counts_by_account,
    bucket_for_lead,
    build_agency_pool_account_diagnosis,
    build_agency_pool_next_action,
    build_dashboard_report,
)
from apps.recruiter_agency_outreach.models import (
    Lead,
    LeadStatus,
    MessageStatus,
    OutreachState,
)
from apps.recruiter_agency_outreach.storage import Store as RecruiterStore
from packages.linkedin_browser.config import chrome_profile_from_env, chrome_profile_storage_dir


@dataclass(frozen=True)
class IntegrationNotice:
    area: str
    owner: str
    dependency: str


@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    detail: str
    state: str = "neutral"


@dataclass(frozen=True)
class OpportunitySourceRow:
    source_id: str
    source_type: str
    label: str
    enabled: bool
    priority: str
    hypothesis: str
    target_needs: str
    latest_yield: str
    notes: str


@dataclass(frozen=True)
class PostQueueRow:
    post_id: str
    post_url: str
    source_id: str
    author: str
    priority_reasons: str
    extraction_status: str
    artifact_path: str


@dataclass(frozen=True)
class ExtractionRunRow:
    run_id: str
    post_url: str
    comments_found: int
    failures: int
    screenshot_path: str
    raw_artifact_path: str
    retry_recommendation: str


@dataclass(frozen=True)
class RankedCommentRow:
    comment_id: str
    commenter: str
    headline: str
    profile_url: str
    comment_text: str
    post_url: str
    evidence_quote: str
    fit_reasons: str
    reject_reasons: str
    level: str
    signal_score: int
    source_id: str
    human_label: str


@dataclass(frozen=True)
class ExperimentRow:
    source_id: str
    valid_comments: int
    qualified_comments: int
    qualified_per_100: float
    direct_buyer_rate: str
    noise_rate: str
    warm_hot_rate: str
    source_decision: str


@dataclass(frozen=True)
class CalibrationRow:
    item_id: str
    machine_label: str
    human_label: str
    disagreement: bool
    evidence: str


@dataclass(frozen=True)
class NetworkRunStatus:
    run_id: str
    phase: str
    plan_next_action: str
    audit_status: str
    reconciliation_status: str
    safety_state: str


@dataclass(frozen=True)
class NetworkCandidateRow:
    candidate_id: str
    name: str
    source: str
    status: str
    next_step: str


@dataclass(frozen=True)
class AcceptanceDraftRow:
    draft_id: str
    person: str
    draft_status: str
    dry_run_status: str
    send_history: str
    profile_url: str
    draft_body: str


@dataclass(frozen=True)
class PendingCleanupRow:
    invite_id: str
    name: str
    age: str
    threshold: str
    dry_run_status: str
    withdraw_history: str


@dataclass(frozen=True)
class RecruiterRunSummary:
    run_id: str
    started_at: str
    result: str
    next_recommendation: str
    blockers: str


@dataclass(frozen=True)
class AgencyAccountRow:
    account_id: str
    agency: str
    status: str
    contactability: str
    blocker: str


@dataclass(frozen=True)
class RecruiterLeadRow:
    lead_id: str
    name: str
    lead_type: str
    draft_status: str
    messageability_status: str
    send_readiness: str
    blocker: str
    profile_url: str
    draft_subject: str
    draft_body: str


@dataclass(frozen=True)
class BrowserSessionRow:
    profile_name: str
    session_state: str
    current_session: str
    safety_warning: str


@dataclass(frozen=True)
class BrowserArtifactRow:
    artifact_id: str
    app: str
    kind: str
    path: str
    status: str
    retryable_error: str


@dataclass(frozen=True)
class ReviewSnapshot:
    notices: tuple[IntegrationNotice, ...]
    system_metrics: tuple[Metric, ...]
    opportunity_metrics: tuple[Metric, ...]
    opportunity_sources: tuple[OpportunitySourceRow, ...]
    post_queue: tuple[PostQueueRow, ...]
    extraction_runs: tuple[ExtractionRunRow, ...]
    ranked_comments: tuple[RankedCommentRow, ...]
    experiments: tuple[ExperimentRow, ...]
    calibration_queue: tuple[CalibrationRow, ...]
    network_status: NetworkRunStatus
    network_candidates: tuple[NetworkCandidateRow, ...]
    acceptance_drafts: tuple[AcceptanceDraftRow, ...]
    pending_cleanup: tuple[PendingCleanupRow, ...]
    recruiter_summary: RecruiterRunSummary
    agency_accounts: tuple[AgencyAccountRow, ...]
    recruiter_leads: tuple[RecruiterLeadRow, ...]
    browser_sessions: tuple[BrowserSessionRow, ...]
    browser_artifacts: tuple[BrowserArtifactRow, ...]


class ReviewReadModelProvider(Protocol):
    def snapshot(self) -> ReviewSnapshot:
        """Return the current cross-system review snapshot."""


class SQLiteReviewReadModelProvider:
    """Read model provider for the local review UI."""

    def __init__(
        self,
        *,
        store: OpportunityStore | None = None,
        state_dir: str | Path | None = None,
        network_store: NetworkStore | None = None,
        network_state_dir: str | Path | None = None,
        recruiter_store: RecruiterStore | None = None,
        recruiter_state_dir: str | Path | None = None,
    ) -> None:
        self.store = store or OpportunityStore(state_dir)
        self.network_store = network_store or NetworkStore(network_state_dir)
        self.recruiter_store = recruiter_store or RecruiterStore(recruiter_state_dir)

    def snapshot(self) -> ReviewSnapshot:
        opportunity_metrics = self._opportunity_metrics()
        network_run = self._load_network_run()
        network_reservoir = self._load_network_reservoir()
        acceptance_followups = self._load_acceptance_followups()
        pending_cleanup = self._load_pending_cleanup()
        recruiter_state = self._load_recruiter_state()
        return ReviewSnapshot(
            notices=(
                IntegrationNotice(
                    area="Opportunity Intel",
                    owner="SQLite",
                    dependency="Live source, post, extraction, ranking, and review state.",
                ),
                IntegrationNotice(
                    area="Network Automation",
                    owner="JSON state",
                    dependency="Live run, reservoir, acceptance, and pending-cleanup state.",
                ),
                IntegrationNotice(
                    area="Recruiter/Agency/Advisor Outreach",
                    owner="SQLite state",
                    dependency="Live account pool, lead queue, drafts, and send readiness.",
                ),
                IntegrationNotice(
                    area="Browser/Artifacts",
                    owner="SQLite",
                    dependency="Live extraction artifacts and browser setup state.",
                ),
            ),
            system_metrics=(
                Metric(
                    "Opportunity review",
                    "SQLite",
                    f"{opportunity_metrics[0].value} persisted comments",
                    "good",
                ),
                self._network_metric(network_run, acceptance_followups, pending_cleanup),
                self._recruiter_metric(recruiter_state),
                Metric(
                    "Browser artifacts",
                    "SQLite",
                    "Artifacts read from opportunity extraction runs.",
                    "good",
                ),
            ),
            opportunity_metrics=opportunity_metrics,
            opportunity_sources=self._opportunity_sources(),
            post_queue=self._post_queue(),
            extraction_runs=self._extraction_runs(),
            ranked_comments=self._ranked_comments(),
            experiments=self._experiments(),
            calibration_queue=self._calibration_queue(),
            network_status=self._network_status(network_run, network_reservoir),
            network_candidates=self._network_candidates(network_run, network_reservoir),
            acceptance_drafts=self._acceptance_drafts(acceptance_followups),
            pending_cleanup=self._pending_cleanup_rows(pending_cleanup),
            recruiter_summary=self._recruiter_summary(recruiter_state),
            agency_accounts=self._agency_accounts(recruiter_state),
            recruiter_leads=self._recruiter_leads(recruiter_state),
            browser_sessions=self._browser_sessions(),
            browser_artifacts=self._browser_artifacts(),
        )

    def _opportunity_metrics(self) -> tuple[Metric, ...]:
        row = self.store.fetch_all(
            """
            SELECT
              COUNT(c.comment_key) AS valid_comments,
              COALESCE(SUM(CASE WHEN r.rank_level IN ('strong', 'possible') THEN 1 ELSE 0 END), 0)
                AS reviewable_comments,
              COALESCE(SUM(CASE WHEN r.direct_buyer = 1 THEN 1 ELSE 0 END), 0)
                AS direct_buyers,
              COALESCE(SUM(CASE WHEN r.rank_level = 'irrelevant' THEN 1 ELSE 0 END), 0)
                AS rejected_comments
            FROM comments c
            LEFT JOIN rankings r ON r.comment_key = c.comment_key
            """
        )[0]
        valid = int(row["valid_comments"] or 0)
        direct = int(row["direct_buyers"] or 0)
        rejected = int(row["rejected_comments"] or 0)
        reviewable = int(row["reviewable_comments"] or 0)
        direct_rate = f"{(direct / valid * 100):.1f}%" if valid else "0.0%"
        rejected_rate = f"{(rejected / valid * 100):.1f}%" if valid else "0.0%"
        return (
            Metric("Valid comments", str(valid), "Persisted comment rows"),
            Metric("Strong/possible", str(reviewable), "Reviewable buyer-signal comments"),
            Metric("Direct-buyer rate", direct_rate, "Based on persisted rankings", "good"),
            Metric("Rejected rate", rejected_rate, "Irrelevant or excluded comments"),
        )

    def _opportunity_sources(self) -> tuple[OpportunitySourceRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT
              s.source_id, s.source_kind, s.title, s.enabled, s.priority,
              s.description, s.query_ids_json,
              COUNT(c.comment_key) AS valid_comments,
              COALESCE(SUM(CASE WHEN r.rank_level IN ('strong', 'possible') THEN 1 ELSE 0 END), 0)
                AS qualified_comments
            FROM sources s
            LEFT JOIN comments c ON c.source_id = s.source_id
            LEFT JOIN rankings r ON r.comment_key = c.comment_key
            GROUP BY s.source_id
            ORDER BY s.priority DESC, s.source_id ASC
            """
        )
        if not rows:
            return _registry_source_rows()
        return tuple(
            OpportunitySourceRow(
                source_id=str(row["source_id"]),
                source_type=str(row["source_kind"]),
                label=str(row["title"]),
                enabled=bool(row["enabled"]),
                priority=str(row["priority"]),
                hypothesis=str(row["description"]),
                target_needs=", ".join(_json_strings(str(row["query_ids_json"]))),
                latest_yield=(
                    f"{int(row['qualified_comments'] or 0)} strong/possible of "
                    f"{int(row['valid_comments'] or 0)} valid comments"
                ),
                notes="Live SQLite source row",
            )
            for row in rows
        )

    def _post_queue(self) -> tuple[PostQueueRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT post_id, post_url, source_url, source_id, post_author_name, reason,
                   extraction_status, artifact_path
            FROM posts
            ORDER BY updated_at DESC, priority DESC
            """
        )
        merged: list[PostQueueRow] = []
        seen_ids: set[str] = set()
        persisted_urls: set[str] = set()
        for row in rows:
            post_url = str(row["post_url"] or row["source_url"])
            item = PostQueueRow(
                post_id=str(row["post_id"]),
                post_url=post_url,
                source_id=str(row["source_id"]),
                author=str(row["post_author_name"]),
                priority_reasons=str(row["reason"]),
                extraction_status=str(row["extraction_status"]),
                artifact_path=str(row["artifact_path"]),
            )
            merged.append(item)
            seen_ids.add(item.post_id)
            if item.post_url:
                persisted_urls.add(item.post_url)
        for item in _registry_post_rows():
            if item.post_id in seen_ids or (item.post_url and item.post_url in persisted_urls):
                continue
            merged.append(item)
            seen_ids.add(item.post_id)
        return tuple(merged)

    def _extraction_runs(self) -> tuple[ExtractionRunRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT run_id, post_url, comments_found, failures, retry_recommendation
            FROM extraction_runs
            ORDER BY started_at DESC
            """
        )
        return tuple(
            ExtractionRunRow(
                run_id=str(row["run_id"]),
                post_url=str(row["post_url"]),
                comments_found=int(row["comments_found"] or 0),
                failures=int(row["failures"] or 0),
                screenshot_path=self._artifact_path(str(row["run_id"]), "screenshot"),
                raw_artifact_path=self._artifact_path(str(row["run_id"]), "raw_comments"),
                retry_recommendation=str(row["retry_recommendation"]),
            )
            for row in rows
        )

    def _ranked_comments(self) -> tuple[RankedCommentRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT
              c.comment_key, c.commenter_name, c.commenter_headline,
              c.commenter_profile_url, c.comment_text, c.post_url,
              r.evidence_quote, r.fit_reasons_json, r.reject_reasons_json,
              r.rank_level, r.rank_points, c.source_id,
              COALESCE(l.label, 'unlabeled') AS human_label
            FROM comments c
            JOIN rankings r ON r.comment_key = c.comment_key
            LEFT JOIN review_labels l ON l.comment_key = c.comment_key
            ORDER BY r.rank_points DESC, c.updated_at DESC
            """
        )
        return tuple(
            RankedCommentRow(
                comment_id=str(row["comment_key"]),
                commenter=str(row["commenter_name"]),
                headline=str(row["commenter_headline"]),
                profile_url=str(row["commenter_profile_url"]),
                comment_text=str(row["comment_text"]),
                post_url=str(row["post_url"]),
                evidence_quote=str(row["evidence_quote"]),
                fit_reasons="; ".join(_json_strings(str(row["fit_reasons_json"]))),
                reject_reasons="; ".join(_json_strings(str(row["reject_reasons_json"]))),
                level=str(row["rank_level"]),
                signal_score=int(row["rank_points"] or 0),
                source_id=str(row["source_id"]),
                human_label=str(row["human_label"]),
            )
            for row in rows
        )

    def _experiments(self) -> tuple[ExperimentRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT
              c.source_id,
              COUNT(c.comment_key) AS valid_comments,
              COALESCE(SUM(CASE WHEN r.rank_level IN ('strong', 'possible') THEN 1 ELSE 0 END), 0)
                AS qualified_comments,
              COALESCE(SUM(CASE WHEN r.direct_buyer = 1 THEN 1 ELSE 0 END), 0)
                AS direct_buyers,
              COALESCE(SUM(CASE WHEN r.rank_level = 'irrelevant' THEN 1 ELSE 0 END), 0)
                AS rejected_comments
            FROM comments c
            JOIN rankings r ON r.comment_key = c.comment_key
            GROUP BY c.source_id
            ORDER BY qualified_comments DESC, valid_comments DESC
            """
        )
        experiments: list[ExperimentRow] = []
        for row in rows:
            valid = int(row["valid_comments"] or 0)
            qualified = int(row["qualified_comments"] or 0)
            direct = int(row["direct_buyers"] or 0)
            rejected = int(row["rejected_comments"] or 0)
            experiments.append(
                ExperimentRow(
                    source_id=str(row["source_id"]),
                    valid_comments=valid,
                    qualified_comments=qualified,
                    qualified_per_100=(qualified / valid * 100) if valid else 0.0,
                    direct_buyer_rate=f"{(direct / valid * 100):.1f}%" if valid else "0.0%",
                    noise_rate=f"{(rejected / valid * 100):.1f}%" if valid else "0.0%",
                    warm_hot_rate=f"{(qualified / valid * 100):.1f}%" if valid else "0.0%",
                    source_decision="Review persisted comments",
                )
            )
        return tuple(experiments)

    def _calibration_queue(self) -> tuple[CalibrationRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT
              c.comment_key, r.rank_level, COALESCE(l.label, 'unlabeled') AS human_label,
              r.evidence_quote
            FROM comments c
            JOIN rankings r ON r.comment_key = c.comment_key
            LEFT JOIN review_labels l ON l.comment_key = c.comment_key
            ORDER BY
              CASE WHEN l.label IS NULL THEN 0 ELSE 1 END ASC,
              r.rank_points DESC
            """
        )
        return tuple(
            CalibrationRow(
                item_id=str(row["comment_key"]),
                machine_label=str(row["rank_level"]),
                human_label=str(row["human_label"]),
                disagreement=(
                    str(row["human_label"]) != "unlabeled"
                    and str(row["human_label"]) != str(row["rank_level"])
                ),
                evidence=str(row["evidence_quote"]),
            )
            for row in rows
        )

    def _load_network_run(self) -> Run | None:
        try:
            return self.network_store.load_run()
        except (OSError, ValueError):
            return None

    def _load_network_reservoir(self) -> CandidateReservoir:
        try:
            return self.network_store.load_reservoir()
        except (OSError, ValueError):
            return CandidateReservoir()

    def _load_acceptance_followups(self) -> AcceptanceFollowupLedger:
        try:
            return self.network_store.load_acceptance_followup_ledger()
        except (OSError, ValueError):
            return AcceptanceFollowupLedger()

    def _load_pending_cleanup(self) -> PendingCleanupRun | None:
        try:
            return self.network_store.load_pending()
        except (OSError, ValueError):
            return None

    def _load_recruiter_state(self) -> OutreachState:
        try:
            return self.recruiter_store.load()
        except (OSError, ValueError):
            return OutreachState()

    def _network_metric(
        self,
        run: Run | None,
        followups: AcceptanceFollowupLedger,
        pending_cleanup: PendingCleanupRun | None,
    ) -> Metric:
        if run is None:
            return Metric(
                "Network review",
                "Not started",
                "No active network run state file found.",
                "warning",
            )
        pending_count = pending_cleanup.withdrawn_count() if pending_cleanup else 0
        return Metric(
            "Network review",
            "JSON",
            (
                f"{run.verified_count()}/{run.target} sent, "
                f"{len(run.observations)} candidates, "
                f"{len(followups.drafts)} follow-up drafts, "
                f"{pending_count} cleanup withdrawals"
            ),
            "good",
        )

    def _recruiter_metric(self, state: OutreachState) -> Metric:
        if not self.recruiter_store.database_path.exists():
            return Metric(
                "Recruiter/agency review",
                "Not started",
                "No recruiter/agency/advisor SQLite state file found.",
                "warning",
            )
        drafted = sum(1 for lead in state.leads if lead.draft is not None)
        return Metric(
            "Recruiter/agency review",
            "SQLite",
            f"{len(state.leads)} leads, {drafted} drafted, {len(state.agency_accounts)} accounts",
            "good",
        )

    def _network_status(
        self,
        run: Run | None,
        reservoir: CandidateReservoir,
    ) -> NetworkRunStatus:
        if run is None:
            return NetworkRunStatus(
                run_id="not-started",
                phase="not started",
                plan_next_action="Start or import a network automation run.",
                audit_status="no active run",
                reconciliation_status="no active run",
                safety_state="real sends still require guarded action flags",
            )
        plan = run.operator_plan_with_reservoir(reservoir)
        return NetworkRunStatus(
            run_id=str(run.id),
            phase=run.state.value,
            plan_next_action=_network_plan_text(plan),
            audit_status=(
                f"start {format_option(run.start_audit)}, "
                f"latest {format_option(run.latest_audit)}, "
                f"delta {format_delta(run.audited_delta())}"
            ),
            reconciliation_status=(
                f"{run.verified_count()}/{run.target} row-level verified; "
                f"{len(run.observations)} imported observations"
            ),
            safety_state=(
                f"real-send capacity {run.real_send_capacity_remaining()}/"
                f"{run.max_real_sends}"
            ),
        )

    def _network_candidates(
        self,
        run: Run | None,
        reservoir: CandidateReservoir,
    ) -> tuple[NetworkCandidateRow, ...]:
        if run is None:
            return tuple(
                _network_observation_row(observation, "reservoir")
                for observation in reservoir.observations
            )
        rows: list[NetworkCandidateRow] = []
        seen: set[str] = set()
        for index, event in enumerate(reversed(run.candidates)):
            row_id = event.profile_url or f"event:{event.source}:{event.name}:{index}"
            seen.add(row_id)
            rows.append(
                NetworkCandidateRow(
                    candidate_id=row_id,
                    name=event.name,
                    source=event.source,
                    status=event.status.value,
                    next_step=event.note or _network_event_next_step(event.status.value),
                )
            )
        for observation in run.observations:
            row_id = _observation_row_id(observation)
            if row_id in seen or run.has_candidate_event_for_observation(observation):
                continue
            seen.add(row_id)
            rows.append(_network_observation_row(observation, "active run"))
        for observation in reservoir.observations:
            row_id = _observation_row_id(observation)
            if row_id in seen:
                continue
            seen.add(row_id)
            rows.append(_network_observation_row(observation, "reservoir"))
        return tuple(rows)

    def _acceptance_drafts(
        self,
        followups: AcceptanceFollowupLedger,
    ) -> tuple[AcceptanceDraftRow, ...]:
        return tuple(
            AcceptanceDraftRow(
                draft_id=record.id,
                person=record.name,
                draft_status=record.status.value,
                dry_run_status=_acceptance_dry_run_status(record),
                send_history=_acceptance_send_history(record),
                profile_url=record.profile_url or "",
                draft_body=record.draft,
            )
            for record in sorted(
                followups.drafts,
                key=lambda item: item.updated_at,
                reverse=True,
            )
        )

    def _pending_cleanup_rows(
        self,
        run: PendingCleanupRun | None,
    ) -> tuple[PendingCleanupRow, ...]:
        if run is None:
            return ()
        threshold = format_pending_threshold(run)
        rows: list[PendingCleanupRow] = []
        matched_withdrawals: set[int] = set()
        for observation in run.observations:
            event_index = _matching_withdrawal_index(run, observation)
            event = run.withdrawals[event_index] if event_index is not None else None
            if event_index is not None:
                matched_withdrawals.add(event_index)
            rows.append(
                PendingCleanupRow(
                    invite_id=observation.profile_url or f"pending:{observation.index}",
                    name=observation.name,
                    age=observation.age_text,
                    threshold=threshold,
                    dry_run_status="eligible" if observation.eligible else "below threshold",
                    withdraw_history=event.status.value if event else "not attempted",
                )
            )
        for index, event in enumerate(run.withdrawals):
            if index in matched_withdrawals:
                continue
            rows.append(
                PendingCleanupRow(
                    invite_id=event.profile_url or f"withdrawal:{index}",
                    name=event.name,
                    age=event.age_text,
                    threshold=threshold,
                    dry_run_status="recorded",
                    withdraw_history=event.status.value,
                )
            )
        return tuple(rows)

    def _recruiter_summary(self, state: OutreachState) -> RecruiterRunSummary:
        report = build_dashboard_report(state, str(self.recruiter_store.state_path))
        latest = state.run_events[-1] if state.run_events else None
        next_action = build_agency_pool_next_action(state, str(self.recruiter_store.state_path))
        drafted = sum(1 for lead in state.leads if lead.draft is not None)
        ready = (
            report.ready_counts.agencies
            + report.ready_counts.recruiters
            + report.ready_counts.advisors
        )
        sent = (
            report.lifetime_counts.agencies
            + report.lifetime_counts.recruiters
            + report.lifetime_counts.advisors
        )
        backlog = (
            report.backlog_counts.agencies
            + report.backlog_counts.recruiters
            + report.backlog_counts.advisors
        )
        blocker = ""
        if latest and latest.blocker:
            blocker = latest.blocker
        elif report.limiting_reason:
            blocker = report.limiting_reason
        return RecruiterRunSummary(
            run_id=(latest.run_id if latest and latest.run_id else "state"),
            started_at=(latest.started_at or latest.at if latest else state.updated_at),
            result=(
                f"{len(state.leads)} leads; {drafted} drafted; {backlog} needs validation; "
                f"{ready} dry_run_ready; {sent} sent; {len(state.agency_accounts)} accounts"
            ),
            next_recommendation=_recruiter_next_action_text(next_action.action, next_action.reason),
            blockers=blocker,
        )

    def _agency_accounts(self, state: OutreachState) -> tuple[AgencyAccountRow, ...]:
        lead_counts_by_account = agency_pool_lead_counts_by_account(state)
        accounts = sorted(
            state.agency_accounts,
            key=lambda item: (item.status.value, -item.fit_score, item.name),
        )
        rows: list[AgencyAccountRow] = []
        for account in accounts:
            lead_counts = lead_counts_by_account.get(account.id, AgencyPoolLeadCounts())
            diagnosis = build_agency_pool_account_diagnosis(account, lead_counts)
            blocker = (
                diagnosis.next_step
                if diagnosis.next_step != "no_action"
                else "; ".join(account.reject_reasons)
            )
            if not blocker and account.last_contact_error:
                blocker = account.last_contact_error
            rows.append(
                AgencyAccountRow(
                    account_id=account.id,
                    agency=account.name,
                    status=account.status.value,
                    contactability=(
                        f"{lead_counts.contacts} contacts; "
                        f"{lead_counts.open_leads} open; "
                        f"{lead_counts.messageable_or_sent} messageable/sent"
                    ),
                    blocker=blocker,
                )
            )
        return tuple(rows)

    def _recruiter_leads(self, state: OutreachState) -> tuple[RecruiterLeadRow, ...]:
        leads = sorted(
            state.leads,
            key=lambda item: (
                bucket_for_lead(item),
                item.message_status.value,
                -item.fit_score,
                item.name,
            ),
        )
        return tuple(_recruiter_lead_row(state, lead) for lead in leads)

    def _browser_sessions(self) -> tuple[BrowserSessionRow, ...]:
        config = chrome_profile_from_env()
        profile_path = chrome_profile_storage_dir(config)
        latest_error = self.store.fetch_all(
            """
            SELECT message
            FROM extraction_errors
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        warning = str(latest_error[0]["message"]) if latest_error else ""
        state = "profile present" if profile_path.exists() else "profile missing"
        return (
            BrowserSessionRow(
                profile_name=config.profile_name,
                session_state=state,
                current_session=str(profile_path),
                safety_warning=warning,
            ),
        )

    def _browser_artifacts(self) -> tuple[BrowserArtifactRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT artifact_id, app, kind, path, status, retryable_error
            FROM extraction_artifacts
            ORDER BY created_at DESC
            """
        )
        return tuple(
            BrowserArtifactRow(
                artifact_id=str(row["artifact_id"]),
                app=str(row["app"]),
                kind=str(row["kind"]),
                path=str(row["path"]),
                status=str(row["status"]),
                retryable_error=str(row["retryable_error"]),
            )
            for row in rows
        )

    def _artifact_path(self, run_id: str, kind: str) -> str:
        rows = self.store.fetch_all(
            """
            SELECT path
            FROM extraction_artifacts
            WHERE run_id = ? AND kind = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, kind),
        )
        return str(rows[0]["path"]) if rows else ""


def _json_strings(raw: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, str))


def _registry_source_rows() -> tuple[OpportunitySourceRow, ...]:
    registry = load_source_registry()
    return tuple(
        OpportunitySourceRow(
            source_id=source.source_id,
            source_type=source.source_kind.value,
            label=source.title,
            enabled=source.enabled,
            priority=str(source.priority),
            hypothesis=source.description,
            target_needs=", ".join(source.query_ids),
            latest_yield="not extracted yet",
            notes="Source registry",
        )
        for source in sorted(
            registry.sources,
            key=lambda item: (-item.priority, item.source_id),
        )
    )


def _registry_post_rows() -> tuple[PostQueueRow, ...]:
    registry = load_source_registry()
    candidates = discover_posts_from_registry(registry)
    return tuple(
        PostQueueRow(
            post_id=post_candidate_key(candidate),
            post_url=candidate.post_url or candidate.source_url or candidate.search_query,
            source_id=candidate.source_id,
            author="",
            priority_reasons=candidate.reason,
            extraction_status="registry",
            artifact_path="",
        )
        for candidate in candidates
    )


def _network_plan_text(plan: object) -> str:
    parts = [str(getattr(plan, "action", ""))]
    source = getattr(plan, "source", None)
    name = getattr(plan, "name", None)
    reason = getattr(plan, "reason", None)
    if source:
        parts.append(f"source={source}")
    if name:
        parts.append(f"name={name}")
    if reason:
        parts.append(str(reason))
    return "; ".join(part for part in parts if part)


def _network_event_next_step(status: str) -> str:
    if status in {"pending", "audit-top-up"}:
        return "connection request recorded"
    if status == "already-pending":
        return "already pending on LinkedIn"
    if status == "skipped":
        return "skipped"
    if status == "failed":
        return "needs review"
    return ""


def _observation_row_id(observation: CandidateObservation) -> str:
    return (
        observation.profile_url
        or observation.sales_profile_urn
        or f"observation:{observation.source}:{observation.index}:{observation.name}"
    )


def _network_observation_row(
    observation: CandidateObservation,
    location: str,
) -> NetworkCandidateRow:
    next_step = "queued for send" if observation.menu_state == "connectable" else "review capture"
    return NetworkCandidateRow(
        candidate_id=_observation_row_id(observation),
        name=observation.name,
        source=f"{observation.source} ({location})",
        status=f"captured:{observation.menu_state}",
        next_step=next_step,
    )


def _acceptance_dry_run_status(record: AcceptanceFollowupRecord) -> str:
    for attempt in reversed(record.attempts):
        if attempt.dry_run:
            return attempt.status
    if record.status.value == "dry_run_ready":
        return record.status.value
    return "not run"


def _acceptance_send_history(record: AcceptanceFollowupRecord) -> str:
    if record.sent_at is not None:
        return f"sent at {record.sent_at.isoformat()}"
    if not record.attempts:
        return "no attempts"
    latest = record.attempts[-1]
    mode = "dry-run" if latest.dry_run else "send"
    return (
        f"{len(record.attempts)} attempts; latest {mode} "
        f"{latest.status} at {latest.at.isoformat()}"
    )


def _matching_withdrawal_index(
    run: PendingCleanupRun,
    observation: PendingCandidateObservation,
) -> int | None:
    for index, event in enumerate(run.withdrawals):
        if event.profile_url and observation.profile_url:
            if event.profile_url == observation.profile_url:
                return index
            continue
        if event.name == observation.name and event.age_text == observation.age_text:
            return index
    return None


def _recruiter_next_action_text(action: str, reason: str) -> str:
    return f"{action}: {reason}" if reason else action


def _recruiter_lead_row(state: OutreachState, lead: Lead) -> RecruiterLeadRow:
    draft_subject = lead.draft.subject if lead.draft else ""
    draft_body = lead.draft.body if lead.draft else ""
    return RecruiterLeadRow(
        lead_id=lead.id,
        name=lead.name,
        lead_type=lead.lead_type.value,
        draft_status="drafted" if lead.draft else "no draft",
        messageability_status=lead.message_status.value,
        send_readiness=_lead_send_readiness(lead),
        blocker=_lead_blocker(state, lead),
        profile_url=lead.profile_url or "",
        draft_subject=draft_subject,
        draft_body=draft_body,
    )


def _lead_send_readiness(lead: Lead) -> str:
    if lead.message_status in {MessageStatus.SENT, MessageStatus.MANUALLY_SENT}:
        return "sent"
    if lead.message_status == MessageStatus.DRY_RUN_READY:
        return "ready to send"
    if lead.message_status == MessageStatus.APPROVED:
        return "approved"
    if lead.draft and lead.profile_url and lead.status == LeadStatus.ELIGIBLE:
        return "needs dry-run"
    if lead.draft:
        return "drafted"
    if lead.status != LeadStatus.ELIGIBLE:
        return lead.status.value
    return "needs draft"


def _lead_blocker(state: OutreachState, lead: Lead) -> str:
    if lead.reject_reasons:
        return "; ".join(lead.reject_reasons)
    if lead.status != LeadStatus.ELIGIBLE:
        return lead.status.value
    if not lead.profile_url:
        return "missing profile URL"
    if bucket_for_lead(lead) == "agency" and lead.agency_account_id:
        account = next(
            (item for item in state.agency_accounts if item.id == lead.agency_account_id),
            None,
        )
        if account is not None and account.status.value != "qualified":
            return f"agency account {account.status.value}"
    if lead.send_attempts:
        latest = lead.send_attempts[-1]
        if latest.status in {"not-messageable", "blocked", "send-failed"}:
            return latest.note or latest.status
    return ""
