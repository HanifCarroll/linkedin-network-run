"""Shared browser automation primitives."""

from .artifacts import ArtifactRef, ArtifactWriter
from .incident import (
    ActiveLinkedInIncidentError,
    IncidentKind,
    LinkedInIncident,
    LinkedInIncidentDetectedError,
    active_incident,
    assert_no_active_incident,
    browser_operation_lock,
    clear_incident,
    open_incident,
)
from .playwriter import (
    PLAYWRITER_BIN_ENV,
    PLAYWRITER_BROWSER_KEY_ENV,
    PLAYWRITER_SESSION_ENV,
    PlaywriterRunner,
    safe_stem,
)
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
    "ActiveLinkedInIncidentError",
    "BrowserBlockKind",
    "BrowserSession",
    "BrowserStateClassification",
    "BrowserStateEvidence",
    "GuardedActionResult",
    "IncidentKind",
    "LinkedInIncident",
    "LinkedInIncidentDetectedError",
    "PageReusePolicy",
    "PLAYWRITER_BIN_ENV",
    "PLAYWRITER_BROWSER_KEY_ENV",
    "PLAYWRITER_SESSION_ENV",
    "PlaywriterRunner",
    "RealAction",
    "RealActionApproval",
    "UnsafeRealActionError",
    "active_incident",
    "assert_no_active_incident",
    "browser_operation_lock",
    "clear_incident",
    "choose_reusable_page",
    "classify_browser_state",
    "guarded_click",
    "open_incident",
    "require_real_action_approval",
    "safe_stem",
]
