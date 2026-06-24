"""SQLite migration, read-only import, and legacy state migration primitives."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from packages.linkedin_common.paths import DEFAULT_STATE_ROOT

from .sqlite import SQLiteValue, connect_sqlite

SCHEMA_MIGRATIONS_TABLE = "schema_migrations"

SQLiteParameters = Sequence[SQLiteValue] | Mapping[str, SQLiteValue]
RowMapper = Callable[[Mapping[str, SQLiteValue]], SQLiteParameters | None]
SourceApp = Literal["network", "recruiter_agency", "opportunity"]

LEGACY_IMPORTS_DB_NAME = "legacy-imports.sqlite"

NETWORK_LEGACY_APP_DIR = "linkedin-network-run"
RECRUITER_AGENCY_LEGACY_APP_DIR = "recruiter-agency-outreach"
OPPORTUNITY_LEGACY_DIR = Path("/tmp/linkedin-opportunity-signals")
NETWORK_STATE_DIR = "network-automation"
RECRUITER_AGENCY_STATE_DIR = "recruiter-agency-outreach"

OUTREACH_SQLITE_TABLES = (
    "meta",
    "leads",
    "drafts",
    "send_attempts",
    "agency_accounts",
    "agency_contact_candidates",
    "capture_cursors",
    "run_events",
)


@dataclass(frozen=True)
class Migration:
    """One ordered SQLite schema migration."""

    version: int
    name: str
    sql: str


@dataclass(frozen=True)
class ImportResult:
    """Summary from copying rows out of a read-only source database."""

    source_path: Path
    rows_read: int
    rows_inserted: int


@dataclass(frozen=True)
class LegacyArtifact:
    source_app: SourceApp
    relative_path: str
    kind: str
    size_bytes: int
    mtime_ns: int | None
    sha256: str
    content: bytes


@dataclass(frozen=True)
class MigrationResult:
    import_id: str
    source_app: SourceApp
    source_path: Path
    target_root: Path
    database_path: Path
    imported_at: str
    artifact_count: int
    warnings: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "import_id": self.import_id,
            "source_app": self.source_app,
            "source_path": str(self.source_path),
            "target_root": str(self.target_root),
            "database_path": str(self.database_path),
            "imported_at": self.imported_at,
            "artifact_count": self.artifact_count,
            "warnings": list(self.warnings),
        }


def apply_migrations(conn: sqlite3.Connection, migrations: Sequence[Migration]) -> list[Migration]:
    """Apply unapplied migrations in version order and return the applied subset."""

    _ensure_migrations_table(conn)
    _validate_migration_versions(migrations)
    applied_versions = _applied_versions(conn)
    applied_now: list[Migration] = []
    for migration in sorted(migrations, key=lambda item: item.version):
        if migration.version in applied_versions:
            continue
        with conn:
            conn.executescript(migration.sql)
            conn.execute(
                f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
        applied_now.append(migration)
    return applied_now


def copy_rows_from_readonly_source(
    *,
    source_path: Path,
    target_conn: sqlite3.Connection,
    select_sql: str,
    insert_sql: str,
    row_mapper: RowMapper,
    select_parameters: Sequence[SQLiteValue] = (),
) -> ImportResult:
    """Copy selected source rows into a target DB without opening the source mutably."""

    rows_read = 0
    rows_inserted = 0
    with connect_sqlite(source_path, readonly=True) as source_conn:
        source_cursor = source_conn.execute(select_sql, tuple(select_parameters))
        with target_conn:
            for source_row in source_cursor:
                rows_read += 1
                params = row_mapper(_sqlite_row_to_mapping(source_row))
                if params is None:
                    continue
                target_conn.execute(insert_sql, params)
                rows_inserted += 1
    return ImportResult(source_path=source_path, rows_read=rows_read, rows_inserted=rows_inserted)


def default_legacy_network_state_dir(home: Path | None = None) -> Path:
    return _application_support_root(home) / NETWORK_LEGACY_APP_DIR


def default_legacy_recruiter_agency_state_dir(home: Path | None = None) -> Path:
    return _application_support_root(home) / RECRUITER_AGENCY_LEGACY_APP_DIR


def default_legacy_opportunity_state_dir() -> Path:
    return OPPORTUNITY_LEGACY_DIR


def import_legacy_network_state(
    *,
    old_state_dir: Path | None = None,
    target_root: Path = DEFAULT_STATE_ROOT,
) -> MigrationResult:
    source_dir = old_state_dir or default_legacy_network_state_dir()
    promotion_warnings = _promote_network_state(source_dir=source_dir, target_root=target_root)
    return _import_legacy_directory(
        source_app="network",
        source_dir=source_dir,
        target_root=target_root,
        extra_artifacts=(),
        initial_warnings=promotion_warnings,
    )


def import_legacy_recruiter_agency_state(
    *,
    old_state_dir: Path | None = None,
    target_root: Path = DEFAULT_STATE_ROOT,
) -> MigrationResult:
    source_dir = old_state_dir or default_legacy_recruiter_agency_state_dir()
    extra_artifacts, warnings = _outreach_sqlite_snapshots(source_dir)
    promotion_warnings = _promote_recruiter_agency_state(
        source_dir=source_dir,
        target_root=target_root,
    )
    return _import_legacy_directory(
        source_app="recruiter_agency",
        source_dir=source_dir,
        target_root=target_root,
        extra_artifacts=extra_artifacts,
        initial_warnings=warnings + promotion_warnings,
    )


def import_legacy_opportunity_runs(
    *,
    old_state_dir: Path | None = None,
    target_root: Path = DEFAULT_STATE_ROOT,
) -> MigrationResult:
    source_dir = old_state_dir or default_legacy_opportunity_state_dir()
    return _import_legacy_directory(
        source_app="opportunity",
        source_dir=source_dir,
        target_root=target_root,
        extra_artifacts=(),
    )


def import_all_legacy_state(
    *,
    target_root: Path = DEFAULT_STATE_ROOT,
    network_state_dir: Path | None = None,
    recruiter_agency_state_dir: Path | None = None,
    opportunity_state_dir: Path | None = None,
) -> tuple[MigrationResult, MigrationResult, MigrationResult]:
    return (
        import_legacy_network_state(
            old_state_dir=network_state_dir,
            target_root=target_root,
        ),
        import_legacy_recruiter_agency_state(
            old_state_dir=recruiter_agency_state_dir,
            target_root=target_root,
        ),
        import_legacy_opportunity_runs(
            old_state_dir=opportunity_state_dir,
            target_root=target_root,
        ),
    )


def latest_import_summary(
    *,
    source_app: SourceApp,
    target_root: Path = DEFAULT_STATE_ROOT,
) -> dict[str, object] | None:
    database_path = target_root / LEGACY_IMPORTS_DB_NAME
    if not database_path.exists():
        return None
    with sqlite3.connect(database_path) as connection:
        _ensure_legacy_import_schema(connection)
        row = connection.execute(
            """
            SELECT id, source_path, target_root, imported_at, artifact_count, warning_count
            FROM import_runs
            WHERE source_app = ?
            ORDER BY imported_at DESC, id DESC
            LIMIT 1
            """,
            (source_app,),
        ).fetchone()
        if row is None:
            return None
        import_id, source_path, stored_target_root, imported_at, artifact_count, warning_count = row
        return {
            "import_id": str(import_id),
            "source_app": source_app,
            "source_path": str(source_path),
            "target_root": str(stored_target_root),
            "database_path": str(database_path),
            "imported_at": str(imported_at),
            "artifact_count": int(artifact_count),
            "warning_count": int(warning_count),
        }


def _application_support_root(home: Path | None = None) -> Path:
    resolved_home = home or Path.home()
    return resolved_home / "Library" / "Application Support"


def _import_legacy_directory(
    *,
    source_app: SourceApp,
    source_dir: Path,
    target_root: Path,
    extra_artifacts: tuple[LegacyArtifact, ...],
    initial_warnings: tuple[str, ...] = (),
) -> MigrationResult:
    import_id = str(uuid.uuid4())
    imported_at = _utc_now()
    warnings = list(initial_warnings)
    artifacts: list[LegacyArtifact] = []

    if not source_dir.exists():
        warnings.append(f"source directory does not exist: {source_dir}")
    elif not source_dir.is_dir():
        warnings.append(f"source path is not a directory: {source_dir}")
    else:
        artifacts.extend(_file_artifacts(source_app=source_app, source_dir=source_dir))

    artifacts.extend(extra_artifacts)
    database_path = _write_import(
        import_id=import_id,
        source_app=source_app,
        source_dir=source_dir,
        target_root=target_root,
        imported_at=imported_at,
        artifacts=tuple(artifacts),
        warnings=tuple(warnings),
    )
    return MigrationResult(
        import_id=import_id,
        source_app=source_app,
        source_path=source_dir,
        target_root=target_root,
        database_path=database_path,
        imported_at=imported_at,
        artifact_count=len(artifacts),
        warnings=tuple(warnings),
    )


def _promote_network_state(*, source_dir: Path, target_root: Path) -> tuple[str, ...]:
    if not source_dir.exists() or not source_dir.is_dir():
        return ()
    target_dir = target_root / NETWORK_STATE_DIR
    if _directory_has_files(target_dir):
        return (f"target network state already exists; skipped promotion: {target_dir}",)

    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        destination = target_dir / path.relative_to(source_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return ()


def _promote_recruiter_agency_state(*, source_dir: Path, target_root: Path) -> tuple[str, ...]:
    if not source_dir.exists() or not source_dir.is_dir():
        return ()
    source_database = source_dir / "outreach.sqlite"
    source_json = source_dir / "outreach.json"
    target_dir = target_root / RECRUITER_AGENCY_STATE_DIR
    target_database = target_dir / "outreach.sqlite"
    if target_database.exists():
        return (
            "target recruiter/agency SQLite state already exists; "
            f"skipped promotion: {target_database}",
        )
    if source_database.exists():
        try:
            _copy_sqlite_database_readonly(source_database, target_database)
        except sqlite3.Error as error:
            return (f"could not promote outreach.sqlite: {error}",)
        return ()
    if source_json.exists():
        try:
            _promote_recruiter_agency_json(source_json, target_database)
        except (OSError, ValueError, TypeError, sqlite3.Error) as error:
            return (f"could not promote outreach.json: {error}",)
        return ()
    return ("no outreach.sqlite or outreach.json found for recruiter/agency promotion",)


def _directory_has_files(path: Path) -> bool:
    return path.exists() and any(item.is_file() for item in path.rglob("*"))


def _copy_sqlite_database_readonly(source_database: Path, target_database: Path) -> None:
    target_database.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source_database.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(target_database) as target_connection:
            source_connection.backup(target_connection)


def _promote_recruiter_agency_json(source_json: Path, target_database: Path) -> None:
    payload = json.loads(source_json.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("outreach.json root must be an object")
    target_database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target_database) as connection:
        _ensure_recruiter_agency_state_schema(connection)
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("schema_version", str(payload.get("schema_version") or 1)),
            )
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("updated_at", str(payload.get("updated_at") or "")),
            )
            _insert_json_rows(connection, "leads", "id", payload.get("leads"))
            _insert_json_rows(
                connection,
                "agency_accounts",
                "id",
                payload.get("agency_accounts"),
            )
            _insert_json_rows(
                connection,
                "agency_contact_candidates",
                "id",
                payload.get("agency_contact_candidates"),
            )
            raw_cursors = payload.get("capture_cursors")
            if isinstance(raw_cursors, Mapping):
                cursor_rows = [
                    dict(cursor, source=str(source))
                    for source, cursor in raw_cursors.items()
                    if isinstance(cursor, Mapping)
                ]
                _insert_json_rows(connection, "capture_cursors", "source", cursor_rows)
            _insert_run_events(connection, payload.get("run_events"))


def _ensure_recruiter_agency_state_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS leads (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agency_accounts (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agency_contact_candidates (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS capture_cursors (
            source TEXT PRIMARY KEY,
            data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS run_events (position INTEGER PRIMARY KEY, data TEXT NOT NULL);
        """
    )


def _insert_json_rows(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    raw_rows: object,
) -> None:
    if not isinstance(raw_rows, list):
        return
    quoted_table = _quote_identifier(table)
    quoted_id_column = _quote_identifier(id_column)
    for row in raw_rows:
        if not isinstance(row, Mapping):
            continue
        row_id = row.get(id_column)
        if row_id is None:
            continue
        connection.execute(
            f"INSERT OR REPLACE INTO {quoted_table}({quoted_id_column}, data) VALUES (?, ?)",
            (str(row_id), json.dumps(dict(row), sort_keys=True)),
        )


def _insert_run_events(connection: sqlite3.Connection, raw_events: object) -> None:
    if not isinstance(raw_events, list):
        return
    for index, event in enumerate(raw_events[-500:]):
        if not isinstance(event, Mapping):
            continue
        connection.execute(
            "INSERT OR REPLACE INTO run_events(position, data) VALUES (?, ?)",
            (index, json.dumps(dict(event), sort_keys=True)),
        )


def _file_artifacts(*, source_app: SourceApp, source_dir: Path) -> tuple[LegacyArtifact, ...]:
    artifacts: list[LegacyArtifact] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        content = path.read_bytes()
        artifacts.append(
            LegacyArtifact(
                source_app=source_app,
                relative_path=path.relative_to(source_dir).as_posix(),
                kind=_artifact_kind(path),
                size_bytes=len(content),
                mtime_ns=stat.st_mtime_ns,
                sha256=_sha256(content),
                content=content,
            )
        )
    return tuple(artifacts)


def _outreach_sqlite_snapshots(
    source_dir: Path,
) -> tuple[tuple[LegacyArtifact, ...], tuple[str, ...]]:
    database_path = source_dir / "outreach.sqlite"
    if not database_path.exists():
        return (), ()

    artifacts: list[LegacyArtifact] = []
    warnings: list[str] = []
    try:
        uri = f"{database_path.resolve().as_uri()}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as connection:
            existing_tables = _sqlite_tables(connection)
            for table in OUTREACH_SQLITE_TABLES:
                if table not in existing_tables:
                    continue
                content = _sqlite_table_snapshot(connection, table)
                artifacts.append(
                    LegacyArtifact(
                        source_app="recruiter_agency",
                        relative_path=f"outreach.sqlite::{table}.json",
                        kind="sqlite_table_snapshot",
                        size_bytes=len(content),
                        mtime_ns=None,
                        sha256=_sha256(content),
                        content=content,
                    )
                )
    except sqlite3.Error as error:
        warnings.append(f"could not read outreach.sqlite in read-only mode: {error}")
    return tuple(artifacts), tuple(warnings)


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_table_snapshot(connection: sqlite3.Connection, table: str) -> bytes:
    quoted_table = _quote_identifier(table)
    cursor = connection.execute(f"SELECT * FROM {quoted_table}")
    columns = [description[0] for description in cursor.description or ()]
    rows = [
        {column: _json_ready(value) for column, value in zip(columns, row, strict=True)}
        for row in cursor.fetchall()
    ]
    payload = {
        "table": table,
        "columns": columns,
        "rows": rows,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _quote_identifier(value: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError(f"unsafe sqlite identifier: {value!r}")
    return f'"{value}"'


def _json_ready(value: object) -> object:
    if isinstance(value, bytes):
        return {
            "bytes_sha256": _sha256(value),
            "size_bytes": len(value),
        }
    return value


def _write_import(
    *,
    import_id: str,
    source_app: SourceApp,
    source_dir: Path,
    target_root: Path,
    imported_at: str,
    artifacts: tuple[LegacyArtifact, ...],
    warnings: tuple[str, ...],
) -> Path:
    target_root.mkdir(parents=True, exist_ok=True)
    database_path = target_root / LEGACY_IMPORTS_DB_NAME
    with sqlite3.connect(database_path) as connection:
        _ensure_legacy_import_schema(connection)
        with connection:
            connection.execute(
                """
                INSERT INTO import_runs (
                    id, source_app, source_path, target_root, imported_at,
                    artifact_count, warning_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    source_app,
                    str(source_dir),
                    str(target_root),
                    imported_at,
                    len(artifacts),
                    len(warnings),
                ),
            )
            connection.executemany(
                """
                INSERT INTO legacy_artifacts (
                    import_id, source_app, relative_path, kind, size_bytes,
                    mtime_ns, sha256, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        artifact.source_app,
                        artifact.relative_path,
                        artifact.kind,
                        artifact.size_bytes,
                        artifact.mtime_ns,
                        artifact.sha256,
                        artifact.content,
                    )
                    for artifact in artifacts
                ],
            )
            connection.executemany(
                """
                INSERT INTO import_warnings (
                    import_id, source_app, source_path, message
                ) VALUES (?, ?, ?, ?)
                """,
                [(import_id, source_app, str(source_dir), warning) for warning in warnings],
            )
    return database_path


def _ensure_legacy_import_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS import_runs (
            id TEXT PRIMARY KEY,
            source_app TEXT NOT NULL,
            source_path TEXT NOT NULL,
            target_root TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            artifact_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS legacy_artifacts (
            import_id TEXT NOT NULL,
            source_app TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            kind TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER,
            sha256 TEXT NOT NULL,
            content BLOB NOT NULL,
            PRIMARY KEY (import_id, source_app, relative_path),
            FOREIGN KEY (import_id) REFERENCES import_runs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS import_warnings (
            import_id TEXT NOT NULL,
            source_app TEXT NOT NULL,
            source_path TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY (import_id) REFERENCES import_runs(id) ON DELETE CASCADE
        );
        """
    )


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(f"SELECT version FROM {SCHEMA_MIGRATIONS_TABLE}").fetchall()
    return {int(row["version"]) for row in rows}


def _validate_migration_versions(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    duplicate_versions = {version for version in versions if versions.count(version) > 1}
    if duplicate_versions:
        raise ValueError(f"duplicate migration versions: {sorted(duplicate_versions)}")
    if any(migration.version <= 0 for migration in migrations):
        raise ValueError("migration versions must be positive")


def _sqlite_row_to_mapping(row: sqlite3.Row) -> dict[str, SQLiteValue]:
    result: dict[str, SQLiteValue] = {}
    for key in row.keys():
        value = row[key]
        if value is None or isinstance(value, str | int | float | bytes):
            result[str(key)] = value
            continue
        raise TypeError(f"unsupported SQLite value type for column {key!r}: {type(value).__name__}")
    return result


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in {".sqlite", ".db"}:
        return "sqlite"
    if suffix == ".md":
        return "markdown"
    if suffix == ".csv":
        return "csv"
    return "file"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
