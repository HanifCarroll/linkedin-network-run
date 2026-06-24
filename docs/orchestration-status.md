# Python Port Orchestration Status

Last updated: 2026-06-24

## Orchestrator Branch

- Branch: `python-port/orchestrator-scaffold`
- Baseline scaffold commit: `84a6fc0`
- Latest cutover-prep commit: `a46869d`
- Follow-up integration: network browser adapter, recruiter/agency command
  parity, opportunity command parity, cutover audit, and legacy state promotion
  are included after the root routing commit.

## Integrated Workstreams

### Thread 1: Shared Foundation

- Thread ID: `019efa1d-2c92-7af3-9b8e-5ec2e7398e02`
- Worktree: `/Users/hanifcarroll/.codex/worktrees/f591/linkedin-network-automation`
- Source commit: `99d49d0`
- Integrated commit: `13c0f5b`
- Handoff: `docs/handoffs/shared-foundation.md`
- Scope integrated:
  - Shared Pydantic schemas.
  - LinkedIn URL canonicalization helpers.
  - SQLite, JSONL, and CSV helpers.
  - Report helpers.
  - Experiment metrics and gates.
  - Shared fixtures and tests.

### Thread 2: Browser Automation Layer

- Thread ID: `019efa1d-5ed7-7291-aa6e-23437edf4a4f`
- Worktree: `/Users/hanifcarroll/.codex/worktrees/542a/linkedin-network-automation`
- Source commit: `22683e6`
- Integrated commit: `c7dc53b`
- Handoff: `docs/handoffs/browser-automation.md`
- Scope integrated:
  - Chrome `LinkedIn` profile config.
  - Playwright session/page reuse helpers.
  - Browser artifact writers.
  - Browser blocked-state classifier.
  - Guarded real-action primitives.
  - Sales Navigator capture/audit/message primitives.

### Thread 3: Opportunity Intel And Comment Extractor

- Thread ID: `019efa1d-854b-7903-a838-8f058b82da1e`
- Worktree: `/Users/hanifcarroll/.codex/worktrees/e350/linkedin-network-automation`
- Source commit: `ace709c`
- Integrated commit: `16fb1c4`
- Handoff: `docs/handoffs/opportunity-intel-comment-extractor.md`
- Scope integrated:
  - Source registry and query pack contracts.
  - Provider/manual CSV import contract and dedupe.
  - Direct-buyer ranking with recruiter/staffing noise rejection.
  - Post queue, source experiments, proof gate, calibration, action plan, and
    review queue exports.
  - Explicit-selector LinkedIn comment extractor and `raw_comments.jsonl`
    contract.

### Thread 4: Network Automation Port

- Thread ID: `019efa1d-ac54-74f3-80a2-f82de6401f37`
- Worktree: `/Users/hanifcarroll/.codex/worktrees/8c13/linkedin-network-automation`
- Source commit: `3ff9fda`
- Integrated commit: `ce1cdf5`
- Handoff: `docs/handoffs/network-automation.md`
- Scope integrated:
  - Run controller, plan/status/report, audit import, capture import, source
    tuning, and reservoir operations.
  - Guarded connection send path and browser fixture interface.
  - Acceptance tracking, accepted follow-up drafts, guarded follow-up sends,
    and pending cleanup guarded withdrawals.
  - Read-only old-state inspection hook and parity fixtures/tests.

### Thread 5: Recruiter/Agency Outreach Port

- Thread ID: `019efa1d-d601-7570-97a5-0a5c1fd97a02`
- Worktree: `/Users/hanifcarroll/.codex/worktrees/e8e0/linkedin-network-automation`
- Source commit: `7e56380`
- Integrated commit: `0f2864a`
- Handoff: `docs/handoffs/recruiter-agency-outreach.md`
- Scope integrated:
  - Recruiter and agency capture/import.
  - Account-first agency sourcing and agency pool reporting.
  - Sales Navigator identity-gated agency contact promotion.
  - Draft generation, messageability validation, dashboard/reporting, and
    guarded message-send state transitions.
  - `run-daily` no-send behavior and parity fixtures/tests.

### Thread 6: Review UI

- Thread ID: `019efa1e-05c6-7570-a1db-bc8786b62af5`
- Worktree: `/Users/hanifcarroll/.codex/worktrees/ee92/linkedin-network-automation`
- Source commit: `5139106`
- Integrated commit: `e814b6d`
- Handoff: `docs/handoffs/review-ui.md`
- Scope integrated:
  - FastAPI review server.
  - Jinja templates, HTMX partials, and Alpine presentation state.
  - Opportunity, network, recruiter/agency, browser, and guarded-action review
    screens.
  - Local token enforcement and UI action registry.
  - UI safety tests.

### Thread 7: Migration And Compatibility

- Thread ID: `019efa1e-2818-7282-ac5d-b322717c080b`
- Worktree: `/Users/hanifcarroll/.codex/worktrees/972b/linkedin-network-automation`
- Source commit: `6e48328`
- Integrated commit: `29ae3a0`
- Handoff: `docs/handoffs/migration-compatibility.md`
- Scope integrated:
  - Read-only legacy importers for network, recruiter/agency, and opportunity
    artifacts.
  - Immutable read-only SQLite snapshots for old recruiter/agency state.
  - Temporary compatibility shims for `linkedin-network-run`,
    `recruiter-agency-outreach`, and `linkedin-opportunity-intel`.
  - Real-action flag blocking in compatibility shims.

## Orchestrator-Owned Integration

- Top-level `linkedin-tools` dispatch now routes to the integrated Python app
  CLIs.
- Legacy command names keep `import-legacy-state` and delegate implemented
  commands to the app ports. Compatibility now delegates every known network,
  recruiter/agency, and opportunity command to the Python app ports, leaving
  only `import-legacy-state` in the migration shim.
- Legacy importers now preserve raw old artifacts and promote usable Python app
  state for network and recruiter/agency under
  `~/Library/Application Support/linkedin-tools/`.
- Runtime package data includes opportunity JSON contracts and review UI
  templates/static assets.
- Network automation now has Python Playwright-backed command paths for
  `capture`, `reconcile-audit`, `send-next`, `send-guarded`, accepted
  follow-up sends, and pending cleanup withdrawals. These are covered by fake
  browser tests. Live `reconcile-audit`, `capture`, `send-next --dry-run`,
  accepted follow-up `send-followup --dry-run`, and pending cleanup
  `withdraw-next --dry-run` passed from a temporary state directory by
  attaching to the existing Playwriter CDP endpoint at
  `ws://127.0.0.1:19988/cdp`.
- Active local automation JS artifact producer dependencies now have Python
  command replacements: `saved-searches`, `acceptance check`,
  `acceptance research`, `acceptance draft-followups --session`,
  `pending-cleanup audit`, and `pending-cleanup capture`.
- Python Sales Navigator people capture now preserves the old JavaScript
  capture API-response enrichment from `/sales-api/salesApiLeadSearch`,
  including top-level API metadata, per-row API state, API-derived profile
  URLs, and `pendingInvitation` menu-state classification with exact menu
  fallback.
- Network command parity now includes `drain-stale-candidates` and
  `top-up-reconcile`; top-up reconciliation uses the Python browser client,
  preserves the real-send `--allow-send` gate, records audit-top-up send
  results, and re-audits through the Python audit path.
- Recruiter/agency outreach now exposes the remaining state-backed command
  surface in Python and compatibility routing delegates those implemented
  commands. Live recruiter/agency people capture, account capture, and
  `send-message --dry-run` passed from a temporary SQLite state directory by
  attaching to the existing Playwriter CDP endpoint.
- Opportunity-intel now exposes the full recommend-only command surface named
  by the compatibility shim, including source/query inspection, post queues,
  provider CSV contracts, batch preparation/status, experiment/spike runs,
  review/calibration/gate/action artifacts, merge/export commands, and
  recommend-only profile/source placeholders.

## Integrated Verification

After integrating Threads 1 through 7, root routing, the follow-up
browser/command parity slices, opportunity command parity, and cutover state
promotion:

- PASS: `uv run pytest` (`109 passed`, one existing FastAPI/Starlette
  deprecation warning)
- PASS: `uv run ruff check apps packages tests`
- PASS: `uv run mypy apps packages tests`
- PASS: temp-root import rehearsal for real local network and recruiter/agency
  state, followed by Python `network status --json` and
  `recruiter-agency report --json`.
- PASS: source-faithful grep required by `AGENTS.md`. Remaining hits are
  existing legacy migration names, explicit score fields/classifiers, UI
  headings/templates, and documented handoff grep examples; no new weak
  extraction fallback remains in the browser adapters.

## Next Orchestration Steps

1. Complete Hanif review and approval before archiving the Go/JavaScript
   implementation.
