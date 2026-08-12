# LinkedIn Tools

A single-package Bun/TypeScript implementation for two workflows:

- Daily LinkedIn network automation.
- Weekly seven-day content analytics export.

Promoted 2026-08-11 from `linkedin-tools-next` to the canonical repository
path. The former Python implementation (acceptance tracking, Relationship
Radar, welcome messages, pending cleanup, recruiter outreach, opportunity
intel, review UI) was retired on the same date; its code remains in git
history before the promotion commit.

## Operating contract

Networking uses only these exact Sales Navigator saved searches:

| Source | Saved search ID | Preferred allocation |
| --- | --- | ---: |
| Consulting - HubSpot Agency Ops | 1980844577 | 15 |
| Consulting - HubSpot B2B RevOps | 1980870185 | 15 |

The daily target is exactly 30 durable requests, shared across the two configured sources
(15/15, with bidirectional carryover). Completion requires exactly 30 durable, zero provisional
or planned attempts, and a complete final sent-list reconciliation. A possible send is reserved
until reconciliation proves its outcome.

Playwriter is the only browser boundary. The repository does not directly import Playwright, use
direct CDP, use a Chrome-control fallback, or implement browser leases or cross-automation locks.
Networking and analytics have distinct, command-bound Playwriter sessions and non-overlapping
schedules.

## Install and verify

```sh
bun install
bun run test:cli
bun run check:cli
bun run typecheck
bun run build
bun run smoke
```

The package exposes `linkedin-tools` from `dist/cli.js`. It is not installed globally by this
repository.

## Configuration

Default fresh state:

```text
~/Library/Application Support/linkedin-tools-next/
  linkedin-tools.db
  downloads/
  logs/
  receipts/
  reports/
```

Supported environment variables:

- `LINKEDIN_TOOLS_STATE_DIR`
- `LINKEDIN_TOOLS_PLAYWRITER_BIN`
- `LINKEDIN_TOOLS_NETWORK_SESSION`
- `LINKEDIN_TOOLS_ANALYTICS_SESSION`
- `LINKEDIN_TOOLS_ANALYTICS_ACCOUNT`
- `LINKEDIN_TOOLS_ANALYTICS_DOWNLOAD_ROOTS`

Run the local-only prerequisite check first:

```sh
bun run src/cli.ts --json doctor \
  --network-session 7 \
  --analytics-session 8
```

Doctor checks Bun, the configured Playwriter executable and version, configured session IDs, active
session listing, state-directory writability, and SQLite migrations. It does not navigate to or open
LinkedIn.

## Commands

```sh
linkedin-tools --json doctor
linkedin-tools --json network status
linkedin-tools --json network report
linkedin-tools --json network tick --allow-send --batch-size 5 \
  --target 30 --max-real-sends 30 --session auto
linkedin-tools --json network reconcile --session auto
linkedin-tools --json network incident-status
linkedin-tools --json network incident-clear \
  --account-access-confirmed --warning-cleared-confirmed \
  --reason "weekly limit cleared after manual review"

linkedin-tools --json analytics export \
  --session auto \
  --period previous-7-days \
  --account Hanif \
  --download-root /Users/hanifcarroll/Downloads \
  --out '/absolute/path/content-{startDate}-{endDate}.xlsx'

linkedin-tools --json migration dry-run \
  --source-root '/Users/hanifcarroll/Library/Application Support/linkedin-tools/network-automation'
```

`network tick` refuses to dispatch without the exact `--allow-send` flag. One invocation continues
serially until the run is Done or returns a typed checkpoint or terminal blocker. `--batch-size` is
capped at five, the target is fixed at 30, and `--max-real-sends` cannot exceed 30. The controller
walks each saved-search list in order: it opens the list, scrolls to load every row, sends a
connection request to each connectable person (5s pacing between sends), and paginates until the
source's share or the daily target is met, then moves to the next list. After the send phase it
waits 60 seconds for invitations to settle, audits the sent-invitations page, and confirms which
sends appear. Unconfirmed sends after the settle wait are marked proven-no-send, and the tick sends
more to top up until 30 are confirmed on the sent page, then finishes. Audits cannot be disabled.

When a new local day starts, the controller checks older active runs first. It parks an older run as
missed only when it has no planned or possible sends, then starts the new day. If an older run still
has a planned or possible send, the controller returns `NETWORK_PRIOR_DAY_NEEDS_AUDIT` before any
browser session is created; reconcile that exact earlier date before sending on the new day.

`analytics export` accepts either `--period previous-7-days` or exact `--start-date` and
`--end-date`, never both. The range must contain exactly seven inclusive days. Repeat
`--download-root` for more than one native download location. Current optional export controls are
`--receipt`, `--recovery-state`, `--poll-interval-ms`, and `--max-polls`.

`migration dry-run` is proposal-only. It reads legacy state and exposes no apply/import command.

Unknown, duplicate, conflicting, malformed, or out-of-range flags fail before an operation runs.
Paths for state, exports, downloads, receipts, recovery state, Playwriter, and legacy sources must
be absolute.

A shared incident gate guards every browser operation. Fatal account signals (weekly limit, rate
limit, unusual activity, checkpoint or security-verification walls, login walls) open
`linkedin-incident.json` in the state directory and block all further browser automation with
`INCIDENT_ACTIVE` (exit 7) until a human clears it. Clearing requires both confirmation flags and a
reason; partial clears fail with exit 2. `doctor` reports an active incident as a failing check.

For browser-capable commands, `--session auto` uses the exact workflow binding in
`sessions/network.json` or `sessions/analytics.json`. It reuses the bound ID only while Playwriter
reports it active. A missing or stale binding creates and verifies a new persistent session through
Playwriter's supported client API, then writes the binding atomically. Auto mode never adopts an
unbound active session or shares one session between workflows. Numeric session IDs remain
supported for explicit operator control. Tests fake this boundary and never create a live session.

## JSON contract

With `--json`, stdout contains exactly one envelope and diagnostics use stable error codes.

```json
{"ok":true,"data":{"command":"network status","state":"not_started"}}
```

```json
{"ok":false,"error":{"code":"SEND_NOT_AUTHORIZED","message":"network tick requires the explicit --allow-send flag"}}
```

## Scheduling templates

Uninstalled templates are in `launchd/`:

- `com.hanif.linkedin-tools.network.plist`: one completion-capable daily run at 09:05.
- `com.hanif.linkedin-tools.analytics.plist`: Sunday at 06:15.

Both templates are configuration-complete and use `--session auto`; they contain no replacement
tokens. They use distinct windows and log files, an explicit working directory and PATH, and no
`KeepAlive` or lease. This repository does not install or load them.

## Safety

Tests and `bun run smoke` use dependency injection, temporary state, and fake commands. They do not
exercise a live browser, LinkedIn, launchd, the old Python repository, or legacy state.
