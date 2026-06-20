# linkedin-network-run

Durable run controller for LinkedIn Sales Navigator networking runs.

This tool owns the deterministic run state while Playwriter acts against the live
LinkedIn UI one verified action at a time. A human operator can still inspect or
repair state, but normal automation should keep audit, capture, send, and
reconciliation on the Playwriter-backed controller rail.

Playwriter-backed controller commands default to the installed executable at
`/Users/hanifcarroll/.bun/bin/playwriter`. To fall back to Bun resolution, pass
`--playwriter /Users/hanifcarroll/.bun/bin/bunx`; the controller detects `bunx`
and invokes it as `bunx playwriter@latest`. The older `--bunx` flag remains a
compatibility alias.

## Build And Install

```sh
go test ./...
go build -o linkedin-network-run ./cmd/linkedin-network-run
go install ./cmd/linkedin-network-run
```

For the local automation path that previously used the installed controller
from `~/.cargo/bin`, build the Go binary directly to that path when replacing
the local executable:

```sh
go build -o /Users/hanifcarroll/.cargo/bin/linkedin-network-run ./cmd/linkedin-network-run
```

## Architecture

The Go controller is split by responsibility:

| Module | Responsibility |
| --- | --- |
| `cmd/linkedin-network-run/main.go` | CLI entrypoint only. |
| `internal/app/cli.go` | Cobra command/flag definitions. |
| `internal/app/types.go` | Durable state types, source planning, quotas, acceptance ledger, and pending-cleanup run models. |
| `internal/app/commands.go` | High-level command handlers. |
| `internal/app/run_ops.go` | Core run mutations such as audits, send-result recording, acceptance import, and stale-candidate draining. |
| `internal/app/browser_ops.go` | Playwriter-backed send, capture, reconcile, top-up, and accepted-research orchestration. |
| `internal/app/playwriter.go` | Low-level Playwriter process invocation helpers. |
| `internal/app/salesnav.go` | Sales Navigator artifact parsing, candidate import, reservoir import/fill, and source-yield calculations. |
| `internal/app/pending.go` | Pending-invitation cleanup artifact parsing and withdrawal bookkeeping. |
| `internal/app/accepted_drafts.go` | Accepted-connection follow-up draft strategy, idempotence ledger, and Markdown rendering. |
| `internal/app/reports.go` | Human-readable run reports and status printers. |
| `internal/app/store.go` | Filesystem persistence and JSONL event logs. |
| `internal/app/util.go` | Shared formatting and normalization helpers. |
| `internal/app/app_test.go` | Behavioral parity tests for run planning, capture import, acceptance, reservoir, pending cleanup, and follow-up drafts. |

## Core Flow

```sh
go run ./cmd/linkedin-network-run -- start --target 30 --max-real-sends 30
go run ./cmd/linkedin-network-run -- audit 913
go run ./cmd/linkedin-network-run -- plan --json
go run ./cmd/linkedin-network-run -- record \
  --source "ASAP - Agency Owners Delivery" \
  --name "Example Lead" \
  --status pending
go run ./cmd/linkedin-network-run -- needs-reaudit --reason "Agent Browser command hung after row action"
go run ./cmd/linkedin-network-run -- audit 914
go run ./cmd/linkedin-network-run -- report
go run ./cmd/linkedin-network-run -- finish
```

The default 30-request source mix is weighted for the current ASAP
contractor/freelancer goal:

| Priority | Saved search | Target |
| --- | --- | ---: |
| 1 | `ASAP - Agency Owners Delivery` | 9 |
| 2 | `ASAP - Contract Recruiters Staffing` | 7 |
| 3 | `ASAP - Startup CTO Eng Leaders` | 6 |
| 4 | `ASAP - High-Intent SaaS AI Founders` | 5 |
| 5 | `ASAP - Vertical Proof Buyers` | 3 |

This mix prioritizes people most likely to convert quickly into contract or
freelance work: agencies with overflow delivery demand, contract recruiters,
startup engineering leaders, high-intent SaaS/AI founders, and vertical buyers
that map to existing proof from Palabruno, Genrupt, Casamo, and BA Eventos.

`FO - Founders - Urgent` remains fallback-only for audit top-up continuity; it
is not part of the primary ASAP source mix.

## Sales Navigator Capture Flow

Use Playwriter to capture a normalized Sales Navigator result page, then import
the static artifact into the run controller:

```sh
playwriter -s <session> -e 'state.salesNavCaptureConfig = {
  out: "/tmp/salesnav-capture",
  source: "ASAP - Agency Owners Delivery",
  url: "https://www.linkedin.com/sales/search/people?savedSearchId=...",
  limit: 25,
  pages: 3,
  stopAfterConnectable: 10,
  rowScrollDelayMs: 250,
  openMenus: true,
  onlyConnectable: true,
  saveHtml: true
}'

playwriter -s <session> --timeout <capture.playwriter_timeout_ms> \
  -f /Users/hanifcarroll/projects/tool/scripts/salesnav-capture.js

linkedin-network-run import-capture /tmp/salesnav-capture/page.json --only-connectable
linkedin-network-run candidates --status connectable
linkedin-network-run next-candidate
```

When `linkedin-network-run plan --json` returns `capture-source`, use the
planner's `capture.pages`, `capture.stop_after_connectable`, and
`capture.playwriter_timeout_ms` values exactly. The planner returns `90000` for
deeper captures, resume-cursor captures, and expanded high-email/low-yield
captures. It returns `45000` for ordinary short captures.

The capture script records the final `resumeUrl`, page URLs, visible row
metadata, profile URLs, Sales Navigator profile URNs, optional per-row HTML
snapshots, and API/menu state labels that expose `Connect` vs
`Connect — Pending`.

By default, capture listens for the rendered `salesApiLeadSearch` response and
uses each result's `pendingInvitation` field to classify rows without opening
menus. DOM row `data-scroll-into-view` URNs match the API `entityUrn`, so the
script can map API state back to visible rows. If API state is missing, the
existing `openMenus: true` overflow-menu path remains the read-only fallback.
The send adapter still verifies `Connect` on the lead page before any real send.

`pages` clicks the Sales Navigator `Next` button between result pages.
`stopAfterConnectable` stops inside the current page as soon as enough
connectable rows are captured, so the script does not keep opening menus after
the source has enough candidates. The artifact includes `stopReason` when this
early exit fires.
`onlyConnectable` keeps the output artifact focused on sendable rows while
preserving raw state counts in the artifact.
Both are read-only; the script scrolls virtualized rows into view, then opens
row overflow menus only for rows that API state could not classify. Pass
`apiState: false` only when debugging the older menu-only path.

`import-capture` stores one source cursor per saved search. When `plan --json`
returns a `capture-source` action with `resume_url`, use that URL as the next
capture `url` instead of reopening the saved search at page 1. This lets repeated
runs resume from the last captured Sales Navigator page after saturated ranges.

## Candidate Reservoir

Read-only capture can be run outside the daily send window and stored in a
durable reservoir:

```sh
linkedin-network-run reservoir capture \
  --session <session> \
  --source "FO - Founders - Urgent" \
  --saved-searches /tmp/linkedin-network-run-saved-searches.json \
  --pages 5 \
  --stop-after-connectable 10 \
  --only-connectable

linkedin-network-run reservoir import-capture /tmp/salesnav-capture/page.json --only-connectable
linkedin-network-run reservoir report
```

When `plan --json` returns `use-reservoir`, fill the active run from that queue
instead of opening Sales Navigator for a fresh capture:

```sh
linkedin-network-run reservoir fill-run --source "ASAP - Agency Owners Delivery"
linkedin-network-run plan --json
```

`reservoir fill-run` consumes distinct connectable candidates from
`candidate-reservoir.json` and imports them into the active run as normal
observations. Sending still uses the same guarded controller path.
During final audit reconciliation, `top-up-reconcile` can also draw distinct
fallback candidates from the reservoir before opening Sales Navigator again.

## Saved Search Resolver

Resolve Sales Navigator saved searches into stable URLs before capture:

```sh
playwriter -s <session> -e 'state.salesNavSavedSearchConfig = {
  out: "/tmp/salesnav-saved-searches.json"
}'

playwriter -s <session> --timeout 45000 \
  -f /Users/hanifcarroll/projects/tool/scripts/salesnav-saved-searches.js
```

The artifact includes `savedSearchId`, display name, `viewUrl`, and `freshUrl`
when LinkedIn exposes a "new results since" link.

## One-Candidate Send Flow

The sender is intentionally one candidate at a time. It defaults to dry-run and
requires `allowSend: true` before it will click `Send Invitation`.

```sh
linkedin-network-run next-candidate --json > /tmp/next-candidate.json

playwriter -s <session> -e 'state.salesNavSendConfig = {
  out: "/tmp/salesnav-send-result.json",
  dryRun: true,
  candidate: JSON.parse(require("node:fs").readFileSync("/tmp/next-candidate.json", "utf8"))
}'

playwriter -s <session> --timeout 45000 -f /Users/hanifcarroll/projects/tool/scripts/salesnav-send-one.js
```

For a real send, change the config to:

```js
dryRun: false,
allowSend: true
```

Then import the result:

```sh
linkedin-network-run record-send-result /tmp/salesnav-send-result.json
linkedin-network-run audit <latest-people-count>
```

The script verifies the lead-page overflow menu before sending, clicks the exact
visible `Send Invitation` button, then reopens the lead-page menu and only
reports `pending-verified` when it sees `Connect — Pending`.

The controller can run that sequence end to end with a deterministic candidate
selection wrapper:

```sh
linkedin-network-run send-next \
  --session <session> \
  --dry-run \
  --out-dir /tmp/linkedin-network-run-send-next
```

Real sends require `--allow-send` and are blocked when `--max-real-sends` has
already been reached for the run.

For the normal automation path, prefer the guarded source sender in single-pass
mode. The Playwriter sender validates `Connect` on the lead page before clicking,
then verifies `Connect — Pending` after sending, so the controller avoids the
older dry-run plus real-send double navigation for every candidate. It performs
at most one real send at a time, re-checks the controller source quota before the
next send, automatically skips stale queued candidates from filled sources, and
stops in `NEEDS_REAUDIT` on uncertain browser outcomes:

```sh
linkedin-network-run send-guarded \
  --session <session> \
  --allow-send \
  --single-pass \
  --max-attempts 30 \
  --out-dir /tmp/linkedin-network-run-send-guarded
```

Use `send-guarded --dry-run` or `send-next --dry-run` only for browser-state
validation or one-candidate debugging.

## Sent-Page Audit Flow

Capture the authoritative LinkedIn sent-invitations count:

```sh
playwriter -s <session> -e 'state.salesNavAuditConfig = {
  out: "/tmp/linkedin-network-run-audit.json",
  loadMore: 0
}'

playwriter -s <session> --timeout 45000 \
  -f /Users/hanifcarroll/projects/tool/scripts/salesnav-audit.js

linkedin-network-run import-audit /tmp/linkedin-network-run-audit.json
```

The audit script opens `https://www.linkedin.com/mynetwork/invitation-manager/sent/`,
parses `People (N)`, and stores recent sent names for reconciliation.

Before direct top-ups, let the controller retry final audits with a short delay:

```sh
linkedin-network-run reconcile-audit \
  --session <session> \
  --attempts 3 \
  --delay-ms 5000 \
  --finish
```

If an adapter-only top-up is still required after the controller cap is reached,
let the controller own the one-candidate send plus immediate audit loop. It
defaults to 20 attempts and stops as soon as the audited sent-page delta reaches
the target:

```sh
linkedin-network-run top-up-reconcile \
  --session <session> \
  --allow-send \
  --finish
```

If no distinct top-up candidate is already queued, `top-up-reconcile` first
tries the fallback reservoir, then captures `FO - Founders - Urgent` from the
saved-search artifact and continues the same one-candidate send plus immediate
audit loop. Successful top-ups are recorded as `audit-top-up`, so row-level
source counts do not inflate. To import a manually run top-up adapter result,
use:

```sh
linkedin-network-run record-top-up-result /tmp/linkedin-network-run-topup-send-result.json \
  --note "audit reconciliation"
```

## Timing and Source Yield

`report` includes recorded phase timing from controller-owned imports, guarded
sends, reconcile audits, and top-ups. It also summarizes source yield from
capture cursors and send outcomes.

Use source tuning as an explicit decision point before spending more time on a
thin pool:

```sh
linkedin-network-run tune-sources
linkedin-network-run tune-sources --apply
```

Without `--apply`, the command only reports low-yield sources. With `--apply`,
it marks sources that meet the configured low-yield threshold as exhausted so
their shortfall carries forward.

## Acceptance Tracking Flow

`finish` automatically seeds a durable acceptance ledger with every invitation
that was actually sent by the run: normal `pending` sends plus adapter-only
`audit-top-up` sends. The ledger is stored beside controller state as
`acceptance-ledger.json` and is separate from daily send completion.

Backfill the ledger from historical controller JSONL run logs before checking
older outcomes:

```sh
linkedin-network-run acceptance seed-history
```

Export older invites for a later outcome check:

```sh
linkedin-network-run acceptance export \
  --min-age-days 7 \
  --max-age-days 21 \
  --out /tmp/linkedin-acceptance-candidates.json
```

Classify those exported candidates with Playwriter:

```sh
playwriter -s <session> -e 'state.salesNavAcceptanceConfig = {
  in: "/tmp/linkedin-acceptance-candidates.json",
  out: "/tmp/linkedin-acceptance-outcomes.json",
  limit: 50,
  delayMs: 500
}'

playwriter -s <session> --timeout 45000 \
  -f /Users/hanifcarroll/projects/tool/scripts/salesnav-acceptance-outcomes.js
```

Import the browser outcome artifact and report acceptance by source:

```sh
linkedin-network-run acceptance import /tmp/linkedin-acceptance-outcomes.json
linkedin-network-run acceptance report --min-age-days 7 --max-age-days 21
```

Generate draft-only first messages for newly accepted connections:

```sh
linkedin-network-run acceptance draft-followups \
  --session <session> \
  --out "/Users/hanifcarroll/Library/Application Support/linkedin-network-run/acceptance-followups/$(date +%F).md"
```

This command exports every accepted ledger entry that has not already received a
draft, researches each person through Sales Navigator plus public web search,
writes a Markdown report, and records the drafted people in
`acceptance-followups.json`. It never sends LinkedIn messages. Re-run with
`--include-drafted` only when intentionally regenerating drafts. Pass
`--research <artifact.json>` to render from an existing research artifact without
opening the browser. Adjust `--max-web-results` if the public web evidence needs
to be broader or narrower without changing the draft strategy.

Outcome statuses are:

- `accepted`: the Sales Navigator lead page shows a `1st` relationship.
- `pending`: the lead overflow menu still shows `Connect - Pending`.
- `connectable`: the lead is connectable again, so it is not accepted and no
  longer visibly pending.
- `unknown`, `blocked`, or `failed`: the browser could not safely classify the
  outcome.

Use this report before raising the daily send target. Sent-page audits prove
throughput into pending; acceptance tracking measures whether those sends become
connections.

State defaults to:

```text
~/Library/Application Support/linkedin-network-run/
```

Use `--state-dir <dir>` for dry runs, tests, or isolated experiments.

## Contract

- `start` creates the run with the current 30-request weighted source mix.
- `start --max-real-sends N` sets a hard cap enforced by `send-next` and
  `send-guarded`.
- `audit` records the Sales Navigator sent invitations `People (N)` count.
- `import-audit` imports the `salesnav-audit.js` artifact.
- `record --status pending` is the only status that increments verified sends.
- `import-capture --only-connectable` imports only sendable static candidate
  observations from `page.json`; imports do not count sends, but every import
  updates the source capture cursor from the artifact `resumeUrl`.
- `reservoir import-capture` stores read-only connectable captures for a later
  active run; `reservoir fill-run` consumes them into the current controller run.
- `next-candidate` returns the next imported, unrecorded `connectable` lead.
- `drain-stale-candidates` records queued connectable observations from filled
  or exhausted sources as skipped.
- `plan --json` returns the next machine-readable operator action.
- `capture-source.resume_url` tells the operator to resume capture from the last
  imported page for that source instead of starting from page 1.
- `capture-source.capture.playwriter_timeout_ms` tells the operator which
  Playwriter `--timeout` to use for the capture script.
- `send-next` writes the selected candidate config, runs `salesnav-send-one.js`,
  and records the result only for non-dry-run sends unless `--no-record` is set.
- `send-guarded --single-pass` runs one Playwriter visit per candidate, with the
  sender validating pre-send `Connect` and post-send `Connect — Pending`.
- `send-guarded --dry-run` keeps the older no-send candidate validation path for
  debugging.
- `record-send-result` imports `salesnav-send-one.js` output and records only
  `pending-verified` as a counted send.
- `record-top-up-result` records adapter-only audit top-ups as `audit-top-up`
  so final reconciliation is visible without changing source counts.
- `reconcile-audit` repeats sent-page audits and can finish the run once the
  audited delta matches the target.
- `top-up-reconcile` owns the direct top-up loop after `reconcile-audit` stays
  short, sending one distinct candidate at a time and auditing until target
  with a default 20-attempt ceiling.
- `tune-sources` reports low-yield source health and can explicitly exhaust
  low-yield sources with `--apply`.
- `report` includes source-yield health and recorded phase timing.
- `finish` seeds the acceptance ledger with every invitation actually sent.
- `acceptance seed-history` backfills the acceptance ledger from historical
  controller JSONL logs.
- `acceptance export/import/report` measures later accepted/connected outcomes
  from exported invitation candidates.
- `acceptance draft-followups` creates draft-only Markdown first messages for
  newly accepted connections and tracks which accepted people already have a
  draft.
- `needs-reaudit` blocks further sending until a fresh `audit` is recorded.
- `source-exhausted` carries the source shortfall into the next source.
- `finish` refuses to complete unless the audited sent-page delta equals the target.

The final audit is intentionally stricter than row-level confirmations because
LinkedIn can accept a send while a browser tool reports a stale or failed UI state.

## Pending Cleanup Flow

The same binary also owns stale sent-invitation cleanup state:

```sh
linkedin-network-run pending-cleanup start \
  --max-withdrawals 75 \
  --threshold-months 2

linkedin-network-run pending-cleanup import-audit /tmp/linkedin-pending-cleanup-audit.json
linkedin-network-run pending-cleanup import-capture /tmp/linkedin-pending-cleanup-capture.json
linkedin-network-run pending-cleanup plan --json
linkedin-network-run pending-cleanup withdraw-next --session <session> --dry-run
linkedin-network-run pending-cleanup withdraw-next --session <session> --allow-withdraw
linkedin-network-run pending-cleanup finish
```

Capture sent invitations with Playwriter:

```sh
playwriter -s <session> -e 'state.salesNavPendingCaptureConfig = {
  out: "/tmp/linkedin-pending-cleanup-capture.json",
  loadMore: 10,
  thresholdMonths: 2
}'

playwriter -s <session> --timeout 45000 \
  -f /Users/hanifcarroll/projects/tool/scripts/salesnav-pending-capture.js
```

The pending cleanup sender is also one candidate at a time. It refuses fresh
rows before clicking, defaults to dry-run, and requires `--allow-withdraw` before
it can perform a real withdrawal. `finish` expects the final sent-page audit
delta to equal `-withdrawn_count`.
