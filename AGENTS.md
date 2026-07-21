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
  audit reconciliation, welcome messages, and pending-invitation cleanup.
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
- `linkedin-acceptance-daily` owns acceptance outcome checks and imports only;
  `run-daily-session` is report-only and has no drafting mode.
- `linkedin-relationship-radar` owns non-blocking relationship enrichment and
  cumulative radar updates. It reuses original connection-review evidence and
  refreshes public sources only for missing, stale, or explicitly prioritized
  records. Research is review-only. A separate guarded radar command may save
  exact source-backed buyer recommendations to `Watch - Business Systems
  Prospects`; it must not draft or publish comments.
- `linkedin-prospect-investigation` owns the bounded authenticated-browser
  follow-up for radar records that remain `needs_review`. It may inspect only
  the exact queued profile, current-company links, and recent activity; it
  applies a complete queue-bound artifact, observes a 30-day browser cooldown,
  and must not connect, comment, react, message, or send.
- `linkedin-accepted-relationship-followup` owns the exact welcome message for
  every durable accepted connection. It does not depend on enrichment or an
  original review approval, and it must not save people to a list. It dry-runs
  the stored exact welcome and only then sends.
- Relationship enrichment may launch read-only, ephemeral `codex exec` workers.
  Those workers only write local enrichment artifacts; they must not draft a
  message, touch LinkedIn, or send.
- `linkedin-acceptance-weekly` is report-only. It should not open LinkedIn, run Playwriter classification, import outcomes, or draft messages.
- `linkedin-tools recruiter-agency` is separate from network state. It must not
  send connection requests and must not write into the networking controller
  state directory.
- Pending-invitation cleanup must go through
  `linkedin-tools network ... pending-cleanup`. Treat the age threshold as a
  hard safety boundary.

## Live Browser Safety

- Browser operations default to dry-run. Real sends or mutations require
  explicit user intent plus the matching flag: `--allow-send`, `--allow-save`,
  or `--allow-withdraw`.
- Send or withdraw one candidate at a time through the controller. Do not ad
  hoc click LinkedIn buttons outside guarded Python browser paths.
- Poll an active controller process until it exits. Never interrupt or kill it
  while a browser operation is in progress; an interruption during a real-send
  call must be preserved as a possible-send incident and reconciled by audit.
- Daily completion prioritizes 30 durable sends with zero provisional. The
  15/10/5 source mix guides selection across the three approved sources; when
  one is exhausted, its shortfall carries to the next approved source. Proven
  failed or reverted sends release active capacity, and `--no-fallback` still
  excludes every source outside the approved three.
- `NeedsBrowserInspection` is a normal controller checkpoint only when the
  active incident says `possible_send=false`. Codex may use Chrome on the exact
  automation-owned tab for actions listed in the incident lease, then must
  capture before/after evidence and apply a fresh recovery receipt through
  `browser-inspection apply` before resuming the same run.
- Chrome recovery never permits Connect, Send, message, save-to-list, withdraw,
  login, checkpoint, or security-verification actions. A possible-send incident
  remains on the sent-page audit path and must never receive a recovery receipt.
- Use `send-guarded --single-pass` for the normal connection-request path. Use `send-next --dry-run` or `send-guarded --dry-run` for focused validation.
- Record browser artifacts back into the controller with the matching import or record command.
- After uncertainty, exhausted transient-load retries, blocked browser state, or
  possible real sends, audit before declaring success.
- `finish` must be backed by sent-page audit reconciliation, not row-level confidence alone.
- When guarded attempts, active sends, and the sent-page delta all exactly equal
  the target with no failed or reverted request, the complete aggregate audit
  may confirm remaining name-unmatched provisional requests. Do not replace them.
- If Playwriter reports a closed page/context/session, run `playwriter session reset <session>` or reopen the session before retrying.
- Let controller retry budgets handle transient Sales Navigator UI-load misses,
  such as a temporarily missing saved-search control. Treat login, checkpoint,
  security verification, LinkedIn `429`, network refusal, or uncertain send
  results as blocking evidence. Preserve controller state and diagnose the
  cause before retrying.
- Treat a post-send public-profile `connectable` result as inconclusive, not
  proof that the invite failed. Reconcile with the sent-page audit; matching
  `recentNames` can confirm the provisional send as durable pending.

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
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" run-session --daily --session auto --target 30 --max-real-sends 30 --refresh-saved-searches --no-fallback --allow-send --finish --out-dir /tmp/linkedin-network-session
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" send-guarded --session auto --allow-send --single-pass --max-attempts 30
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" reconcile-audit --session auto --attempts 3 --delay-ms 5000 --finish
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" report
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" browser-inspection status
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" browser-inspection apply /tmp/linkedin-network-session/001-browser-incident-recovery-receipt.json
```

Acceptance tracking:

```sh
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance seed-history
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance export --min-age-days 1 --max-age-days 45 --out /tmp/linkedin-acceptance-candidates.json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance import /tmp/linkedin-acceptance-outcomes.json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance report --min-age-days 1 --max-age-days 45
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance prepare-welcome-messages --out /tmp/linkedin-accepted-welcome/eligibility.json --report-out /tmp/linkedin-accepted-welcome/welcome.md --limit 30
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance run-welcome-messages --session auto --limit 30 --allow-send
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance sync-relationship-radar-actions
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance export-enrichment-queue --out /tmp/linkedin-relationship-radar/enrichment-queue.json --stale-after-days 30 --limit 30
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance launch-enrichment-workers --enrichment-queue /tmp/linkedin-relationship-radar/enrichment-queue.json --sources-dir "$HOME/Library/Application Support/linkedin-tools/network-automation/relationship-radar/source-bundles" --jobs-dir /tmp/linkedin-relationship-radar/enrichment-jobs
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance collect-enrichment-workers --enrichment-queue /tmp/linkedin-relationship-radar/enrichment-queue.json --jobs-dir /tmp/linkedin-relationship-radar/enrichment-jobs --out /tmp/linkedin-relationship-radar/enrichment-decisions.json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance update-relationship-radar --enrichment /tmp/linkedin-relationship-radar/enrichment-decisions.json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance export-browser-investigation-queue --out /tmp/linkedin-prospect-investigation/queue.json --cooldown-days 30 --limit 5
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance apply-browser-investigation --queue /tmp/linkedin-prospect-investigation/queue.json --enrichment /tmp/linkedin-prospect-investigation/decisions.json
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance save-watchlist-leads --session auto --limit 30 --allow-save
```

For large acceptance-classification batches, use `acceptance check` with
`offset` / `limit` and incremental chunk artifacts. One-shot large browser runs
are fragile. Relationship enrichment is separate from greeting eligibility.
Every enrichment decision must classify the relationship role, record the
source-backed signal and follow-up reason, name the next useful action, and
retain `permission_boundary=review_only`. Missing evidence must remain empty
with a warning; the saved-search label is context, not proof of role.

Welcome-message low-level controls:

```sh
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance dry-run-greetings --session auto --limit 5
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance send-greeting --id <id> --session auto --preview-fill
uv run linkedin-tools network --state-dir "$HOME/Library/Application Support/linkedin-tools/network-automation" acceptance send-ready-greetings --session auto --limit 5 --allow-send
```

Real welcome sends require the stored exact message, prior `dry_run_ready`
status, and `--allow-send`. `run-welcome-messages` is the normal path; low-level
commands diagnose the same queue. Watchlist saving happens only after research
through `save-watchlist-leads --allow-save`.

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
- Preserve draft formatting for recruiter/agency messages and the exact welcome copy.
- Do not broaden recruiter/agency outreach into connection requests or generic networking.

## Reporting Back

In final responses, include:

- What changed and where.
- What verification ran and whether it passed.
- Any live-browser, automation, or stateful behavior that was not exercised.
- Exact artifact paths when drafts, dashboards, captures, or reports are created.
