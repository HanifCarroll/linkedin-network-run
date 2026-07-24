from __future__ import annotations

from pathlib import Path

import pytest

from packages.linkedin_browser.incident import BROWSER_LOCK_PATH_ENV, INCIDENT_PATH_ENV


@pytest.fixture(autouse=True)
def isolated_linkedin_incident_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(INCIDENT_PATH_ENV, str(tmp_path / "linkedin-incident.json"))
    monkeypatch.setenv(BROWSER_LOCK_PATH_ENV, str(tmp_path / "browser-operation.lock"))
