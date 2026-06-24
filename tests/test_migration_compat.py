from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from apps.compat import (
    linkedin_network_run,
    linkedin_opportunity_intel,
    recruiter_agency_outreach,
)
from packages.linkedin_storage.migrations import (
    LEGACY_IMPORTS_DB_NAME,
    import_legacy_network_state,
    import_legacy_opportunity_runs,
    import_legacy_recruiter_agency_state,
    latest_import_summary,
)


def test_network_import_preserves_legacy_files(tmp_path: Path) -> None:
    old_state = tmp_path / "old-network"
    old_state.mkdir()
    active_content = b'{"id":"run-1","state":"Done","candidates":[]}\n'
    log_content = b'{"kind":"sent","payload":{"name":"Ada"}}\n'
    (old_state / "active.json").write_bytes(active_content)
    (old_state / "run-1.jsonl").write_bytes(log_content)
    target_root = tmp_path / "linkedin-tools"
    before = _hash_tree(old_state)

    result = import_legacy_network_state(old_state_dir=old_state, target_root=target_root)

    assert _hash_tree(old_state) == before
    assert result.artifact_count == 2
    assert result.warnings == ()
    assert (
        _artifact_content(target_root, result.import_id, "network", "active.json")
        == active_content
    )
    summary = latest_import_summary(source_app="network", target_root=target_root)
    assert summary is not None
    assert summary["artifact_count"] == 2


def test_recruiter_agency_import_preserves_json_sqlite_and_table_snapshots(
    tmp_path: Path,
) -> None:
    old_state = tmp_path / "old-outreach"
    old_state.mkdir()
    outreach_json = b'{"schema_version":1,"leads":[]}\n'
    (old_state / "outreach.json").write_bytes(outreach_json)
    database_path = old_state / "outreach.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        connection.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
        connection.execute(
            "INSERT INTO leads (id, data) VALUES (?, ?)",
            ("lead-1", '{"id":"lead-1","name":"Grace"}'),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (old_state / "outreach.sqlite-shm").unlink(missing_ok=True)
    (old_state / "outreach.sqlite-wal").unlink(missing_ok=True)

    target_root = tmp_path / "linkedin-tools"
    before = _hash_tree(old_state)

    result = import_legacy_recruiter_agency_state(
        old_state_dir=old_state,
        target_root=target_root,
    )

    assert _hash_tree(old_state) == before
    assert result.artifact_count == 4
    assert _artifact_content(
        target_root,
        result.import_id,
        "recruiter_agency",
        "outreach.json",
    ) == outreach_json
    lead_snapshot = _artifact_content(
        target_root,
        result.import_id,
        "recruiter_agency",
        "outreach.sqlite::leads.json",
    )
    assert b"lead-1" in lead_snapshot
    assert b"Grace" in lead_snapshot


def test_missing_opportunity_import_records_warning_without_source_mutation(
    tmp_path: Path,
) -> None:
    missing_source = tmp_path / "missing-opportunity"
    target_root = tmp_path / "linkedin-tools"

    result = import_legacy_opportunity_runs(
        old_state_dir=missing_source,
        target_root=target_root,
    )

    assert result.artifact_count == 0
    assert result.warnings == (f"source directory does not exist: {missing_source}",)
    assert (target_root / LEGACY_IMPORTS_DB_NAME).exists()


def test_compat_help_status_and_no_send_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert linkedin_network_run(["--help"]) == 0
    assert "send-guarded" in capsys.readouterr().out

    assert linkedin_network_run(["start", "--target", "1", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert linkedin_network_run(["status", "--json", "--state-dir", str(tmp_path)]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["target"] == 1

    assert (
        linkedin_network_run(
            ["send-next", "--session", "1", "--dry-run", "--state-dir", str(tmp_path)]
        )
        == 1
    )
    network_error = capsys.readouterr().err
    assert "browser is unavailable" in network_error or "connectable candidate" in network_error

    assert linkedin_network_run(["reconcile-audit", "--allow-send"]) == 2
    assert "blocked" in capsys.readouterr().err

    assert recruiter_agency_outreach(["dashboard", "--state-dir", str(tmp_path)]) == 0
    assert "dashboard=" in capsys.readouterr().out

    assert (
        recruiter_agency_outreach(["run-daily", "--state-dir", str(tmp_path), "--allow-send"])
        == 1
    )
    assert "run-daily is sourcing-only" in capsys.readouterr().err

    assert linkedin_opportunity_intel(["status", "--json", "--target-root", str(tmp_path)]) == 0
    opportunity_payload = json.loads(capsys.readouterr().out)
    assert opportunity_payload["source_app"] == "opportunity"

    assert linkedin_opportunity_intel(["validate-contracts"]) == 0
    assert "validated" in capsys.readouterr().out


def test_network_compat_import_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_state = tmp_path / "old-network"
    old_state.mkdir()
    (old_state / "active.json").write_text('{"id":"run-1"}\n')
    target_root = tmp_path / "linkedin-tools"

    assert (
        linkedin_network_run(
            [
                "import-legacy-state",
                "--old-state-dir",
                str(old_state),
                "--target-root",
                str(target_root),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifact_count"] == 1
    assert payload["database_path"] == str(target_root / LEGACY_IMPORTS_DB_NAME)


def _hash_tree(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return hashes


def _artifact_content(
    target_root: Path,
    import_id: str,
    source_app: str,
    relative_path: str,
) -> bytes:
    with sqlite3.connect(target_root / LEGACY_IMPORTS_DB_NAME) as connection:
        row = connection.execute(
            """
            SELECT content
            FROM legacy_artifacts
            WHERE import_id = ? AND source_app = ? AND relative_path = ?
            """,
            (import_id, source_app, relative_path),
        ).fetchone()
    assert row is not None
    return cast(bytes, row[0])
