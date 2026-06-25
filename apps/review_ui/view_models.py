"""Read models for the local LinkedIn tools review UI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from apps.opportunity_intel.store import OpportunityStore
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


class StubReviewReadModelProvider:
    """Pre-integration provider used until app read models are available."""

    def snapshot(self) -> ReviewSnapshot:
        return build_stub_snapshot()


def build_stub_snapshot() -> ReviewSnapshot:
    notices = (
        IntegrationNotice(
            area="Opportunity Intel",
            owner="Thread 3",
            dependency=(
                "Replace stub rows with source, post, comment, experiment, "
                "and calibration read models."
            ),
        ),
        IntegrationNotice(
            area="Network Automation",
            owner="Thread 4",
            dependency=(
                "Connect run, reservoir, acceptance, audit, and pending-cleanup "
                "read models."
            ),
        ),
        IntegrationNotice(
            area="Recruiter/Agency Outreach",
            owner="Thread 5",
            dependency=(
                "Connect latest run, account pool, lead queue, draft, and "
                "send-readiness read models."
            ),
        ),
        IntegrationNotice(
            area="Browser/Artifacts",
            owner="Thread 2",
            dependency=(
                "Connect Playwright session state, artifact manifests, "
                "screenshots, and rate-limit warnings."
            ),
        ),
    )
    return ReviewSnapshot(
        notices=notices,
        system_metrics=(
            Metric("Opportunity review", "SQLite", "Read model owned by opportunity store."),
            Metric("Network review", "Stubbed", "Read model dependency pending.", "warning"),
            Metric(
                "Recruiter/agency review",
                "Stubbed",
                "Read model dependency pending.",
                "warning",
            ),
            Metric("Browser artifacts", "SQLite", "Read model owned by opportunity store."),
        ),
        opportunity_metrics=(),
        opportunity_sources=(),
        post_queue=(),
        extraction_runs=(),
        ranked_comments=(),
        experiments=(),
        calibration_queue=(),
        network_status=NetworkRunStatus(
            run_id="network-run-stub",
            phase="planning",
            plan_next_action="Network read model not part of this opportunity scope",
            audit_status="not connected",
            reconciliation_status="not connected",
            safety_state="real sends require guarded service integration",
        ),
        network_candidates=(),
        acceptance_drafts=(),
        pending_cleanup=(),
        recruiter_summary=RecruiterRunSummary(
            run_id="recruiter-run-stub",
            started_at="pending",
            result="Recruiter/agency read model not part of this opportunity scope",
            next_recommendation="Use recruiter/agency dashboard commands for live state.",
            blockers="",
        ),
        agency_accounts=(),
        recruiter_leads=(),
        browser_sessions=(),
        browser_artifacts=(),
    )


class SQLiteReviewReadModelProvider:
    """SQLite-backed opportunity and browser read model provider."""

    def __init__(
        self,
        *,
        store: OpportunityStore | None = None,
        state_dir: str | Path | None = None,
    ) -> None:
        self.store = store or OpportunityStore(state_dir)

    def snapshot(self) -> ReviewSnapshot:
        fallback = build_stub_snapshot()
        opportunity_metrics = self._opportunity_metrics()
        return ReviewSnapshot(
            notices=(
                IntegrationNotice(
                    area="Opportunity Intel",
                    owner="SQLite",
                    dependency="Live source, post, extraction, ranking, and review state.",
                ),
                IntegrationNotice(
                    area="Browser/Artifacts",
                    owner="SQLite",
                    dependency="Live extraction artifacts and browser setup state.",
                ),
                *tuple(
                    notice
                    for notice in fallback.notices
                    if notice.area not in {"Opportunity Intel", "Browser/Artifacts"}
                ),
            ),
            system_metrics=(
                Metric(
                    "Opportunity review",
                    "SQLite",
                    f"{opportunity_metrics[0].value} persisted comments",
                    "good",
                ),
                fallback.system_metrics[1],
                fallback.system_metrics[2],
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
            network_status=fallback.network_status,
            network_candidates=fallback.network_candidates,
            acceptance_drafts=fallback.acceptance_drafts,
            pending_cleanup=fallback.pending_cleanup,
            recruiter_summary=fallback.recruiter_summary,
            agency_accounts=fallback.agency_accounts,
            recruiter_leads=fallback.recruiter_leads,
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
            LIMIT 200
            """
        )
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
            LIMIT 200
            """
        )
        return tuple(
            PostQueueRow(
                post_id=str(row["post_id"]),
                post_url=str(row["post_url"] or row["source_url"]),
                source_id=str(row["source_id"]),
                author=str(row["post_author_name"]),
                priority_reasons=str(row["reason"]),
                extraction_status=str(row["extraction_status"]),
                artifact_path=str(row["artifact_path"]),
            )
            for row in rows
        )

    def _extraction_runs(self) -> tuple[ExtractionRunRow, ...]:
        rows = self.store.fetch_all(
            """
            SELECT run_id, post_url, comments_found, failures, retry_recommendation
            FROM extraction_runs
            ORDER BY started_at DESC
            LIMIT 100
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
            LIMIT 200
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
            LIMIT 100
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
            LIMIT 200
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
