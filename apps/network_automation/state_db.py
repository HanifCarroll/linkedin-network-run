"""SQLite-backed canonical state for network automation ledgers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from packages.linkedin_storage import (
    Migration,
    apply_migrations,
    connect_sqlite,
    transaction,
)

from .models import (
    AcceptanceFollowupAttempt,
    AcceptanceFollowupLedger,
    AcceptanceFollowupRecord,
    AcceptanceInvitation,
    AcceptanceLedger,
    SendLedgerEntry,
    stable_hash,
)

NETWORK_DB_NAME = "network.sqlite"

META_ACCEPTANCE_LEDGER_CANONICAL = "canonical.acceptance_ledger"
META_ACCEPTANCE_FOLLOWUPS_CANONICAL = "canonical.acceptance_followups"
META_SEND_LEDGER_CANONICAL = "canonical.send_ledger"


MIGRATIONS = (
    Migration(
        1,
        "network_ledger_foundation",
        """
        CREATE TABLE IF NOT EXISTS state_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            stream TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_event_log_stream_at
            ON event_log(stream, at);

        CREATE TABLE IF NOT EXISTS acceptance_invitations (
            key TEXT PRIMARY KEY,
            position INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            run_date TEXT NOT NULL,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            profile_url TEXT,
            public_profile_url TEXT,
            sent_at TEXT NOT NULL,
            latest_status TEXT NOT NULL,
            latest_checked_at TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_acceptance_invitations_status
            ON acceptance_invitations(latest_status, latest_checked_at);
        CREATE INDEX IF NOT EXISTS idx_acceptance_invitations_source
            ON acceptance_invitations(source, sent_at);

        CREATE TABLE IF NOT EXISTS acceptance_outcome_events (
            id TEXT PRIMARY KEY,
            invitation_key TEXT NOT NULL,
            position INTEGER NOT NULL,
            at TEXT NOT NULL,
            status TEXT NOT NULL,
            relationship TEXT,
            evidence TEXT,
            note TEXT,
            raw_json TEXT NOT NULL,
            FOREIGN KEY(invitation_key)
                REFERENCES acceptance_invitations(key)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_acceptance_outcome_events_invitation
            ON acceptance_outcome_events(invitation_key, position);

        CREATE TABLE IF NOT EXISTS acceptance_followups (
            id TEXT PRIMARY KEY,
            key TEXT NOT NULL UNIQUE,
            position INTEGER NOT NULL,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            profile_url TEXT,
            sales_nav_profile_url TEXT,
            drafted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            accepted_at TEXT NOT NULL,
            strategy TEXT NOT NULL,
            template_key TEXT NOT NULL,
            angle TEXT NOT NULL,
            draft TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT,
            report_path TEXT NOT NULL,
            research_path TEXT,
            person_does TEXT,
            company_does TEXT,
            message_fit TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_acceptance_followups_status
            ON acceptance_followups(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_acceptance_followups_source
            ON acceptance_followups(source, accepted_at);

        CREATE TABLE IF NOT EXISTS acceptance_followup_attempts (
            id TEXT PRIMARY KEY,
            followup_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            at TEXT NOT NULL,
            dry_run INTEGER NOT NULL,
            status TEXT NOT NULL,
            result_url TEXT,
            note TEXT,
            out_path TEXT NOT NULL,
            diagnostics_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            FOREIGN KEY(followup_id)
                REFERENCES acceptance_followups(id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_acceptance_followup_attempts_followup
            ON acceptance_followup_attempts(followup_id, position);

        CREATE TABLE IF NOT EXISTS send_ledger_entries (
            entry_id TEXT PRIMARY KEY,
            attempt_key TEXT NOT NULL,
            run_id TEXT NOT NULL,
            run_date TEXT,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            profile_url TEXT,
            public_profile_url TEXT,
            attempted_at TEXT NOT NULL,
            confirmed_at TEXT,
            status TEXT NOT NULL,
            durable INTEGER NOT NULL,
            reason TEXT,
            event_kind TEXT NOT NULL,
            result_path TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_send_ledger_entries_attempt
            ON send_ledger_entries(attempt_key);
        CREATE INDEX IF NOT EXISTS idx_send_ledger_entries_attempted_at
            ON send_ledger_entries(attempted_at);
        CREATE INDEX IF NOT EXISTS idx_send_ledger_entries_run_date
            ON send_ledger_entries(run_date, source);
        """,
    ),
)


@dataclass(frozen=True)
class NetworkStateDbStatus:
    database_path: Path
    exists: bool
    applied_migrations: tuple[int, ...]
    acceptance_invitations: int
    acceptance_outcome_events: int
    acceptance_followups: int
    acceptance_followup_attempts: int
    send_ledger_entries: int
    canonical_acceptance_ledger: bool
    canonical_acceptance_followups: bool
    canonical_send_ledger: bool

    def to_json_dict(self) -> dict[str, object]:
        return {
            "database_path": str(self.database_path),
            "exists": self.exists,
            "applied_migrations": list(self.applied_migrations),
            "acceptance_invitations": self.acceptance_invitations,
            "acceptance_outcome_events": self.acceptance_outcome_events,
            "acceptance_followups": self.acceptance_followups,
            "acceptance_followup_attempts": self.acceptance_followup_attempts,
            "send_ledger_entries": self.send_ledger_entries,
            "canonical_acceptance_ledger": self.canonical_acceptance_ledger,
            "canonical_acceptance_followups": self.canonical_acceptance_followups,
            "canonical_send_ledger": self.canonical_send_ledger,
        }


@dataclass(frozen=True)
class NetworkStateMigrationSummary:
    database_path: Path
    dry_run: bool
    acceptance_invitations: int
    acceptance_outcome_events: int
    acceptance_followups: int
    acceptance_followup_attempts: int
    send_ledger_entries: int
    warnings: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, object]:
        return {
            "database_path": str(self.database_path),
            "dry_run": self.dry_run,
            "acceptance_invitations": self.acceptance_invitations,
            "acceptance_outcome_events": self.acceptance_outcome_events,
            "acceptance_followups": self.acceptance_followups,
            "acceptance_followup_attempts": self.acceptance_followup_attempts,
            "send_ledger_entries": self.send_ledger_entries,
            "warnings": list(self.warnings),
        }


class NetworkStateDb:
    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / NETWORK_DB_NAME

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        return connect_sqlite(self.path, readonly=readonly)

    def ensure_schema(self) -> list[Migration]:
        with self.connect() as conn:
            return apply_migrations(conn, MIGRATIONS)

    def status(self) -> NetworkStateDbStatus:
        if not self.path.exists():
            return NetworkStateDbStatus(
                database_path=self.path,
                exists=False,
                applied_migrations=(),
                acceptance_invitations=0,
                acceptance_outcome_events=0,
                acceptance_followups=0,
                acceptance_followup_attempts=0,
                send_ledger_entries=0,
                canonical_acceptance_ledger=False,
                canonical_acceptance_followups=False,
                canonical_send_ledger=False,
            )
        with self.connect(readonly=True) as conn:
            return NetworkStateDbStatus(
                database_path=self.path,
                exists=True,
                applied_migrations=_applied_migrations(conn),
                acceptance_invitations=_table_count(conn, "acceptance_invitations"),
                acceptance_outcome_events=_table_count(conn, "acceptance_outcome_events"),
                acceptance_followups=_table_count(conn, "acceptance_followups"),
                acceptance_followup_attempts=_table_count(
                    conn, "acceptance_followup_attempts"
                ),
                send_ledger_entries=_table_count(conn, "send_ledger_entries"),
                canonical_acceptance_ledger=_meta_bool(
                    conn, META_ACCEPTANCE_LEDGER_CANONICAL
                ),
                canonical_acceptance_followups=_meta_bool(
                    conn, META_ACCEPTANCE_FOLLOWUPS_CANONICAL
                ),
                canonical_send_ledger=_meta_bool(conn, META_SEND_LEDGER_CANONICAL),
            )

    def has_acceptance_ledger(self) -> bool:
        status = self.status()
        return status.canonical_acceptance_ledger or status.acceptance_invitations > 0

    def has_acceptance_followups(self) -> bool:
        status = self.status()
        return status.canonical_acceptance_followups or status.acceptance_followups > 0

    def has_send_ledger(self) -> bool:
        status = self.status()
        return status.canonical_send_ledger or status.send_ledger_entries > 0

    def load_acceptance_ledger(self) -> AcceptanceLedger:
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                """
                SELECT raw_json
                FROM acceptance_invitations
                ORDER BY position, sent_at, source, name
                """
            ).fetchall()
        return AcceptanceLedger(
            invitations=[
                AcceptanceInvitation.model_validate_json(str(row["raw_json"]))
                for row in rows
            ]
        )

    def replace_acceptance_ledger(self, ledger: AcceptanceLedger) -> None:
        self.ensure_schema()
        with self.connect() as conn, transaction(conn):
            conn.execute("DELETE FROM acceptance_outcome_events")
            conn.execute("DELETE FROM acceptance_invitations")
            for position, invitation in enumerate(ledger.invitations):
                _insert_acceptance_invitation(conn, invitation, position, replace=True)
            _set_meta(conn, META_ACCEPTANCE_LEDGER_CANONICAL, "1")

    def load_acceptance_followup_ledger(self) -> AcceptanceFollowupLedger:
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                """
                SELECT raw_json
                FROM acceptance_followups
                ORDER BY position, accepted_at, source, name
                """
            ).fetchall()
        return AcceptanceFollowupLedger(
            drafts=[
                AcceptanceFollowupRecord.model_validate_json(str(row["raw_json"]))
                for row in rows
            ]
        )

    def replace_acceptance_followup_ledger(self, ledger: AcceptanceFollowupLedger) -> None:
        self.ensure_schema()
        with self.connect() as conn, transaction(conn):
            conn.execute("DELETE FROM acceptance_followup_attempts")
            conn.execute("DELETE FROM acceptance_followups")
            for position, record in enumerate(ledger.drafts):
                _insert_acceptance_followup(conn, record, position, replace=True)
            _set_meta(conn, META_ACCEPTANCE_FOLLOWUPS_CANONICAL, "1")

    def load_send_ledger_entries(self) -> list[SendLedgerEntry]:
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                """
                SELECT raw_json
                FROM send_ledger_entries
                ORDER BY attempted_at, confirmed_at, entry_id
                """
            ).fetchall()
        return [SendLedgerEntry.model_validate_json(str(row["raw_json"])) for row in rows]

    def append_send_ledger_entry(self, entry: SendLedgerEntry) -> bool:
        self.ensure_schema()
        with self.connect() as conn, transaction(conn):
            inserted = _insert_send_ledger_entry(conn, entry, replace=False)
            _set_meta(conn, META_SEND_LEDGER_CANONICAL, "1")
            return inserted

    def import_acceptance_ledger(self, ledger: AcceptanceLedger) -> None:
        self.ensure_schema()
        with self.connect() as conn, transaction(conn):
            for position, invitation in enumerate(ledger.invitations):
                _insert_acceptance_invitation(conn, invitation, position, replace=True)
            _set_meta(conn, META_ACCEPTANCE_LEDGER_CANONICAL, "1")

    def import_acceptance_followup_ledger(self, ledger: AcceptanceFollowupLedger) -> None:
        self.ensure_schema()
        with self.connect() as conn, transaction(conn):
            for position, record in enumerate(ledger.drafts):
                _insert_acceptance_followup(conn, record, position, replace=True)
            _set_meta(conn, META_ACCEPTANCE_FOLLOWUPS_CANONICAL, "1")

    def import_send_ledger_entries(self, entries: list[SendLedgerEntry]) -> int:
        self.ensure_schema()
        inserted = 0
        with self.connect() as conn, transaction(conn):
            for entry in entries:
                if _insert_send_ledger_entry(conn, entry, replace=False):
                    inserted += 1
            _set_meta(conn, META_SEND_LEDGER_CANONICAL, "1")
        return inserted

    def append_event(self, stream: str, kind: str, payload: object) -> None:
        self.ensure_schema()
        with self.connect() as conn, transaction(conn):
            conn.execute(
                "INSERT INTO event_log(stream, kind, payload_json) VALUES (?, ?, ?)",
                (stream, kind, _json_dumps(payload)),
            )


def _insert_acceptance_invitation(
    conn: sqlite3.Connection,
    invitation: AcceptanceInvitation,
    position: int,
    *,
    replace: bool,
) -> bool:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    cursor = conn.execute(
        f"""
        {verb} INTO acceptance_invitations(
            key,
            position,
            run_id,
            run_date,
            source,
            name,
            profile_url,
            public_profile_url,
            sent_at,
            latest_status,
            latest_checked_at,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invitation.key(),
            position,
            str(invitation.run_id),
            invitation.run_date.isoformat(),
            invitation.source,
            invitation.name,
            invitation.profile_url,
            invitation.public_profile_url,
            invitation.sent_at.isoformat(),
            invitation.latest_status.value,
            invitation.latest_checked_at.isoformat()
            if invitation.latest_checked_at is not None
            else None,
            _model_json(invitation),
        ),
    )
    for event_position, event in enumerate(invitation.history):
        _insert_acceptance_outcome_event(conn, invitation.key(), event, event_position, replace)
    return cursor.rowcount > 0


def _insert_acceptance_outcome_event(
    conn: sqlite3.Connection,
    invitation_key: str,
    event: Any,
    position: int,
    replace: bool,
) -> None:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    conn.execute(
        f"""
        {verb} INTO acceptance_outcome_events(
            id,
            invitation_key,
            position,
            at,
            status,
            relationship,
            evidence,
            note,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stable_hash(
                "acceptance-outcome-event",
                invitation_key,
                str(position),
                event.at.isoformat(),
                event.status.value,
                event.note or "",
                event.evidence or "",
            ),
            invitation_key,
            position,
            event.at.isoformat(),
            event.status.value,
            event.relationship,
            event.evidence,
            event.note,
            _model_json(event),
        ),
    )


def _insert_acceptance_followup(
    conn: sqlite3.Connection,
    record: AcceptanceFollowupRecord,
    position: int,
    *,
    replace: bool,
) -> bool:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    cursor = conn.execute(
        f"""
        {verb} INTO acceptance_followups(
            id,
            key,
            position,
            source,
            name,
            profile_url,
            sales_nav_profile_url,
            drafted_at,
            updated_at,
            accepted_at,
            strategy,
            template_key,
            angle,
            draft,
            status,
            sent_at,
            report_path,
            research_path,
            person_does,
            company_does,
            message_fit,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.id,
            record.key,
            position,
            record.source,
            record.name,
            record.profile_url,
            record.sales_nav_profile_url,
            record.drafted_at.isoformat(),
            record.updated_at.isoformat(),
            record.accepted_at.isoformat(),
            record.strategy.value,
            record.template_key.value,
            record.angle,
            record.draft,
            record.status.value,
            record.sent_at.isoformat() if record.sent_at is not None else None,
            record.report_path,
            record.research_path,
            record.person_does,
            record.company_does,
            record.message_fit,
            _model_json(record),
        ),
    )
    if replace:
        conn.execute(
            "DELETE FROM acceptance_followup_attempts WHERE followup_id = ?",
            (record.id,),
        )
    for attempt_position, attempt in enumerate(record.attempts):
        _insert_acceptance_followup_attempt(
            conn,
            record.id,
            attempt,
            attempt_position,
            replace,
        )
    return cursor.rowcount > 0


def _insert_acceptance_followup_attempt(
    conn: sqlite3.Connection,
    followup_id: str,
    attempt: AcceptanceFollowupAttempt,
    position: int,
    replace: bool,
) -> None:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    conn.execute(
        f"""
        {verb} INTO acceptance_followup_attempts(
            id,
            followup_id,
            position,
            at,
            dry_run,
            status,
            result_url,
            note,
            out_path,
            diagnostics_json,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stable_hash(
                "acceptance-followup-attempt",
                followup_id,
                str(position),
                attempt.at.isoformat(),
                attempt.status,
                attempt.out_path,
            ),
            followup_id,
            position,
            attempt.at.isoformat(),
            1 if attempt.dry_run else 0,
            attempt.status,
            attempt.result_url,
            attempt.note,
            attempt.out_path,
            _json_dumps(attempt.diagnostics),
            _model_json(attempt),
        ),
    )


def _insert_send_ledger_entry(
    conn: sqlite3.Connection,
    entry: SendLedgerEntry,
    *,
    replace: bool,
) -> bool:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    cursor = conn.execute(
        f"""
        {verb} INTO send_ledger_entries(
            entry_id,
            attempt_key,
            run_id,
            run_date,
            source,
            name,
            profile_url,
            public_profile_url,
            attempted_at,
            confirmed_at,
            status,
            durable,
            reason,
            event_kind,
            result_path,
            raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.entry_id,
            entry.attempt_key,
            entry.run_id,
            entry.run_date.isoformat() if entry.run_date is not None else None,
            entry.source,
            entry.name,
            entry.profile_url,
            entry.public_profile_url,
            entry.attempted_at.isoformat(),
            entry.confirmed_at.isoformat() if entry.confirmed_at is not None else None,
            entry.status.value,
            1 if entry.durable else 0,
            entry.reason,
            entry.event_kind,
            entry.result_path,
            _model_json(entry),
        ),
    )
    return cursor.rowcount > 0


def _applied_migrations(conn: sqlite3.Connection) -> tuple[int, ...]:
    if not _table_exists(conn, "schema_migrations"):
        return ()
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return tuple(int(row["version"]) for row in rows)


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"] if row is not None else 0)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _meta_bool(conn: sqlite3.Connection, key: str) -> bool:
    if not _table_exists(conn, "state_meta"):
        return False
    row = conn.execute("SELECT value FROM state_meta WHERE key = ?", (key,)).fetchone()
    return row is not None and str(row["value"]) == "1"


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO state_meta(key, value) VALUES (?, ?)",
        (key, value),
    )


def _model_json(model: BaseModel) -> str:
    return _json_dumps(model.model_dump(mode="json", by_alias=False))


def _json_dumps(value: object) -> str:
    return json.dumps(_to_jsonable(value), separators=(",", ":"), sort_keys=True)


def _to_jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=False)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_jsonable(item) for item in value]
    return value
