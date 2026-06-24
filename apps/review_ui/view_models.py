"""Read models for the local LinkedIn tools review UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
            Metric(
                "Opportunity review",
                "Stubbed",
                "All required views are scaffolded.",
                "warning",
            ),
            Metric("Network review", "Stubbed", "Read model dependency pending.", "warning"),
            Metric(
                "Recruiter/agency review",
                "Stubbed",
                "Read model dependency pending.",
                "warning",
            ),
            Metric("Browser artifacts", "Stubbed", "Browser layer dependency pending.", "warning"),
        ),
        opportunity_metrics=(
            Metric("Valid comments", "128", "Fixture-backed example"),
            Metric("Warm/hot comments", "17", "Fixture-backed example"),
            Metric("Direct-buyer rate", "12.5%", "Fixture-backed example", "good"),
            Metric("Noise rate", "48%", "Fixture-backed example"),
        ),
        opportunity_sources=(
            OpportunitySourceRow(
                source_id="src-operator-tools",
                source_type="creator_profile",
                label="Operator tool builders",
                enabled=True,
                priority="high",
                hypothesis="Operators discussing internal tooling will surface buyer pain.",
                target_needs="Internal tools, AI workflow automation, dashboards",
                latest_yield="11 warm/hot of 84 valid comments",
                notes="Stub pending source registry model.",
            ),
            OpportunitySourceRow(
                source_id="src-spreadsheet-ops",
                source_type="keyword_search",
                label="Spreadsheet-heavy operations",
                enabled=True,
                priority="medium",
                hypothesis="Manual spreadsheet workflows expose dashboard and automation needs.",
                target_needs="Spreadsheet-heavy operations, reporting",
                latest_yield="6 warm/hot of 44 valid comments",
                notes="Stub pending query-pack model.",
            ),
        ),
        post_queue=(
            PostQueueRow(
                post_id="post-001",
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:0001/",
                source_id="src-operator-tools",
                author="Product Ops Lead",
                priority_reasons="First-party workflow pain and visible comments",
                extraction_status="queued",
                artifact_path="/tmp/linkedin-tools/opportunity/post-001.json",
            ),
        ),
        extraction_runs=(
            ExtractionRunRow(
                run_id="extract-001",
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:0001/",
                comments_found=42,
                failures=0,
                screenshot_path="/tmp/linkedin-tools/artifacts/post-001.png",
                raw_artifact_path="/tmp/linkedin-tools/artifacts/post-001-comments.jsonl",
                retry_recommendation="No retry needed",
            ),
        ),
        ranked_comments=(
            RankedCommentRow(
                comment_id="comment-hot-1",
                commenter="Avery Chen",
                headline="COO at Seed-stage SaaS",
                profile_url="https://www.linkedin.com/in/avery-chen/",
                comment_text="We are still stitching this together in spreadsheets every Friday.",
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:0001/",
                evidence_quote="stitching this together in spreadsheets",
                fit_reasons="First-person operational pain; reporting workflow fit",
                reject_reasons="",
                level="hot",
                signal_score=87,
                source_id="src-spreadsheet-ops",
                human_label="unlabeled",
            ),
            RankedCommentRow(
                comment_id="comment-watch-1",
                commenter="Sam Rivera",
                headline="Founder",
                profile_url="https://www.linkedin.com/in/sam-rivera/",
                comment_text="This is useful, but we solved most of it with a no-code dashboard.",
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:0002/",
                evidence_quote="solved most of it with a no-code dashboard",
                fit_reasons="Dashboard context",
                reject_reasons="No active pain stated",
                level="watch",
                signal_score=41,
                source_id="src-operator-tools",
                human_label="unlabeled",
            ),
        ),
        experiments=(
            ExperimentRow(
                source_id="src-operator-tools",
                valid_comments=84,
                qualified_comments=11,
                qualified_per_100=13.1,
                direct_buyer_rate="14%",
                noise_rate="44%",
                warm_hot_rate="13%",
                source_decision="Collect more before promotion",
            ),
            ExperimentRow(
                source_id="src-spreadsheet-ops",
                valid_comments=44,
                qualified_comments=6,
                qualified_per_100=13.6,
                direct_buyer_rate="9%",
                noise_rate="52%",
                warm_hot_rate="14%",
                source_decision="Label more before promotion",
            ),
        ),
        calibration_queue=(
            CalibrationRow(
                item_id="comment-hot-1",
                machine_label="hot",
                human_label="unlabeled",
                disagreement=False,
                evidence="First-party spreadsheet workflow pain.",
            ),
            CalibrationRow(
                item_id="comment-watch-1",
                machine_label="watch",
                human_label="unlabeled",
                disagreement=False,
                evidence="Dashboard mention without active ask.",
            ),
        ),
        network_status=NetworkRunStatus(
            run_id="network-run-stub",
            phase="planning",
            plan_next_action="Await Thread 4 read model",
            audit_status="not connected",
            reconciliation_status="not connected",
            safety_state="real sends require guarded service integration",
        ),
        network_candidates=(
            NetworkCandidateRow(
                candidate_id="cand-001",
                name="Morgan Patel",
                source="Sales Navigator saved search",
                status="queued",
                next_step="Dry-run guarded send validation",
            ),
        ),
        acceptance_drafts=(
            AcceptanceDraftRow(
                draft_id="draft-001",
                person="Jordan Lee",
                draft_status="ready",
                dry_run_status="dry_run_ready",
                send_history="none in Python port",
            ),
        ),
        pending_cleanup=(
            PendingCleanupRow(
                invite_id="invite-001",
                name="Taylor Smith",
                age="17 days",
                threshold="14 days",
                dry_run_status="pending integration",
                withdraw_history="none in Python port",
            ),
        ),
        recruiter_summary=RecruiterRunSummary(
            run_id="recruiter-run-stub",
            started_at="pending",
            result="Await Thread 5 read model",
            next_recommendation="Connect latest run and dashboard summaries",
            blockers="missing_linkedin_company_url and Sales Navigator identity rows are examples",
        ),
        agency_accounts=(
            AgencyAccountRow(
                account_id="acct-001",
                agency="Northstar Recruiting",
                status="needs account evidence",
                contactability="unknown",
                blocker="missing_linkedin_company_url",
            ),
        ),
        recruiter_leads=(
            RecruiterLeadRow(
                lead_id="lead-001",
                name="Riley Adams",
                lead_type="agency_contact",
                draft_status="drafted",
                messageability_status="dry_run_ready",
                send_readiness="guarded send pending",
                blocker="requires Thread 5 service integration",
            ),
        ),
        browser_sessions=(
            BrowserSessionRow(
                profile_name="LinkedIn",
                session_state="not connected",
                current_session="pending browser layer",
                safety_warning="rate-limit warnings will block retries when connected",
            ),
        ),
        browser_artifacts=(
            BrowserArtifactRow(
                artifact_id="artifact-001",
                app="opportunity",
                kind="screenshot",
                path="/tmp/linkedin-tools/artifacts/post-001.png",
                status="fixture",
                retryable_error="",
            ),
            BrowserArtifactRow(
                artifact_id="artifact-002",
                app="network",
                kind="raw JSON",
                path="/tmp/linkedin-tools/artifacts/network-audit.json",
                status="stub",
                retryable_error="read model pending",
            ),
        ),
    )
