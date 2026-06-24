"""Local access-token helpers for the review UI."""

from __future__ import annotations

from dataclasses import dataclass
from secrets import compare_digest, token_urlsafe

AUTH_FORM_FIELD = "access_token"
AUTH_HEADER = "x-linkedin-ui-token"
AUTH_QUERY_PARAM = "access_token"


@dataclass(frozen=True)
class LocalAccessToken:
    """Generated token required for state-changing review UI routes."""

    token: str

    @classmethod
    def generate(cls) -> LocalAccessToken:
        return cls(token=token_urlsafe(32))

    def verify(self, supplied: str | None) -> bool:
        return supplied is not None and compare_digest(self.token, supplied)
