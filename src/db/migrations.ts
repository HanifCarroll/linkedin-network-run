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
  {
    id: 2,
    name: "baseline_optional",
    sql: `
      -- Confirmation is exact per-attempt match against the post-send audit,
      -- which does not need a pre-run sent-list baseline. Make
      -- reconciliations.baseline_id nullable so a run can reconcile without a
      -- captured baseline. The runner disables foreign_keys around the batch.
      CREATE TABLE reconciliations_new (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        baseline_id TEXT REFERENCES audit_baselines(id),
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

      INSERT INTO reconciliations_new
        (id, run_id, baseline_id, audit_id, mode, attempt_count, complete,
         competing_sender_absent, confirmed_attempt_ids_json, created_at,
         causal_sequence, sealed, scope)
      SELECT id, run_id, baseline_id, audit_id, mode, attempt_count, complete,
         competing_sender_absent, confirmed_attempt_ids_json, created_at,
         causal_sequence, sealed, scope
      FROM reconciliations;

      DROP TABLE reconciliations;
      ALTER TABLE reconciliations_new RENAME TO reconciliations;
    `,
  },
  {
    id: 3,
    name: "jobs",
    sql: `
      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        company TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        posting_url TEXT NOT NULL,
        hiring_team_json TEXT NOT NULL DEFAULT '[]',
        has_hiring_team INTEGER NOT NULL DEFAULT 0 CHECK(has_hiring_team IN (0, 1)),
        status TEXT NOT NULL DEFAULT 'collected'
          CHECK(status IN ('collected', 'favorite', 'drafted', 'sent')),
        message TEXT,
        collected_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        sent_at TEXT
      );
    `,
  },
  {
    id: 4,
    name: "jobs_captured_status",
    sql: `
      -- Split collection from enrichment: a job starts as 'captured' (raw id +
      -- title from the search XHR) and becomes 'collected' once enriched with
      -- company/location/hiring team. Recreate the table to widen the CHECK.
      CREATE TABLE jobs_new (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        company TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        posting_url TEXT NOT NULL,
        hiring_team_json TEXT NOT NULL DEFAULT '[]',
        has_hiring_team INTEGER NOT NULL DEFAULT 0 CHECK(has_hiring_team IN (0, 1)),
        status TEXT NOT NULL DEFAULT 'collected'
          CHECK(status IN ('captured', 'collected', 'favorite', 'drafted', 'sent')),
        message TEXT,
        collected_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        sent_at TEXT
      );

      INSERT INTO jobs_new
        (id, title, company, location, posting_url, hiring_team_json,
         has_hiring_team, status, message, collected_at, updated_at, sent_at)
      SELECT id, title, company, location, posting_url, hiring_team_json,
         has_hiring_team, status, message, collected_at, updated_at, sent_at
      FROM jobs;

      DROP TABLE jobs;
      ALTER TABLE jobs_new RENAME TO jobs;
    `,
  },
  {
    id: 5,
    name: "jobs_checked_at",
    sql: `
      -- Track when a stored posting was last liveness-checked so jobs check
      -- can resume: re-runs skip rows that already have a checked_at.
      ALTER TABLE jobs ADD COLUMN checked_at TEXT;
    `,
  },
  {
    id: 6,
    name: "jobs_detail_fields",
    sql: `
      -- Full posting-page details captured by the detail-enrich pass: the raw
      -- description plus the structured header fields that are always SSR'd
      -- into the view DOM (no salary -- it is only free text, when present).
      ALTER TABLE jobs ADD COLUMN description TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN workplace_type TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN employment_type TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN apply_method TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN promoted INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE jobs ADD COLUMN actively_reviewing INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE jobs ADD COLUMN posted_at TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN applicant_count TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN benefits_json TEXT NOT NULL DEFAULT '[]';
    `,
  },
  {
    id: 7,
    name: "jobs_classification",
    sql: `
      -- Manual two-field classification of a posting, set by the user after
      -- review: the work focus (the functional area the role centers on) and
      -- the product system (the tool or platform the role is built around).
      -- Brief free-text phrases, trimmed and length-bounded at the CLI.
      ALTER TABLE jobs ADD COLUMN work_focus TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN product_system TEXT NOT NULL DEFAULT '';
    `,
  },
  {
    id: 8,
    name: "jobs_classification_summaries",
    sql: `
      -- Longer structured prose alongside the brief classification phrases:
      -- what the role does day to day and what it builds, shown in the viewer
      -- as full sentences rather than pills.
      ALTER TABLE jobs ADD COLUMN work_summary TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN product_summary TEXT NOT NULL DEFAULT '';
    `,
  },
  {
    id: 9,
    name: "jobs_review",
    sql: `
      -- Draft review state, orthogonal to the status lifecycle: an editable
      -- subject line plus a review decision. Durable send targets are approved
      -- drafted jobs (review = 'approved'). The column-level CHECK is enforced
      -- by SQLite on ADD COLUMN (existing rows take the passing default).
      ALTER TABLE jobs ADD COLUMN subject TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN review TEXT NOT NULL DEFAULT 'needs_review'
        CHECK(review IN ('needs_review', 'approved', 'skipped'));
    `,
  },
  {
    id: 10,
    name: "jobs_capture",
    sql: `
      CREATE TABLE capture_runs (
        id TEXT PRIMARY KEY,
        source_url TEXT NOT NULL,
        search_config_json TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'active'
          CHECK(state IN ('active', 'complete', 'failed')),
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        checkpoint_json TEXT NOT NULL DEFAULT '{}',
        error TEXT
      );

      CREATE TABLE capture_pages (
        run_id TEXT NOT NULL REFERENCES capture_runs(id) ON DELETE CASCADE,
        page_identity TEXT NOT NULL,
        cursor TEXT,
        source_url TEXT NOT NULL,
        response_url TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        parser_version TEXT NOT NULL,
        item_count INTEGER,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        error TEXT,
        PRIMARY KEY(run_id, page_identity)
      );
      CREATE INDEX capture_pages_run_idx ON capture_pages(run_id, captured_at);
    `,
  },
  {
    id: 11,
    name: "jobs_normalization",
    sql: `
      CREATE TABLE capture_page_normalizations (
        run_id TEXT NOT NULL REFERENCES capture_runs(id) ON DELETE CASCADE,
        page_identity TEXT NOT NULL,
        parser_version TEXT NOT NULL,
        observed_count INTEGER NOT NULL CHECK(observed_count >= 0),
        normalized_at TEXT NOT NULL,
        PRIMARY KEY(run_id, page_identity)
      );

      CREATE TABLE job_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES capture_runs(id) ON DELETE CASCADE,
        page_identity TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        observed_title TEXT NOT NULL DEFAULT '',
        observed_at TEXT NOT NULL,
        UNIQUE(run_id, page_identity, job_id)
      );
      CREATE INDEX job_observations_job_idx ON job_observations(job_id);
    `,
  },
  {
    id: 12,
    name: "jobs_intake_filter",
    sql: `
      ALTER TABLE jobs ADD COLUMN fit TEXT NOT NULL DEFAULT 'pending'
        CHECK(fit IN ('pending', 'kept', 'dropped'));
      ALTER TABLE jobs ADD COLUMN filter_reason TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN matched_term TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN filter_policy_version TEXT NOT NULL DEFAULT '';
      ALTER TABLE jobs ADD COLUMN filtered_at TEXT;
    `,
  },
  {
    id: 13,
    name: "hubspot_imports",
    sql: `
      CREATE TABLE hubspot_imports (
        prospect_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        company_id TEXT,
        contact_id TEXT,
        deal_id TEXT,
        task_id TEXT,
        associations_complete INTEGER NOT NULL DEFAULT 0
          CHECK(associations_complete IN (0, 1)),
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        UNIQUE(job_id)
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
  database.exec("PRAGMA foreign_keys = OFF;");
  try {
    applyPending();
  } finally {
    database.exec("PRAGMA foreign_keys = ON;");
  }

  const row = database
    .query<{ version: number }, []>("SELECT COALESCE(MAX(id), 0) AS version FROM schema_migrations")
    .get();

  return { applied, currentVersion: row?.version ?? 0 };
}
