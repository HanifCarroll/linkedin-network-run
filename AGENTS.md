# AGENTS.md

Act like a high-performing senior engineer. Be concise, direct, and execution-focused.

## Operating Standard

- Search before building. Inspect current files and state before changing behavior.
- Make narrow, production-friendly changes that follow the existing Go and Playwriter patterns.
- Protect user work. Do not revert or overwrite uncommitted changes you did not make.
- Test before shipping. For docs-only changes, at least verify syntax and relevant references.
- State blockers, assumptions, changed files, and verification results clearly.

## Project Overview

This is a Go 1.26 module with two command-line tools and Playwriter browser adapters:

- `cmd/linkedin-network-run`: deterministic controller for LinkedIn Sales Navigator connection-request runs, acceptance tracking, reservoir capture, audit reconciliation, and pending-invitation cleanup.
- `cmd/recruiter-agency-outreach`: separate recruiter/agency sourcing, drafting, dashboard, and guarded message workflow.
- `internal/app`: controller state, planning, run mutations, Sales Navigator artifact import, acceptance ledgers, pending cleanup, reports, and Playwriter orchestration.
- `internal/outreach`: recruiter/agency capture classification, account-first agency sourcing, draft generation, dashboard/server, guarded message sends, and SQLite-backed state.
- `scripts/salesnav-*.js`: Playwriter browser adapters and artifact extractors. Treat these as live-browser automation code with explicit safety contracts.

Default local state:

- `linkedin-network-run`: `~/Library/Application Support/linkedin-network-run/`
- `recruiter-agency-outreach`: `~/Library/Application Support/recruiter-agency-outreach/`

The current stable workspace is `/Users/hanifcarroll/projects/linkedin-network-automation`. Older logs or comments may still mention `/Users/hanifcarroll/projects/tool`; do not reintroduce that path.

## Build And Test

Use the smallest relevant test target, then broaden when touching shared behavior:

```sh
go test ./internal/app
go test ./internal/outreach
go test ./...
```

Build local binaries with:

```sh
go build -o linkedin-network-run ./cmd/linkedin-network-run
go build -o recruiter-agency-outreach ./cmd/recruiter-agency-outreach
```

When a change affects installed automation behavior, rebuild the installed binary:

```sh
go build -o /Users/hanifcarroll/.local/bin/linkedin-network-run ./cmd/linkedin-network-run
go build -o /Users/hanifcarroll/.local/bin/recruiter-agency-outreach ./cmd/recruiter-agency-outreach
```

The Playwriter default is `/Users/hanifcarroll/.bun/bin/playwriter`. The `--bunx` flag is a compatibility alias and should not become the primary path again.

## Workflow Boundaries

- `linkedin-network-run` is the source of truth for connection-request runs. Let `linkedin-network-run plan --json` drive the next action.
- `linkedin-network` automation sends and reconciles new connection requests only.
- `linkedin-acceptance-daily` owns acceptance outcome checks, imports, draft follow-ups, and guarded accepted-follow-up sends.
- `linkedin-acceptance-weekly` is report-only. It should not open LinkedIn, run Playwriter classification, import outcomes, or draft messages.
- `recruiter-agency-outreach` is separate from `linkedin-network-run` state. It must not send connection requests and must not write into the networking controller state directory.
- Pending-invitation cleanup must go through `linkedin-network-run pending-cleanup`. Treat the age threshold as a hard safety boundary.

## Live Browser Safety

- Browser operations default to dry-run. Real sends require explicit user intent plus the matching flag: `--allow-send` or `--allow-withdraw`.
- Send or withdraw one candidate at a time through the controller. Do not ad hoc click LinkedIn buttons outside the guarded scripts.
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
- Remove stale compatibility paths, hidden fallbacks, and unused heuristic implementations when replacing behavior.
- Recovery layers should fail loudly when contracts are violated. Do not salvage malformed JSON or silently coerce invalid output unless that behavior is explicitly part of the contract.

Before finishing extraction or context-selection changes, run and address the results:

```sh
rg -n "slice\(|substring\(|substr\(|visibleText|innerText|document\.title|legacy|fallback|infer|keyword|score|\[class\*=|h1|h2|h3|article|raw_text"
```

## Common Flows

Networking controller:

```sh
linkedin-network-run status --json
linkedin-network-run plan --json
linkedin-network-run send-guarded --session <session> --allow-send --single-pass --max-attempts 30
linkedin-network-run reconcile-audit --session <session> --attempts 3 --delay-ms 5000 --finish
linkedin-network-run report
```

Acceptance tracking:

```sh
linkedin-network-run acceptance seed-history
linkedin-network-run acceptance export --min-age-days 1 --max-age-days 45 --out /tmp/linkedin-acceptance-candidates.json
linkedin-network-run acceptance import /tmp/linkedin-acceptance-outcomes.json
linkedin-network-run acceptance report --min-age-days 1 --max-age-days 45
linkedin-network-run acceptance draft-followups --session <session>
```

For large acceptance classification or accepted-research batches, use the scripts' `offset` and `limit` support with incremental chunk artifacts. One-shot large Playwriter runs are fragile.

Accepted follow-ups:

```sh
linkedin-network-run acceptance dry-run-followups --session <session> --limit 5
linkedin-network-run acceptance send-followup --id <id> --session <session> --preview-fill
linkedin-network-run acceptance send-ready-followups --session <session> --limit 5 --allow-send
```

Real accepted-follow-up sends require a stored draft, prior `dry_run_ready` status for batch sends, and `--allow-send`.

Recruiter/agency outreach:

```sh
recruiter-agency-outreach run-daily --session <session> --target-agencies 5 --target-recruiters 5 --print-markdown
recruiter-agency-outreach dashboard --print-markdown
recruiter-agency-outreach send-message --lead-id <id> --session <session>
recruiter-agency-outreach send-message --lead-id <id> --session <session> --allow-send
```

This flow sends already-drafted LinkedIn messages only. It must never click `Connect`.

Pending cleanup:

```sh
linkedin-network-run pending-cleanup start --max-withdrawals 75 --threshold-weeks 2
linkedin-network-run pending-cleanup plan --json
linkedin-network-run pending-cleanup withdraw-next --session <session> --dry-run
linkedin-network-run pending-cleanup withdraw-next --session <session> --allow-withdraw
linkedin-network-run pending-cleanup finish
```

Re-audit before finishing. `pending-cleanup finish` should only pass when the sent-page delta matches `-withdrawn_count`.

## Code Change Guidance

- Keep CLI entrypoints thin. Put command wiring in `internal/app/cli.go` or `internal/outreach/cli.go`; put behavior in the corresponding package modules.
- Add behavior tests in `internal/app/app_test.go` or `internal/outreach/outreach_test.go`.
- When browser artifact schemas change, update the Go parser/import tests and the README contract.
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
