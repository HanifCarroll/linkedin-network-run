# linkedin-tools

Python monorepo for Hanif's LinkedIn networking, recruiter/agency outreach,
opportunity intelligence, comment extraction, and local review UI tools.

The Python implementation is the active implementation after the approved
2026-06-24 cutover. The old Go/JavaScript implementation is archived under
`archive/legacy-go-js/` for reference.

## Current Status

- Current branch: `python-port/orchestrator-scaffold`
- Python package: `linkedin-tools`
- Runtime: Python, `uv`, SQLite, FastAPI/Jinja, and Python Playwright
- New state root: `~/Library/Application Support/linkedin-tools/`
- Cutover status: approved and executed on 2026-06-24
- Cutover gate: [docs/cutover-checklist.md](docs/cutover-checklist.md)
- Cutover runbook: [docs/cutover-execution-runbook.md](docs/cutover-execution-runbook.md)
- Acceptance audit: [docs/cutover-acceptance-audit.md](docs/cutover-acceptance-audit.md)

## Install And Verify

```sh
uv sync
uv sync --extra dev
uv run linkedin-tools --help
uv run pytest
uv run ruff check apps packages tests
uv run mypy apps packages tests
```

`uv sync` installs runtime dependencies. Use `uv sync --extra dev` before
running tests, lint, or type checks.

## CLI Namespaces

```sh
uv run linkedin-tools network --help
uv run linkedin-tools recruiter-agency --help
uv run linkedin-tools opportunity --help
uv run linkedin-tools comments --help
uv run linkedin-tools ui --help
uv run linkedin-tools cutover --help
```

Compatibility commands are kept during migration:

```sh
uv run linkedin-network-run --help
uv run recruiter-agency-outreach --help
uv run linkedin-opportunity-intel --help
```

## State Layout

New Python state lives under:

```text
~/Library/Application Support/linkedin-tools/
```

Namespaces:

```text
network-automation/
recruiter-agency-outreach/
opportunity-intel/
comment-extractor/
review-ui/
legacy-imports.sqlite
```

Legacy state import commands preserve raw old artifacts and promote usable
Python app state:

```sh
uv run linkedin-network-run import-legacy-state \
  --old-state-dir "$HOME/Library/Application Support/linkedin-network-run" \
  --target-root "$HOME/Library/Application Support/linkedin-tools" \
  --json

uv run recruiter-agency-outreach import-legacy-state \
  --old-state-dir "$HOME/Library/Application Support/recruiter-agency-outreach" \
  --target-root "$HOME/Library/Application Support/linkedin-tools" \
  --json

uv run linkedin-opportunity-intel import-legacy-state \
  --old-state-dir /tmp/linkedin-opportunity-signals \
  --target-root "$HOME/Library/Application Support/linkedin-tools" \
  --json
```

Run the rehearsal commands in
[docs/cutover-execution-runbook.md](docs/cutover-execution-runbook.md)
before importing into the live Python state root.

After cutover, verify the active local Codex automation prompts point at the
Python commands:

```sh
uv run linkedin-tools cutover audit-automations --expect post-cutover
```

Most runtime examples below use this shell variable:

```sh
state_root="$HOME/Library/Application Support/linkedin-tools"
```

## Network Automation

The network namespace owns Sales Navigator connection-request runs, audit
reconciliation, candidate reservoirs, acceptance tracking, accepted follow-ups,
and pending-invitation cleanup.

```sh
uv run linkedin-tools network --state-dir "$state_root/network-automation" status --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" plan --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" report
uv run linkedin-tools network --state-dir "$state_root/network-automation" saved-searches --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" acceptance check --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" acceptance draft-followups --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" pending-cleanup audit --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" pending-cleanup capture --session auto
```

Browser-backed commands default to guarded dry-run behavior unless the explicit
real-action flag is provided:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  send-next --session auto --dry-run

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  send-guarded --session auto --single-pass --max-attempts 30 --allow-send
```

Pending cleanup also requires an explicit approval flag for real withdrawals:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  pending-cleanup withdraw-next --session auto --dry-run

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  pending-cleanup withdraw-next --session auto --allow-withdraw
```

## Recruiter And Agency Outreach

The recruiter/agency namespace owns account sourcing, lead capture, drafting,
dashboarding, guarded message dry-runs, and guarded sends. It must not send
connection requests.

```sh
uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  dashboard --print-markdown

uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  queue --limit 20 --include-drafts

uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  report --json
```

Message sends remain guarded:

```sh
uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  send-message --lead-id <lead-id> --session auto --dry-run

uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  send-message --lead-id <lead-id> --session auto --allow-send
```

## Opportunity Intelligence And Comments

Opportunity intelligence is recommend-only. It ranks and reviews buyer-signal
comments but does not send messages, connect, withdraw, or otherwise take
LinkedIn actions.

```sh
uv run linkedin-tools opportunity status --json
uv run linkedin-tools opportunity sources --json
uv run linkedin-tools opportunity post-queue --out /tmp/linkedin-opportunity-posts.csv
```

Known-post HTML comment extraction:

```sh
uv run linkedin-tools comments extract \
  --post-url <linkedin-post-url> \
  --html /path/to/post.html \
  --source-id <source-id> \
  --query-id <query-id> \
  --out-dir /tmp/linkedin-comments
```

Source experiments:

```sh
uv run linkedin-tools opportunity run-experiment \
  --comments-csv /path/to/comments.csv \
  --out-dir /tmp/linkedin-opportunity-intel \
  --run-id source-test
```

## Local Review UI

```sh
uv run linkedin-tools ui --host 127.0.0.1 --port 8787
```

The UI exposes review surfaces for opportunities, networking state,
recruiter/agency state, browser artifacts, and guarded action paths.

## Safety Rules

- No real LinkedIn sends without `--allow-send`.
- No real pending-invitation withdrawals without `--allow-withdraw`.
- Opportunity intelligence is recommend-only.
- Browser flows should start with dry-runs.
- Keep archived Go/JavaScript code read-only unless restoring or auditing
  legacy behavior.

## Project Layout

```text
apps/
  cli.py
  compat.py
  network_automation/
  recruiter_agency_outreach/
  opportunity_intel/
  comment_extractor/
  review_ui/
packages/
  linkedin_common/
  linkedin_browser/
  linkedin_salesnav/
  linkedin_storage/
  linkedin_reports/
  linkedin_ui/
  linkedin_experiments/
tests/
docs/
```

Archived legacy Go/JavaScript paths:

```text
archive/legacy-go-js/cmd/
archive/legacy-go-js/internal/
archive/legacy-go-js/scripts/
archive/legacy-go-js/go.mod
archive/legacy-go-js/go.sum
```
