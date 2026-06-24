"""Shared Playwright browser automation primitives."""

from .artifacts import ArtifactRef, ArtifactWriter
from .config import (
    DEFAULT_BROWSER_PROFILE_NAME,
    DEFAULT_CHROME_USER_DATA_DIR,
    DEFAULT_PLAYWRITER_CDP_URL,
    LINKEDIN_BROWSER_CHANNEL_ENV,
    LINKEDIN_BROWSER_HEADLESS_ENV,
    LINKEDIN_CDP_URL_ENV,
    LINKEDIN_PROFILE_ENV,
    LINKEDIN_PROFILE_NAME_ENV,
    ChromeProfileConfig,
    chrome_launch_env,
    chrome_profile_from_env,
)
from .playwright import BrowserContextHandle, open_linkedin_browser_context
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
    "BrowserContextHandle",
    "BrowserSession",
    "BrowserStateClassification",
    "BrowserStateEvidence",
    "ChromeProfileConfig",
    "DEFAULT_BROWSER_PROFILE_NAME",
    "DEFAULT_CHROME_USER_DATA_DIR",
    "DEFAULT_PLAYWRITER_CDP_URL",
    "GuardedActionResult",
    "LINKEDIN_BROWSER_CHANNEL_ENV",
    "LINKEDIN_BROWSER_HEADLESS_ENV",
    "LINKEDIN_CDP_URL_ENV",
    "LINKEDIN_PROFILE_ENV",
    "LINKEDIN_PROFILE_NAME_ENV",
    "PageReusePolicy",
    "RealAction",
    "RealActionApproval",
    "UnsafeRealActionError",
    "choose_reusable_page",
    "chrome_launch_env",
    "chrome_profile_from_env",
    "classify_browser_state",
    "guarded_click",
    "open_linkedin_browser_context",
    "require_real_action_approval",
]
