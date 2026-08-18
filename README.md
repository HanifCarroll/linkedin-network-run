# LinkedIn Tools

A single-package Bun/TypeScript implementation for three workflows:

- Daily LinkedIn network automation.
- Weekly seven-day content analytics export.
- Chrome-based LinkedIn Jobs result capture with guarded hiring-team outreach
  and a local draft-review queue.

Promoted 2026-08-11 from `linkedin-tools-next` to the canonical repository
path. The former Python implementation (acceptance tracking, Relationship
Radar, welcome messages, pending cleanup, recruiter outreach, opportunity
intel, review UI) was retired on the same date; its code remains in git
history before the promotion commit.

## Operating contract

Networking uses only these exact Sales Navigator saved searches:

| Source | Saved search ID | Preferred allocation |
| --- | --- | ---: |
| B2B SaaS Founders & CTOs | 2006164114 | 15 |
| B2B SaaS Engineering & Product Leaders | 2006164122 | 15 |

The daily target is exactly 30 durable requests, shared across the two configured sources
(15/15, with bidirectional carryover). Completion requires exactly 30 durable, zero provisional
or planned attempts, and a complete final sent-list reconciliation. A possible send is reserved
until reconciliation proves its outcome.

LinkedIn Jobs result capture uses the caller-owned Codex Chrome tab and stores raw responses through
the CLI. Networking, analytics, Jobs enrichment/checking, and sending remain on distinct,
command-bound Playwriter sessions for now. The repository does not implement browser leases or
cross-automation locks.

## Install and verify

```sh
bun install
bun run check:cli
bun run typecheck
bun run build
bun run smoke
```

The package exposes `linkedin-tools` from `dist/cli.js`. It is not installed globally by this
repository.

## Configuration

Default fresh state:

```text
~/Library/Application Support/linkedin-tools-next/
  linkedin-tools.db
  downloads/
  logs/
  receipts/
  reports/
```

Supported environment variables:

- `LINKEDIN_TOOLS_STATE_DIR`
- `LINKEDIN_TOOLS_PLAYWRITER_BIN`
- `LINKEDIN_TOOLS_NETWORK_SESSION`
- `LINKEDIN_TOOLS_ANALYTICS_SESSION`
- `LINKEDIN_TOOLS_ANALYTICS_ACCOUNT`
- `LINKEDIN_TOOLS_ANALYTICS_DOWNLOAD_ROOTS`

Run the local-only prerequisite check first:

```sh
bun run src/cli.ts --json doctor \
  --network-session 7 \
  --analytics-session 8
```

Doctor checks Bun, the configured Playwriter executable and version, configured session IDs, active
session listing, state-directory writability, and SQLite migrations. It does not navigate to or open
LinkedIn.

## Commands

```sh
linkedin-tools --json doctor
linkedin-tools --json network status
linkedin-tools --json network report
linkedin-tools --json network tick --allow-send --batch-size 5 \
  --target 30 --max-real-sends 30 --session auto
linkedin-tools --json network reconcile --session auto
linkedin-tools --json network incident-status
linkedin-tools --json network incident-clear \
  --account-access-confirmed --warning-cleared-confirmed \
  --reason "weekly limit cleared after manual review"

linkedin-tools --json analytics export \
  --session auto \
  --period previous-7-days \
  --account Hanif \
  --download-root /Users/hanifcarroll/Downloads \
  --out '/absolute/path/content-{startDate}-{endDate}.xlsx'

linkedin-tools --json migration dry-run \
  --source-root '/Users/hanifcarroll/Library/Application Support/linkedin-tools/network-automation'
```

`network tick` refuses to dispatch without the exact `--allow-send` flag. One invocation continues
serially until the run is Done or returns a typed checkpoint or terminal blocker. `--batch-size` is
capped at five, the target is fixed at 30, and `--max-real-sends` cannot exceed 30. The controller
walks each saved-search list in order: it opens the list, scrolls to load every row, sends a
connection request to each connectable person (5s pacing between sends), and paginates until the
source's share or the daily target is met, then moves to the next list. After the send phase it
waits 60 seconds for invitations to settle, audits the sent-invitations page, and confirms which
sends appear. Unconfirmed sends after the settle wait are marked proven-no-send, and the tick sends
more to top up until 30 are confirmed on the sent page, then finishes. Audits cannot be disabled.

When a new local day starts, the controller checks older active runs first. It parks an older run as
missed only when it has no planned or possible sends, then starts the new day. If an older run still
has a planned or possible send, the controller returns `NETWORK_PRIOR_DAY_NEEDS_AUDIT` before any
browser session is created; reconcile that exact earlier date before sending on the new day.

`analytics export` accepts either `--period previous-7-days` or exact `--start-date` and
`--end-date`, never both. The range must contain exactly seven inclusive days. Repeat
`--download-root` for more than one native download location. Current optional export controls are
`--receipt`, `--recovery-state`, `--poll-interval-ms`, and `--max-polls`.

`migration dry-run` is proposal-only. It reads legacy state and exposes no apply/import command.

Unknown, duplicate, conflicting, malformed, or out-of-range flags fail before an operation runs.
Paths for state, exports, downloads, receipts, recovery state, Playwriter, and legacy sources must
be absolute.

A shared incident gate guards every browser operation. Fatal account signals (weekly limit, rate
limit, unusual activity, checkpoint or security-verification walls, login walls) open
`linkedin-incident.json` in the state directory and block all further browser automation with
`INCIDENT_ACTIVE` (exit 7) until a human clears it. Clearing requires both confirmation flags and a
reason; partial clears fail with exit 2. `doctor` reports an active incident as a failing check.

For browser-capable commands, `--session auto` uses the exact workflow binding in
`sessions/network.json` or `sessions/analytics.json`. It reuses the bound ID only while Playwriter
reports it active. A missing or stale binding creates and verifies a new persistent session through
Playwriter's supported client API, then writes the binding atomically. Auto mode never adopts an
unbound active session or shares one session between workflows. Numeric session IDs remain
supported for explicit operator control. Tests fake this boundary and never create a live session.

## Jobs and review queue

Step 1 captures LinkedIn Jobs XHR responses through the Codex Chrome handoff and stores
raw JSON durably in SQLite. Start a run with `jobs capture-start`, pipe each helper `rawBody`
directly to `jobs capture-ingest --payload -`, and use the saved page/cursor checkpoint to
resume. No per-page artifact is required (a file is diagnostic-only). The helper can pipe and
parse the CLI's stable JSON envelope with `captureAndIngestJobsPage`. Run
`jobs normalize --run-id ID [--limit N]` to process pages transactionally, deduplicate by
LinkedIn job ID, preserve run/page provenance, and resume safely. It does not filter by location
or fit and does not enrich. Enrichment, detail, live-job checks, networking, analytics, and
sending remain unchanged for now and are planned for later migration to the same Chrome boundary.
`jobs classify` stores the two brief review phrases (`--work-focus`, `--product-system`) and
two longer summaries (`--work-summary`, `--product-summary`) shown in the viewer.

```sh
linkedin-tools --json jobs capture-start --run-id run-20260818-1 \
  --source-url "https://www.linkedin.com/jobs/search/?keywords=product%20engineer" \
  --search-config '{"keywords":"product engineer"}'
# After the Codex helper returns rawBody:
printf '%s' "$rawBody" | linkedin-tools --json jobs capture-ingest --run-id run-20260818-1 --page start:0 \
  --payload - --source-url "https://www.linkedin.com/jobs/search" \
  --response-url "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards"
linkedin-tools --json jobs capture-finish --run-id run-20260818-1 --state complete
linkedin-tools --json jobs normalize --run-id run-20260818-1
```

Normalization reports stable counts for pages processed, jobs observed, newly inserted jobs,
deduplicated observations, and remaining pages.

Every hiring-team job gets a draft — there is no pre-draft qualification. Draft once per
recipient: when several postings list the same person, draft the best-fitting role; the other
roles are context, not separate messages, and at most one approved/sent message holds per
person. A draft stores an editable subject line and a body; interior blank lines in the body
round-trip unchanged, and storing or redrafting returns the job to needs-review. The draft
template depends on the role's queue section.

**Direct outreach** uses the existing conversational contract-help pitch (three paragraphs
separated by one blank line, `\n\n`):

```text
Hi [first name] — I saw the [role] opening at [company]. One plain, specific detail
about the product, users, or problem that caught my attention.

One relevant experience or proof statement, in ordinary language, connected directly
to the detail in the first paragraph.

Would you be open to contract help while you're hiring?
```

The proof sentence must tie back to the detail in paragraph 1, not stand alone as a résumé line.

**Application note** is for contract roles (and full-time roles that are really contract
engagements: contract-to-hire, or W2/C2C/1099-only). Apply first, then send the short
application note the same day or next day — do not wait one to two weeks. The note names the
role and company, leads with one specific relevant proof, and closes by putting a name to the
application. It does not ask whether they are open to contract help and does not request a call:

```text
Hi [name] — I just applied for [role] at [company].

[One concrete sentence showing strong alignment.]

I wanted to put a name to the application. Happy to provide anything else that would be useful.
```

The subject line is normally `[Role] at [Company]` or `About the [Role] opening`. `--subject`
fills the composer's subject field only when the composer exposes one; normal no-subject DM
composers are unaffected.

Review happens in the local queue, not through the CLI:

```sh
bun run view   # http://127.0.0.1:4567
```

The queue splits people into top-level sections with person counts: **All outreach**, **Direct
outreach**, and **Application follow-up**. All outreach shows every person group once and marks
each with a compact Direct/Applied badge. Contract roles — and full-time roles that are really
contract engagements (contract-to-hire, or W2/C2C/1099-only) — appear only under Application
follow-up, never under Direct outreach. A mixed group (one person listed on both a direct and a contract
role) stays one queue item in Application follow-up and defaults its primary selection to the
contract role unless a sent, approved, or explicitly selected role owns the decision. The queue
defaults to Needs review, and the Needs review / Approved / Skipped / Sent / All filters and the
summary counts scope to the selected section. Each row is one recipient — a normalized first
hiring-team profile URL —
not one job. When several postings list the same person they appear as a single item with a
roles badge; the primary role (sent, then approved, then the selected role, then the most
recently updated non-skipped role) is shown as context and every other role is clickable
context. Approving one role keeps exactly one approved role per recipient, Skip & next skips
the whole person, and Return to review returns the whole person; a sent recipient is covered
and immutable. Jobs without a usable recipient stay visible under a job-id fallback row. The
Application follow-up detail shows a short reminder to apply first and send the application note
the same day or next day. There is no Send button and the viewer performs no
browser mutation; writes go through tight same-origin local JSON endpoints that route through
`JobsEngine`.

`jobs send` selects approved drafted jobs only (review = approved) and still requires the exact
`--allow-send` flag. Recipient uniqueness holds across time: approving conflicts with another
approved draft or an already-sent job for the same hiring-team profile URL (normalized: trim,
query/hash/trailing slash stripped, case-folded), reported as `DUPLICATE_APPROVED_PROFILE`.
A defensive duplicate-recipient guard in `jobs send` seeds from already-sent jobs and skips a
repeat profile without changing state. When a profile has no direct Message button, the send
script reads the member's id from the profile's static compose anchor, navigates the same page
to the Sales Navigator lead URL, and sends from the lead page's in-place InMail composer
(subject + message + Send). It never opens a new tab, because LinkedIn-opened tabs are invisible
to the playwriter bridge, and it reports a job as sent only when the message is visible in the
thread after Send.

## JSON contract

With `--json`, stdout contains exactly one envelope and diagnostics use stable error codes.

```json
{"ok":true,"data":{"command":"network status","state":"not_started"}}
```

```json
{"ok":false,"error":{"code":"SEND_NOT_AUTHORIZED","message":"network tick requires the explicit --allow-send flag"}}
```

## Scheduling templates

Uninstalled templates are in `launchd/`:

- `com.hanif.linkedin-tools.network.plist`: one completion-capable daily run at 09:05.
- `com.hanif.linkedin-tools.analytics.plist`: Sunday at 06:15.

Both templates are configuration-complete and use `--session auto`; they contain no replacement
tokens. They use distinct windows and log files, an explicit working directory and PATH, and no
`KeepAlive` or lease. This repository does not install or load them.

## Safety

Tests and `bun run smoke` use dependency injection, temporary state, and fake commands. They do not
exercise a live browser, LinkedIn, launchd, the old Python repository, or legacy state.
