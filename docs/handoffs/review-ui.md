# Review UI Handoff

## Goal

Build the local FastAPI/Jinja/HTMX/Alpine review UI for opportunity intel,
network automation, recruiter/agency outreach, and browser/artifact state.

## Owned Paths Changed

- `apps/review_ui/`
- `packages/linkedin_ui/`
- `tests/test_review_ui.py`
- `tests/fixtures/review_ui/README.md`
- `docs/handoffs/review-ui.md`

## Commands Implemented

- `python -m apps.review_ui.cli --host 127.0.0.1 --port 8787`

The CLI generates a local access token by default and prints a localhost URL
with `access_token=<token>`. `--access-token` can be supplied for deterministic
tests or local validation.

## Data Models Introduced

- `ReviewSnapshot` cross-system read model.
- Opportunity rows for sources, post queue, extraction runs, ranked comments,
  experiment reports, and calibration queue.
- Network rows for run status, candidates, acceptance drafts, and pending
  cleanup.
- Recruiter/agency rows for latest run summary, agency accounts, and lead
  queue.
- Browser rows for session state and artifacts.
- Declarative `ReviewAction` and `GuardedCommand` models for token-gated
  real-action surfaces.

## Tests Added

- Page rendering for all required review areas.
- Opportunity recommend-only page safety checks proving no send/connect/withdraw
  controls or guarded command flags are exposed there.
- Local token enforcement for state-changing opportunity label routes.
- Local token enforcement for guarded action pages.
- Guarded real-action registry checks for `--allow-send` and
  `--allow-withdraw`.
- Action route delegation through the injected action service.
- Alpine/HTMX presence for presentation state and server-backed mutation routes.

## Verification Run

- `uv sync --extra dev`
- `uv run pytest tests/test_review_ui.py` passed: 7 tests.
- `uv run pytest` passed: 9 tests.
- `uv run ruff check apps/review_ui packages/linkedin_ui tests/test_review_ui.py`
  passed.
- `uv run mypy apps/review_ui packages/linkedin_ui tests/test_review_ui.py`
  passed.
- Local server smoke test used
  `http://127.0.0.1:8791/?access_token=smoke-token` because port `8787` was
  already in use. Confirmed overview and opportunity pages rendered, confirmed
  `/actions?access_token=smoke-token` returned `200`, confirmed `/actions`
  returned `403`, then stopped the server.
- Weak-inference grep over touched UI paths returned only HTML headings/article
  tags and PRD display fields such as `signal_score` and `keyword_search`; no
  extraction or scraping logic was added.

## Known Gaps

- Opportunity and browser artifact read models are now SQLite-backed for the
  opportunity/comment discovery system. Network and recruiter/agency read
  models still use placeholder rows until their app-specific read models land.
- Guarded real-action buttons are registered but disabled until app service
  integrations land.
- Opportunity comment label routes validate the token and write durable SQLite
  review labels, reject reasons, notes, and status transitions.
- No live browser actions, send actions, message sends, or withdrawals were
  exercised.
- Template/static package data may need orchestrator-owned packaging review
  before distribution builds, because this thread did not modify `pyproject.toml`.

## Integration Dependencies

- Thread 2: browser profile/session/artifact read models and artifact manifest
  paths.
- Thread 3: opportunity source, post, comment, experiment, calibration, and
  review-queue read models plus durable label/note services.
- Thread 4: network run, reservoir, audit, acceptance, pending-cleanup read
  models and guarded send/withdraw services.
- Thread 5: recruiter/agency run, account pool, lead queue, draft,
  messageability, blocker, and guarded message services.
- Thread 0: top-level `linkedin-tools ui` dispatch wiring if desired, because
  `apps/cli.py` is outside this thread's owned paths.

## Decisions Needing Orchestrator Approval

- Whether to expose disabled guarded action buttons before service integration
  or keep them hidden until Threads 4-5 land.
- Whether to add package-data configuration for templates/static assets in
  `pyproject.toml`.
- Whether the generated token should stay process-local or move to a temporary
  file under the `linkedin-tools` state root for browser reload ergonomics.
