from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.linkedin_storage import (
    Migration,
    apply_migrations,
    connect_sqlite,
    copy_rows_from_readonly_source,
    dict_rows,
)


def test_apply_migrations_records_versions(tmp_path: Path) -> None:
    db_path = tmp_path / "target.sqlite"
    with connect_sqlite(db_path) as conn:
        applied = apply_migrations(
            conn,
            [
                Migration(1, "create_sources", "CREATE TABLE sources (id TEXT PRIMARY KEY);"),
                Migration(2, "add_label", "ALTER TABLE sources ADD COLUMN label TEXT;"),
            ],
        )
        applied_again = apply_migrations(
            conn,
            [
                Migration(1, "create_sources", "CREATE TABLE sources (id TEXT PRIMARY KEY);"),
                Migration(2, "add_label", "ALTER TABLE sources ADD COLUMN label TEXT;"),
            ],
        )

        assert [migration.version for migration in applied] == [1, 2]
        assert applied_again == []
        assert dict_rows(conn.execute("SELECT version, name FROM schema_migrations")) == [
            {"version": 1, "name": "create_sources"},
            {"version": 2, "name": "add_label"},
        ]


def test_readonly_source_import_copies_to_target_without_mutating_source(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite"
    target_path = tmp_path / "target.sqlite"
    with connect_sqlite(source_path) as source_conn:
        source_conn.execute("CREATE TABLE comments (id TEXT PRIMARY KEY, text TEXT NOT NULL)")
        source_conn.execute("INSERT INTO comments (id, text) VALUES (?, ?)", ("c1", "hello"))
        source_conn.commit()

    with connect_sqlite(target_path) as target_conn:
        target_conn.execute(
            "CREATE TABLE imported_comments (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
        result = copy_rows_from_readonly_source(
            source_path=source_path,
            target_conn=target_conn,
            select_sql="SELECT id, text FROM comments",
            insert_sql="INSERT INTO imported_comments (id, body) VALUES (?, ?)",
            row_mapper=lambda row: (row["id"], row["text"]),
        )

        assert result.rows_read == 1
        assert result.rows_inserted == 1
        assert dict_rows(target_conn.execute("SELECT id, body FROM imported_comments")) == [
            {"id": "c1", "body": "hello"}
        ]

    with connect_sqlite(source_path, readonly=True) as readonly_conn:
        with pytest.raises(sqlite3.OperationalError):
            readonly_conn.execute(
                "INSERT INTO comments (id, text) VALUES (?, ?)",
                ("c2", "blocked"),
            )
        assert dict_rows(readonly_conn.execute("SELECT id, text FROM comments")) == [
            {"id": "c1", "text": "hello"}
        ]


def test_duplicate_migration_versions_are_rejected(tmp_path: Path) -> None:
    with connect_sqlite(tmp_path / "target.sqlite") as conn:
        with pytest.raises(ValueError, match="duplicate migration"):
            apply_migrations(
                conn,
                [
                    Migration(1, "one", "CREATE TABLE one (id TEXT);"),
                    Migration(1, "two", "CREATE TABLE two (id TEXT);"),
                ],
            )
