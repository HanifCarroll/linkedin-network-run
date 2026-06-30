# Implementation Status

Last updated: 2026-06-25

`linkedin-tools` is the active local toolchain for LinkedIn networking,
recruiter/agency/advisor outreach, opportunity intelligence, comment extraction, and
review workflows. Runtime state lives under
`~/Library/Application Support/linkedin-tools/`.

## Active Workflows

- `linkedin-tools network`: deterministic Sales Navigator connection-request
  controller, sent-page audit reconciliation, source reservoirs, acceptance
  tracking, accepted follow-ups, and pending-invitation cleanup.
- `linkedin-tools recruiter-agency`: recruiter, agency, and advisor sourcing,
  lead capture, drafting, dashboards, guarded message dry-runs, and guarded
  message sends.
- `linkedin-tools opportunity`: recommend-only source registry, query packs,
  post queues, provider imports, capture batches, scoring, and review exports.
- `linkedin-tools comments`: browser-backed and saved-HTML LinkedIn comment
  extraction.
- `linkedin-tools ui`: local review UI for opportunities, networking state,
  recruiter/agency/advisor state, browser artifacts, and guarded actions.

## Shared Runtime

- Browser automation uses Python Playwright through the configured local
  Chrome/Playwriter CDP path.
- App state is stored in app-owned directories under the shared state root.
- Browser artifacts are written to explicit output directories or app-owned
  state for auditability.
- Shared packages own browser/session helpers, Sales Navigator primitives,
  storage, report rendering, UI helpers, experiment gates, schemas, config, and
  progress reporting.

## Safety Boundaries

- Connection requests and accepted follow-ups are controller-owned network
  workflows.
- Recruiter/agency outreach sends drafted messages only and must not send
  connection requests.
- Opportunity intelligence is recommend-only.
- Real sends require `--allow-send`.
- Real pending-invitation withdrawals require `--allow-withdraw`.
- Uncertain browser state, LinkedIn network refusal, or possible real sends
  require audit reconciliation before declaring success.

## Verification

Use the narrowest relevant target for a change, then broaden when shared
behavior moves:

```sh
uv run pytest tests/network_automation/test_network_automation.py -q
uv run pytest tests/test_recruiter_agency_outreach.py -q
uv run pytest -q
uv run ruff check .
uv run mypy apps packages tests
```
