from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.network_automation.browser import PlaywriterBrowserClient
from apps.recruiter_agency_outreach.message_browser import PlaywriterMessageBrowserClient
from packages.linkedin_browser.playwriter import PlaywriterRunner, format_progress_event


def test_network_playwriter_browser_uses_runner_progress_config(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    playwriter = _fake_playwriter(tmp_path, state_path=state_path)
    final_out = tmp_path / "network-result.json"
    client = PlaywriterBrowserClient(
        out_dir=tmp_path / "network",
        session="network-session",
        playwriter_bin=str(playwriter),
    )

    client._run_script(tmp_path / "network.js", {"out": str(final_out), "value": "network"})

    state = json.loads(state_path.read_text(encoding="utf-8"))
    staged_config = json.loads(Path(state["config_path"]).read_text(encoding="utf-8"))
    assert state["expression"].startswith("state.linkedinToolsConfigPath = ")
    assert staged_config["progressOut"] == f"{final_out}.progress.jsonl"
    assert json.loads(final_out.read_text(encoding="utf-8"))["received"] == "network"


def test_recruiter_message_browser_uses_message_config_state_key(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    playwriter = _fake_playwriter(tmp_path, state_path=state_path)
    final_out = tmp_path / "message-result.json"
    client = PlaywriterMessageBrowserClient(
        out_dir=tmp_path / "message",
        session="message-session",
        playwriter_bin=str(playwriter),
    )

    client._run_script(tmp_path / "message.js", {"out": str(final_out), "value": "message"})

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["expression"].startswith("state.recruiterAgencyMessageConfigPath = ")
    assert json.loads(final_out.read_text(encoding="utf-8"))["received"] == "message"


def test_playwriter_runner_stages_config_and_moves_output(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    playwriter = _fake_playwriter(tmp_path, state_path=state_path)
    final_out = tmp_path / "result.json"
    runner = PlaywriterRunner(
        playwriter_bin=str(playwriter),
        config_state_key="state.linkedinToolsConfigPath",
    )

    runner.run_script(
        tmp_path / "script.js",
        {"out": str(final_out), "value": "kept"},
        output_missing_message="missing output",
        out_dir=tmp_path / "artifacts",
    )

    assert runner.session == "42"
    assert json.loads(final_out.read_text(encoding="utf-8")) == {
        "received": "kept",
        "status": "ok",
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["expression"].startswith("state.linkedinToolsConfigPath = ")
    staged_config = json.loads(Path(state["config_path"]).read_text(encoding="utf-8"))
    assert staged_config["value"] == "kept"
    assert staged_config["out"] != str(final_out)
    assert staged_config["out"].endswith("-out.json")


def test_playwriter_runner_preserves_existing_session_and_custom_state_key(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    playwriter = _fake_playwriter(tmp_path, state_path=state_path)
    final_out = tmp_path / "message-result.json"
    runner = PlaywriterRunner(
        playwriter_bin=str(playwriter),
        session="existing-session",
        config_state_key="state.recruiterAgencyMessageConfigPath",
    )

    runner.run_script(
        tmp_path / "message.js",
        {"out": str(final_out), "value": "message"},
        output_missing_message="missing message output",
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["sessions"] == ["existing-session", "existing-session"]
    assert state["created_sessions"] == 0
    assert state["expression"].startswith("state.recruiterAgencyMessageConfigPath = ")
    assert json.loads(final_out.read_text(encoding="utf-8"))["received"] == "message"


def test_playwriter_runner_temporary_staging_uses_temp_config(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    playwriter = _fake_playwriter(tmp_path, state_path=state_path)
    final_out = tmp_path / "account-result.json"
    runner = PlaywriterRunner(playwriter_bin=str(playwriter), session="account-session")

    runner.run_script(
        tmp_path / "account.js",
        {"out": str(final_out), "value": "account"},
        output_missing_message="missing account output",
        staging="temporary",
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert Path(state["config_path"]).name == "config.json"
    assert json.loads(final_out.read_text(encoding="utf-8"))["received"] == "account"


def test_playwriter_runner_can_preserve_numeric_session_parsing(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    playwriter = _fake_playwriter(
        tmp_path,
        state_path=state_path,
        session_output="created browser session 17",
    )
    runner = PlaywriterRunner(
        playwriter_bin=str(playwriter),
        allow_numeric_session_id_parse=True,
    )

    assert runner.session == "17"


def test_playwriter_runner_reports_missing_output(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    playwriter = _fake_playwriter(tmp_path, state_path=state_path, write_output=False)
    runner = PlaywriterRunner(
        playwriter_bin=str(playwriter),
        session="test",
        output_wait_seconds=0.01,
    )

    with pytest.raises(RuntimeError, match="script did not write output; expected "):
        runner.run_script(
            tmp_path / "script.js",
            {"out": str(tmp_path / "missing.json")},
            output_missing_message="script did not write output",
        )


def test_format_progress_event_compacts_structured_events() -> None:
    line = json.dumps({"step": "scroll", "count": 3, "items": ["a", "b"], "ok": True})

    assert format_progress_event(line) == 'browser progress: scroll count=3 items=["a","b"] ok=true'
    assert format_progress_event("plain") == "browser progress: plain"


def _fake_playwriter(
    tmp_path: Path,
    *,
    state_path: Path,
    write_output: bool = True,
    session_output: str = "Session 42 created",
) -> Path:
    script = tmp_path / "playwriter"
    script.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

STATE_PATH = Path({str(state_path)!r})
WRITE_OUTPUT = {write_output!r}
SESSION_OUTPUT = {session_output!r}


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {{"created_sessions": 0, "sessions": []}}


def save_state(payload):
    STATE_PATH.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


args = sys.argv[1:]
state = load_state()
if args == ["session", "new"]:
    state["created_sessions"] = state.get("created_sessions", 0) + 1
    save_state(state)
    print(SESSION_OUTPUT)
    raise SystemExit(0)
if "-s" in args:
    state.setdefault("sessions", []).append(args[args.index("-s") + 1])
if "-e" in args:
    expression = args[args.index("-e") + 1]
    state["expression"] = expression
    state["config_path"] = json.loads(expression.split("=", 1)[1].strip())
    save_state(state)
    raise SystemExit(0)
if "-f" in args:
    config = json.loads(Path(state["config_path"]).read_text(encoding="utf-8"))
    if WRITE_OUTPUT:
        Path(config["out"]).write_text(
            json.dumps({{"status": "ok", "received": config.get("value")}}),
            encoding="utf-8",
        )
    save_state(state)
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script
