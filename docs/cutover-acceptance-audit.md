# Python LinkedIn Tools Cutover Acceptance Audit

Date: 2026-06-24
Branch: `python-port/orchestrator-scaffold`

This audit maps the PRD acceptance criteria and migration requirements to
current repository evidence. It does not approve cutover; Hanif approval remains
the only pending cutover item.

## Verification Commands Run

- `uv sync`
- `uv sync --extra dev`
- `uv run linkedin-tools --help`
- `uv run linkedin-tools network --help`
- `uv run linkedin-tools recruiter-agency --help`
- `uv run linkedin-tools opportunity --help`
- `uv run linkedin-tools comments --help`
- `uv run linkedin-tools ui --help`
- `uv run linkedin-network-run --help`
- `uv run recruiter-agency-outreach --help`
- `uv run linkedin-opportunity-intel --help`
- `uv run python - <<'PY' ... compatibility command coverage ... PY`
- `uv run linkedin-tools comments extract ...`
- `uv run linkedin-tools opportunity run-experiment ...`
- `uv run linkedin-network-run import-legacy-state --old-state-dir "$HOME/Library/Application Support/linkedin-network-run" --target-root "$tmp_root" --json`
- `uv run recruiter-agency-outreach import-legacy-state --old-state-dir "$HOME/Library/Application Support/recruiter-agency-outreach" --target-root "$tmp_root" --json`
- `uv run linkedin-tools network --state-dir "$tmp_root/network-automation" status --json`
- `uv run linkedin-tools recruiter-agency --state-dir "$tmp_root/recruiter-agency-outreach" report --json`
- `uv run pytest tests/test_browser_layer.py tests/test_salesnav_primitives.py tests/test_review_ui.py tests/test_migration_compat.py tests/test_opportunity_intel.py -q`
- `uv run pytest tests/network_automation/test_network_automation.py tests/test_recruiter_agency_outreach.py -q`
- `uv run pytest tests/test_report_helpers.py tests/test_storage_migrations.py tests/test_common_schemas.py tests/test_common_io.py tests/test_common_urls.py -q`
- `uv run pytest`
- `uv run ruff check apps packages tests`
- `uv run mypy apps packages tests`
- `rg -n "slice\\(|substring\\(|substr\\(|visibleText|innerText|document\\.title|legacy|fallback|infer|keyword|score|\\[class\\*=|h1|h2|h3|article|raw_text" apps packages tests docs --glob '!**/__pycache__/**'`

## Environment Note

Plain `uv sync` passed and installed the runtime package. It intentionally
removed optional dev tools because test/lint/type tools live under the `dev`
extra in `pyproject.toml`. `uv sync --extra dev` restored the verification
toolchain. Both commands completed without modifying the git worktree.

## Migration Requirements

| Requirement | Status | Evidence |
| --- | --- | --- |
| Every current CLI command has a Python equivalent. | Proven | `docs/cutover-checklist.md`; compatibility coverage command printed `network: missing=[]`, `recruiter: missing=[]`, `opportunity: missing=[]`. |
| Every current browser script has a Python Playwright equivalent or documented consolidated replacement. | Proven | `docs/cutover-checklist.md`; live dry-run artifact paths checked; Sales Navigator API enrichment port covered by `bc3b923`. |
| Existing state can be imported or read without data loss. | Proven | `uv run pytest tests/test_storage_migrations.py tests/test_migration_compat.py`; migration tests hash source state before and after import; temp-root import of real local network and recruiter/agency state completed with no warnings. |
| Existing reports can be regenerated from migrated state. | Proven | Temp-root import of real local state regenerated `linkedin-tools network ... status --json` and `linkedin-tools recruiter-agency ... report --json`; report helper tests also pass. |
| Current tests have Python equivalents. | Proven | `uv run pytest` collected and passed 105 Python tests. |
| Send/withdraw safety gates have parity tests. | Proven | `tests/test_browser_layer.py`, `tests/test_salesnav_primitives.py`, `tests/network_automation/test_network_automation.py`, and `tests/test_recruiter_agency_outreach.py` passed. |
| Opportunity-intel recommend-only boundaries have import-boundary tests. | Proven | `tests/test_opportunity_intel.py` passed; static import/action boundary test rejects action modules and send/connect/withdraw definitions. |
| The current Go repo can be frozen or archived after successful cutover. | Pending approval | Technical prerequisites are checked; `docs/cutover-checklist.md` still requires Hanif approval before archive/freeze. |

## PRD Acceptance Criteria

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | The Python monorepo exists and installs with `uv sync`. | Proven | `uv sync` completed successfully; `pyproject.toml` defines package `linkedin-tools`. |
| 2 | `linkedin-tools` exposes all planned namespaces. | Proven | `uv run linkedin-tools --help` listed `network`, `recruiter-agency`, `opportunity`, `comments`, and `ui`; each namespace help command succeeded. |
| 3 | Compatibility commands exist for the three current CLIs. | Proven | `uv run linkedin-network-run --help`, `uv run recruiter-agency-outreach --help`, and `uv run linkedin-opportunity-intel --help` succeeded. |
| 4 | All current networking behavior is ported. | Proven | Compatibility coverage has no network gaps; `drain-stale-candidates` and `top-up-reconcile` are Python commands; network tests passed. |
| 5 | All current recruiter/agency behavior is ported. | Proven | Compatibility coverage has no recruiter/agency gaps; recruiter/agency tests passed. |
| 6 | All current opportunity-intel behavior is ported. | Proven | Compatibility coverage has no opportunity gaps; opportunity tests passed; full command surface delegates to Python app. |
| 7 | The new comment extractor works from known post URLs into `raw_comments.jsonl`. | Proven | `uv run linkedin-tools comments extract ...` wrote `/tmp/linkedin-tools-acceptance-audit/comments/raw_comments.jsonl` with 2 rows. |
| 8 | Opportunity-intel can run a full source experiment from source registry to review queue and source report. | Proven | `uv run linkedin-tools opportunity run-experiment ...` wrote `/tmp/linkedin-tools-acceptance-audit/runs/acceptance/source_report.md` and `review_queue.csv`. |
| 9 | Real-send and real-withdraw safety gates are covered by tests. | Proven | Browser and workflow safety tests passed; real-action routes require approval flags/tokens. |
| 10 | Recommend-only opportunity modules cannot call send/withdraw code. | Proven | `tests/test_opportunity_intel.py` static boundary test passed. |
| 11 | Existing state can be imported without mutating old state. | Proven | Migration compatibility tests passed and hash old-state fixtures before/after import; network promotion writes `network-automation`, recruiter/agency promotion writes `recruiter-agency-outreach/outreach.sqlite`. |
| 12 | Tests pass. | Proven | `uv run pytest`: 105 passed, 1 existing FastAPI/Starlette deprecation warning. |
| 13 | Browser dry-runs pass. | Proven | Existing live dry-run artifacts are present; no real send/withdraw was performed. |
| 14 | The local review UI exposes required opportunity, networking, recruiter/agency, and browser/artifact views. | Proven | `tests/test_review_ui.py` passed. |
| 15 | UI safety tests prove recommend-only pages cannot call send/withdraw and real-action controls use guarded command paths. | Proven | `tests/test_review_ui.py` passed; tests assert opportunity pages exclude real action commands and action routes require token. |
| 16 | Hanif reviews and approves cutover. | Pending | Explicit user approval has not been given. |

## Live Dry-Run Artifacts Checked

- `/tmp/linkedin-tools-live-dryrun.84HHg5/followup-dryrun/001-afu_290bef9f8226.json`
- `/tmp/linkedin-tools-live-dryrun.84HHg5/withdraw-dryrun-actual-age/001-withdraw-result.json`
- `/tmp/recruiter-agency-live-dryrun.h4e40B/capture-live/001-capture-page.json`
- `/tmp/recruiter-agency-live-dryrun.h4e40B/message-dryrun/001-lead_d17f3936.json`
- `/tmp/recruiter-agency-live-dryrun.h4e40B/account-capture-live/001-ASAP---Agency-Accounts-Product-Studio-accounts.json`

## Legacy Import Rehearsal

Temp target root:

```text
/tmp/linkedin-tools-cutover-import.SOcnB8
```

Results:

- Network import preserved 52 artifacts, emitted no warnings, promoted state to
  `network-automation`, and `linkedin-tools network --state-dir ... status
  --json` loaded run `6a9de241-d15e-4355-9930-471e98441766`.
- Recruiter/agency import preserved 33 artifacts, emitted no warnings, promoted
  state to `recruiter-agency-outreach/outreach.sqlite`, and
  `linkedin-tools recruiter-agency --state-dir ... report --json` regenerated
  status, source, lead-type, message-status, account, and contact-candidate
  counts.

## Current Conclusion

The technical implementation is ready for Hanif review. The cutover should not
be marked complete, and the old Go/JavaScript implementation should not be
archived or frozen, until Hanif explicitly approves cutover.
