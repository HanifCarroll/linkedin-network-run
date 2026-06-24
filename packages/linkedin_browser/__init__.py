"""Shared Playwright browser automation primitives."""

from .artifacts import ArtifactRef, ArtifactWriter
from .config import (
    DEFAULT_BROWSER_PROFILE_NAME,
    DEFAULT_CHROME_USER_DATA_DIR,
    LINKEDIN_PROFILE_ENV,
    LINKEDIN_PROFILE_NAME_ENV,
    ChromeProfileConfig,
    chrome_profile_from_env,
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
    "BrowserBlockKind",
    "BrowserSession",
    "BrowserStateClassification",
    "BrowserStateEvidence",
    "ChromeProfileConfig",
    "DEFAULT_BROWSER_PROFILE_NAME",
    "DEFAULT_CHROME_USER_DATA_DIR",
    "GuardedActionResult",
    "LINKEDIN_PROFILE_ENV",
    "LINKEDIN_PROFILE_NAME_ENV",
    "PageReusePolicy",
    "RealAction",
    "RealActionApproval",
    "UnsafeRealActionError",
    "choose_reusable_page",
    "chrome_profile_from_env",
    "classify_browser_state",
    "guarded_click",
    "require_real_action_approval",
]
