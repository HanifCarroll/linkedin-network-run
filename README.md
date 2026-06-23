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

For local verification:

```sh
go test ./...
go build -o linkedin-network-run ./cmd/linkedin-network-run
```

For the installed automation executable, build the Go binary into the user-local
bin directory:

```sh
mkdir -p /Users/hanifcarroll/.local/bin
go build -o /Users/hanifcarroll/.local/bin/linkedin-network-run ./cmd/linkedin-network-run
```

The controller source lives in this repository. The installed binary should not
live under `~/.cargo/bin` now that the Rust controller has been removed.

## Recruiter And Agency Outreach

The repo also includes a separate recruiter/agency workflow for the ASAP
job-search outbound lane:

```sh
go run ./cmd/recruiter-agency-outreach -- report
```

This workflow is intentionally separate from `linkedin-network-run` state. It
does not send connection requests, does not consume the generic networking run,
and does not write into `~/Library/Application Support/linkedin-network-run/`.
It can send already-drafted LinkedIn messages only through the guarded
`send-message` command, and only when `--allow-send` is passed. Its default state
lives at:

```text
~/Library/Application Support/recruiter-agency-outreach/outreach.sqlite
```

Use it for recruiters and agencies only:

```sh
recruiter-agency-outreach run-daily \
  --session auto \
  --target-agencies 5 \
  --target-recruiters 5 \
  --allow-send \
  --stop-when-no-progress \
  --max-no-progress-searches 12 \
  --print-markdown
```

`run-daily` resolves `--session auto` by reusing an existing Playwriter session
for this workspace, or creating one when none exists. It resets the Playwriter
session connection, opens generated Sales Navigator searches, imports and
dedupes accounts/leads, drafts context-aware messages, validates messageability
in the browser, sends up to 5 agency messages and 5 recruiter messages for the
current run when `--allow-send` is present, and writes a Markdown dashboard
under the outreach state directory. Each run gets a stable `run_id`; default
artifacts are written under run-specific directories, with dashboard aliases at
`dashboards/latest-run.md`, `dashboards/latest-render.md`, and
`dashboards/runs/<run_id>.md`. Use `--skip-session-reset` only when
intentionally preserving a live Playwriter page connection.

For agency-heavy reruns, `--stop-when-no-progress` keeps the run from spending
the whole browser budget on account-scoped people searches that produce no new
messageable contacts. The default CLI threshold is 12 consecutive no-progress
agency contact searches; tune it with `--max-no-progress-searches`.

The built-in daily sources do not depend on stale Sales Navigator saved-search
names. After a source has been captured once, the workflow still uses its saved
resume cursor so later rounds continue from the next result page.

Agency sourcing is account-first. The daily runner captures agency account
searches, qualifies accounts into `qualified`, `needs_review`, `rejected`, or
`exhausted`, then searches people scoped to qualified accounts with a Sales
Navigator `CURRENT_COMPANY` filter. The older person-first agency searches are
kept as fallback sources only when the account-first path produces no contact
captures in a round. Recruiter sourcing remains person-first.

Website, Webflow/Shopify, and WordPress-focused agencies are valid agency
targets. The classifier tags them with a website/CMS build signal and the draft
generator uses a frontend-heavy website/CMS implementation pitch instead of the
digital-product/MVP pitch.

Validated source configuration:

| Bucket | Source | Sales Navigator filters | Measured result |
| --- | --- | --- | --- |
| Recruiters | `ASAP - Contract Recruiter Titles` | United States, 2nd-degree, Posted on LinkedIn, current title in `Contract Recruiter`, `Senior Contract Recruiter`, `Contract Technical Recruiter`, `Senior Technical Recruiter Contract` | 66 eligible and 3 needs-review from 75 captured; 43 eligible and 3 needs-review from a 50-row daily-depth stress capture |
| Agency accounts primary | `ASAP - Agency Accounts Development Agency` | United States, industry in `Software Development`, `IT Services and IT Consulting`, `Design Services`, company headcount `11-50`, `51-200`, `201-500`; keyword `custom software development agency` | Used to build the qualified account reservoir before contact capture |
| Agency accounts backup | `ASAP - Agency Accounts Digital Agency` | Same account filters; keyword `digital product agency` | Used to build the qualified account reservoir before contact capture |
| Agency accounts backup | `ASAP - Agency Accounts Product Studio` | Same account filters; keyword `product studio` | Used to build the qualified account reservoir before contact capture |
| Agency contacts | `ASAP - Agency Account Contacts - <account> - founder_recent` | United States, 2nd-degree, Posted on LinkedIn, `CURRENT_COMPANY` set to the qualified agency account, current title in `Founder`, `Co-Founder`, `Owner`, `Partner`, `Managing Partner`, `Principal Consultant`, `Technical Director`, `President`, `Managing Director` | First account-scoped contact pass |
| Agency contacts broad | `ASAP - Agency Account Contacts - <account> - executive_delivery_broad` | United States, 2nd-degree, `CURRENT_COMPANY` set to the qualified agency account, broad executive/delivery keywords, no Posted-on-LinkedIn filter | Second pass before exhausting an account |
| Agency contacts resource | `ASAP - Agency Account Contacts - <account> - resource_delivery_broad` | United States, 2nd-degree, `CURRENT_COMPANY` set to a high-fit qualified agency account, resource/delivery/client-services keywords, no Posted-on-LinkedIn filter | Third pass for high-fit accounts |
| Agencies fallback | `ASAP - Agency Development Agency Leaders`, `ASAP - Agency Digital Agency Leaders`, `ASAP - Agency Product Studio Leaders` | Person-first agency searches retained from the previous source-quality run | Used only when account-first contact capture yields no candidates in a round |

The latest source-quality test captured 956 visible Sales Navigator rows across
13 recruiter/agency source configurations, with no real sends. The final
2-page daily-depth stress test captured the default source mix into one deduped
state and drafted 16 agency-bucket eligible leads plus 44 recruiter-bucket
eligible leads.

For guarded sends, the workflow reopens the saved Sales Navigator people search
and clicks the matching row-level `Message` or `InMail` action first. Sales
Navigator lead detail pages are now only a fallback when the saved search row is
missing or cannot open a composer. This keeps validation usable when direct
lead pages render blank or return profile API 429 responses while search results
still render normally.

To force a fresh saved-search resolver for a custom/manual saved-search source:

```sh
recruiter-agency-outreach run-daily \
  --session auto \
  --target-agencies 5 \
  --target-recruiters 5 \
  --allow-send \
  --refresh-saved-searches \
  --stop-when-no-progress \
  --max-no-progress-searches 12 \
  --print-markdown
```

The dashboard includes visible source context, account context, fit reasons,
draft angle, draft evidence, message text, last send check, run actions,
latest-run evidence, agency contactability, agency drill-down counts, and a
plain limiting reason. A standalone dashboard render is labeled as a render and
does not claim a send run occurred:

```sh
recruiter-agency-outreach dashboard --print-markdown
```

Review the latest actual run, including `run_id`, start/end time, command,
send/skipped counts, blocker, dashboard path, and the recommended next command:

```sh
recruiter-agency-outreach last-run
recruiter-agency-outreach recommend-next-run --target-agencies 5 --target-recruiters 5 --allow-send
```

Diagnose the agency account pool before another agency-only rerun:

```sh
recruiter-agency-outreach agency-pool diagnose --limit 20
```

This is read-only. It shows the state path, account statuses, contactability
funnel, drill-down counts, retryable browser errors, and the next useful account
actions such as `continue_linkedin_contact_search`, `validate_or_send_open_lead`,
or `website_enrichment`.

Website checks are a second-stage agency source. Use them after LinkedIn
account/contact searches fail to find a messageable person, and only from public
company/team/contact pages. The output should feed reviewed drafts or source
artifacts before any send path; do not treat random scraped personal emails as
automatically messageable.

Import reviewed directory or partner-list agency sources into the account pool:

```sh
recruiter-agency-outreach agency-pool import-source /path/to/agency-source.json
```

The source artifact is structured JSON, not raw scraped page text:

```json
{
  "schema_version": 1,
  "source": "Webflow partners",
  "source_type": "webflow_partner",
  "rows": [
    {
      "name": "Bright Studio",
      "website": "https://bright.example.com",
      "source_url": "https://webflow.com/agencies/bright-studio",
      "services": ["Web Development"],
      "contacts": [
        {
          "name": "Jane Doe",
          "profile_url": "https://www.linkedin.com/in/jane-doe/",
          "evidence": ["listed as team contact in source directory"]
        }
      ]
    }
  ]
}
```

Website enrichment is still review-only. It records explicit `mailto:` links,
explicit LinkedIn `/in/` profile links, and contact forms from contact-oriented
pages/actions as `agency_contact_candidates`; it does not create sendable
LinkedIn leads:

```sh
recruiter-agency-outreach agency-pool enrich-websites --limit 25
recruiter-agency-outreach agency-pool contacts --limit 20
recruiter-agency-outreach agency-pool contacts --status generic_inbox --limit 20
```

The dashboard and `agency-pool diagnose` include review-only contact counts and
source-yield counts so failed agency reruns show whether the limit is LinkedIn
messageability, empty account-scoped searches, or a missing source/enrichment
pool.

Revise a draft before sending by writing the revised body to a local file and
resetting the lead to `drafted`:

```sh
recruiter-agency-outreach revise \
  --lead-id <id> \
  --body-file /tmp/revised-message.txt \
  --angle "manual adjustment after dashboard review"
```

Send leads that have already passed a dry-run messageability check:

```sh
recruiter-agency-outreach send-ready \
  --session auto \
  --target-agencies 5 \
  --target-recruiters 5 \
  --allow-send \
  --print-markdown
```

Manual capture remains available for one-off recovery or inspection:

```sh
recruiter-agency-outreach capture \
  --session <session> \
  --source "ASAP - Contract Recruiter Titles" \
  --pages 2 \
  --limit 25

recruiter-agency-outreach capture \
  --session <session> \
  --source "ASAP - Agency Digital Agency Leaders" \
  --pages 2 \
  --limit 25
```

Manual agency account capture and review are separate:

```sh
recruiter-agency-outreach capture-accounts \
  --session <session> \
  --source "ASAP - Agency Accounts Development Agency" \
  --pages 2 \
  --limit 25

recruiter-agency-outreach accounts --status qualified --limit 20
recruiter-agency-outreach accounts --status needs_review --limit 20
```

If capture was run manually, import the artifact directly:

```sh
recruiter-agency-outreach import-capture /tmp/recruiter-agency-outreach-capture/page.json
recruiter-agency-outreach import-accounts /tmp/recruiter-agency-outreach-account-capture/page.json
```

Review eligible leads:

```sh
recruiter-agency-outreach queue --limit 20
recruiter-agency-outreach queue --status needs_review --limit 20
```

Generate draft-only messages:

```sh
recruiter-agency-outreach draft --limit 20
```

The draft command writes Markdown under the outreach state directory and updates
local message status to `drafted`. Dry-run messageability checks are the default
for `send-message`; omitting `--allow-send` will not send:

```sh
recruiter-agency-outreach send-message \
  --lead-id <id> \
  --session <session>
```

Real message sends require an explicit flag and a visible Sales Navigator
`Message` or `InMail` surface. The browser adapter refuses to click `Connect`.
When a normal `Message` surface is available, the adapter opens it first and
records `conversation_exists` instead of sending if a prior conversation is
detected:

```sh
recruiter-agency-outreach send-message \
  --lead-id <id> \
  --session <session> \
  --allow-send
```

Manual send/reply tracking remains explicit:

```sh
recruiter-agency-outreach mark-message --lead-id <id> --status manually_sent
recruiter-agency-outreach mark-message --lead-id <id> --status replied
recruiter-agency-outreach reject --lead-id <id> --reason "not a contract recruiter or agency resource target"
```

The classifier accepts contract recruiters, staffing/account-manager profiles,
agency resource managers, delivery/technical directors, and agency
founders/partners. Startup CTO/founder sources are rejected or held out of this
workflow unless their profile clearly looks like a recruiter or agency/resource
target.

## Architecture

The Go controller is split by responsibility:

| Module | Responsibility |
| --- | --- |
| `cmd/linkedin-network-run/main.go` | CLI entrypoint only. |
| `cmd/recruiter-agency-outreach/main.go` | Recruiter/agency sourcing, drafting, and guarded message entrypoint. |
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
| `internal/outreach/` | Recruiter/agency lead classification, queueing, drafting, guarded message recording, and separate outreach state. |

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
  -f /Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-capture.js

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
  -f /Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-saved-searches.js
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

playwriter -s <session> --timeout 45000 -f /Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-send-one.js
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
  -f /Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-audit.js

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

Automation ownership is intentionally split:

- `linkedin-network` sends and reconciles new connection requests only.
- `linkedin-acceptance-daily` checks accepted outcomes, imports the acceptance
  artifact, writes follow-up drafts for newly accepted people, and can send
  those follow-ups through the guarded message sender.
- `linkedin-acceptance-weekly` is report-only. It summarizes the acceptance
  ledger and does not open LinkedIn, run Playwriter classification, import
  outcomes, or draft messages.

Backfill the ledger from historical controller JSONL run logs before checking
older outcomes:

```sh
linkedin-network-run acceptance seed-history
```

Export older invites for a later outcome check:

```sh
linkedin-network-run acceptance export \
  --min-age-days 1 \
  --max-age-days 45 \
  --out /tmp/linkedin-acceptance-candidates.json
```

Classify those exported candidates with Playwriter:

```sh
playwriter -s <session> -e 'state.salesNavAcceptanceConfig = {
  in: "/tmp/linkedin-acceptance-candidates.json",
  out: "/tmp/linkedin-acceptance-outcomes.json",
  limit: 0,
  delayMs: 750
}'

playwriter -s <session> --timeout 45000 \
  -f /Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-acceptance-outcomes.js
```

For large exports, set `offset` and `limit` and write one chunk artifact per
batch. The script writes its artifact after each checked row and marks the final
artifact with `complete: true`, so interrupted browser runs can be resumed from
the last completed chunk instead of restarting the whole export.

Import the browser outcome artifact and report acceptance by source:

```sh
linkedin-network-run acceptance import /tmp/linkedin-acceptance-outcomes.json
linkedin-network-run acceptance report --min-age-days 1 --max-age-days 45
```

Generate first-message drafts for newly accepted connections:

```sh
linkedin-network-run acceptance draft-followups \
  --session <session> \
  --out "/Users/hanifcarroll/Library/Application Support/linkedin-network-run/acceptance-followups/$(date +%F).md"
```

This command exports every accepted ledger entry that has not already received a
draft, researches each person through Sales Navigator plus public web search,
writes a Markdown report, and records the drafted people plus their message text
in `acceptance-followups.json`. Re-run with `--include-drafted` only when
intentionally regenerating drafts. Pass `--research <artifact.json>` to render
from an existing research artifact without opening the browser. Adjust
`--max-web-results` if the public web evidence needs to be broader or narrower
without changing the draft strategy.
For large accepted batches, `salesnav-accepted-research.js` also accepts
`offset` and `limit` and writes incremental chunk artifacts with `complete:
true` when a chunk finishes.

Dry-run a drafted accepted follow-up before sending:

```sh
linkedin-network-run acceptance send-followup \
  --id <accepted-followup-id> \
  --session <session> \
  --dry-run
```

Or dry-run a bounded batch:

```sh
linkedin-network-run acceptance dry-run-followups \
  --session <session> \
  --limit 5
```

A successful dry run marks the record `dry_run_ready`. Real sends require that
ready status and an explicit `--allow-send` flag:

To verify the actual rendered body formatting without sending, use the preview
fill mode. It fills the composer, records `bodyFill.lineBreakCount`, and stops
before any send click:

```sh
linkedin-network-run acceptance send-followup \
  --id <accepted-followup-id> \
  --session <session> \
  --preview-fill
```

```sh
linkedin-network-run acceptance send-followup \
  --id <accepted-followup-id> \
  --session <session> \
  --allow-send
```

Send a bounded batch that already passed dry-run checks:

```sh
linkedin-network-run acceptance send-ready-followups \
  --session <session> \
  --limit 5 \
  --allow-send
```

The guarded message sender opens the accepted person's Sales Navigator profile,
uses the normal Message/InMail action, preserves line breaks, records every
attempt in `acceptance-followups.json`, and records `conversation_exists`
instead of sending when an existing conversation is detected.

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
- `acceptance draft-followups` creates Markdown first messages for newly
  accepted connections and stores the drafts in `acceptance-followups.json`.
- `acceptance dry-run-followups` checks a bounded batch of drafted accepted
  follow-ups and marks messageable records `dry_run_ready`.
- `acceptance send-followup` dry-runs or sends one accepted follow-up through
  the guarded message sender; `--preview-fill` fills the composer for formatting
  verification without sending.
- `acceptance send-ready-followups` sends a bounded batch of accepted follow-ups
  already marked `dry_run_ready`.
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
  --threshold-weeks 2

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
  thresholdDays: 14
}'

playwriter -s <session> --timeout 45000 \
  -f /Users/hanifcarroll/projects/linkedin-network-automation/scripts/salesnav-pending-capture.js
```

The pending cleanup sender is also one candidate at a time. It refuses fresh
rows before clicking, defaults to dry-run, and requires `--allow-withdraw` before
it can perform a real withdrawal. `finish` expects the final sent-page audit
delta to equal `-withdrawn_count`.
