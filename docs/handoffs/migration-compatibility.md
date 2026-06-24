# Migration And Compatibility Handoff

## Goal

Thread 7 built read-only legacy state importers and temporary compatibility
command shims for the Python LinkedIn tools cutover.

## Owned Paths Changed

- `packages/linkedin_storage/migrations.py`
- `packages/linkedin_storage/__init__.py`
- `apps/compat.py`
- `tests/test_migration_compat.py`
- `docs/handoffs/migration-compatibility.md`

## Commands Implemented

Compatibility entrypoints already registered in `pyproject.toml` are now wired:

- `linkedin-network-run`
- `recruiter-agency-outreach`
- `linkedin-opportunity-intel`

Each shim supports:

- `--help`
- `import-legacy-state --old-state-dir <path> --target-root <path> --json`
- no-send placeholder responses for known legacy commands
- JSON placeholder status for `status`, `plan`, `dashboard`, `report`,
  `last-run`, and `queue` where applicable

Real-action flags are blocked in the temporary shims:

- `--allow-send`
- `--allow-withdraw`

## Data Models Introduced

The migration compatibility store is SQLite at:

```text
~/Library/Application Support/linkedin-tools/legacy-imports.sqlite
```

Tables:

- `import_runs`: one row per import invocation.
- `legacy_artifacts`: exact source bytes for each imported file, keyed by
  import run, source app, and relative path.
- `import_warnings`: non-fatal source availability or read-only snapshot
  warnings.

Importer APIs:

- `import_legacy_network_state`
- `import_legacy_recruiter_agency_state`
- `import_legacy_opportunity_runs`
- `import_all_legacy_state`
- `latest_import_summary`

The recruiter/agency importer also adds read-only snapshots for known
`outreach.sqlite` tables using synthetic artifact paths like:

```text
outreach.sqlite::leads.json
```

Raw SQLite files are still imported as exact source bytes.

## Tests Added

`tests/test_migration_compat.py` covers:

- network import preserves source files and stores raw artifacts
- recruiter/agency import preserves JSON and SQLite files
- recruiter/agency read-only SQLite table snapshots are stored
- missing opportunity source directory records a warning without creating
  source state
- compatibility help/status/no-send paths
- real send flags are blocked
- network shim import command writes the compatibility SQLite store

## Verification Run

Passed:

```sh
uv run --extra dev pytest tests/test_migration_compat.py tests/test_scaffold.py
uv run --extra dev ruff check apps/compat.py packages/linkedin_storage tests/test_migration_compat.py
uv run --extra dev mypy apps/compat.py packages/linkedin_storage tests/test_migration_compat.py
```

Source-faithful grep was run:

```sh
rg -n "slice\(|substring\(|substr\(|visibleText|innerText|document\.title|legacy|fallback|infer|keyword|score|\[class\*=|h1|h2|h3|article|raw_text"
```

The new Python hits are intentional `legacy` migration names only. Broader hits
are in existing Go/JavaScript parity code and docs outside this thread's allowed
modification scope.

## Known Gaps

- Compatibility shims do not execute final app behavior yet. They return
  explicit `not_ported` placeholders for app-owned commands.
- Imported artifacts are preserved in the compatibility store, not transformed
  into final network, recruiter/agency, or opportunity-intel app tables.
- Opportunity import currently imports `/tmp/linkedin-opportunity-signals`
  when present and records a warning when absent.
- Browser dry-runs were not exercised in this thread because browser behavior is
  owned by the browser and app-port workstreams.

## Integration Dependencies

- Thread 4 must define stable network automation SQLite/read-model tables before
  these raw `legacy_artifacts` are transformed into final controller state.
- Thread 5 must define stable recruiter/agency tables before importer output is
  promoted beyond raw artifacts and read-only `outreach.sqlite` snapshots.
- Thread 3 must define opportunity-intel run/artifact contracts before
  `/tmp/linkedin-opportunity-signals` imports can become final app state.
- The orchestrator should decide whether final migrations consume raw artifacts
  directly from `legacy-imports.sqlite` or run one-time promotion commands into
  app-specific SQLite stores.

## Removal Plan After Cutover

1. Keep compatibility shims through parity audit and Hanif-approved cutover.
2. After all Python app commands pass parity smoke tests, change shims from
   placeholders to direct app dispatchers or remove the old command names from
   `pyproject.toml`.
3. Keep `legacy-imports.sqlite` read-only until final app stores are validated.
4. After final app stores are validated and backed up, archive the compatibility
   importer and remove shim-only placeholder branches.
5. Update `docs/cutover-checklist.md` when compatibility commands are no longer
   needed.

## Decisions Needing Orchestrator Approval

- Final destination schema for transformed legacy artifacts.
- Whether compatibility command names remain as permanent aliases after cutover
  or are removed from `pyproject.toml`.
- Whether opportunity-intel should keep importing `/tmp/linkedin-opportunity-signals`
  or move those runs through an app-owned artifact directory.
