"""Playwriter-backed Sales Navigator account capture."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from apps.network_automation.store import write_json_atomic

DEFAULT_ACCOUNT_CAPTURE_OUT_DIR = Path("/tmp/recruiter-agency-outreach-account-capture")
PLAYWRITER_BIN_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BIN"
PLAYWRITER_BROWSER_KEY_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY"
PLAYWRITER_SESSION_ENV = "LINKEDIN_TOOLS_PLAYWRITER_SESSION"


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
        self._session = session or os.environ.get(PLAYWRITER_SESSION_ENV)
        self._browser_key = browser_key or os.environ.get(PLAYWRITER_BROWSER_KEY_ENV)
        self._playwriter_bin = playwriter_bin or _playwriter_bin()
        self._counter = 0

    @property
    def session(self) -> str:
        if self._session is None:
            self._session = self._create_session()
        return self._session

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

    def _create_session(self) -> str:
        command = [self._playwriter_bin, "session", "new"]
        if self._browser_key:
            command.extend(["--browser", self._browser_key])
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Playwriter session creation failed: {result.stderr.strip()}")
        match = re.search(r"\b(\d+)\b", result.stdout)
        if not match:
            raise RuntimeError(f"could not parse Playwriter session id from: {result.stdout}")
        return match.group(1)

    def _run_script(self, script: Path, config: dict[str, Any]) -> None:
        final_out = Path(str(config["out"]))
        final_out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="linkedin-tools-playwriter-") as tmp:
            config_path = Path(tmp) / "config.json"
            staged_out = Path(tmp) / final_out.name
            staged_config = dict(config, out=str(staged_out))
            write_json_atomic(config_path, staged_config)
            command = [
                self._playwriter_bin,
                "-s",
                self.session,
                "-e",
                f"state.linkedinToolsConfigPath = {json.dumps(str(config_path))}",
            ]
            _run_playwriter_command(command)
            _run_playwriter_command(
                [self._playwriter_bin, "-s", self.session, "-f", str(script), "--timeout", "120000"]
            )
            if not _wait_for_path(staged_out):
                raise RuntimeError(
                    "Playwriter account capture script did not write an output artifact; "
                    f"expected {staged_out}"
                )
            shutil.move(str(staged_out), str(final_out))


def _playwriter_bin() -> str:
    configured = os.environ.get(PLAYWRITER_BIN_ENV)
    if configured:
        return configured
    default = Path.home() / ".bun/bin/playwriter"
    if default.exists():
        return str(default)
    resolved = shutil.which("playwriter")
    if resolved:
        return resolved
    raise RuntimeError("Playwriter binary was not found; set LINKEDIN_TOOLS_PLAYWRITER_BIN")


def _run_playwriter_command(command: list[str]) -> None:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=130)
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"Playwriter command failed ({result.returncode}): {' '.join(command)}\n{reason}"
        )


def _playwriter_account_capture_script() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts" / "account_capture.js"


def _wait_for_path(path: Path, *, timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return stem or "capture"
