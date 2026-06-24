"""SQLite-backed state store for recruiter/agency outreach."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from packages.linkedin_common.paths import DEFAULT_STATE_ROOT

from .models import (
    AgencyAccount,
    AgencyContactCandidate,
    CaptureCursor,
    Lead,
    OutreachState,
    RunEvent,
)
from .utils import now_iso

APP_DIR = "recruiter_agency_outreach"
SQLITE_BUSY_TIMEOUT_MS = 5000


class Store:
    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.dir = Path(state_dir) if state_dir else DEFAULT_STATE_ROOT / APP_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path:
        return self.dir / "outreach.sqlite"

    @property
    def state_path(self) -> Path:
        return self.database_path

    def default_draft_report_path(self) -> Path:
        return self.dir / "drafts" / f"{now_iso()[:10]}.md"

    def latest_render_dashboard_path(self) -> Path:
        return self.dir / "dashboards" / "latest-render.md"

    def latest_run_dashboard_path(self) -> Path:
        return self.dir / "dashboards" / "latest-run.md"

    def run_dashboard_path(self, run_id: str) -> Path:
        return self.dir / "dashboards" / f"{run_id}.md"

    def agency_source_dir(self) -> Path:
        return self.dir / "agency-sources"

    def agency_source_artifact_path(self, source: str) -> Path:
        safe = "-".join(source.lower().split()).replace("/", "-")
        return self.agency_source_dir() / f"{now_iso()[:10]}-{safe}.json"

    def load(self) -> OutreachState:
        if not self.database_path.exists():
            return OutreachState(updated_at=now_iso())
        with self._connect() as db:
            self._ensure_schema(db)
            state = OutreachState(
                leads=[Lead.from_mapping(row) for row in self._load_table(db, "leads")],
                agency_accounts=[
                    AgencyAccount.from_mapping(row)
                    for row in self._load_table(db, "agency_accounts")
                ],
                agency_contact_candidates=[
                    AgencyContactCandidate.from_mapping(row)
                    for row in self._load_table(db, "agency_contact_candidates")
                ],
                capture_cursors={
                    row["source"]: CaptureCursor.from_mapping(row)
                    for row in self._load_table(db, "capture_cursors")
                    if isinstance(row.get("source"), str)
                },
                run_events=[
                    RunEvent.from_mapping(row)
                    for row in self._load_table(db, "run_events", order_by="position")
                ],
            )
            return state

    def save(self, state: OutreachState) -> None:
        state.updated_at = now_iso()
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            self._ensure_schema(db)
            db.execute("BEGIN")
            try:
                self._replace_table(
                    db, "leads", ((lead.id, lead.to_dict()) for lead in state.leads)
                )
                self._replace_table(
                    db,
                    "agency_accounts",
                    ((account.id, account.to_dict()) for account in state.agency_accounts),
                )
                self._replace_table(
                    db,
                    "agency_contact_candidates",
                    (
                        (candidate.id, candidate.to_dict())
                        for candidate in state.agency_contact_candidates
                    ),
                )
                self._replace_table(
                    db,
                    "capture_cursors",
                    (
                        (cursor.source, cursor.to_dict())
                        for cursor in state.capture_cursors.values()
                    ),
                )
                db.execute("DELETE FROM run_events")
                for index, event in enumerate(state.run_events[-500:]):
                    db.execute(
                        "INSERT INTO run_events(position, data) VALUES (?, ?)",
                        (index, json.dumps(event.to_dict(), sort_keys=True)),
                    )
                db.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    ("updated_at", state.updated_at),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path)
        db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _ensure_schema(self, db: sqlite3.Connection) -> None:
        statements = [
            "PRAGMA journal_mode = WAL",
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS leads (id TEXT PRIMARY KEY, data TEXT NOT NULL)",
            (
                "CREATE TABLE IF NOT EXISTS agency_accounts "
                "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            ),
            (
                "CREATE TABLE IF NOT EXISTS agency_contact_candidates "
                "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            ),
            (
                "CREATE TABLE IF NOT EXISTS capture_cursors "
                "(source TEXT PRIMARY KEY, data TEXT NOT NULL)"
            ),
            (
                "CREATE TABLE IF NOT EXISTS run_events "
                "(position INTEGER PRIMARY KEY, data TEXT NOT NULL)"
            ),
        ]
        for statement in statements:
            db.execute(statement)

    def _load_table(
        self,
        db: sqlite3.Connection,
        table: str,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        order = f" ORDER BY {order_by}" if order_by else ""
        rows = db.execute(f"SELECT data FROM {table}{order}").fetchall()
        values: list[dict[str, Any]] = []
        for (raw,) in rows:
            parsed = json.loads(str(raw))
            if isinstance(parsed, dict):
                values.append(parsed)
        return values

    def _replace_table(
        self,
        db: sqlite3.Connection,
        table: str,
        rows: Iterable[tuple[str, dict[str, Any]]],
    ) -> None:
        id_column = "source" if table == "capture_cursors" else "id"
        db.execute(f"DELETE FROM {table}")
        for row_id, payload in rows:
            db.execute(
                f"INSERT INTO {table}({id_column}, data) VALUES (?, ?)",
                (row_id, json.dumps(payload, sort_keys=True)),
            )


def append_run_event(state: OutreachState, event: RunEvent) -> None:
    state.run_events.append(event)
    if len(state.run_events) > 500:
        state.run_events = state.run_events[-500:]
