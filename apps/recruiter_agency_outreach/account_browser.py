"""Playwriter-backed Sales Navigator account capture."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from packages.linkedin_browser.playwriter import PlaywriterRunner

DEFAULT_ACCOUNT_CAPTURE_OUT_DIR = Path("/tmp/recruiter-agency-outreach-account-capture")


class PlaywriterAccountCaptureClient:
    """Playwriter-backed Sales Navigator company/account capture."""

    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_ACCOUNT_CAPTURE_OUT_DIR,
        session: str | None = None,
        browser_key: str | None = None,
        playwriter_bin: str | None = None,
    ) -> None:
        self.out_dir = out_dir
        self._runner = PlaywriterRunner(
            session=session,
            browser_key=browser_key,
            playwriter_bin=playwriter_bin,
            config_state_key="state.linkedinToolsConfigPath",
            command_timeout_seconds=130,
            session_timeout_seconds=30,
            output_wait_seconds=2.0,
            allow_numeric_session_id_parse=True,
        )
        self._counter = 0

    @property
    def session(self) -> str:
        return self._runner.session

    def close(self) -> None:
        return None

    def capture_accounts(
        self,
        *,
        source: str,
        url: str | None = None,
        pages: int = 1,
        limit: int = 25,
    ) -> tuple[dict[str, Any], str]:
        self._counter += 1
        out = self.out_dir / f"{self._counter:03d}-{_safe_stem(source)}-accounts.json"
        config = {
            "source": source,
            "url": url,
            "pages": pages,
            "limit": limit,
            "out": str(out),
        }
        self._run_script(_playwriter_account_capture_script(), config)
        payload = json.loads(out.read_text())
        if not isinstance(payload, dict):
            raise RuntimeError("Playwriter account capture output must be a JSON object")
        return payload, str(out)

    def _run_script(self, script: Path, config: dict[str, Any]) -> None:
        self._runner.run_script(
            script,
            config,
            output_missing_message=(
                "Playwriter account capture script did not write an output artifact"
            ),
            out_dir=self.out_dir,
            staging="temporary",
        )


def _playwriter_account_capture_script() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts" / "account_capture.js"


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return stem or "capture"
