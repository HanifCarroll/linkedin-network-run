# Python LinkedIn Tools Cutover Review Summary

Date: 2026-06-24
Branch: `python-port/orchestrator-scaffold`

## Current State

The Python `linkedin-tools` monorepo port is technically ready for Hanif review.
The Go/JavaScript implementation has not been archived, and no real LinkedIn
sends or withdrawals were performed during verification.

## Integrated Scope

- Shared schemas, storage helpers, CSV/JSONL helpers, report helpers, and URL
  canonicalization.
- Python Playwright browser/session layer using the logged-in `LinkedIn` Chrome
  profile and existing CDP session reuse.
- Network automation controller, browser-backed capture/audit/send paths,
  acceptance tracking/follow-ups, pending cleanup, reservoir capture, stale
  candidate draining, and top-up reconciliation.
- Recruiter/agency outreach capture, account capture, agency pool, drafting,
  messageability validation, guarded message sending, dashboard/reporting, and
  consolidated review UI routing.
- Recommend-only opportunity intelligence and comment extraction, including the
  full compatibility command surface, provider CSV contracts, source
  experiments, calibration artifacts, review queues, and source decision
  artifacts.
- Python replacements for active local automation dependencies that previously
  called standalone `salesnav-*.js` artifact producers: saved-search
  resolution, acceptance outcome checks, accepted-candidate research, pending
  cleanup audits, and pending invitation capture.
- Legacy importers and compatibility commands for `linkedin-network-run`,
  `recruiter-agency-outreach`, and `linkedin-opportunity-intel`; network and
  recruiter/agency imports now promote usable Python app state under the new
  `linkedin-tools` state root.

## Recent Closure Commits

- `f620d70 feat: close remaining network command parity`
- `a46869d feat: promote legacy state for cutover`
- `bc3b923 feat: preserve salesnav capture api enrichment`
- `c807cf9 feat: expand opportunity command parity`
- `e1e3ca6 feat: complete recruiter browser parity`
- `83f4bc5 feat: add browser and command parity follow-up`

## Verification

- PASS: `uv run pytest` (`109 passed`, one existing FastAPI/Starlette warning)
- PASS: `uv run ruff check apps packages tests`
- PASS: `uv run mypy apps packages tests`
- PASS: source-faithful extraction grep. Remaining hits are intentional legacy
  migration names, explicit score fields/classifiers, UI headings/templates,
  documented grep examples, and explicit browser/menu fallback terminology.
- PASS: compatibility command coverage check. Known network, recruiter/agency,
  and opportunity commands all delegate to Python app ports; only
  `import-legacy-state` remains in the migration shim.
- PASS: temp-root legacy import rehearsal. Real local network and
  recruiter/agency state imported without warnings and regenerated Python
  network status and recruiter/agency report.
- PASS: focused automation-cutover tests for `saved-searches`, `acceptance
  check`, accepted research inside `acceptance draft-followups --session`,
  `pending-cleanup audit`, and `pending-cleanup capture`.

## Live Dry-Run Evidence

- Network `reconcile-audit`, `capture`, and `send-next --dry-run` passed through
  the existing Playwriter CDP endpoint.
- Accepted follow-up dry-run artifact:
  `/tmp/linkedin-tools-live-dryrun.84HHg5/followup-dryrun/001-afu_290bef9f8226.json`
- Pending cleanup withdrawal dry-run artifact:
  `/tmp/linkedin-tools-live-dryrun.84HHg5/withdraw-dryrun-actual-age/001-withdraw-result.json`
- Recruiter/agency people capture artifact:
  `/tmp/recruiter-agency-live-dryrun.h4e40B/capture-live/001-capture-page.json`
- Recruiter/agency message dry-run artifact:
  `/tmp/recruiter-agency-live-dryrun.h4e40B/message-dryrun/001-lead_d17f3936.json`
- Recruiter/agency account capture artifact:
  `/tmp/recruiter-agency-live-dryrun.h4e40B/account-capture-live/001-ASAP---Agency-Accounts-Product-Studio-accounts.json`

## Remaining Cutover Decision

The only unchecked item in `docs/cutover-checklist.md` is:

- Hanif approves cutover.

The active local automation replacement map is documented in
`docs/cutover-automation-inventory.md`; the live prompts should be edited only
after approval.

After approval, the old Go/JavaScript implementation can be archived or frozen
according to the cutover checklist.
