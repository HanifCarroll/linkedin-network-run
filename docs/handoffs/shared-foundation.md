# Shared Foundation Handoff

## Goal

Build common schemas, config, storage, URL utilities, logging, report helpers,
experiment helpers, migration primitives, and shared fixtures for the Python
LinkedIn tools port.

## Owned Paths Changed

- `packages/linkedin_common/`
- `packages/linkedin_storage/`
- `packages/linkedin_reports/`
- `packages/linkedin_experiments/`
- `tests/fixtures/shared_foundation/`
- `tests/test_common_*.py`
- `tests/test_storage_migrations.py`
- `tests/test_report_helpers.py`
- `tests/test_experiment_helpers.py`
- `docs/handoffs/shared-foundation.md`

## Commands Implemented

No user-facing CLI commands were implemented in this thread.

## Data Models Introduced

- `AppNamespace`, `AppConfig`: shared state namespace and config contracts.
- `SourceRecord`, `SourceAttribution`: source registry and row attribution.
- `PostRecord`: shared `post_queue.jsonl` record.
- `CommentRecord`: shared `raw_comments.jsonl` proof row requiring
  `post_url`, `comment_text`, `commenter_name`, and `commenter_profile_url`.
- `CaptureArtifact`, `CaptureRecord`, `WarningRecord`: artifact and warning
  contracts.
- `RunManifest`, `RunStatus`: machine-readable run manifests.
- `SourceExperimentMetrics`, `SourceGateThresholds`, `SourceGateResult`:
  source experiment metric and gate primitives.

## Public Package APIs

### `packages.linkedin_common`

- `state_root_for_namespace(namespace, root=DEFAULT_STATE_ROOT) -> Path`
- `old_state_root(namespace) -> Path | None`
- `AppConfig.for_namespace(namespace, root=DEFAULT_STATE_ROOT, browser_profile_name="LinkedIn")`
- `canonicalize_linkedin_profile_url(value) -> str`
- `canonicalize_linkedin_post_url(value) -> str`
- `canonicalize_sales_nav_lead_url(value) -> str`
- `canonicalize_sales_profile_url(value) -> str`
- `sales_profile_urn_to_lead_url(value) -> str`

URL helpers raise `URLCanonicalizationError` when a value does not match an
explicit supported LinkedIn shape. Sales Navigator lead canonicalization returns
the stable identity URL with only the profile id. `sales_profile_urn_to_lead_url`
returns the full navigable `/sales/lead/id,authType,token` URL.

### `packages.linkedin_storage`

- `connect_sqlite(path, readonly=False, timeout=30.0) -> sqlite3.Connection`
- `transaction(conn)`
- `dict_rows(cursor) -> list[dict[str, SQLiteValue]]`
- `apply_migrations(conn, migrations) -> list[Migration]`
- `copy_rows_from_readonly_source(...) -> ImportResult`
- `write_jsonl(path, rows) -> int`
- `read_jsonl_dicts(path) -> list[dict[str, object]]`
- `read_jsonl_models(path, model) -> list[BaseModel]`
- `write_csv_rows(path, rows, fieldnames=[...]) -> int`
- `read_csv_rows(path) -> list[dict[str, str]]`

The import primitive opens source SQLite files with `mode=ro` and writes only to
the supplied target connection.

### `packages.linkedin_reports`

- `render_markdown_table(headers, rows, alignments=None) -> str`
- `heading(text, level=1) -> str`
- `bullet_list(items) -> str`
- `key_value_section(values) -> str`
- `MarkdownReport`

### `packages.linkedin_experiments`

- `rate(numerator, denominator) -> float`
- `per_100(numerator, denominator) -> float`
- `SourceExperimentMetrics`
- `SourceGateThresholds`
- `evaluate_source_gate(metrics, thresholds, evidence_fields_complete=True)`

## Tests Added

- Schema strictness and URL normalization in shared Pydantic contracts.
- LinkedIn public profile, post, Sales Navigator lead URL, and Sales Profile URN
  canonicalization.
- SQLite migrations and read-only source import.
- JSONL and CSV helper round trips plus shared fixture validation.
- Markdown table and report helper rendering.
- Source experiment metrics and gate evaluation.

## Verification Run

- PASS: `uv run pytest` (`29 passed`)
- PASS: `uv run ruff check packages/linkedin_common packages/linkedin_storage packages/linkedin_reports packages/linkedin_experiments tests`
- PASS: `uv run mypy packages/linkedin_common packages/linkedin_storage packages/linkedin_reports packages/linkedin_experiments tests`
- REVIEWED: Source-faithfulness grep on touched Python package/test paths.
  Remaining matches are `priority_score` in the PRD-required post queue
  contract.

## Known Gaps

- App-specific tables and old-state import mappings belong to the app and
  migration workstreams.
- Browser artifact contracts are represented only as shared metadata records;
  browser capture implementation belongs to the browser workstream.

## Integration Dependencies

- App threads can depend on the shared schemas and IO helpers immediately.
- Migration thread should build concrete import plans on top of
  `copy_rows_from_readonly_source`.
- Browser thread can attach screenshots, JSON artifacts, and debug files through
  `CaptureArtifact`.

## Decisions Needing Orchestrator Approval

- Whether Sales Navigator identity canonicalization should remain profile-id
  only in all app-level dedupe logic, or whether any app needs to preserve auth
  tuple differences as separate identities.
