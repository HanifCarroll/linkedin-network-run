# Codex Automation Cutover Inventory

Date: 2026-06-24

This inventory maps the active local Codex LinkedIn automations to the Python
`linkedin-tools` command surface. It is preparation for cutover only. Do not
edit the live automation prompts until Hanif approves cutover in
`docs/cutover-checklist.md`.

## Active Automations Checked

- `linkedin-network`
- `linkedin-acceptance-daily`
- `linkedin-acceptance-weekly`
- `linkedin-pending-cleanup`
- `recruiter-agency-outreach-daily`
- `recruiter-agency-sending-daily`

## Replacement Map

| Automation | Old dependency | Python replacement |
| --- | --- | --- |
| `linkedin-network` | `linkedin-network-run start/plan/report/finish` | `uv run linkedin-tools network ...` |
| `linkedin-network` | `scripts/salesnav-audit.js` plus `import-audit` | `linkedin-tools network reconcile-audit --session auto --attempts 1 --delay-ms 0` |
| `linkedin-network` | `scripts/salesnav-saved-searches.js` | `linkedin-tools network saved-searches --session auto --out /tmp/linkedin-network-run-saved-searches.json` |
| `linkedin-network` | `scripts/salesnav-capture.js` plus `import-capture` | `linkedin-tools network capture --session auto ...` |
| `linkedin-network` | guarded send/top-up commands | `linkedin-tools network send-guarded ...` and `top-up-reconcile ...` |
| `linkedin-acceptance-daily` | `linkedin-network-run acceptance seed-history/export/import/report` | `linkedin-tools network acceptance seed-history/export/import/report` |
| `linkedin-acceptance-daily` | `scripts/salesnav-acceptance-outcomes.js` | `linkedin-tools network acceptance check --session auto --in <candidates> --out <outcomes>` |
| `linkedin-acceptance-daily` | `scripts/salesnav-accepted-research.js` | `linkedin-tools network acceptance research --session auto --in <accepted-candidates> --out <research>` |
| `linkedin-acceptance-daily` | built-in accepted research during `draft-followups --session` | `linkedin-tools network acceptance draft-followups --session auto --out-dir <dir>` |
| `linkedin-acceptance-weekly` | report-only `linkedin-network-run acceptance` commands | `linkedin-tools network acceptance seed-history` and `report` |
| `linkedin-pending-cleanup` | `scripts/salesnav-audit.js` plus `pending-cleanup import-audit` | `linkedin-tools network pending-cleanup audit --session auto` |
| `linkedin-pending-cleanup` | `scripts/salesnav-pending-capture.js` plus `pending-cleanup import-capture` | `linkedin-tools network pending-cleanup capture --session auto --load-more <n>` |
| `linkedin-pending-cleanup` | `pending-cleanup withdraw-next` | `linkedin-tools network pending-cleanup withdraw-next --session auto ...` |
| `recruiter-agency-outreach-daily` | `/Users/hanifcarroll/.local/bin/recruiter-agency-outreach run-daily` | `uv run linkedin-tools recruiter-agency run-daily ...` |
| `recruiter-agency-sending-daily` | `/Users/hanifcarroll/.local/bin/recruiter-agency-outreach send-ready` | `uv run linkedin-tools recruiter-agency send-ready ...` |

## Verification Added

- `tests/network_automation/test_network_automation.py` covers Python browser
  routing for `saved-searches`, `acceptance check`, accepted research inside
  `acceptance draft-followups --session`, `pending-cleanup audit`, and
  `pending-cleanup capture`.
- `apps.cli` now passes subcommands through to app namespaces, so
  `uv run linkedin-tools network ...` exposes the real network parser instead
  of only the top-level namespace stub.
- `apps.compat` now routes `linkedin-network-run saved-searches` through the
  Python network app during migration.

## Cutover Prompt Edits

After Hanif approves cutover, update the six active automation prompts above to
call `uv run linkedin-tools ...` with explicit Python state directories under:

```text
~/Library/Application Support/linkedin-tools/
```

Keep the same safety language in the prompts:

- no concurrent send or withdrawal loops
- real sends require `--allow-send`
- real withdrawals require `--allow-withdraw`
- no opportunity-intel outreach automation
- stop on login/checkpoint/security/weekly-limit blockers
