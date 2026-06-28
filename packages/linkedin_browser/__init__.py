"""Shared Playwright browser automation primitives."""

from .artifacts import ArtifactRef, ArtifactWriter
from .config import (
    DEFAULT_AUTOMATION_CHROME_USER_DATA_DIR,
    DEFAULT_BROWSER_PROFILE_NAME,
    DEFAULT_CHROME_USER_DATA_DIR,
    DEFAULT_PLAYWRITER_CDP_URL,
    LINKEDIN_BROWSER_PROFILE_MODE_ENV,
    LINKEDIN_CDP_URL_ENV,
    LINKEDIN_PROFILE_ENV,
    LINKEDIN_PROFILE_NAME_ENV,
    ChromeProfileConfig,
    chrome_profile_from_env,
    chrome_profile_storage_dir,
)
from .playwright import (
    BrowserContextHandle,
    ManagedChromeSession,
    close_browser_context_handle,
    open_linkedin_browser_context,
    start_managed_chrome_cdp_session,
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
    "BrowserContextHandle",
    "BrowserSession",
    "BrowserStateClassification",
    "BrowserStateEvidence",
    "ChromeProfileConfig",
    "DEFAULT_AUTOMATION_CHROME_USER_DATA_DIR",
    "DEFAULT_BROWSER_PROFILE_NAME",
    "DEFAULT_CHROME_USER_DATA_DIR",
    "DEFAULT_PLAYWRITER_CDP_URL",
    "GuardedActionResult",
    "LINKEDIN_BROWSER_PROFILE_MODE_ENV",
    "LINKEDIN_CDP_URL_ENV",
    "LINKEDIN_PROFILE_ENV",
    "LINKEDIN_PROFILE_NAME_ENV",
    "ManagedChromeSession",
    "PageReusePolicy",
    "RealAction",
    "RealActionApproval",
    "UnsafeRealActionError",
    "choose_reusable_page",
    "close_browser_context_handle",
    "chrome_profile_storage_dir",
    "chrome_profile_from_env",
    "classify_browser_state",
    "guarded_click",
    "open_linkedin_browser_context",
    "require_real_action_approval",
    "start_managed_chrome_cdp_session",
]
