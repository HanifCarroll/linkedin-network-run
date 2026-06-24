"""Shared SQLite and migration primitives."""

from .json_store import (
    read_csv_rows,
    read_jsonl_dicts,
    read_jsonl_models,
    write_csv_rows,
    write_jsonl,
)
from .migrations import (
    LEGACY_IMPORTS_DB_NAME,
    ImportResult,
    Migration,
    MigrationResult,
    apply_migrations,
    copy_rows_from_readonly_source,
    import_all_legacy_state,
    import_legacy_network_state,
    import_legacy_opportunity_runs,
    import_legacy_recruiter_agency_state,
    latest_import_summary,
)
from .sqlite import SQLiteValue, connect_sqlite, dict_rows, transaction

__all__ = [
    "LEGACY_IMPORTS_DB_NAME",
    "ImportResult",
    "Migration",
    "MigrationResult",
    "SQLiteValue",
    "apply_migrations",
    "connect_sqlite",
    "copy_rows_from_readonly_source",
    "dict_rows",
    "import_all_legacy_state",
    "import_legacy_network_state",
    "import_legacy_opportunity_runs",
    "import_legacy_recruiter_agency_state",
    "latest_import_summary",
    "read_csv_rows",
    "read_jsonl_dicts",
    "read_jsonl_models",
    "transaction",
    "write_csv_rows",
    "write_jsonl",
]
