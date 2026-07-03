"""Shared Playwriter command runner for browser adapters."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

PLAYWRITER_BIN_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BIN"
PLAYWRITER_BROWSER_KEY_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY"
PLAYWRITER_SESSION_ENV = "LINKEDIN_TOOLS_PLAYWRITER_SESSION"
DEFAULT_SCRIPT_TIMEOUT_MS = 240_000

_SESSION_CREATED = re.compile(r"Session\s+(\S+)\s+created")
_STAGING_DIR = Path(tempfile.gettempdir()) / "linkedin-tools-playwriter"
StagingMode = Literal["shared", "temporary"]


class PlaywriterRunner:
    """Runs Playwriter scripts with shared session, config, and artifact staging."""

    def __init__(
        self,
        *,
        session: str | None = None,
        browser_key: str | None = None,
        playwriter_bin: str | None = None,
        config_state_key: str = "state.linkedinToolsConfigPath",
        script_timeout_ms: int = DEFAULT_SCRIPT_TIMEOUT_MS,
        command_timeout_seconds: float | None = None,
        session_timeout_seconds: float | None = None,
        output_wait_seconds: float = 5.0,
        allow_numeric_session_id_parse: bool = False,
    ) -> None:
        self._session = session or os.environ.get(PLAYWRITER_SESSION_ENV)
        self._browser_key = browser_key or os.environ.get(PLAYWRITER_BROWSER_KEY_ENV)
        self._playwriter_bin = playwriter_bin or resolve_playwriter_bin()
        self._config_state_key = config_state_key
        self._script_timeout_ms = script_timeout_ms
        self._command_timeout_seconds = command_timeout_seconds
        self._session_timeout_seconds = session_timeout_seconds
        self._output_wait_seconds = output_wait_seconds
        self._allow_numeric_session_id_parse = allow_numeric_session_id_parse

    @property
    def session(self) -> str:
        if self._session is None:
            self._session = self._create_session()
        return self._session

    def run_script(
        self,
        script: Path,
        config: Mapping[str, Any],
        *,
        output_missing_message: str,
        out_dir: Path | None = None,
        staging: StagingMode = "shared",
        progress: bool = False,
    ) -> None:
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
        if staging == "temporary":
            self._run_with_temporary_staging(
                script,
                config,
                output_missing_message=output_missing_message,
                progress=progress,
            )
            return
        self._run_with_shared_staging(
            script,
            config,
            output_missing_message=output_missing_message,
            progress=progress,
        )

    def _create_session(self) -> str:
        command = [self._playwriter_bin, "session", "new"]
        if self._browser_key:
            command.extend(["--browser", self._browser_key])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self._session_timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"Playwriter session creation failed ({result.returncode})"
                + (f": {detail}" if detail else "")
            )
        match = _SESSION_CREATED.search(result.stdout)
        if not match:
            if self._allow_numeric_session_id_parse:
                match = re.search(r"\b(\d+)\b", result.stdout)
        if not match:
            raise RuntimeError(f"could not parse Playwriter session id from: {result.stdout}")
        return match.group(1)

    def _run_with_shared_staging(
        self,
        script: Path,
        config: Mapping[str, Any],
        *,
        output_missing_message: str,
        progress: bool,
    ) -> None:
        config_path, staged_out, final_out = stage_playwriter_config(config)
        script_config = dict(config)
        progress_out = progress_path(final_out) if progress else None
        if staged_out is not None:
            script_config["out"] = str(staged_out)
        if progress_out is not None:
            progress_out.parent.mkdir(parents=True, exist_ok=True)
            progress_out.unlink(missing_ok=True)
            script_config["progressOut"] = str(progress_out)
        write_json_atomic(config_path, script_config)
        self._stage_config_path(config_path)
        self._run_file(script, progress_path=progress_out)
        self._move_staged_output(
            staged_out,
            final_out,
            output_missing_message=output_missing_message,
        )

    def _run_with_temporary_staging(
        self,
        script: Path,
        config: Mapping[str, Any],
        *,
        output_missing_message: str,
        progress: bool,
    ) -> None:
        final_out = final_output_path(config)
        if final_out is None:
            raise RuntimeError("Playwriter temporary staging requires an out path")
        final_out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="linkedin-tools-playwriter-") as tmp:
            config_path = Path(tmp) / "config.json"
            staged_out = Path(tmp) / final_out.name
            script_config = dict(config, out=str(staged_out))
            progress_out = progress_path(final_out) if progress else None
            if progress_out is not None:
                progress_out.parent.mkdir(parents=True, exist_ok=True)
                progress_out.unlink(missing_ok=True)
                script_config["progressOut"] = str(progress_out)
            write_json_atomic(config_path, script_config)
            self._stage_config_path(config_path)
            self._run_file(script, progress_path=progress_out)
            self._move_staged_output(
                staged_out,
                final_out,
                output_missing_message=output_missing_message,
            )

    def _stage_config_path(self, config_path: Path) -> None:
        self._run_command(
            [
                self._playwriter_bin,
                "-s",
                self.session,
                "-e",
                f"{self._config_state_key} = {json.dumps(str(config_path))}",
            ]
        )

    def _run_file(self, script: Path, *, progress_path: Path | None) -> None:
        self._run_command(
            [
                self._playwriter_bin,
                "-s",
                self.session,
                "-f",
                str(script),
                "--timeout",
                str(self._script_timeout_ms),
            ],
            progress_path=progress_path,
        )

    def _run_command(self, command: list[str], *, progress_path: Path | None = None) -> None:
        if progress_path is None:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._command_timeout_seconds,
                check=False,
            )
        else:
            result = run_playwriter_command_with_progress(command, progress_path)
        if result.returncode != 0:
            detail = "\n".join(
                part for part in (result.stdout.strip(), result.stderr.strip()) if part
            )
            raise RuntimeError(
                f"Playwriter command failed ({result.returncode}): {' '.join(command)}"
                + (f"\n{detail}" if detail else "")
            )

    def _move_staged_output(
        self,
        staged_out: Path | None,
        final_out: Path | None,
        *,
        output_missing_message: str,
    ) -> None:
        if staged_out is None or final_out is None:
            return
        if not wait_for_path(staged_out, timeout_seconds=self._output_wait_seconds):
            raise RuntimeError(f"{output_missing_message}; expected {staged_out}")
        final_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged_out), str(final_out))


def resolve_playwriter_bin() -> str:
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


def stage_playwriter_config(config: Mapping[str, Any]) -> tuple[Path, Path | None, Path | None]:
    _STAGING_DIR.mkdir(parents=True, exist_ok=True)
    final_out = final_output_path(config)
    stem = safe_stem(Path(str(config.get("out") or "artifact.json")).stem)
    config_path = _STAGING_DIR / f"{stem}-config.json"
    staged_out = _STAGING_DIR / f"{stem}-out.json" if final_out is not None else None
    return config_path, staged_out, final_out


def final_output_path(config: Mapping[str, Any]) -> Path | None:
    return Path(str(config["out"])) if config.get("out") else None


def progress_path(final_out: Path | None) -> Path | None:
    return Path(f"{final_out}.progress.jsonl") if final_out is not None else None


def wait_for_path(path: Path, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def run_playwriter_command_with_progress(
    command: list[str], progress_path: Path
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryFile("w+", encoding="utf-8") as stdout:
        with tempfile.TemporaryFile("w+", encoding="utf-8") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)
            progress_position = 0
            while process.poll() is None:
                progress_position = emit_progress_events(progress_path, progress_position)
                time.sleep(0.25)
            progress_position = emit_progress_events(progress_path, progress_position)
            stdout.seek(0)
            stderr.seek(0)
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout.read(),
                stderr.read(),
            )


def emit_progress_events(progress_path: Path, position: int) -> int:
    if not progress_path.exists():
        return position
    with progress_path.open(encoding="utf-8") as handle:
        handle.seek(position)
        while line := handle.readline():
            line = line.strip()
            if not line:
                continue
            print(format_progress_event(line), file=sys.stderr, flush=True)
        return handle.tell()


def format_progress_event(line: str) -> str:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return f"browser progress: {line}"
    step = str(event.get("step") or "progress")
    details = []
    for key, value in event.items():
        if key in {"at", "step"} or value is None or value == "" or value is False:
            continue
        if isinstance(value, bool):
            details.append(f"{key}=true")
        elif isinstance(value, int | float | str):
            details.append(f"{key}={value}")
        elif isinstance(value, list | dict):
            compact = json.dumps(value, separators=(",", ":"), sort_keys=True)[:180]
            details.append(f"{key}={compact}")
    suffix = " " + " ".join(details) if details else ""
    return f"browser progress: {step}{suffix}"


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return stem or "artifact"
