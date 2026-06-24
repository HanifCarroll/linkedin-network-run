"""Message/InMail safety primitives for Sales Navigator actions."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from packages.linkedin_browser import (
    GuardedActionResult,
    RealAction,
    RealActionApproval,
    guarded_click,
)

from .models import CandidateIdentity

MessageKind = Literal["message", "inmail"]


@dataclass(frozen=True)
class MessageActionCandidate:
    kind: MessageKind
    action_label: str
    identity_label: str | None
    source: str
    opened_page_url: str | None = None
    used_profile_more_menu: bool = False

    def __post_init__(self) -> None:
        if self.used_profile_more_menu and not self.opened_page_url:
            raise ValueError("profile More-menu message path must record opened_page_url")


@dataclass(frozen=True)
class MessageActionSafetyResult:
    status: str
    candidate_name: str
    action_label: str
    identity_label: str | None
    source: str
    opened_page_url: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MessageActionClickResult:
    status: str
    safety: MessageActionSafetyResult
    guard: GuardedActionResult | None


def validate_message_action_candidate(
    candidate: CandidateIdentity,
    action: MessageActionCandidate,
) -> MessageActionSafetyResult:
    label_for_identity = action.identity_label or action.action_label
    if not _candidate_name_present(label_for_identity, candidate.name):
        return MessageActionSafetyResult(
            status="message-action-candidate-mismatch",
            candidate_name=candidate.name,
            action_label=action.action_label,
            identity_label=action.identity_label,
            source=action.source,
            opened_page_url=action.opened_page_url,
            reason="message action identity label did not match candidate name",
        )
    return MessageActionSafetyResult(
        status="ok",
        candidate_name=candidate.name,
        action_label=action.action_label,
        identity_label=action.identity_label,
        source=action.source,
        opened_page_url=action.opened_page_url,
    )


async def guarded_message_click(
    candidate: CandidateIdentity,
    action: MessageActionCandidate,
    click: Callable[[], Awaitable[None]],
    *,
    dry_run: bool = True,
    approval: RealActionApproval | None = None,
) -> MessageActionClickResult:
    safety = validate_message_action_candidate(candidate, action)
    if safety.status != "ok":
        return MessageActionClickResult(status=safety.status, safety=safety, guard=None)
    guard = await guarded_click(
        RealAction.SEND_MESSAGE,
        click,
        label=action.action_label,
        candidate_id=candidate.stable_id,
        dry_run=dry_run,
        approval=approval,
    )
    return MessageActionClickResult(status=guard.status, safety=safety, guard=guard)


def _candidate_name_present(label: str, candidate_name: str) -> bool:
    normalized_label = _normalize_identity_text(label)
    normalized_candidate = _normalize_identity_text(candidate_name)
    if not normalized_candidate:
        return False
    return f" {normalized_candidate} " in f" {normalized_label} "


def _normalize_identity_text(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", " ", value).strip().casefold()
