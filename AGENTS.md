# AGENTS.md

## Scope

This is the canonical Bun/TypeScript `linkedin-tools` repository, promoted
2026-08-11 from the former `linkedin-tools-next` path. The Python
implementation it replaced is retired; migration from legacy state is dry-run
and proposal-only.

The product workflows are deterministic daily networking, read-only content
analytics export, and interactive jobs collection/outreach. The `jobs` workflow
collects postings from a LinkedIn jobs search (read-only XHR + direct-view
reads), stores them locally with their listed hiring team, and sends drafted
messages to hiring team members only with the explicit `--allow-send` flag.

## Invariants

- Use Bun, strict TypeScript, one package, `bun:sqlite`, and one `linkedin-tools` binary.
- Emit stable `{ "ok": true, "data": ... }` or `{ "ok": false, "error": ... }` JSON envelopes.
- Reject unknown, duplicate, conflicting, malformed, and out-of-range arguments before dispatch.
- Playwriter is the only browser boundary. Never add direct Playwright, direct CDP, Chrome control,
  browser leases, or cross-automation locks.
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

Smoke checks must use fakes or temporary state. Do not invoke a live browser,
LinkedIn, launchd installation, live automation, or legacy writes without a
separate explicit request.
