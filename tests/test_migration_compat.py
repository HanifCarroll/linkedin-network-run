from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from apps.compat import (
    NETWORK_APP_COMMANDS,
    NETWORK_COMMANDS,
    OPPORTUNITY_APP_COMMANDS,
    OPPORTUNITY_COMMANDS,
    linkedin_network_run,
    linkedin_opportunity_intel,
)
from packages.linkedin_storage.migrations import (
    LEGACY_IMPORTS_DB_NAME,
    import_legacy_network_state,
    import_legacy_opportunity_runs,
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
    assert (target_root / "network-automation" / "active.json").read_bytes() == active_content
    assert (target_root / "network-automation" / "run-1.jsonl").read_bytes() == log_content
    assert (
        _artifact_content(target_root, result.import_id, "network", "active.json")
        == active_content
    )
    summary = latest_import_summary(source_app="network", target_root=target_root)
    assert summary is not None
    assert summary["artifact_count"] == 2


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


def test_compatibility_command_sets_delegate_known_commands() -> None:
    assert set(NETWORK_COMMANDS) - NETWORK_APP_COMMANDS == {"import-legacy-state"}
    assert set(OPPORTUNITY_COMMANDS) - OPPORTUNITY_APP_COMMANDS == {"import-legacy-state"}


def test_compat_help_and_delegated_command_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    assert (
        linkedin_network_run(
            [
                "capture",
                "--source",
                "Consulting - Founder Owner Buyers",
                "--saved-searches",
                "",
                "--fixture-result",
                "tests/fixtures/network_automation/capture_consulting.json",
                "--state-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "captured 3 candidate observations" in capsys.readouterr().out

    assert linkedin_opportunity_intel(["status", "--json", "--target-root", str(tmp_path)]) == 0
    opportunity_payload = json.loads(capsys.readouterr().out)
    assert opportunity_payload["recommend_only"] is True

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
