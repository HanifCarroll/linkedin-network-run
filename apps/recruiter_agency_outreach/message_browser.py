"""Live browser adapter for recruiter/agency LinkedIn messages."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from apps.network_automation.browser import _safe_stem
from packages.linkedin_browser.playwriter import PlaywriterRunner

from .send import MessageSendResult, load_message_send_result

DEFAULT_MESSAGE_OUT_DIR = Path("/tmp/recruiter-agency-outreach-message")


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
