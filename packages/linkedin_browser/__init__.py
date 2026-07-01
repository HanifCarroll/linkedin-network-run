"""Shared browser automation primitives."""

from .artifacts import ArtifactRef, ArtifactWriter
from .safety import (
    GuardedActionResult,
    RealAction,
    RealActionApproval,
    UnsafeRealActionError,
    guarded_click,
    require_real_action_approval,
)
from .sessions import BrowserSession, PageReusePolicy, choose_reusable_page
from .state import (
    BrowserBlockKind,
    BrowserStateClassification,
    BrowserStateEvidence,
    classify_browser_state,
)

__all__ = [
    "ArtifactRef",
    "ArtifactWriter",
    "BrowserBlockKind",
    "BrowserSession",
    "BrowserStateClassification",
    "BrowserStateEvidence",
    "GuardedActionResult",
    "PageReusePolicy",
    "RealAction",
    "RealActionApproval",
    "UnsafeRealActionError",
    "choose_reusable_page",
    "classify_browser_state",
    "guarded_click",
    "require_real_action_approval",
]
