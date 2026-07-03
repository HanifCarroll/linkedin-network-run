"""Shared SQLite and migration primitives."""

from .migrations import (
    LEGACY_IMPORTS_DB_NAME,
    Migration,
    MigrationResult,
    apply_migrations,
    import_all_legacy_state,
    import_legacy_network_state,
    import_legacy_opportunity_runs,
    import_legacy_recruiter_agency_state,
    latest_import_summary,
)
from .sqlite import SQLiteValue, connect_sqlite, transaction

__all__ = [
    "LEGACY_IMPORTS_DB_NAME",
    "Migration",
    "MigrationResult",
    "SQLiteValue",
    "apply_migrations",
    "connect_sqlite",
    "import_all_legacy_state",
    "import_legacy_network_state",
    "import_legacy_opportunity_runs",
    "import_legacy_recruiter_agency_state",
    "latest_import_summary",
    "transaction",
]
