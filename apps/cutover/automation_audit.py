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


@dataclass(frozen=True, slots=True)
class AutomationCommandReplacement:
    old: str
    new: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class AutomationPromptEditPlan:
    automation_id: str
    path: str
    current_status: AutomationStatus
    state_dir: str
    required_new_markers: tuple[str, ...]
    command_replacements: tuple[AutomationCommandReplacement, ...]
    safety_requirements: tuple[str, ...]
    remove_instructions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AutomationEditPlanReport:
    root: str
    plans: tuple[AutomationPromptEditPlan, ...]

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

NETWORK_STATE_DIR = "$HOME/Library/Application Support/linkedin-tools/network-automation"
RECRUITER_STATE_DIR = "$HOME/Library/Application Support/linkedin-tools/recruiter-agency-outreach"
NETWORK_CMD = f'uv run linkedin-tools network --state-dir "{NETWORK_STATE_DIR}"'
RECRUITER_CMD = f'uv run linkedin-tools recruiter-agency --state-dir "{RECRUITER_STATE_DIR}"'

AUTOMATION_EDIT_REPLACEMENTS: dict[str, tuple[AutomationCommandReplacement, ...]] = {
    "linkedin-network": (
        AutomationCommandReplacement(
            old="linkedin-network-run start --target 30 --max-real-sends 30 --force",
            new=f"{NETWORK_CMD} start --target 30 --max-real-sends 30 --force",
        ),
        AutomationCommandReplacement(
            old="salesnav-audit.js plus linkedin-network-run import-audit",
            new=f"{NETWORK_CMD} reconcile-audit --session auto --attempts 1 --delay-ms 0",
            note="Use this for sent-page audit capture/import work.",
        ),
        AutomationCommandReplacement(
            old="salesnav-saved-searches.js",
            new=(
                f"{NETWORK_CMD} saved-searches --session auto "
                "--out /tmp/linkedin-network-run-saved-searches.json"
            ),
        ),
        AutomationCommandReplacement(
            old="salesnav-capture.js plus linkedin-network-run import-capture",
            new=(
                f"{NETWORK_CMD} capture --session auto --source <source> "
                "--url <saved-search-or-resume-url> --pages <pages> "
                "--stop-after-connectable <n> --row-scroll-delay-ms 250 "
                "--only-connectable"
            ),
            note="Keep using plan.resume_url when present.",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run plan --json",
            new=f"{NETWORK_CMD} plan --json",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run status --json",
            new=f"{NETWORK_CMD} status --json",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run report",
            new=f"{NETWORK_CMD} report",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run finish",
            new=f"{NETWORK_CMD} finish",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run send-guarded --session <session> --allow-send --single-pass",
            new=(f"{NETWORK_CMD} send-guarded --session auto --allow-send --single-pass"),
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run top-up-reconcile --session <session> --allow-send --finish",
            new=f"{NETWORK_CMD} top-up-reconcile --session auto --allow-send --finish",
        ),
    ),
    "linkedin-acceptance-daily": (
        AutomationCommandReplacement(
            old="linkedin-network-run acceptance seed-history",
            new=f"{NETWORK_CMD} acceptance seed-history",
        ),
        AutomationCommandReplacement(
            old=(
                "linkedin-network-run acceptance export --min-age-days 1 "
                "--max-age-days 45 --out /tmp/linkedin-acceptance-candidates.json"
            ),
            new=(
                f"{NETWORK_CMD} acceptance export --min-age-days 1 "
                "--max-age-days 45 --out /tmp/linkedin-acceptance-candidates.json"
            ),
        ),
        AutomationCommandReplacement(
            old="scripts/salesnav-acceptance-outcomes.js",
            new=(
                f"{NETWORK_CMD} acceptance check --session auto "
                "--in /tmp/linkedin-acceptance-candidates.json "
                "--out <outcomes-or-chunk-path> "
                "--offset <offset> --limit <limit> --delay-ms 750"
            ),
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run acceptance import /tmp/linkedin-acceptance-outcomes.json",
            new=f"{NETWORK_CMD} acceptance import /tmp/linkedin-acceptance-outcomes.json",
        ),
        AutomationCommandReplacement(
            old="scripts/salesnav-accepted-research.js",
            new=(
                f"{NETWORK_CMD} acceptance research --session auto "
                "--in /tmp/linkedin-accepted-followups/accepted-candidates.json "
                "--out /tmp/linkedin-accepted-followups/research-chunks/chunk-<offset>.json "
                "--offset <offset> --limit <limit> --max-web-results 5 "
                "--delay-ms 500"
            ),
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run acceptance draft-followups --session <session>",
            new=f"{NETWORK_CMD} acceptance draft-followups --session auto",
        ),
        AutomationCommandReplacement(
            old=(
                "linkedin-network-run acceptance draft-followups --research "
                "/tmp/linkedin-accepted-followups/accepted-research.json "
                "--session <session>"
            ),
            new=(
                f"{NETWORK_CMD} acceptance draft-followups "
                "--research /tmp/linkedin-accepted-followups/accepted-research.json "
                "--session auto"
            ),
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run acceptance report --min-age-days 1 --max-age-days 45",
            new=f"{NETWORK_CMD} acceptance report --min-age-days 1 --max-age-days 45",
        ),
    ),
    "linkedin-acceptance-weekly": (
        AutomationCommandReplacement(
            old="linkedin-network-run acceptance seed-history",
            new=f"{NETWORK_CMD} acceptance seed-history",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run acceptance report --min-age-days 7 --max-age-days 45",
            new=f"{NETWORK_CMD} acceptance report --min-age-days 7 --max-age-days 45",
        ),
    ),
    "linkedin-pending-cleanup": (
        AutomationCommandReplacement(
            old=(
                "linkedin-network-run pending-cleanup start --max-withdrawals 75 "
                "--threshold-weeks 2 --force"
            ),
            new=(
                f"{NETWORK_CMD} pending-cleanup start --max-withdrawals 75 "
                "--threshold-weeks 2 --force"
            ),
        ),
        AutomationCommandReplacement(
            old="salesnav-audit.js plus pending-cleanup import-audit",
            new=f"{NETWORK_CMD} pending-cleanup audit --session auto --load-more <n>",
        ),
        AutomationCommandReplacement(
            old="salesnav-pending-capture.js plus pending-cleanup import-capture",
            new=(
                f"{NETWORK_CMD} pending-cleanup capture --session auto "
                "--load-more <n> --threshold-weeks 2"
            ),
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run pending-cleanup plan --json",
            new=f"{NETWORK_CMD} pending-cleanup plan --json",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run pending-cleanup finish",
            new=f"{NETWORK_CMD} pending-cleanup finish",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run pending-cleanup withdraw-next --dry-run",
            new=f"{NETWORK_CMD} pending-cleanup withdraw-next --session auto --dry-run",
        ),
        AutomationCommandReplacement(
            old="linkedin-network-run pending-cleanup withdraw-next --allow-withdraw",
            new=(f"{NETWORK_CMD} pending-cleanup withdraw-next --session auto --allow-withdraw"),
        ),
    ),
    "recruiter-agency-outreach-daily": (
        AutomationCommandReplacement(
            old=(
                "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach "
                "run-daily --session <discovered-session-id> "
                "--target-agencies 5 --target-recruiters 5 "
                "--refresh-saved-searches --print-markdown"
            ),
            new=(
                f"{RECRUITER_CMD} run-daily --session auto --target-agencies 5 "
                "--target-recruiters 5 --refresh-saved-searches --print-markdown"
            ),
        ),
    ),
    "recruiter-agency-sending-daily": (
        AutomationCommandReplacement(
            old=(
                "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach "
                "send-ready --session <discovered-session-id> "
                "--target-agencies 5 --target-recruiters 5 "
                "--allow-send --print-markdown"
            ),
            new=(
                f"{RECRUITER_CMD} send-ready --session auto --target-agencies 5 "
                "--target-recruiters 5 --allow-send --print-markdown"
            ),
        ),
    ),
}

AUTOMATION_EDIT_REMOVE_INSTRUCTIONS: dict[str, tuple[str, ...]] = {
    "linkedin-network": (
        "Remove direct `salesnav-*.js` execution steps.",
        "Remove installed Go binary rebuild instructions for this workflow.",
    ),
    "linkedin-acceptance-daily": (
        "Remove direct `salesnav-acceptance-outcomes.js` execution steps.",
        "Remove direct `salesnav-accepted-research.js` execution steps.",
    ),
    "linkedin-acceptance-weekly": (
        "Keep this job report-only; do not add browser classification or drafting.",
    ),
    "linkedin-pending-cleanup": (
        "Remove direct `salesnav-audit.js` execution steps.",
        "Remove direct `salesnav-pending-capture.js` execution steps.",
    ),
    "recruiter-agency-outreach-daily": (
        "Remove installed Go binary presence checks.",
        "Remove `go build -o "
        "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach` instructions.",
    ),
    "recruiter-agency-sending-daily": (
        "Remove installed Go binary presence checks.",
        "Remove `go build -o "
        "/Users/hanifcarroll/.local/bin/recruiter-agency-outreach` instructions.",
    ),
}

AUTOMATION_EDIT_SAFETY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "linkedin-network": (
        "Real sends still require `--allow-send`.",
        "Do not run concurrent browser send loops.",
        "Stop on checkpoint, challenge, security, login, or weekly-limit blockers.",
        "Finish only after audit-backed reconciliation succeeds.",
    ),
    "linkedin-acceptance-daily": (
        "Do not auto-send follow-up messages.",
        "Do not withdraw or modify invitations.",
        "Stop on checkpoint, challenge, security, login, or weekly-limit blockers.",
    ),
    "linkedin-acceptance-weekly": (
        "Do not open LinkedIn or run browser classification from the weekly report.",
        "Do not import outcomes or draft messages.",
    ),
    "linkedin-pending-cleanup": (
        "Real withdrawals still require `--allow-withdraw`.",
        "Do not run concurrent withdrawal loops.",
        "Keep the two-week age threshold as the hard safety boundary.",
        "Finish only when final audit delta matches withdrawn count.",
    ),
    "recruiter-agency-outreach-daily": (
        "Do not pass `--allow-send`.",
        "Do not send LinkedIn messages, InMail, or connection requests.",
        "Keep the workflow sourcing, drafting, and dry-run validation only.",
    ),
    "recruiter-agency-sending-daily": (
        "Send only already stored `dry_run_ready` messages.",
        "Do not source, refresh saved searches, capture, import, or draft.",
        "Do not send connection requests.",
        "Real messages still require `--allow-send`.",
    ),
}


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


def plan_automation_prompt_edits(*, root: Path | None = None) -> AutomationEditPlanReport:
    automation_root = root or default_automation_root()
    audits_by_id = {
        audit.automation_id: audit
        for audit in audit_automation_prompts(root=automation_root).audits
    }
    plans = []
    for spec in AUTOMATION_SPECS:
        audit = audits_by_id[spec.automation_id]
        state_dir = NETWORK_STATE_DIR if "network" in spec.new_markers[1] else RECRUITER_STATE_DIR
        plans.append(
            AutomationPromptEditPlan(
                automation_id=spec.automation_id,
                path=audit.path,
                current_status=audit.status,
                state_dir=state_dir,
                required_new_markers=spec.new_markers,
                command_replacements=AUTOMATION_EDIT_REPLACEMENTS[spec.automation_id],
                safety_requirements=AUTOMATION_EDIT_SAFETY_REQUIREMENTS[spec.automation_id],
                remove_instructions=AUTOMATION_EDIT_REMOVE_INSTRUCTIONS[spec.automation_id],
            )
        )
    return AutomationEditPlanReport(root=str(automation_root), plans=tuple(plans))


def render_automation_edit_plan(report: AutomationEditPlanReport) -> str:
    lines = [
        "# Codex Automation Cutover Edit Plan",
        "",
        "Read-only plan. Do not edit live automation prompts until Hanif approves cutover.",
        "",
        f"- Root: `{report.root}`",
    ]
    for plan in report.plans:
        lines.extend(
            [
                "",
                f"## `{plan.automation_id}`",
                "",
                f"- Prompt file: `{plan.path}`",
                f"- Current status: `{plan.current_status}`",
                f"- Python state dir: `{plan.state_dir}`",
                "- Required new markers: "
                + ", ".join(f"`{marker}`" for marker in plan.required_new_markers),
                "",
                "| Replace | With | Note |",
                "| --- | --- | --- |",
            ]
        )
        for replacement in plan.command_replacements:
            lines.append(
                f"| `{replacement.old}` | `{replacement.new}` | {replacement.note or '-'} |"
            )
        lines.extend(["", "Safety requirements:"])
        lines.extend(f"- {item}" for item in plan.safety_requirements)
        lines.extend(["", "Remove or preserve:"])
        lines.extend(f"- {item}" for item in plan.remove_instructions)
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
            audit.status == "post_cutover" and not audit.missing_new_markers for audit in audits
        )
    return all(
        audit.status == "pre_cutover"
        or (audit.status == "post_cutover" and not audit.missing_new_markers)
        for audit in audits
    )
