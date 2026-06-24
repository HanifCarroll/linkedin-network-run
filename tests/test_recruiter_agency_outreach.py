from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import apps.recruiter_agency_outreach.cli as recruiter_cli
import apps.recruiter_agency_outreach.daily as daily_module
import apps.recruiter_agency_outreach.send as send_module
from apps.recruiter_agency_outreach.cli import build_parser, main
from apps.recruiter_agency_outreach.daily import DailyOptions, daily_buckets, run_daily
from apps.recruiter_agency_outreach.dashboard import (
    build_agency_pool_diagnosis,
    build_agency_pool_next_action,
    build_dashboard_report,
    ready_leads,
)
from apps.recruiter_agency_outreach.drafts import draft_messages
from apps.recruiter_agency_outreach.models import (
    AgencyAccount,
    AgencyAccountStatus,
    AgencyContactCandidate,
    AgencyContactCandidateStatus,
    AgencyContactReviewStatus,
    Lead,
    LeadStatus,
    LeadType,
    MessageDraft,
    MessageStatus,
    OutreachState,
)
from apps.recruiter_agency_outreach.send import (
    MessageSendResult,
    SendMessageOptions,
    send_message,
)
from apps.recruiter_agency_outreach.sourcing import (
    import_account_capture,
    import_agency_source_capture,
    import_salesnav_capture,
    load_agency_source_csv,
    promote_agency_contact_candidates,
    review_agency_contact_candidate,
    validate_agency_source_capture,
)
from apps.recruiter_agency_outreach.storage import Store

FIXTURES = Path(__file__).parent / "fixtures" / "recruiter_agency_outreach"


def test_import_capture_classifies_contract_recruiter_and_drafts() -> None:
    state = OutreachState()
    capture = {
        "source": "ASAP - Contract Recruiters Staffing",
        "rows": [
            {
                "index": 0,
                "name": "Riley Recruiter",
                "text": (
                    "Riley Recruiter\nSenior Technical Recruiter\n"
                    "Acme Staffing\nContract React TypeScript roles"
                ),
                "profileUrl": "https://www.linkedin.com/sales/lead/abc?_ntb=x",
                "menuState": "connectable",
            }
        ],
    }

    summary = import_salesnav_capture(state, capture)
    assert summary.eligible == 1
    assert len(state.leads) == 1
    lead = state.leads[0]
    assert lead.lead_type == LeadType.CONTRACT_RECRUITER
    assert lead.status == LeadStatus.ELIGIBLE
    assert lead.title == "Senior Technical Recruiter"

    report = draft_messages(state, 10)
    assert len(report.items) == 1
    assert state.leads[0].message_status == MessageStatus.DRAFTED
    assert state.leads[0].draft is not None
    assert "I saw that you recruit for contract technical roles" in state.leads[0].draft.body
    assert "Are you the right person to ask about contract roles" in state.leads[0].draft.body


def test_agency_source_promotion_requires_salesnav_identity() -> None:
    capture = load_agency_source_csv(
        FIXTURES / "agency_directory.csv",
        source="Reviewed agency directory",
        source_type="manual_directory",
    )
    assert validate_agency_source_capture(capture) == []

    state = OutreachState()
    summary = import_agency_source_capture(state, capture)
    assert summary.contact_candidates_stored == 2
    assert len(state.leads) == 0

    for candidate in state.agency_contact_candidates:
        review_agency_contact_candidate(
            state,
            candidate_id=candidate.id,
            review_status=AgencyContactReviewStatus.APPROVED,
        )

    promoted = promote_agency_contact_candidates(
        state,
        limit=10,
        draft=True,
        allow_multiple_per_agency=True,
    )
    assert promoted.stored == 1
    assert promoted.drafted == 1
    assert len(promoted.skipped) == 1
    assert promoted.skipped[0].reason == "resolve_agency_contact_salesnav_identity"

    lead = promoted.leads[0]
    assert lead.profile_url == "https://www.linkedin.com/sales/lead/ACwAA123ABC,NAME_SEARCH,abcdef"
    assert lead.sales_profile_urn == "urn:li:fs_salesProfile:(ACwAA123ABC,NAME_SEARCH,abcdef)"
    assert (
        "Public LinkedIn profile: https://www.linkedin.com/in/lorenzo-fernandez-297017b/"
        in lead.evidence_text
    )
    assert lead.draft is not None


def test_import_account_capture_qualifies_agency_accounts() -> None:
    state = OutreachState()
    summary = import_account_capture(
        state,
        {
            "source": "ASAP - Agency Accounts Product Studio",
            "rows": [
                {
                    "name": "Bright Product Studio",
                    "text": "Software Development custom software AI MVP product launches",
                    "accountUrl": "https://www.linkedin.com/sales/company/12345?_ntb=x",
                    "website": "https://bright.example.com",
                    "industry": "Software Development",
                }
            ],
        },
    )

    assert summary.qualified == 1
    assert len(state.agency_accounts) == 1
    account = state.agency_accounts[0]
    assert account.status == AgencyAccountStatus.QUALIFIED
    assert account.account_url == "https://www.linkedin.com/sales/company/12345"
    assert account.domain == "bright.example.com"


def test_next_action_surfaces_salesnav_identity_resolution() -> None:
    state = OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_bright",
                source="manual",
                name="Bright Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=90,
            )
        ],
        agency_contact_candidates=[
            AgencyContactCandidate(
                id="agc_jane",
                agency_account_id="acct_bright",
                agency_account_name="Bright Studio",
                source="website_enrichment",
                status=AgencyContactCandidateStatus.WEBSITE_CONTACT_CANDIDATE,
                review_status=AgencyContactReviewStatus.APPROVED,
                name="Jane Doe",
                title="Founder",
                profile_url="https://www.linkedin.com/in/jane-doe/",
            )
        ],
    )

    next_action = build_agency_pool_next_action(state, "/tmp/outreach.sqlite")
    assert next_action.action == "resolve_agency_contact_salesnav_identity"
    assert next_action.candidate is not None
    assert next_action.candidate.profile_url == "https://www.linkedin.com/in/jane-doe/"


def test_missing_linkedin_company_url_blocks_account_scoped_search_first() -> None:
    state = OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_missing",
                source="manual",
                name="Missing Company URL Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=92,
                website="https://missing.example.com",
            )
        ]
    )

    diagnosis = build_agency_pool_diagnosis(state, "/tmp/outreach.sqlite")
    assert diagnosis.missing_linkedin_company_url == 1
    assert diagnosis.accounts[0].next_step == "missing_linkedin_company_url"
    next_action = build_agency_pool_next_action(state, "/tmp/outreach.sqlite")
    assert next_action.action == "missing_linkedin_company_url"


def test_guarded_send_flow_requires_dry_run_ready_and_updates_dashboard(tmp_path: Path) -> None:
    store = Store(tmp_path)
    state = OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_bright",
                source="manual",
                name="Bright Product Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=95,
            )
        ],
        leads=[
            Lead(
                id="lead_fixture",
                source="Agency website contact - Bright Product Studio",
                name="Dana Delivery",
                first_name="Dana",
                lead_type=LeadType.AGENCY_DELIVERY,
                status=LeadStatus.ELIGIBLE,
                message_status=MessageStatus.DRAFTED,
                fit_score=95,
                profile_url="https://www.linkedin.com/sales/lead/dana",
                agency_account_id="acct_bright",
                agency_account_name="Bright Product Studio",
                draft=MessageDraft(subject="Subject", body="Body", angle="agency"),
            )
        ],
    )
    store.save(state)

    with pytest.raises(ValueError, match="real sends require dry_run_ready"):
        send_message(
            store,
            SendMessageOptions(
                lead_id="lead_fixture",
                session="auto",
                allow_send=True,
                result_path=str(FIXTURES / "sent_clicked.json"),
            ),
        )

    with pytest.raises(ValueError, match="real send result requires --allow-send"):
        send_message(
            store,
            SendMessageOptions(
                lead_id="lead_fixture",
                session="auto",
                result_path=str(FIXTURES / "sent_clicked.json"),
            ),
        )

    send_message(
        store,
        SendMessageOptions(
            lead_id="lead_fixture",
            session="auto",
            result_path=str(FIXTURES / "dry_run_messageable.json"),
        ),
    )
    loaded = store.load()
    assert loaded.leads[0].message_status == MessageStatus.DRY_RUN_READY
    assert len(ready_leads(loaded, "agency")) == 1

    report = build_dashboard_report(loaded, str(store.state_path), allow_send=True)
    assert report.ready_counts.agencies == 1

    send_message(
        store,
        SendMessageOptions(
            lead_id="lead_fixture",
            session="auto",
            allow_send=True,
            result_path=str(FIXTURES / "sent_clicked.json"),
        ),
    )
    sent = store.load()
    assert sent.leads[0].message_status == MessageStatus.SENT
    assert len(sent.leads[0].send_attempts) == 2


def test_send_message_uses_browser_when_result_path_is_missing(tmp_path: Path) -> None:
    class FakeMessageBrowser:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed = False

        def send_message(
            self,
            config: dict[str, object],
            *,
            dry_run: bool,
            allow_send: bool,
        ) -> tuple[MessageSendResult, str]:
            self.calls.append(
                {"config": config, "dry_run": dry_run, "allow_send": allow_send}
            )
            out = tmp_path / "message-result.json"
            out.write_text(
                json.dumps(
                    {
                        "status": "dry-run-messageable",
                        "dryRun": True,
                        "url": "https://www.linkedin.com/sales/lead/dana",
                        "action": {"status": "ok"},
                    }
                )
            )
            return MessageSendResult.from_mapping(json.loads(out.read_text())), str(out)

        def close(self) -> None:
            self.closed = True

    store = Store(tmp_path)
    state = _sendable_state(message_status=MessageStatus.DRAFTED)
    store.save(state)
    browser = FakeMessageBrowser()

    result = send_message(
        store,
        SendMessageOptions(
            lead_id="lead_fixture",
            session="auto",
            browser=browser,
        ),
    )

    assert "status=dry-run-messageable" in result
    assert browser.calls[0]["dry_run"] is True
    assert browser.calls[0]["allow_send"] is False
    assert browser.closed is True
    loaded = store.load()
    assert loaded.leads[0].message_status == MessageStatus.DRY_RUN_READY
    assert loaded.leads[0].send_attempts[0].out_path == str(tmp_path / "message-result.json")


def test_run_daily_is_no_send_and_agency_bucket_is_account_first(tmp_path: Path) -> None:
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="run-daily is sourcing-only"):
        run_daily(store, DailyOptions(session="auto", allow_send=True))

    buckets = daily_buckets(target_agencies=5, target_recruiters=5)
    assert buckets[0] == ("agency", [], 5)
    assert buckets[1][0] == "recruiter"


def test_run_daily_validates_drafted_leads_with_live_dry_run_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeMessageBrowser:
        def __init__(self, out_dir: Path) -> None:
            self.out_dir = out_dir
            self.closed = False
            instances.append(self)

        def send_message(
            self,
            config: dict[str, object],
            *,
            dry_run: bool,
            allow_send: bool,
        ) -> tuple[MessageSendResult, str]:
            assert dry_run is True
            assert allow_send is False
            candidate = config["candidate"]
            assert isinstance(candidate, dict)
            out = self.out_dir / f"{candidate['id']}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "status": "dry-run-messageable",
                        "dryRun": True,
                        "url": "https://www.linkedin.com/sales/lead/dana",
                        "action": {"status": "ok"},
                    }
                )
            )
            return MessageSendResult.from_mapping(json.loads(out.read_text())), str(out)

        def close(self) -> None:
            self.closed = True

    instances: list[FakeMessageBrowser] = []
    monkeypatch.setattr(
        send_module,
        "_default_message_browser",
        lambda options, store: FakeMessageBrowser(store.dir / "message-results"),
    )
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRAFTED))

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "run-daily",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
                "--refresh-saved-searches",
                "--print-markdown",
            ]
        )
        == 0
    )

    assert instances and instances[0].closed is True
    output = capsys.readouterr().out
    assert "Ready now: `1` agencies, `0` recruiters" in output
    loaded = store.load()
    assert loaded.leads[0].message_status == MessageStatus.DRY_RUN_READY
    assert [event.phase for event in loaded.run_events] == [
        "run-start",
        "send-message",
        "run-finish",
    ]
    assert "--refresh-saved-searches" in loaded.run_events[0].args
    assert loaded.run_events[1].result == "dry-run-messageable"
    assert loaded.run_events[-1].result == "completed"


def test_run_daily_captures_recruiters_and_validates_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCaptureBrowser:
        def __init__(self, source: str, out_dir: Path) -> None:
            self.source = source
            self.out_dir = out_dir
            self.closed = False
            capture_instances.append(self)

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
        ) -> tuple[object, str]:
            self.call = {
                "source": source,
                "url": url,
                "pages": pages,
                "limit": limit,
                "stop_after_connectable": stop_after_connectable,
                "only_connectable": only_connectable,
                "row_scroll_delay_ms": row_scroll_delay_ms,
            }
            self.out_dir.mkdir(parents=True, exist_ok=True)
            artifact = self.out_dir / "people.json"
            artifact.write_text(
                json.dumps(
                    {
                        "source": source,
                        "capturedAt": "2026-06-24T12:00:00Z",
                        "rows": [
                            {
                                "index": 0,
                                "name": "Riley Recruiter",
                                "text": (
                                    "Riley Recruiter\nSenior Contract Recruiter\n"
                                    "Acme Staffing\nContract React TypeScript roles"
                                ),
                                "profileUrl": "https://www.linkedin.com/sales/lead/riley",
                                "menuState": "connectable",
                            }
                        ],
                    }
                )
            )
            return object(), str(artifact)

        def close(self) -> None:
            self.closed = True

    class FakeMessageBrowser:
        def send_message(
            self,
            config: dict[str, object],
            *,
            dry_run: bool,
            allow_send: bool,
        ) -> tuple[MessageSendResult, str]:
            assert dry_run is True
            assert allow_send is False
            candidate = config["candidate"]
            assert isinstance(candidate, dict)
            out = tmp_path / "message-results" / f"{candidate['id']}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"status": "dry-run-messageable", "dryRun": True}))
            return MessageSendResult.from_mapping(json.loads(out.read_text())), str(out)

        def close(self) -> None:
            pass

    capture_instances: list[FakeCaptureBrowser] = []
    monkeypatch.setattr(
        daily_module,
        "_capture_browser",
        lambda store, options, run_id, source, round_number: FakeCaptureBrowser(
            source,
            store.dir / "captures" / run_id / source / str(round_number),
        ),
    )
    monkeypatch.setattr(
        send_module,
        "_default_message_browser",
        lambda options, store: FakeMessageBrowser(),
    )

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "run-daily",
                "--session",
                "auto",
                "--target-agencies",
                "0",
                "--target-recruiters",
                "1",
                "--max-capture-rounds",
                "1",
            ]
        )
        == 0
    )

    assert len(capture_instances) == 1
    assert capture_instances[0].closed is True
    assert capture_instances[0].call["source"] == "ASAP - Contract Recruiter Titles"
    assert "/sales/search/people?query=" in str(capture_instances[0].call["url"])
    state = Store(tmp_path).load()
    assert state.leads[0].name == "Riley Recruiter"
    assert state.leads[0].message_status == MessageStatus.DRY_RUN_READY
    assert state.run_events[-1].result == "completed"


def test_run_daily_captures_agency_accounts_contacts_and_validates_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAccountBrowser:
        def __init__(self, source: str, out_dir: Path) -> None:
            self.source = source
            self.out_dir = out_dir
            self.closed = False
            account_instances.append(self)

        def capture_accounts(
            self,
            *,
            source: str,
            url: str | None = None,
            pages: int = 1,
            limit: int = 25,
        ) -> tuple[object, str]:
            self.call = {"source": source, "url": url, "pages": pages, "limit": limit}
            self.out_dir.mkdir(parents=True, exist_ok=True)
            artifact = self.out_dir / "accounts.json"
            artifact.write_text(
                json.dumps(
                    {
                        "source": source,
                        "capturedAt": "2026-06-24T12:00:00Z",
                        "rows": [
                            {
                                "index": 0,
                                "name": "Bright Product Studio",
                                "text": (
                                    "Software Development custom software AI MVP "
                                    "product launches"
                                ),
                                "accountUrl": "https://www.linkedin.com/sales/company/12345",
                                "website": "https://bright.example.com",
                                "industry": "Software Development",
                            }
                        ],
                    }
                )
            )
            return object(), str(artifact)

        def close(self) -> None:
            self.closed = True

    class FakePeopleBrowser:
        def __init__(self, source: str, out_dir: Path) -> None:
            self.source = source
            self.out_dir = out_dir
            self.closed = False
            people_instances.append(self)

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
        ) -> tuple[object, str]:
            self.call = {
                "source": source,
                "url": url,
                "pages": pages,
                "limit": limit,
                "stop_after_connectable": stop_after_connectable,
                "only_connectable": only_connectable,
                "row_scroll_delay_ms": row_scroll_delay_ms,
            }
            self.out_dir.mkdir(parents=True, exist_ok=True)
            artifact = self.out_dir / "people.json"
            artifact.write_text(
                json.dumps(
                    {
                        "source": source,
                        "capturedAt": "2026-06-24T12:00:00Z",
                        "rows": [
                            {
                                "index": 0,
                                "name": "Dana Delivery",
                                "text": (
                                    "Dana Delivery\nFounder\nBright Product Studio\n"
                                    "AI MVP product studio custom software"
                                ),
                                "profileUrl": "https://www.linkedin.com/sales/lead/dana",
                                "menuState": "connectable",
                            }
                        ],
                    }
                )
            )
            return object(), str(artifact)

        def close(self) -> None:
            self.closed = True

    class FakeMessageBrowser:
        def send_message(
            self,
            config: dict[str, object],
            *,
            dry_run: bool,
            allow_send: bool,
        ) -> tuple[MessageSendResult, str]:
            assert dry_run is True
            assert allow_send is False
            candidate = config["candidate"]
            assert isinstance(candidate, dict)
            out = tmp_path / "message-results" / f"{candidate['id']}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"status": "dry-run-messageable", "dryRun": True}))
            return MessageSendResult.from_mapping(json.loads(out.read_text())), str(out)

        def close(self) -> None:
            pass

    account_instances: list[FakeAccountBrowser] = []
    people_instances: list[FakePeopleBrowser] = []
    monkeypatch.setattr(
        daily_module,
        "_account_browser",
        lambda store, options, run_id, source, round_number: FakeAccountBrowser(
            source,
            store.dir / "account-captures" / run_id / source / str(round_number),
        ),
    )
    monkeypatch.setattr(
        daily_module,
        "_capture_browser",
        lambda store, options, run_id, source, round_number: FakePeopleBrowser(
            source,
            store.dir / "captures" / run_id / source / str(round_number),
        ),
    )
    monkeypatch.setattr(
        send_module,
        "_default_message_browser",
        lambda options, store: FakeMessageBrowser(),
    )

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "run-daily",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
                "--max-capture-rounds",
                "1",
            ]
        )
        == 0
    )

    assert len(account_instances) == 1
    assert account_instances[0].closed is True
    assert "/sales/search/company?query=" in str(account_instances[0].call["url"])
    assert len(people_instances) == 1
    assert people_instances[0].closed is True
    assert "Bright Product Studio" in str(people_instances[0].call["source"])
    assert "CURRENT_COMPANY" in str(people_instances[0].call["url"])
    state = Store(tmp_path).load()
    assert state.agency_accounts[0].contact_capture_count == 1
    assert state.leads[0].name == "Dana Delivery"
    assert state.leads[0].agency_account_name == "Bright Product Studio"
    assert state.leads[0].message_status == MessageStatus.DRY_RUN_READY


def test_run_daily_records_failed_lifecycle_when_validation_browser_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingMessageBrowser:
        def send_message(
            self,
            config: dict[str, object],
            *,
            dry_run: bool,
            allow_send: bool,
        ) -> tuple[MessageSendResult, str]:
            _ = config, dry_run, allow_send
            raise RuntimeError("browser blocked")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        send_module,
        "_default_message_browser",
        lambda options, store: FailingMessageBrowser(),
    )
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRAFTED))

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "run-daily",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
            ]
        )
        == 1
    )

    assert "browser blocked" in capsys.readouterr().err
    loaded = store.load()
    assert [event.phase for event in loaded.run_events] == ["run-start", "run-finish"]
    assert loaded.run_events[-1].result == "failed"
    assert loaded.run_events[-1].blocker == "browser blocked"


def test_cli_namespace_is_wired(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["--state-dir", str(tmp_path), "dashboard", "--print-markdown"])
    assert args.command == "dashboard"
    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "run-daily",
                "--session",
                "auto",
                "--target-agencies",
                "0",
                "--target-recruiters",
                "0",
            ]
        )
        == 0
    )


def test_cli_state_parity_commands_update_and_render(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRAFTED))
    body_file = tmp_path / "revision.txt"
    body_file.write_text("Revised message body")

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "revise",
                "--lead-id",
                "lead_fixture",
                "--body-file",
                str(body_file),
                "--subject",
                "Updated subject",
            ]
        )
        == 0
    )
    revised_draft = store.load().leads[0].draft
    assert revised_draft is not None
    assert revised_draft.body == "Revised message body"

    assert main(["--state-dir", str(tmp_path), "lead", "show", "--lead-id", "lead_fixture"]) == 0
    assert "lead=lead_fixture" in capsys.readouterr().out

    assert main(["--state-dir", str(tmp_path), "queue", "--lead-id", "lead_fixture"]) == 0
    assert "lead_fixture" in capsys.readouterr().out

    assert main(["--state-dir", str(tmp_path), "accounts"]) == 0
    assert "Bright Product Studio" in capsys.readouterr().out

    assert main(["--state-dir", str(tmp_path), "report"]) == 0
    assert "by message status:" in capsys.readouterr().out

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "mark-message",
                "--lead-id",
                "lead_fixture",
                "--status",
                "needs_edit",
                "--note",
                "manual review",
            ]
        )
        == 0
    )
    marked = store.load().leads[0]
    assert marked.message_status == MessageStatus.NEEDS_EDIT
    assert "manual review" in marked.notes

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "reject",
                "--lead-id",
                "lead_fixture",
                "--reason",
                "not a fit",
            ]
        )
        == 0
    )
    rejected = store.load().leads[0]
    assert rejected.status == LeadStatus.REJECTED
    assert "not a fit" in rejected.reject_reasons


def test_send_ready_applies_structured_results_and_records_last_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRY_RUN_READY))
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "lead_fixture.json").write_text((FIXTURES / "sent_clicked.json").read_text())

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "send-ready",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
                "--allow-send",
                "--result-dir",
                str(result_dir),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "sent=1 agencies,0 recruiters" in output
    sent = store.load()
    assert sent.leads[0].message_status == MessageStatus.SENT
    assert [event.phase for event in sent.run_events] == [
        "run-start",
        "send-message",
        "run-finish",
    ]

    assert main(["--state-dir", str(tmp_path), "last-run"]) == 0
    assert "status=completed" in capsys.readouterr().out

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "recommend-next-run",
                "--target-recruiters",
                "0",
                "--allow-send",
            ]
        )
        == 0
    )
    assert "no retry is needed" in capsys.readouterr().out


def test_send_ready_uses_live_browser_when_result_dir_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeMessageBrowser:
        def __init__(self, out_dir: Path) -> None:
            self.out_dir = out_dir
            self.closed = False
            instances.append(self)

        def send_message(
            self,
            config: dict[str, object],
            *,
            dry_run: bool,
            allow_send: bool,
        ) -> tuple[MessageSendResult, str]:
            assert dry_run is False
            assert allow_send is True
            candidate = config["candidate"]
            assert isinstance(candidate, dict)
            out = self.out_dir / f"{candidate['id']}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "status": "sent-clicked",
                        "dryRun": False,
                        "url": "https://www.linkedin.com/sales/lead/dana",
                        "send": {"status": "clicked"},
                    }
                )
            )
            return MessageSendResult.from_mapping(json.loads(out.read_text())), str(out)

        def close(self) -> None:
            self.closed = True

    instances: list[FakeMessageBrowser] = []
    monkeypatch.setattr(
        send_module,
        "_default_message_browser",
        lambda options, store: FakeMessageBrowser(store.dir / "message-results"),
    )
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRY_RUN_READY))

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "send-ready",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
                "--allow-send",
            ]
        )
        == 0
    )

    assert instances and instances[0].closed is True
    output = capsys.readouterr().out
    assert "sent=1 agencies,0 recruiters" in output
    sent = store.load()
    assert sent.leads[0].message_status == MessageStatus.SENT
    assert sent.leads[0].send_attempts[0].out_path.endswith("lead_fixture.json")


def test_send_ready_requires_real_send_result_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = Store(tmp_path)
    store.save(_sendable_state(message_status=MessageStatus.DRY_RUN_READY))
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "lead_fixture.json").write_text(
        (FIXTURES / "dry_run_messageable.json").read_text()
    )

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "send-ready",
                "--session",
                "auto",
                "--target-agencies",
                "1",
                "--target-recruiters",
                "0",
                "--allow-send",
                "--result-dir",
                str(result_dir),
            ]
        )
        == 1
    )
    assert "dry_run=true" in capsys.readouterr().err
    assert store.load().run_events[-1].result == "failed"


def test_serve_runs_review_ui_without_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls.append({"app": app, "host": host, "port": port, "log_level": log_level})

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert (
        main(
            [
                "serve",
                "--addr",
                "127.0.0.1:8766",
                "--access-token",
                "test-token",
                "--log-level",
                "warning",
            ]
        )
        == 0
    )

    assert calls
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8766
    assert calls[0]["log_level"] == "warning"
    assert (
        "review_ui=http://127.0.0.1:8766/recruiter-agency?access_token=test-token"
        in capsys.readouterr().out
    )


def test_live_capture_command_uses_browser_and_imports_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeCaptureBrowser:
        instances: list[FakeCaptureBrowser] = []

        def __init__(self, out_dir: Path) -> None:
            self.out_dir = out_dir
            self.calls: list[str] = []
            self.closed = False
            FakeCaptureBrowser.instances.append(self)

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
        ) -> tuple[object, str]:
            self.calls.append(
                f"capture:{source}:pages={pages}:limit={limit}:only={only_connectable}:url={url}"
            )
            _ = stop_after_connectable, row_scroll_delay_ms
            self.out_dir.mkdir(parents=True, exist_ok=True)
            artifact = self.out_dir / "capture-page.json"
            artifact.write_text(
                json.dumps(
                    {
                        "source": source,
                        "capturedAt": "2026-06-24T12:00:00Z",
                        "rows": [
                            {
                                "index": 0,
                                "name": "Riley Recruiter",
                                "text": (
                                    "Riley Recruiter\nSenior Technical Recruiter\n"
                                    "Acme Staffing\nContract React TypeScript roles"
                                ),
                                "profileUrl": "https://www.linkedin.com/sales/lead/abc?_ntb=x",
                                "menuState": "connectable",
                            }
                        ],
                    }
                )
            )
            return object(), str(artifact)

        def close(self) -> None:
            self.closed = True

    def fake_browser(args: Namespace, store: Store) -> FakeCaptureBrowser:
        out_dir = Path(args.out_dir or store.dir / "captures")
        return FakeCaptureBrowser(out_dir)

    monkeypatch.setattr(recruiter_cli, "_browser_from_capture_args", fake_browser)

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "capture",
                "--session",
                "auto",
                "--source",
                "ASAP - Contract Recruiter Titles",
                "--url",
                "https://www.linkedin.com/sales/search/people?savedSearchId=1",
                "--pages",
                "2",
                "--limit",
                "4",
                "--only-connectable",
            ]
        )
        == 0
    )
    browser = FakeCaptureBrowser.instances[-1]
    assert browser.calls == [
        (
            "capture:ASAP - Contract Recruiter Titles:pages=2:limit=4:"
            "only=True:url=https://www.linkedin.com/sales/search/people?savedSearchId=1"
        )
    ]
    assert browser.closed is True
    state = Store(tmp_path).load()
    assert [lead.name for lead in state.leads] == ["Riley Recruiter"]
    assert "artifact=" in capsys.readouterr().out


def test_live_account_capture_command_uses_browser_and_imports_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAccountBrowser:
        instances: list[FakeAccountBrowser] = []

        def __init__(self, out_dir: Path) -> None:
            self.out_dir = out_dir
            self.calls: list[str] = []
            self.closed = False
            FakeAccountBrowser.instances.append(self)

        def capture_accounts(
            self,
            *,
            source: str,
            url: str | None = None,
            pages: int = 1,
            limit: int = 25,
        ) -> tuple[object, str]:
            self.calls.append(
                f"accounts:{source}:pages={pages}:limit={limit}:url={url}"
            )
            self.out_dir.mkdir(parents=True, exist_ok=True)
            artifact = self.out_dir / "accounts.json"
            artifact.write_text(
                json.dumps(
                    {
                        "source": source,
                        "capturedAt": "2026-06-24T12:00:00Z",
                        "rows": [
                            {
                                "index": 0,
                                "name": "Bright Product Studio",
                                "text": (
                                    "Software Development custom software AI MVP "
                                    "product launches"
                                ),
                                "accountUrl": "https://www.linkedin.com/sales/company/12345?_ntb=x",
                                "website": "https://bright.example.com",
                                "industry": "Software Development",
                            }
                        ],
                    }
                )
            )
            return object(), str(artifact)

        def close(self) -> None:
            self.closed = True

    def fake_browser(args: Namespace, store: Store) -> FakeAccountBrowser:
        out_dir = Path(args.out_dir or store.dir / "account-captures")
        return FakeAccountBrowser(out_dir)

    monkeypatch.setattr(recruiter_cli, "_account_browser_from_args", fake_browser)

    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "capture-accounts",
                "--session",
                "auto",
                "--source",
                "ASAP - Agency Accounts Product Studio",
                "--url",
                "https://www.linkedin.com/sales/search/company?savedSearchId=1",
                "--pages",
                "2",
                "--limit",
                "4",
            ]
        )
        == 0
    )
    browser = FakeAccountBrowser.instances[-1]
    assert browser.calls == [
        (
            "accounts:ASAP - Agency Accounts Product Studio:pages=2:limit=4:"
            "url=https://www.linkedin.com/sales/search/company?savedSearchId=1"
        )
    ]
    assert browser.closed is True
    state = Store(tmp_path).load()
    assert [account.name for account in state.agency_accounts] == ["Bright Product Studio"]
    assert "artifact=" in capsys.readouterr().out


def _sendable_state(*, message_status: MessageStatus) -> OutreachState:
    return OutreachState(
        agency_accounts=[
            AgencyAccount(
                id="acct_bright",
                source="manual",
                name="Bright Product Studio",
                status=AgencyAccountStatus.QUALIFIED,
                fit_score=95,
            )
        ],
        leads=[
            Lead(
                id="lead_fixture",
                source="Agency website contact - Bright Product Studio",
                name="Dana Delivery",
                first_name="Dana",
                lead_type=LeadType.AGENCY_DELIVERY,
                status=LeadStatus.ELIGIBLE,
                message_status=message_status,
                fit_score=95,
                profile_url="https://www.linkedin.com/sales/lead/dana",
                agency_account_id="acct_bright",
                agency_account_name="Bright Product Studio",
                draft=MessageDraft(subject="Subject", body="Body", angle="agency"),
            )
        ],
    )
