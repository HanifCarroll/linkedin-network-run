"""Explicit approval gates for browser actions that can mutate LinkedIn state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum


class RealAction(StrEnum):
    SEND_CONNECTION = "send-connection"
    SEND_MESSAGE = "send-message"
    WITHDRAW_INVITATION = "withdraw-invitation"


@dataclass(frozen=True)
class RealActionApproval:
    action: RealAction
    allow: bool
    approved_by: str = "user"
    reason: str = ""

    def grants(self, action: RealAction) -> bool:
        return self.allow and self.action is action


@dataclass(frozen=True)
class GuardedActionResult:
    status: str
    action: RealAction
    dry_run: bool
    clicked: bool
    label: str
    candidate_id: str | None = None
    reason: str | None = None


class UnsafeRealActionError(RuntimeError):
    """Raised when a real browser mutation is attempted without explicit approval."""


def require_real_action_approval(
    action: RealAction,
    *,
    dry_run: bool,
    approval: RealActionApproval | None,
) -> None:
    if dry_run:
        return
    if approval is None:
        raise UnsafeRealActionError(f"real {action.value} requires explicit approval")
    if not approval.grants(action):
        raise UnsafeRealActionError(f"approval does not grant real {action.value}")


async def guarded_click(
    action: RealAction,
    click: Callable[[], Awaitable[None]],
    *,
    label: str,
    candidate_id: str | None = None,
    dry_run: bool = True,
    approval: RealActionApproval | None = None,
) -> GuardedActionResult:
    require_real_action_approval(action, dry_run=dry_run, approval=approval)
    if dry_run:
        return GuardedActionResult(
            status=f"dry-run-{action.value}",
            action=action,
            dry_run=True,
            clicked=False,
            label=label,
            candidate_id=candidate_id,
        )
    await click()
    return GuardedActionResult(
        status=f"{action.value}-clicked",
        action=action,
        dry_run=False,
        clicked=True,
        label=label,
        candidate_id=candidate_id,
    )
