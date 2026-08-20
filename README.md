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

LinkedIn Jobs capture, direct-page enrichment, and outreach use the caller-owned Codex Chrome tab and store bounded evidence through the CLI. Networking, analytics, and Jobs liveness checking remain on distinct, command-bound Playwriter sessions. The repository does not implement browser leases or cross-automation locks.

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

Jobs capture uses the caller's already logged-in Codex Chrome tab to observe
`voyagerJobsDashJobCards` responses and stores raw JSON durably in SQLite. The capture commands
need no Playwriter binary or browser session: start a run with `jobs capture-start`, pipe each
helper `rawBody` directly to `jobs capture-ingest --payload -`, and use the saved page/cursor
checkpoint to resume. No per-page artifact is required (a file is diagnostic-only). The helper can
pipe and parse the CLI's stable JSON envelope with `captureAndIngestJobsPage`. Run
`jobs normalize --run-id ID [--limit N]` to process pages transactionally, deduplicate by
LinkedIn job ID, preserve run/page provenance, and resume safely. It does not filter by location
or fit and does not enrich. Run `jobs filter --run-id ID --terms '["product engineer","software engineer"]' --policy-version jobs-fit-v1`
with explicit title terms and a caller-supplied policy version; `--max-age-days` defaults to 30.
It never reads `JOB_SEARCH_TERMS`, uses no location, records deterministic
fit/freshness reasons, and keeps unknown freshness while reporting it. Direct-page enrichment is request-first:
the helper arms CDP before caller-owned navigation, captures only the target Document and the three named
flagship-web components, parses their bodies locally, then verifies with two stable DOM reads. DOM data
fills missing or empty component fields; only `fit=kept` jobs are eligible. Raw scoped bodies and provenance
are stored in SQLite, with bounded size/count validation. A no-team result requires stable DOM plus an
observed (possibly empty) `peopleWhoCanHelp` response; otherwise the job remains `retry_required`.
Live-job checks, networking, and analytics remain on their existing paths. Jobs sending uses the
caller-owned Chrome handoff described below;
triage prioritizes opportunities but never automatically pursues or rejects them.
Before human review, `jobs triage-next` hands one eligible kept job to an agent. `jobs triage-record`
stores an evidence-backed `strong`, `possible`, or `weak` fit brief under policy
`jobs-triage-v1-20260819`; it never rejects a job or changes review, and weak jobs remain visible.
Pending review shows only triaged kept jobs with hiring-team people, ordered Strong, Possible, Weak.
Previously decided or sent history remains visible after the migration.
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

The viewer is the intake queue for kept jobs with listed hiring-team people. It shows Pending,
Approved, and Rejected decisions plus matched term, filter reason, employment/posting/capture facts,
links, and evidence gaps. Approval does not require a draft; sending remains guarded by drafted +
approved + `--allow-send`, and rejection persists across sibling jobs.

Every hiring-team job gets a draft — there is no pre-draft qualification. Draft once per
recipient: when several postings list the same person, draft the best-fitting role; the other
roles are context, not separate messages, and at most one approved/sent message holds per
person. `jobs draft-next` is a deterministic, read-only companion handoff for one person and
one primary role from an approved kept opportunity. It emits stored person/job/company evidence, route-specific writing instructions,
and `blockedApplications` for unapplied contract follow-ups. It never calls an LLM, stores a draft,
approves, sends, opens a browser, or touches HubSpot. The companion writes the message, then calls
`jobs draft`, which stores the draft and returns review to needs-review. A draft stores an editable
subject line and a body; interior blank lines in the body round-trip unchanged. The draft template
depends on the role's queue section.

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

For an approved application-followup role, `jobs application-next --id JOB_ID` emits one
read-only, caller-owned browser handoff with the posting/application URL, stored role/company
evidence, and the required `jobs applied` checkpoint. The operator completes the external ATS;
the handoff stops on unknown questions, uploads, assessments, compensation, legal/eligibility,
or before submit. It is not a form filler and never submits.

Review happens in the local queue, not through the CLI:

```sh
bun run view   # SvelteKit production server at http://127.0.0.1:4567
# For development with component reload:
bun run view:dev   # set PORT=... to override 4567
```

The SvelteKit viewer (built for Bun with `svelte-adapter-bun`) is the intake queue. The queue splits people into top-level sections with person counts: **All outreach**, **Direct
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

### HubSpot import handoff

After approval, `jobs hubspot-next` prepares or resumes one person-centred import. It returns the
company, contact, deal, association, and Day 1 task mapping plus any HubSpot IDs already recorded.
An authorized agent performs each lookup-before-create action through the official HubSpot
connection. The CLI stores only durable receipts; it has no HubSpot token and performs no network
request itself.

```sh
linkedin-tools --json jobs hubspot-next --id JOB_ID
linkedin-tools --json jobs hubspot-record --prospect-id PROSPECT_ID --company-id 123
linkedin-tools --json jobs hubspot-record --prospect-id PROSPECT_ID --contact-id 456 --deal-id 789
linkedin-tools --json jobs hubspot-record --prospect-id PROSPECT_ID --task-id 1011 \
  --associations-complete
```

The handoff accepts only kept, approved jobs with a company and a usable hiring-team profile. The
prospect ID is derived from that normalized profile, so sibling roles remain one prospect. The
packet includes the computed route and application checkpoint evidence: contract-route tasks say
to apply first when absent, or send the application follow-up after it is recorded; direct-route
tasks offer short-term contract help while the company fills the full-time role. On a retry, the
agent searches HubSpot using the packet's stable keys before creating anything. Replayed IDs are
no-ops, conflicting IDs stop the import, and completion requires company, contact, deal, task, and
association receipts. This stage creates a task only; it never sends outreach.

`jobs applied --id JOB_ID [--application-url URL] [--applied-at ISO]` records the application
checkpoint for application-followup jobs (the timestamp defaults to now).

`jobs send-prepare --allow-send [--id JOB_ID]` reserves exactly one approved
 drafted job while preserving review, application, hiring-team, and duplicate-recipient guards.
It persists a prepared reservation (never job state), returns a unique attempt identity, and emits a route copy plus transport evidence contract. The caller-owned
`scripts/linkedin-jobs-outreach-chrome-helper.mjs` arms CDP before visible UI interaction and
accepts an explicit exact endpoint URL contract from a live spike; it fails closed without one.
It never reads cookies or headers, replays private write XHR, creates/closes tabs, or persists state.

The helper records `possible` immediately before the visible Send action, then pipes confirmed
evidence to `jobs send-record --payload -`. DM and InMail have separate exact
allowlists; HTTP success alone is never enough. The composer must be gone and the message visible
in the matching recipient's thread. A prepared or unresolved `possible` receipt keeps the draft unsent but blocks blind retry;
`proven_no_send` safely releases the reservation, and only `confirmed` marks it sent. Replaying the same attempt is a no-op. A live spike is still
needed to populate the exact DM and InMail endpoint patterns and any observable recipient URN
binding in the caller's LinkedIn account.

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

### Sales Navigator staffing intake

This read-only slice accepts only the saved people search with `savedSearchId=2006360906`.
The CLI owns SQLite and JSON envelopes; caller-owned Chrome owns the visible action and response
capture. It never creates or closes tabs and never receives cookies or headers.

```sh
linkedin-tools salesnav staffing capture-start --run-id ID \
  --source-url 'https://www.linkedin.com/sales/search/people?savedSearchId=2006360906'
linkedin-tools salesnav staffing capture-ingest --run-id ID --start 0 --payload - \
  --source-url 'https://www.linkedin.com/sales/search/people?savedSearchId=2006360906' \
  --response-url 'https://www.linkedin.com/sales-api/salesApiLeadSearch?q=savedSearchId&start=0&count=25&savedSearchId=2006360906'
linkedin-tools salesnav staffing capture-finish --run-id ID --state complete
linkedin-tools salesnav staffing normalize --run-id ID
linkedin-tools salesnav staffing qualify --run-id ID --policy-version staffing-v2
linkedin-tools salesnav staffing status --run-id ID
```

Use `--json` for the stable `{ "ok": true, "data": ... }` or
`{ "ok": false, "error": ... }` boundary. The handoff helper is
`scripts/linkedin-salesnav-chrome-helper.mjs`. Account capture uses
`scripts/linkedin-salesnav-account-chrome-helper.mjs` and the additive migration
19 tables; qualification decisions are organization-scoped across runs.

Staffing intake now begins with the account search. The supported contract is United States,
Staffing and Recruiting, 11–50 and 51–200 employees, with a non-empty, run-bound keyword query.
Open Sales Navigator at `/sales/home`, then open the account search through its filters or saved
searches. The helper is armed before the visible action that loads the results. The captured account
request must match the run's exact filters and keywords before it can be stored:

```sh
linkedin-tools salesnav staffing account-capture-start --run-id ID \
  --source-url '<current-supported-account-search-url>'
linkedin-tools salesnav staffing account-capture-ingest --run-id ID --start 0 \
  --payload - --source-url '<same-url>' --response-url '<captured-account-response-url>'
linkedin-tools salesnav staffing account-capture-finish --run-id ID --state complete
linkedin-tools salesnav staffing account-normalize --run-id ID
linkedin-tools salesnav staffing account-qualify-next --run-id ID
linkedin-tools salesnav staffing account-qualify-record --run-id ID \
  --organization-id ID --fit kept --evidence '{}' --unknowns '[]' \
  --reason 'Relevant staffing firm' --policy-version staffing-account-v1
linkedin-tools salesnav staffing account-people-candidates --run-id ID
linkedin-tools salesnav staffing account-people-capture-start --run-id PEOPLE_RUN \
  --account-run-id ACCOUNT_RUN --organization-id ORG_ID \
  --source-url 'https://www.linkedin.com/sales/search/people?query=(filters:List((type:CURRENT_COMPANY,values:List((id:ACCOUNT_ID,selectionType:INCLUDED)))))'
linkedin-tools salesnav staffing account-people-capture-ingest --run-id PEOPLE_RUN --start 0 \
  --payload - --source-url '<same-company-scoped-url>' --response-url '<scoped-lead-response-url>'
linkedin-tools salesnav staffing account-people-capture-finish --run-id PEOPLE_RUN --state complete
linkedin-tools salesnav staffing account-people-normalize --run-id PEOPLE_RUN
linkedin-tools salesnav staffing account-people-next --run-id PEOPLE_RUN
linkedin-tools salesnav staffing account-people-review --run-id PEOPLE_RUN --person-id PERSON_ID --review approved --evidence '{}'
linkedin-tools salesnav staffing account-status --run-id ID
```

Review the firm's account evidence, LinkedIn company page, and website before recording the
qualification. People capture is only allowed for a kept account and only accepts a company-scoped
lead-search response. Selection uses fixed lane role order and stable tie-breaking, returning one
primary and at most one backup. Local review is approve/reject only; it does not draft, send, or call
HubSpot. A future dry CRM handoff must lookup-before-create the organization by LinkedIn company URL,
the person by normalized LinkedIn profile URL, and record the company-contact association;
no CRM identifiers or writes are implemented here.

### Sales Navigator studio lane

`salesnav studio` uses the same caller-owned account capture flow, but only these two exact
Boolean searches are accepted: US, 11–50 employees, IT Services & IT Consulting plus Design
Services, with the approved product-development or product-studio query. Decisions are scoped by
`(lane, organization)` while organizations remain globally deduplicated. Before a studio keep,
record manual evidence:

```sh
linkedin-tools salesnav studio account-capture-start --run-id ID \
  --source-url 'https://www.linkedin.com/sales/search/company?savedSearchId=2006497026' \
  --keyword-query '<approved-studio-query>'
linkedin-tools salesnav studio account-capture-ingest --run-id ID --start 0 \
  --payload - --source-url '<same-url>' --response-url '<captured-account-response-url>'
linkedin-tools salesnav studio account-capture-finish --run-id ID --state complete
linkedin-tools salesnav studio account-normalize --run-id ID
linkedin-tools salesnav studio account-qualify-next --run-id ID
linkedin-tools salesnav studio firm-research-record --run-id ID --organization-id ORG \
  --source-urls '["https://www.linkedin.com/company/example","https://example.com"]' \
  --services 'Product design and custom software' --fact 'Built a customer portal' \
  --unknowns '["team size"]'
linkedin-tools salesnav studio account-qualify-record --run-id ID \
  --organization-id ORG --fit kept --evidence '{"linkedin":"reviewed","website":"reviewed"}' \
  --unknowns '[]' --reason 'Engineering delivery demonstrated' \
  --policy-version studio-account-v1
linkedin-tools salesnav studio account-people-candidates --run-id ID
linkedin-tools salesnav studio account-status --run-id ID
```

`account-people-candidates` is the read-only downstream boundary: it returns only kept accounts
observed in that lane and run. It does not capture people. This lane has no HubSpot or sending path.
