"""Sales Navigator guarded click wrappers for app-level workflows."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from packages.linkedin_browser import (
    GuardedActionResult,
    RealAction,
    RealActionApproval,
    guarded_click,
)

from .models import CandidateIdentity


async def guarded_connection_request(
    candidate: CandidateIdentity,
    click: Callable[[], Awaitable[None]],
    *,
    label: str = "Connect",
    dry_run: bool = True,
    approval: RealActionApproval | None = None,
) -> GuardedActionResult:
    return await guarded_click(
        RealAction.SEND_CONNECTION,
        click,
        label=label,
        candidate_id=candidate.stable_id,
        dry_run=dry_run,
        approval=approval,
    )


async def guarded_withdraw_invitation(
    candidate: CandidateIdentity,
    click: Callable[[], Awaitable[None]],
    *,
    label: str = "Withdraw",
    dry_run: bool = True,
    approval: RealActionApproval | None = None,
) -> GuardedActionResult:
    return await guarded_click(
        RealAction.WITHDRAW_INVITATION,
        click,
        label=label,
        candidate_id=candidate.stable_id,
        dry_run=dry_run,
        approval=approval,
    )
