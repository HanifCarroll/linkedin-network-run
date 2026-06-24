# Python LinkedIn Tools Cutover Checklist

Do not archive the current Go/JavaScript implementation until all items pass.

- [ ] Every current CLI command has a Python equivalent.
- [ ] Every current browser script has a Python Playwright equivalent or an
      approved consolidated replacement.
- [x] Old state importers preserve data and do not mutate old state.
- [x] Compatibility commands work during migration.
- [x] Local UI exposes all required PRD views.
- [x] Send and withdraw safety tests pass.
- [ ] Browser dry-runs pass.
- [x] Opportunity-intel remains recommend-only.
- [ ] Hanif approves cutover.

## Network Automation Browser Slice

- [x] `send-next` and `send-guarded` construct a Python Playwright browser
      client when no fixture result is provided.
- [x] Pending cleanup `withdraw-next` constructs the Python browser client and
      preserves the `--allow-withdraw` real-action gate.
- [x] Accepted follow-up dry-run/send paths construct the Python browser client
      and preserve the stored-result application path.
- [x] `reconcile-audit`, active-run `capture`, and `reservoir capture` have
      Python browser-backed CLI paths that feed existing state import/apply
      functions.
- [x] Live network `reconcile-audit`, `capture`, and `send-next --dry-run`
      passed against a temporary state directory on 2026-06-24 through the
      existing Playwriter CDP endpoint at `ws://127.0.0.1:19988/cdp`.
- [ ] Live pending-withdrawal and accepted-follow-up browser dry-runs are not
      exercised yet.
- [ ] Python capture does not yet include the old JS Sales Navigator API
      response enrichment; it uses exact row/profile/menu selectors only.

## Recruiter/Agency Browser And Command Slice

- [x] State-backed Python CLI parity exists for `accounts`, `lead show`,
      `queue`, `last-run`, `recommend-next-run`, `revise`, `send-ready`,
      `reject`, and `report`.
- [x] Legacy `recruiter-agency-outreach` compatibility now delegates
      implemented recruiter/agency commands to the Python app CLI.
- [x] `send-ready` preserves the real-send gate and applies explicit
      structured non-dry-run result artifacts only.
- [ ] Live recruiter/agency browser capture and message-send runners are not
      wired. `capture` and `capture-accounts` fail explicitly until a safe
      concrete browser runner replaces the old JS scripts.
- [ ] Legacy `serve` behavior is not ported.
