# AGENTS.md

## Scope

This is the canonical Bun/TypeScript `linkedin-tools` repository, promoted
2026-08-11 from the former `linkedin-tools-next` path. The Python
implementation it replaced is retired; migration from legacy state is dry-run
and proposal-only.

The product workflows are deterministic daily networking, read-only content
analytics export, and interactive jobs intake/outreach. The current `jobs`
intake step captures raw LinkedIn Jobs result responses through Codex Chrome
and stores them in SQLite. Existing normalized rows still support guarded
hiring-team outreach with the explicit `--allow-send` flag.

## Invariants

- Use Bun, strict TypeScript, one package, `bun:sqlite`, and one `linkedin-tools` binary.
- Emit stable `{ "ok": true, "data": ... }` or `{ "ok": false, "error": ... }` JSON envelopes.
- Reject unknown, duplicate, conflicting, malformed, and out-of-range arguments before dispatch.
- LinkedIn Jobs capture and direct-page enrichment use caller-owned Codex Chrome handoffs plus CLI SQLite ingest. Direct enrichment is request-first: arm CDP before navigation, retain only the target Document and aboutTheJob/aboutTheCompanyForJobDetails/peopleWhoCanHelp response bodies, parse locally, then verify with stable DOM reads. Never add CLI browser control, browser leases, or cross-automation locks. Jobs liveness checking, networking, analytics, and sending remain on Playwriter.
- Scheduled browser commands use `--session auto`. Command-owned exact bindings keep network and
  analytics sessions distinct.
- Real network ticks require the exact `--allow-send` flag.
- One scheduled tick must continue serially until Done or a typed checkpoint/terminal blocker.
  It walks each source list (open, scroll to load all rows, send to every connectable person with
  5s pacing, paginate), waits 60s for invitations to settle, audits the sent page, resolves
  unconfirmed sends as proven_no_send, and tops up until 30 durable are confirmed on the sent page.
  Target and total real-send cap are 30.
- Target exactly 30 durable requests, preferred 15/15 across the two configured sources, with
  bidirectional carryover.
- Done requires 30 durable, zero provisional/planned, and complete final reconciliation.
- When a new local day starts, park older active runs as missed only when they have zero planned and
  possible sends. If an older run has either, return `NETWORK_PRIOR_DAY_NEEDS_AUDIT` before creating
  a browser session; reconcile that exact earlier date before starting the new day.
- Never replace a possible send or infer failure from absence.
- Migration never applies or writes legacy state.
- `jobs normalize` processes captured pages one SQLite transaction at a time, deduplicates only by LinkedIn job ID, records page/job provenance, and resumes from completed pages. It never filters by location or fit and never enriches.
- `jobs filter` requires explicit `--terms` JSON; it never imports or activates `JOB_SEARCH_TERMS`, never uses location, and scopes filtering to jobs observed in the requested run. Unknown freshness is kept and reported. Enrichment/detail may optionally use `--run-id` to process only that run's kept jobs.
- Jobs triage precedes human review: every kept job with hiring-team people remains available; an agent records strong|possible|weak plus an evidence-backed fit brief under `jobs-triage-v1-20260819`. Triage never rejects or changes review, and weak remains reviewable. Pending review shows only triaged jobs; previously decided or sent history remains visible. Approval may exist without a draft, but send still requires drafted + approved + `--allow-send`.
- Jobs application-followup roles require the durable `applied_at`/`application_url` checkpoint recorded by `jobs applied`; direct roles do not. The route remains computed by `outreachKindFor`, never persisted. Sending still requires drafted + approved + `--allow-send` and unique recipients.
- `jobs draft-next [--id JOB_ID] [--state-dir]` is a deterministic, read-only companion handoff. It returns one kept, triaged, approved opportunity per usable first hiring-team person, skips stored drafts and sent groups, reports `blockedApplications` for unapplied application-followup roles, and emits stored evidence plus route-specific writing instructions. It never calls an LLM, stores a draft, approves, sends, accesses a browser, or touches HubSpot. The next action is `jobs draft`, which resets review to `needs_review`.
- HubSpot import is an agent handoff: `jobs hubspot-next` emits the deterministic route-aware lookup-before-create packet and `jobs hubspot-record` stores IDs and association completion. The CLI holds no HubSpot credentials, performs no CRM request, and never treats task creation as send authority.
- Do not add the retired acceptance, radar, recruiter, opportunity, Python, uv,
  or web UI workflows. The `jobs` workflow is a distinct, approved workflow for
  hiring-team outreach on job postings; it is not a re-add of the retired
  recruiter workflow.

## Verification

No tests exist or should ever be written for this repository. The project
deliberately has no test suite: bounded local checks are the only verification,
and they must use fakes or temporary state.

```sh
bun run check:cli
bun run typecheck
bun run build
bun run smoke
plutil -lint launchd/*.plist
```

`jobs normalize` smoke coverage uses temporary SQLite state only; no live browser or legacy state is touched.

Smoke checks must use fakes or temporary state. Do not invoke a live browser,
LinkedIn, launchd installation, live automation, or legacy writes without a
separate explicit request.

## Sales Navigator staffing

`salesnav staffing` and `salesnav studio` are read-only, caller-owned Chrome account-capture workflows. Account capture enters through `/sales/home` and a visible account-filter or saved-search action; the captured request, not the address-bar form, must match the run's lane, filters, and keywords. Staffing remains the existing lane; studio accepts exactly two approved US 11–50 IT/design Boolean searches. Runs persist lane, canonical search config, and keyword query. Qualification is scoped by lane plus globally deduplicated organization; studio keeps require a recorded firm-research row with LinkedIn/website URLs, services, one concrete fact, unknowns, and review timestamp. Neither lane adds HubSpot or sending. Staffing remains a read-only, caller-owned Chrome capture workflow. Staffing begins with a United States account search fixed to industry 104 and headcount buckets C and D; each run is bound to its exact non-empty keyword query. Its commands are `account-capture-start`, `account-capture-ingest`, `account-capture-finish`, `account-normalize`, `account-qualify-next`, `account-qualify-record`, and `account-status`. The older `capture-*`, `normalize`, `qualify`, and `status` commands for savedSearchId 2006360906 remain available for later contact selection. The workflow must not touch Jobs capture tables, use live browser control from the CLI, or write live state during smoke checks. Migration 18 owns the `salesnav_staffing_*` schema; migration 19/20 own account capture and lane qualification; migration 21 owns kept-account people capture, selection, and local review tables. People capture is company-scoped to a kept account and remains read-only with no HubSpot or outreach authority. Keep body, endpoint, page, filter, and keyword validation strict, retain raw source evidence, and emit stable JSON envelopes. Account capture uses `scripts/linkedin-salesnav-account-chrome-helper.mjs`; it owns no cookies, headers, tabs, or browser lifecycle.
