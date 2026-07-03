"""Live browser adapter for recruiter/agency LinkedIn messages."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from apps.network_automation.browser import (
    _find_composer,
    _locator_count,
    _locator_disabled,
    _locator_visible,
    _safe_stem,
)
from packages.linkedin_browser.playwriter import PlaywriterRunner

from .send import MessageSendResult, load_message_send_result

DEFAULT_MESSAGE_OUT_DIR = Path("/tmp/recruiter-agency-outreach-message")
SALES_NAV_INMAIL_ACTION = "button[data-anchor-send-inmail]"
COMPOSER_WAIT_ATTEMPTS = 20
COMPOSER_WAIT_MS = 500


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
        self._runner = PlaywriterRunner(
            session=session,
            browser_key=browser_key,
            playwriter_bin=playwriter_bin,
            config_state_key="state.recruiterAgencyMessageConfigPath",
        )

    @property
    def session(self) -> str:
        return self._runner.session

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

    def _run_script(self, script: Path, config: dict[str, Any]) -> None:
        self._runner.run_script(
            script,
            config,
            output_missing_message=(
                "Playwriter recruiter message script did not write an output artifact"
            ),
            out_dir=self.out_dir,
        )


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

def _playwriter_message_script() -> Path:
    return Path(__file__).resolve().parent / "playwriter_scripts" / "send_message.js"
