"""SQLite migration and read-only import primitives."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .sqlite import SQLiteValue, connect_sqlite

SCHEMA_MIGRATIONS_TABLE = "schema_migrations"

SQLiteParameters = Sequence[SQLiteValue] | Mapping[str, SQLiteValue]
RowMapper = Callable[[Mapping[str, SQLiteValue]], SQLiteParameters | None]


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
