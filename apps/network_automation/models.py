"""Domain model for the Python port of linkedin-network-run."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from datetime import date as Date
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse, urlunparse

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
    NEEDS_REAUDIT = "NeedsReaudit"
    FINAL_RECONCILE = "FinalReconcile"
    DONE = "Done"
    BLOCKED = "Blocked"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    ALREADY_PENDING = "already-pending"
    AUDIT_TOP_UP = "audit-top-up"
    SKIPPED = "skipped"
    FAILED = "failed"


class AcceptanceStatus(StrEnum):
    SENT = "sent"
    PENDING = "pending"
    ACCEPTED = "accepted"
    CONNECTABLE = "connectable"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    FAILED = "failed"


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


class DraftStrategy(StrEnum):
    ASAP_CONTRACT_V1 = "asap-contract-v1"


class AcceptanceFollowupStatus(StrEnum):
    DRAFTED = "drafted"
    DRY_RUN_READY = "dry_run_ready"
    SENT = "sent"
    CONVERSATION_EXISTS = "conversation_exists"
    NOT_MESSAGEABLE = "not_messageable"
    BLOCKED = "blocked"
    SEND_FAILED = "send_failed"


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
    status: CandidateStatus
    note: str | None = None


class CandidateObservation(AppModel):
    imported_at: datetime = Field(default_factory=now_utc)
    captured_at: str | None = None
    source: str
    index: int
    name: str
    profile_url: str | None = None
    sales_profile_urn: str | None = None
    visible_state: Any = None
    menu_state: str = "unknown"
    menu_labels: list[str] = Field(default_factory=list)
    row_html_path: str | None = None


class SourceCaptureCursor(AppModel):
    source: str
    updated_at: datetime = Field(default_factory=now_utc)
    captured_at: str | None = None
    resume_url: str | None = None
    page_label: str | None = None
    captured_pages: int = 0
    raw_row_count: int = 0
    output_row_count: int = 0
    connectable_count: int = 0
    already_pending_count: int = 0
    missing_trigger_count: int = 0
    state_counts: dict[str, int] = Field(default_factory=dict)


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
    reason: str | None = None


class SourceYieldStats(AppModel):
    source: str
    raw_row_count: int
    connectable_count: int
    already_pending_count: int
    email_required_skips: int
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
    blocked_resume_at: datetime | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    def normalize(self) -> None:
        if self.max_real_sends == 0:
            self.max_real_sends = self.target
        if not self.sources:
            self.sources = default_sources(self.target)

    def mark_updated(self) -> None:
        self.updated_at = now_utc()

    def verified_count(self) -> int:
        return sum(
            1 for candidate in self.candidates if candidate.status == CandidateStatus.PENDING
        )

    def audited_delta(self) -> int | None:
        if self.start_audit is None or self.latest_audit is None:
            return None
        return self.latest_audit - self.start_audit

    def source_verified_count(self, source: str) -> int:
        return sum(
            1
            for candidate in self.candidates
            if candidate.source == source and candidate.status == CandidateStatus.PENDING
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
            verified = self.source_verified_count(plan.name)
            if plan.target > verified:
                total += plan.target - verified
        return total

    def source_quota_with_carryover(self, source_index: int) -> int:
        source = self.sources[source_index]
        if source.fallback:
            remaining = self.target - min(self.target, self.verified_count())
            return max(remaining, source.target)
        return source.target + self.primary_shortfall_before(source_index)

    def next_source(self) -> NextSource | None:
        if self.state in {RunState.NEEDS_REAUDIT, RunState.DONE, RunState.BLOCKED}:
            return None
        total_remaining = self.target - min(self.target, self.verified_count())
        if total_remaining == 0:
            return None
        for index, source in enumerate(self.sources):
            if source.exhausted:
                continue
            quota = self.source_quota_with_carryover(index)
            verified = self.source_verified_count(source.name)
            if source.fallback or verified < quota:
                remaining_for_source = min(quota - min(quota, verified), total_remaining)
                return NextSource(
                    name=source.name,
                    quota=quota,
                    verified=verified,
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
        if self.verified_count() >= self.target:
            return True
        index = self.source_index(source)
        if index is None:
            return False
        plan = self.sources[index]
        return plan.exhausted or self.source_verified_count(
            source
        ) >= self.source_quota_with_carryover(index)

    def source_is_fallback(self, source: str) -> bool:
        return any(plan.name == source and plan.fallback for plan in self.sources)

    def real_send_capacity_remaining(self) -> int:
        verified = self.verified_count()
        if verified >= self.max_real_sends:
            return 0
        return self.max_real_sends - verified

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

    def next_top_up_observation(self) -> CandidateObservation | None:
        for observation in self.observations:
            if (
                self.source_is_fallback(observation.source)
                and observation.menu_state == "connectable"
                and not self.has_top_up_blocking_event_for_observation(observation)
            ):
                return observation
        for observation in self.observations:
            if (
                observation.menu_state == "connectable"
                and not self.has_top_up_blocking_event_for_observation(observation)
            ):
                return observation
        return None

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
        if self.state == RunState.NEEDS_REAUDIT:
            return OperatorPlan(action="reaudit", reason="run is paused in NEEDS_REAUDIT")
        if self.state == RunState.BLOCKED:
            return OperatorPlan(
                action="blocked", reason="run is blocked by the latest guarded send result"
            )
        if self.verified_count() >= self.target:
            return OperatorPlan(action="final-audit")
        candidate = self.next_connectable_observation()
        if candidate is not None:
            if self.real_send_capacity_remaining() == 0:
                return OperatorPlan(
                    action="blocked",
                    reason=(
                        f"real-send cap reached: {self.verified_count()}/{self.max_real_sends} "
                        "verified sends"
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
        return OperatorPlan(
            action="blocked", reason="no connectable candidate and no available source"
        )

    def operator_plan(self) -> OperatorPlan:
        return self.operator_plan_with_reservoir(None)

    def sent_invitation_events(self) -> list[CandidateEvent]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.status in {CandidateStatus.PENDING, CandidateStatus.AUDIT_TOP_UP}
        ]


DEFAULT_SOURCE_MIX: list[tuple[str, int]] = [
    ("ASAP - Agency Owners Delivery", 9),
    ("ASAP - Contract Recruiters Staffing", 7),
    ("ASAP - Startup CTO Eng Leaders", 6),
    ("ASAP - High-Intent SaaS AI Founders", 5),
    ("ASAP - Vertical Proof Buyers", 3),
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
    sources = [SourcePlan(name=name, target=count) for name, count in allocated]
    sources.append(SourcePlan(name="FO - Founders - Urgent", target=0, fallback=True))
    return sources


def new_run(target: int, run_date: Date | None = None, max_real_sends: int | None = None) -> Run:
    effective_date = run_date or today()
    return Run(
        date=effective_date,
        target=target,
        max_real_sends=target if max_real_sends is None else max_real_sends,
        sources=default_sources(target),
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
    buffer = 3 if remaining > 0 else 0
    pages = 5 if remaining + buffer > 10 else 3
    return CaptureRecommendation(
        pages=pages,
        stop_after_connectable=min(remaining + buffer, 25),
        buffer=buffer,
        reason="standard-buffer",
        playwriter_timeout_ms=45000,
    )


def expanded_capture_recommendation(remaining: int, reason: str) -> CaptureRecommendation:
    buffer = max(remaining, 5)
    return CaptureRecommendation(
        pages=5,
        stop_after_connectable=min(remaining + buffer, 25),
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
    pending_sends = run.source_verified_count(source.name)
    yield_value = connectable_count / raw_row_count if raw_row_count > 0 else None
    recommendation = "no capture data"
    if yield_value is not None:
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
    name: str | None = None
    text: str | None = None
    profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_url", "profileUrl")
    )
    scroll_urn: str | None = Field(
        default=None, validation_alias=AliasChoices("scroll_urn", "scrollUrn")
    )
    visible_state: Any = Field(
        default=None, validation_alias=AliasChoices("visible_state", "visibleState")
    )
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
    page: SalesNavCapturePage | None = None
    pages: list[SalesNavCapturePage] = Field(default_factory=list)
    state_counts: dict[str, int] = Field(
        default_factory=dict, validation_alias=AliasChoices("state_counts", "stateCounts")
    )
    raw_row_count: int | None = Field(
        default=None, validation_alias=AliasChoices("raw_row_count", "rawRowCount")
    )
    output_row_count: int | None = Field(
        default=None, validation_alias=AliasChoices("output_row_count", "outputRowCount")
    )
    rows: list[SalesNavCaptureRow] = Field(default_factory=list)


class SalesNavAudit(AppModel):
    people_count: int = Field(validation_alias=AliasChoices("people_count", "peopleCount"))
    recent_names: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("recent_names", "recentNames")
    )


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

    def to_candidate_status(self) -> tuple[CandidateStatus, str]:
        if self.status == "pending-verified":
            return CandidateStatus.PENDING, "salesnav-send-one verified Connect - Pending"
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
                sales_profile_urn=row.scroll_urn,
                visible_state=row.visible_state,
                menu_state=menu_state,
                menu_labels=labels,
                row_html_path=row.row_html_path,
            )
        )
    return observations


def update_capture_cursor(run: Run, source: str, capture: SalesNavCapture) -> None:
    last_page = capture.page or (capture.pages[-1] if capture.pages else None)
    resume_url = capture.resume_url or capture.url or (last_page.url if last_page else None)
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
        if event.status in {CandidateStatus.PENDING, CandidateStatus.AUDIT_TOP_UP}:
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


def record_send_result(run: Run, result: SalesNavSendResult, path: str) -> CandidateEvent:
    status, status_note = result.to_candidate_status()
    note = f"{status_note}; result={path}"
    event = CandidateEvent(
        at=now_utc(),
        source=result.candidate.source,
        name=result.candidate.name,
        profile_url=result.candidate.profile_url,
        status=status,
        note=note,
    )
    ensure_known_source(run, event.source)
    if status == CandidateStatus.PENDING:
        for candidate in run.candidates:
            if (
                candidate.status == CandidateStatus.PENDING
                and candidate.name == event.name
                and candidate.profile_url == event.profile_url
            ):
                raise ValueError(f"candidate already recorded as pending: {event.name}")
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
    if status == CandidateStatus.PENDING:
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


class AcceptanceInvitation(AppModel):
    run_id: uuid.UUID
    run_date: Date
    source: str
    name: str
    profile_url: str | None = None
    sent_at: datetime
    latest_status: AcceptanceStatus = AcceptanceStatus.SENT
    latest_checked_at: datetime | None = None
    history: list[AcceptanceOutcomeEvent] = Field(default_factory=list)

    def key(self) -> str:
        return candidate_key(self.source, self.name, self.profile_url)


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
    note: str | None = None


class AcceptanceOutcomeArtifact(AppModel):
    rows: list[AcceptanceOutcomeRow] = Field(default_factory=list)


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


class AcceptanceSourceReport(AppModel):
    total_sent: int = 0
    checked: int = 0
    accepted: int = 0
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
    pending: int = 0
    connectable: int = 0
    unknown: int = 0
    blocked: int = 0
    failed: int = 0
    withdrawn: int = 0
    unchecked: int = 0
    by_source: dict[str, AcceptanceSourceReport] = Field(default_factory=dict)

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
            if event.status not in {CandidateStatus.PENDING, CandidateStatus.AUDIT_TOP_UP}:
                continue
            if self.upsert_invitation(run_id, run_date, event):
                inserted += 1
        return inserted

    def upsert_invitation(self, run_id: uuid.UUID, run_date: Date, event: CandidateEvent) -> bool:
        key = candidate_key(event.source, event.name, event.profile_url)
        for invitation in self.invitations:
            if invitation.key() == key:
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
                sent_at=event.at,
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
                )
                invitation.latest_status = sanitized.status
                invitation.latest_checked_at = checked_at
                invitation.history.append(event)
                summary.matched += 1
                matched = True
                break
            if not matched:
                summary.unmatched += 1
        return summary

    def eligible_for_check(
        self, min_age_days: int, max_age_days: int | None
    ) -> list[AcceptanceInvitation]:
        current = now_utc()
        result: list[AcceptanceInvitation] = []
        for invitation in self.invitations:
            if invitation.latest_status in {AcceptanceStatus.ACCEPTED, AcceptanceStatus.WITHDRAWN}:
                continue
            if invitation.profile_url is None:
                continue
            age_days = int((current - invitation.sent_at).total_seconds() // 86400)
            if age_days >= min_age_days and (max_age_days is None or age_days <= max_age_days):
                result.append(invitation)
        return result

    def report(self, min_age_days: int, max_age_days: int | None) -> AcceptanceReport:
        current = now_utc()
        report = AcceptanceReport(min_age_days=min_age_days, max_age_days=max_age_days)
        for invitation in self.invitations:
            age_days = int((current - invitation.sent_at).total_seconds() // 86400)
            if age_days < min_age_days or (max_age_days is not None and age_days > max_age_days):
                continue
            report.add(
                invitation.source,
                invitation.latest_status,
                invitation.latest_checked_at is not None,
            )
        return report

    def accepted_for_followup(
        self, followups: AcceptanceFollowupLedger, include_drafted: bool
    ) -> list[AcceptedDraftCandidate]:
        result: list[AcceptedDraftCandidate] = []
        for invitation in self.invitations:
            if invitation.latest_status != AcceptanceStatus.ACCEPTED:
                continue
            accepted_event: AcceptanceOutcomeEvent | None = None
            for event in reversed(invitation.history):
                if event.status == AcceptanceStatus.ACCEPTED:
                    accepted_event = event
                    break
            if accepted_event is not None:
                accepted_at = accepted_event.at
            elif invitation.latest_checked_at is not None:
                accepted_at = invitation.latest_checked_at
            else:
                continue
            candidate = AcceptedDraftCandidate(
                run_id=invitation.run_id,
                run_date=invitation.run_date,
                source=invitation.source,
                name=invitation.name,
                profile_url=invitation.profile_url,
                sent_at=invitation.sent_at,
                accepted_at=accepted_at,
                relationship=accepted_event.relationship if accepted_event else None,
                acceptance_note=accepted_event.note if accepted_event else None,
                acceptance_evidence=accepted_event.evidence if accepted_event else None,
            )
            if include_drafted or not followups.has_draft_for(candidate):
                result.append(candidate)
        return sorted(result, key=lambda candidate: (candidate.accepted_at, candidate.name))


def sanitize_acceptance_outcome(
    row: AcceptanceOutcomeRow, invitation: AcceptanceInvitation
) -> AcceptanceOutcomeRow:
    if row.status != AcceptanceStatus.ACCEPTED or row.evidence is None:
        return row
    if acceptance_evidence_matches_candidate(
        row.evidence, row.name
    ) or acceptance_evidence_matches_candidate(row.evidence, invitation.name):
        return row
    note = "accepted outcome evidence did not match candidate identity; downgraded to unknown"
    if row.note and row.note.strip():
        note = f"{row.note.strip()}; {note}"
    return row.model_copy(update={"status": AcceptanceStatus.UNKNOWN, "note": note})


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
    sent_at: datetime
    accepted_at: datetime
    relationship: str | None = None
    acceptance_note: str | None = None
    acceptance_evidence: str | None = None


class SalesNavResearch(AppModel):
    name: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class WebResult(AppModel):
    title: str | None = None
    url: str | None = None
    snippet: str | None = None


class WebResearch(AppModel):
    query: str | None = None
    results: list[WebResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AcceptedResearchRow(AppModel):
    source: str
    name: str
    profile_url: str | None = Field(
        default=None, validation_alias=AliasChoices("profile_url", "profileUrl")
    )
    sales_nav: SalesNavResearch | None = Field(
        default=None, validation_alias=AliasChoices("sales_nav", "salesNav")
    )
    web: WebResearch | None = None
    warnings: list[str] = Field(default_factory=list)


class AcceptedResearchArtifact(AppModel):
    captured_at: str | None = Field(
        default=None, validation_alias=AliasChoices("captured_at", "capturedAt")
    )
    rows: list[AcceptedResearchRow] = Field(default_factory=list)


class DraftItem(AppModel):
    candidate: AcceptedDraftCandidate
    angle: str
    draft: str
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DraftReport(AppModel):
    generated_at: datetime = Field(default_factory=now_utc)
    strategy: DraftStrategy = DraftStrategy.ASAP_CONTRACT_V1
    research_path: str | None = None
    research_captured_at: str | None = None
    items: list[DraftItem] = Field(default_factory=list)
    skipped_names: list[str] = Field(default_factory=list)


class AcceptanceFollowupAttempt(AppModel):
    at: datetime = Field(default_factory=now_utc)
    dry_run: bool
    status: str
    result_url: str | None = None
    note: str | None = None
    out_path: str
    diagnostics: dict[str, str] = Field(default_factory=dict)


class AcceptanceFollowupRecord(AppModel):
    key: str
    id: str
    source: str
    name: str
    profile_url: str | None = None
    drafted_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    accepted_at: datetime
    strategy: DraftStrategy = DraftStrategy.ASAP_CONTRACT_V1
    angle: str
    draft: str
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: AcceptanceFollowupStatus = AcceptanceFollowupStatus.DRAFTED
    sent_at: datetime | None = None
    attempts: list[AcceptanceFollowupAttempt] = Field(default_factory=list)
    report_path: str
    research_path: str | None = None

    def terminal(self) -> bool:
        return self.status in {
            AcceptanceFollowupStatus.SENT,
            AcceptanceFollowupStatus.CONVERSATION_EXISTS,
        }


class AcceptanceFollowupLedger(AppModel):
    drafts: list[AcceptanceFollowupRecord] = Field(default_factory=list)

    def has_draft_for(self, candidate: AcceptedDraftCandidate) -> bool:
        key = candidate_key(candidate.source, candidate.name, candidate.profile_url)
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

    def needs_dry_run(self, limit: int) -> list[AcceptanceFollowupRecord]:
        records = [
            record
            for record in self.drafts
            if record.status
            in {
                AcceptanceFollowupStatus.DRAFTED,
                AcceptanceFollowupStatus.NOT_MESSAGEABLE,
                AcceptanceFollowupStatus.BLOCKED,
                AcceptanceFollowupStatus.SEND_FAILED,
            }
        ]
        return records[:limit] if limit > 0 else records

    def record_report(
        self, report: DraftReport, report_path: str, research_path: str | None
    ) -> int:
        written = 0
        for item in report.items:
            key = candidate_key(
                item.candidate.source, item.candidate.name, item.candidate.profile_url
            )
            existing_index = next(
                (index for index, record in enumerate(self.drafts) if record.key == key),
                None,
            )
            if existing_index is not None:
                existing = self.drafts[existing_index]
                if not existing.terminal():
                    self.drafts[existing_index] = existing.model_copy(
                        update={
                            "drafted_at": report.generated_at,
                            "updated_at": report.generated_at,
                            "strategy": report.strategy,
                            "angle": item.angle,
                            "draft": item.draft,
                            "evidence": list(item.evidence),
                            "warnings": list(item.warnings),
                            "report_path": report_path,
                            "research_path": research_path,
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
                    drafted_at=report.generated_at,
                    updated_at=report.generated_at,
                    accepted_at=item.candidate.accepted_at,
                    strategy=report.strategy,
                    angle=item.angle,
                    draft=item.draft,
                    evidence=list(item.evidence),
                    warnings=list(item.warnings),
                    report_path=report_path,
                    research_path=research_path,
                )
            )
            written += 1
        return written


def acceptance_followup_id(key: str) -> str:
    return "afu_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def build_draft_report(
    candidates: list[AcceptedDraftCandidate],
    artifact: AcceptedResearchArtifact | None,
    strategy: DraftStrategy,
    research_path: str | None,
) -> DraftReport:
    research_by_key: dict[str, AcceptedResearchRow] = {}
    research_captured_at: str | None = None
    if artifact is not None:
        research_captured_at = artifact.captured_at
        for row in artifact.rows:
            research_by_key[candidate_key(row.source, row.name, row.profile_url)] = row
    seen: set[str] = set()
    items: list[DraftItem] = []
    skipped_names: list[str] = []
    for candidate in candidates:
        key = candidate_key(candidate.source, candidate.name, candidate.profile_url)
        if key in seen:
            skipped_names.append(candidate.name)
            continue
        seen.add(key)
        items.append(build_draft_item(candidate, research_by_key.get(key), strategy))
    return DraftReport(
        strategy=strategy,
        research_path=research_path,
        research_captured_at=research_captured_at,
        items=items,
        skipped_names=skipped_names,
    )


def build_draft_item(
    candidate: AcceptedDraftCandidate,
    research: AcceptedResearchRow | None,
    strategy: DraftStrategy,
) -> DraftItem:
    _ = strategy
    sales_nav = research.sales_nav if research else None
    title = _non_empty(sales_nav.title if sales_nav else None)
    company = _non_empty(sales_nav.company if sales_nav else None)
    web_result = (
        research.web.results[0] if research and research.web and research.web.results else None
    )
    first = first_name(candidate.name)
    angle_kind, angle_label = choose_angle(candidate.source, title, company, web_result)
    if angle_kind == "recruiter":
        draft = recruiter_accepted_followup_draft(first)
    elif angle_kind == "agency":
        draft = agency_accepted_followup_draft(first)
    elif angle_kind == "investor-advisor":
        draft = investor_advisor_accepted_followup_draft(first)
    elif angle_kind == "technical-leader":
        draft = technical_accepted_followup_draft(first)
    else:
        draft = general_accepted_followup_draft(first)
    evidence: list[str] = []
    if title:
        evidence.append(f"Sales Nav title/headline: {title}")
    if company:
        evidence.append(f"Sales Nav company: {company}")
    if sales_nav is not None:
        if _non_empty(sales_nav.name):
            evidence.append(f"Sales Nav displayed name: {sales_nav.name}")
        if _non_empty(sales_nav.location):
            evidence.append(f"Sales Nav location: {sales_nav.location}")
        if _non_empty(sales_nav.url):
            evidence.append(f"Sales Nav URL after load: {sales_nav.url}")
    if candidate.relationship:
        evidence.append(f"Sales Nav relationship: {candidate.relationship}")
    if candidate.acceptance_note:
        evidence.append(f"Acceptance check: {candidate.acceptance_note}")
    if web_result is not None:
        if web_result.title:
            evidence.append(f"Public web result: {web_result.title}")
        if web_result.url:
            evidence.append(f"Public web URL: {web_result.url}")
        if web_result.snippet:
            evidence.append(f"Public web snippet: {web_result.snippet}")
    if research and research.web and research.web.query:
        evidence.append(f"Public web query: {research.web.query}")
    warnings: list[str] = []
    if research is None:
        warnings.append(
            "No research row matched this accepted candidate; draft uses source and "
            "ledger evidence only."
        )
    else:
        warnings.extend(research.warnings)
        if research.sales_nav:
            warnings.extend(research.sales_nav.warnings)
        if research.web:
            warnings.extend(research.web.warnings)
    if not title and not company:
        warnings.append("Sales Nav title/company were not extracted; review before sending.")
    return DraftItem(
        candidate=candidate,
        angle=angle_label,
        draft=draft,
        evidence=evidence,
        warnings=warnings,
    )


def general_accepted_followup_draft(first: str) -> str:
    return (
        f"Hey, {first}. Thanks for connecting.\n\n"
        "I'm available for contract product engineering work through HC Studio LLC, mostly around "
        "full-stack product builds and AI workflows.\n\n"
        "If it would be helpful, I'm happy to send over my resume and a couple of "
        "project examples.\n\n"
        "Best,\nHanif Carroll"
    )


def technical_accepted_followup_draft(first: str) -> str:
    return (
        f"Hey, {first}. Thanks for connecting.\n\n"
        "I'm available for contract product engineering work through HC Studio LLC, mostly around "
        "full-stack product builds, AI workflows, and prototype-to-production work.\n\n"
        "If it would be helpful, I'm happy to send over my resume and a couple of "
        "project examples.\n\n"
        "Best,\nHanif Carroll"
    )


def investor_advisor_accepted_followup_draft(first: str) -> str:
    return (
        f"Hey, {first}. Thanks for connecting.\n\n"
        "I'm available for contract product engineering work through HC Studio LLC, "
        "mostly helping teams "
        "ship full-stack products and AI workflows.\n\n"
        "If someone in your network ever needs that kind of help, I'm happy to send "
        "over my resume and "
        "a couple of project examples.\n\n"
        "Best,\nHanif Carroll"
    )


def agency_accepted_followup_draft(first: str) -> str:
    return (
        f"Hey, {first}. Thanks for connecting.\n\n"
        "I'm available for contract product engineering work through HC Studio LLC, "
        "mostly helping with "
        "project overflow, prototypes, and AI-enabled product builds.\n\n"
        "If it would be helpful, I'm happy to send over my resume and a couple of "
        "project examples.\n\n"
        "Best,\nHanif Carroll"
    )


def recruiter_accepted_followup_draft(first: str) -> str:
    return (
        f"Hey, {first}. Thanks for connecting.\n\n"
        "I'm available for contract product engineering work through HC Studio LLC, "
        "focused on full-stack "
        "product builds and AI workflows.\n\n"
        "If useful, I'm happy to send over my resume and a couple of project examples "
        "for your files.\n\n"
        "Best,\nHanif Carroll"
    )


def choose_angle(
    source: str, title: str | None, company: str | None, web_result: WebResult | None
) -> tuple[str, str]:
    source_lower = source.lower()
    title_lower = title.lower() if title else ""
    company_suffix = f" for {clean_inline(company)}" if company else ""
    web_suffix = (
        f"; public result: {clean_inline(web_result.title)}"
        if web_result and web_result.title
        else ""
    )
    combined = " ".join([source_lower, title_lower, company_suffix, web_suffix]).lower()
    if contains_any(
        combined, "recruit", "staffing", "talent acquisition", "headhunter", "hire recruiters"
    ):
        return "recruiter", "contract-role availability ask" + company_suffix + web_suffix
    if contains_any(
        combined,
        "agency",
        "studio",
        "digital transformation",
        "custom ai solutions",
        "web design",
        "ux/ui",
        "cro",
        "seo",
        "implementation partner",
        "technology services",
        "software agency",
        "development agency",
        "consulting partners",
        "consulting services",
    ):
        return (
            "agency",
            "agency overflow or specialist contractor capacity" + company_suffix + web_suffix,
        )
    if (
        contains_any(
            combined,
            "cto",
            "cpo",
            "chief product",
            "product lead",
            "product manager",
            "ai product",
            "platform",
            "llm",
            "agentic",
            "software engineer",
            "developer",
            "technical",
            "data",
            "automation",
            "workflow",
            "internal tools",
            "voice agents",
            "enterprise ai",
            "ai-native",
            "product leader",
        )
        or "product leaders" in source_lower
    ):
        return (
            "technical-leader",
            "senior product-engineering contractor help" + company_suffix + web_suffix,
        )
    if contains_any(
        combined,
        "investor",
        "investment",
        "m&a",
        "broker",
        "fundraising",
        "private equity",
        "advisor",
        "coach",
        "mentor",
        "board",
        "career coach",
    ):
        return (
            "investor-advisor",
            "network referral for contract product-engineering help" + company_suffix + web_suffix,
        )
    if "vertical" in source_lower or "proof" in source_lower:
        return "proof-matched", "proof-matched product/workflow help" + company_suffix + web_suffix
    return "general-founder", "fast contract product-engineering help" + company_suffix + web_suffix


def contains_any(value: str, *needles: str) -> bool:
    return any(needle in value for needle in needles)


def first_name(name: str) -> str:
    fields = name.split()
    return fields[0] if fields else "there"


def _non_empty(value: str | None) -> str | None:
    return value if value else None


def clean_inline(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())


def render_draft_markdown(report: DraftReport) -> str:
    lines = [
        f"# LinkedIn Accepted Follow-Up Drafts {report.generated_at.date().isoformat()}",
        "",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Strategy: `{report.strategy.value}`",
        f"- Draft count: {len(report.items)}",
    ]
    if report.research_path:
        lines.append(f"- Research artifact: `{report.research_path}`")
    if report.research_captured_at:
        lines.append(f"- Research captured: `{clean_inline(report.research_captured_at)}`")
    if report.skipped_names:
        lines.append("- Duplicate candidates skipped: " + ", ".join(report.skipped_names))
    if not report.items:
        lines.extend(["", "No newly accepted connections need first-message drafts."])
        return "\n".join(lines)
    for item in report.items:
        key = candidate_key(item.candidate.source, item.candidate.name, item.candidate.profile_url)
        lines.extend(
            [
                "",
                "## " + clean_inline(item.candidate.name),
                "- Follow-up ID: `" + acceptance_followup_id(key) + "`",
                "- Source: " + clean_inline(item.candidate.source),
            ]
        )
        if item.candidate.profile_url:
            lines.append("- Profile: " + clean_inline(item.candidate.profile_url))
        lines.append(f"- Accepted at: `{item.candidate.accepted_at.isoformat()}`")
        lines.append("- Best angle: " + clean_inline(item.angle))
        if item.evidence:
            lines.append("- Evidence used:")
            lines.extend("  - " + clean_inline(evidence) for evidence in item.evidence)
        if item.warnings:
            lines.append("- Warnings:")
            lines.extend("  - " + clean_inline(warning) for warning in item.warnings)
        lines.extend(["", "Draft:", ""])
        lines.extend(blockquote(item.draft))
    return "\n".join(lines)


def blockquote(value: str) -> list[str]:
    normalized = value.strip().replace("\r\n", "\n")
    if normalized == "":
        return [">"]
    return [">" if line.strip() == "" else "> " + line for line in normalized.split("\n")]


class AcceptanceFollowupMessageCandidate(AppModel):
    id: str
    key: str
    name: str
    profile_url: str = Field(validation_alias=AliasChoices("profile_url", "profileUrl"))
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
    search_row_action: Any = Field(
        default=None, validation_alias=AliasChoices("search_row_action", "searchRowAction")
    )
    conversation_check: Any = Field(
        default=None, validation_alias=AliasChoices("conversation_check", "conversationCheck")
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
    body: str | None = None


def validate_acceptance_followup_can_send(
    record: AcceptanceFollowupRecord, dry_run: bool, allow_send: bool
) -> None:
    if record.terminal():
        raise ValueError(f"accepted follow-up {record.id} is already {record.status.value}")
    if record.draft.strip() == "":
        raise ValueError(
            f"accepted follow-up {record.id} has no stored draft; rerun "
            "`acceptance draft-followups --include-drafted` first"
        )
    if not record.profile_url or record.profile_url.strip() == "":
        raise ValueError(f"accepted follow-up {record.id} has no profile URL")
    if not dry_run and not allow_send:
        raise ValueError("real send requires --allow-send")
    if not dry_run and record.status != AcceptanceFollowupStatus.DRY_RUN_READY:
        raise ValueError(
            f"accepted follow-up {record.id} is {record.status.value}; real sends require "
            f"{AcceptanceFollowupStatus.DRY_RUN_READY.value}"
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
        )
    )
    record.status = acceptance_followup_status_for_result(result)
    record.updated_at = current
    if record.status == AcceptanceFollowupStatus.SENT:
        record.sent_at = current


def acceptance_followup_status_for_result(
    result: AcceptanceFollowupSendResult,
) -> AcceptanceFollowupStatus:
    if result.status in {"dry-run-messageable", "preview-filled"}:
        return AcceptanceFollowupStatus.DRY_RUN_READY
    if result.status == "sent-clicked":
        return AcceptanceFollowupStatus.SENT
    if result.status == "not-messageable":
        return AcceptanceFollowupStatus.NOT_MESSAGEABLE
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
        "action": result.action,
    }.items():
        if value is not None:
            diagnostics[key] = compact_json(value)
    return diagnostics


def acceptance_followup_result_note(result: AcceptanceFollowupSendResult) -> str | None:
    parts: list[str] = []
    if result.reason and result.reason.strip():
        parts.append(result.reason.strip())
    if result.composer_selector and result.composer_selector.strip():
        parts.append("composer " + result.composer_selector.strip())
    if result.body_fill is not None:
        parts.append("body " + compact_json(result.body_fill))
    if result.send is not None:
        parts.append("send " + compact_json(result.send))
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
    threshold_days: int
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
