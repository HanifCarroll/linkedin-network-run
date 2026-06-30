"""Guarded LinkedIn message send state transitions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dashboard import bucket_for_lead, lead_matches_sendable_bucket
from .drafts import draft_subject
from .models import Lead, MessageStatus, OutreachState, RunEvent, SendAttempt
from .sourcing import find_lead_by_id
from .storage import Store, append_run_event
from .utils import clean_text, now_iso, truncate_evidence


@dataclass(slots=True)
class MessageCandidate:
    id: str
    name: str
    profile_url: str
    source: str
    lead_type: str
    title: str | None = None
    company: str | None = None
    search_url: str | None = None


@dataclass(slots=True)
class SendMessageOptions:
    lead_id: str
    run_id: str = ""
    session: str = ""
    out_dir: str = ""
    dry_run: bool = False
    allow_send: bool = False
    result_path: str = ""
    browser: Any = None


def build_message_candidate(state: OutreachState, lead: Lead) -> MessageCandidate:
    if not lead.profile_url:
        raise ValueError(f"lead {lead.id} has no profile URL")
    search_url = None
    cursor = state.capture_cursors.get(lead.source)
    if cursor and cursor.resume_url:
        search_url = cursor.resume_url
    return MessageCandidate(
        id=lead.id,
        name=lead.name,
        profile_url=lead.profile_url,
        source=lead.source,
        lead_type=lead.lead_type.value,
        title=lead.title,
        company=lead.company,
        search_url=search_url,
    )


def prepare_message_config(
    state: OutreachState, lead: Lead, dry_run: bool, allow_send: bool
) -> dict[str, Any]:
    if lead.draft is None or not clean_text(lead.draft.body):
        raise ValueError(f"lead {lead.id} has no draft; run draft first")
    candidate = build_message_candidate(state, lead)
    return {
        "candidate": {
            "id": candidate.id,
            "name": candidate.name,
            "profileUrl": candidate.profile_url,
            "searchUrl": candidate.search_url,
            "source": candidate.source,
            "leadType": candidate.lead_type,
            "title": candidate.title,
            "company": candidate.company,
        },
        "message": lead.draft.body,
        "subject": draft_subject(lead),
        "dryRun": dry_run,
        "allowSend": allow_send,
    }


def send_message(store: Store, options: SendMessageOptions) -> str:
    run_id = options.run_id or _default_run_id("message")
    state = store.load()
    lead = find_lead_by_id(state.leads, options.lead_id)
    if lead is None:
        raise ValueError(f"unknown lead id {options.lead_id!r}")
    dry_run = options.dry_run or not options.allow_send
    if not options.session:
        raise ValueError("--session is required")
    if lead.draft is None or not clean_text(lead.draft.body):
        raise ValueError(f"lead {lead.id} has no draft; run draft first")
    if not lead.profile_url:
        raise ValueError(f"lead {lead.id} has no profile URL")
    if not dry_run and lead.message_status != MessageStatus.DRY_RUN_READY:
        raise ValueError(
            f"lead {lead.id} is {lead.message_status.value}; real sends require dry_run_ready"
        )
    bucket = bucket_for_lead(lead)
    if not dry_run and not lead_matches_sendable_bucket(state, lead, bucket):
        raise ValueError(
            f"lead {lead.id} is not sendable for {bucket}; "
            "agency sends require qualified account context"
        )
    if not dry_run and not options.allow_send:
        raise ValueError("real send requires --allow-send")

    config = prepare_message_config(state, lead, dry_run, options.allow_send)
    if options.result_path:
        result = load_message_send_result(options.result_path)
        out_path = options.result_path
    else:
        browser = options.browser or _default_message_browser(options, store)
        try:
            try:
                result, out_path = browser.send_message(
                    config,
                    dry_run=dry_run,
                    allow_send=options.allow_send,
                )
            except Exception as exc:
                if dry_run:
                    raise
                out_path = _write_browser_exception_result(
                    browser,
                    store,
                    lead,
                    dry_run=dry_run,
                    exc=exc,
                )
                result = load_message_send_result(out_path)
        finally:
            close = getattr(browser, "close", None)
            if callable(close):
                close()
    if not result.dry_run and dry_run:
        raise ValueError("real send result requires --allow-send")
    if result.status == "sent-clicked" and result.dry_run:
        raise ValueError("sent-clicked result cannot be dry_run=true")
    apply_message_send_result(lead, result, out_path, run_id)
    append_run_event(
        state,
        RunEvent(
            at=now_iso(),
            run_id=run_id,
            phase="send-message",
            command="send-message",
            bucket=bucket,
            lead_id=lead.id,
            name=lead.name,
            result=result.status,
            note=result_note(result) or "",
            out_path=out_path,
            state_path=str(store.state_path),
        ),
    )
    store.save(state)
    return (
        f"lead={lead.id} status={result.status} "
        f"dry_run={str(result.dry_run).lower()} out={out_path}"
    )


def _default_message_browser(options: SendMessageOptions, store: Store) -> Any:
    from .message_browser import PlaywriterMessageBrowserClient

    out_dir = Path(options.out_dir) if options.out_dir else store.dir / "message-results"
    session = None if options.session == "auto" else options.session
    return PlaywriterMessageBrowserClient(out_dir=out_dir, session=session)


def _write_browser_exception_result(
    browser: Any,
    store: Store,
    lead: Lead,
    *,
    dry_run: bool,
    exc: Exception,
) -> str:
    out_dir = Path(getattr(browser, "out_dir", store.dir / "message-results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now_iso().replace(':', '').replace('-', '')}-{_safe_stem(lead.id)}.json"
    payload = {
        "status": "send-failed",
        "dryRun": dry_run,
        "url": lead.profile_url,
        "reason": f"{type(exc).__name__}: {exc}",
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return str(path)


def _safe_stem(value: str) -> str:
    cleaned = []
    for char in value:
        cleaned.append(char if char.isalnum() or char in {"-", "_"} else "_")
    return "".join(cleaned).strip("._") or "lead"


@dataclass(slots=True)
class MessageSendResult:
    status: str
    dry_run: bool = True
    url: str | None = None
    reason: str | None = None
    body: str | None = None
    composer_selector: str | None = None
    action: dict[str, Any] | None = None
    search_row_action: dict[str, Any] | None = None
    conversation_check: dict[str, Any] | None = None
    subject_fill: dict[str, Any] | None = None
    body_fill: dict[str, Any] | None = None
    send: dict[str, Any] | None = None
    send_buttons: list[dict[str, Any]] | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> MessageSendResult:
        return cls(
            status=str(data.get("status") or ""),
            dry_run=bool(data.get("dryRun", data.get("dry_run", True))),
            url=_optional_str(data.get("url")),
            reason=_optional_str(data.get("reason")),
            body=_optional_str(data.get("body")),
            composer_selector=_optional_str(data.get("composerSelector")),
            action=_optional_dict(data.get("action")),
            search_row_action=_optional_dict(data.get("searchRowAction")),
            conversation_check=_optional_dict(data.get("conversationCheck")),
            subject_fill=_optional_dict(data.get("subjectFill")),
            body_fill=_optional_dict(data.get("bodyFill")),
            send=_optional_dict(data.get("send")),
            send_buttons=_optional_list_dict(data.get("sendButtons")),
        )


def load_message_send_result(path: str | Path) -> MessageSendResult:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return MessageSendResult.from_mapping(raw)


def apply_message_send_result(
    lead: Lead,
    result: MessageSendResult,
    out_path: str,
    run_id: str,
) -> None:
    at = now_iso()
    lead.send_attempts.append(
        SendAttempt(
            at=at,
            run_id=run_id,
            dry_run=result.dry_run,
            status=result.status,
            result_url=result.url,
            note=result_note(result),
            out_path=out_path,
            diagnostics=send_diagnostics(result),
        )
    )
    lead.message_status = message_status_for_result(result)
    lead.message_status_at = at
    lead.updated_at = at


def message_status_for_result(result: MessageSendResult) -> MessageStatus:
    if result.status == "dry-run-messageable":
        return MessageStatus.DRY_RUN_READY
    if result.status == "sent-clicked":
        return MessageStatus.SENT
    if result.status == "not-messageable":
        return MessageStatus.NOT_MESSAGEABLE
    if result.status == "conversation-exists":
        return MessageStatus.CONVERSATION_EXISTS
    if result.status == "blocked":
        return MessageStatus.BLOCKED
    return MessageStatus.SEND_FAILED


def result_note(result: MessageSendResult) -> str | None:
    if result.reason:
        return result.reason
    if result.action:
        return truncate_evidence(json.dumps(result.action, sort_keys=True))
    return None


def send_diagnostics(result: MessageSendResult) -> dict[str, str]:
    diagnostics: dict[str, str] = {}
    if result.composer_selector:
        diagnostics["composer"] = result.composer_selector
    for key, value in {
        "subject": result.subject_fill,
        "body": result.body_fill,
        "send": result.send,
        "conversation": result.conversation_check,
        "action": result.action,
    }.items():
        if value:
            diagnostics[key] = truncate_evidence(json.dumps(value, sort_keys=True))
    if result.send_buttons:
        diagnostics["send_buttons"] = truncate_evidence(
            json.dumps(result.send_buttons, sort_keys=True)
        )
    return diagnostics


def _optional_str(value: object) -> str | None:
    cleaned = clean_text(value)
    return cleaned or None


def _optional_dict(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _optional_list_dict(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _default_run_id(prefix: str) -> str:
    stamp = now_iso().replace(":", "").replace("-", "").replace(".", "")
    return f"{prefix}-{stamp}"
