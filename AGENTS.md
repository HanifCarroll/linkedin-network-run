# AGENTS.md

## Scope

This is the Bun/TypeScript `linkedin-tools` repository. Its workflows are content analytics export, interactive Jobs intake/outreach, and Sales Navigator research/account capture. Jobs and Sales Navigator use caller-owned Codex Chrome handoffs; the CLI stores bounded evidence and guarded decisions in SQLite. Analytics export and `jobs check` use Playwriter. Generic invitation campaigns belong to `contract-outreach` and its policy.

Respect an explicit browser choice. If a required helper cannot run on that surface, report the limitation without switching browsers or control methods. Sending requires exact-copy approval and the workflow's explicit `--allow-send` gate; CLI capability alone is not authority.

## Invariants

- Use Bun, strict TypeScript, one package, `bun:sqlite`, and one `linkedin-tools` binary.
- Emit stable `{ "ok": true, "data": ... }` or `{ "ok": false, "error": ... }` JSON envelopes.
- Reject unknown, duplicate, conflicting, malformed, and out-of-range arguments before dispatch.
- LinkedIn Jobs capture, direct-page enrichment, outreach, and Sales Navigator research use caller-owned Codex Chrome handoffs plus CLI SQLite ingest. Direct enrichment is request-first: arm CDP before navigation, retain only the target Document and aboutTheJob/aboutTheCompanyForJobDetails/peopleWhoCanHelp response bodies, parse locally, then verify with stable DOM reads. Jobs outreach uses jobs send-prepare/send-record; contract outreach additionally uses jobs contract-outreach-prepare/record. Never add CLI browser control, browser leases, or cross-automation locks.
- Never replace a possible send or infer failure from absence. Jobs send-prepare and contract-outreach-prepare reserve one prepared attempt before the caller acts; a contract-outreach handoff without an allowlisted 2xx invitation request, its exact recipient URN, and the invitation dialog closing remains possible and blocks retry until reconciled. The invitation endpoint must match the exact allowlist in `src/jobs/contract-outreach.ts`. `proven_no_send` is the only safe release; there is no failed terminal state.
- Migration never applies or writes legacy state.
- `jobs normalize` processes captured pages one SQLite transaction at a time, deduplicates only by LinkedIn job ID, records page/job provenance, and resumes from completed pages. It never filters by location or fit and never enriches.
- `jobs filter` requires explicit `--terms` JSON; it never imports or activates `JOB_SEARCH_TERMS`, never uses location, and scopes filtering to jobs observed in the requested run. Unknown freshness is kept and reported. Enrichment/detail may optionally use `--run-id` to process only that run's kept jobs.
- Jobs triage precedes human review: every kept job with hiring-team people remains available; an agent records strong|possible|weak plus an evidence-backed fit brief under `jobs-triage-v1-20260819`. Triage never rejects or changes review, and weak remains reviewable. Pending review shows only triaged jobs; previously decided or sent history remains visible. Approval may exist without a draft, but send still requires drafted + approved + `--allow-send`.
- Jobs application-followup roles require the durable `applied_at`/`application_url` checkpoint recorded by `jobs applied`; direct roles do not. The route remains computed by `outreachKindFor`, never persisted. Sending still requires drafted + approved + `--allow-send` and unique recipients.
- `jobs draft-next [--id JOB_ID] [--state-dir]` is a deterministic, read-only companion handoff. It returns one kept, triaged, approved opportunity per usable first hiring-team person, skips stored drafts and sent groups, reports `blockedApplications` for unapplied application-followup roles, and emits stored evidence plus route-specific writing instructions. The companion adapts the packet's canonical connection-note template to at most 200 characters and stores a distinct later follow-up message; they are never reused across channels. It never calls an LLM, stores a draft, approves, sends, accesses a browser, or touches HubSpot. The next action is `jobs draft`, which resets review to `needs_review`.
- `jobs application-next [--id JOB_ID] [--state-dir]` is the smallest approved contract/application-followup browser handoff. It emits one posting/application URL, stored role/company evidence, the required `jobs applied` checkpoint, and explicit stops for unknown questions, uploads, assessments, compensation, legal/eligibility, and submit. It is read-only, caller-owned, never fills a universal form, and never submits to an external ATS.
- HubSpot import is an agent handoff: `jobs hubspot-next` emits the deterministic route-aware lookup-before-create packet and `jobs hubspot-record` stores IDs and association completion. The CLI holds no HubSpot credentials, performs no CRM request, and never treats task creation as send authority.
- Instantly email is an agent handoff: `jobs instantly-next` emits a deterministic official API v2 SuperSearch/enrollment/status packet only for an approved kept contract-outreach prospect with a complete HubSpot receipt; `jobs instantly-record` stores a durable receipt. The CLI never calls Instantly, stores credentials, sends email, schedules follow-ups, or accepts ambiguous email; campaign cadence and stop-on-reply remain Instantly-owned.
- `jobs followup-next` is a read-only post-Day-1 HubSpot task packet. Automatic selection skips completed follow-up receipts; `--id` may inspect one. A completed Instantly email/lead produces only a monitoring task; no-email produces LinkedIn checks for days 5–7, 8–10, and 12–14 with reply/connection gates. Every task has the stable body marker, exact HubSpot task properties, owner `96636780`, all three associations, and due timestamps from the later Day 1 receipt at offsets 5/8/12. `jobs followup-record` stores explicit company/contact/deal-associated task IDs. The CLI never calls HubSpot, mutates CRM, schedules locally, or duplicates Instantly cadence; any reply stops/completes the remaining sequence.
- Keep work within the requested analytics, Jobs or Sales Navigator workflow; generic invitation campaigns use `contract-outreach`.

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

Smoke checks must use fakes or temporary state. Live browser work, LinkedIn actions, scheduler installation and state migration require a separate explicit request.

## Sales Navigator staffing

`salesnav staffing` and `salesnav studio` are read-only, caller-owned Chrome account-capture workflows. Account capture enters through `/sales/home` and a visible account-filter or saved-search action; the captured request, not the address-bar form, must match the run's lane, filters, and keywords. Studio accepts exactly two approved US 11–50 IT/design Boolean searches. Runs persist lane, canonical search config, and keyword query. Qualification is scoped by lane plus globally deduplicated organization; studio keeps require a recorded firm-research row with LinkedIn/website URLs, services, one concrete fact, unknowns, and review timestamp. Neither lane performs HubSpot writes or sends. Staffing begins with a United States account search fixed to industry 104 and headcount buckets C and D; each run is bound to its exact non-empty keyword query. Its commands are `account-capture-start`, `account-capture-ingest`, `account-capture-finish`, `account-normalize`, `account-qualify-next`, `account-qualify-record`, and `account-status`. The saved-search `capture-*`, `normalize`, `qualify`, and `status` commands for savedSearchId 2006360906 remain available for later contact selection. The workflow must not touch Jobs capture tables, use live browser control from the CLI, or write live state during smoke checks. Migration 18 owns the `salesnav_staffing_*` schema; migration 19/20 own account capture and lane qualification; migration 21 owns kept-account people capture, selection, and local review tables. People capture is company-scoped to a kept account and remains read-only with no HubSpot or outreach authority. Keep body, endpoint, page, filter, and keyword validation strict, retain raw source evidence, and emit stable JSON envelopes. Account capture uses `scripts/linkedin-salesnav-account-chrome-helper.mjs`; it owns no cookies, headers, tabs, or browser lifecycle.
