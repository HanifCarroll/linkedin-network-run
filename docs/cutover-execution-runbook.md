# Python LinkedIn Tools Cutover Execution Runbook

Date: 2026-06-24
Branch: `python-port/orchestrator-scaffold`

This runbook is the post-approval execution path for moving from the current
Go/JavaScript implementation to the Python `linkedin-tools` monorepo. The
technical readiness evidence is in `docs/cutover-acceptance-audit.md`; this
file covers the operational cutover steps.
The active local Codex automation prompt replacement map is in
`docs/cutover-automation-inventory.md`.

## Current State

- Python package: `linkedin-tools`
- New state root: `~/Library/Application Support/linkedin-tools/`
- Python app namespaces:
  - `network-automation`
  - `recruiter-agency-outreach`
  - `opportunity-intel`
  - `comment-extractor`
  - `review-ui`
- Compatibility commands:
  - `linkedin-network-run`
  - `recruiter-agency-outreach`
  - `linkedin-opportunity-intel`
- Legacy implementation still present:
  - `cmd/`
  - `internal/`
  - `scripts/`
  - `go.mod`
  - `go.sum`

## Approval Gate

Cutover is not approved until Hanif explicitly approves it. Before that
approval, keep the Go/JavaScript implementation in place and treat the Python
port as the candidate implementation.

After approval, update `docs/cutover-checklist.md` so `Hanif approves cutover`
is checked in the same commit that records approval.

## Pre-Cutover Verification

Run these commands from the repository root:

```sh
git status --short
uv sync
uv sync --extra dev
uv run linkedin-tools --help
uv run linkedin-tools network --help
uv run linkedin-tools recruiter-agency --help
uv run linkedin-tools opportunity --help
uv run linkedin-tools comments --help
uv run linkedin-tools ui --help
uv run linkedin-network-run --help
uv run recruiter-agency-outreach --help
uv run linkedin-opportunity-intel --help
uv run linkedin-tools cutover audit-automations --expect pre-cutover
uv run linkedin-tools cutover plan-automation-edits
uv run pytest
uv run ruff check apps packages tests
uv run mypy apps packages tests
git diff --check
```

Run the source-faithful extraction grep and review any hits that are not known
intentional fields, docs, or UI templates:

```sh
rg -n "slice\(|substring\(|substr\(|visibleText|innerText|document\.title|legacy|fallback|infer|keyword|score|\[class\*=|h1|h2|h3|article|raw_text" apps packages tests docs --glob '!**/__pycache__/**'
```

## Legacy State Import

Run imports only after approval. These commands read the old state locations
and write both raw import records and promoted Python app state under the new
Python state root.

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

Expected import target:

```text
~/Library/Application Support/linkedin-tools/legacy-imports.sqlite
~/Library/Application Support/linkedin-tools/network-automation/
~/Library/Application Support/linkedin-tools/recruiter-agency-outreach/outreach.sqlite
```

If an import emits warnings, keep the old implementation available until the
warning is understood and either accepted or fixed.

## Import Rehearsal

Before importing into the live Python state root, rehearse against a temporary
target root:

```sh
tmp_root=$(mktemp -d /tmp/linkedin-tools-cutover-import.XXXXXX)

uv run linkedin-network-run import-legacy-state \
  --old-state-dir "$HOME/Library/Application Support/linkedin-network-run" \
  --target-root "$tmp_root" \
  --json

uv run recruiter-agency-outreach import-legacy-state \
  --old-state-dir "$HOME/Library/Application Support/recruiter-agency-outreach" \
  --target-root "$tmp_root" \
  --json

uv run linkedin-tools network \
  --state-dir "$tmp_root/network-automation" \
  status --json

uv run linkedin-tools recruiter-agency \
  --state-dir "$tmp_root/recruiter-agency-outreach" \
  report --json
```

## Runtime Smoke Checks

After live import, use explicit Python state directories for smoke checks
before changing any automation or operator habits:

```sh
state_root="$HOME/Library/Application Support/linkedin-tools"

uv run linkedin-tools network --state-dir "$state_root/network-automation" status --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" plan --json
uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  dashboard --print-markdown
uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  report --json
uv run linkedin-tools opportunity status --json
uv run linkedin-tools ui --host 127.0.0.1 --port 8787
```

For browser-backed smoke checks, use dry-run paths first:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  send-next --session auto --dry-run
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance dry-run-followups --session auto --limit 5
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  pending-cleanup withdraw-next --session auto --dry-run
uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  send-message --lead-id <lead-id> --session auto --dry-run
```

Real sends and real withdrawals still require explicit operator intent plus the
matching approval flag:

```sh
--allow-send
--allow-withdraw
```

## Cutover Commit Sequence

1. Record approval by checking `Hanif approves cutover` in
   `docs/cutover-checklist.md`.
2. Create a rollback anchor before archiving legacy code:

   ```sh
   git tag python-cutover-approved-YYYYMMDD
   ```

3. Generate the read-only prompt edit plan:

   ```sh
   uv run linkedin-tools cutover plan-automation-edits
   ```

   Apply those old-command to new-command replacements to the six active
   automation prompts listed in `docs/cutover-automation-inventory.md`. Keep
   the safety requirements from the plan in the prompt text.
4. Verify the live automation prompts now point at Python commands:

   ```sh
   uv run linkedin-tools cutover audit-automations --expect post-cutover
   ```

5. Run the pre-cutover verification commands again.
6. In a separate archive commit, freeze or remove legacy Go/JavaScript entry
   points according to the approved archive decision.
7. Run the pre-cutover verification commands after the archive commit.

## Archive Targets

The archive commit can remove or move these legacy implementation paths after
approval:

```text
cmd/
internal/
scripts/
go.mod
go.sum
```

Keep the Python paths as the active implementation:

```text
apps/
packages/
tests/
pyproject.toml
uv.lock
```

## Rollback

Rollback uses the approval tag and the pre-archive commit. If a Python cutover
issue appears, restore the previous commit or branch from the tag, keep real
send/withdraw actions paused, and re-run the relevant compatibility command
with `--dry-run` before resuming live work.
