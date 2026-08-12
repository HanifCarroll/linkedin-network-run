import type { Database } from "bun:sqlite";

type Migration = {
  id: number;
  name: string;
  sql: string;
};

const migrations: readonly Migration[] = [
  {
    id: 1,
    name: "initial",
    sql: `
      CREATE TABLE causal_records (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        UNIQUE(kind, receipt_id)
      );

      CREATE TABLE daily_runs (
        id TEXT PRIMARY KEY,
        local_date TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('active', 'done', 'blocked')),
        target INTEGER NOT NULL DEFAULT 30 CHECK(target = 30),
        preferred_per_source INTEGER NOT NULL DEFAULT 15 CHECK(preferred_per_source = 15),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        source_contract_version INTEGER NOT NULL DEFAULT 1,
        final_reconciliation_id TEXT
      );

      CREATE TABLE people (
        id TEXT PRIMARY KEY,
        sales_nav_id TEXT UNIQUE,
        public_url TEXT UNIQUE,
        lead_key TEXT UNIQUE,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(sales_nav_id IS NOT NULL OR public_url IS NOT NULL OR lead_key IS NOT NULL)
      );

      CREATE TABLE events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        run_id TEXT REFERENCES daily_runs(id),
        type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        dedupe_key TEXT
      );

      CREATE UNIQUE INDEX events_dedupe_key_unique
        ON events(dedupe_key) WHERE dedupe_key IS NOT NULL;

      CREATE TABLE source_observations (
        id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        person_id TEXT REFERENCES people(id),
        observed_name TEXT NOT NULL,
        observation_kind TEXT NOT NULL CHECK(observation_kind IN ('candidate', 'terminal')),
        row_state TEXT CHECK(row_state IN (
          'connectable', 'pending', 'connected', 'email_required', 'invalid', 'unknown'
        )),
        page_identity TEXT,
        stable_row_ids_json TEXT,
        next_control TEXT CHECK(
          next_control IS NULL OR next_control IN ('available', 'missing', 'disabled')
        ),
        observed_at TEXT NOT NULL,
        run_id TEXT REFERENCES daily_runs(id),
        source_contract_version INTEGER NOT NULL DEFAULT 1,
        reload_generation INTEGER NOT NULL DEFAULT 0,
        tick_id TEXT,
        row_order INTEGER,
        identity_evidence_json TEXT NOT NULL DEFAULT '{}',
        causal_sequence INTEGER REFERENCES causal_records(sequence),
        controller_candidate_json TEXT
          CHECK(controller_candidate_json IS NULL OR json_valid(controller_candidate_json)),
        UNIQUE(invocation_id, source_id, id)
      );

      CREATE TABLE person_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT NOT NULL REFERENCES people(id),
        kind TEXT NOT NULL CHECK(kind IN ('sales_nav_id', 'public_url', 'lead_key')),
        value TEXT NOT NULL,
        evidence TEXT NOT NULL,
        created_at TEXT NOT NULL,
        anchor_kind TEXT
          CHECK(anchor_kind IS NULL OR anchor_kind IN ('sales_nav_id', 'public_url', 'lead_key')),
        anchor_value TEXT,
        evidence_observation_id TEXT REFERENCES source_observations(id),
        evidence_invocation_id TEXT,
        evidence_source_id TEXT,
        UNIQUE(kind, value)
      );

      CREATE TABLE reservoir_entries (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        source_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES people(id),
        observation_id TEXT NOT NULL REFERENCES source_observations(id),
        status TEXT NOT NULL CHECK(status IN ('available', 'selected', 'consumed', 'ineligible')),
        added_at TEXT NOT NULL,
        selected_at TEXT,
        UNIQUE(run_id, person_id)
      );

      CREATE TABLE relationship_facts (
        id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL REFERENCES people(id),
        kind TEXT NOT NULL CHECK(kind IN (
          'pending', 'connected', 'do_not_contact', 'cross_workflow_message_sent',
          'unresolved_send', 'proven_no_send'
        )),
        effective_at TEXT NOT NULL,
        run_id TEXT REFERENCES daily_runs(id),
        evidence TEXT NOT NULL,
        supersedes_id TEXT REFERENCES relationship_facts(id)
      );

      CREATE INDEX relationship_facts_person_idx
        ON relationship_facts(person_id, effective_at);

      CREATE TABLE send_attempts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        person_id TEXT NOT NULL REFERENCES people(id),
        source_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('planned', 'possible', 'durable', 'proven_no_send')),
        attempted_at TEXT,
        resolved_at TEXT,
        evidence TEXT NOT NULL,
        possible_receipt_key TEXT,
        resolution_receipt_key TEXT,
        planned_causal_sequence INTEGER REFERENCES causal_records(sequence),
        possible_causal_sequence INTEGER REFERENCES causal_records(sequence),
        resolution_causal_sequence INTEGER REFERENCES causal_records(sequence),
        reservoir_entry_id TEXT REFERENCES reservoir_entries(id)
          ON UPDATE RESTRICT ON DELETE RESTRICT,
        plan_evidence_json TEXT
          CHECK(plan_evidence_json IS NULL OR json_valid(plan_evidence_json)),
        possible_evidence_json TEXT
          CHECK(possible_evidence_json IS NULL OR json_valid(possible_evidence_json)),
        prepare_receipt_json TEXT
          CHECK(prepare_receipt_json IS NULL OR json_valid(prepare_receipt_json)),
        prepare_binding_json TEXT
          CHECK(prepare_binding_json IS NULL OR json_valid(prepare_binding_json)),
        commit_started_at TEXT,
        commit_receipt_json TEXT
          CHECK(commit_receipt_json IS NULL OR json_valid(commit_receipt_json)),
        commit_causal_sequence INTEGER REFERENCES causal_records(sequence),
        UNIQUE(run_id, person_id)
      );

      CREATE UNIQUE INDEX one_active_attempt_per_person
        ON send_attempts(person_id)
        WHERE state IN ('planned', 'possible');

      CREATE TABLE audit_baselines (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        invocation_id TEXT NOT NULL,
        people_count INTEGER NOT NULL CHECK(people_count >= 0),
        competing_sender_absent INTEGER NOT NULL
          CHECK(competing_sender_absent IN (0, 1)),
        attempt_count_at_capture INTEGER NOT NULL CHECK(attempt_count_at_capture >= 0),
        captured_at TEXT NOT NULL,
        causal_sequence INTEGER REFERENCES causal_records(sequence),
        identities_json TEXT NOT NULL DEFAULT '[]'
          CHECK(json_valid(identities_json) AND json_type(identities_json) = 'array'),
        UNIQUE(run_id, invocation_id)
      );

      CREATE UNIQUE INDEX audit_baselines_one_per_run
        ON audit_baselines(run_id);

      CREATE TABLE audit_snapshots (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        invocation_id TEXT NOT NULL,
        people_count INTEGER NOT NULL CHECK(people_count >= 0),
        identities_json TEXT NOT NULL,
        names_json TEXT NOT NULL,
        complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
        competing_sender_absent INTEGER NOT NULL CHECK(competing_sender_absent IN (0, 1)),
        captured_at TEXT NOT NULL,
        baseline_id TEXT,
        contradictory_evidence INTEGER NOT NULL DEFAULT 0
          CHECK(contradictory_evidence IN (0, 1)),
        causal_sequence INTEGER REFERENCES causal_records(sequence),
        UNIQUE(run_id, invocation_id)
      );

      CREATE TABLE reconciliations (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        baseline_id TEXT NOT NULL REFERENCES audit_baselines(id),
        audit_id TEXT NOT NULL REFERENCES audit_snapshots(id),
        mode TEXT NOT NULL CHECK(mode IN ('exact', 'aggregate', 'mixed')),
        attempt_count INTEGER NOT NULL CHECK(attempt_count >= 0 AND attempt_count <= 30),
        complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
        competing_sender_absent INTEGER NOT NULL
          CHECK(competing_sender_absent IN (0, 1)),
        confirmed_attempt_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        causal_sequence INTEGER REFERENCES causal_records(sequence),
        sealed INTEGER NOT NULL DEFAULT 0 CHECK(sealed IN (0, 1)),
        scope TEXT NOT NULL DEFAULT 'final'
          CHECK(scope IN ('microbatch', 'final')),
        UNIQUE(run_id, audit_id)
      );

      CREATE TABLE reconciliation_attempts (
        reconciliation_id TEXT NOT NULL REFERENCES reconciliations(id),
        attempt_id TEXT NOT NULL REFERENCES send_attempts(id),
        evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('identity', 'name', 'aggregate')),
        matched_value TEXT,
        PRIMARY KEY(reconciliation_id, attempt_id)
      );
    `,
  },
];

export type MigrationResult = {
  applied: string[];
  currentVersion: number;
};

export function runMigrations(
  database: Database,
  targetVersion = Number.POSITIVE_INFINITY,
): MigrationResult {
  database.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
  `);

  const hasMigration = database.query("SELECT 1 FROM schema_migrations WHERE id = ? LIMIT 1");
  const recordMigration = database.query("INSERT INTO schema_migrations (id, name) VALUES (?, ?)");
  const applied: string[] = [];

  const applyPending = database.transaction(() => {
    for (const migration of migrations) {
      if (migration.id > targetVersion) continue;
      if (hasMigration.get(migration.id) !== null) continue;
      database.exec(migration.sql);
      recordMigration.run(migration.id, migration.name);
      applied.push(migration.name);
    }
  });
  applyPending();

  const row = database
    .query<{ version: number }, []>("SELECT COALESCE(MAX(id), 0) AS version FROM schema_migrations")
    .get();

  return { applied, currentVersion: row?.version ?? 0 };
}
