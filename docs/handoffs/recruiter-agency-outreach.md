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
- App-local CLI namespace in `apps/recruiter_agency_outreach/cli.py`.

## Safety Preserved

- `run-daily` is sourcing/drafting/reporting only and rejects send flags.
- Real message sends require `--allow-send` and a prior `dry_run_ready` state.
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

- The shared browser packages are still scaffolded, so this workstream did not
  implement a live Playwright Sales Navigator message adapter. The Python send
  path enforces the safety gates and can apply a structured browser result via
  `--result-path`; wiring an actual browser runner belongs in the shared
  browser/Sales Navigator workstream.
- The root compatibility entrypoint in `apps/compat.py` still points at a
  scaffold shim. The orchestrator should route `recruiter-agency-outreach` to
  `apps.recruiter_agency_outreach.cli:main` when root CLI ownership is updated.
- No live LinkedIn sends, messages, or browser actions were performed.

