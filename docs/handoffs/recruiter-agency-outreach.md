# Recruiter/Agency Outreach Python Port Handoff

## Scope

Owned implementation landed under `apps/recruiter_agency_outreach/` with
fixture-backed tests in `tests/test_recruiter_agency_outreach.py` and
`tests/fixtures/recruiter_agency_outreach/`.

Implemented:

- Recruiter Sales Navigator capture import and deterministic classification.
- Agency account capture import and account-first agency sourcing surface.
- Structured agency source CSV/JSON import.
- Agency pool diagnosis and next-action reporting.
- Review-only agency contact candidates.
- Contact promotion with Sales Navigator identity gating.
- Draft generation using the current shorter recruiter/agency copy.
- Dashboard/report rendering with agency/recruiter buckets.
- Messageability/send-result application and guarded real-send state gates.
- `run-daily` no-send behavior that rejects `--allow-send`.
- Python CLI parity for state-backed commands: `accounts`, `lead show`,
  `queue`, `last-run`, `recommend-next-run`, `revise`, `send-ready`,
  `reject`, and `report`.
- `send-ready` structured-result replay for already `dry_run_ready` leads.
  It requires `--allow-send`, rejects dry-run artifacts, records run lifecycle
  and send-message events, and writes the sending dashboard.
- App-local CLI namespace in `apps/recruiter_agency_outreach/cli.py`.

## Safety Preserved

- `run-daily` is sourcing/drafting/reporting only and rejects send flags.
- Real message sends require `--allow-send` and a prior `dry_run_ready` state.
- Python `send-ready` does not click LinkedIn. It only applies explicit
  structured result artifacts supplied by `--result-dir`.
- Agency sends require a qualified agency account context.
- Public LinkedIn `/in/...` URLs found from agency websites remain review
  context only.
- Promotion requires `sales_profile_urn`, including the
  `contact_sales_profile_urn` CSV alias, and converts it to a Sales Navigator
  lead URL.
- Approved website contacts without Sales Navigator identity surface as
  `resolve_agency_contact_salesnav_identity`.
- Qualified agency accounts without LinkedIn company URLs surface
  `missing_linkedin_company_url` before account-scoped contact search.

## Verification

Ran:

```sh
uv run pytest tests/test_recruiter_agency_outreach.py
uv run ruff check apps/recruiter_agency_outreach tests/test_recruiter_agency_outreach.py
uv run mypy apps/recruiter_agency_outreach tests/test_recruiter_agency_outreach.py
```

All passed.

Also ran the source-faithful grep from `AGENTS.md`. Remaining hits are
intentional `fit_score` / `score` fields and deterministic scoring functions
used for parity classification and queue ordering, not extraction fallbacks.

## Integration Notes

- `capture`, `capture-accounts`, and `send-message` now have concrete Python
  Playwright runners. `send-message` still accepts `--result-path` for
  structured artifact replay, and `send-ready` can apply one non-dry-run result
  per ready lead via `--result-dir`.
- Live dry-run/capture proof artifacts from 2026-06-24:
  `/tmp/recruiter-agency-live-dryrun.h4e40B/capture-live/001-capture-page.json`,
  `/tmp/recruiter-agency-live-dryrun.h4e40B/account-capture-live/001-ASAP---Agency-Accounts-Product-Studio-accounts.json`,
  and
  `/tmp/recruiter-agency-live-dryrun.h4e40B/message-dryrun/001-lead_d17f3936.json`.
- The root compatibility entrypoint delegates the implemented recruiter/agency
  commands to `apps.recruiter_agency_outreach.cli:main`. `serve` now launches
  the consolidated Python review UI at the recruiter/agency route.
- No live LinkedIn sends, messages, or browser actions were performed.
