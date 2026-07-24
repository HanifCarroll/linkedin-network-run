from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import ClassVar

import pytest

import apps.network_automation.cli as network_cli
from apps.network_automation.models import (
    AcceptanceFollowupRecord,
    AcceptanceFollowupSendResult,
    AcceptanceLeadListSaveResult,
    AcceptanceListArtifact,
    CandidateObservation,
    PendingCandidateObservation,
    PendingCapture,
    PendingWithdrawBatchResult,
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
    acceptance_failures_remaining: ClassVar[int] = 0
    acceptance_rate_limited: ClassVar[bool] = False
    audit_people_count: ClassVar[int] = 101
    audit_recent_names: ClassVar[list[str]] = ["Duplicate Lead"]

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
        self.recoveries = 0
        FakeLiveBrowserClient.instances.append(self)

    def recover_after_failure(self) -> None:
        self.recoveries += 1

    def send_connection(
        self, candidate: CandidateObservation, *, dry_run: bool, allow_send: bool
    ) -> tuple[SalesNavSendResult, str]:
        self.calls.append(f"send:{candidate.name}:dry={dry_run}:allow={allow_send}")
        return (
            read_model(FIXTURES / "send_pending_consulting.json", SalesNavSendResult),
            str(self.out_dir / "send-result.json"),
        )

    def save_acceptance_lead_to_list(
        self,
        record: AcceptanceFollowupRecord,
        *,
        allow_save: bool,
    ) -> tuple[AcceptanceLeadListSaveResult, str]:
        _ = record, allow_save
        raise RuntimeError("lead-list save is not configured for this browser fixture")

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
        capture = read_model(FIXTURES / "capture_consulting.json", SalesNavCapture).model_copy(
            update={"source": source}
        )
        return capture, str(self.out_dir / "capture-page.json")

    def audit_sent_invitations(self, *, load_more: int = 0) -> tuple[SalesNavAudit, str]:
        self.calls.append(f"audit:load_more={load_more}")
        artifact = SalesNavAudit.model_validate(
            {
                "peopleCount": FakeLiveBrowserClient.audit_people_count,
                "recentNames": FakeLiveBrowserClient.audit_recent_names,
                "loadedCount": len(FakeLiveBrowserClient.audit_recent_names),
                "requestedLoadMore": load_more,
                "invitations": [
                    {"name": name, "rowIndex": index}
                    for index, name in enumerate(FakeLiveBrowserClient.audit_recent_names)
                ],
            }
        )
        out = self.out_dir / "audit.json"
        _write_fake_artifact(out, artifact)
        return artifact, str(out)

    def resolve_saved_searches(self, *, url: str, out: Path) -> tuple[SavedSearchArtifact, str]:
        self.calls.append(f"saved-searches:{url}")
        artifact = SavedSearchArtifact.model_validate(
            {
                "capturedAt": "2026-06-24T12:00:00Z",
                "url": url,
                "searches": [
                    {
                        "savedSearchId": "abc",
                        "name": "Consulting - Founder Owner Buyers",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    },
                    {
                        "savedSearchId": "def",
                        "name": "Consulting - Operations Leader Buyers",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=def",
                    },
                    {
                        "savedSearchId": "ghi",
                        "name": "Consulting - Trusted Referral Partners",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=ghi",
                    }
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

    def capture_acceptance_lists(
        self,
        *,
        previous_watermark: list[str],
        out: Path,
        max_load_actions: int = 100,
        watermark_size: int = 25,
    ) -> tuple[AcceptanceListArtifact, str]:
        _ = previous_watermark, out, max_load_actions, watermark_size
        fixture = FIXTURES / "acceptance_lists_baseline.json"
        return read_model(fixture, AcceptanceListArtifact), str(fixture)

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

    def withdraw_loaded_pending(
        self,
        *,
        limit: int,
        threshold_days: int,
        timeout_seconds: float,
        dry_run: bool,
        allow_withdraw: bool,
    ) -> tuple[PendingWithdrawBatchResult, str]:
        _ = timeout_seconds
        self.calls.append(
            f"withdraw-loaded:limit={limit}:threshold={threshold_days}:dry={dry_run}:allow={allow_withdraw}"
        )
        status = "dry-run-withdrawable" if dry_run or not allow_withdraw else "withdrawn"
        result_status = (
            "dry-run-withdrawable" if dry_run or not allow_withdraw else "withdrawn-verified"
        )
        artifact = PendingWithdrawBatchResult.model_validate(
            {
                "status": status,
                "results": [
                    {
                        "candidate": {
                            "name": "Stale Invite",
                            "profileUrl": "https://www.linkedin.com/in/stale",
                            "ageText": "Sent 3 weeks ago",
                        },
                        "status": result_status,
                        "detail": {"source": "loaded-page-bottom"},
                    }
                ],
            }
        )
        path = self.out_dir / "withdraw-loaded-result.json"
        _write_fake_artifact(path, artifact)
        return artifact, str(path)


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
        return (
            read_model(FIXTURES / "send_pending_consulting.json", SalesNavSendResult),
            "send.json",
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
                        "name": "Consulting - Founder Owner Buyers",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=abc",
                    },
                    {
                        "savedSearchId": "def",
                        "name": "Consulting - Operations Leader Buyers",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=def",
                    },
                    {
                        "savedSearchId": "ghi",
                        "name": "Consulting - Trusted Referral Partners",
                        "viewUrl": "https://www.linkedin.com/sales/search/people?savedSearchId=ghi",
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
        if source == "Consulting - Founder Owner Buyers":
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
    FakeLiveBrowserClient.audit_people_count = 101
    FakeLiveBrowserClient.audit_recent_names = ["Duplicate Lead"]
    monkeypatch.setattr(network_cli, "PlaywriterBrowserClient", FakeLiveBrowserClient)
