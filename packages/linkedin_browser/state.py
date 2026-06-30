"""Classification for login, checkpoint, rate-limit, and blocked browser states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class BrowserBlockKind(StrEnum):
    CLEAR = "clear"
    LOGIN_REQUIRED = "login-required"
    CHECKPOINT = "checkpoint"
    SECURITY_CHALLENGE = "security-challenge"
    RATE_LIMITED = "rate-limited"
    ACCOUNT_RESTRICTED = "account-restricted"
    BLOCKED = "blocked"
    NETWORK_ERROR = "network-error"


@dataclass(frozen=True)
class BrowserStateEvidence:
    url: str | None = None
    http_status: int | None = None
    network_error: str | None = None
    login_form_present: bool = False
    checkpoint_present: bool = False
    security_challenge_present: bool = False
    security_verification_present: bool = False
    account_restricted_banner_present: bool = False
    blocked_banner_present: bool = False


@dataclass(frozen=True)
class BrowserStateClassification:
    kind: BrowserBlockKind
    reason: str

    @property
    def is_blocking(self) -> bool:
        return self.kind is not BrowserBlockKind.CLEAR


def classify_browser_state(evidence: BrowserStateEvidence) -> BrowserStateClassification:
    url_kind = _classify_url(evidence.url)
    if evidence.http_status == 429:
        return BrowserStateClassification(BrowserBlockKind.RATE_LIMITED, "http-429")
    if evidence.security_challenge_present:
        return BrowserStateClassification(
            BrowserBlockKind.SECURITY_CHALLENGE,
            "security-challenge-present",
        )
    if evidence.security_verification_present:
        return BrowserStateClassification(
            BrowserBlockKind.SECURITY_CHALLENGE,
            "security-verification-present",
        )
    if evidence.checkpoint_present or url_kind is BrowserBlockKind.CHECKPOINT:
        return BrowserStateClassification(BrowserBlockKind.CHECKPOINT, "checkpoint-present")
    if evidence.login_form_present or url_kind is BrowserBlockKind.LOGIN_REQUIRED:
        return BrowserStateClassification(BrowserBlockKind.LOGIN_REQUIRED, "login-required")
    if evidence.account_restricted_banner_present:
        return BrowserStateClassification(
            BrowserBlockKind.ACCOUNT_RESTRICTED,
            "account-restricted-banner-present",
        )
    if evidence.blocked_banner_present:
        return BrowserStateClassification(BrowserBlockKind.BLOCKED, "blocked-banner-present")
    if evidence.http_status in {401, 403}:
        return BrowserStateClassification(BrowserBlockKind.BLOCKED, f"http-{evidence.http_status}")
    if evidence.http_status is not None and evidence.http_status >= 500:
        return BrowserStateClassification(BrowserBlockKind.BLOCKED, f"http-{evidence.http_status}")
    if evidence.network_error:
        return BrowserStateClassification(BrowserBlockKind.NETWORK_ERROR, "network-error")
    return BrowserStateClassification(BrowserBlockKind.CLEAR, "clear")


def _classify_url(url: str | None) -> BrowserBlockKind | None:
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return None
    path = parts.path.rstrip("/")
    if path == "/uas/login" or path.startswith("/uas/login/") or path == "/login":
        return BrowserBlockKind.LOGIN_REQUIRED
    if path == "/checkpoint" or path.startswith("/checkpoint/"):
        return BrowserBlockKind.CHECKPOINT
    return None
