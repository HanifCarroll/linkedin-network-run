"""Purpose-built Playwriter runner for read-only LinkedIn analytics downloads.

This module intentionally does not use the shared guarded Playwriter runner.
The analytics export command is read-only and is explicitly exempt from the
networking incident gate and automation preflight. Existing guarded commands
continue to use ``packages.linkedin_browser.playwriter.PlaywriterRunner``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from packages.linkedin_browser.playwriter import resolve_playwriter_bin

ANALYTICS_URL = "https://www.linkedin.com/analytics/creator/content/"
SCRIPT = Path(__file__).with_name("playwriter_scripts") / "export_content_analytics.js"
_SESSION_CREATED = re.compile(r"Session\s+(\S+)\s+created")
_LINKEDIN_EXPORT_NAME = re.compile(
    r"^AggregateAnalytics_.+_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})"
    r"(?: \(\d+\))?\.xlsx$"
)


def export_content_analytics(
    *,
    out: Path,
    session: str | None = None,
    browser_key: str | None = None,
    playwriter_bin: str | None = None,
    command_timeout_seconds: float = 300,
    download_roots: tuple[Path, ...] | None = None,
) -> dict[str, Any]:
    destination = out.expanduser().resolve()
    if destination.suffix.lower() != ".xlsx":
        raise ValueError("LinkedIn content analytics export path must end in .xlsx")

    executable = playwriter_bin or resolve_playwriter_bin()
    active_session = session or _create_session(
        executable,
        browser_key=browser_key,
        timeout_seconds=command_timeout_seconds,
    )
    roots = download_roots or _default_download_roots()
    before = _snapshot_downloads(roots)

    with tempfile.TemporaryDirectory(prefix="linkedin-content-analytics-") as temp_dir:
        temp_root = Path(temp_dir)
        browser_receipt = temp_root / "browser-receipt.json"
        config_path = temp_root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "analyticsUrl": ANALYTICS_URL,
                    "receiptOut": str(browser_receipt),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        _run(
            [
                executable,
                "-s",
                active_session,
                "-e",
                "state.linkedinContentAnalyticsConfigPath = "
                + json.dumps(str(config_path)),
            ],
            timeout_seconds=command_timeout_seconds,
        )
        _run(
            [
                executable,
                "-s",
                active_session,
                "-f",
                str(SCRIPT),
                "--timeout",
                str(int(command_timeout_seconds * 1000)),
            ],
            timeout_seconds=command_timeout_seconds,
        )

        if not browser_receipt.is_file():
            raise RuntimeError(
                f"Playwriter did not write the analytics export receipt: {browser_receipt}"
            )
        browser_state = json.loads(browser_receipt.read_text(encoding="utf-8"))
        if browser_state.get("status") != "confirmation_clicked":
            raise RuntimeError(
                "LinkedIn analytics export did not complete: "
                + json.dumps(browser_state, sort_keys=True)
            )
        downloaded = _wait_for_native_download(
            roots,
            before=before,
            timeout_seconds=min(command_timeout_seconds, 90),
        )
        _validate_xlsx(downloaded)
        digest = f"sha256:{hashlib.sha256(downloaded.read_bytes()).hexdigest()}"
        size_bytes = downloaded.stat().st_size
        name_match = _LINKEDIN_EXPORT_NAME.fullmatch(downloaded.name)
        assert name_match is not None

        destination.parent.mkdir(parents=True, exist_ok=True)
        if downloaded.resolve() != destination:
            shutil.move(str(downloaded), destination)

    return {
        "kind": "linkedin_content_analytics_export",
        "status": "exported",
        "workbook_path": str(destination),
        "workbook_sha256": digest,
        "workbook_size_bytes": size_bytes,
        "analytics_url": ANALYTICS_URL,
        "date_range": {
            "preset": browser_state["dateRange"],
            "start": name_match.group(1),
            "end": name_match.group(2),
        },
        "suggested_filename": downloaded.name,
        "selector_contract": browser_state["selectorContract"],
        "session": active_session,
    }


def _default_download_roots() -> tuple[Path, ...]:
    configured = os.environ.get("LINKEDIN_TOOLS_ANALYTICS_DOWNLOAD_DIR")
    if configured:
        return (Path(configured).expanduser().resolve(),)
    user_home = Path.home()
    return (
        user_home / "Downloads",
        user_home
        / "Library"
        / "Mobile Documents"
        / "com~apple~CloudDocs"
        / "Downloads",
    )


def _snapshot_downloads(roots: tuple[Path, ...]) -> dict[Path, tuple[int, int, int]]:
    snapshot: dict[Path, tuple[int, int, int]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.iterdir():
            if not candidate.is_file() or not _LINKEDIN_EXPORT_NAME.fullmatch(
                candidate.name
            ):
                continue
            stat = candidate.stat()
            snapshot[candidate.resolve()] = (
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
            )
    return snapshot


def _wait_for_native_download(
    roots: tuple[Path, ...],
    *,
    before: dict[Path, tuple[int, int, int]],
    timeout_seconds: float,
) -> Path:
    deadline = time.monotonic() + timeout_seconds
    last_sizes: dict[Path, int] = {}
    while time.monotonic() < deadline:
        after = _snapshot_downloads(roots)
        candidates = [path for path, identity in after.items() if before.get(path) != identity]
        if len(candidates) > 1:
            raise RuntimeError(
                "LinkedIn analytics export produced multiple new workbooks: "
                + ", ".join(str(path) for path in sorted(candidates))
            )
        if len(candidates) == 1:
            candidate = candidates[0]
            size = candidate.stat().st_size
            if size > 0 and last_sizes.get(candidate) == size and zipfile.is_zipfile(
                candidate
            ):
                return candidate
            last_sizes[candidate] = size
        time.sleep(0.25)
    raise RuntimeError(
        "LinkedIn analytics export confirmation succeeded, but exactly one new "
        "AggregateAnalytics workbook did not appear in the configured download roots"
    )


def _create_session(
    executable: str, *, browser_key: str | None, timeout_seconds: float
) -> str:
    command = [executable, "session", "new"]
    selected_browser = browser_key or os.environ.get("LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY")
    if selected_browser:
        command.extend(["--browser", selected_browser])
    result = _run(command, timeout_seconds=timeout_seconds)
    match = _SESSION_CREATED.search(result.stdout)
    if not match:
        raise RuntimeError(f"could not parse Playwriter session id from: {result.stdout}")
    return match.group(1)


def _run(command: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        detail = "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        )
        raise RuntimeError(
            f"Playwriter command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def _validate_xlsx(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Playwriter did not download the analytics workbook: {path}")
    if not zipfile.is_zipfile(path):
        raise RuntimeError("LinkedIn analytics export is not a valid XLSX ZIP container")
    with zipfile.ZipFile(path) as workbook:
        required = {"[Content_Types].xml", "xl/workbook.xml"}
        missing = required - set(workbook.namelist())
    if missing:
        raise RuntimeError(
            "LinkedIn analytics export is missing required XLSX members: "
            + ", ".join(sorted(missing))
        )
