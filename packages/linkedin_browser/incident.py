"""Shared LinkedIn browser incident gate and cross-lane operation lock."""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from packages.linkedin_common.paths import DEFAULT_STATE_ROOT

INCIDENT_PATH_ENV = "LINKEDIN_TOOLS_INCIDENT_PATH"
BROWSER_LOCK_PATH_ENV = "LINKEDIN_TOOLS_BROWSER_LOCK_PATH"


class IncidentKind(StrEnum):
    MANUAL_PAUSE = "manual-pause"
    UNUSUAL_ACTIVITY = "unusual-activity"
    RATE_LIMIT = "rate-limit"
    LOGIN_REQUIRED = "login-required"
    CHECKPOINT = "checkpoint"
    SECURITY_CHALLENGE = "security-challenge"
    WEEKLY_LIMIT = "weekly-limit"
    NETWORK_REFUSAL = "network-refusal"


class IncidentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: IncidentKind
    source: str
    operation: str | None = None
    summary: str
    evidence_path: str | None = None


class LinkedInIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active: bool = True
    observations: list[IncidentObservation] = Field(default_factory=list)
    cleared_at: datetime | None = None
    clearance_reason: str | None = None
    account_access_confirmed: bool = False
    warning_cleared_confirmed: bool = False

    @property
    def latest(self) -> IncidentObservation:
        if not self.observations:
            raise ValueError("incident has no observations")
        return self.observations[-1]


class ActiveLinkedInIncidentError(RuntimeError):
    """Raised before browser work when the shared incident gate is active."""

    def __init__(self, incident: LinkedInIncident) -> None:
        self.incident = incident
        latest = incident.latest
        super().__init__(
            "LinkedIn browser work is paused by active incident "
            f"{incident.id} ({latest.kind.value}: {latest.summary})"
        )


class LinkedInIncidentDetectedError(RuntimeError):
    """Raised after a browser operation records a new fatal incident."""

    def __init__(self, incident: LinkedInIncident) -> None:
        self.incident = incident
        latest = incident.latest
        super().__init__(
            "LinkedIn browser operation stopped and opened incident "
            f"{incident.id} ({latest.kind.value}: {latest.summary})"
        )


def default_incident_path() -> Path:
    configured = os.environ.get(INCIDENT_PATH_ENV)
    return Path(configured) if configured else DEFAULT_STATE_ROOT / "linkedin-incident.json"


def default_browser_lock_path() -> Path:
    configured = os.environ.get(BROWSER_LOCK_PATH_ENV)
    return Path(configured) if configured else DEFAULT_STATE_ROOT / "browser-operation.lock"


def load_incident(path: Path | None = None) -> LinkedInIncident | None:
    target = path or default_incident_path()
    if not target.exists():
        return None
    return LinkedInIncident.model_validate_json(target.read_text(encoding="utf-8"))


def active_incident(path: Path | None = None) -> LinkedInIncident | None:
    incident = load_incident(path)
    return incident if incident is not None and incident.active else None


def assert_no_active_incident(path: Path | None = None) -> None:
    incident = active_incident(path)
    if incident is not None:
        raise ActiveLinkedInIncidentError(incident)


def open_incident(
    *,
    kind: IncidentKind,
    source: str,
    summary: str,
    operation: str | None = None,
    evidence_path: str | None = None,
    path: Path | None = None,
) -> LinkedInIncident:
    target = path or default_incident_path()
    incident = active_incident(target) or LinkedInIncident()
    incident.observations.append(
        IncidentObservation(
            kind=kind,
            source=source,
            operation=operation,
            summary=summary,
            evidence_path=evidence_path,
        )
    )
    _write_incident(target, incident)
    return incident


def clear_incident(
    *,
    reason: str,
    account_access_confirmed: bool,
    warning_cleared_confirmed: bool,
    path: Path | None = None,
) -> LinkedInIncident:
    target = path or default_incident_path()
    incident = active_incident(target)
    if incident is None:
        raise RuntimeError("no active LinkedIn incident exists")
    if not account_access_confirmed or not warning_cleared_confirmed:
        raise RuntimeError(
            "clearing the LinkedIn incident requires confirmed account access "
            "and confirmed warning clearance"
        )
    if not reason.strip():
        raise RuntimeError("clearing the LinkedIn incident requires a reason")
    incident.active = False
    incident.cleared_at = datetime.now(UTC)
    incident.clearance_reason = reason.strip()
    incident.account_access_confirmed = True
    incident.warning_cleared_confirmed = True
    _write_incident(target, incident)
    return incident


@contextmanager
def browser_operation_lock(
    operation: str,
    *,
    path: Path | None = None,
) -> Iterator[None]:
    target = path or default_browser_lock_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "owner details unavailable"
            raise RuntimeError(
                f"another LinkedIn browser operation is active: {owner}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "operation": operation,
                    "started_at": datetime.now(UTC).isoformat(),
                },
                sort_keys=True,
            )
        )
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def detect_fatal_incident(value: object) -> tuple[IncidentKind, str] | None:
    strings = _flatten_scalar_strings(value)
    normalized = "\n".join(strings).casefold()
    rules: Sequence[tuple[IncidentKind, tuple[str, ...], str]] = (
        (
            IncidentKind.UNUSUAL_ACTIVITY,
            ("we noticed some unusual activity on your account",),
            "LinkedIn unusual-activity warning",
        ),
        (
            IncidentKind.RATE_LIMIT,
            (
                "http 429",
                "status 429",
                "httpstatus\n429",
                "http_status\n429",
                "returned http 429",
                "profile api rate limited",
                "sales-nav-profile-api-rate-limited",
            ),
            "LinkedIn returned HTTP 429",
        ),
        (
            IncidentKind.CHECKPOINT,
            ("checkpoint present", "/checkpoint",),
            "LinkedIn checkpoint present",
        ),
        (
            IncidentKind.SECURITY_CHALLENGE,
            ("security verification present", "security challenge"),
            "LinkedIn security verification present",
        ),
        (
            IncidentKind.LOGIN_REQUIRED,
            ("login required", "/uas/login", "linkedin.com/login"),
            "LinkedIn login required",
        ),
        (
            IncidentKind.WEEKLY_LIMIT,
            ("weekly limit",),
            "LinkedIn weekly limit reached",
        ),
        (
            IncidentKind.NETWORK_REFUSAL,
            (
                "net::err_name_not_resolved",
                "network refusal",
                "connection refused",
            ),
            "LinkedIn browser network request failed",
        ),
    )
    for kind, needles, summary in rules:
        if any(needle in normalized for needle in needles):
            return kind, summary
    return None


def inspect_artifact_for_fatal_incident(path: Path) -> tuple[IncidentKind, str] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return detect_fatal_incident(payload)


def _flatten_scalar_strings(value: object) -> list[str]:
    result: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            result.append(item)
            return
        if isinstance(item, int | float | bool):
            result.append(str(item))
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if isinstance(key, str):
                    result.append(key)
                visit(child)
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                visit(child)

    visit(value)
    return result


def _write_incident(path: Path, incident: LinkedInIncident) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(incident.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
