from __future__ import annotations

from pathlib import Path

import pytest

from packages.linkedin_storage import (
    Migration,
    apply_migrations,
    connect_sqlite,
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
        rows = [dict(row) for row in conn.execute("SELECT version, name FROM schema_migrations")]
        assert rows == [
            {"version": 1, "name": "create_sources"},
            {"version": 2, "name": "add_label"},
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
