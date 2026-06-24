"""Read-only audit for local Codex automation prompt cutover state."""

from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

AutomationStatus = Literal["pre_cutover", "post_cutover", "mixed", "missing", "unknown"]
Expectation = Literal["any", "pre-cutover", "post-cutover"]


@dataclass(frozen=True, slots=True)
class AutomationPromptSpec:
    automation_id: str
    relative_path: str
    old_markers: tuple[str, ...]
    new_markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AutomationPromptAudit:
    automation_id: str
    path: str
    status: AutomationStatus
    old_markers_found: tuple[str, ...] = ()
    new_markers_found: tuple[str, ...] = ()
    missing_new_markers: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True, slots=True)
class AutomationAuditReport:
    root: str
    expectation: Expectation
    passed: bool
    audits: tuple[AutomationPromptAudit, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


AUTOMATION_SPECS: tuple[AutomationPromptSpec, ...] = (
    AutomationPromptSpec(
        automation_id="linkedin-network",
        relative_path="linkedin-network/automation.toml",
        old_markers=(
            "linkedin-network-run",
            "scripts/salesnav-audit.js",
            "scripts/salesnav-saved-searches.js",
            "scripts/salesnav-capture.js",
        ),
        new_markers=(
            "uv run linkedin-tools network",
            "Application Support/linkedin-tools/network-automation",
        ),
    ),
    AutomationPromptSpec(
        automation_id="linkedin-acceptance-daily",
        relative_path="linkedin-acceptance-daily/automation.toml",
        old_markers=(
            "linkedin-network-run acceptance",
            "scripts/salesnav-acceptance-outcomes.js",
            "scripts/salesnav-accepted-research.js",
        ),
        new_markers=(
            "uv run linkedin-tools network",
            "Application Support/linkedin-tools/network-automation",
        ),
    ),
    AutomationPromptSpec(
        automation_id="linkedin-acceptance-weekly",
        relative_path="linkedin-acceptance-weekly/automation.toml",
        old_markers=("linkedin-network-run acceptance",),
        new_markers=(
            "uv run linkedin-tools network",
            "Application Support/linkedin-tools/network-automation",
        ),
    ),
    AutomationPromptSpec(
        automation_id="linkedin-pending-cleanup",
        relative_path="linkedin-pending-cleanup/automation.toml",
        old_markers=(
            "linkedin-network-run pending-cleanup",
            "salesnav-audit.js",
            "salesnav-pending-capture.js",
        ),
        new_markers=(
            "uv run linkedin-tools network",
            "Application Support/linkedin-tools/network-automation",
        ),
    ),
    AutomationPromptSpec(
        automation_id="recruiter-agency-outreach-daily",
        relative_path="recruiter-agency-outreach-daily/automation.toml",
        old_markers=(
            "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach run-daily",
            "go build -o /Users/hanifcarroll/.local/bin/recruiter-agency-outreach",
        ),
        new_markers=(
            "uv run linkedin-tools recruiter-agency",
            "Application Support/linkedin-tools/recruiter-agency-outreach",
        ),
    ),
    AutomationPromptSpec(
        automation_id="recruiter-agency-sending-daily",
        relative_path="recruiter-agency-sending-daily/automation.toml",
        old_markers=(
            "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach send-ready",
            "go build -o /Users/hanifcarroll/.local/bin/recruiter-agency-outreach",
        ),
        new_markers=(
            "uv run linkedin-tools recruiter-agency",
            "Application Support/linkedin-tools/recruiter-agency-outreach",
        ),
    ),
)


def default_automation_root() -> Path:
    return Path.home() / ".codex" / "automations"


def audit_automation_prompts(
    *,
    root: Path | None = None,
    expectation: Expectation = "any",
) -> AutomationAuditReport:
    automation_root = root or default_automation_root()
    audits = tuple(_audit_one(automation_root, spec) for spec in AUTOMATION_SPECS)
    passed = _passes_expectation(audits, expectation)
    return AutomationAuditReport(
        root=str(automation_root),
        expectation=expectation,
        passed=passed,
        audits=audits,
    )


def render_automation_audit(report: AutomationAuditReport) -> str:
    lines = [
        "# Codex Automation Cutover Audit",
        "",
        f"- Root: `{report.root}`",
        f"- Expectation: `{report.expectation}`",
        f"- Passed: `{str(report.passed).lower()}`",
        "",
        "| Automation | Status | Old Markers | New Markers | Missing New Markers |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for audit in report.audits:
        missing = ", ".join(f"`{item}`" for item in audit.missing_new_markers) or "-"
        lines.append(
            "| "
            f"`{audit.automation_id}` | "
            f"`{audit.status}` | "
            f"{len(audit.old_markers_found)} | "
            f"{len(audit.new_markers_found)} | "
            f"{missing} |"
        )
        if audit.error:
            lines.append(f"| `{audit.automation_id}` | error | 0 | 0 | {audit.error} |")
    return "\n".join(lines)


def _audit_one(root: Path, spec: AutomationPromptSpec) -> AutomationPromptAudit:
    path = root / spec.relative_path
    if not path.exists():
        return AutomationPromptAudit(
            automation_id=spec.automation_id,
            path=str(path),
            status="missing",
            missing_new_markers=spec.new_markers,
        )
    try:
        data = tomllib.loads(path.read_text())
    except Exception as exc:
        return AutomationPromptAudit(
            automation_id=spec.automation_id,
            path=str(path),
            status="unknown",
            missing_new_markers=spec.new_markers,
            error=str(exc),
        )
    prompt = str(data.get("prompt") or "")
    old_found = tuple(marker for marker in spec.old_markers if marker in prompt)
    new_found = tuple(marker for marker in spec.new_markers if marker in prompt)
    missing_new = tuple(marker for marker in spec.new_markers if marker not in prompt)
    status = _status_for_markers(old_found, new_found)
    return AutomationPromptAudit(
        automation_id=spec.automation_id,
        path=str(path),
        status=status,
        old_markers_found=old_found,
        new_markers_found=new_found,
        missing_new_markers=missing_new,
    )


def _status_for_markers(
    old_found: tuple[str, ...],
    new_found: tuple[str, ...],
) -> AutomationStatus:
    if old_found and new_found:
        return "mixed"
    if new_found:
        return "post_cutover"
    if old_found:
        return "pre_cutover"
    return "unknown"


def _passes_expectation(
    audits: tuple[AutomationPromptAudit, ...],
    expectation: Expectation,
) -> bool:
    if expectation == "pre-cutover":
        return all(audit.status == "pre_cutover" for audit in audits)
    if expectation == "post-cutover":
        return all(
            audit.status == "post_cutover" and not audit.missing_new_markers
            for audit in audits
        )
    return all(
        audit.status == "pre_cutover"
        or (audit.status == "post_cutover" and not audit.missing_new_markers)
        for audit in audits
    )
