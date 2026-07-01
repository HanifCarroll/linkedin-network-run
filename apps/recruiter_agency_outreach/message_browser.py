"""Live browser adapter for recruiter/agency LinkedIn messages."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from apps.network_automation.browser import (
    _find_composer,
    _locator_count,
    _locator_disabled,
    _locator_visible,
    _safe_stem,
    _wait_for_path,
)
from apps.network_automation.store import write_json_atomic

from .send import MessageSendResult, load_message_send_result

DEFAULT_MESSAGE_OUT_DIR = Path("/tmp/recruiter-agency-outreach-message")
SALES_NAV_INMAIL_ACTION = "button[data-anchor-send-inmail]"
COMPOSER_WAIT_ATTEMPTS = 20
COMPOSER_WAIT_MS = 500
PLAYWRITER_BIN_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BIN"
PLAYWRITER_BROWSER_KEY_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY"
PLAYWRITER_SESSION_ENV = "LINKEDIN_TOOLS_PLAYWRITER_SESSION"


class PlaywriterMessageBrowserClient:
    """Playwriter-backed adapter for guarded recruiter/agency messages."""

    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_MESSAGE_OUT_DIR,
        session: str | None = None,
        browser_key: str | None = None,
        playwriter_bin: str | None = None,
    ) -> None:
        self.out_dir = out_dir
        self._session = session or os.environ.get(PLAYWRITER_SESSION_ENV)
        self._browser_key = browser_key or os.environ.get(PLAYWRITER_BROWSER_KEY_ENV)
        self._playwriter_bin = playwriter_bin or _playwriter_bin()

    @property
    def session(self) -> str:
        if self._session is None:
            self._session = self._create_session()
        return self._session

    def close(self) -> None:
        return None

    def send_message(
        self,
        config: Mapping[str, Any],
        *,
        dry_run: bool,
        allow_send: bool,
    ) -> tuple[MessageSendResult, str]:
        if not dry_run and not allow_send:
            raise RuntimeError("real send requires allow_send=True")
        candidate = _candidate(config)
        out = self.out_dir / f"{_safe_stem(str(candidate['id']))}-message-result.json"
        payload = {
            "candidate": candidate,
            "message": str(config.get("message") or ""),
            "subject": str(config.get("subject") or ""),
            "dryRun": dry_run,
            "allowSend": allow_send,
            "out": str(out),
        }
        self._run_script(_playwriter_message_script(), payload)
        return load_message_send_result(out), str(out)

    def _create_session(self) -> str:
        command = [self._playwriter_bin, "session", "new"]
        if self._browser_key:
            command.extend(["--browser", self._browser_key])
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        match = re.search(r"Session\s+(\S+)\s+created", result.stdout)
        if not match:
            raise RuntimeError(f"could not parse Playwriter session id from: {result.stdout}")
        return match.group(1)

    def _run_script(self, script: Path, config: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        config_path, staged_out, final_out = _stage_playwriter_config(config)
        script_config = dict(config)
        if staged_out is not None:
            script_config["out"] = str(staged_out)
        write_json_atomic(config_path, script_config)
        _run_playwriter_command(
            [
                self._playwriter_bin,
                "-s",
                self.session,
                "-e",
                f"state.recruiterAgencyMessageConfigPath = {json.dumps(str(config_path))}",
            ]
        )
        _run_playwriter_command(
            [self._playwriter_bin, "-s", self.session, "-f", str(script), "--timeout", "120000"],
        )
        if staged_out is not None and final_out is not None:
            if not _wait_for_path(staged_out):
                raise RuntimeError(
                    "Playwriter recruiter message script did not write an output artifact; "
                    f"expected {staged_out}"
                )
            final_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_out), str(final_out))
async def _click_message_action(page: Any, action: Mapping[str, Any]) -> dict[str, Any]:
    inmail = page.locator(SALES_NAV_INMAIL_ACTION).first
    if (
        await _locator_count(inmail)
        and await _locator_visible(inmail)
        and not await _locator_disabled(inmail)
    ):
        box = await inmail.bounding_box()
        if box and box.get("width") and box.get("height"):
            x = max(1.0, min(8.0, float(box["width"]) - 1.0))
            y = max(1.0, min(float(box["height"]) / 2.0, float(box["height"]) - 1.0))
            await inmail.click(position={"x": x, "y": y}, timeout=8000)
            return {
                "method": "salesnav-inmail-padding-click",
                "selector": SALES_NAV_INMAIL_ACTION,
                "position": {"x": x, "y": y},
            }
        await inmail.click(timeout=8000)
        return {
            "method": "salesnav-inmail-default-click",
            "selector": SALES_NAV_INMAIL_ACTION,
        }

    locator = action["locator"]
    await locator.click(timeout=8000)
    return {
        "method": "generic-message-action-click",
        "label": str(action.get("label") or ""),
    }


async def _wait_for_message_composer(page: Any) -> dict[str, Any] | None:
    for _ in range(COMPOSER_WAIT_ATTEMPTS):
        composer = await _find_composer(page)
        if composer is not None:
            return composer
        await page.wait_for_timeout(COMPOSER_WAIT_MS)
    return await _find_composer(page)


def _candidate(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config.get("candidate")
    if not isinstance(raw, Mapping):
        raise RuntimeError("candidate is required")
    candidate = dict(raw)
    if not candidate.get("id"):
        raise RuntimeError("candidate id is required")
    if not candidate.get("profileUrl"):
        raise RuntimeError("candidate with profileUrl is required")
    if not str(config.get("message") or "").strip():
        raise RuntimeError("message is required")
    return candidate


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


def _playwriter_message_script() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts" / "send_message.js"


def _stage_playwriter_config(config: dict[str, Any]) -> tuple[Path, Path | None, Path | None]:
    staging_dir = Path(tempfile.gettempdir()) / "linkedin-tools-playwriter"
    staging_dir.mkdir(parents=True, exist_ok=True)
    final_out = Path(str(config["out"])) if config.get("out") else None
    stem = _safe_stem(final_out.stem if final_out is not None else "message-result")
    config_path = staging_dir / f"{stem}-config.json"
    staged_out = staging_dir / f"{stem}-out.json" if final_out is not None else None
    return config_path, staged_out, final_out


def _run_playwriter_command(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        )
        raise RuntimeError(
            f"Playwriter command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
