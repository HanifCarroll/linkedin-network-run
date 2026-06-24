"""SQLite connection helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

type SQLiteValue = str | int | float | bytes | None


def connect_sqlite(
    path: Path,
    *,
    readonly: bool = False,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    """Open a SQLite database with project defaults."""

    db_path = path.expanduser().resolve()
    if readonly:
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        uri = f"file:{quote(db_path.as_posix(), safe='/')}?mode=ro"
        conn = sqlite3.connect(uri, timeout=timeout, uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap SQLite work in commit/rollback handling."""

    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def dict_rows(cursor: sqlite3.Cursor) -> list[dict[str, SQLiteValue]]:
    """Return cursor rows as plain dictionaries."""

    rows: list[dict[str, SQLiteValue]] = []
    for row in cursor.fetchall():
        result: dict[str, SQLiteValue] = {}
        for key in row.keys():
            value = row[key]
            if value is None or isinstance(value, str | int | float | bytes):
                result[str(key)] = value
                continue
            raise TypeError(
                f"unsupported SQLite value type for column {key!r}: {type(value).__name__}"
            )
        rows.append(result)
    return rows
