from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import ClassVar

import pytest

import apps.network_automation.cli as network_cli
from apps.network_automation.models import (
    AcceptanceCheckCandidate,
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceOutcomeArtifact,
    AcceptedDraftCandidate,
    AcceptedResearchArtifact,
    CandidateObservation,
    PendingCandidateObservation,
    PendingCapture,
    PendingWithdrawResult,
    SalesNavAudit,
    SalesNavCapture,
    SalesNavSendResult,
    SavedSearchArtifact,
)
from apps.network_automation.store import read_model

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "network_automation"


class FakeLiveBrowserClient:
    instances: ClassVar[list[FakeLiveBrowserClient]] = []
    acceptance_status: ClassVar[str] = "accepted"
    fail_acceptance_check: ClassVar[bool] = False

    def __init__(
        self,
        *,
        out_dir: Path,
        session: str | None = None,
        withdraw_timeout_seconds: float = 90.0,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.session = session
        self.withdraw_timeout_seconds = withdraw_timeout_seconds
        self.calls: list[str] = []
        FakeLiveBrowserClient.instances.append(self)

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        self.calls.append(f"send:{candidate.name}:dry={dry_run}:allow={allow_send}")
        return (
            read_model(FIXTURES / "send_pending.json", SalesNavSendResult),
            str(self.out_dir / "send-result.json"),
        )

    def capture_salesnav(
        self,
        *,
        source: str,
        url: str | None = None,
        pages: int = 1,
        limit: int = 25,
        stop_after_connectable: int = 0,
        only_connectable: bool = False,
        row_scroll_delay_ms: int = 250,
    ) -> tuple[SalesNavCapture, str]:
        self.calls.append(
            f"capture:{source}:pages={pages}:limit={limit}:only={only_connectable}:url={url}"
        )
        _ = stop_after_connectable, row_scroll_delay_ms
        capture = read_model(FIXTURES / "capture.json", SalesNavCapture).model_copy(
            update={"source": source}
        )
        return capture, str(self.out_dir / "capture-page.json")

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        self.calls.append(f"audit:load_more={load_more}")
        return (
            read_model(FIXTURES / "audit_101.json", SalesNavAudit),
            str(self.out_dir / "audit.json"),
        )

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        self.calls.append(f"saved-searches:{url}")
        artifact = SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "url": url,
                "searches": [
                    {
                        "savedSearchId": "def",
                        "name": "ASAP - Contract Recruiters Staffing",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=def",
                    },
                    {
                        "savedSearchId": "abc",
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    }
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def check_acceptance_outcomes(
        self,
        *,
        candidates: list[AcceptanceCheckCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptanceOutcomeArtifact, str]:
        self.calls.append(
            f"acceptance-check:{len(candidates)}:offset={offset}:limit={limit}:delay={delay_ms}"
        )
        if FakeLiveBrowserClient.fail_acceptance_check:
            raise RuntimeError("browser timed out")
        selected = candidates[offset : offset + limit] if limit else candidates[offset:]
        artifact = AcceptanceOutcomeArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "input": str(input_path),
                "count": len(selected),
                "offset": offset,
                "limit": limit,
                "totalCandidates": len(candidates),
                "complete": True,
                "rows": [
                    {
                        "source": candidate.source,
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "status": FakeLiveBrowserClient.acceptance_status,
                        "checkedAt": "2026-06-24T12:00:00Z",
                        "relationship": (
                            "1st"
                            if FakeLiveBrowserClient.acceptance_status == "accepted"
                            else None
                        ),
                        "evidence": candidate.name,
                        "note": (
                            "fixture"
                            if FakeLiveBrowserClient.acceptance_status == "accepted"
                            else "security-verification-present"
                        ),
                    }
                    for candidate in selected
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def research_accepted_candidates(
        self,
        *,
        candidates: list[AcceptedDraftCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptedResearchArtifact, str]:
        self.calls.append(
            "accepted-research:"
            f"{len(candidates)}:offset={offset}:limit={limit}:delay={delay_ms}"
        )
        selected = candidates[offset : offset + limit] if limit else candidates[offset:]
        artifact = AcceptedResearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "rows": [
                    {
                        "source": candidate.source,
                        "name": candidate.name,
                        "profileUrl": candidate.profile_url,
                        "salesNavProfileUrl": candidate.sales_nav_profile_url
                        or candidate.profile_url,
                        "publicProfileUrl": "https://www.linkedin.com/in/example-lead",
                        "salesNav": {
                            "name": candidate.name,
                            "title": "Founder",
                            "company": "Example Co",
                            "url": candidate.profile_url,
                        },
                        "companyProfile": {
                            "name": "Example Co",
                            "url": "https://www.linkedin.com/sales/company/example-co",
                            "websiteUrl": "https://example.com",
                            "description": "Example Co builds software products for clients.",
                            "warnings": [],
                        },
                        "companyWebsite": {
                            "url": "https://example.com",
                            "title": "Example Co",
                            "description": "Software product services for teams.",
                            "warnings": [],
                        },
                        "web": {
                            "query": candidate.name,
                            "results": [
                                {
                                    "title": "Example Co",
                                    "url": "https://example.com",
                                    "snippet": "Example Co builds software products for clients.",
                                }
                            ],
                            "warnings": [],
                        },
                    }
                    for candidate in selected
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def capture_pending_invitations(
        self, *, load_more: int = 0, threshold_days: int = 14, out: Path
    ) -> tuple[PendingCapture, str]:
        self.calls.append(f"pending-capture:load_more={load_more}:threshold={threshold_days}")
        artifact = PendingCapture.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "rows": [
                    {
                        "index": 0,
                        "name": "Stale Invite",
                        "profileUrl": "https://www.linkedin.com/in/stale",
                        "ageText": "Sent 3 weeks ago",
                        "ageDays": 21,
                        "eligible": True,
                        "rowText": "Stale Invite Sent 3 weeks ago Withdraw",
                    }
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        self.calls.append(
            f"followup:{record.name}:dry={dry_run}:preview={preview_fill}:allow={allow_send}"
        )
        return (
            read_model(FIXTURES / "followup_preview.json", AcceptanceFollowupSendResult),
            str(self.out_dir / f"{record.id}.json"),
        )

    def withdraw_pending(
        self,
        candidate: PendingCandidateObservation,
        *,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawResult, str]:
        self.calls.append(f"withdraw:{candidate.name}:dry={dry_run}:allow={allow_withdraw}")
        return (
            read_model(FIXTURES / "withdraw_result.json", PendingWithdrawResult),
            str(self.out_dir / "withdraw-result.json"),
        )


class SequenceFollowupBrowser:
    def __init__(self, out_dir: Path, results: list[Path]) -> None:
        self.out_dir = out_dir
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def send_acceptance_followup(
        self,
        record: AcceptanceFollowupRecord,
        *,
        dry_run: bool,
        preview_fill: bool,
        allow_send: bool,
    ) -> tuple[AcceptanceFollowupSendResult, str]:
        self.calls.append(
            {
                "record_id": record.id,
                "dry_run": dry_run,
                "preview_fill": preview_fill,
                "allow_send": allow_send,
            }
        )
        if not self.results:
            raise RuntimeError("no follow-up fixture results remain")
        fixture = self.results.pop(0)
        return (
            read_model(fixture, AcceptanceFollowupSendResult),
            str(self.out_dir / f"{len(self.calls):03d}-{record.id}.json"),
        )


class CandidateCapturingBrowser(FakeLiveBrowserClient):
    def __init__(self) -> None:
        super().__init__(out_dir=Path("/tmp"))
        self.candidate: CandidateObservation | None = None

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        _ = dry_run, allow_send
        self.candidate = candidate
        return read_model(FIXTURES / "send_pending.json", SalesNavSendResult), "send.json"


class AcceptanceCandidateCapturingBrowser(FakeLiveBrowserClient):
    def __init__(self, out_dir: Path) -> None:
        super().__init__(out_dir=out_dir)
        self.acceptance_candidates: list[AcceptanceCheckCandidate] = []

    def check_acceptance_outcomes(
        self,
        *,
        candidates: list[AcceptanceCheckCandidate],
        input_path: Path,
        out: Path,
        offset: int = 0,
        limit: int = 0,
        delay_ms: int = 500,
    ) -> tuple[AcceptanceOutcomeArtifact, str]:
        self.acceptance_candidates = candidates
        return super().check_acceptance_outcomes(
            candidates=candidates,
            input_path=input_path,
            out=out,
            offset=offset,
            limit=limit,
            delay_ms=delay_ms,
        )


class ZeroThenNextSourceBrowserClient(FakeLiveBrowserClient):
    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        self.calls.append(f"saved-searches:{url}")
        artifact = SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "url": url,
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": "ASAP - Agency Owners Delivery",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    },
                    {
                        "savedSearchId": "def",
                        "name": "ASAP - Contract Recruiters Staffing",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=def",
                    },
                ],
            }
        )
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def capture_salesnav(
        self,
        *,
        source: str,
        url: str | None = None,
        pages: int = 1,
        limit: int = 25,
        stop_after_connectable: int = 0,
        only_connectable: bool = False,
        row_scroll_delay_ms: int = 250,
    ) -> tuple[SalesNavCapture, str]:
        self.calls.append(
            f"capture:{source}:pages={pages}:limit={limit}:only={only_connectable}:url={url}"
        )
        _ = stop_after_connectable, row_scroll_delay_ms
        if source == "ASAP - Contract Recruiters Staffing":
            artifact = SalesNavCapture.model_validate(
                {
                    "capturedAt": "2026-06-24T12:00:00Z",
                    "source": source,
                    "url": url or "",
                    "resumeUrl": None,
                    "nextUrl": None,
                    "lastScannedUrl": url or "",
                    "endOfResults": True,
                    "rawRowCount": 5,
                    "outputRowCount": 0,
                    "stateCounts": {"unknown": 5},
                    "rows": [],
                }
            )
            return artifact, str(self.out_dir / f"{_safe_file_stem(source)}-empty.json")
        artifact = SalesNavCapture.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "source": source,
                "url": url or "",
                "resumeUrl": url or "",
                "nextUrl": "https://www.linkedin.com/sales/search/people?page=2",
                "lastScannedUrl": url or "",
                "rawRowCount": 1,
                "outputRowCount": 1,
                "stateCounts": {"connectable": 1},
                "rows": [
                    {
                        "index": 1,
                        "name": f"{source} Lead",
                        "profileUrl": f"https://www.linkedin.com/sales/lead/{_safe_file_stem(source)}",
                        "menuState": "connectable",
                        "menuLabels": [{"text": "Connect"}],
                    }
                ],
            }
        )
        return artifact, str(self.out_dir / f"{_safe_file_stem(source)}-capture.json")


def _safe_file_stem(value: str) -> str:
    return value.lower().replace(" ", "-").replace("/", "-")


def _write_fake_artifact(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(model, "model_dump"):
        payload = model.model_dump(mode="json", by_alias=False)
    else:
        payload = model
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _run_id() -> uuid.UUID:
    return uuid.uuid4()


def _install_fake_live_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeLiveBrowserClient.instances.clear()
    FakeLiveBrowserClient.acceptance_status = "accepted"
    FakeLiveBrowserClient.fail_acceptance_check = False
    monkeypatch.setattr(network_cli, "PlaywriterBrowserClient", FakeLiveBrowserClient)
