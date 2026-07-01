# AGENTS.md

Act like a high-performing senior engineer. Be concise, direct, and execution-focused.

## Operating Standard

- Search before building. Inspect current files and state before changing behavior.
- Make narrow, production-friendly changes that follow the existing Python,
  Playwriter, SQLite, and CLI patterns.
- Protect user work. Do not revert or overwrite uncommitted changes you did not make.
- Test before shipping. For docs-only changes, at least verify syntax and relevant references.
- State blockers, assumptions, changed files, and verification results clearly.

## Project Overview

This is the active Python `linkedin-tools` monorepo:

- `apps/network_automation`: deterministic controller for LinkedIn Sales
  Navigator connection-request runs, acceptance tracking, reservoir capture,
  audit reconciliation, accepted follow-ups, and pending-invitation cleanup.
- `apps/recruiter_agency_outreach`: separate recruiter/agency sourcing,
  drafting, dashboard, guarded message dry-runs, and guarded sends.
- `apps/opportunity_intel` and `apps/comment_extractor`: recommend-only
  opportunity/comment discovery and source experiments.
- `apps/review_ui`: local review UI for opportunities, networking,
  recruiter/agency state, browser artifacts, and guarded action paths.
- `packages/`: shared browser, Sales Navigator, storage, report, UI, schema,
  and experiment helpers.

Default local state:

- State root: `~/Library/Application Support/linkedin-tools/`
- Network state: `~/Library/Application Support/linkedin-tools/network-automation/`
- Recruiter/agency state: `~/Library/Application Support/linkedin-tools/recruiter-agency-outreach/`

The current stable workspace is `/Users/hanifcarroll/projects/linkedin-tools`. Older logs or comments may still mention obsolete workspace paths; do not reintroduce them.

## Build And Test

Use the smallest relevant test target, then broaden when touching shared behavior:

```sh
uv run pytest tests/network_automation/test_network_automation.py -q
uv run pytest tests/test_recruiter_agency_outreach.py -q
uv run pytest -q
uv run ruff check .
uv run mypy apps packages tests
```

The main CLI is:

```sh
uv run linkedin-tools --help
```

## Workflow Boundaries

- `linkedin-tools network` is the source of truth for connection-request runs.
  Let `linkedin-tools network ... plan --json` drive the next action.
- `linkedin-network` automation sends and reconciles new connection requests only.
- `linkedin-acceptance-daily` owns acceptance outcome checks, imports, draft follow-ups, and guarded accepted-follow-up sends.
- `linkedin-acceptance-weekly` is report-only. It should not open LinkedIn, run Playwriter classification, import outcomes, or draft messages.
- `linkedin-tools recruiter-agency` is separate from network state. It must not
  send connection requests and must not write into the networking controller
  state directory.
- Pending-invitation cleanup must go through
  `linkedin-tools network ... pending-cleanup`. Treat the age threshold as a
  hard safety boundary.

## Live Browser Safety

- Browser operations default to dry-run. Real sends require explicit user intent plus the matching flag: `--allow-send` or `--allow-withdraw`.
- Send or withdraw one candidate at a time through the controller. Do not ad
  hoc click LinkedIn buttons outside guarded Python browser paths.
- Use `send-guarded --single-pass` for the normal connection-request path. Use `send-next --dry-run` or `send-guarded --dry-run` for focused validation.
- Record browser artifacts back into the controller with the matching import or record command.
- After uncertainty, blocked browser state, or possible real sends, audit before declaring success.
- `finish` must be backed by sent-page audit reconciliation, not row-level confidence alone.
- If Playwriter reports a closed page/context/session, run `playwriter session reset <session>` or reopen the session before retrying.
- Treat LinkedIn `429`, network refusal, or uncertain send results as blocking evidence. Preserve controller state and diagnose the cause before retrying.

## Source-Faithful Extraction

For capture, scraping, parsing, prompt context, and model-selection changes:

- Prefer source-of-truth inputs: structured APIs, JSON artifacts, declared schemas, exact Sales Navigator selectors, or explicit user-provided fields.
- Do not infer from generic DOM text, page titles, broad substring matches, keyword scoring, or heading fallbacks.
- If required data is missing, write an empty field plus a clear warning instead of guessing.
- Do not truncate, slice, cap, or filter extracted source data unless the product requirement explicitly says so. Do prompt-size control later in a named context-selection step.
- Remove hidden fallbacks and unused heuristic implementations when replacing behavior.
- Recovery layers should fail loudly when contracts are violated. Do not salvage malformed JSON or silently coerce invalid output unless that behavior is explicitly part of the contract.

Before finishing extraction or context-selection changes, run and address the results:

```sh
rg -n "slice\(|substring\(|substr\(|visibleText|innerText|document\.title|legacy|fallback|infer|keyword|score|\[class\*=|h1|h2|h3|article|raw_text"
```

## Common Flows

Networking controller:

```sh
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" status --json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" plan --json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" send-guarded --session auto --allow-send --single-pass --max-attempts 30
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" reconcile-audit --session auto --attempts 3 --delay-ms 5000 --finish
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" report
```

Acceptance tracking:

```sh
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance seed-history
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance export --min-age-days 1 --max-age-days 45 --out /tmp/linkedin-acceptance-candidates.json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance import /tmp/linkedin-acceptance-outcomes.json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance report --min-age-days 1 --max-age-days 45
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance draft-followups --session auto
```

For large acceptance classification or accepted-research batches, use the
Python `acceptance check` and `acceptance research` `offset` / `limit` support
with incremental chunk artifacts. One-shot large browser runs are fragile.

Accepted follow-ups:

```sh
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance dry-run-followups --session auto --limit 5
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance send-followup --id <id> --session auto --preview-fill
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance send-ready-followups --session auto --limit 5 --allow-send
```

Real accepted-follow-up sends require a stored draft, prior `dry_run_ready` status for batch sends, and `--allow-send`.

Recruiter/agency outreach:

```sh
uv run linkedin-tools recruiter-agency --state-dir "$HOME/Library/Application Support/linkedin-tools/recruiter-agency-outreach" run-daily --session auto --target-agencies 5 --target-recruiters 5 --print-markdown
uv run linkedin-tools recruiter-agency --state-dir "$HOME/Library/Application Support/linkedin-tools/recruiter-agency-outreach" dashboard --print-markdown
uv run linkedin-tools recruiter-agency --state-dir "$HOME/Library/Application Support/linkedin-tools/recruiter-agency-outreach" send-message --lead-id <id> --session auto
uv run linkedin-tools recruiter-agency --state-dir "$HOME/Library/Application Support/linkedin-tools/recruiter-agency-outreach" send-message --lead-id <id> --session auto --allow-send
```

This flow sends already-drafted LinkedIn messages only. It must never click `Connect`.

Pending cleanup:

```sh
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" pending-cleanup start --max-withdrawals 75 --threshold-weeks 2
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" pending-cleanup plan --json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" pending-cleanup withdraw-next --session auto --dry-run --withdraw-timeout-seconds 90
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" pending-cleanup withdraw-next --session auto --allow-withdraw --withdraw-timeout-seconds 90
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" pending-cleanup finish
```

Re-audit before finishing. `pending-cleanup finish` should only pass when the sent-page delta matches `-withdrawn_count`.

## Code Change Guidance

- Keep CLI entrypoints thin. Put command wiring in the relevant `apps/*/cli.py`
  module; put behavior in the corresponding application or package module.
- Add focused behavior tests under `tests/`, especially
  `tests/network_automation/test_network_automation.py` and
  `tests/test_recruiter_agency_outreach.py` for workflow changes.
- When browser artifact schemas change, update the Python parser/import tests
  and the README contract.
- Use structured JSON parsing and explicit status transitions. Avoid hidden string heuristics.
- Keep real-send and real-withdraw safety gates close to the code that performs the browser action.
- Preserve draft formatting and line breaks for recruiter/agency and accepted-follow-up messages.
- Do not broaden recruiter/agency outreach into connection requests or generic networking.

## Reporting Back

In final responses, include:

- What changed and where.
- What verification ran and whether it passed.
- Any live-browser, automation, or stateful behavior that was not exercised.
- Exact artifact paths when drafts, dashboards, captures, or reports are created.
