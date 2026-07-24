"""Domain model for the Python port of linkedin-network-run."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from datetime import date as Date
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


def now_utc() -> datetime:
    return datetime.now(UTC)


def today() -> Date:
    return Date.today()


class AppModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class RunState(StrEnum):
    STARTED = "Started"
    START_AUDITED = "StartAudited"
    SENDING = "Sending"
    NEEDS_BROWSER_INSPECTION = "NeedsBrowserInspection"
    NEEDS_REAUDIT = "NeedsReaudit"
    FINAL_RECONCILE = "FinalReconcile"
    DONE = "Done"
    BLOCKED = "Blocked"


class BrowserRecoveryAction(StrEnum):
    INSPECT_ONLY = "inspect_only"
    RELOAD_PAGE = "reload_page"
    NAVIGATE_EXPECTED_URL = "navigate_expected_url"
    DISMISS_OBSTRUCTION = "dismiss_obstruction"
    RECONNECT_SESSION = "reconnect_session"
    RETRY_OPERATION = "retry_operation"


class BrowserIncidentStatus(StrEnum):
    NEEDS_INSPECTION = "needs_inspection"
    RECOVERED = "recovered"
    UNRESOLVED = "unresolved"
    BLOCKED = "blocked"
    AUDIT_REQUIRED = "audit_required"


class BrowserRecoveryStatus(StrEnum):
    RECOVERED = "recovered"
    UNRESOLVED = "unresolved"
    BLOCKED = "blocked"


class BrowserRecoveryReceipt(AppModel):
    incident_id: uuid.UUID
    lease_id: uuid.UUID
    status: BrowserRecoveryStatus
    actions: list[BrowserRecoveryAction] = Field(default_factory=list)
    inspected_at: datetime = Field(default_factory=now_utc)
    before_artifacts: list[str] = Field(default_factory=list)
    after_artifacts: list[str] = Field(default_factory=list)
    current_url: str | None = None
    evidence: str
    note: str | None = None


class BrowserIncident(AppModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    lease_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    created_at: datetime = Field(default_factory=now_utc)
    operation: str
    previous_state: RunState
    possible_send: bool = False
    expected_url: str | None = None
    source: str | None = None
    candidate_name: str | None = None
    profile_url: str | None = None
    error: str
    incident_path: str
    markdown_path: str
    receipt_path: str
    diagnostic_path: str | None = None
    screenshot_path: str | None = None
    diagnostic_error: str | None = None
    allowed_actions: list[BrowserRecoveryAction] = Field(default_factory=list)
    status: BrowserIncidentStatus = BrowserIncidentStatus.NEEDS_INSPECTION
    receipt: BrowserRecoveryReceipt | None = None


class CandidateStatus(StrEnum):
    PENDING_PROVISIONAL = "pending-provisional"
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVERTED_CONNECT = "reverted-connect"
    ALREADY_PENDING = "already-pending"
    AUDIT_TOP_UP = "audit-top-up"
    SKIPPED = "skipped"
    FAILED = "failed"


class SourceCursorStatus(StrEnum):
    ADVANCED = "advanced"
    PARTIAL_PAGE = "partial_page"
    END_OF_RESULTS = "end_of_results"
    STALLED_NAVIGATION = "stalled_navigation"
    ROW_LOAD_TIMEOUT = "row_load_timeout"
    WRONG_PAGE = "wrong_page"
    NO_NEXT_URL = "no_next_url"


class LeadStatus(StrEnum):
    NEW = "new"
    APPROVED = "approved"
    SENT = "sent"
    SKIPPED = "skipped"
    PENDING = "pending"
    CONNECTED = "connected"
    BLOCKED = "blocked"


class GreetingEligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    NEEDS_REVIEW = "needs_review"
    INELIGIBLE = "ineligible"


class RelationshipEnrichmentStatus(StrEnum):
    MISSING = "missing"
    CURRENT = "current"
    STALE = "stale"
    NEEDS_REVIEW = "needs_review"
    NOT_APPLICABLE = "not_applicable"


TARGET_COUNTED_SEND_STATUSES = frozenset({CandidateStatus.PENDING, CandidateStatus.ACCEPTED})
REAL_SEND_ATTEMPT_STATUSES = frozenset(
    {
        CandidateStatus.PENDING_PROVISIONAL,
        CandidateStatus.PENDING,
        CandidateStatus.ACCEPTED,
        CandidateStatus.REVERTED_CONNECT,
        CandidateStatus.AUDIT_TOP_UP,
    }
)
SEND_LEDGER_STATUSES = frozenset(
    {
        CandidateStatus.PENDING_PROVISIONAL,
        CandidateStatus.PENDING,
        CandidateStatus.ACCEPTED,
        CandidateStatus.REVERTED_CONNECT,
        CandidateStatus.AUDIT_TOP_UP,
        CandidateStatus.FAILED,
    }
)
DURABLE_SEND_LEDGER_STATUSES = frozenset(
    {
        CandidateStatus.PENDING,
        CandidateStatus.ACCEPTED,
        CandidateStatus.AUDIT_TOP_UP,
    }
)


class AcceptanceStatus(StrEnum):
    SENT = "sent"
    PENDING = "pending"
    ACCEPTED = "accepted"
    INVALIDATED = "invalidated"
    CONNECTABLE = "connectable"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    FAILED = "failed"


class AcceptanceObservationPrecision(StrEnum):
    DAILY_SCAN = "daily_scan"
    LIST_RECONCILIATION = "list_reconciliation"
    CONTROLLER_CONFIRMATION = "controller_confirmation"


class AcceptanceEvidenceGrade(StrEnum):
    CONTROLLER_CONFIRMED = "controller_confirmed"
    STRUCTURED_FIRST_DEGREE = "structured_first_degree"
    CONNECTIONS_LIST_FIRST_DEGREE = "connections_list_first_degree"
    LEGACY_UNVERIFIED = "legacy_unverified"
    INVALIDATED = "invalidated"


class AcceptanceRelationshipStatus(StrEnum):
    FIRST_DEGREE = "first_degree"
    SECOND_DEGREE = "second_degree"
    UNKNOWN = "unknown"


class PendingCleanupState(StrEnum):
    STARTED = "Started"
    AUDITED = "Audited"
    CAPTURING = "Capturing"
    WITHDRAWING = "Withdrawing"
    NEEDS_REAUDIT = "NeedsReaudit"
    FINAL_RECONCILE = "FinalReconcile"
    DONE = "Done"
    BLOCKED = "Blocked"


class PendingWithdrawStatus(StrEnum):
    WITHDRAWN = "Withdrawn"
    SKIPPED = "Skipped"
    FAILED = "Failed"


class RelationshipRole(StrEnum):
    BUYER = "buyer"
    REFERRAL_PARTNER = "referral_partner"
    HIRING_RECRUITER = "hiring_recruiter"
    OTHER = "other"


class RelationshipSignalType(StrEnum):
    LINKEDIN_POST = "linkedin_post"
    COMPANY_SITE = "company_site"
    PROFILE = "profile"
    OTHER = "other"
    NONE = "none"


class RelationshipPriority(StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    PAUSE = "pause"


class RelationshipWatchlistDecision(StrEnum):
    RECOMMENDED = "recommended"
    NEEDS_REVIEW = "needs_review"
    NOT_RECOMMENDED = "not_recommended"


class CommercialCriterionAssessment(StrEnum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNKNOWN = "unknown"


DEFAULT_ICP_PROFILE_ID = "icp-v1"
DEFAULT_OFFERS_PROFILE_ID = "offers-v1"
DEFAULT_COMMERCIAL_OFFER_ID = "business-systems-audit"
DEFAULT_ICP_SOURCE_PATH = (
    "/Users/hanifcarroll/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
    "hanif-md/system/ICP.md"
)
DEFAULT_OFFERS_SOURCE_PATH = (
    "/Users/hanifcarroll/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
    "hanif-md/system/OFFERS.md"
)


class CommercialContextReference(AppModel):
    icp_profile_id: str = DEFAULT_ICP_PROFILE_ID
    icp_source_path: str = DEFAULT_ICP_SOURCE_PATH
    offers_profile_id: str = DEFAULT_OFFERS_PROFILE_ID
    offers_source_path: str = DEFAULT_OFFERS_SOURCE_PATH
    offer_id: str = DEFAULT_COMMERCIAL_OFFER_ID


class CommercialCriterionEvidence(AppModel):
    criterion_id: str
    assessment: CommercialCriterionAssessment
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class AcceptanceFollowupStatus(StrEnum):
    DRAFTED = "drafted"
    DRY_RUN_READY = "dry_run_ready"
    SENT = "sent"
    CONVERSATION_EXISTS = "conversation_exists"
    INVALID_ACCEPTANCE = "invalid_acceptance"
    EXCLUDED = "excluded"
    NOT_MESSAGEABLE = "not_messageable"
    DIRECT_MESSAGE_UNAVAILABLE = "direct_message_unavailable"
    BLOCKED = "blocked"
    SEND_FAILED = "send_failed"
    POSSIBLE_SEND = "possible_send"


class AcceptanceLeadListStatus(StrEnum):
    PENDING = "pending"
    SAVED = "saved"
    ALREADY_SAVED = "already_saved"
    LIST_MISSING = "list_missing"
    NOT_SAVEABLE = "not_saveable"
    BLOCKED = "blocked"
    SAVE_FAILED = "save_failed"


class SourcePlan(AppModel):
    name: str
    target: int
    fallback: bool = False
    exhausted: bool = False


class AuditEvent(AppModel):
    at: datetime = Field(default_factory=now_utc)
    people_count: int
    note: str | None = None


class CandidateEvent(AppModel):
    at: datetime = Field(default_factory=now_utc)
    source: str
    name: str
    profile_url: str | None = None
    public_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("public_profile_url", "publicProfileUrl")
    )
    status: CandidateStatus
    note: str | None = None


class SendLedgerEntry(AppModel):
    entry_id: str
    attempt_key: str
    run_id: str
    run_date: Date | None = None
    source: str
    name: str
    profile_url: str | None = None
    public_profile_url: str | None = None
    attempted_at: datetime
    confirmed_at: datetime | None = None
    status: CandidateStatus
    durable: bool
    reason: str | None = None
    event_kind: str
    result_path: str | None = None


class SendLedgerSummary(AppModel):
    date: Date
    timezone: str
    ledger_path: str
    durable_sent_count: int
    by_source: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    provisional_count: int = 0
    failed_count: int = 0
    reverted_count: int = 0
    top_up_count: int = 0
    synced_entries: int = 0
    history_logs_scanned: int = 0
    entries: list[SendLedgerEntry] = Field(default_factory=list)


def send_ledger_attempt_key(run_id: str, event: CandidateEvent) -> str:
    return stable_hash(
        "send-ledger-attempt",
        run_id,
        event.source,
        event.name,
        event.profile_url or "",
        event.at.isoformat(),
    )


def send_ledger_entry_id(attempt_key: str, status: CandidateStatus, event_kind: str) -> str:
    return stable_hash("send-ledger-entry", attempt_key, status.value, event_kind)


def stable_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


class CandidateObservation(AppModel):
    imported_at: datetime = Field(default_factory=now_utc)
    captured_at: str | None = None
    source: str
    index: int
    name: str
    profile_url: str | None = None
    public_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("public_profile_url", "publicProfileUrl")
    )
    search_url: str | None = Field(
        default=None, validation_alias=AliasChoices("search_url", "searchUrl")
    )
    sales_profile_urn: str | None = None
    text: str | None = None
    visible_state: Any = None
    menu_state: str = "unknown"
    menu_labels: list[str] = Field(default_factory=list)
    links: list[Any] = Field(default_factory=list)
    row_html_path: str | None = None


class LeadRecord(AppModel):
    lead_key: str
    name: str
    profile_url: str | None = None
    public_profile_url: str | None = None
    search_url: str | None = None
    sales_profile_urn: str | None = None
    first_seen_at: datetime = Field(default_factory=now_utc)
    last_seen_at: datetime = Field(default_factory=now_utc)
    first_source: str | None = None
    last_source: str | None = None
    status: LeadStatus = LeadStatus.NEW
    status_reason: str | None = None
    approved_source: str | None = None
    approved_reason: str | None = None
    approved_at: datetime | None = None
    reviewed_at: datetime | None = None
    last_menu_state: str | None = None
    last_row_text: str | None = None


class LeadLedger(AppModel):
    leads: dict[str, LeadRecord] = Field(default_factory=dict)

    def upsert_observation(self, observation: CandidateObservation) -> LeadRecord:
        lead_key = lead_key_for_observation(observation)
        current = now_utc()
        record = self.leads.get(lead_key)
        if record is None:
            record = LeadRecord(
                lead_key=lead_key,
                name=observation.name,
                profile_url=observation.profile_url,
                public_profile_url=observation.public_profile_url,
                search_url=observation.search_url,
                sales_profile_urn=observation.sales_profile_urn,
                first_seen_at=current,
                last_seen_at=current,
                first_source=observation.source,
                last_source=observation.source,
                last_menu_state=observation.menu_state,
                last_row_text=observation.text,
            )
        else:
            record.name = observation.name or record.name
            record.profile_url = observation.profile_url or record.profile_url
            record.public_profile_url = observation.public_profile_url or record.public_profile_url
            record.search_url = observation.search_url or record.search_url
            record.sales_profile_urn = observation.sales_profile_urn or record.sales_profile_urn
            record.last_seen_at = current
            record.last_source = observation.source
            record.last_menu_state = observation.menu_state
            record.last_row_text = observation.text
        if observation.menu_state == "already-pending" and record.status in {
            LeadStatus.NEW,
            LeadStatus.APPROVED,
        }:
            record.status = LeadStatus.PENDING
            record.status_reason = "capture row showed an existing pending invitation"
        self.leads[lead_key] = record
        return record

    def get_for_observation(self, observation: CandidateObservation) -> LeadRecord | None:
        return self.leads.get(lead_key_for_observation(observation))

    def approve(self, lead_key: str, reason: str | None = None) -> LeadRecord:
        record = self.require(lead_key)
        current = now_utc()
        record.status = LeadStatus.APPROVED
        record.status_reason = reason
        record.approved_source = record.last_source or record.first_source
        record.approved_reason = reason
        record.approved_at = current
        record.reviewed_at = current
        return record

    def skip(self, lead_key: str, reason: str | None = None) -> LeadRecord:
        record = self.require(lead_key)
        current = now_utc()
        record.status = LeadStatus.SKIPPED
        record.status_reason = reason
        record.reviewed_at = current
        return record

    def block(self, lead_key: str, reason: str | None = None) -> LeadRecord:
        record = self.require(lead_key)
        current = now_utc()
        record.status = LeadStatus.BLOCKED
        record.status_reason = reason
        record.reviewed_at = current
        return record

    def apply_candidate_event(self, event: CandidateEvent) -> LeadRecord:
        lead_key = lead_key_for_values(
            event.profile_url or event.public_profile_url, None, event.name
        )
        record = self.leads.get(lead_key)
        current = now_utc()
        if record is None:
            record = LeadRecord(
                lead_key=lead_key,
                name=event.name,
                profile_url=event.profile_url,
                public_profile_url=event.public_profile_url,
                first_seen_at=current,
                last_seen_at=current,
                first_source=event.source,
                last_source=event.source,
            )
        else:
            record.profile_url = event.profile_url or record.profile_url
            record.public_profile_url = event.public_profile_url or record.public_profile_url
        if event.status in {
            CandidateStatus.PENDING_PROVISIONAL,
            CandidateStatus.PENDING,
            CandidateStatus.ALREADY_PENDING,
        }:
            record.status = LeadStatus.PENDING
        elif event.status == CandidateStatus.ACCEPTED:
            record.status = LeadStatus.CONNECTED
        elif event.status == CandidateStatus.SKIPPED:
            record.status = LeadStatus.SKIPPED
        elif event.status in {CandidateStatus.REVERTED_CONNECT, CandidateStatus.FAILED}:
            record.status = LeadStatus.BLOCKED
        elif event.status == CandidateStatus.AUDIT_TOP_UP:
            record.status = LeadStatus.SENT
        record.status_reason = event.note
        record.last_seen_at = current
        record.last_source = event.source
        self.leads[lead_key] = record
        return record

    def require(self, lead_key: str) -> LeadRecord:
        record = self.leads.get(lead_key)
        if record is None:
            raise KeyError(f"unknown lead_key {lead_key!r}")
        return record


class LeadReviewCandidate(AppModel):
    lead_key: str
    source: str
    name: str
    profile_url: str | None = None
    public_profile_url: str | None = None
    search_url: str | None = None
    send_blockers: list[str] = Field(default_factory=list)
    captured_at: str | None = None
    menu_state: str
    menu_labels: list[str] = Field(default_factory=list)
    text: str | None = None
    links: list[Any] = Field(default_factory=list)
    current_status: LeadStatus = LeadStatus.NEW
    status_reason: str | None = None
    approved_reason: str | None = None


class LeadReviewPacket(AppModel):
    packet_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    generated_at: datetime = Field(default_factory=now_utc)
    source: str | None = None
    candidates: list[LeadReviewCandidate] = Field(default_factory=list)


class LeadReviewDecision(AppModel):
    lead_key: str
    status: LeadStatus
    reason: str | None = None


class LeadReviewDecisionArtifact(AppModel):
    packet_id: uuid.UUID
    decisions: list[LeadReviewDecision] = Field(default_factory=list)


class LeadReviewCheckpoint(AppModel):
    packet_id: uuid.UUID
    packet_path: str
    markdown_path: str
    decisions_path: str
    candidate_keys: list[str] = Field(default_factory=list)
    source: str | None = None
    action: str = "review-required"
    terminal: bool = False
    created_at: datetime = Field(default_factory=now_utc)


class SourceCaptureCursor(AppModel):
    source: str
    updated_at: datetime = Field(default_factory=now_utc)
    captured_at: str | None = None
    saved_search_id: str | None = None
    saved_search_url: str | None = None
    resume_url: str | None = None
    start_url: str | None = None
    last_scanned_url: str | None = None
    next_url: str | None = None
    next_page_available: bool | None = None
    end_of_results: bool = False
    cursor_status: str | None = None
    cursor_reason: str | None = None
    deferred_for_run: bool = False
    deferred_reason: str | None = None
    page_label: str | None = None
    captured_pages: int = 0
    raw_row_count: int = 0
    output_row_count: int = 0
    connectable_count: int = 0
    already_pending_count: int = 0
    missing_trigger_count: int = 0
    state_counts: dict[str, int] = Field(default_factory=dict)


class SourceScanProgress(AppModel):
    source: str
    updated_at: datetime = Field(default_factory=now_utc)
    saved_search_id: str | None = None
    saved_search_url: str | None = None
    next_url: str | None = None
    last_scanned_url: str | None = None
    last_started_url: str | None = None
    end_of_results: bool = False
    cursor_status: str | None = None
    cursor_reason: str | None = None
    deferred_for_run: bool = False
    deferred_reason: str | None = None
    zero_usable_capture_streak: int = 0
    last_raw_row_count: int = 0
    last_output_row_count: int = 0
    last_connectable_count: int = 0
    last_already_pending_count: int = 0
    last_state_counts: dict[str, int] = Field(default_factory=dict)
    last_note: str | None = None


class SourceScanProgressLedger(AppModel):
    sources: dict[str, SourceScanProgress] = Field(default_factory=dict)


class RunTimingEvent(AppModel):
    at: datetime = Field(default_factory=now_utc)
    phase: str
    source: str | None = None
    duration_ms: int
    detail: str | None = None


class CaptureRecommendation(AppModel):
    pages: int
    stop_after_connectable: int
    buffer: int
    reason: str
    playwriter_timeout_ms: int


class NextSource(AppModel):
    name: str
    quota: int
    verified: int
    remaining_for_source: int
    remaining_for_run: int
    fallback: bool


class OperatorPlan(AppModel):
    action: str
    source: str | None = None
    remaining: int | None = None
    available: int | None = None
    capture: CaptureRecommendation | None = None
    resume_url: str | None = None
    cursor: SourceCaptureCursor | None = None
    name: str | None = None
    profile_url: str | None = None
    real_send_capacity_remaining: int | None = None
    review_checkpoint: LeadReviewCheckpoint | None = None
    reason: str | None = None


class SourceYieldStats(AppModel):
    source: str
    raw_row_count: int
    connectable_count: int
    already_pending_count: int
    email_required_skips: int
    reverted_connect_count: int
    pending_sends: int
    connectable_yield: float | None
    recommendation: str


class CandidateReservoir(AppModel):
    observations: list[CandidateObservation] = Field(default_factory=list)
    updated_at: datetime | None = None

    def available_for_run_source(self, run: Run, source: str) -> list[CandidateObservation]:
        result: list[CandidateObservation] = []
        for observation in self.observations:
            if observation.source != source or observation.menu_state != "connectable":
                continue
            if run.has_candidate_event_for_observation(observation):
                continue
            exists_in_run = any(
                same_observation_identity(existing, observation) for existing in run.observations
            )
            if not exists_in_run:
                result.append(observation)
        return result


class Run(AppModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    date: Date = Field(default_factory=today)
    target: int
    max_real_sends: int
    state: RunState = RunState.STARTED
    sources: list[SourcePlan] = Field(default_factory=list)
    start_audit: int | None = None
    latest_audit: int | None = None
    audits: list[AuditEvent] = Field(default_factory=list)
    candidates: list[CandidateEvent] = Field(default_factory=list)
    observations: list[CandidateObservation] = Field(default_factory=list)
    capture_cursors: dict[str, SourceCaptureCursor] = Field(default_factory=dict)
    timings: list[RunTimingEvent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    lead_review_checkpoint: LeadReviewCheckpoint | None = None
    browser_incidents: list[BrowserIncident] = Field(default_factory=list)
    active_browser_incident_id: uuid.UUID | None = None
    blocked_resume_at: datetime | None = None
    allow_fallback_sources: bool = True
    carry_over_shortfall: bool = True
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    def normalize(self) -> None:
        if self.max_real_sends == 0:
            self.max_real_sends = self.target
        if not self.sources:
            self.sources = default_sources(self.target)

    def mark_updated(self) -> None:
        self.updated_at = now_utc()

    def active_browser_incident(self) -> BrowserIncident | None:
        if self.active_browser_incident_id is None:
            return None
        return next(
            (
                incident
                for incident in reversed(self.browser_incidents)
                if incident.id == self.active_browser_incident_id
            ),
            None,
        )

    def verified_count(self) -> int:
        return sum(
            1 for candidate in self.candidates if candidate.status in TARGET_COUNTED_SEND_STATUSES
        )

    def provisional_count(self) -> int:
        return sum(
            1
            for candidate in self.candidates
            if candidate.status == CandidateStatus.PENDING_PROVISIONAL
        )

    def reverted_connect_count(self) -> int:
        return sum(
            1
            for candidate in self.candidates
            if candidate.status == CandidateStatus.REVERTED_CONNECT
        )

    def real_send_attempt_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate_counts_as_real_send(candidate))

    def source_real_send_attempt_count(self, source: str) -> int:
        return sum(
            1
            for candidate in self.candidates
            if candidate.source == source and candidate_counts_as_real_send(candidate)
        )

    def active_send_count(self) -> int:
        return self.verified_count() + self.provisional_count()

    def source_active_send_count(self, source: str) -> int:
        return sum(
            1
            for candidate in self.candidates
            if candidate.source == source
            and (
                candidate.status in TARGET_COUNTED_SEND_STATUSES
                or candidate.status == CandidateStatus.PENDING_PROVISIONAL
            )
        )

    def send_attempt_goal(self) -> int:
        return min(self.target, self.max_real_sends)

    def audited_delta(self) -> int | None:
        if self.start_audit is None or self.latest_audit is None:
            return None
        return self.latest_audit - self.start_audit

    def source_verified_count(self, source: str) -> int:
        return sum(
            1
            for candidate in self.candidates
            if candidate.source == source and candidate.status in TARGET_COUNTED_SEND_STATUSES
        )

    def source_index(self, source: str) -> int | None:
        for index, plan in enumerate(self.sources):
            if plan.name == source:
                return index
        return None

    def source_quota(self, source: str) -> int | None:
        index = self.source_index(source)
        if index is None:
            return None
        return self.source_quota_with_carryover(index)

    def primary_shortfall_before(self, source_index: int) -> int:
        total = 0
        for plan in self.sources[:source_index]:
            if plan.fallback:
                continue
            active = self.source_active_send_count(plan.name)
            if plan.target > active:
                total += plan.target - active
        return total

    def source_quota_with_carryover(self, source_index: int) -> int:
        source = self.sources[source_index]
        if source.fallback:
            remaining = self.target - min(self.target, self.verified_count())
            return max(remaining, source.target)
        if not self.carry_over_shortfall:
            return source.target
        return source.target + self.primary_shortfall_before(source_index)

    def next_source(self) -> NextSource | None:
        if self.state in {
            RunState.NEEDS_BROWSER_INSPECTION,
            RunState.NEEDS_REAUDIT,
            RunState.DONE,
            RunState.BLOCKED,
        }:
            return None
        send_attempt_goal = self.send_attempt_goal()
        total_remaining = send_attempt_goal - min(send_attempt_goal, self.active_send_count())
        if total_remaining == 0:
            return None
        for index, source in enumerate(self.sources):
            if source.exhausted:
                continue
            if self.source_is_deferred_for_run(source.name):
                continue
            if source.fallback and not self.allow_fallback_sources:
                continue
            quota = self.source_quota_with_carryover(index)
            active = self.source_active_send_count(source.name)
            if source.fallback or active < quota:
                if source.fallback:
                    remaining_for_source = total_remaining
                else:
                    remaining_for_source = min(quota - min(quota, active), total_remaining)
                return NextSource(
                    name=source.name,
                    quota=quota,
                    verified=self.source_verified_count(source.name),
                    remaining_for_source=remaining_for_source,
                    remaining_for_run=total_remaining,
                    fallback=source.fallback,
                )
        return None

    def has_candidate_event_for_observation(self, observation: CandidateObservation) -> bool:
        return any(
            candidate_matches_observation(candidate, observation) for candidate in self.candidates
        )

    def next_connectable_observation(self) -> CandidateObservation | None:
        next_source = self.next_source()
        if next_source is None:
            return None
        return self.next_connectable_observation_for_source(next_source.name)

    def next_connectable_observation_for_source(self, source: str) -> CandidateObservation | None:
        if self.source_is_filled_or_closed(source):
            return None
        for observation in self.observations:
            if (
                observation.source == source
                and observation.menu_state == "connectable"
                and not self.has_candidate_event_for_observation(observation)
            ):
                return observation
        return None

    def source_is_filled_or_closed(self, source: str) -> bool:
        if self.active_send_count() >= self.send_attempt_goal():
            return True
        index = self.source_index(source)
        if index is None:
            return False
        plan = self.sources[index]
        if plan.fallback:
            return plan.exhausted
        return (
            plan.exhausted
            or self.source_is_deferred_for_run(source)
            or self.source_active_send_count(source) >= self.source_quota_with_carryover(index)
        )

    def source_is_fallback(self, source: str) -> bool:
        return any(plan.name == source and plan.fallback for plan in self.sources)

    def source_is_deferred_for_run(self, source: str) -> bool:
        cursor = self.capture_cursors.get(source)
        return bool(cursor and cursor.deferred_for_run)

    def deferred_source_reasons(self) -> list[str]:
        reasons: list[str] = []
        for source in self.sources:
            cursor = self.capture_cursors.get(source.name)
            if not cursor or not cursor.deferred_for_run:
                continue
            reason = cursor.deferred_reason or cursor.cursor_reason or cursor.cursor_status
            reasons.append(f"{source.name}: {reason or 'deferred for this run'}")
        return reasons

    def exact_source_shortfalls(self) -> list[str]:
        if self.carry_over_shortfall:
            return []
        return [
            f"{source.name}: {self.source_active_send_count(source.name)}/{source.target} active"
            for source in self.sources
            if not source.fallback
            and self.source_active_send_count(source.name) < source.target
        ]

    def real_send_capacity_remaining(self) -> int:
        active = self.active_send_count()
        if active >= self.max_real_sends:
            return 0
        return self.max_real_sends - active

    def final_audit_is_short(self) -> bool:
        if self.verified_count() < self.target or self.state in {RunState.DONE, RunState.BLOCKED}:
            return False
        delta = self.audited_delta()
        return delta is None or delta < self.target

    def preserve_for_audit_top_up(self, observation: CandidateObservation) -> bool:
        return (
            self.final_audit_is_short()
            and self.source_is_fallback(observation.source)
            and observation.menu_state == "connectable"
        )

    def has_top_up_blocking_event_for_observation(self, observation: CandidateObservation) -> bool:
        return any(
            candidate_matches_observation(candidate, observation)
            and not is_auto_stale_skip(candidate)
            for candidate in self.candidates
        )

    def capture_recommendation(self, source: str, remaining: int) -> CaptureRecommendation:
        source_plan = next((plan for plan in self.sources if plan.name == source), None)
        if source_plan is None:
            return standard_capture_recommendation(remaining)
        stats = source_yield_stats_for_run(self, source_plan)
        attempted = stats.pending_sends + stats.email_required_skips
        high_email_required = attempted >= 3 and stats.email_required_skips / attempted >= 0.30
        thin_capture_yield = (
            stats.raw_row_count >= 25
            and stats.connectable_yield is not None
            and stats.connectable_yield <= 0.10
        )
        cursor = self.capture_cursors.get(source)
        has_resume_url = bool(cursor and cursor.resume_url)
        if high_email_required:
            recommendation = expanded_capture_recommendation(remaining, "high-email-required")
        elif thin_capture_yield:
            recommendation = expanded_capture_recommendation(remaining, "thin-capture-yield")
        else:
            recommendation = standard_capture_recommendation(remaining)
        if has_resume_url or recommendation.pages >= 5:
            recommendation.playwriter_timeout_ms = 90000
        return recommendation

    def operator_plan_with_reservoir(
        self, reservoir: CandidateReservoir | None = None
    ) -> OperatorPlan:
        if self.state == RunState.NEEDS_BROWSER_INSPECTION:
            incident = self.active_browser_incident()
            detail = f"; incident={incident.incident_path}" if incident is not None else ""
            return OperatorPlan(
                action="browser-inspection-required",
                reason=f"run is paused for bounded browser inspection{detail}",
            )
        if self.state == RunState.NEEDS_REAUDIT:
            return OperatorPlan(action="reaudit", reason="run is paused in NEEDS_REAUDIT")
        if self.state == RunState.BLOCKED:
            return OperatorPlan(
                action="blocked", reason="run is blocked by the latest guarded send result"
            )
        if self.lead_review_checkpoint is not None:
            return OperatorPlan(
                action="review-required",
                review_checkpoint=self.lead_review_checkpoint,
                reason="normal non-terminal lead review checkpoint is awaiting decisions",
            )
        if self.verified_count() >= self.target:
            return OperatorPlan(action="final-audit")
        if self.active_send_count() >= self.send_attempt_goal():
            return OperatorPlan(
                action="final-audit",
                reason=(
                    f"active sends reached {self.active_send_count()}/{self.send_attempt_goal()}"
                ),
            )
        candidate = self.next_connectable_observation()
        if candidate is not None:
            if self.real_send_capacity_remaining() == 0:
                return OperatorPlan(
                    action="blocked",
                    reason=(
                        f"active-send cap reached: {self.active_send_count()}/"
                        f"{self.max_real_sends} active sends "
                        f"({self.real_send_attempt_count()} real attempts recorded)"
                    ),
                )
            return OperatorPlan(
                action="send-candidate",
                source=candidate.source,
                name=candidate.name,
                profile_url=candidate.profile_url,
                real_send_capacity_remaining=self.real_send_capacity_remaining(),
            )
        next_source = self.next_source()
        if next_source is not None:
            if reservoir is not None:
                available = len(reservoir.available_for_run_source(self, next_source.name))
                if available > 0:
                    return OperatorPlan(
                        action="use-reservoir",
                        source=next_source.name,
                        remaining=next_source.remaining_for_source,
                        available=available,
                    )
            cursor = self.capture_cursors.get(next_source.name)
            if cursor and cursor.end_of_results:
                return OperatorPlan(
                    action="source-exhausted",
                    source=next_source.name,
                    remaining=next_source.remaining_for_source,
                    cursor=cursor,
                    reason="source cursor is already at end of results",
                )
            return OperatorPlan(
                action="capture-source",
                source=next_source.name,
                remaining=next_source.remaining_for_source,
                capture=self.capture_recommendation(
                    next_source.name, next_source.remaining_for_source
                ),
                resume_url=cursor.resume_url if cursor else None,
                cursor=cursor,
            )
        deferred_reasons = self.deferred_source_reasons()
        if deferred_reasons:
            return OperatorPlan(
                action="blocked",
                reason=(
                    "no connectable candidate and all remaining sources are deferred for this run: "
                    + "; ".join(deferred_reasons)
                ),
            )
        exact_shortfalls = self.exact_source_shortfalls()
        if exact_shortfalls:
            return OperatorPlan(
                action="blocked",
                reason=(
                    "exact source targets remain incomplete and cannot borrow quota: "
                    + "; ".join(exact_shortfalls)
                ),
            )
        return OperatorPlan(
            action="blocked", reason="no connectable candidate and no available source"
        )

    def operator_plan(self) -> OperatorPlan:
        return self.operator_plan_with_reservoir(None)

    def sent_invitation_events(self) -> list[CandidateEvent]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.status
            in {
                CandidateStatus.PENDING,
                CandidateStatus.ACCEPTED,
                CandidateStatus.AUDIT_TOP_UP,
            }
        ]


FOUNDER_OWNER_BUYERS_SOURCE = "Consulting - Founder Owner Buyers"
OPERATIONS_LEADER_BUYERS_SOURCE = "Consulting - Operations Leader Buyers"
TRUSTED_REFERRAL_PARTNERS_SOURCE = "Consulting - Trusted Referral Partners"

FOUNDER_OWNER_BUYERS_LEAD_LIST = "Accepted - Founder Owner Buyers"
OPERATIONS_LEADER_BUYERS_LEAD_LIST = "Accepted - Operations Leader Buyers"
TRUSTED_REFERRAL_PARTNERS_LEAD_LIST = "Accepted - Trusted Referral Partners"
BUSINESS_SYSTEMS_WATCHLIST = "Watch - Business Systems Prospects"

APPROVED_RELATIONSHIP_ROLE_BY_SOURCE: dict[str, RelationshipRole] = {
    FOUNDER_OWNER_BUYERS_SOURCE: RelationshipRole.BUYER,
    OPERATIONS_LEADER_BUYERS_SOURCE: RelationshipRole.BUYER,
    TRUSTED_REFERRAL_PARTNERS_SOURCE: RelationshipRole.REFERRAL_PARTNER,
}

ACCEPTED_LEAD_LIST_BY_SOURCE: dict[str, str] = {
    FOUNDER_OWNER_BUYERS_SOURCE: FOUNDER_OWNER_BUYERS_LEAD_LIST,
    OPERATIONS_LEADER_BUYERS_SOURCE: OPERATIONS_LEADER_BUYERS_LEAD_LIST,
    TRUSTED_REFERRAL_PARTNERS_SOURCE: TRUSTED_REFERRAL_PARTNERS_LEAD_LIST,
}

DEFAULT_SOURCE_MIX: list[tuple[str, int]] = [
    (FOUNDER_OWNER_BUYERS_SOURCE, 15),
    (OPERATIONS_LEADER_BUYERS_SOURCE, 10),
    (TRUSTED_REFERRAL_PARTNERS_SOURCE, 5),
]


def default_sources(target: int) -> list[SourcePlan]:
    default_target = sum(weight for _, weight in DEFAULT_SOURCE_MIX)
    allocated: list[tuple[str, int]] = []
    if target == default_target:
        allocated = list(DEFAULT_SOURCE_MIX)
    else:
        total = 0
        for name, weight in DEFAULT_SOURCE_MIX:
            count = int(target * weight / default_target)
            allocated.append((name, count))
            total += count
        remaining = target - total
        updated: list[tuple[str, int]] = []
        for name, count in allocated:
            if remaining > 0:
                count += 1
                remaining -= 1
            updated.append((name, count))
        allocated = updated
    return [SourcePlan(name=name, target=count) for name, count in allocated]


def sources_for_per_source_target(per_source_target: int) -> list[SourcePlan]:
    return [SourcePlan(name=name, target=per_source_target) for name, _weight in DEFAULT_SOURCE_MIX]


def target_for_per_source_target(per_source_target: int) -> int:
    return per_source_target * len(DEFAULT_SOURCE_MIX)


def new_run(
    target: int,
    run_date: Date | None = None,
    max_real_sends: int | None = None,
    *,
    sources: list[SourcePlan] | None = None,
    allow_fallback_sources: bool = True,
    carry_over_shortfall: bool = True,
) -> Run:
    effective_date = run_date or today()
    return Run(
        date=effective_date,
        target=target,
        max_real_sends=target if max_real_sends is None else max_real_sends,
        sources=sources or default_sources(target),
        allow_fallback_sources=allow_fallback_sources,
        carry_over_shortfall=carry_over_shortfall,
    )


def normalize_linkedin_url(value: str) -> str:
    trimmed = value.strip()
    parsed = urlparse(trimmed)
    if parsed.scheme and parsed.netloc:
        path = parsed.path
        if parsed.hostname in {"www.linkedin.com", "linkedin.com"} and path.startswith(
            "/sales/lead/"
        ):
            lead_path = path.removeprefix("/sales/lead/")
            profile_id = lead_path.split(",", maxsplit=1)[0].strip()
            if profile_id:
                path = "/sales/lead/" + profile_id
        normalized = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
        return normalized.rstrip("/")
    head = trimmed.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    marker = "/sales/lead/"
    if marker in head and (
        head.startswith("https://www.linkedin.com/sales/lead/")
        or head.startswith("https://linkedin.com/sales/lead/")
    ):
        prefix = head[: head.index(marker) + len(marker)]
        lead_path = head.removeprefix(prefix)
        profile_id = lead_path.split(",", maxsplit=1)[0].strip()
        if profile_id:
            head = prefix + profile_id
    return head.rstrip("/")


def candidate_key(source: str, name: str, profile_url: str | None) -> str:
    normalized = normalize_linkedin_url(profile_url) if profile_url else ""
    return f"{source.strip()}|{name.strip()}|{normalized}"


def lead_key_for_values(profile_url: str | None, sales_profile_urn: str | None, name: str) -> str:
    sales_profile_url = ""
    if profile_url:
        normalized = normalize_linkedin_url(profile_url)
        if "/sales/lead/" in normalized:
            sales_profile_url = normalized
        else:
            return f"linkedin:{normalized}"
    if not sales_profile_url and sales_profile_urn:
        derived = sales_profile_urn_to_lead_url(sales_profile_urn)
        if derived:
            sales_profile_url = normalize_linkedin_url(derived)
    if sales_profile_url:
        return f"linkedin:{sales_profile_url}"
    name_key = re.sub(r"\s+", " ", name.strip().casefold())
    return f"name:{name_key}"


def lead_key_for_observation(observation: CandidateObservation) -> str:
    return lead_key_for_values(
        observation.profile_url, observation.sales_profile_urn, observation.name
    )


def candidate_matches_observation(
    candidate: CandidateEvent, observation: CandidateObservation
) -> bool:
    if candidate.profile_url and observation.profile_url:
        return normalize_linkedin_url(candidate.profile_url) == normalize_linkedin_url(
            observation.profile_url
        )
    return candidate.name == observation.name and candidate.source == observation.source


def same_observation_identity(left: CandidateObservation, right: CandidateObservation) -> bool:
    if left.profile_url and right.profile_url:
        return normalize_linkedin_url(left.profile_url) == normalize_linkedin_url(right.profile_url)
    return left.source == right.source and left.name == right.name


def is_auto_stale_skip(candidate: CandidateEvent) -> bool:
    return (
        candidate.status == CandidateStatus.SKIPPED
        and candidate.note is not None
        and "auto-skipped stale imported candidate" in candidate.note
    )


def observation_key(observation: CandidateObservation) -> str:
    profile = normalize_linkedin_url(observation.profile_url) if observation.profile_url else ""
    return f"{observation.source}\0{observation.name}\0{profile}"


def standard_capture_recommendation(remaining: int) -> CaptureRecommendation:
    stop_after_connectable = min(max(remaining * 3, 10), 25) if remaining > 0 else 0
    buffer = max(0, stop_after_connectable - remaining)
    pages = 5 if stop_after_connectable > 10 else 3
    return CaptureRecommendation(
        pages=pages,
        stop_after_connectable=stop_after_connectable,
        buffer=buffer,
        reason="approval-yield-buffer",
        playwriter_timeout_ms=45000,
    )


def expanded_capture_recommendation(remaining: int, reason: str) -> CaptureRecommendation:
    stop_after_connectable = 25 if remaining > 0 else 0
    buffer = max(0, stop_after_connectable - remaining)
    return CaptureRecommendation(
        pages=5,
        stop_after_connectable=stop_after_connectable,
        buffer=buffer,
        reason=reason,
        playwriter_timeout_ms=90000,
    )


def source_yield_stats_for_run(run: Run, source: SourcePlan) -> SourceYieldStats:
    cursor = run.capture_cursors.get(source.name)
    if cursor is not None:
        raw_row_count = cursor.raw_row_count
        connectable_count = cursor.connectable_count
        already_pending_count = cursor.already_pending_count
    else:
        raw_row_count = 0
        connectable_count = sum(
            1
            for observation in run.observations
            if observation.source == source.name and observation.menu_state == "connectable"
        )
        already_pending_count = 0
    email_required_skips = sum(
        1
        for candidate in run.candidates
        if candidate.source == source.name
        and candidate.status == CandidateStatus.SKIPPED
        and candidate.note is not None
        and "email-required" in candidate.note.lower()
    )
    reverted_connect_count = sum(
        1
        for candidate in run.candidates
        if candidate.source == source.name and candidate.status == CandidateStatus.REVERTED_CONNECT
    )
    pending_sends = run.source_verified_count(source.name)
    yield_value = connectable_count / raw_row_count if raw_row_count > 0 else None
    recommendation = "no capture data"
    attempted = pending_sends + email_required_skips + reverted_connect_count
    if attempted >= 3 and email_required_skips / attempted >= 0.30:
        recommendation = "high-email-required: capture more candidates before retrying source"
    elif attempted >= 3 and reverted_connect_count / attempted >= 0.50:
        recommendation = "not-durable: pause source until send confirmation behavior is understood"
    elif yield_value is not None:
        if raw_row_count >= 50 and yield_value <= 0.05:
            recommendation = "low-yield: consider reservoir/fallback before deeper capture"
        elif raw_row_count >= 25 and yield_value <= 0.10:
            recommendation = "thin: capture with a small buffer and be ready to carry over"
        else:
            recommendation = "ok"
    return SourceYieldStats(
        source=source.name,
        raw_row_count=raw_row_count,
        connectable_count=connectable_count,
        already_pending_count=already_pending_count,
        email_required_skips=email_required_skips,
        reverted_connect_count=reverted_connect_count,
        pending_sends=pending_sends,
        connectable_yield=yield_value,
        recommendation=recommendation,
    )


def source_yield_report(run: Run) -> list[SourceYieldStats]:
    return [source_yield_stats_for_run(run, source) for source in run.sources]


def low_yield_source_names(run: Run, min_raw_rows: int, max_connectable_yield: float) -> list[str]:
    return [
        stats.source
        for stats in source_yield_report(run)
        if stats.raw_row_count >= min_raw_rows
        and stats.connectable_yield is not None
        and stats.connectable_yield <= max_connectable_yield
    ]


class SalesNavCaptureMenuLabel(AppModel):
    text: str | None = None
    aria: str | None = None


class SalesNavCaptureLink(AppModel):
    text: str | None = None
    aria: str | None = None
    href: str | None = None


class SalesNavCaptureRow(AppModel):
    index: int
    page_url: str | None = Field(default=None, validation_alias=AliasChoices("page_url", "pageUrl"))
    name: str | None = None
    text: str | None = None
    profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_url", "profileUrl")
    )
    public_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("public_profile_url", "publicProfileUrl")
    )
    scroll_urn: str | None = Field(
        default=None, validation_alias=AliasChoices("scroll_urn", "scrollUrn")
    )
    visible_state: Any = Field(
        default=None, validation_alias=AliasChoices("visible_state", "visibleState")
    )
    api_state: Any = Field(default=None, validation_alias=AliasChoices("api_state", "apiState"))
    menu_state: str | None = Field(
        default=None, validation_alias=AliasChoices("menu_state", "menuState")
    )
    menu_labels: list[SalesNavCaptureMenuLabel] = Field(
        default_factory=list, validation_alias=AliasChoices("menu_labels", "menuLabels")
    )
    links: list[SalesNavCaptureLink] = Field(default_factory=list)
    row_html_path: str | None = Field(
        default=None, validation_alias=AliasChoices("row_html_path", "rowHtmlPath")
    )


class SalesNavCapturePage(AppModel):
    url: str | None = None
    page_label: str | None = Field(
        default=None, validation_alias=AliasChoices("page_label", "pageLabel")
    )


class SalesNavCapture(AppModel):
    captured_at: str | None = Field(
        default=None, validation_alias=AliasChoices("captured_at", "capturedAt")
    )
    source: str | None = None
    url: str | None = None
    resume_url: str | None = Field(
        default=None, validation_alias=AliasChoices("resume_url", "resumeUrl")
    )
    start_url: str | None = Field(
        default=None, validation_alias=AliasChoices("start_url", "startUrl")
    )
    last_scanned_url: str | None = Field(
        default=None, validation_alias=AliasChoices("last_scanned_url", "lastScannedUrl")
    )
    next_url: str | None = Field(default=None, validation_alias=AliasChoices("next_url", "nextUrl"))
    next_page_available: bool | None = Field(
        default=None, validation_alias=AliasChoices("next_page_available", "nextPageAvailable")
    )
    end_of_results: bool = Field(
        default=False, validation_alias=AliasChoices("end_of_results", "endOfResults")
    )
    cursor_status: str | None = Field(
        default=None, validation_alias=AliasChoices("cursor_status", "cursorStatus")
    )
    cursor_reason: str | None = Field(
        default=None, validation_alias=AliasChoices("cursor_reason", "cursorReason")
    )
    warnings: list[str] = Field(default_factory=list)
    page: SalesNavCapturePage | None = None
    pages: list[SalesNavCapturePage] = Field(default_factory=list)
    state_counts: dict[str, int] = Field(
        default_factory=dict, validation_alias=AliasChoices("state_counts", "stateCounts")
    )
    api_state: Any = Field(default=None, validation_alias=AliasChoices("api_state", "apiState"))
    raw_row_count: int | None = Field(
        default=None, validation_alias=AliasChoices("raw_row_count", "rawRowCount")
    )
    output_row_count: int | None = Field(
        default=None, validation_alias=AliasChoices("output_row_count", "outputRowCount")
    )
    rows: list[SalesNavCaptureRow] = Field(default_factory=list)


class SalesNavAuditInvitation(AppModel):
    name: str
    public_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("public_profile_url", "publicProfileUrl")
    )
    public_identifier: str | None = Field(
        default=None, validation_alias=AliasChoices("public_identifier", "publicIdentifier")
    )
    invitation_urn: str | None = Field(
        default=None, validation_alias=AliasChoices("invitation_urn", "invitationUrn")
    )
    profile_urn: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_urn", "profileUrn")
    )
    row_index: int | None = Field(
        default=None, validation_alias=AliasChoices("row_index", "rowIndex")
    )


class SalesNavAudit(AppModel):
    people_count: int = Field(validation_alias=AliasChoices("people_count", "peopleCount"))
    recent_names: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("recent_names", "recentNames")
    )
    invitations: list[SalesNavAuditInvitation] = Field(default_factory=list)
    loaded_count: int | None = Field(
        default=None, validation_alias=AliasChoices("loaded_count", "loadedCount")
    )
    requested_load_more: int | None = Field(
        default=None, validation_alias=AliasChoices("requested_load_more", "requestedLoadMore")
    )
    load_more_clicks: int | None = Field(
        default=None, validation_alias=AliasChoices("load_more_clicks", "loadMoreClicks")
    )
    load_more_exhausted: bool | None = Field(
        default=None, validation_alias=AliasChoices("load_more_exhausted", "loadMoreExhausted")
    )
    warnings: list[str] = Field(default_factory=list)


class SalesNavSendCandidate(AppModel):
    source: str
    name: str
    profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_url", "profileUrl")
    )


class SalesNavSendResult(AppModel):
    candidate: SalesNavSendCandidate
    status: str
    send: Any = None
    public_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("public_profile_url", "publicProfileUrl")
    )

    def to_candidate_status(self) -> tuple[CandidateStatus, str]:
        if self.status == "uncertain-browser-exception":
            return (
                CandidateStatus.PENDING_PROVISIONAL,
                "browser transport failed during a real send; outcome is uncertain",
            )
        if self.status == "pending-provisional":
            return (
                CandidateStatus.PENDING_PROVISIONAL,
                "salesnav-send-one saw immediate Connect - Pending; durable check required",
            )
        if self.status == "pending-verified":
            return (
                CandidateStatus.PENDING_PROVISIONAL,
                "salesnav-send-one saw legacy immediate Connect - Pending; durable check required",
            )
        if self.status == "already-pending":
            return CandidateStatus.ALREADY_PENDING, "salesnav-send-one found already pending"
        if self.status == "email-required":
            return (
                CandidateStatus.SKIPPED,
                "salesnav-send-one stopped on email-required invite flow",
            )
        return (
            CandidateStatus.FAILED,
            f"salesnav-send-one status {self.status}; {self.send or 'no send detail'}",
        )


def sales_profile_urn_to_lead_url(urn: str) -> str | None:
    start = urn.find("(")
    if start < 0 or not urn.endswith(")"):
        return None
    tuple_value = urn[start + 1 : -1]
    parts = [part.strip() for part in tuple_value.split(",")]
    if len(parts) != 3 or any(part == "" for part in parts):
        return None
    return f"https://www.linkedin.com/sales/lead/{parts[0]},{parts[1]},{parts[2]}"


def capture_state_count(capture: SalesNavCapture, state: str) -> int:
    if state in capture.state_counts:
        return capture.state_counts[state]
    return sum(1 for row in capture.rows if row.menu_state == state)


def _same_url(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return left == right


def validated_capture_next_url(
    capture: SalesNavCapture, last_scanned_url: str | None
) -> str | None:
    if capture.next_url is None:
        return None
    if _same_url(capture.next_url, last_scanned_url):
        return None
    return capture.next_url


def capture_cursor_status(
    capture: SalesNavCapture, last_scanned_url: str | None, next_url: str | None
) -> SourceCursorStatus:
    if capture.cursor_status:
        try:
            raw_status = SourceCursorStatus(capture.cursor_status)
            if raw_status == SourceCursorStatus.ADVANCED and next_url is None:
                return SourceCursorStatus.STALLED_NAVIGATION
            return raw_status
        except ValueError:
            pass
    if capture.end_of_results:
        return SourceCursorStatus.END_OF_RESULTS
    if next_url:
        return SourceCursorStatus.ADVANCED
    if capture.next_page_available is True and _same_url(capture.next_url, last_scanned_url):
        return SourceCursorStatus.STALLED_NAVIGATION
    if capture.next_page_available is True:
        return SourceCursorStatus.STALLED_NAVIGATION
    return SourceCursorStatus.NO_NEXT_URL


def capture_to_observations(
    source: str, capture: SalesNavCapture, only_connectable: bool
) -> list[CandidateObservation]:
    observations: list[CandidateObservation] = []
    for row in capture.rows:
        if row.name is None or row.name.strip() == "":
            continue
        menu_state = row.menu_state or "unknown"
        if only_connectable and menu_state != "connectable":
            continue
        labels: list[str] = []
        for label in row.menu_labels:
            value = label.text or label.aria
            if value and value.strip():
                labels.append(value.strip())
        profile_url = row.profile_url
        if profile_url is None and row.scroll_urn:
            profile_url = sales_profile_urn_to_lead_url(row.scroll_urn)
        observations.append(
            CandidateObservation(
                imported_at=now_utc(),
                captured_at=capture.captured_at,
                source=source,
                index=row.index,
                name=row.name.strip(),
                profile_url=profile_url,
                public_profile_url=row.public_profile_url,
                search_url=(
                    row.page_url or capture.url or capture.last_scanned_url or capture.start_url
                ),
                sales_profile_urn=row.scroll_urn,
                text=row.text,
                visible_state=row.visible_state,
                menu_state=menu_state,
                menu_labels=labels,
                links=[link.model_dump(mode="json") for link in row.links],
                row_html_path=row.row_html_path,
            )
        )
    return observations


def update_capture_cursor(run: Run, source: str, capture: SalesNavCapture) -> None:
    last_page = capture.page or (capture.pages[-1] if capture.pages else None)
    last_scanned_url = (
        capture.last_scanned_url or capture.url or (last_page.url if last_page else None)
    )
    next_url = validated_capture_next_url(capture, last_scanned_url)
    cursor_status = capture_cursor_status(capture, last_scanned_url, next_url)
    if cursor_status == SourceCursorStatus.END_OF_RESULTS:
        resume_url = None
        next_url = None
    elif cursor_status == SourceCursorStatus.PARTIAL_PAGE:
        resume_url = capture.resume_url or last_scanned_url
        next_url = None
    else:
        resume_url = next_url or capture.resume_url
    captured_pages = len(capture.pages) or (1 if capture.page else 0)
    raw_row_count = (
        capture.raw_row_count if capture.raw_row_count is not None else len(capture.rows)
    )
    output_row_count = (
        capture.output_row_count if capture.output_row_count is not None else len(capture.rows)
    )
    run.capture_cursors[source] = SourceCaptureCursor(
        source=source,
        updated_at=now_utc(),
        captured_at=capture.captured_at,
        resume_url=resume_url,
        start_url=capture.start_url,
        last_scanned_url=last_scanned_url,
        next_url=next_url,
        next_page_available=capture.next_page_available,
        end_of_results=capture.end_of_results,
        cursor_status=cursor_status.value,
        cursor_reason=capture.cursor_reason,
        page_label=last_page.page_label if last_page else None,
        captured_pages=captured_pages,
        raw_row_count=raw_row_count,
        output_row_count=output_row_count,
        connectable_count=capture_state_count(capture, "connectable"),
        already_pending_count=capture_state_count(capture, "already-pending"),
        missing_trigger_count=capture_state_count(capture, "missing-trigger"),
        state_counts=capture.state_counts,
    )


def import_capture(run: Run, capture: SalesNavCapture, only_connectable: bool = False) -> int:
    next_source = run.next_source()
    source = capture.source or (next_source.name if next_source else None)
    if source is None:
        raise ValueError("capture did not include source and run has no next source")
    ensure_known_source(run, source)
    update_capture_cursor(run, source, capture)
    imported = 0
    for observation in capture_to_observations(source, capture, only_connectable):
        existing_index: int | None = None
        for index, existing in enumerate(run.observations):
            if same_observation_identity(existing, observation):
                existing_index = index
                break
        if existing_index is None:
            run.observations.append(observation)
            imported += 1
        else:
            run.observations[existing_index] = observation
    run.mark_updated()
    return imported


def import_capture_into_reservoir(
    reservoir: CandidateReservoir, capture: SalesNavCapture, only_connectable: bool = False
) -> int:
    if capture.source is None:
        raise ValueError("capture did not include source")
    imported = 0
    for observation in capture_to_observations(capture.source, capture, only_connectable):
        existing_index: int | None = None
        for index, existing in enumerate(reservoir.observations):
            if same_observation_identity(existing, observation):
                existing_index = index
                break
        if existing_index is None:
            reservoir.observations.append(observation)
            imported += 1
        else:
            reservoir.observations[existing_index] = observation
    reservoir.updated_at = now_utc()
    return imported


def fill_run_from_reservoir(
    run: Run, reservoir: CandidateReservoir, source: str, limit: int
) -> int:
    ensure_known_source(run, source)
    selected_keys: set[str] = set()
    imported = 0
    for observation in reservoir.available_for_run_source(run, source):
        if imported >= limit:
            break
        observation.imported_at = now_utc()
        selected_keys.add(observation_key(observation))
        run.observations.append(observation)
        imported += 1
    if imported > 0:
        reservoir.observations = [
            observation
            for observation in reservoir.observations
            if observation_key(observation) not in selected_keys
        ]
        reservoir.updated_at = now_utc()
        run.mark_updated()
    return imported


def ensure_known_source(run: Run, source: str) -> None:
    if not any(plan.name == source for plan in run.sources):
        raise ValueError(f"unknown source: {source}")


def apply_audit(run: Run, people_count: int, note: str | None = None) -> None:
    audit = AuditEvent(people_count=people_count, note=note)
    if run.start_audit is None:
        run.start_audit = people_count
        run.state = RunState.START_AUDITED
    elif has_blocking_send_result(run):
        run.state = RunState.BLOCKED
    elif run.state == RunState.NEEDS_REAUDIT:
        run.state = RunState.SENDING
    run.latest_audit = people_count
    run.audits.append(audit)
    run.mark_updated()


def has_blocking_send_result(run: Run) -> bool:
    for event in run.candidates:
        if run.blocked_resume_at is not None and event.at <= run.blocked_resume_at:
            continue
        if (
            event.status == CandidateStatus.FAILED
            and event.note is not None
            and "salesnav-send-one status blocked" in event.note
        ):
            return True
    return False


def is_uncertain_send_status(status: str) -> bool:
    return status.startswith("unverified:") or status == "blocked"


def is_send_noop_status(status: str) -> bool:
    return status in {
        "unverified:clicked-send",
        "unverified:send-not-accepted",
        "unverified:send-button-disabled",
    }


def source_repeated_send_noop(run: Run, source: str, threshold: int) -> bool:
    if threshold == 0:
        return False
    consecutive = 0
    for event in reversed(run.candidates):
        if event.source != source:
            continue
        if (
            event.status in TARGET_COUNTED_SEND_STATUSES
            or event.status == CandidateStatus.AUDIT_TOP_UP
        ):
            return False
        if (
            event.status == CandidateStatus.FAILED
            and event.note
            and any(
                marker in event.note
                for marker in (
                    "unverified:clicked-send",
                    "unverified:send-not-accepted",
                    "unverified:send-button-disabled",
                )
            )
        ):
            consecutive += 1
            if consecutive >= threshold:
                return True
            continue
        return False
    return False


def candidate_counts_as_real_send(candidate: CandidateEvent) -> bool:
    if candidate.status in REAL_SEND_ATTEMPT_STATUSES:
        return True
    note = candidate.note.casefold() if candidate.note is not None else ""
    return candidate.status == CandidateStatus.FAILED and (
        "clicked-send" in note
        or "send-connection-clicked" in note
        or "pending-provisional" in note
        or "pending-verified" in note
        or "immediate connect - pending" in note
    )


def record_send_result(run: Run, result: SalesNavSendResult, path: str) -> CandidateEvent:
    status, status_note = result.to_candidate_status()
    note = f"{status_note}; result={path}"
    event = CandidateEvent(
        at=now_utc(),
        source=result.candidate.source,
        name=result.candidate.name,
        profile_url=result.candidate.profile_url,
        public_profile_url=result.public_profile_url,
        status=status,
        note=note,
    )
    ensure_known_source(run, event.source)
    if status in TARGET_COUNTED_SEND_STATUSES:
        for candidate in run.candidates:
            if (
                candidate.status in TARGET_COUNTED_SEND_STATUSES
                and candidate.name == event.name
                and candidate.profile_url == event.profile_url
            ):
                raise ValueError(f"candidate already recorded as delivered: {event.name}")
    run.candidates.append(event)
    if run.state not in {RunState.DONE, RunState.BLOCKED}:
        run.state = (
            RunState.FINAL_RECONCILE if run.verified_count() >= run.target else RunState.SENDING
        )
    run.mark_updated()
    return event


def record_top_up_send_result(
    run: Run, result: SalesNavSendResult, path: str, note: str | None = None
) -> CandidateEvent:
    status, status_note = result.to_candidate_status()
    if status in TARGET_COUNTED_SEND_STATUSES:
        status = CandidateStatus.AUDIT_TOP_UP
    parts = [status_note]
    if note:
        parts.append(note)
    parts.append(f"result={path}")
    event = CandidateEvent(
        at=now_utc(),
        source=result.candidate.source,
        name=result.candidate.name,
        profile_url=result.candidate.profile_url,
        public_profile_url=result.public_profile_url,
        status=status,
        note="; ".join(parts),
    )
    ensure_known_source(run, event.source)
    run.candidates.append(event)
    run.mark_updated()
    return event


def drain_stale_connectable_candidates(
    run: Run, source_filter: str | None = None
) -> list[CandidateEvent]:
    stale: list[CandidateObservation] = []
    for observation in run.observations:
        if observation.menu_state != "connectable":
            continue
        if source_filter is not None and observation.source != source_filter:
            continue
        if run.preserve_for_audit_top_up(observation):
            continue
        if not run.source_is_filled_or_closed(observation.source):
            continue
        if run.has_candidate_event_for_observation(observation):
            continue
        stale.append(observation)
    events: list[CandidateEvent] = []
    for observation in stale:
        ensure_known_source(run, observation.source)
        quota = run.source_quota(observation.source) or 0
        note = (
            "auto-skipped stale imported candidate after source closed or filled; "
            f"source {run.source_verified_count(observation.source)}/{quota}, "
            f"run {run.verified_count()}/{run.target}"
        )
        event = CandidateEvent(
            at=now_utc(),
            source=observation.source,
            name=observation.name,
            profile_url=observation.profile_url,
            status=CandidateStatus.SKIPPED,
            note=note,
        )
        run.candidates.append(event)
        events.append(event)
    if events:
        run.mark_updated()
    return events


class AcceptanceOutcomeEvent(AppModel):
    at: datetime = Field(default_factory=now_utc)
    status: AcceptanceStatus
    note: str | None = None
    relationship: str | None = None
    evidence: str | None = None
    evidence_grade: AcceptanceEvidenceGrade | None = None
    contract_version: str | None = None


DURABLY_CONFIRMED_ACCEPTED_NOTE = "durably confirmed accepted during send"
WEAK_MESSAGE_ACCEPTED_NOTE = "profile shows first-degree/message evidence"
WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE = (
    "invalidated weak message-based acceptance; sampled profiles were 2nd-degree"
)


class AcceptanceInvitation(AppModel):
    run_id: uuid.UUID
    run_date: Date
    source: str
    name: str
    profile_url: str | None = None
    public_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("public_profile_url", "publicProfileUrl")
    )
    sent_at: datetime
    latest_status: AcceptanceStatus = AcceptanceStatus.SENT
    latest_checked_at: datetime | None = None
    first_observed_accepted_at: datetime | None = None
    last_observed_unaccepted_at: datetime | None = None
    acceptance_observation_precision: AcceptanceObservationPrecision | None = None
    acceptance_evidence_grade: AcceptanceEvidenceGrade | None = None
    current_relationship_status: AcceptanceRelationshipStatus = (
        AcceptanceRelationshipStatus.UNKNOWN
    )
    current_relationship_observed_at: datetime | None = None
    history: list[AcceptanceOutcomeEvent] = Field(default_factory=list)

    def key(self) -> str:
        return candidate_key(self.source, self.name, self.profile_url)


class AcceptanceDailyRun(AppModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    started_at: datetime
    completed_at: datetime
    local_date: Date
    timezone: str
    min_age_days: int
    max_age_days: int | None = None
    eligible: int
    checked: int
    newly_confirmed_accepted: int
    remaining_unresolved: int
    coverage_complete: bool
    blocker: str | None = None


class AcceptanceDailyMetric(AppModel):
    date: Date
    newly_confirmed_accepted: int = 0
    eligible: int | None = None
    checked: int | None = None
    remaining_unresolved: int | None = None
    coverage_complete: bool | None = None
    blocker: str | None = None


class AcceptanceDailyWindowSummary(AppModel):
    days: int
    newly_confirmed_accepted: int
    per_calendar_day: float
    complete_days: int
    missing_or_incomplete_days: int


class AcceptanceOutcomeRow(AppModel):
    source: str
    name: str
    profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_url", "profileUrl")
    )
    status: AcceptanceStatus
    checked_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("checked_at", "checkedAt")
    )
    relationship: str | None = None
    evidence: str | None = None
    evidence_grade: AcceptanceEvidenceGrade | None = Field(
        default=None,
        validation_alias=AliasChoices("evidence_grade", "evidenceGrade"),
    )
    contract_version: str | None = Field(
        default=None,
        validation_alias=AliasChoices("contract_version", "contractVersion"),
    )
    expected_lead_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("expected_lead_id", "expectedLeadId"),
    )
    loaded_lead_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("loaded_lead_id", "loadedLeadId"),
    )
    note: str | None = None


class AcceptanceOutcomeArtifact(AppModel):
    captured_at: str | None = Field(
        default=None, validation_alias=AliasChoices("captured_at", "capturedAt")
    )
    input: str | None = None
    input_sha256: str | None = Field(
        default=None, validation_alias=AliasChoices("input_sha256", "inputSha256")
    )
    count: int | None = None
    offset: int | None = None
    limit: int | None = None
    total_candidates: int | None = Field(
        default=None, validation_alias=AliasChoices("total_candidates", "totalCandidates")
    )
    complete: bool | None = None
    rows: list[AcceptanceOutcomeRow] = Field(default_factory=list)


class AcceptanceListRow(AppModel):
    name: str | None = None
    public_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("public_profile_url", "publicProfileUrl")
    )
    public_identifier: str | None = Field(
        default=None, validation_alias=AliasChoices("public_identifier", "publicIdentifier")
    )
    profile_urn: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_urn", "profileUrn")
    )
    member_id: str | None = Field(
        default=None, validation_alias=AliasChoices("member_id", "memberId")
    )
    row_index: int = Field(
        default=0, validation_alias=AliasChoices("row_index", "rowIndex")
    )

    def identities(self) -> set[str]:
        result: set[str] = set()
        if self.member_id:
            result.add(f"member:{self.member_id}")
        identifier = self.public_identifier or public_profile_identifier(
            self.public_profile_url
        )
        if identifier:
            result.add(f"public:{identifier}")
        return result


class AcceptanceListSection(AppModel):
    url: str
    complete: bool
    loaded_count: int = Field(
        default=0, validation_alias=AliasChoices("loaded_count", "loadedCount")
    )
    load_actions: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("load_actions", "loadActions"),
    )
    stop_reason: str | None = Field(
        default=None, validation_alias=AliasChoices("stop_reason", "stopReason")
    )
    sort_order: str | None = Field(
        default=None, validation_alias=AliasChoices("sort_order", "sortOrder")
    )
    identity_missing_count: int = Field(
        default=0,
        validation_alias=AliasChoices("identity_missing_count", "identityMissingCount"),
    )
    rows: list[AcceptanceListRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AcceptanceListArtifact(AppModel):
    captured_at: datetime = Field(
        default_factory=now_utc,
        validation_alias=AliasChoices("captured_at", "capturedAt"),
    )
    contract_version: str = Field(
        validation_alias=AliasChoices("contract_version", "contractVersion")
    )
    baseline_only: bool = Field(
        default=False, validation_alias=AliasChoices("baseline_only", "baselineOnly")
    )
    connection_delta_complete: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "connection_delta_complete", "connectionDeltaComplete"
        ),
    )
    previous_watermark: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("previous_watermark", "previousWatermark"),
    )
    next_watermark: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("next_watermark", "nextWatermark"),
    )
    pending: AcceptanceListSection
    connections: AcceptanceListSection
    warnings: list[str] = Field(default_factory=list)

    def blocker(self) -> str | None:
        blockers: list[str] = []
        if self.contract_version != "acceptance-list-reconciliation-v1":
            blockers.append(
                f"unexpected acceptance list contract {self.contract_version!r}"
            )
        if not self.pending.complete:
            blockers.append(
                "sent-invitations capture incomplete"
                + (f": {self.pending.stop_reason}" if self.pending.stop_reason else "")
            )
        if not self.connections.complete:
            blockers.append(
                "connections capture incomplete"
                + (
                    f": {self.connections.stop_reason}"
                    if self.connections.stop_reason
                    else ""
                )
            )
        if not self.baseline_only and not self.connection_delta_complete:
            blockers.append("recent-connections capture did not reach the prior watermark")
        return "; ".join(blockers) if blockers else None


class AcceptanceListState(AppModel):
    last_successful_capture_at: datetime | None = None
    watermark: list[str] = Field(default_factory=list)


class AcceptanceListReconciliationSummary(AppModel):
    accepted: int = 0
    pending: int = 0
    unchanged_unknown: int = 0
    missing_identity: int = 0
    ambiguous: int = 0


class AcceptanceImportSummary(AppModel):
    rows: int = 0
    matched: int = 0
    unmatched: int = 0


class AcceptanceHistorySeedSummary(AppModel):
    run_logs: int = 0
    sent_events: int = 0
    seeded: int = 0


class AcceptanceCheckCandidate(AppModel):
    run_id: str
    run_date: Date
    source: str
    name: str
    profile_url: str | None
    sent_at: datetime
    latest_status: AcceptanceStatus
    latest_checked_at: datetime | None
    reconciliation: bool = False


class SavedSearchRow(AppModel):
    saved_search_id: str = Field(validation_alias=AliasChoices("saved_search_id", "savedSearchId"))
    name: str | None = None
    view_url: str | None = Field(default=None, validation_alias=AliasChoices("view_url", "viewUrl"))
    fresh_url: str | None = Field(
        default=None, validation_alias=AliasChoices("fresh_url", "freshUrl")
    )
    fresh_text: str | None = Field(
        default=None, validation_alias=AliasChoices("fresh_text", "freshText")
    )
    row_text: str | None = Field(default=None, validation_alias=AliasChoices("row_text", "rowText"))


class SavedSearchArtifact(AppModel):
    captured_at: str | None = Field(
        default=None, validation_alias=AliasChoices("captured_at", "capturedAt")
    )
    url: str | None = None
    searches: list[SavedSearchRow] = Field(default_factory=list)


class AcceptanceSourceReport(AppModel):
    total_sent: int = 0
    checked: int = 0
    accepted: int = 0
    invalidated: int = 0
    pending: int = 0
    connectable: int = 0
    unknown: int = 0
    blocked: int = 0
    failed: int = 0
    withdrawn: int = 0
    unchecked: int = 0


class AcceptanceReport(AppModel):
    min_age_days: int
    max_age_days: int | None = None
    total_sent: int = 0
    checked: int = 0
    accepted: int = 0
    invalidated: int = 0
    pending: int = 0
    connectable: int = 0
    unknown: int = 0
    blocked: int = 0
    failed: int = 0
    withdrawn: int = 0
    unchecked: int = 0
    by_source: dict[str, AcceptanceSourceReport] = Field(default_factory=dict)
    daily_timezone: str = "America/Argentina/Buenos_Aires"
    daily: list[AcceptanceDailyMetric] = Field(default_factory=list)
    daily_windows: list[AcceptanceDailyWindowSummary] = Field(default_factory=list)

    def add(self, source: str, status: AcceptanceStatus, checked: bool) -> None:
        self.total_sent += 1
        source_report = self.by_source.setdefault(source, AcceptanceSourceReport())
        source_report.total_sent += 1
        if checked:
            self.checked += 1
            source_report.checked += 1
        else:
            self.unchecked += 1
            source_report.unchecked += 1
        if status == AcceptanceStatus.PENDING:
            self.pending += 1
            source_report.pending += 1
        elif status == AcceptanceStatus.ACCEPTED:
            self.accepted += 1
            source_report.accepted += 1
        elif status == AcceptanceStatus.INVALIDATED:
            self.invalidated += 1
            source_report.invalidated += 1
        elif status == AcceptanceStatus.CONNECTABLE:
            self.connectable += 1
            source_report.connectable += 1
        elif status == AcceptanceStatus.WITHDRAWN:
            self.withdrawn += 1
            source_report.withdrawn += 1
        elif status == AcceptanceStatus.UNKNOWN:
            self.unknown += 1
            source_report.unknown += 1
        elif status == AcceptanceStatus.BLOCKED:
            self.blocked += 1
            source_report.blocked += 1
        elif status == AcceptanceStatus.FAILED:
            self.failed += 1
            source_report.failed += 1


class AcceptanceLedger(AppModel):
    invitations: list[AcceptanceInvitation] = Field(default_factory=list)

    def upsert_from_run(self, run: Run) -> int:
        inserted = 0
        for event in run.sent_invitation_events():
            if self.upsert_invitation(run.id, run.date, event):
                inserted += 1
        return inserted

    def upsert_from_events(
        self, run_id: uuid.UUID, run_date: Date, events: list[CandidateEvent]
    ) -> int:
        inserted = 0
        for event in events:
            if event.status not in {
                CandidateStatus.PENDING,
                CandidateStatus.ACCEPTED,
                CandidateStatus.AUDIT_TOP_UP,
            }:
                continue
            if self.upsert_invitation(run_id, run_date, event):
                inserted += 1
        return inserted

    def upsert_invitation(self, run_id: uuid.UUID, run_date: Date, event: CandidateEvent) -> bool:
        key = candidate_key(event.source, event.name, event.profile_url)
        for invitation in self.invitations:
            if invitation.key() == key:
                if event.public_profile_url and not invitation.public_profile_url:
                    invitation.public_profile_url = event.public_profile_url
                if invitation.run_id != run_id and invitation.sent_at > event.at:
                    invitation.run_id = run_id
                    invitation.run_date = run_date
                    invitation.sent_at = event.at
                return False
        self.invitations.append(
            AcceptanceInvitation(
                run_id=run_id,
                run_date=run_date,
                source=event.source,
                name=event.name,
                profile_url=event.profile_url,
                public_profile_url=event.public_profile_url,
                sent_at=event.at,
                latest_status=(
                    AcceptanceStatus.ACCEPTED
                    if event.status == CandidateStatus.ACCEPTED
                    else AcceptanceStatus.SENT
                ),
                latest_checked_at=(event.at if event.status == CandidateStatus.ACCEPTED else None),
                first_observed_accepted_at=(
                    event.at if event.status == CandidateStatus.ACCEPTED else None
                ),
                last_observed_unaccepted_at=(
                    None if event.status == CandidateStatus.ACCEPTED else event.at
                ),
                acceptance_observation_precision=(
                    AcceptanceObservationPrecision.CONTROLLER_CONFIRMATION
                    if event.status == CandidateStatus.ACCEPTED
                    else None
                ),
                acceptance_evidence_grade=(
                    AcceptanceEvidenceGrade.CONTROLLER_CONFIRMED
                    if event.status == CandidateStatus.ACCEPTED
                    else None
                ),
                current_relationship_status=(
                    AcceptanceRelationshipStatus.FIRST_DEGREE
                    if event.status == CandidateStatus.ACCEPTED
                    else AcceptanceRelationshipStatus.UNKNOWN
                ),
                current_relationship_observed_at=(
                    event.at if event.status == CandidateStatus.ACCEPTED else None
                ),
                history=(
                    [
                        AcceptanceOutcomeEvent(
                            at=event.at,
                            status=AcceptanceStatus.ACCEPTED,
                            note="durably confirmed accepted during send",
                            relationship="1st",
                            evidence_grade=AcceptanceEvidenceGrade.CONTROLLER_CONFIRMED,
                            contract_version="network-send-controller-v1",
                        )
                    ]
                    if event.status == CandidateStatus.ACCEPTED
                    else []
                ),
            )
        )
        return True

    def import_outcomes(self, artifact: AcceptanceOutcomeArtifact) -> AcceptanceImportSummary:
        summary = AcceptanceImportSummary()
        for row in artifact.rows:
            summary.rows += 1
            key = candidate_key(row.source, row.name, row.profile_url)
            matched = False
            for invitation in self.invitations:
                if invitation.key() != key:
                    continue
                checked_at = row.checked_at or now_utc()
                sanitized = sanitize_acceptance_outcome(row, invitation)
                event = AcceptanceOutcomeEvent(
                    at=checked_at,
                    status=sanitized.status,
                    note=sanitized.note,
                    relationship=sanitized.relationship,
                    evidence=sanitized.evidence,
                    evidence_grade=sanitized.evidence_grade,
                    contract_version=sanitized.contract_version,
                )
                if (
                    sanitized.status == AcceptanceStatus.ACCEPTED
                    and invitation.first_observed_accepted_at is None
                ):
                    invitation.first_observed_accepted_at = checked_at
                    invitation.last_observed_unaccepted_at = (
                        invitation.last_observed_unaccepted_at
                        or invitation.latest_checked_at
                        or invitation.sent_at
                    )
                    invitation.acceptance_observation_precision = (
                        AcceptanceObservationPrecision.DAILY_SCAN
                    )
                elif (
                    invitation.first_observed_accepted_at is None
                    and sanitized.status
                    in {AcceptanceStatus.PENDING, AcceptanceStatus.CONNECTABLE}
                ):
                    invitation.last_observed_unaccepted_at = checked_at
                invitation.latest_status = sanitized.status
                invitation.latest_checked_at = checked_at
                if sanitized.status == AcceptanceStatus.ACCEPTED:
                    invitation.acceptance_evidence_grade = sanitized.evidence_grade
                    invitation.acceptance_observation_precision = (
                        AcceptanceObservationPrecision.DAILY_SCAN
                    )
                    invitation.current_relationship_status = (
                        AcceptanceRelationshipStatus.FIRST_DEGREE
                    )
                    invitation.current_relationship_observed_at = checked_at
                elif sanitized.relationship == "2nd":
                    invitation.acceptance_evidence_grade = AcceptanceEvidenceGrade.INVALIDATED
                    invitation.current_relationship_status = (
                        AcceptanceRelationshipStatus.SECOND_DEGREE
                    )
                    invitation.current_relationship_observed_at = checked_at
                elif sanitized.status in {
                    AcceptanceStatus.UNKNOWN,
                    AcceptanceStatus.FAILED,
                    AcceptanceStatus.BLOCKED,
                }:
                    invitation.current_relationship_status = (
                        AcceptanceRelationshipStatus.UNKNOWN
                    )
                    invitation.current_relationship_observed_at = checked_at
                invitation.history.append(event)
                summary.matched += 1
                matched = True
                break
            if not matched:
                summary.unmatched += 1
        return summary

    def quarantine_unverified_acceptances(self, *, at: datetime | None = None) -> list[str]:
        current = at or now_utc()
        quarantined: list[str] = []
        for invitation in self.invitations:
            if invitation.latest_status != AcceptanceStatus.ACCEPTED:
                continue
            accepted_event = latest_acceptance_event(invitation)
            grade = acceptance_event_evidence_grade(accepted_event)
            if grade in {
                AcceptanceEvidenceGrade.CONTROLLER_CONFIRMED,
                AcceptanceEvidenceGrade.STRUCTURED_FIRST_DEGREE,
                AcceptanceEvidenceGrade.CONNECTIONS_LIST_FIRST_DEGREE,
            } and accepted_event is not None:
                if accepted_event.evidence_grade is None:
                    accepted_event.evidence_grade = grade
                if accepted_event.contract_version is None:
                    accepted_event.contract_version = (
                        "network-send-controller-v1"
                        if grade == AcceptanceEvidenceGrade.CONTROLLER_CONFIRMED
                        else (
                            "acceptance-list-reconciliation-v1"
                            if grade
                            == AcceptanceEvidenceGrade.CONNECTIONS_LIST_FIRST_DEGREE
                            else "acceptance-relationship-v2"
                        )
                    )
                invitation.acceptance_evidence_grade = grade
                invitation.current_relationship_status = (
                    AcceptanceRelationshipStatus.FIRST_DEGREE
                )
                invitation.current_relationship_observed_at = (
                    invitation.latest_checked_at or accepted_event.at
                )
                continue
            invitation.acceptance_evidence_grade = AcceptanceEvidenceGrade.LEGACY_UNVERIFIED
            if accepted_event is not None:
                accepted_event.evidence_grade = AcceptanceEvidenceGrade.LEGACY_UNVERIFIED
                accepted_event.contract_version = "legacy-unstructured-v1"
            invitation.latest_status = AcceptanceStatus.INVALIDATED
            invitation.current_relationship_status = AcceptanceRelationshipStatus.UNKNOWN
            invitation.current_relationship_observed_at = None
            invitation.history.append(
                AcceptanceOutcomeEvent(
                    at=current,
                    status=AcceptanceStatus.INVALIDATED,
                    relationship=None,
                    evidence_grade=AcceptanceEvidenceGrade.LEGACY_UNVERIFIED,
                    contract_version="acceptance-evidence-quarantine-v1",
                    note=(
                        "legacy acceptance evidence quarantined pending structured "
                        "first-degree reconciliation"
                    ),
                )
            )
            quarantined.append(invitation.key())
        return quarantined

    def legacy_acceptances_for_reconciliation(self) -> list[AcceptanceInvitation]:
        return [
            invitation
            for invitation in self.invitations
            if invitation.acceptance_evidence_grade
            == AcceptanceEvidenceGrade.LEGACY_UNVERIFIED
            and invitation.profile_url is not None
            and (
                invitation.current_relationship_observed_at is None
                or _latest_relationship_contract_was_unavailable(invitation)
            )
        ]

    def clear_unavailable_relationship_observations(self) -> list[str]:
        cleared: list[str] = []
        for invitation in self.invitations:
            if (
                invitation.acceptance_evidence_grade
                == AcceptanceEvidenceGrade.LEGACY_UNVERIFIED
                and invitation.current_relationship_status
                == AcceptanceRelationshipStatus.UNKNOWN
                and invitation.current_relationship_observed_at is not None
                and _latest_relationship_contract_was_unavailable(invitation)
            ):
                invitation.current_relationship_observed_at = None
                cleared.append(invitation.key())
        return cleared

    def weak_message_acceptances(self) -> list[AcceptanceInvitation]:
        result: list[AcceptanceInvitation] = []
        for invitation in self.invitations:
            if invitation.latest_status != AcceptanceStatus.ACCEPTED:
                continue
            accepted_event = latest_acceptance_event(invitation)
            if accepted_event is not None and accepted_event.note == WEAK_MESSAGE_ACCEPTED_NOTE:
                result.append(invitation)
        return result

    def invalidate_weak_message_acceptances(
        self,
        *,
        note: str = WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE,
        at: datetime | None = None,
    ) -> list[str]:
        current = at or now_utc()
        invalidated_keys: list[str] = []
        for invitation in self.weak_message_acceptances():
            accepted_event = latest_acceptance_event(invitation)
            invitation.latest_status = AcceptanceStatus.INVALIDATED
            invitation.latest_checked_at = current
            invitation.acceptance_evidence_grade = AcceptanceEvidenceGrade.INVALIDATED
            invitation.current_relationship_status = AcceptanceRelationshipStatus.UNKNOWN
            invitation.current_relationship_observed_at = current
            invitation.history.append(
                AcceptanceOutcomeEvent(
                    at=current,
                    status=AcceptanceStatus.INVALIDATED,
                    note=note,
                    relationship=accepted_event.relationship if accepted_event else None,
                    evidence=accepted_event.evidence if accepted_event else None,
                    evidence_grade=AcceptanceEvidenceGrade.INVALIDATED,
                    contract_version="legacy-message-invalidation-v1",
                )
            )
            invalidated_keys.append(invitation.key())
        return invalidated_keys

    def eligible_for_check(
        self, min_age_days: int, max_age_days: int | None
    ) -> list[AcceptanceInvitation]:
        current = now_utc()
        result: list[AcceptanceInvitation] = []
        for invitation in self.invitations:
            if invitation.latest_status in {
                AcceptanceStatus.ACCEPTED,
                AcceptanceStatus.INVALIDATED,
                AcceptanceStatus.WITHDRAWN,
            }:
                continue
            if invitation.profile_url is None:
                continue
            age_days = int((current - invitation.sent_at).total_seconds() // 86400)
            if age_days >= min_age_days and (max_age_days is None or age_days <= max_age_days):
                result.append(invitation)
        return result

    def report(
        self,
        min_age_days: int,
        max_age_days: int | None,
        *,
        daily_runs: list[AcceptanceDailyRun] | None = None,
        daily_days: int = 30,
        daily_timezone: str = "America/Argentina/Buenos_Aires",
        current: datetime | None = None,
    ) -> AcceptanceReport:
        current = current or now_utc()
        report = AcceptanceReport(
            min_age_days=min_age_days,
            max_age_days=max_age_days,
            daily_timezone=daily_timezone,
        )
        for invitation in self.invitations:
            age_days = int((current - invitation.sent_at).total_seconds() // 86400)
            if age_days < min_age_days or (max_age_days is not None and age_days > max_age_days):
                continue
            report.add(
                invitation.source,
                invitation.latest_status,
                invitation.latest_checked_at is not None,
            )
        report.daily = self._daily_acceptance_metrics(
            daily_runs=daily_runs or [],
            days=daily_days,
            timezone=daily_timezone,
            current=current,
        )
        for window_days in (7, 30):
            if window_days > daily_days:
                continue
            metrics = report.daily[-window_days:]
            accepted = sum(item.newly_confirmed_accepted for item in metrics)
            complete_days = sum(item.coverage_complete is True for item in metrics)
            report.daily_windows.append(
                AcceptanceDailyWindowSummary(
                    days=window_days,
                    newly_confirmed_accepted=accepted,
                    per_calendar_day=round(accepted / window_days, 2),
                    complete_days=complete_days,
                    missing_or_incomplete_days=window_days - complete_days,
                )
            )
        return report

    def _daily_acceptance_metrics(
        self,
        *,
        daily_runs: list[AcceptanceDailyRun],
        days: int,
        timezone: str,
        current: datetime,
    ) -> list[AcceptanceDailyMetric]:
        if days <= 0:
            raise ValueError("daily_days must be > 0")
        zone = ZoneInfo(timezone)
        end_date = current.astimezone(zone).date()
        start_date = end_date.fromordinal(end_date.toordinal() - days + 1)
        accepted_by_date: dict[Date, int] = {}
        for invitation in self.invitations:
            if invitation.latest_status != AcceptanceStatus.ACCEPTED:
                continue
            accepted_event = latest_acceptance_event(invitation)
            if not accepted_event or not accepted_event_confirms_followup(accepted_event):
                continue
            accepted_at = invitation.first_observed_accepted_at
            if accepted_at is None:
                accepted_at = accepted_event.at
            if accepted_at is None:
                continue
            accepted_date = accepted_at.astimezone(zone).date()
            if start_date <= accepted_date <= end_date:
                accepted_by_date[accepted_date] = accepted_by_date.get(accepted_date, 0) + 1

        latest_run_by_date: dict[Date, AcceptanceDailyRun] = {}
        for daily_run in daily_runs:
            if daily_run.timezone != timezone or not (
                start_date <= daily_run.local_date <= end_date
            ):
                continue
            previous = latest_run_by_date.get(daily_run.local_date)
            if previous is None or daily_run.completed_at > previous.completed_at:
                latest_run_by_date[daily_run.local_date] = daily_run

        metrics: list[AcceptanceDailyMetric] = []
        for offset in range(days):
            day = start_date.fromordinal(start_date.toordinal() + offset)
            coverage_run = latest_run_by_date.get(day)
            metrics.append(
                AcceptanceDailyMetric(
                    date=day,
                    newly_confirmed_accepted=accepted_by_date.get(day, 0),
                    eligible=coverage_run.eligible if coverage_run else None,
                    checked=coverage_run.checked if coverage_run else None,
                    remaining_unresolved=(
                        coverage_run.remaining_unresolved if coverage_run else None
                    ),
                    coverage_complete=(
                        coverage_run.coverage_complete if coverage_run else None
                    ),
                    blocker=coverage_run.blocker if coverage_run else None,
                )
            )
        return metrics

    def accepted_connections(self) -> list[AcceptedDraftCandidate]:
        result: list[AcceptedDraftCandidate] = []
        for invitation in self.invitations:
            if invitation.latest_status != AcceptanceStatus.ACCEPTED:
                continue
            accepted_event = latest_acceptance_event(invitation)
            if accepted_event is None or not accepted_event_confirms_followup(accepted_event):
                continue
            accepted_at = invitation.first_observed_accepted_at or accepted_event.at
            candidate = AcceptedDraftCandidate(
                run_id=invitation.run_id,
                run_date=invitation.run_date,
                source=invitation.source,
                name=invitation.name,
                profile_url=invitation.public_profile_url or invitation.profile_url,
                sales_nav_profile_url=(
                    invitation.profile_url
                    if is_sales_nav_profile_url(invitation.profile_url)
                    else None
                ),
                sent_at=invitation.sent_at,
                accepted_at=accepted_at,
                relationship=accepted_event.relationship if accepted_event else None,
                acceptance_note=accepted_event.note if accepted_event else None,
                acceptance_evidence=accepted_event.evidence if accepted_event else None,
            )
            result.append(candidate)
        return sorted(result, key=lambda candidate: (candidate.accepted_at, candidate.name))

    def accepted_for_followup(
        self, followups: AcceptanceFollowupLedger, include_drafted: bool
    ) -> list[AcceptedDraftCandidate]:
        candidates = self.accepted_connections()
        if include_drafted:
            return candidates
        return [candidate for candidate in candidates if not followups.has_draft_for(candidate)]


def sanitize_acceptance_outcome(
    row: AcceptanceOutcomeRow, invitation: AcceptanceInvitation
) -> AcceptanceOutcomeRow:
    if row.status != AcceptanceStatus.ACCEPTED:
        return row
    if not acceptance_row_confirms_first_degree(row):
        note = "accepted outcome did not include first-degree relationship evidence"
        if row.note and row.note.strip():
            note = f"{row.note.strip()}; {note}"
        return row.model_copy(update={"status": AcceptanceStatus.UNKNOWN, "note": note})
    if (
        row.evidence_grade != AcceptanceEvidenceGrade.STRUCTURED_FIRST_DEGREE
        or row.contract_version != "acceptance-relationship-v2"
    ):
        note = "accepted outcome did not include structured first-degree provenance"
        if row.note and row.note.strip():
            note = f"{row.note.strip()}; {note}"
        return row.model_copy(update={"status": AcceptanceStatus.UNKNOWN, "note": note})
    expected_lead_id = sales_nav_lead_id(invitation.profile_url)
    if (
        expected_lead_id
        and row.expected_lead_id == expected_lead_id
        and row.loaded_lead_id == expected_lead_id
    ):
        return row
    note = (
        "accepted outcome did not match the exact Sales Navigator lead identity; "
        "downgraded to unknown"
    )
    if row.note and row.note.strip():
        note = f"{row.note.strip()}; {note}"
    return row.model_copy(update={"status": AcceptanceStatus.UNKNOWN, "note": note})


def latest_acceptance_event(invitation: AcceptanceInvitation) -> AcceptanceOutcomeEvent | None:
    for event in reversed(invitation.history):
        if event.status == AcceptanceStatus.ACCEPTED:
            return event
    return None


def _latest_relationship_contract_was_unavailable(
    invitation: AcceptanceInvitation,
) -> bool:
    if not invitation.history:
        return False
    event = invitation.history[-1]
    if event.contract_version != "acceptance-relationship-v2" or not event.evidence:
        return False
    try:
        evidence = json.loads(event.evidence)
    except (TypeError, ValueError):
        return False
    if not isinstance(evidence, dict):
        return False
    top_card_count: object = evidence.get("topCardCount")
    return top_card_count != 1


def accepted_event_confirms_followup(event: AcceptanceOutcomeEvent) -> bool:
    return acceptance_event_evidence_grade(event) in {
        AcceptanceEvidenceGrade.CONTROLLER_CONFIRMED,
        AcceptanceEvidenceGrade.STRUCTURED_FIRST_DEGREE,
        AcceptanceEvidenceGrade.CONNECTIONS_LIST_FIRST_DEGREE,
    }


def acceptance_event_evidence_grade(
    event: AcceptanceOutcomeEvent | None,
) -> AcceptanceEvidenceGrade:
    if event is None:
        return AcceptanceEvidenceGrade.LEGACY_UNVERIFIED
    if event.evidence_grade is not None:
        return event.evidence_grade
    if event.note == DURABLY_CONFIRMED_ACCEPTED_NOTE:
        return AcceptanceEvidenceGrade.CONTROLLER_CONFIRMED
    return AcceptanceEvidenceGrade.LEGACY_UNVERIFIED


def acceptance_row_confirms_first_degree(row: AcceptanceOutcomeRow) -> bool:
    return (
        row.relationship == "1st"
        and row.evidence_grade == AcceptanceEvidenceGrade.STRUCTURED_FIRST_DEGREE
        and row.contract_version == "acceptance-relationship-v2"
    )


def sales_nav_lead_id(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    path = urlparse(profile_url).path
    prefix = "/sales/lead/"
    if not path.startswith(prefix):
        return None
    lead_id = path.removeprefix(prefix).split(",", 1)[0].strip()
    return lead_id or None


def acceptance_evidence_matches_candidate(evidence: str, name: str) -> bool:
    evidence_tokens = acceptance_name_tokens(evidence)
    name_tokens = acceptance_name_tokens(name)
    if not evidence_tokens or not name_tokens:
        return False
    evidence_text = " ".join(evidence_tokens)
    name_text = " ".join(name_tokens)
    if name_text in evidence_text:
        return True
    if len(name_tokens) == 1:
        return name_tokens[0] in evidence_tokens
    first = name_tokens[0]
    last = name_tokens[-1]
    if first not in evidence_tokens:
        return False
    if len(last) == 1:
        return any(token.startswith(last) for token in evidence_tokens)
    return last in evidence_tokens


def acceptance_name_tokens(value: str) -> list[str]:
    return [part for part in re.split(r"[^a-zA-Z0-9]+", value.lower()) if part]


class AcceptedDraftCandidate(AppModel):
    run_id: uuid.UUID
    run_date: Date
    source: str
    name: str
    profile_url: str | None = None
    sales_nav_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("sales_nav_profile_url", "salesNavProfileUrl")
    )
    sent_at: datetime
    accepted_at: datetime
    relationship: str | None = None
    acceptance_note: str | None = None
    acceptance_evidence: str | None = None


class AcceptedGreetingEligibilityItem(AppModel):
    followup_id: str
    candidate_key: str
    candidate: AcceptedDraftCandidate
    status: GreetingEligibilityStatus
    lead_key: str | None = None
    original_connection_source: str | None = None
    original_connection_approved_at: datetime | None = None
    original_connection_approval_reason: str | None = None
    original_connection_review_text: str | None = None
    relationship_role: RelationshipRole | None = None
    sales_nav_list_name: str | None = None
    proposed_message: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AcceptedGreetingEligibilityArtifact(AppModel):
    generated_at: datetime = Field(default_factory=now_utc)
    items: list[AcceptedGreetingEligibilityItem] = Field(default_factory=list)


class RelationshipEnrichmentDecisionStatus(StrEnum):
    ENRICHED = "enriched"
    NEEDS_REVIEW = "needs_review"
    SKIP = "skip"


class RelationshipEnrichmentConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RelationshipEnrichmentEvidence(AppModel):
    evidence_id: str
    source_url: str
    claim: str
    relevance: str | None = None
    source_excerpt: str


class RelationshipEnrichmentDecisionTemplate(AppModel):
    status: RelationshipEnrichmentDecisionStatus = RelationshipEnrichmentDecisionStatus.NEEDS_REVIEW
    confidence: RelationshipEnrichmentConfidence | None = None
    person_summary: str | None = None
    company_name: str | None = None
    company_summary: str | None = None
    official_company_url: str | None = None
    evidence_urls: list[str] = Field(default_factory=list)
    research_evidence: list[RelationshipEnrichmentEvidence] = Field(default_factory=list)
    commercial_context: CommercialContextReference | None = None
    criterion_evidence: list[CommercialCriterionEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    notes: str | None = None
    warnings: list[str] = Field(default_factory=list)
    relationship_role: RelationshipRole | None = None
    priority: RelationshipPriority | None = None
    signal_type: RelationshipSignalType = RelationshipSignalType.NONE
    visible_signal: str | None = None
    signal_url: str | None = None
    followup_reason: str | None = None
    next_useful_action: str | None = None
    permission_boundary: str = "review_only"


class RelationshipEnrichmentQueueItem(AppModel):
    followup_id: str
    candidate_key: str
    candidate: AcceptedDraftCandidate
    evidence: list[str] = Field(default_factory=list)
    enrichment_status: RelationshipEnrichmentStatus = RelationshipEnrichmentStatus.MISSING
    enrichment_reason: str | None = None
    original_connection_approved_at: datetime | None = None
    original_connection_approval_reason: str | None = None
    original_connection_review_text: str | None = None
    decision: RelationshipEnrichmentDecisionTemplate = Field(
        default_factory=RelationshipEnrichmentDecisionTemplate
    )


class RelationshipEnrichmentQueue(AppModel):
    generated_at: datetime = Field(default_factory=now_utc)
    commercial_context: CommercialContextReference = Field(
        default_factory=CommercialContextReference
    )
    items: list[RelationshipEnrichmentQueueItem] = Field(default_factory=list)


class RelationshipEnrichmentDecision(RelationshipEnrichmentDecisionTemplate):
    followup_id: str
    candidate_key: str
    candidate: AcceptedDraftCandidate


class RelationshipEnrichmentArtifact(AppModel):
    generated_at: datetime = Field(default_factory=now_utc)
    source_path: str | None = None
    commercial_context: CommercialContextReference | None = None
    decisions: list[RelationshipEnrichmentDecision] = Field(default_factory=list)


class CodexRelationshipEnrichmentResult(AppModel):
    candidate_key: str
    status: RelationshipEnrichmentDecisionStatus
    confidence: RelationshipEnrichmentConfidence | None = None
    person_summary: str | None = None
    company_name: str | None = None
    company_summary: str | None = None
    official_company_url: str | None = None
    evidence_urls: list[str] = Field(default_factory=list)
    research_evidence: list[RelationshipEnrichmentEvidence] = Field(default_factory=list)
    commercial_context: CommercialContextReference
    criterion_evidence: list[CommercialCriterionEvidence] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    notes: str | None = None
    warnings: list[str] = Field(default_factory=list)
    relationship_role: RelationshipRole | None = None
    priority: RelationshipPriority | None = None
    signal_type: RelationshipSignalType = RelationshipSignalType.NONE
    visible_signal: str | None = None
    signal_url: str | None = None
    followup_reason: str | None = None
    next_useful_action: str | None = None
    permission_boundary: str = "review_only"


class CodexRelationshipEnrichmentJob(AppModel):
    generated_at: datetime = Field(default_factory=now_utc)
    followup_id: str
    candidate_key: str
    packet_path: str
    schema_path: str
    result_path: str
    events_path: str
    stderr_path: str
    pid: int
    command: list[str] = Field(default_factory=list)
    context_path: str
    sources_path: str


class AcceptanceFollowupAttempt(AppModel):
    at: datetime = Field(default_factory=now_utc)
    dry_run: bool
    status: str
    result_url: str | None = None
    note: str | None = None
    out_path: str
    diagnostics: dict[str, str] = Field(default_factory=dict)
    transaction_id: str | None = None
    message_sha256: str | None = None
    possible_send: bool = False


class AcceptanceLeadListAttempt(AppModel):
    at: datetime = Field(default_factory=now_utc)
    status: AcceptanceLeadListStatus
    list_name: str
    result_url: str | None = None
    reason: str | None = None
    out_path: str


class AcceptanceFollowupRecord(AppModel):
    key: str
    id: str
    source: str
    name: str
    profile_url: str | None = None
    sales_nav_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("sales_nav_profile_url", "salesNavProfileUrl")
    )
    drafted_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    accepted_at: datetime
    draft: str
    relationship_role: RelationshipRole | None = None
    relationship_priority: RelationshipPriority | None = None
    greeting_eligibility_status: GreetingEligibilityStatus | None = None
    relationship_enrichment_status: RelationshipEnrichmentStatus | None = None
    relationship_enriched_at: datetime | None = None
    original_connection_approved_at: datetime | None = None
    original_connection_approval_reason: str | None = None
    original_connection_review_text: str | None = None
    sales_nav_list_name: str | None = None
    sales_nav_list_status: AcceptanceLeadListStatus | None = None
    sales_nav_list_saved_at: datetime | None = None
    sales_nav_list_attempts: list[AcceptanceLeadListAttempt] = Field(default_factory=list)
    company_profile_url: str | None = None
    company_website_url: str | None = None
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: AcceptanceFollowupStatus = AcceptanceFollowupStatus.DRAFTED
    sent_at: datetime | None = None
    attempts: list[AcceptanceFollowupAttempt] = Field(default_factory=list)
    report_path: str

    def terminal(self) -> bool:
        return self.status in {
            AcceptanceFollowupStatus.SENT,
            AcceptanceFollowupStatus.CONVERSATION_EXISTS,
            AcceptanceFollowupStatus.INVALID_ACCEPTANCE,
            AcceptanceFollowupStatus.EXCLUDED,
            AcceptanceFollowupStatus.DIRECT_MESSAGE_UNAVAILABLE,
        }


class AcceptanceFollowupLedger(AppModel):
    drafts: list[AcceptanceFollowupRecord] = Field(default_factory=list)

    def has_draft_for(self, candidate: AcceptedDraftCandidate) -> bool:
        key = accepted_followup_candidate_key(candidate)
        return any(record.key == key for record in self.drafts)

    def find_by_id(self, record_id: str) -> int | None:
        for index, record in enumerate(self.drafts):
            if record.id == record_id:
                return index
        return None

    def ready(self, limit: int) -> list[AcceptanceFollowupRecord]:
        records = [
            record
            for record in self.drafts
            if record.status == AcceptanceFollowupStatus.DRY_RUN_READY
        ]
        return records[:limit] if limit > 0 else records

    def needs_dry_run(
        self, limit: int, *, retry_classified: bool = False
    ) -> list[AcceptanceFollowupRecord]:
        statuses = {AcceptanceFollowupStatus.DRAFTED}
        if retry_classified:
            statuses.update(
                {
                    AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                    AcceptanceFollowupStatus.BLOCKED,
                    AcceptanceFollowupStatus.SEND_FAILED,
                }
            )
        records = [
            record
            for record in self.drafts
            if record.status in statuses
        ]
        return records[:limit] if limit > 0 else records

    def welcome_message_records(self) -> list[AcceptanceFollowupRecord]:
        return [
            record
            for record in self.drafts
            if record.greeting_eligibility_status == GreetingEligibilityStatus.ELIGIBLE
            and record.draft == accepted_welcome_message(first_name(record.name))
        ]

    def welcome_message_sent_count(self) -> int:
        return sum(
            record.status == AcceptanceFollowupStatus.SENT
            and record.draft == accepted_welcome_message(first_name(record.name))
            for record in self.drafts
        )

    def invalidatable_for_acceptance_keys(self, keys: set[str]) -> list[AcceptanceFollowupRecord]:
        return [
            record
            for record in self.drafts
            if record.key in keys
            and record.status
            in {
                AcceptanceFollowupStatus.DRAFTED,
                AcceptanceFollowupStatus.DRY_RUN_READY,
                AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                AcceptanceFollowupStatus.BLOCKED,
                AcceptanceFollowupStatus.SEND_FAILED,
            }
        ]

    def invalidate_acceptance_keys(
        self,
        keys: set[str],
        *,
        note: str = WEAK_MESSAGE_ACCEPTED_INVALIDATION_NOTE,
        at: datetime | None = None,
    ) -> int:
        current = at or now_utc()
        updated = 0
        for record in self.invalidatable_for_acceptance_keys(keys):
            record.status = AcceptanceFollowupStatus.INVALID_ACCEPTANCE
            record.updated_at = current
            if note not in record.warnings:
                record.warnings.append(note)
            updated += 1
        return updated

    def restore_verified_acceptance_keys(
        self,
        keys: set[str],
        *,
        at: datetime | None = None,
    ) -> int:
        current = at or now_utc()
        updated = 0
        for record in self.drafts:
            if (
                record.key not in keys
                or record.status != AcceptanceFollowupStatus.INVALID_ACCEPTANCE
            ):
                continue
            record.status = AcceptanceFollowupStatus.DRAFTED
            record.updated_at = current
            record.warnings = [
                warning
                for warning in record.warnings
                if "acceptance evidence" not in warning.lower()
                and "legacy acceptance" not in warning.lower()
            ]
            updated += 1
        return updated

    def record_welcome_messages(
        self,
        artifact: AcceptedGreetingEligibilityArtifact,
        report_path: str,
    ) -> int:
        written = 0
        for item in artifact.items:
            if (
                item.status != GreetingEligibilityStatus.ELIGIBLE
                or item.proposed_message is None
            ):
                continue
            key = accepted_followup_candidate_key(item.candidate)
            existing_index = next(
                (index for index, record in enumerate(self.drafts) if record.key == key),
                None,
            )
            if existing_index is not None:
                existing = self.drafts[existing_index]
                if not existing.terminal():
                    status = existing.status
                    if existing.draft != item.proposed_message:
                        status = AcceptanceFollowupStatus.DRAFTED
                    self.drafts[existing_index] = existing.model_copy(
                        update={
                            "drafted_at": artifact.generated_at,
                            "updated_at": artifact.generated_at,
                            "draft": item.proposed_message,
                            "relationship_role": item.relationship_role,
                            "greeting_eligibility_status": item.status,
                            "relationship_enrichment_status": (
                                existing.relationship_enrichment_status
                                or RelationshipEnrichmentStatus.MISSING
                            ),
                            "original_connection_approved_at": item.original_connection_approved_at,
                            "original_connection_approval_reason": (
                                item.original_connection_approval_reason
                            ),
                            "original_connection_review_text": item.original_connection_review_text,
                            "sales_nav_list_name": existing.sales_nav_list_name,
                            "sales_nav_list_status": existing.sales_nav_list_status,
                            "profile_url": item.candidate.profile_url,
                            "sales_nav_profile_url": item.candidate.sales_nav_profile_url,
                            "evidence": [
                                f"Original connection source: {item.original_connection_source}",
                                "Original connection approval: "
                                f"{item.original_connection_approval_reason}",
                            ],
                            "warnings": list(item.warnings),
                            "status": status,
                            "report_path": report_path,
                        }
                    )
                continue
            self.drafts.append(
                AcceptanceFollowupRecord(
                    key=key,
                    id=acceptance_followup_id(key),
                    source=item.candidate.source,
                    name=item.candidate.name,
                    profile_url=item.candidate.profile_url,
                    sales_nav_profile_url=item.candidate.sales_nav_profile_url,
                    drafted_at=artifact.generated_at,
                    updated_at=artifact.generated_at,
                    accepted_at=item.candidate.accepted_at,
                    draft=item.proposed_message,
                    relationship_role=item.relationship_role,
                    greeting_eligibility_status=item.status,
                    relationship_enrichment_status=RelationshipEnrichmentStatus.MISSING,
                    original_connection_approved_at=item.original_connection_approved_at,
                    original_connection_approval_reason=(item.original_connection_approval_reason),
                    original_connection_review_text=item.original_connection_review_text,
                    sales_nav_list_name=None,
                    sales_nav_list_status=None,
                    evidence=[
                        f"Original connection source: {item.original_connection_source}",
                        f"Original connection approval: {item.original_connection_approval_reason}",
                    ],
                    warnings=list(item.warnings),
                    report_path=report_path,
                )
            )
            written += 1
        return written


def acceptance_followup_id(key: str) -> str:
    return "afu_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def accepted_followup_candidate_key(candidate: AcceptedDraftCandidate) -> str:
    identity_url = candidate.sales_nav_profile_url or candidate.profile_url
    return candidate_key(candidate.source, candidate.name, identity_url)


def relationship_enrichment_queue_item(
    candidate: AcceptedDraftCandidate,
) -> RelationshipEnrichmentQueueItem:
    key = accepted_followup_candidate_key(candidate)
    evidence = [
        f"Source: {candidate.source}",
        f"Accepted at: {candidate.accepted_at.isoformat()}",
    ]
    if candidate.profile_url:
        evidence.append(f"Profile URL: {candidate.profile_url}")
    if candidate.sales_nav_profile_url:
        evidence.append(f"Sales Nav profile URL: {candidate.sales_nav_profile_url}")
    if candidate.relationship:
        evidence.append(f"Relationship: {candidate.relationship}")
    if candidate.acceptance_note:
        evidence.append(f"Acceptance note: {candidate.acceptance_note}")
    if candidate.acceptance_evidence:
        evidence.append(f"Acceptance evidence: {candidate.acceptance_evidence}")
    return RelationshipEnrichmentQueueItem(
        followup_id=acceptance_followup_id(key),
        candidate_key=key,
        candidate=candidate,
        evidence=evidence,
    )


def build_relationship_enrichment_queue(
    candidates: list[AcceptedDraftCandidate],
) -> RelationshipEnrichmentQueue:
    return RelationshipEnrichmentQueue(
        items=[relationship_enrichment_queue_item(candidate) for candidate in candidates]
    )


def validate_relationship_enrichment_artifact(
    artifact: RelationshipEnrichmentArtifact,
) -> None:
    errors: list[str] = []
    for index, decision in enumerate(artifact.decisions, start=1):
        label = f"{decision.followup_id or index} ({decision.candidate.name})"
        if decision.status != RelationshipEnrichmentDecisionStatus.ENRICHED:
            continue
        status_label = decision.status.value
        if decision.confidence != RelationshipEnrichmentConfidence.HIGH:
            errors.append(f"{label}: {status_label} requires confidence=high")
        required = {
            "person_summary": decision.person_summary,
            "company_name": decision.company_name,
            "company_summary": decision.company_summary,
        }
        for field, value in required.items():
            if not _non_empty(value):
                errors.append(f"{label}: {status_label} missing {field}")
        if not decision.evidence_urls and not decision.research_evidence:
            errors.append(f"{label}: {status_label} requires at least one evidence source")
    if errors:
        raise ValueError("invalid relationship enrichment decisions: " + "; ".join(errors))


def accepted_welcome_message(first: str) -> str:
    return (
        f"Hey {first}, thanks for connecting. Glad to be in each other’s network, "
        "and I’m looking forward to following what you share here."
    )


def first_name(name: str) -> str:
    fields = name.split()
    return fields[0] if fields else "there"


def _non_empty(value: str | None) -> str | None:
    return value if value else None


def clean_inline(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())


class AcceptanceFollowupMessageCandidate(AppModel):
    id: str
    key: str
    name: str
    profile_url: str = Field(validation_alias=AliasChoices("profile_url", "profileUrl"))
    sales_nav_profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("sales_nav_profile_url", "salesNavProfileUrl")
    )
    source: str


class AcceptanceFollowupSendResult(AppModel):
    candidate: AcceptanceFollowupMessageCandidate | None = None
    dry_run: bool = Field(default=False, validation_alias=AliasChoices("dry_run", "dryRun"))
    url: str | None = None
    message_length: int = Field(
        default=0, validation_alias=AliasChoices("message_length", "messageLength")
    )
    status: str
    reason: str | None = None
    action: Any = None
    visible_actions: Any = Field(
        default=None, validation_alias=AliasChoices("visible_actions", "visibleActions")
    )
    profile_identity: Any = Field(
        default=None, validation_alias=AliasChoices("profile_identity", "profileIdentity")
    )
    search_row_action: Any = Field(
        default=None, validation_alias=AliasChoices("search_row_action", "searchRowAction")
    )
    conversation_check: Any = Field(
        default=None, validation_alias=AliasChoices("conversation_check", "conversationCheck")
    )
    conversation_cleanup: Any = Field(
        default=None, validation_alias=AliasChoices("conversation_cleanup", "conversationCleanup")
    )
    message_containers: Any = Field(
        default=None, validation_alias=AliasChoices("message_containers", "messageContainers")
    )
    subject_fill: Any = Field(
        default=None, validation_alias=AliasChoices("subject_fill", "subjectFill")
    )
    body_fill: Any = Field(default=None, validation_alias=AliasChoices("body_fill", "bodyFill"))
    send: Any = None
    send_buttons: Any = Field(
        default=None, validation_alias=AliasChoices("send_buttons", "sendButtons")
    )
    profile_api_responses: Any = Field(
        default=None, validation_alias=AliasChoices("profile_api_responses", "profileApiResponses")
    )
    composer_selector: str | None = Field(
        default=None, validation_alias=AliasChoices("composer_selector", "composerSelector")
    )
    composer_contract: Any = Field(
        default=None, validation_alias=AliasChoices("composer_contract", "composerContract")
    )
    body: str | None = None
    transaction_id: str | None = Field(
        default=None, validation_alias=AliasChoices("transaction_id", "transactionId")
    )
    message_sha256: str | None = Field(
        default=None, validation_alias=AliasChoices("message_sha256", "messageSha256")
    )
    send_confirmation: Any = Field(
        default=None, validation_alias=AliasChoices("send_confirmation", "sendConfirmation")
    )


class AcceptanceFollowupConfirmationArtifact(AppModel):
    followup_id: str
    transaction_id: str
    candidate_name: str
    profile_url: str
    recipient_lead_id: str
    message_sha256: str
    exact_message: str
    exact_match_count: int
    direction: str
    observed_at: datetime
    source: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class AcceptanceLeadListSaveResult(AppModel):
    candidate: AcceptanceFollowupMessageCandidate | None = None
    list_name: str = Field(validation_alias=AliasChoices("list_name", "listName"))
    status: AcceptanceLeadListStatus
    url: str | None = None
    reason: str | None = None
    action: Any = None
    visible_lists: Any = Field(
        default=None, validation_alias=AliasChoices("visible_lists", "visibleLists")
    )


def validate_acceptance_followup_can_send(
    record: AcceptanceFollowupRecord, dry_run: bool, allow_send: bool
) -> None:
    if record.terminal():
        raise ValueError(f"accepted follow-up {record.id} is already {record.status.value}")
    if record.draft.strip() == "":
        raise ValueError(
            f"accepted welcome {record.id} has no stored message; rerun "
            "`acceptance prepare-welcome-messages` first"
        )
    if not record.profile_url or record.profile_url.strip() == "":
        raise ValueError(f"accepted follow-up {record.id} has no profile URL")
    if not dry_run and not allow_send:
        raise ValueError("real send requires --allow-send")
    if not dry_run and not (
        is_public_linkedin_profile_url(record.profile_url)
        or is_sales_nav_profile_url(record.profile_url)
        or is_sales_nav_profile_url(record.sales_nav_profile_url)
    ):
        raise ValueError(
            f"accepted follow-up {record.id} has no usable LinkedIn or Sales Navigator profile URL"
        )
    if not dry_run and record.status != AcceptanceFollowupStatus.DRY_RUN_READY:
        raise ValueError(
            f"accepted follow-up {record.id} is {record.status.value}; real sends require "
            f"{AcceptanceFollowupStatus.DRY_RUN_READY.value}"
        )


def is_public_linkedin_profile_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    host = parsed.hostname or ""
    return (host == "linkedin.com" or host.endswith(".linkedin.com")) and parsed.path.startswith(
        "/in/"
    )


def public_profile_identifier(value: str | None) -> str | None:
    if not is_public_linkedin_profile_url(value):
        return None
    parsed = urlparse(value or "")
    parts = [part for part in parsed.path.split("/") if part]
    return parts[1] if len(parts) >= 2 and parts[0] == "in" else None


def acceptance_invitation_identities(invitation: AcceptanceInvitation) -> set[str]:
    result: set[str] = set()
    member_id = sales_nav_lead_id(invitation.profile_url)
    if member_id:
        result.add(f"member:{member_id}")
    identifier = public_profile_identifier(invitation.public_profile_url)
    if identifier:
        result.add(f"public:{identifier}")
    return result


def is_sales_nav_profile_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    host = parsed.hostname or ""
    return (host == "linkedin.com" or host.endswith(".linkedin.com")) and parsed.path.startswith(
        "/sales/lead/"
    )


def apply_acceptance_followup_send_result(
    record: AcceptanceFollowupRecord, result: AcceptanceFollowupSendResult, out_path: str
) -> None:
    current = now_utc()
    record.attempts.append(
        AcceptanceFollowupAttempt(
            at=current,
            dry_run=result.dry_run,
            status=result.status,
            result_url=result.url,
            note=acceptance_followup_result_note(result),
            out_path=out_path,
            diagnostics=acceptance_followup_diagnostics(result),
            transaction_id=result.transaction_id,
            message_sha256=result.message_sha256,
            possible_send=result.status in {"sent-clicked", "send-confirmation-missing"},
        )
    )
    record.status = acceptance_followup_status_for_result(result)
    record.updated_at = current
    if record.status == AcceptanceFollowupStatus.SENT:
        record.sent_at = current


def apply_acceptance_lead_list_save_result(
    record: AcceptanceFollowupRecord,
    result: AcceptanceLeadListSaveResult,
    out_path: str,
) -> None:
    current = now_utc()
    record.sales_nav_list_attempts.append(
        AcceptanceLeadListAttempt(
            at=current,
            status=result.status,
            list_name=result.list_name,
            result_url=result.url,
            reason=result.reason,
            out_path=out_path,
        )
    )
    record.sales_nav_list_status = result.status
    record.updated_at = current
    if result.status in {
        AcceptanceLeadListStatus.SAVED,
        AcceptanceLeadListStatus.ALREADY_SAVED,
    }:
        record.sales_nav_list_saved_at = current


def acceptance_followup_status_for_result(
    result: AcceptanceFollowupSendResult,
) -> AcceptanceFollowupStatus:
    if result.status in {"dry-run-messageable", "preview-filled"}:
        return AcceptanceFollowupStatus.DRY_RUN_READY
    if result.status == "sent-confirmed":
        return AcceptanceFollowupStatus.SENT
    if result.status in {"sent-clicked", "send-confirmation-missing"}:
        return AcceptanceFollowupStatus.POSSIBLE_SEND
    if result.status == "not-messageable":
        return AcceptanceFollowupStatus.NOT_MESSAGEABLE
    if result.status == "direct-message-unavailable":
        return AcceptanceFollowupStatus.DIRECT_MESSAGE_UNAVAILABLE
    if result.status == "conversation-exists":
        return AcceptanceFollowupStatus.CONVERSATION_EXISTS
    if result.status == "blocked":
        return AcceptanceFollowupStatus.BLOCKED
    return AcceptanceFollowupStatus.SEND_FAILED


def acceptance_followup_diagnostics(result: AcceptanceFollowupSendResult) -> dict[str, str]:
    diagnostics: dict[str, str] = {}
    if result.composer_selector and result.composer_selector.strip():
        diagnostics["composer"] = result.composer_selector.strip()
    for key, value in {
        "subject": result.subject_fill,
        "body": result.body_fill,
        "send": result.send,
        "send_buttons": result.send_buttons,
        "conversation": result.conversation_check,
        "conversation_cleanup": result.conversation_cleanup,
        "message_containers": result.message_containers,
        "action": result.action,
        "visible_actions": result.visible_actions,
        "profile_identity": result.profile_identity,
        "send_confirmation": result.send_confirmation,
        "composer_contract": result.composer_contract,
    }.items():
        if value is not None:
            diagnostics[key] = compact_json(value)
    return diagnostics


def acceptance_followup_result_note(result: AcceptanceFollowupSendResult) -> str | None:
    parts: list[str] = []
    if result.reason and result.reason.strip():
        parts.append(result.reason.strip())
    if result.visible_actions is not None:
        parts.append("visible_actions " + compact_json(result.visible_actions))
    if result.conversation_check is not None:
        parts.append("conversation " + compact_json(result.conversation_check))
    if result.message_containers is not None:
        parts.append("message_containers " + compact_json(result.message_containers))
    if result.composer_selector and result.composer_selector.strip():
        parts.append("composer " + result.composer_selector.strip())
    if result.body_fill is not None:
        parts.append("body " + compact_json(result.body_fill))
    if result.send is not None:
        parts.append("send " + compact_json(result.send))
    if result.send_confirmation is not None:
        parts.append("send_confirmation " + compact_json(result.send_confirmation))
    if not parts:
        return None
    return "; ".join(parts)[:1000]


def compact_json(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), sort_keys=True)[:1000]


class PendingCandidateObservation(AppModel):
    imported_at: datetime = Field(default_factory=now_utc)
    captured_at: str | None = None
    index: int
    name: str
    profile_url: str | None = None
    age_text: str = ""
    age_months: int | None = None
    age_days: int | None = None
    eligible: bool = False
    row_text: str = ""


class PendingWithdrawEvent(AppModel):
    at: datetime = Field(default_factory=now_utc)
    name: str
    profile_url: str | None = None
    age_text: str = ""
    status: PendingWithdrawStatus
    note: str | None = None


class PendingCleanupPlan(AppModel):
    action: str
    reason: str | None = None
    name: str | None = None
    profile_url: str | None = None
    age_text: str | None = None
    withdraw_capacity_remaining: int | None = None


class PendingCleanupRun(AppModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    date: Date = Field(default_factory=today)
    max_withdrawals: int
    threshold_months: int = 0
    threshold_days: int = 0
    state: PendingCleanupState = PendingCleanupState.STARTED
    start_audit: int | None = None
    latest_audit: int | None = None
    audits: list[AuditEvent] = Field(default_factory=list)
    observations: list[PendingCandidateObservation] = Field(default_factory=list)
    withdrawals: list[PendingWithdrawEvent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    def normalize(self) -> None:
        if self.threshold_days == 0 and self.threshold_months > 0:
            self.threshold_days = self.threshold_months * 30

    def mark_updated(self) -> None:
        self.updated_at = now_utc()

    def withdrawn_count(self) -> int:
        return sum(
            1 for event in self.withdrawals if event.status == PendingWithdrawStatus.WITHDRAWN
        )

    def audited_delta(self) -> int | None:
        if self.start_audit is None or self.latest_audit is None:
            return None
        return self.latest_audit - self.start_audit

    def has_withdraw_event_for_observation(self, observation: PendingCandidateObservation) -> bool:
        for event in self.withdrawals:
            if event.profile_url and observation.profile_url:
                if event.profile_url == observation.profile_url:
                    return True
                continue
            if event.name == observation.name and event.age_text == observation.age_text:
                return True
        return False

    def next_eligible_observation(self) -> PendingCandidateObservation | None:
        for observation in self.observations:
            if observation.eligible and not self.has_withdraw_event_for_observation(observation):
                return observation
        return None

    def withdraw_capacity_remaining(self) -> int:
        count = self.withdrawn_count()
        if count >= self.max_withdrawals:
            return 0
        return self.max_withdrawals - count

    def operator_plan(self) -> PendingCleanupPlan:
        if self.state == PendingCleanupState.NEEDS_REAUDIT:
            return PendingCleanupPlan(action="reaudit", reason="cleanup is paused in NEEDS_REAUDIT")
        if self.withdraw_capacity_remaining() == 0:
            return PendingCleanupPlan(action="final-audit")
        candidate = self.next_eligible_observation()
        if candidate is not None:
            return PendingCleanupPlan(
                action="withdraw-candidate",
                name=candidate.name,
                profile_url=candidate.profile_url,
                age_text=candidate.age_text,
                withdraw_capacity_remaining=self.withdraw_capacity_remaining(),
            )
        return PendingCleanupPlan(
            action="capture-more", reason="no unrecorded eligible stale invitation is imported"
        )


def new_pending_cleanup_run(
    max_withdrawals: int,
    threshold_days: int,
    run_date: Date | None = None,
    threshold_months: int = 0,
) -> PendingCleanupRun:
    effective_days = threshold_days or threshold_months * 30
    return PendingCleanupRun(
        date=run_date or today(),
        max_withdrawals=max_withdrawals,
        threshold_months=threshold_months or effective_days // 30,
        threshold_days=effective_days,
    )


class PendingCaptureRow(AppModel):
    index: int
    name: str | None = None
    profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_url", "profileUrl")
    )
    age_text: str | None = Field(default=None, validation_alias=AliasChoices("age_text", "ageText"))
    age_months: int | None = Field(
        default=None, validation_alias=AliasChoices("age_months", "ageMonths")
    )
    age_days: int | None = Field(default=None, validation_alias=AliasChoices("age_days", "ageDays"))
    eligible: bool | None = None
    row_text: str | None = Field(default=None, validation_alias=AliasChoices("row_text", "rowText"))


class PendingCapture(AppModel):
    captured_at: str | None = Field(
        default=None, validation_alias=AliasChoices("captured_at", "capturedAt")
    )
    visible_withdraw_count: int | None = Field(
        default=None,
        validation_alias=AliasChoices("visible_withdraw_count", "visibleWithdrawCount"),
    )
    warnings: list[str] = Field(default_factory=list)
    rows: list[PendingCaptureRow] = Field(default_factory=list)


class PendingWithdrawCandidate(AppModel):
    name: str
    profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_url", "profileUrl")
    )
    age_text: str = Field(default="", validation_alias=AliasChoices("age_text", "ageText"))


class PendingWithdrawResult(AppModel):
    candidate: PendingWithdrawCandidate
    status: str
    detail: Any = None

    def to_withdraw_status(self) -> tuple[PendingWithdrawStatus, str]:
        if self.status == "withdrawn-verified":
            return (
                PendingWithdrawStatus.WITHDRAWN,
                "salesnav-pending-withdraw-one verified row removed or count decreased",
            )
        if self.status == "dry-run-withdrawable":
            return PendingWithdrawStatus.SKIPPED, "dry run found eligible stale invitation"
        if self.status in {"not-eligible", "row-not-found"}:
            return (
                PendingWithdrawStatus.SKIPPED,
                "salesnav-pending-withdraw-one status " + self.status,
            )
        return (
            PendingWithdrawStatus.FAILED,
            f"salesnav-pending-withdraw-one status {self.status}; {self.detail or 'no detail'}",
        )


class PendingWithdrawBatchResult(AppModel):
    status: str
    results: list[PendingWithdrawResult] = Field(default_factory=list)
    detail: Any = None


def apply_pending_audit(run: PendingCleanupRun, people_count: int, note: str | None = None) -> None:
    if run.start_audit is None:
        run.start_audit = people_count
        run.state = PendingCleanupState.AUDITED
    elif run.state == PendingCleanupState.NEEDS_REAUDIT:
        run.state = PendingCleanupState.WITHDRAWING
    run.latest_audit = people_count
    run.audits.append(AuditEvent(people_count=people_count, note=note))
    run.mark_updated()


def first_number(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def parse_sent_age_months(age_text: str) -> int | None:
    lower = age_text.lower()
    if "year" in lower:
        return (first_number(lower) or 1) * 12
    if "month" in lower:
        return first_number(lower) or 1
    if any(marker in lower for marker in ("today", "minute", "hour", "day", "week")):
        return 0
    return None


def parse_sent_age_days(age_text: str) -> int | None:
    lower = age_text.lower()
    if any(marker in lower for marker in ("today", "minute", "hour")):
        return 0
    count = first_number(lower) or 1
    if "year" in lower:
        return count * 365
    if "month" in lower:
        return count * 30
    if "week" in lower:
        return count * 7
    if "yesterday" in lower:
        return 1
    if "day" in lower:
        return count
    return None


def import_pending_capture(run: PendingCleanupRun, capture: PendingCapture) -> int:
    imported = 0
    for row in capture.rows:
        if row.name is None or row.name.strip() == "":
            continue
        age_text = row.age_text or ""
        age_months = (
            row.age_months if row.age_months is not None else parse_sent_age_months(age_text)
        )
        age_days = row.age_days if row.age_days is not None else parse_sent_age_days(age_text)
        if age_days is not None and run.threshold_days > 0:
            eligible = age_days >= run.threshold_days
        elif age_months is not None:
            eligible = age_months >= run.threshold_months
        else:
            eligible = bool(row.eligible)
        observation = PendingCandidateObservation(
            imported_at=now_utc(),
            captured_at=capture.captured_at,
            index=row.index,
            name=row.name,
            profile_url=row.profile_url,
            age_text=age_text,
            age_months=age_months,
            age_days=age_days,
            eligible=eligible,
            row_text=row.row_text or "",
        )
        existing_index: int | None = None
        for index, existing in enumerate(run.observations):
            if existing.profile_url and observation.profile_url:
                if existing.profile_url == observation.profile_url:
                    existing_index = index
                    break
            elif existing.name == observation.name and existing.age_text == observation.age_text:
                existing_index = index
                break
        if existing_index is None:
            run.observations.append(observation)
            imported += 1
        else:
            run.observations[existing_index] = observation
    run.mark_updated()
    return imported


def record_pending_withdraw_result(
    run: PendingCleanupRun, result: PendingWithdrawResult, path: str
) -> PendingWithdrawEvent:
    status, status_note = result.to_withdraw_status()
    event = PendingWithdrawEvent(
        at=now_utc(),
        name=result.candidate.name,
        profile_url=result.candidate.profile_url,
        age_text=result.candidate.age_text,
        status=status,
        note=f"{status_note}; result={path}",
    )
    if status == PendingWithdrawStatus.WITHDRAWN:
        for withdrawal in run.withdrawals:
            if (
                withdrawal.status == PendingWithdrawStatus.WITHDRAWN
                and withdrawal.name == event.name
                and withdrawal.profile_url == event.profile_url
            ):
                raise ValueError(f"candidate already recorded as withdrawn: {event.name}")
    run.withdrawals.append(event)
    if run.state not in {PendingCleanupState.DONE, PendingCleanupState.BLOCKED}:
        run.state = (
            PendingCleanupState.FINAL_RECONCILE
            if run.withdraw_capacity_remaining() == 0
            else PendingCleanupState.WITHDRAWING
        )
    run.mark_updated()
    return event
