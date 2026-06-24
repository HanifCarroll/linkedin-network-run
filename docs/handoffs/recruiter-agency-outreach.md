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

- The shared browser packages have safety primitives and artifact parsers, but
  still do not provide the concrete Playwriter runner needed for
  `scripts/salesnav-send-message-one.js`, `scripts/salesnav-capture.js`, or
  `scripts/salesnav-account-capture.js`. The Python `send-message` path
  enforces gates and can apply one structured result via `--result-path`;
  `send-ready` can apply one non-dry-run result per ready lead via
  `--result-dir`. Live capture commands are present but fail explicitly until
  the shared browser runner exists.
- The root compatibility entrypoint delegates the implemented recruiter/agency
  commands to `apps.recruiter_agency_outreach.cli:main`. Legacy-only behavior
  such as `serve` remains outside this app slice.
- No live LinkedIn sends, messages, or browser actions were performed.
