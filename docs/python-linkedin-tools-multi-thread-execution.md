# Python LinkedIn Tools Multi-Thread Execution Plan

## Status

Draft for Hanif review. Do not start implementation threads until the PRD and
this execution plan are approved.

## Purpose

Use multiple Codex chats to port the existing LinkedIn tools into the new
`linkedin-tools` Python monorepo while keeping architecture, safety boundaries,
state migration, and final integration under one primary orchestration thread.

## Operating Model

The primary thread is the orchestrator. It owns:

- Final architecture.
- Workstream sequencing.
- Cross-thread decisions.
- Shared contracts.
- Merge order.
- Integration tests.
- Cutover readiness.
- Final acceptance audit.

Subthreads own scoped implementation goals. They should not modify files outside
their assigned ownership boundaries unless the primary thread explicitly
changes the assignment.

## Shared Rules

1. The PRD is the source of truth.
2. The new monorepo is `linkedin-tools`.
3. This is a full port; no partial cutover.
4. Use Python, `uv`, SQLite, FastAPI/Jinja/HTMX/Alpine, and Playwright.
5. Use the logged-in Chrome profile named `LinkedIn`.
6. Preserve send/withdraw safety gates.
7. Keep opportunity intelligence recommend-only.
8. Every workstream writes a handoff note before it is considered complete.
9. Every workstream includes tests for its scope.
10. The orchestrator performs final integration and cutover audit.
11. Launch each subthread with the `5.5 extra high fast` setting.
12. Relevant workstreams must read
    `docs/python-linkedin-tools-pre-port-salvage.md` before rebuilding behavior
    covered by the previous Go/JavaScript worktree.

## Proposed Thread Map

### Thread 0: Orchestrator

Goal:

Own the full migration, coordinate subthreads, resolve cross-cutting decisions,
integrate work, and verify acceptance criteria.

Owned paths:

- `README.md`
- `pyproject.toml`
- `uv.lock`
- root CLI entrypoints
- `docs/`
- final integration tests
- final cutover checklist

Deliverables:

- Monorepo scaffold.
- Shared architecture decisions.
- Workstream prompts.
- Integration plan.
- Final acceptance audit.

Completion criteria:

- All subthread handoffs reviewed.
- Full test suite passes.
- Browser dry-runs pass.
- UI safety checks pass.
- Hanif approves cutover.

### Thread 1: Shared Foundation

Goal:

Build common schemas, config, storage, URL utilities, logging, report helpers,
and migration primitives used by all apps.

Owned paths:

- `packages/linkedin_common/`
- `packages/linkedin_storage/`
- `packages/linkedin_reports/`
- `packages/linkedin_experiments/`
- shared fixtures under `tests/fixtures/`

Deliverables:

- Pydantic schemas.
- SQLite connection and migration layer.
- JSONL/CSV helpers.
- Markdown/table report helpers.
- URL canonicalization.
- Shared test fixtures.

Completion criteria:

- Unit tests cover schemas, migrations, URL canonicalization, and report
  helpers.
- Public package APIs are documented in a handoff note.

### Thread 2: Browser Automation Layer

Goal:

Build the Python Playwright browser/session layer and port common LinkedIn /
Sales Navigator browser primitives.

Owned paths:

- `packages/linkedin_browser/`
- `packages/linkedin_salesnav/`
- browser test fixtures

Deliverables:

- Chrome profile config using profile named `LinkedIn`.
- Session/page reuse.
- Artifact capture.
- Screenshot/debug helpers.
- Rate-limit and blocked-state classification.
- Guarded action primitives.
- Sales Navigator capture/audit/message primitives.

Completion criteria:

- Browser layer has dry-run tests or fixture-backed tests.
- Guarded click helpers require explicit real-action approval.
- Handoff documents profile setup and browser assumptions.

### Thread 3: Opportunity Intel And Comment Extractor

Goal:

Port opportunity intelligence and build the comment extractor app inside the
monorepo.

Owned paths:

- `apps/opportunity_intel/`
- `apps/comment_extractor/`
- opportunity-specific tests
- opportunity fixtures

Deliverables:

- Source registry.
- Post discovery.
- Post prioritization.
- Comment extractor from known post URLs.
- Normalizer/deduper.
- Buyer-signal ranker.
- Source experiment reporter.
- Calibration/reporting commands.
- Review queue exports.

Completion criteria:

- Can run a fixture-backed experiment from source registry to source report.
- Comment extractor writes `raw_comments.jsonl`.
- Opportunity modules cannot import send/withdraw modules.
- Recommend-only boundary tests pass.

### Thread 4: Network Automation Port

Goal:

Port `linkedin-network-run` behavior into Python.

Owned paths:

- `apps/network_automation/`
- network automation tests
- network migration fixtures

Deliverables:

- Run controller.
- Plan/status/report.
- Sales Navigator capture import.
- Audit/reconciliation.
- Candidate reservoir.
- Guarded connection send path.
- Acceptance tracking.
- Accepted follow-up drafts and guarded sends.
- Pending cleanup and guarded withdrawals.

Completion criteria:

- Existing command behavior has parity tests.
- Real-send and real-withdraw gates are preserved.
- State import works without mutating old state.
- Dry-run browser paths pass.

### Thread 5: Recruiter/Agency Outreach Port

Goal:

Port `recruiter-agency-outreach` behavior into Python.

Owned paths:

- `apps/recruiter_agency_outreach/`
- recruiter/agency tests
- recruiter/agency migration fixtures

Deliverables:

- Recruiter and agency capture/import.
- Account-first agency sourcing.
- Agency pool.
- Contact promotion.
- Draft generation.
- Messageability validation.
- Dashboard/reporting.
- Guarded message send path.

Completion criteria:

- `run-daily` remains no-send and rejects send flags.
- Guarded sends require explicit approval.
- State remains separate from network automation.
- Parity tests cover sourcing, drafting, dashboard, and send-ready behavior.

### Thread 6: Review UI

Goal:

Build the local UI for reviewing all pertinent system aspects.

Owned paths:

- `apps/review_ui/`
- `packages/linkedin_ui/`
- UI tests and fixtures

Deliverables:

- FastAPI server.
- Jinja templates.
- HTMX partials.
- Alpine local UI state.
- Opportunity review screens.
- Network automation review screens.
- Recruiter/agency review screens.
- Browser/artifact review screens.
- Local access token for state-changing and real-action pages.

Completion criteria:

- UI exposes required PRD views.
- UI does not create new real-action paths.
- Recommend-only pages have no send/connect/withdraw controls.
- Real-action controls call guarded command paths.
- UI tests cover safety boundaries.

### Thread 7: Migration And Compatibility

Goal:

Build state importers and temporary compatibility commands.

Owned paths:

- `packages/linkedin_storage/migrations.py`
- migration commands under relevant apps
- compatibility CLI wrappers
- migration tests

Deliverables:

- Import old `linkedin-network-run` state.
- Import old `recruiter-agency-outreach` state.
- Import old `/tmp/linkedin-opportunity-signals` runs where useful.
- Compatibility command shims:
  - `linkedin-network-run`
  - `recruiter-agency-outreach`
  - `linkedin-opportunity-intel`

Completion criteria:

- Importers do not mutate old state.
- Compatibility commands pass parity smoke tests.
- Handoff documents removal plan after cutover.

## Integration Order

1. Orchestrator scaffolds repo and shared package interfaces.
2. Shared Foundation lands first.
3. Browser Automation lands next.
4. Opportunity Intel and Comment Extractor land after shared/browser contracts.
5. Network Automation and Recruiter/Agency Outreach land in parallel after
   shared/browser contracts.
6. Migration and Compatibility lands after app data models stabilize.
7. Review UI lands after app read models stabilize, with early stubs allowed.
8. Orchestrator runs final integration and acceptance audit.

## Handoff Contract

Each subthread must create:

```text
docs/handoffs/<thread-name>.md
```

Required handoff sections:

- Goal.
- Owned paths changed.
- Commands implemented.
- Data models introduced.
- Tests added.
- Verification run.
- Known gaps.
- Integration dependencies.
- Decisions needing orchestrator approval.

## Conflict Rules

- Shared package API changes require orchestrator approval.
- Browser safety primitives require orchestrator approval.
- Send/withdraw behavior changes require orchestrator approval.
- State schema changes require migration-thread coordination.
- UI may read from app services but must not own core business logic.
- Opportunity-intel cannot depend on send-capable app modules.

## Initial Prompts For Subthreads

Each subthread prompt should include:

- PRD path:
  `docs/python-linkedin-tools-monorepo-prd.md`
- Execution plan path:
  `docs/python-linkedin-tools-multi-thread-execution.md`
- Salvage note path:
  `docs/python-linkedin-tools-pre-port-salvage.md`
- Assigned thread section.
- Launch setting:
  `5.5 extra high fast`
- Owned paths.
- Prohibited paths.
- Current acceptance criteria.
- Required handoff path.

## Final Acceptance Audit

The orchestrator must verify:

1. Every current CLI command has a Python equivalent.
2. Every current browser script has a Python Playwright equivalent or approved
   consolidated replacement.
3. All state importers preserve old state.
4. All compatibility commands work through migration.
5. The local UI exposes all required views.
6. Safety tests pass.
7. Browser dry-runs pass.
8. Opportunity-intel remains recommend-only.
9. The old Go/JavaScript repo can be archived after Hanif approves cutover.
