# Opportunity Intel And Comment Extractor Handoff

## Goal

Port the recommend-only opportunity intelligence workflow and comment extractor
inside the Python monorepo without touching send-capable workflows.

## Owned Paths Changed

- `apps/opportunity_intel/`
- `apps/comment_extractor/`
- `tests/test_opportunity_intel.py`
- `tests/fixtures/opportunity_intel/linkedin_post_comments.html`
- `docs/handoffs/opportunity-intel-comment-extractor.md`

## Commands Implemented

Opportunity intelligence:

```sh
uv run python -m apps.opportunity_intel.cli validate-contracts
uv run python -m apps.opportunity_intel.cli post-queue
uv run python -m apps.opportunity_intel.cli run-experiment --comments-csv <csv> --out-dir <dir>
```

Comment extraction:

```sh
uv run python -m apps.comment_extractor.cli extract --post-url <url> --html <html> --source-id <source> --query-id <query> --out-dir <dir>
uv run python -m apps.comment_extractor.cli extract-queue --post-queue <csv> --out-dir <dir>
```

## Data Models Introduced

- Source registry contract: `opportunity-source-registry.v1`.
- Query pack contract: `opportunity-comment-signal-queries.v1`.
- Raw comment output contract: `raw_comments.v1` in `raw_comments.jsonl`.
- Canonical provider/manual CSV columns and aliases from the salvage note.
- Actual-comment evidence rows with required LinkedIn post URL, commenter
  profile URL, commenter name, comment text, and source/query attribution.
- Post queue candidates, ranked comments, proof gate results, calibration
  reports, source decisions, experiment artifacts, and review queue rows.

## Tests Added

- Contract validation for source registry and query pack.
- Explicit-selector comment extraction from a fixture LinkedIn post page.
- `raw_comments.jsonl` writer contract.
- Provider/manual CSV alias normalization.
- 100-row batch proof gate behavior.
- Fixture-backed source experiment artifact generation.
- Direct-buyer ranking and noise rejection.
- Static import/action boundary checks for opportunity/comment modules.

## Verification Run

```sh
uv run ruff check apps/opportunity_intel apps/comment_extractor tests/test_opportunity_intel.py
uv run mypy apps/opportunity_intel apps/comment_extractor tests/test_opportunity_intel.py
uv run pytest tests/test_opportunity_intel.py
uv run python -m apps.opportunity_intel.cli validate-contracts
rg -n "slice\(|substring\(|substr\(|visibleText|innerText|document\.title|legacy|fallback|infer|keyword|score|\[class\*=|h1|h2|h3|article|raw_text" apps/opportunity_intel apps/comment_extractor tests/test_opportunity_intel.py
```

Results:

- Ruff: passed.
- Mypy: passed, 15 source files checked.
- Pytest: passed, 7 tests.
- Contract CLI: validated 5 sources and 6 queries.
- Weak-inference grep: no matches in touched opportunity/comment code or tests.

## Known Gaps

- Live Playwright extraction is not wired yet because the shared browser layer is
  owned by Thread 2. The extractor currently consumes known post URLs with saved
  HTML artifacts or a queue CSV pointing at those artifacts.
- Calibration report generation currently creates the report/template and blocks
  promotion until labels exist. Label import/storage should be integrated once
  the orchestrator settles shared experiment storage.
- Native provider adapters are intentionally behind the CSV contract; none are
  added here.
- If the project builds wheels, the orchestrator should add package-data config
  for `apps/opportunity_intel/data/*.json`.

## Integration Dependencies

- Thread 2 browser layer should provide a read-only page/HTML capture API for
  known LinkedIn post URLs.
- Thread 1/shared foundation may eventually replace local CSV/JSONL writers
  with shared report/storage helpers.
- Thread 6 review UI can consume `source_report.md`, `source_gate.json`,
  `calibration_template.csv`, `calibration_report.md`, `source_decision.json`,
  `action_plan.md`, `run_history.jsonl`, `review_queue.csv`, and
  `review_queue.jsonl`.

## Decisions Needing Orchestrator Approval

- Whether to package JSON source/query contracts via root `pyproject.toml`.
- Final browser API shape for known-post comment extraction.
- Shared schema location for human calibration labels.
