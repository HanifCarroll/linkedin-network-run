# Network Automation Handoff

## Goal

Port `linkedin-network-run` behavior into the Python monorepo under
`apps/network_automation/` while preserving controller-led state transitions,
real-action safety gates, old-state read hooks, and fixture-backed browser
paths.

## Owned Paths Changed

- `apps/network_automation/`
- `tests/network_automation/`
- `tests/fixtures/network_automation/`
- `docs/handoffs/network-automation.md`

The existing Go and JavaScript implementation under `cmd/`, `internal/`, and
`scripts/` was inspected for parity and not modified.

## Commands Implemented

The Python network CLI now supports:

- Run controller: `start`, `audit`, `import-audit`, `record`,
  `record-send-result`, `record-top-up-result`, `send-next`, `send-guarded`,
  `source-exhausted`, `needs-reaudit`, `resume-blocked`, `import-capture`,
  `next`, `next-candidate`, `candidates`, `plan`, `status`, `report`,
  `finish`, `tune-sources`.
- Acceptance: `acceptance seed`, `seed-history`, `export`, `import`,
  `report`, `draft-followups`, `send-followup`, `dry-run-followups`,
  `send-ready-followups`.
- Reservoir: `reservoir import-capture`, `fill-run`, `report`, `clear`.
- Pending cleanup: `pending-cleanup start`, `import-audit`, `import-capture`,
  `plan`, `next`, `record-withdraw-result`, `withdraw-next`, `status`,
  `report`, `finish`.
- Migration hook: `old-state inspect`.

Real connection sends require `--allow-send`. Real accepted-follow-up sends
require `--allow-send` and prior `dry_run_ready`. Real withdrawals require
`--allow-withdraw`. Run finish requires audit delta equal to target unless
`--force`; pending cleanup finish requires audit delta equal to negative
withdrawn count unless `--force`.

## Data Models Introduced

- Durable run controller models: `Run`, `SourcePlan`, `CandidateEvent`,
  `CandidateObservation`, `SourceCaptureCursor`, `OperatorPlan`,
  `CandidateReservoir`.
- Sales Navigator artifact models: capture, audit, send result, accepted
  research, acceptance outcomes, pending capture, pending withdraw result.
- Acceptance models: `AcceptanceLedger`, invitations, outcome history, reports,
  accepted follow-up draft ledger, send attempts.
- Pending cleanup models: `PendingCleanupRun`, stale invitation observations,
  withdraw events, pending operator plan.
- Browser adapter contract: `BrowserClient`, `UnavailableBrowserClient`, and
  `FixtureBrowserClient` for dry-run/parity tests.

## Tests Added

`tests/network_automation/test_network_automation.py` covers:

- Default source allocation parity.
- Sales Navigator capture import, dedupe, resume cursor, and Sales Profile URN
  to lead URL derivation.
- Guarded connection send real-action gate and fixture-backed recording.
- Audit-backed finish and acceptance seeding.
- Acceptance import identity mismatch downgrade.
- Accepted follow-up draft generation and dry-run-ready send guard.
- Pending cleanup threshold parsing, dry-run withdrawal, record, and
  audit-backed finish.
- Network CLI namespace smoke.
- Read-only old-state inspection.

Fixtures live in `tests/fixtures/network_automation/`.

## Verification Run

- `uv run --extra dev pytest` -> 11 passed.
- `uv run ruff check apps/network_automation tests/network_automation` -> passed.
- `uv run --extra dev mypy apps/network_automation tests/network_automation` -> passed.
- Required weak-inference grep was run:
  `rg -n "slice\(|substring\(|substr\(|visibleText|innerText|document\.title|legacy|fallback|infer|keyword|score|\[class\*=|h1|h2|h3|article|raw_text"`.
  Findings are existing Go/JS parity/browser code plus explicit `fallback`
  source fields in the Python model. The Python port did not add generic DOM
  text extraction, keyword scoring, title fallback, or partial JSON recovery.

## Known Gaps

- Live Python Playwright browser primitives are not implemented in this
  workstream because `packages/linkedin_browser/` and
  `packages/linkedin_salesnav/` are owned elsewhere. Browser-capable commands
  are wired through `BrowserClient`; tests use `FixtureBrowserClient`.
- Storage is file-backed JSON/JSONL to preserve the current controller contract
  while shared SQLite/migration packages are not available. The models are
  ready for migration-thread import, and `old-state inspect` reads old state
  without mutating it.
- Root `linkedin-tools network ...` dispatch and compatibility shim behavior
  are orchestrator/migration-owned paths and were not edited here.
- Live sends, live withdrawals, and live browser dry-runs were not exercised.

## Integration Dependencies

- Browser workstream should implement `BrowserClient` using Python Playwright
  and the logged-in `LinkedIn` Chrome profile.
- Shared storage/migration workstream should decide whether to persist these
  app models directly in SQLite or import from the JSON store as an intermediate
  state.
- Orchestrator should wire the root `linkedin-tools network` namespace and any
  compatibility `linkedin-network-run` shim to `apps.network_automation.cli`.

## Decisions Needing Orchestrator Approval

- Whether temporary fixture flags (`--fixture-result`) stay CLI-visible for
  integration tests or move behind test-only harnesses once the browser
  workstream lands.
- Whether the JSON store remains as a migration bridge or is replaced directly
  by the shared SQLite layer before cutover.
