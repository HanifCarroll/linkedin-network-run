# Migration And Compatibility Handoff

## Orchestrator Update 2026-06-24

The orchestrator follow-up promoted the legacy importers beyond raw artifact
archival:

- Network import now copies legacy state files into
  `~/Library/Application Support/linkedin-tools/network-automation/` when that
  target has no existing files.
- Recruiter/agency import now promotes `outreach.sqlite` into
  `~/Library/Application Support/linkedin-tools/recruiter-agency-outreach/`, or
  converts `outreach.json` into that SQLite store when the old SQLite file is
  absent.
- Compatibility command sets now delegate all known app-owned commands to the
  Python app ports; `import-legacy-state` remains the migration shim.
- Temp-root rehearsal against the real local legacy state regenerated network
  status and recruiter/agency report from promoted Python state.

The historical gaps below are retained as handoff context, but the raw
artifact-only migration gap is no longer current for network and
recruiter/agency state.

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
- delegation of all known non-import commands to the Python app ports
- app-owned real-action gates such as `--allow-send` and `--allow-withdraw`

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

- network import preserves source files, stores raw artifacts, and promotes
  Python app state
- recruiter/agency import preserves JSON and SQLite files
- recruiter/agency read-only SQLite table snapshots are stored
- recruiter/agency import promotes usable Python SQLite app state from old
  `outreach.sqlite` or `outreach.json`
- missing opportunity source directory records a warning without creating
  source state
- compatibility help and delegated command paths
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

- Opportunity import currently preserves `/tmp/linkedin-opportunity-signals`
  artifacts when present and records a warning when absent; those artifacts are
  still consumed through the opportunity artifact commands rather than promoted
  to a separate app database.
- Browser dry-runs were not exercised in the original migration thread because
  browser behavior was owned by the browser and app-port workstreams. The
  orchestrator later recorded live dry-run evidence in
  `docs/cutover-acceptance-audit.md`.

## Integration Dependencies

- Thread 3 owns any future decision to move `/tmp/linkedin-opportunity-signals`
  artifacts into an app-owned database. The current port keeps opportunity
  intelligence artifact-first and recommend-only.

## Removal Plan After Cutover

1. Keep compatibility shims through parity audit and Hanif-approved cutover.
2. After Hanif-approved cutover, decide whether the old command names remain as
   permanent aliases or are removed from `pyproject.toml`.
3. Keep `legacy-imports.sqlite` read-only until final app stores are validated.
4. After final app stores are validated and backed up, archive the compatibility
   importer and remove shim-only placeholder branches.
5. Update `docs/cutover-checklist.md` when compatibility commands are no longer
   needed.

## Decisions Needing Orchestrator Approval

- Whether compatibility command names remain as permanent aliases after cutover
  or are removed from `pyproject.toml`.
- Whether opportunity-intel should keep importing `/tmp/linkedin-opportunity-signals`
  or move those runs through an app-owned artifact directory.
