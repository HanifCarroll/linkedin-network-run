# Python LinkedIn Tools Cutover Checklist

Do not archive the current Go/JavaScript implementation until all items pass.

- [x] Every current CLI command has a Python equivalent.
- [x] Every current browser script has a Python Playwright equivalent or an
      approved consolidated replacement.
- [x] Old state importers preserve data and do not mutate old state.
- [x] Compatibility commands work during migration.
- [x] Local UI exposes all required PRD views.
- [x] Send and withdraw safety tests pass.
- [x] Browser dry-runs pass.
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
- [x] Live accepted-follow-up `send-followup --dry-run` passed on 2026-06-24
      with artifact
      `/tmp/linkedin-tools-live-dryrun.84HHg5/followup-dryrun/001-afu_290bef9f8226.json`.
- [x] Live pending-cleanup `withdraw-next --dry-run` passed on 2026-06-24 with
      artifact
      `/tmp/linkedin-tools-live-dryrun.84HHg5/withdraw-dryrun-actual-age/001-withdraw-result.json`.
- [x] Python people capture preserves Sales Navigator API response enrichment
      from `/sales-api/salesApiLeadSearch`, including artifact-level API
      metadata, per-row API state, API-derived profile URLs, and
      `pendingInvitation` menu-state classification with menu fallback.

## Recruiter/Agency Browser And Command Slice

- [x] State-backed Python CLI parity exists for `accounts`, `lead show`,
      `queue`, `last-run`, `recommend-next-run`, `revise`, `send-ready`,
      `reject`, and `report`.
- [x] Legacy `recruiter-agency-outreach` compatibility now delegates
      implemented recruiter/agency commands to the Python app CLI.
- [x] `send-ready` preserves the real-send gate and applies explicit
      structured non-dry-run result artifacts only.
- [x] Live recruiter/agency people capture is wired through the shared
      SalesNav people-capture adapter and passed on 2026-06-24 with artifact
      `/tmp/recruiter-agency-live-dryrun.h4e40B/capture-live/001-capture-page.json`.
- [x] Live recruiter/agency `send-message --dry-run` is wired through the
      Python message adapter and passed on 2026-06-24 with artifact
      `/tmp/recruiter-agency-live-dryrun.h4e40B/message-dryrun/001-lead_d17f3936.json`.
- [x] Live recruiter/agency account capture is wired through the Python
      account-capture adapter and passed on 2026-06-24 with artifact
      `/tmp/recruiter-agency-live-dryrun.h4e40B/account-capture-live/001-ASAP---Agency-Accounts-Product-Studio-accounts.json`.
- [x] Legacy `serve` behavior is replaced by the consolidated Python review UI
      and compatibility routing delegates `recruiter-agency-outreach serve`.

## Opportunity Intel Command Slice

- [x] `linkedin-opportunity-intel` compatibility delegates every recommend-only
      command to the Python opportunity app, except `import-legacy-state`, which
      remains in the read-only migration shim.
- [x] Source/query inspection, post queue generation, provider CSV contracts,
      batch preparation/status, experiment/spike runs, artifact review commands,
      merge/export commands, and recommend-only placeholder commands are wired.
- [x] Tests assert the native opportunity parser covers the compatibility
      command surface.
