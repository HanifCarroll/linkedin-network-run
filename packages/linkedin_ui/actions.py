"""Declarative guarded action definitions for the local review UI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ActionSafety(StrEnum):
    STATE_CHANGE = "state_change"
    REAL_ACTION = "real_action"


@dataclass(frozen=True)
class GuardedCommand:
    """CLI command shape the UI is allowed to invoke through app services."""

    argv: tuple[str, ...]
    approval_flag: str | None

    def text(self) -> str:
        return " ".join(self.argv)

    def has_approval_flag(self) -> bool:
        return self.approval_flag is None or self.approval_flag in self.argv


@dataclass(frozen=True)
class ReviewAction:
    id: str
    app: str
    workflow: str
    label: str
    safety: ActionSafety
    guarded_command: GuardedCommand
    enabled: bool
    integration_dependency: str

    def is_real_action_guarded(self) -> bool:
        if self.safety is not ActionSafety.REAL_ACTION:
            return True
        return self.guarded_command.approval_flag in {"--allow-send", "--allow-withdraw"} and (
            self.guarded_command.has_approval_flag()
        )


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    status: str
    command: tuple[str, ...]
    message: str
    warnings: tuple[str, ...] = ()


class ActionService(Protocol):
    def execute(self, action: ReviewAction) -> ActionResult:
        """Execute or delegate a guarded action."""


class GuardedCommandActionService:
    """Default service for pre-integration UI: validate and refuse execution."""

    def execute(self, action: ReviewAction) -> ActionResult:
        if not action.is_real_action_guarded():
            return ActionResult(
                action_id=action.id,
                status="blocked",
                command=action.guarded_command.argv,
                message="Action is missing the required guarded approval flag.",
                warnings=("No command was executed.",),
            )
        return ActionResult(
            action_id=action.id,
            status="stubbed",
            command=action.guarded_command.argv,
            message="Guarded service integration is pending; no command was executed.",
            warnings=(action.integration_dependency,),
        )


REVIEW_ACTIONS: tuple[ReviewAction, ...] = (
    ReviewAction(
        id="network-send-guarded",
        app="network",
        workflow="Connection request send",
        label="Run guarded connection send",
        safety=ActionSafety.REAL_ACTION,
        guarded_command=GuardedCommand(
            argv=(
                "linkedin-tools",
                "network",
                "send-guarded",
                "--single-pass",
                "--allow-send",
            ),
            approval_flag="--allow-send",
        ),
        enabled=False,
        integration_dependency="Thread 4 must expose the guarded send service/read model.",
    ),
    ReviewAction(
        id="network-send-ready-followup",
        app="network",
        workflow="Accepted follow-up send",
        label="Send one ready follow-up",
        safety=ActionSafety.REAL_ACTION,
        guarded_command=GuardedCommand(
            argv=(
                "linkedin-tools",
                "network",
                "acceptance",
                "send-ready-followups",
                "--limit",
                "1",
                "--allow-send",
            ),
            approval_flag="--allow-send",
        ),
        enabled=False,
        integration_dependency="Thread 4 must expose accepted follow-up guarded sends.",
    ),
    ReviewAction(
        id="network-pending-withdraw",
        app="network",
        workflow="Pending invitation cleanup",
        label="Withdraw next stale invite",
        safety=ActionSafety.REAL_ACTION,
        guarded_command=GuardedCommand(
            argv=(
                "linkedin-tools",
                "network",
                "pending-cleanup",
                "withdraw-next",
                "--allow-withdraw",
            ),
            approval_flag="--allow-withdraw",
        ),
        enabled=False,
        integration_dependency="Thread 4 must expose pending cleanup guarded withdrawals.",
    ),
    ReviewAction(
        id="recruiter-send-message",
        app="recruiter-agency",
        workflow="Recruiter/agency message send",
        label="Send one drafted message",
        safety=ActionSafety.REAL_ACTION,
        guarded_command=GuardedCommand(
            argv=(
                "linkedin-tools",
                "recruiter-agency",
                "send-message",
                "--allow-send",
            ),
            approval_flag="--allow-send",
        ),
        enabled=False,
        integration_dependency="Thread 5 must expose guarded message sends.",
    ),
)


def list_review_actions() -> tuple[ReviewAction, ...]:
    return REVIEW_ACTIONS


def get_review_action(
    action_id: str,
    actions: Sequence[ReviewAction] = REVIEW_ACTIONS,
) -> ReviewAction:
    for action in actions:
        if action.id == action_id:
            return action
    msg = f"Unknown review UI action: {action_id}"
    raise KeyError(msg)
