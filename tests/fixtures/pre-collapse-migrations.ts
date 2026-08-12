import type { Database } from "bun:sqlite";

type Migration = {
  id: number;
  name: string;
  sql: string;
};

const migrations: readonly Migration[] = [
  {
    id: 1,
    name: "network-domain",
    sql: `
      CREATE TABLE daily_runs (
        id TEXT PRIMARY KEY,
        local_date TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('active', 'done', 'blocked')),
        target INTEGER NOT NULL DEFAULT 30 CHECK(target = 30),
        preferred_per_source INTEGER NOT NULL DEFAULT 15 CHECK(preferred_per_source = 15),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
      );

      CREATE TABLE source_contracts (
        id TEXT PRIMARY KEY,
        saved_search_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL UNIQUE,
        preferred_allocation INTEGER NOT NULL DEFAULT 15,
        reservoir_target INTEGER NOT NULL DEFAULT 30,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
      );

      INSERT INTO source_contracts
        (id, saved_search_id, name, preferred_allocation, reservoir_target)
      VALUES
        ('marketing-agency-owners', '2004056810', 'Consulting - Marketing Agency Owners', 15, 30),
        ('fractional-coos', '1977917593', 'Consulting - Fractional COOs', 15, 30);

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

      CREATE TABLE person_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT NOT NULL REFERENCES people(id),
        kind TEXT NOT NULL CHECK(kind IN ('sales_nav_id', 'public_url', 'lead_key')),
        value TEXT NOT NULL,
        evidence TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(kind, value)
      );

      CREATE TABLE source_observations (
        id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL,
        source_id TEXT NOT NULL REFERENCES source_contracts(id),
        person_id TEXT REFERENCES people(id),
        observed_name TEXT NOT NULL,
        observation_kind TEXT NOT NULL CHECK(observation_kind IN ('candidate', 'terminal')),
        row_state TEXT CHECK(row_state IN ('connectable', 'pending', 'connected', 'email_required', 'invalid', 'unknown')),
        page_identity TEXT,
        stable_row_ids_json TEXT,
        next_control TEXT CHECK(next_control IS NULL OR next_control IN ('available', 'missing', 'disabled')),
        observed_at TEXT NOT NULL,
        UNIQUE(invocation_id, source_id, id)
      );

      CREATE TABLE reservoir_entries (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        source_id TEXT NOT NULL REFERENCES source_contracts(id),
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
        kind TEXT NOT NULL CHECK(kind IN ('pending', 'connected', 'do_not_contact', 'cross_workflow_message_sent', 'unresolved_send', 'proven_no_send')),
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
        source_id TEXT NOT NULL REFERENCES source_contracts(id),
        state TEXT NOT NULL CHECK(state IN ('planned', 'possible', 'durable', 'proven_no_send')),
        attempted_at TEXT,
        resolved_at TEXT,
        evidence TEXT NOT NULL,
        UNIQUE(run_id, person_id)
      );

      CREATE UNIQUE INDEX one_active_attempt_per_person
        ON send_attempts(person_id)
        WHERE state IN ('planned', 'possible');

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
        UNIQUE(run_id, invocation_id)
      );

      CREATE TABLE events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        run_id TEXT REFERENCES daily_runs(id),
        type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        occurred_at TEXT NOT NULL
      );

      CREATE TRIGGER events_no_update
      BEFORE UPDATE ON events
      BEGIN
        SELECT RAISE(ABORT, 'events are immutable');
      END;

      CREATE TRIGGER events_no_delete
      BEFORE DELETE ON events
      BEGIN
        SELECT RAISE(ABORT, 'events are immutable');
      END;
    `,
  },
  {
    id: 2,
    name: "review-hardening",
    sql: `
      ALTER TABLE daily_runs
        ADD COLUMN source_contract_version INTEGER NOT NULL DEFAULT 1;
      ALTER TABLE daily_runs
        ADD COLUMN final_reconciliation_id TEXT;
      ALTER TABLE source_contracts
        ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1);

      ALTER TABLE source_observations
        ADD COLUMN run_id TEXT REFERENCES daily_runs(id);
      ALTER TABLE source_observations
        ADD COLUMN source_contract_version INTEGER NOT NULL DEFAULT 1;
      ALTER TABLE source_observations
        ADD COLUMN reload_generation INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE source_observations
        ADD COLUMN tick_id TEXT;
      ALTER TABLE source_observations
        ADD COLUMN row_order INTEGER;
      ALTER TABLE source_observations
        ADD COLUMN identity_evidence_json TEXT NOT NULL DEFAULT '{}';

      ALTER TABLE send_attempts ADD COLUMN possible_receipt_key TEXT;
      ALTER TABLE send_attempts ADD COLUMN resolution_receipt_key TEXT;

      ALTER TABLE audit_snapshots
        ADD COLUMN baseline_id TEXT;
      ALTER TABLE audit_snapshots
        ADD COLUMN contradictory_evidence INTEGER NOT NULL DEFAULT 0
          CHECK(contradictory_evidence IN (0, 1));

      ALTER TABLE events ADD COLUMN dedupe_key TEXT;
      CREATE UNIQUE INDEX events_dedupe_key_unique
        ON events(dedupe_key) WHERE dedupe_key IS NOT NULL;

      CREATE TABLE audit_baselines (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        invocation_id TEXT NOT NULL,
        people_count INTEGER NOT NULL CHECK(people_count >= 0),
        competing_sender_absent INTEGER NOT NULL
          CHECK(competing_sender_absent IN (0, 1)),
        attempt_count_at_capture INTEGER NOT NULL CHECK(attempt_count_at_capture >= 0),
        captured_at TEXT NOT NULL,
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
        UNIQUE(run_id, audit_id)
      );

      CREATE TABLE reconciliation_attempts (
        reconciliation_id TEXT NOT NULL REFERENCES reconciliations(id),
        attempt_id TEXT NOT NULL REFERENCES send_attempts(id),
        evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('identity', 'name', 'aggregate')),
        PRIMARY KEY(reconciliation_id, attempt_id)
      );

      CREATE TRIGGER source_contract_insert_guard
      BEFORE INSERT ON source_contracts
      WHEN NEW.preferred_allocation != 15 OR NEW.reservoir_target != 30
      BEGIN
        SELECT RAISE(ABORT, 'source contract requires preferred allocation 15 and reservoir target 30');
      END;

      CREATE TRIGGER source_contract_update_guard
      BEFORE UPDATE OF preferred_allocation, reservoir_target ON source_contracts
      WHEN NEW.preferred_allocation != 15 OR NEW.reservoir_target != 30
      BEGIN
        SELECT RAISE(ABORT, 'source contract requires preferred allocation 15 and reservoir target 30');
      END;

      CREATE TRIGGER send_attempt_capacity_insert_guard
      BEFORE INSERT ON send_attempts
      WHEN NEW.state IN ('planned', 'possible', 'durable')
       AND (
         SELECT COUNT(*) FROM send_attempts
         WHERE run_id = NEW.run_id
           AND state IN ('planned', 'possible', 'durable')
       ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'daily send capacity exceeded');
      END;

      CREATE TRIGGER send_attempt_active_run_insert_guard
      BEFORE INSERT ON send_attempts
      WHEN NOT EXISTS (
        SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'active'
      )
      BEGIN
        SELECT RAISE(ABORT, 'send attempts require an active run');
      END;

      CREATE TRIGGER send_attempt_capacity_update_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'proven_no_send'
       AND NEW.state IN ('planned', 'possible', 'durable')
       AND (
         SELECT COUNT(*) FROM send_attempts
         WHERE run_id = NEW.run_id
           AND state IN ('planned', 'possible', 'durable')
       ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'daily send capacity exceeded');
      END;

      CREATE TRIGGER reservoir_total_insert_guard
      BEFORE INSERT ON reservoir_entries
      WHEN (
        SELECT COUNT(*) FROM reservoir_entries
        WHERE run_id = NEW.run_id
          AND status IN ('available', 'selected')
      ) >= 60
      BEGIN
        SELECT RAISE(ABORT, 'run reservoir capacity exceeded');
      END;

      CREATE TRIGGER reservoir_source_insert_guard
      BEFORE INSERT ON reservoir_entries
      WHEN (
        SELECT COUNT(*) FROM reservoir_entries
        WHERE run_id = NEW.run_id
          AND source_id = NEW.source_id
          AND status IN ('available', 'selected')
      ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'source reservoir capacity exceeded');
      END;

      CREATE TRIGGER reservoir_total_update_guard
      BEFORE UPDATE OF status ON reservoir_entries
      WHEN OLD.status NOT IN ('available', 'selected')
       AND NEW.status IN ('available', 'selected')
       AND (
         SELECT COUNT(*) FROM reservoir_entries
         WHERE run_id = NEW.run_id
           AND status IN ('available', 'selected')
       ) >= 60
      BEGIN
        SELECT RAISE(ABORT, 'run reservoir capacity exceeded');
      END;

      CREATE TRIGGER reservoir_source_update_guard
      BEFORE UPDATE OF status ON reservoir_entries
      WHEN OLD.status NOT IN ('available', 'selected')
       AND NEW.status IN ('available', 'selected')
       AND (
         SELECT COUNT(*) FROM reservoir_entries
         WHERE run_id = NEW.run_id
           AND source_id = NEW.source_id
           AND status IN ('available', 'selected')
       ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'source reservoir capacity exceeded');
      END;

      CREATE TRIGGER reservoir_observation_contract_guard
      BEFORE INSERT ON reservoir_entries
      WHEN NOT EXISTS (
        SELECT 1 FROM source_observations o
        WHERE o.id = NEW.observation_id
          AND o.run_id = NEW.run_id
          AND o.source_id = NEW.source_id
          AND o.person_id = NEW.person_id
          AND o.row_state = 'connectable'
          AND o.source_contract_version = (
            SELECT source_contract_version FROM daily_runs WHERE id = NEW.run_id
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir entry must match a connectable run observation');
      END;

      CREATE TRIGGER terminal_observation_scope_guard
      BEFORE INSERT ON source_observations
      WHEN NEW.observation_kind = 'terminal'
       AND (
         NEW.run_id IS NULL
         OR NEW.tick_id IS NULL
         OR NOT EXISTS (
           SELECT 1 FROM daily_runs r
           JOIN source_contracts s ON s.id = NEW.source_id
           WHERE r.id = NEW.run_id
             AND r.source_contract_version = NEW.source_contract_version
             AND s.version = NEW.source_contract_version
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'terminal observation requires exact run and source contract version');
      END;

      CREATE TRIGGER audit_baseline_run_guard
      BEFORE INSERT ON audit_snapshots
      WHEN NEW.baseline_id IS NOT NULL
       AND NOT EXISTS (
         SELECT 1 FROM audit_baselines
         WHERE id = NEW.baseline_id AND run_id = NEW.run_id
       )
      BEGIN
        SELECT RAISE(ABORT, 'audit baseline must belong to the same run');
      END;

      CREATE TRIGGER reconciliation_run_guard
      BEFORE INSERT ON reconciliations
      WHEN NOT EXISTS (
        SELECT 1
        FROM audit_baselines b
        JOIN audit_snapshots a ON a.id = NEW.audit_id
        WHERE b.id = NEW.baseline_id
          AND b.run_id = NEW.run_id
          AND a.run_id = NEW.run_id
          AND a.baseline_id = NEW.baseline_id
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation evidence must belong to the same run and baseline');
      END;

      CREATE TRIGGER reconciliation_attempt_run_guard
      BEFORE INSERT ON reconciliation_attempts
      WHEN NOT EXISTS (
        SELECT 1
        FROM reconciliations r
        JOIN send_attempts a ON a.id = NEW.attempt_id
        WHERE r.id = NEW.reconciliation_id
          AND r.run_id = a.run_id
          AND a.state = 'durable'
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation attempt must be durable and belong to the run');
      END;

      CREATE TRIGGER audit_baselines_no_update
      BEFORE UPDATE ON audit_baselines
      BEGIN
        SELECT RAISE(ABORT, 'audit baselines are immutable');
      END;

      CREATE TRIGGER audit_baselines_no_delete
      BEFORE DELETE ON audit_baselines
      BEGIN
        SELECT RAISE(ABORT, 'audit baselines are immutable');
      END;

      CREATE TRIGGER audit_snapshots_no_update
      BEFORE UPDATE ON audit_snapshots
      BEGIN
        SELECT RAISE(ABORT, 'audit snapshots are immutable');
      END;

      CREATE TRIGGER audit_snapshots_no_delete
      BEFORE DELETE ON audit_snapshots
      BEGIN
        SELECT RAISE(ABORT, 'audit snapshots are immutable');
      END;

      CREATE TRIGGER reconciliations_no_update
      BEFORE UPDATE ON reconciliations
      BEGIN
        SELECT RAISE(ABORT, 'reconciliations are immutable');
      END;

      CREATE TRIGGER reconciliations_no_delete
      BEFORE DELETE ON reconciliations
      BEGIN
        SELECT RAISE(ABORT, 'reconciliations are immutable');
      END;

      CREATE TRIGGER reconciliation_attempts_no_update
      BEFORE UPDATE ON reconciliation_attempts
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation attempts are immutable');
      END;

      CREATE TRIGGER reconciliation_attempts_no_delete
      BEFORE DELETE ON reconciliation_attempts
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation attempts are immutable');
      END;

      CREATE TRIGGER daily_run_done_insert_guard
      BEFORE INSERT ON daily_runs
      WHEN NEW.status = 'done'
      BEGIN
        SELECT RAISE(ABORT, 'daily run cannot be inserted as done');
      END;

      CREATE TRIGGER daily_run_done_update_guard
      BEFORE UPDATE OF status ON daily_runs
      WHEN NEW.status = 'done'
       AND (
         NEW.final_reconciliation_id IS NULL
         OR (SELECT COUNT(*) FROM send_attempts
             WHERE run_id = NEW.id AND state = 'durable') != 30
         OR (SELECT COUNT(*) FROM send_attempts
             WHERE run_id = NEW.id AND state IN ('planned', 'possible')) != 0
         OR NOT EXISTS (
           SELECT 1 FROM reconciliations r
           WHERE r.id = NEW.final_reconciliation_id
             AND r.run_id = NEW.id
             AND r.attempt_count = 30
             AND r.complete = 1
             AND r.competing_sender_absent = 1
             AND (SELECT COUNT(*) FROM reconciliation_attempts ra
                  WHERE ra.reconciliation_id = r.id) = 30
             AND NOT EXISTS (
               SELECT 1 FROM send_attempts a
               WHERE a.run_id = NEW.id
                 AND a.state = 'durable'
                 AND NOT EXISTS (
                   SELECT 1 FROM reconciliation_attempts ra
                   WHERE ra.reconciliation_id = r.id
                     AND ra.attempt_id = a.id
                 )
             )
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid done transition');
      END;
    `,
  },
  {
    id: 3,
    name: "causal-lockdown",
    sql: `
      CREATE TABLE causal_records (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        UNIQUE(kind, receipt_id)
      );

      ALTER TABLE send_attempts
        ADD COLUMN planned_causal_sequence INTEGER REFERENCES causal_records(sequence);
      ALTER TABLE send_attempts
        ADD COLUMN possible_causal_sequence INTEGER REFERENCES causal_records(sequence);
      ALTER TABLE send_attempts
        ADD COLUMN resolution_causal_sequence INTEGER REFERENCES causal_records(sequence);

      ALTER TABLE source_observations
        ADD COLUMN causal_sequence INTEGER REFERENCES causal_records(sequence);
      ALTER TABLE audit_baselines
        ADD COLUMN causal_sequence INTEGER REFERENCES causal_records(sequence);
      ALTER TABLE audit_snapshots
        ADD COLUMN causal_sequence INTEGER REFERENCES causal_records(sequence);
      ALTER TABLE reconciliations
        ADD COLUMN causal_sequence INTEGER REFERENCES causal_records(sequence);

      ALTER TABLE person_aliases ADD COLUMN anchor_kind TEXT
        CHECK(anchor_kind IS NULL OR anchor_kind IN ('sales_nav_id', 'public_url', 'lead_key'));
      ALTER TABLE person_aliases ADD COLUMN anchor_value TEXT;
      ALTER TABLE person_aliases ADD COLUMN evidence_observation_id TEXT
        REFERENCES source_observations(id);
      ALTER TABLE person_aliases ADD COLUMN evidence_invocation_id TEXT;
      ALTER TABLE person_aliases ADD COLUMN evidence_source_id TEXT
        REFERENCES source_contracts(id);

      DROP TRIGGER send_attempt_capacity_update_guard;
      DROP TRIGGER reservoir_total_update_guard;
      DROP TRIGGER reservoir_source_update_guard;

      CREATE TRIGGER causal_records_no_update
      BEFORE UPDATE ON causal_records
      BEGIN
        SELECT RAISE(ABORT, 'causal records are immutable');
      END;

      CREATE TRIGGER causal_records_no_delete
      BEFORE DELETE ON causal_records
      BEGIN
        SELECT RAISE(ABORT, 'causal records are immutable');
      END;

      CREATE TRIGGER send_attempt_plan_receipt_guard
      BEFORE INSERT ON send_attempts
      WHEN NEW.state = 'planned'
       AND NOT EXISTS (
         SELECT 1 FROM causal_records c
         WHERE c.sequence = NEW.planned_causal_sequence
           AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
           AND c.kind = 'attempt_plan'
           AND c.receipt_id = NEW.id
           AND json_extract(c.payload_json, '$.attemptId') = NEW.id
           AND json_extract(c.payload_json, '$.runId') = NEW.run_id
           AND json_extract(c.payload_json, '$.personId') = NEW.person_id
           AND json_extract(c.payload_json, '$.sourceId') = NEW.source_id
       )
      BEGIN
        SELECT RAISE(ABORT, 'planned attempt requires its immutable receipt');
      END;

      CREATE TRIGGER send_attempt_identity_no_update
      BEFORE UPDATE OF id, run_id, person_id, source_id ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'send attempt identity is immutable');
      END;

      CREATE TRIGGER send_attempts_no_delete
      BEFORE DELETE ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'send attempts are immutable records');
      END;

      CREATE TRIGGER send_attempt_capacity_update_guard_v2
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state IN ('planned', 'possible', 'durable')
       AND (
         SELECT COUNT(*) FROM send_attempts
         WHERE run_id = NEW.run_id
           AND id != OLD.id
           AND state IN ('planned', 'possible', 'durable')
       ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'daily send capacity exceeded');
      END;

      CREATE TRIGGER send_attempt_active_run_update_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state != OLD.state
       AND NOT EXISTS (
         SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'active'
       )
      BEGIN
        SELECT RAISE(ABORT, 'send attempt transitions require an active run');
      END;

      CREATE TRIGGER send_attempt_transition_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state != OLD.state
       AND NOT (
         OLD.state = 'planned'
         AND NEW.state = 'possible'
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.possible_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.kind = 'attempt_possible'
             AND c.receipt_id = NEW.possible_receipt_key
             AND json_extract(c.payload_json, '$.attemptId') = NEW.id
             AND json_extract(c.payload_json, '$.runId') = NEW.run_id
             AND json_extract(c.payload_json, '$.personId') = NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') = NEW.source_id
             AND json_extract(c.payload_json, '$.attemptedAt') = NEW.attempted_at
           )
       )
       AND NOT (
         OLD.state = 'possible'
         AND NEW.state IN ('durable', 'proven_no_send')
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.resolution_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.kind = CASE NEW.state
               WHEN 'durable' THEN 'attempt_durable'
               ELSE 'attempt_proven_no_send'
             END
             AND c.receipt_id = NEW.resolution_receipt_key
             AND json_extract(c.payload_json, '$.attemptId') = NEW.id
             AND json_extract(c.payload_json, '$.runId') = NEW.run_id
             AND json_extract(c.payload_json, '$.personId') = NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') = NEW.source_id
             AND json_extract(c.payload_json, '$.resolvedAt') = NEW.resolved_at
             AND json_extract(c.payload_json, '$.state') = NEW.state
           )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid send attempt transition receipt');
      END;

      CREATE TRIGGER send_attempt_possible_receipt_mutation_guard
      BEFORE UPDATE OF attempted_at, possible_receipt_key, possible_causal_sequence ON send_attempts
      WHEN NOT (
        OLD.state = 'planned'
        AND NEW.state = 'possible'
        AND OLD.possible_causal_sequence IS NULL
        AND NEW.possible_causal_sequence IS NOT NULL
      )
      BEGIN
        SELECT RAISE(ABORT, 'possible receipt fields may only be set during the planned transition');
      END;

      CREATE TRIGGER send_attempt_resolution_receipt_mutation_guard
      BEFORE UPDATE OF resolved_at, resolution_receipt_key, resolution_causal_sequence ON send_attempts
      WHEN NOT (
        OLD.state = 'possible'
        AND NEW.state IN ('durable', 'proven_no_send')
        AND OLD.resolution_causal_sequence IS NULL
        AND NEW.resolution_causal_sequence IS NOT NULL
      )
      BEGIN
        SELECT RAISE(ABORT, 'resolution receipt fields may only be set during resolution');
      END;

      CREATE TRIGGER send_attempt_plan_receipt_no_update
      BEFORE UPDATE OF planned_causal_sequence ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'planned attempt receipt is immutable');
      END;

      CREATE TRIGGER send_attempt_evidence_mutation_guard
      BEFORE UPDATE OF evidence ON send_attempts
      WHEN NEW.state = OLD.state
      BEGIN
        SELECT RAISE(ABORT, 'attempt evidence may only change with a receipt-backed transition');
      END;

      CREATE TRIGGER reservoir_capacity_update_guard_v2
      BEFORE UPDATE OF run_id, source_id, status ON reservoir_entries
      WHEN NEW.status IN ('available', 'selected')
       AND (
         (SELECT COUNT(*) FROM reservoir_entries
          WHERE run_id = NEW.run_id
            AND id != OLD.id
            AND status IN ('available', 'selected')) >= 60
         OR
         (SELECT COUNT(*) FROM reservoir_entries
          WHERE run_id = NEW.run_id
            AND source_id = NEW.source_id
            AND id != OLD.id
            AND status IN ('available', 'selected')) >= 30
       )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir update exceeds run or source capacity');
      END;

      CREATE TRIGGER reservoir_observation_update_guard
      BEFORE UPDATE OF run_id, source_id, person_id, observation_id, status ON reservoir_entries
      WHEN NOT EXISTS (
        SELECT 1 FROM source_observations o
        WHERE o.id = NEW.observation_id
          AND o.run_id = NEW.run_id
          AND o.source_id = NEW.source_id
          AND o.person_id = NEW.person_id
          AND o.row_state = 'connectable'
          AND o.source_contract_version = (
            SELECT source_contract_version FROM daily_runs WHERE id = NEW.run_id
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir update must preserve its observation contract');
      END;

      CREATE TRIGGER source_observation_receipt_guard
      BEFORE INSERT ON source_observations
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = CASE NEW.observation_kind
            WHEN 'candidate' THEN 'candidate_observation'
            ELSE 'terminal_observation'
          END
          AND c.receipt_id = NEW.id
      )
      BEGIN
        SELECT RAISE(ABORT, 'source observation requires its immutable receipt');
      END;

      CREATE TRIGGER source_observations_fields_no_update
      BEFORE UPDATE OF id, invocation_id, run_id, source_id, source_contract_version,
        observed_name, observation_kind, row_state, page_identity, stable_row_ids_json,
        next_control, observed_at, reload_generation, tick_id, row_order,
        identity_evidence_json, causal_sequence
      ON source_observations
      BEGIN
        SELECT RAISE(ABORT, 'source observation evidence is immutable');
      END;

      CREATE TRIGGER source_observations_person_finalize_guard
      BEFORE UPDATE OF person_id ON source_observations
      WHEN OLD.person_id IS NOT NULL OR NEW.person_id IS NULL
      BEGIN
        SELECT RAISE(ABORT, 'source observation person may only be finalized once');
      END;

      CREATE TRIGGER source_observations_no_delete
      BEFORE DELETE ON source_observations
      BEGIN
        SELECT RAISE(ABORT, 'source observations are immutable');
      END;

      CREATE TRIGGER audit_baseline_receipt_guard
      BEFORE INSERT ON audit_baselines
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = 'audit_baseline'
          AND c.receipt_id = NEW.id
      )
      BEGIN
        SELECT RAISE(ABORT, 'audit baseline requires its immutable receipt');
      END;

      CREATE TRIGGER audit_snapshot_receipt_guard
      BEFORE INSERT ON audit_snapshots
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = 'audit_snapshot'
          AND c.receipt_id = NEW.id
      )
      BEGIN
        SELECT RAISE(ABORT, 'audit snapshot requires its immutable receipt');
      END;

      CREATE TRIGGER reconciliation_receipt_guard
      BEFORE INSERT ON reconciliations
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = 'reconciliation'
          AND c.receipt_id = NEW.id
          AND NEW.causal_sequence > (
            SELECT causal_sequence FROM audit_snapshots WHERE id = NEW.audit_id
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation requires its immutable receipt');
      END;

      CREATE TRIGGER reconciliation_attempt_causal_guard
      BEFORE INSERT ON reconciliation_attempts
      WHEN NOT EXISTS (
        SELECT 1
        FROM reconciliations r
        JOIN audit_snapshots s ON s.id = r.audit_id
        JOIN send_attempts a ON a.id = NEW.attempt_id
        WHERE r.id = NEW.reconciliation_id
          AND a.possible_causal_sequence IS NOT NULL
          AND s.causal_sequence IS NOT NULL
          AND s.causal_sequence > a.possible_causal_sequence
      )
      BEGIN
        SELECT RAISE(ABORT, 'audit must be causally after every confirmed attempt');
      END;

      CREATE TRIGGER person_alias_structured_evidence_guard
      BEFORE INSERT ON person_aliases
      WHEN NEW.anchor_kind IS NULL
        OR NEW.anchor_value IS NULL
        OR NEW.evidence_observation_id IS NULL
        OR NEW.evidence_invocation_id IS NULL
        OR NEW.evidence_source_id IS NULL
        OR NEW.anchor_kind = NEW.kind
        OR NEW.anchor_value = NEW.value
        OR NOT EXISTS (
          SELECT 1 FROM people p
          WHERE p.id = NEW.person_id
            AND CASE NEW.anchor_kind
              WHEN 'sales_nav_id' THEN p.sales_nav_id
              WHEN 'public_url' THEN p.public_url
              ELSE p.lead_key
            END = NEW.anchor_value
            AND CASE NEW.kind
              WHEN 'sales_nav_id' THEN p.sales_nav_id
              WHEN 'public_url' THEN p.public_url
              ELSE p.lead_key
            END = NEW.value
        )
        OR NOT EXISTS (
          SELECT 1 FROM source_observations o
          WHERE o.id = NEW.evidence_observation_id
            AND o.invocation_id = NEW.evidence_invocation_id
            AND o.source_id = NEW.evidence_source_id
            AND o.person_id = NEW.person_id
            AND CASE NEW.kind
              WHEN 'sales_nav_id' THEN json_extract(o.identity_evidence_json, '$.salesNavId')
              WHEN 'public_url' THEN json_extract(o.identity_evidence_json, '$.publicUrl')
              ELSE json_extract(o.identity_evidence_json, '$.leadKey')
            END = NEW.value
            AND CASE NEW.anchor_kind
              WHEN 'sales_nav_id' THEN json_extract(o.identity_evidence_json, '$.salesNavId')
              WHEN 'public_url' THEN json_extract(o.identity_evidence_json, '$.publicUrl')
              ELSE json_extract(o.identity_evidence_json, '$.leadKey')
            END = NEW.anchor_value
        )
      BEGIN
        SELECT RAISE(ABORT, 'alias requires exact immutable source evidence for both identities');
      END;

      CREATE TRIGGER person_aliases_no_update
      BEFORE UPDATE ON person_aliases
      BEGIN
        SELECT RAISE(ABORT, 'person aliases are immutable');
      END;

      CREATE TRIGGER person_aliases_no_delete
      BEFORE DELETE ON person_aliases
      BEGIN
        SELECT RAISE(ABORT, 'person aliases are immutable');
      END;

      CREATE TRIGGER people_alias_identity_no_update
      BEFORE UPDATE OF sales_nav_id, public_url, lead_key ON people
      WHEN EXISTS (
        SELECT 1 FROM person_aliases a
        WHERE a.person_id = OLD.id
          AND (
            (a.kind = 'sales_nav_id' AND OLD.sales_nav_id IS NOT NEW.sales_nav_id)
            OR (a.kind = 'public_url' AND OLD.public_url IS NOT NEW.public_url)
            OR (a.kind = 'lead_key' AND OLD.lead_key IS NOT NEW.lead_key)
            OR (a.anchor_kind = 'sales_nav_id' AND OLD.sales_nav_id IS NOT NEW.sales_nav_id)
            OR (a.anchor_kind = 'public_url' AND OLD.public_url IS NOT NEW.public_url)
            OR (a.anchor_kind = 'lead_key' AND OLD.lead_key IS NOT NEW.lead_key)
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'identities supporting aliases are immutable');
      END;

      CREATE TRIGGER daily_run_done_causal_guard
      BEFORE UPDATE OF status ON daily_runs
      WHEN NEW.status = 'done'
       AND EXISTS (
         SELECT 1
         FROM reconciliations r
         JOIN audit_snapshots s ON s.id = r.audit_id
         JOIN reconciliation_attempts ra ON ra.reconciliation_id = r.id
         JOIN send_attempts a ON a.id = ra.attempt_id
         WHERE r.id = NEW.final_reconciliation_id
           AND (
             a.possible_causal_sequence IS NULL
             OR s.causal_sequence IS NULL
             OR s.causal_sequence <= a.possible_causal_sequence
           )
       )
      BEGIN
        SELECT RAISE(ABORT, 'final audit is not causally after every attempt');
      END;

      CREATE TRIGGER daily_run_done_evidence_guard
      BEFORE UPDATE OF status ON daily_runs
      WHEN NEW.status = 'done'
       AND NOT EXISTS (
         SELECT 1
         FROM reconciliations rec
         JOIN audit_snapshots audit ON audit.id = rec.audit_id
         JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
         WHERE rec.id = NEW.final_reconciliation_id
           AND rec.run_id = NEW.id
           AND audit.run_id = NEW.id
           AND baseline.run_id = NEW.id
           AND audit.baseline_id = baseline.id
           AND baseline.attempt_count_at_capture = 0
           AND baseline.competing_sender_absent = 1
           AND audit.complete = 1
           AND audit.competing_sender_absent = 1
           AND audit.contradictory_evidence = 0
           AND audit.people_count - baseline.people_count = 30
           AND rec.attempt_count = 30
           AND rec.complete = 1
           AND rec.competing_sender_absent = 1
           AND baseline.causal_sequence IS NOT NULL
           AND audit.causal_sequence > baseline.causal_sequence
           AND rec.causal_sequence > audit.causal_sequence
           AND NOT EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.run_id = NEW.id
               AND (
                 attempt.planned_causal_sequence IS NULL
                 OR attempt.planned_causal_sequence <= baseline.causal_sequence
               )
           )
       )
      BEGIN
        SELECT RAISE(ABORT, 'final reconciliation lacks exact causal audit evidence');
      END;

      CREATE TRIGGER daily_runs_done_no_update
      BEFORE UPDATE ON daily_runs
      WHEN OLD.status = 'done'
      BEGIN
        SELECT RAISE(ABORT, 'done runs are immutable');
      END;

      CREATE TRIGGER daily_runs_done_no_delete
      BEFORE DELETE ON daily_runs
      WHEN OLD.status = 'done'
      BEGIN
        SELECT RAISE(ABORT, 'done runs are immutable');
      END;

      CREATE TRIGGER send_attempts_done_no_insert
      BEFORE INSERT ON send_attempts
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run attempts are immutable');
      END;

      CREATE TRIGGER send_attempts_done_no_update
      BEFORE UPDATE ON send_attempts
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run attempts are immutable');
      END;

      CREATE TRIGGER send_attempts_done_no_delete
      BEFORE DELETE ON send_attempts
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run attempts are immutable');
      END;

      CREATE TRIGGER reservoir_done_no_insert
      BEFORE INSERT ON reservoir_entries
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run reservoir is immutable');
      END;

      CREATE TRIGGER reservoir_done_no_update
      BEFORE UPDATE ON reservoir_entries
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run reservoir is immutable');
      END;

      CREATE TRIGGER reservoir_done_no_delete
      BEFORE DELETE ON reservoir_entries
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run reservoir is immutable');
      END;

      CREATE TRIGGER source_observations_done_no_insert
      BEFORE INSERT ON source_observations
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run observations are immutable');
      END;

      CREATE TRIGGER source_observations_done_no_person_update
      BEFORE UPDATE OF person_id ON source_observations
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run observations are immutable');
      END;

      CREATE TRIGGER audit_baselines_done_no_insert
      BEFORE INSERT ON audit_baselines
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run audit baselines are immutable');
      END;

      CREATE TRIGGER audit_snapshots_done_no_insert
      BEFORE INSERT ON audit_snapshots
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run audit snapshots are immutable');
      END;

      CREATE TRIGGER reconciliations_done_no_insert
      BEFORE INSERT ON reconciliations
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run reconciliations are immutable');
      END;

      CREATE TRIGGER reconciliation_attempts_done_no_insert
      BEFORE INSERT ON reconciliation_attempts
      WHEN EXISTS (
        SELECT 1 FROM reconciliations rec
        JOIN daily_runs r ON r.id = rec.run_id
        WHERE rec.id = NEW.reconciliation_id AND r.status = 'done'
      )
      BEGIN
        SELECT RAISE(ABORT, 'done run reconciliation evidence is immutable');
      END;

      CREATE TRIGGER people_done_no_update
      BEFORE UPDATE ON people
      WHEN EXISTS (
        SELECT 1 FROM send_attempts a
        JOIN daily_runs r ON r.id = a.run_id
        WHERE a.person_id = OLD.id AND r.status = 'done'
      )
      BEGIN
        SELECT RAISE(ABORT, 'people referenced by done runs are immutable');
      END;

      CREATE TRIGGER people_done_no_delete
      BEFORE DELETE ON people
      WHEN EXISTS (
        SELECT 1 FROM send_attempts a
        JOIN daily_runs r ON r.id = a.run_id
        WHERE a.person_id = OLD.id AND r.status = 'done'
      )
      BEGIN
        SELECT RAISE(ABORT, 'people referenced by done runs are immutable');
      END;

      CREATE TRIGGER person_alias_done_no_insert
      BEFORE INSERT ON person_aliases
      WHEN EXISTS (
        SELECT 1 FROM send_attempts a
        JOIN daily_runs r ON r.id = a.run_id
        WHERE a.person_id = NEW.person_id AND r.status = 'done'
      )
      BEGIN
        SELECT RAISE(ABORT, 'done run aliases are immutable');
      END;

      CREATE TRIGGER relationship_facts_done_no_insert
      BEFORE INSERT ON relationship_facts
      WHEN NEW.run_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run relationship facts are immutable');
      END;

      CREATE TRIGGER relationship_facts_done_no_update
      BEFORE UPDATE ON relationship_facts
      WHEN OLD.run_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run relationship facts are immutable');
      END;

      CREATE TRIGGER relationship_facts_done_no_delete
      BEFORE DELETE ON relationship_facts
      WHEN OLD.run_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run relationship facts are immutable');
      END;

      CREATE TRIGGER source_contracts_done_no_update
      BEFORE UPDATE ON source_contracts
      WHEN EXISTS (
        SELECT 1 FROM daily_runs
        WHERE status = 'done' AND source_contract_version = OLD.version
      )
      BEGIN
        SELECT RAISE(ABORT, 'source contracts referenced by done runs are immutable');
      END;

      CREATE TRIGGER source_contracts_done_no_delete
      BEFORE DELETE ON source_contracts
      WHEN EXISTS (
        SELECT 1 FROM daily_runs
        WHERE status = 'done' AND source_contract_version = OLD.version
      )
      BEGIN
        SELECT RAISE(ABORT, 'source contracts referenced by done runs are immutable');
      END;
    `,
  },
  {
    id: 4,
    name: "receipt-materialization-and-controller-state",
    sql: `
      ALTER TABLE source_observations ADD COLUMN controller_candidate_json TEXT
        CHECK(controller_candidate_json IS NULL OR json_valid(controller_candidate_json));

      ALTER TABLE send_attempts ADD COLUMN reservoir_entry_id TEXT
        REFERENCES reservoir_entries(id) ON UPDATE RESTRICT ON DELETE RESTRICT;
      ALTER TABLE send_attempts ADD COLUMN plan_evidence_json TEXT
        CHECK(plan_evidence_json IS NULL OR json_valid(plan_evidence_json));
      ALTER TABLE send_attempts ADD COLUMN possible_evidence_json TEXT
        CHECK(possible_evidence_json IS NULL OR json_valid(possible_evidence_json));
      ALTER TABLE send_attempts ADD COLUMN prepare_receipt_json TEXT
        CHECK(prepare_receipt_json IS NULL OR json_valid(prepare_receipt_json));
      ALTER TABLE send_attempts ADD COLUMN prepare_binding_json TEXT
        CHECK(prepare_binding_json IS NULL OR json_valid(prepare_binding_json));
      ALTER TABLE send_attempts ADD COLUMN commit_started_at TEXT;
      ALTER TABLE send_attempts ADD COLUMN commit_receipt_json TEXT
        CHECK(commit_receipt_json IS NULL OR json_valid(commit_receipt_json));
      ALTER TABLE send_attempts ADD COLUMN commit_causal_sequence INTEGER
        REFERENCES causal_records(sequence);

      CREATE UNIQUE INDEX audit_baselines_one_per_run
        ON audit_baselines(run_id);

      DROP TRIGGER send_attempt_plan_receipt_guard;
      DROP TRIGGER send_attempt_transition_guard;
      DROP TRIGGER send_attempt_possible_receipt_mutation_guard;
      DROP TRIGGER audit_baseline_receipt_guard;
      DROP TRIGGER audit_snapshot_receipt_guard;
      DROP TRIGGER reconciliation_receipt_guard;
      DROP TRIGGER source_observation_receipt_guard;
      DROP TRIGGER person_alias_structured_evidence_guard;

      CREATE TRIGGER source_observation_receipt_guard_v2
      BEFORE INSERT ON source_observations
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = CASE NEW.observation_kind
            WHEN 'candidate' THEN 'candidate_observation'
            ELSE 'terminal_observation'
          END
          AND c.receipt_id = NEW.id
          AND json_valid(c.payload_json)
          AND json_extract(c.payload_json, '$.observationId') IS NEW.id
          AND json_extract(c.payload_json, '$.invocationId') IS NEW.invocation_id
          AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
          AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
          AND json_extract(c.payload_json, '$.observedAt') IS NEW.observed_at
          AND (
            (
              NEW.observation_kind = 'candidate'
              AND json_extract(c.payload_json, '$.name') IS NEW.observed_name
              AND json_extract(c.payload_json, '$.rowOrder') IS NEW.row_order
              AND json_extract(c.payload_json, '$.rowState') IS NEW.row_state
              AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.identity_evidence_json
              AND json_extract(c.payload_json, '$.candidateJson') IS NEW.controller_candidate_json
            )
            OR
            (
              NEW.observation_kind = 'terminal'
              AND json_extract(c.payload_json, '$.pageIdentity') IS NEW.page_identity
              AND json_extract(c.payload_json, '$.stableRowIdsJson') IS NEW.stable_row_ids_json
              AND json_extract(c.payload_json, '$.nextControl') IS NEW.next_control
              AND json_extract(c.payload_json, '$.reloadGeneration') IS NEW.reload_generation
              AND json_extract(c.payload_json, '$.tickId') IS NEW.tick_id
              AND json_extract(c.payload_json, '$.sourceContractVersion')
                    IS NEW.source_contract_version
            )
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'source observation fields must equal its immutable receipt');
      END;

      CREATE TRIGGER source_observations_controller_candidate_no_update
      BEFORE UPDATE OF controller_candidate_json ON source_observations
      BEGIN
        SELECT RAISE(ABORT, 'source observation controller candidate is immutable');
      END;

      CREATE TRIGGER send_attempt_insert_chain_guard
      BEFORE INSERT ON send_attempts
      WHEN
        NEW.reservoir_entry_id IS NULL
        OR NEW.plan_evidence_json IS NULL
        OR NEW.planned_causal_sequence IS NULL
        OR NOT EXISTS (
          SELECT 1
          FROM causal_records plan
          JOIN reservoir_entries reservoir ON reservoir.id = NEW.reservoir_entry_id
          JOIN source_observations observation ON observation.id = reservoir.observation_id
          WHERE plan.sequence = NEW.planned_causal_sequence
            AND plan.kind = 'attempt_plan'
            AND plan.receipt_id = NEW.id
            AND json_valid(plan.payload_json)
            AND json_extract(plan.payload_json, '$.attemptId') IS NEW.id
            AND json_extract(plan.payload_json, '$.runId') IS NEW.run_id
            AND json_extract(plan.payload_json, '$.personId') IS NEW.person_id
            AND json_extract(plan.payload_json, '$.sourceId') IS NEW.source_id
            AND json_extract(plan.payload_json, '$.reservoirEntryId') IS NEW.reservoir_entry_id
            AND json_extract(plan.payload_json, '$.evidenceJson') IS NEW.plan_evidence_json
            AND reservoir.run_id = NEW.run_id
            AND reservoir.person_id = NEW.person_id
            AND reservoir.source_id = NEW.source_id
            AND reservoir.status = 'selected'
            AND reservoir.selected_at IS json_extract(plan.payload_json, '$.plannedAt')
            AND observation.controller_candidate_json IS NOT NULL
        )
        OR (
          NEW.state = 'planned'
          AND (
            NEW.evidence IS NOT NEW.plan_evidence_json
            OR NEW.attempted_at IS NOT NULL
            OR NEW.possible_receipt_key IS NOT NULL
            OR NEW.possible_causal_sequence IS NOT NULL
            OR NEW.possible_evidence_json IS NOT NULL
            OR NEW.prepare_receipt_json IS NOT NULL
            OR NEW.prepare_binding_json IS NOT NULL
            OR NEW.resolved_at IS NOT NULL
            OR NEW.resolution_receipt_key IS NOT NULL
            OR NEW.resolution_causal_sequence IS NOT NULL
            OR NEW.commit_started_at IS NOT NULL
            OR NEW.commit_receipt_json IS NOT NULL
            OR NEW.commit_causal_sequence IS NOT NULL
            OR NEW.planned_causal_sequence != (SELECT MAX(sequence) FROM causal_records)
          )
        )
        OR (
          NEW.state IN ('possible', 'durable', 'proven_no_send')
          AND (
            NEW.attempted_at IS NULL
            OR NEW.possible_receipt_key IS NULL
            OR NEW.possible_causal_sequence IS NULL
            OR NEW.possible_evidence_json IS NULL
            OR NEW.possible_causal_sequence <= NEW.planned_causal_sequence
            OR NOT EXISTS (
              SELECT 1 FROM causal_records possible
              WHERE possible.sequence = NEW.possible_causal_sequence
                AND possible.kind = 'attempt_possible'
                AND possible.receipt_id = NEW.possible_receipt_key
                AND json_valid(possible.payload_json)
                AND json_extract(possible.payload_json, '$.attemptId') IS NEW.id
                AND json_extract(possible.payload_json, '$.runId') IS NEW.run_id
                AND json_extract(possible.payload_json, '$.personId') IS NEW.person_id
                AND json_extract(possible.payload_json, '$.sourceId') IS NEW.source_id
                AND json_extract(possible.payload_json, '$.receiptId') IS NEW.possible_receipt_key
                AND json_extract(possible.payload_json, '$.attemptedAt') IS NEW.attempted_at
                AND json_extract(possible.payload_json, '$.evidenceJson')
                      IS NEW.possible_evidence_json
                AND json_extract(possible.payload_json, '$.prepareReceiptJson')
                      IS NEW.prepare_receipt_json
                AND json_extract(possible.payload_json, '$.prepareBindingJson')
                      IS NEW.prepare_binding_json
            )
          )
        )
        OR (
          NEW.state = 'possible'
          AND (
            NEW.evidence IS NOT NEW.possible_evidence_json
            OR NEW.resolved_at IS NOT NULL
            OR NEW.resolution_receipt_key IS NOT NULL
            OR NEW.resolution_causal_sequence IS NOT NULL
            OR NEW.possible_causal_sequence != (SELECT MAX(sequence) FROM causal_records)
          )
        )
        OR (
          NEW.state IN ('durable', 'proven_no_send')
          AND (
            NEW.resolved_at IS NULL
            OR NEW.resolution_receipt_key IS NULL
            OR NEW.resolution_causal_sequence IS NULL
            OR NEW.resolution_causal_sequence <= NEW.possible_causal_sequence
            OR NEW.resolution_causal_sequence != (SELECT MAX(sequence) FROM causal_records)
            OR NOT EXISTS (
              SELECT 1 FROM causal_records resolution
              WHERE resolution.sequence = NEW.resolution_causal_sequence
                AND resolution.kind = CASE NEW.state
                  WHEN 'durable' THEN 'attempt_durable'
                  ELSE 'attempt_proven_no_send'
                END
                AND resolution.receipt_id = NEW.resolution_receipt_key
                AND json_valid(resolution.payload_json)
                AND json_extract(resolution.payload_json, '$.attemptId') IS NEW.id
                AND json_extract(resolution.payload_json, '$.runId') IS NEW.run_id
                AND json_extract(resolution.payload_json, '$.personId') IS NEW.person_id
                AND json_extract(resolution.payload_json, '$.sourceId') IS NEW.source_id
                AND json_extract(resolution.payload_json, '$.receiptId')
                      IS NEW.resolution_receipt_key
                AND json_extract(resolution.payload_json, '$.resolvedAt') IS NEW.resolved_at
                AND json_extract(resolution.payload_json, '$.state') IS NEW.state
                AND json_extract(resolution.payload_json, '$.evidenceJson') IS NEW.evidence
            )
          )
        )
        OR (
          (
            NEW.commit_started_at IS NOT NULL
            OR NEW.commit_receipt_json IS NOT NULL
            OR NEW.commit_causal_sequence IS NOT NULL
          )
          AND NOT (
            NEW.state IN ('possible', 'durable', 'proven_no_send')
            AND NEW.commit_started_at IS NOT NULL
            AND NEW.commit_receipt_json IS NOT NULL
            AND NEW.commit_causal_sequence IS NOT NULL
            AND NEW.prepare_receipt_json IS NOT NULL
            AND NEW.prepare_binding_json IS NOT NULL
            AND NEW.commit_receipt_json IS NEW.prepare_receipt_json
            AND NEW.commit_causal_sequence > NEW.possible_causal_sequence
            AND (
              NEW.resolution_causal_sequence IS NULL
              OR NEW.commit_causal_sequence < NEW.resolution_causal_sequence
            )
            AND EXISTS (
              SELECT 1 FROM causal_records commit_start
              WHERE commit_start.sequence = NEW.commit_causal_sequence
                AND commit_start.kind = 'attempt_commit_started'
                AND commit_start.receipt_id =
                      json_extract(NEW.commit_receipt_json, '$.receiptId')
                AND json_valid(commit_start.payload_json)
                AND json_extract(commit_start.payload_json, '$.attemptId') IS NEW.id
                AND json_extract(commit_start.payload_json, '$.runId') IS NEW.run_id
                AND json_extract(commit_start.payload_json, '$.startedAt')
                      IS NEW.commit_started_at
                AND json_extract(commit_start.payload_json, '$.commitReceiptJson')
                      IS NEW.commit_receipt_json
                AND json_extract(commit_start.payload_json, '$.prepareBindingJson')
                      IS NEW.prepare_binding_json
            )
          )
        )
      BEGIN
        SELECT RAISE(ABORT, 'send attempt insert requires its complete causal reservoir chain');
      END;

      CREATE TRIGGER send_attempt_transition_guard_v2
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state != OLD.state
       AND NOT (
         OLD.state = 'planned'
         AND NEW.state = 'possible'
         AND NEW.possible_evidence_json IS NOT NULL
         AND NEW.evidence IS NEW.possible_evidence_json
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.possible_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.planned_causal_sequence
             AND c.kind = 'attempt_possible'
             AND c.receipt_id = NEW.possible_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.possible_receipt_key
             AND json_extract(c.payload_json, '$.attemptedAt') IS NEW.attempted_at
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.possible_evidence_json
             AND json_extract(c.payload_json, '$.prepareReceiptJson') IS NEW.prepare_receipt_json
             AND json_extract(c.payload_json, '$.prepareBindingJson') IS NEW.prepare_binding_json
         )
       )
       AND NOT (
         OLD.state = 'possible'
         AND NEW.state IN ('durable', 'proven_no_send')
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.resolution_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.possible_causal_sequence
             AND c.kind = CASE NEW.state
               WHEN 'durable' THEN 'attempt_durable'
               ELSE 'attempt_proven_no_send'
             END
             AND c.receipt_id = NEW.resolution_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.resolution_receipt_key
             AND json_extract(c.payload_json, '$.resolvedAt') IS NEW.resolved_at
             AND json_extract(c.payload_json, '$.state') IS NEW.state
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.evidence
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid send attempt transition receipt');
      END;

      CREATE TRIGGER send_attempt_possible_receipt_mutation_guard_v2
      BEFORE UPDATE OF attempted_at, possible_receipt_key, possible_causal_sequence,
        possible_evidence_json, prepare_receipt_json, prepare_binding_json
      ON send_attempts
      WHEN NOT (
        OLD.state = 'planned'
        AND NEW.state = 'possible'
        AND OLD.possible_causal_sequence IS NULL
        AND NEW.possible_causal_sequence IS NOT NULL
      )
      BEGIN
        SELECT RAISE(ABORT, 'possible receipt fields may only be set during the planned transition');
      END;

      CREATE TRIGGER send_attempt_plan_material_no_update
      BEFORE UPDATE OF reservoir_entry_id, plan_evidence_json ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'planned reservoir evidence is immutable');
      END;

      CREATE TRIGGER send_attempt_commit_start_guard
      BEFORE UPDATE OF commit_started_at, commit_receipt_json, commit_causal_sequence
      ON send_attempts
      WHEN NOT (
        OLD.state = 'possible'
        AND NEW.state = 'possible'
        AND OLD.commit_started_at IS NULL
        AND OLD.commit_receipt_json IS NULL
        AND OLD.commit_causal_sequence IS NULL
        AND NEW.commit_started_at IS NOT NULL
        AND NEW.commit_receipt_json IS OLD.prepare_receipt_json
        AND NEW.commit_causal_sequence IS NOT NULL
        AND EXISTS (
          SELECT 1 FROM causal_records c
          WHERE c.sequence = NEW.commit_causal_sequence
            AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
            AND c.sequence > NEW.possible_causal_sequence
            AND c.kind = 'attempt_commit_started'
            AND c.receipt_id = json_extract(NEW.commit_receipt_json, '$.receiptId')
            AND json_valid(c.payload_json)
            AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
            AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
            AND json_extract(c.payload_json, '$.startedAt') IS NEW.commit_started_at
            AND json_extract(c.payload_json, '$.commitReceiptJson') IS NEW.commit_receipt_json
            AND json_extract(c.payload_json, '$.prepareBindingJson') IS NEW.prepare_binding_json
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'commit start requires the exact persisted preparation receipt');
      END;

      CREATE TRIGGER audit_baseline_receipt_guard_v2
      BEFORE INSERT ON audit_baselines
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = 'audit_baseline'
          AND c.receipt_id = NEW.id
          AND json_valid(c.payload_json)
          AND json_extract(c.payload_json, '$.baselineId') IS NEW.id
          AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
          AND json_extract(c.payload_json, '$.invocationId') IS NEW.invocation_id
          AND json_extract(c.payload_json, '$.peopleCount') IS NEW.people_count
          AND json_extract(c.payload_json, '$.competingSenderAbsent')
                IS NEW.competing_sender_absent
          AND json_extract(c.payload_json, '$.attemptCountAtCapture')
                IS NEW.attempt_count_at_capture
          AND json_extract(c.payload_json, '$.capturedAt') IS NEW.captured_at
      )
      BEGIN
        SELECT RAISE(ABORT, 'audit baseline fields must equal its immutable receipt');
      END;

      CREATE TRIGGER audit_snapshot_receipt_guard_v2
      BEFORE INSERT ON audit_snapshots
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = 'audit_snapshot'
          AND c.receipt_id = NEW.id
          AND json_valid(c.payload_json)
          AND json_extract(c.payload_json, '$.auditId') IS NEW.id
          AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
          AND json_extract(c.payload_json, '$.invocationId') IS NEW.invocation_id
          AND json_extract(c.payload_json, '$.baselineId') IS NEW.baseline_id
          AND json_extract(c.payload_json, '$.peopleCount') IS NEW.people_count
          AND json_extract(c.payload_json, '$.identities') IS NEW.identities_json
          AND json_extract(c.payload_json, '$.names') IS NEW.names_json
          AND json_extract(c.payload_json, '$.complete') IS NEW.complete
          AND json_extract(c.payload_json, '$.competingSenderAbsent')
                IS NEW.competing_sender_absent
          AND json_extract(c.payload_json, '$.contradictoryEvidence')
                IS NEW.contradictory_evidence
          AND json_extract(c.payload_json, '$.capturedAt') IS NEW.captured_at
      )
      BEGIN
        SELECT RAISE(ABORT, 'audit snapshot fields must equal its immutable receipt');
      END;

      CREATE TRIGGER reconciliation_receipt_guard_v2
      BEFORE INSERT ON reconciliations
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = 'reconciliation'
          AND c.receipt_id = NEW.id
          AND json_valid(c.payload_json)
          AND NEW.causal_sequence > (
            SELECT causal_sequence FROM audit_snapshots WHERE id = NEW.audit_id
          )
          AND json_extract(c.payload_json, '$.reconciliationId') IS NEW.id
          AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
          AND json_extract(c.payload_json, '$.baselineId') IS NEW.baseline_id
          AND json_extract(c.payload_json, '$.auditId') IS NEW.audit_id
          AND json_extract(c.payload_json, '$.mode') IS NEW.mode
          AND json_extract(c.payload_json, '$.attemptCount') IS NEW.attempt_count
          AND json_extract(c.payload_json, '$.finalComplete') IS NEW.complete
          AND json_extract(c.payload_json, '$.competingSenderAbsent')
                IS NEW.competing_sender_absent
          AND json_extract(c.payload_json, '$.newlyConfirmedAttemptIds')
                IS NEW.confirmed_attempt_ids_json
          AND json_extract(c.payload_json, '$.reconciledAt') IS NEW.created_at
          AND json_array_length(json_extract(c.payload_json, '$.evidence')) = NEW.attempt_count
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation fields must equal its immutable receipt');
      END;

      CREATE TRIGGER reconciliation_attempt_payload_guard
      BEFORE INSERT ON reconciliation_attempts
      WHEN NOT EXISTS (
        SELECT 1
        FROM reconciliations rec
        JOIN causal_records c ON c.sequence = rec.causal_sequence
        JOIN json_each(c.payload_json, '$.evidence') item
        WHERE rec.id = NEW.reconciliation_id
          AND json_extract(item.value, '$.attemptId') IS NEW.attempt_id
          AND json_extract(item.value, '$.kind') IS NEW.evidence_kind
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation attempt must equal canonical receipt evidence');
      END;

      CREATE TRIGGER reservoir_identity_no_update
      BEFORE UPDATE OF id, run_id, source_id, person_id, observation_id, added_at
      ON reservoir_entries
      BEGIN
        SELECT RAISE(ABORT, 'reservoir identity and source evidence are immutable');
      END;

      CREATE TRIGGER reservoir_selected_at_guard
      BEFORE UPDATE OF selected_at ON reservoir_entries
      WHEN NOT (
        OLD.status = 'available'
        AND NEW.status = 'selected'
        AND OLD.selected_at IS NULL
        AND NEW.selected_at IS NOT NULL
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir selection timestamp is immutable');
      END;

      CREATE TRIGGER reservoir_status_transition_guard
      BEFORE UPDATE OF status ON reservoir_entries
      WHEN NEW.status != OLD.status
       AND NOT (
         (OLD.status = 'available' AND NEW.status IN ('selected', 'ineligible'))
         OR
         (
           OLD.status = 'selected'
           AND NEW.status = 'ineligible'
           AND EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.state = 'proven_no_send'
           )
         )
         OR
         (
           OLD.status = 'selected'
           AND NEW.status = 'consumed'
           AND EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.state = 'durable'
           )
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid reservoir status transition');
      END;

      CREATE TRIGGER reservoir_delete_guard
      BEFORE DELETE ON reservoir_entries
      WHEN EXISTS (SELECT 1 FROM send_attempts WHERE reservoir_entry_id = OLD.id)
        OR EXISTS (
          SELECT 1 FROM daily_runs
          WHERE id = OLD.run_id AND status IN ('active', 'done')
        )
      BEGIN
        SELECT RAISE(ABORT, 'run-relevant reservoir rows cannot be deleted');
      END;

      CREATE TRIGGER person_alias_structured_evidence_guard_v2
      BEFORE INSERT ON person_aliases
      WHEN json_valid(NEW.evidence) != 1
        OR NEW.anchor_kind IS NULL
        OR NEW.anchor_value IS NULL
        OR NEW.evidence_observation_id IS NULL
        OR NEW.evidence_invocation_id IS NULL
        OR NEW.evidence_source_id IS NULL
        OR NEW.anchor_kind = NEW.kind
        OR NEW.anchor_value = NEW.value
        OR NEW.evidence IS NOT json_object(
          'anchorKind', NEW.anchor_kind,
          'anchorValue', NEW.anchor_value,
          'invocationId', NEW.evidence_invocation_id,
          'observationId', NEW.evidence_observation_id,
          'sourceId', NEW.evidence_source_id
        )
        OR NOT EXISTS (
          SELECT 1 FROM people p
          WHERE p.id = NEW.person_id
            AND CASE NEW.anchor_kind
              WHEN 'sales_nav_id' THEN p.sales_nav_id
              WHEN 'public_url' THEN p.public_url
              ELSE p.lead_key
            END = NEW.anchor_value
            AND CASE NEW.kind
              WHEN 'sales_nav_id' THEN p.sales_nav_id
              WHEN 'public_url' THEN p.public_url
              ELSE p.lead_key
            END = NEW.value
        )
        OR NOT EXISTS (
          SELECT 1 FROM source_observations o
          WHERE o.id = NEW.evidence_observation_id
            AND o.invocation_id = NEW.evidence_invocation_id
            AND o.source_id = NEW.evidence_source_id
            AND o.person_id = NEW.person_id
            AND CASE NEW.kind
              WHEN 'sales_nav_id' THEN json_extract(o.identity_evidence_json, '$.salesNavId')
              WHEN 'public_url' THEN json_extract(o.identity_evidence_json, '$.publicUrl')
              ELSE json_extract(o.identity_evidence_json, '$.leadKey')
            END = NEW.value
            AND CASE NEW.anchor_kind
              WHEN 'sales_nav_id' THEN json_extract(o.identity_evidence_json, '$.salesNavId')
              WHEN 'public_url' THEN json_extract(o.identity_evidence_json, '$.publicUrl')
              ELSE json_extract(o.identity_evidence_json, '$.leadKey')
            END = NEW.anchor_value
        )
      BEGIN
        SELECT RAISE(ABORT, 'alias requires canonical exact immutable source evidence');
      END;
    `,
  },
  {
    id: 5,
    name: "planned-only-attempts-and-sealed-reconciliation",
    sql: `
      ALTER TABLE reconciliations ADD COLUMN sealed INTEGER NOT NULL DEFAULT 0
        CHECK(sealed IN (0, 1));

      DROP TRIGGER send_attempt_insert_chain_guard;
      DROP TRIGGER reservoir_selected_at_guard;
      DROP TRIGGER reservoir_status_transition_guard;
      DROP TRIGGER reservoir_delete_guard;
      DROP TRIGGER reconciliations_no_update;
      DROP TRIGGER reconciliation_attempt_run_guard;
      DROP TRIGGER reconciliation_attempt_payload_guard;

      UPDATE reconciliations
      SET sealed = 1
      WHERE causal_sequence IS NOT NULL
        AND complete = 1
        AND competing_sender_absent = 1
        AND attempt_count = 30
        AND json_valid(confirmed_attempt_ids_json)
        AND json_type(confirmed_attempt_ids_json) = 'array'
        AND (
          SELECT COUNT(*) FROM json_each(confirmed_attempt_ids_json)
        ) = attempt_count
        AND (
          SELECT COUNT(DISTINCT confirmed.value)
          FROM json_each(confirmed_attempt_ids_json) confirmed
        ) = attempt_count
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child
          WHERE child.reconciliation_id = reconciliations.id
        ) = attempt_count
        AND (
          SELECT COUNT(*) FROM send_attempts attempt
          WHERE attempt.run_id = reconciliations.run_id
        ) = attempt_count
        AND NOT EXISTS (
          SELECT 1 FROM send_attempts attempt
          WHERE attempt.run_id = reconciliations.run_id
            AND (
              attempt.state != 'durable'
              OR attempt.possible_causal_sequence IS NULL
              OR attempt.resolution_causal_sequence IS NULL
              OR NOT EXISTS (
                SELECT 1 FROM reconciliation_attempts child
                WHERE child.reconciliation_id = reconciliations.id
                  AND child.attempt_id = attempt.id
              )
            )
        )
        AND EXISTS (
          SELECT 1
          FROM audit_baselines baseline
          JOIN audit_snapshots audit
            ON audit.id = reconciliations.audit_id
           AND audit.baseline_id = baseline.id
          JOIN causal_records baseline_receipt
            ON baseline_receipt.sequence = baseline.causal_sequence
          JOIN causal_records audit_receipt
            ON audit_receipt.sequence = audit.causal_sequence
          WHERE baseline.id = reconciliations.baseline_id
            AND baseline.run_id = reconciliations.run_id
            AND audit.run_id = reconciliations.run_id
            AND baseline.competing_sender_absent = 1
            AND baseline.attempt_count_at_capture = 0
            AND audit.complete = 1
            AND audit.competing_sender_absent = 1
            AND audit.contradictory_evidence = 0
            AND audit.people_count - baseline.people_count = reconciliations.attempt_count
            AND baseline.causal_sequence IS NOT NULL
            AND audit.causal_sequence > baseline.causal_sequence
            AND baseline_receipt.kind = 'audit_baseline'
            AND baseline_receipt.receipt_id = baseline.id
            AND json_valid(baseline_receipt.payload_json)
            AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
            AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
            AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
            AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
            AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
                  IS baseline.competing_sender_absent
            AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
                  IS baseline.attempt_count_at_capture
            AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
            AND audit_receipt.kind = 'audit_snapshot'
            AND audit_receipt.receipt_id = audit.id
            AND json_valid(audit_receipt.payload_json)
            AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
            AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
            AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
            AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
            AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
            AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
            AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
            AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
            AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
                  IS audit.competing_sender_absent
            AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
                  IS audit.contradictory_evidence
            AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        )
        AND EXISTS (
          SELECT 1 FROM causal_records receipt
          WHERE receipt.sequence = reconciliations.causal_sequence
            AND receipt.kind = 'reconciliation'
            AND receipt.receipt_id = reconciliations.id
            AND json_valid(receipt.payload_json)
            AND json_type(receipt.payload_json, '$.evidence') = 'array'
            AND json_array_length(json_extract(receipt.payload_json, '$.evidence'))
                  = reconciliations.attempt_count
            AND json_extract(receipt.payload_json, '$.reconciliationId') IS reconciliations.id
            AND json_extract(receipt.payload_json, '$.runId') IS reconciliations.run_id
            AND json_extract(receipt.payload_json, '$.baselineId') IS reconciliations.baseline_id
            AND json_extract(receipt.payload_json, '$.auditId') IS reconciliations.audit_id
            AND json_extract(receipt.payload_json, '$.mode') IS reconciliations.mode
            AND json_extract(receipt.payload_json, '$.attemptCount') IS reconciliations.attempt_count
            AND json_extract(receipt.payload_json, '$.finalComplete') IS reconciliations.complete
            AND json_extract(receipt.payload_json, '$.competingSenderAbsent')
                  IS reconciliations.competing_sender_absent
            AND json_extract(receipt.payload_json, '$.newlyConfirmedAttemptIds')
                  IS reconciliations.confirmed_attempt_ids_json
            AND json_extract(receipt.payload_json, '$.reconciledAt') IS reconciliations.created_at
        )
        AND NOT EXISTS (
          SELECT 1
          FROM json_each(confirmed_attempt_ids_json) confirmed
          WHERE typeof(confirmed.value) != 'text'
             OR length(trim(confirmed.value)) = 0
             OR NOT EXISTS (
               SELECT 1
               FROM reconciliation_attempts child
               JOIN causal_records receipt ON receipt.sequence = reconciliations.causal_sequence
               JOIN json_each(receipt.payload_json, '$.evidence') item
               WHERE child.reconciliation_id = reconciliations.id
                 AND child.attempt_id = confirmed.value
                 AND json_extract(item.value, '$.attemptId') IS confirmed.value
                 AND json_extract(item.value, '$.kind') IS child.evidence_kind
             )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM causal_records receipt
          JOIN json_each(receipt.payload_json, '$.evidence') item
          WHERE receipt.sequence = reconciliations.causal_sequence
            AND (
              typeof(json_extract(item.value, '$.attemptId')) != 'text'
              OR json_extract(item.value, '$.kind') NOT IN ('identity', 'name', 'aggregate')
              OR NOT EXISTS (
                SELECT 1 FROM json_each(confirmed_attempt_ids_json) confirmed
                WHERE confirmed.value = json_extract(item.value, '$.attemptId')
              )
              OR NOT EXISTS (
                SELECT 1 FROM reconciliation_attempts child
                WHERE child.reconciliation_id = reconciliations.id
                  AND child.attempt_id = json_extract(item.value, '$.attemptId')
                  AND child.evidence_kind = json_extract(item.value, '$.kind')
              )
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          WHERE child.reconciliation_id = reconciliations.id
            AND (
              NOT EXISTS (
                SELECT 1 FROM json_each(confirmed_attempt_ids_json) confirmed
                WHERE confirmed.value = child.attempt_id
              )
              OR NOT EXISTS (
                SELECT 1
                FROM causal_records receipt
                JOIN json_each(receipt.payload_json, '$.evidence') item
                WHERE receipt.sequence = reconciliations.causal_sequence
                  AND json_extract(item.value, '$.attemptId') IS child.attempt_id
                  AND json_extract(item.value, '$.kind') IS child.evidence_kind
              )
              OR NOT EXISTS (
                SELECT 1 FROM send_attempts attempt
                WHERE attempt.id = child.attempt_id
                  AND attempt.run_id = reconciliations.run_id
                  AND attempt.state = 'durable'
              )
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM json_each(confirmed_attempt_ids_json) confirmed
          WHERE NOT EXISTS (
              SELECT 1 FROM reconciliation_attempts child
              WHERE child.reconciliation_id = reconciliations.id
                AND child.attempt_id = confirmed.value
            )
        );

      CREATE TRIGGER send_attempt_planned_only_insert_guard
      BEFORE INSERT ON send_attempts
      WHEN NEW.state != 'planned'
        OR NEW.reservoir_entry_id IS NULL
        OR NEW.plan_evidence_json IS NULL
        OR NEW.planned_causal_sequence IS NULL
        OR NEW.evidence IS NOT NEW.plan_evidence_json
        OR NEW.attempted_at IS NOT NULL
        OR NEW.possible_receipt_key IS NOT NULL
        OR NEW.possible_causal_sequence IS NOT NULL
        OR NEW.possible_evidence_json IS NOT NULL
        OR NEW.prepare_receipt_json IS NOT NULL
        OR NEW.prepare_binding_json IS NOT NULL
        OR NEW.resolved_at IS NOT NULL
        OR NEW.resolution_receipt_key IS NOT NULL
        OR NEW.resolution_causal_sequence IS NOT NULL
        OR NEW.commit_started_at IS NOT NULL
        OR NEW.commit_receipt_json IS NOT NULL
        OR NEW.commit_causal_sequence IS NOT NULL
        OR NEW.planned_causal_sequence != (SELECT MAX(sequence) FROM causal_records)
        OR NOT EXISTS (
          SELECT 1
          FROM causal_records plan
          JOIN reservoir_entries reservoir ON reservoir.id = NEW.reservoir_entry_id
          JOIN source_observations observation ON observation.id = reservoir.observation_id
          WHERE plan.sequence = NEW.planned_causal_sequence
            AND plan.kind = 'attempt_plan'
            AND plan.receipt_id = NEW.id
            AND json_valid(plan.payload_json)
            AND json_type(plan.payload_json, '$.plannedAt') = 'text'
            AND length(trim(json_extract(plan.payload_json, '$.plannedAt'))) > 0
            AND json_extract(plan.payload_json, '$.attemptId') IS NEW.id
            AND json_extract(plan.payload_json, '$.runId') IS NEW.run_id
            AND json_extract(plan.payload_json, '$.personId') IS NEW.person_id
            AND json_extract(plan.payload_json, '$.sourceId') IS NEW.source_id
            AND json_extract(plan.payload_json, '$.reservoirEntryId') IS NEW.reservoir_entry_id
            AND json_extract(plan.payload_json, '$.evidenceJson') IS NEW.plan_evidence_json
            AND reservoir.run_id = NEW.run_id
            AND reservoir.person_id = NEW.person_id
            AND reservoir.source_id = NEW.source_id
            AND reservoir.status = 'available'
            AND reservoir.selected_at IS NULL
            AND observation.controller_candidate_json IS NOT NULL
        )
      BEGIN
        SELECT RAISE(ABORT, 'send attempts must be inserted as planned with an exact plan receipt');
      END;

      CREATE TRIGGER reservoir_selected_at_guard_v2
      BEFORE UPDATE OF selected_at ON reservoir_entries
      WHEN NOT (
        OLD.status = 'available'
        AND NEW.status = 'selected'
        AND OLD.selected_at IS NULL
        AND NEW.selected_at IS NOT NULL
        AND EXISTS (
          SELECT 1
          FROM send_attempts attempt
          JOIN causal_records plan ON plan.sequence = attempt.planned_causal_sequence
          WHERE attempt.reservoir_entry_id = OLD.id
            AND attempt.run_id = OLD.run_id
            AND attempt.person_id = OLD.person_id
            AND attempt.source_id = OLD.source_id
            AND attempt.state = 'planned'
            AND plan.kind = 'attempt_plan'
            AND plan.receipt_id = attempt.id
            AND json_type(plan.payload_json, '$.plannedAt') = 'text'
            AND length(trim(json_extract(plan.payload_json, '$.plannedAt'))) > 0
            AND json_extract(plan.payload_json, '$.plannedAt') IS NEW.selected_at
            AND json_extract(plan.payload_json, '$.reservoirEntryId') IS OLD.id
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir selection requires its non-null canonical plan time');
      END;

      CREATE TRIGGER reservoir_status_transition_guard_v2
      BEFORE UPDATE OF status ON reservoir_entries
      WHEN NEW.status != OLD.status
       AND NOT (
         (
           OLD.status = 'available'
           AND NEW.status = 'selected'
           AND NEW.selected_at IS NOT NULL
           AND EXISTS (
             SELECT 1
             FROM send_attempts attempt
             JOIN causal_records plan ON plan.sequence = attempt.planned_causal_sequence
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.run_id = OLD.run_id
               AND attempt.person_id = OLD.person_id
               AND attempt.source_id = OLD.source_id
               AND attempt.state = 'planned'
               AND plan.kind = 'attempt_plan'
               AND plan.receipt_id = attempt.id
               AND json_type(plan.payload_json, '$.plannedAt') = 'text'
               AND length(trim(json_extract(plan.payload_json, '$.plannedAt'))) > 0
               AND json_extract(plan.payload_json, '$.plannedAt') IS NEW.selected_at
               AND json_extract(plan.payload_json, '$.reservoirEntryId') IS OLD.id
           )
         )
         OR (OLD.status = 'available' AND NEW.status = 'ineligible')
         OR
         (
           OLD.status = 'selected'
           AND NEW.status = 'ineligible'
           AND EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.state = 'proven_no_send'
           )
         )
         OR
         (
           OLD.status = 'selected'
           AND NEW.status = 'consumed'
           AND EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.state = 'durable'
           )
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid reservoir status transition');
      END;

      CREATE TRIGGER send_attempt_select_reservoir_after_insert
      AFTER INSERT ON send_attempts
      BEGIN
        UPDATE reservoir_entries
        SET status = 'selected',
            selected_at = (
              SELECT json_extract(payload_json, '$.plannedAt')
              FROM causal_records WHERE sequence = NEW.planned_causal_sequence
            )
        WHERE id = NEW.reservoir_entry_id AND status = 'available';

        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM reservoir_entries
          WHERE id = NEW.reservoir_entry_id
            AND status = 'selected'
            AND selected_at IS (
              SELECT json_extract(payload_json, '$.plannedAt')
              FROM causal_records WHERE sequence = NEW.planned_causal_sequence
            )
        ) THEN RAISE(ABORT, 'planned attempt did not atomically select its reservoir row') END;

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':planned',
          NEW.run_id,
          'send_planned',
          json_object('attemptId', NEW.id, 'personId', NEW.person_id, 'sourceId', NEW.source_id),
          (SELECT json_extract(payload_json, '$.plannedAt')
           FROM causal_records WHERE sequence = NEW.planned_causal_sequence),
          'attempt:' || NEW.id || ':planned'
        );
      END;

      CREATE TRIGGER send_attempt_possible_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'possible'
      BEGIN
        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':possible', NEW.run_id, 'send_possible',
          json_object('attemptId', NEW.id), NEW.attempted_at,
          'attempt:' || NEW.id || ':possible'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':possible'
            AND run_id = NEW.run_id
            AND type = 'send_possible'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.attempted_at
        ) THEN RAISE(ABORT, 'possible transition lacks its exact event') END;
      END;

      CREATE TRIGGER send_attempt_durable_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
      BEGIN
        INSERT INTO relationship_facts
          (id, person_id, kind, effective_at, run_id, evidence)
        VALUES (
          'pending:' || NEW.id, NEW.person_id, 'pending', NEW.resolved_at, NEW.run_id, NEW.evidence
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM relationship_facts
          WHERE id = 'pending:' || NEW.id
            AND person_id = NEW.person_id
            AND kind = 'pending'
            AND effective_at = NEW.resolved_at
            AND run_id = NEW.run_id
            AND evidence = NEW.evidence
        ) THEN RAISE(ABORT, 'durable transition lacks its exact relationship fact') END;

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':durable', NEW.run_id, 'send_durable',
          json_object('attemptId', NEW.id), NEW.resolved_at,
          'attempt:' || NEW.id || ':durable'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':durable'
            AND run_id = NEW.run_id
            AND type = 'send_durable'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.resolved_at
        ) THEN RAISE(ABORT, 'durable transition lacks its exact event') END;
      END;

      CREATE TRIGGER send_attempt_proven_no_send_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'proven_no_send'
      BEGIN
        INSERT INTO relationship_facts
          (id, person_id, kind, effective_at, run_id, evidence)
        VALUES (
          'proven-no-send:' || NEW.id, NEW.person_id, 'proven_no_send',
          NEW.resolved_at, NEW.run_id, NEW.evidence
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM relationship_facts
          WHERE id = 'proven-no-send:' || NEW.id
            AND person_id = NEW.person_id
            AND kind = 'proven_no_send'
            AND effective_at = NEW.resolved_at
            AND run_id = NEW.run_id
            AND evidence = NEW.evidence
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact relationship fact') END;

        UPDATE reservoir_entries SET status = 'ineligible'
        WHERE id = NEW.reservoir_entry_id AND status = 'selected';

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':proven_no_send',
          NEW.run_id, 'send_proven_no_send', json_object('attemptId', NEW.id),
          NEW.resolved_at, 'attempt:' || NEW.id || ':proven_no_send'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
            AND run_id = NEW.run_id
            AND type = 'send_proven_no_send'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.resolved_at
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact event') END;
      END;

      CREATE TRIGGER relationship_facts_no_update
      BEFORE UPDATE ON relationship_facts
      BEGIN
        SELECT RAISE(ABORT, 'relationship facts are immutable');
      END;

      CREATE TRIGGER relationship_facts_no_delete
      BEFORE DELETE ON relationship_facts
      BEGIN
        SELECT RAISE(ABORT, 'relationship facts are immutable');
      END;

      CREATE TRIGGER reconciliation_initially_unsealed_guard
      BEFORE INSERT ON reconciliations
      WHEN NEW.sealed != 0
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation must be inserted unsealed');
      END;

      CREATE TRIGGER reconciliation_attempt_run_guard_v2
      BEFORE INSERT ON reconciliation_attempts
      WHEN NOT EXISTS (
        SELECT 1
        FROM reconciliations rec
        JOIN send_attempts attempt ON attempt.id = NEW.attempt_id
        WHERE rec.id = NEW.reconciliation_id
          AND rec.sealed = 0
          AND rec.run_id = attempt.run_id
          AND attempt.state IN ('possible', 'durable')
      )
      BEGIN
        SELECT RAISE(ABORT, 'unsealed reconciliation attempt must be active and belong to the run');
      END;

      CREATE TRIGGER reconciliations_fields_no_update
      BEFORE UPDATE OF id, run_id, baseline_id, audit_id, mode, attempt_count, complete,
        competing_sender_absent, confirmed_attempt_ids_json, created_at, causal_sequence
      ON reconciliations
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation material fields are immutable');
      END;

      CREATE TRIGGER reconciliation_attempt_payload_guard_v2
      BEFORE INSERT ON reconciliation_attempts
      WHEN EXISTS (
        SELECT 1 FROM reconciliations rec
        WHERE rec.id = NEW.reconciliation_id AND rec.sealed != 0
      )
      OR NOT EXISTS (
        SELECT 1
        FROM reconciliations rec
        JOIN causal_records c ON c.sequence = rec.causal_sequence
        JOIN json_each(c.payload_json, '$.evidence') item
        WHERE rec.id = NEW.reconciliation_id
          AND rec.sealed = 0
          AND json_extract(item.value, '$.attemptId') IS NEW.attempt_id
          AND json_extract(item.value, '$.kind') IS NEW.evidence_kind
      )
      BEGIN
        SELECT RAISE(ABORT, 'unsealed reconciliation child must equal canonical receipt evidence');
      END;

      CREATE TRIGGER reconciliation_seal_guard
      BEFORE UPDATE OF sealed ON reconciliations
      WHEN NOT (
        OLD.sealed = 0
        AND NEW.sealed = 1
        AND EXISTS (
          SELECT 1 FROM causal_records c
          WHERE c.sequence = NEW.causal_sequence
            AND c.kind = 'reconciliation'
            AND c.receipt_id = NEW.id
            AND json_valid(c.payload_json)
            AND json_extract(c.payload_json, '$.reconciliationId') IS NEW.id
            AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
            AND json_extract(c.payload_json, '$.baselineId') IS NEW.baseline_id
            AND json_extract(c.payload_json, '$.auditId') IS NEW.audit_id
            AND json_extract(c.payload_json, '$.mode') IS NEW.mode
            AND json_extract(c.payload_json, '$.attemptCount') IS NEW.attempt_count
            AND json_extract(c.payload_json, '$.finalComplete') IS NEW.complete
            AND json_extract(c.payload_json, '$.competingSenderAbsent')
                  IS NEW.competing_sender_absent
            AND json_extract(c.payload_json, '$.newlyConfirmedAttemptIds')
                  IS NEW.confirmed_attempt_ids_json
            AND json_extract(c.payload_json, '$.reconciledAt') IS NEW.created_at
            AND json_array_length(json_extract(c.payload_json, '$.evidence')) = NEW.attempt_count
        )
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child
          WHERE child.reconciliation_id = NEW.id
        ) = NEW.attempt_count
        AND json_valid(NEW.confirmed_attempt_ids_json)
        AND json_type(NEW.confirmed_attempt_ids_json) = 'array'
        AND (
          SELECT COUNT(*) FROM json_each(NEW.confirmed_attempt_ids_json)
        ) = (
          SELECT COUNT(DISTINCT confirmed.value)
          FROM json_each(NEW.confirmed_attempt_ids_json) confirmed
        )
        AND NOT EXISTS (
          SELECT 1
          FROM json_each(NEW.confirmed_attempt_ids_json) confirmed
          WHERE typeof(confirmed.value) != 'text'
             OR length(trim(confirmed.value)) = 0
             OR NOT EXISTS (
               SELECT 1
               FROM reconciliation_attempts child
               JOIN send_attempts attempt ON attempt.id = child.attempt_id
               WHERE child.reconciliation_id = NEW.id
                 AND child.attempt_id = confirmed.value
                 AND attempt.run_id = NEW.run_id
                 AND attempt.state = 'possible'
             )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM reconciliation_attempts child
          JOIN send_attempts attempt ON attempt.id = child.attempt_id
          WHERE child.reconciliation_id = NEW.id
            AND attempt.state = 'possible'
            AND NOT EXISTS (
              SELECT 1 FROM json_each(NEW.confirmed_attempt_ids_json) confirmed
              WHERE confirmed.value = child.attempt_id
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM causal_records c
          JOIN json_each(c.payload_json, '$.evidence') item
          WHERE c.sequence = NEW.causal_sequence
            AND NOT EXISTS (
              SELECT 1 FROM reconciliation_attempts child
              WHERE child.reconciliation_id = NEW.id
                AND child.attempt_id = json_extract(item.value, '$.attemptId')
                AND child.evidence_kind = json_extract(item.value, '$.kind')
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          WHERE child.reconciliation_id = NEW.id
            AND NOT EXISTS (
              SELECT 1
              FROM causal_records c
              JOIN json_each(c.payload_json, '$.evidence') item
              WHERE c.sequence = NEW.causal_sequence
                AND json_extract(item.value, '$.attemptId') IS child.attempt_id
                AND json_extract(item.value, '$.kind') IS child.evidence_kind
            )
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation cannot seal before exact evidence materialization');
      END;

      CREATE TRIGGER reconciliation_seal_event
      AFTER UPDATE OF sealed ON reconciliations
      WHEN OLD.sealed = 0 AND NEW.sealed = 1
      BEGIN
        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:reconciliation:' || NEW.run_id || ':' || NEW.audit_id,
          NEW.run_id,
          'reconciliation_recorded',
          json_object(
            'auditId', NEW.audit_id,
            'baselineId', NEW.baseline_id,
            'complete', json(CASE WHEN NEW.complete = 1 THEN 'true' ELSE 'false' END),
            'reconciliationId', NEW.id,
            'referencedAttempts', NEW.attempt_count
          ),
          NEW.created_at,
          'reconciliation:' || NEW.run_id || ':' || NEW.audit_id
        );
      END;

      CREATE TRIGGER daily_run_final_reconciliation_sealed_guard
      BEFORE UPDATE OF final_reconciliation_id ON daily_runs
      WHEN NEW.final_reconciliation_id IS NOT NULL
       AND NOT EXISTS (
         SELECT 1 FROM reconciliations rec
         WHERE rec.id = NEW.final_reconciliation_id
           AND rec.run_id = NEW.id
           AND rec.sealed = 1
       )
      BEGIN
        SELECT RAISE(ABORT, 'final reconciliation must be sealed');
      END;

      CREATE TRIGGER daily_run_done_sealed_guard
      BEFORE UPDATE OF status ON daily_runs
      WHEN NEW.status = 'done'
       AND NOT EXISTS (
         SELECT 1 FROM reconciliations rec
         WHERE rec.id = NEW.final_reconciliation_id
           AND rec.run_id = NEW.id
           AND rec.sealed = 1
       )
      BEGIN
        SELECT RAISE(ABORT, 'Done requires a sealed reconciliation');
      END;

      CREATE TRIGGER reservoir_active_run_insert_guard
      BEFORE INSERT ON reservoir_entries
      WHEN NOT EXISTS (
        SELECT 1 FROM daily_runs run
        WHERE run.id = NEW.run_id AND run.status = 'active'
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir rows require an active run');
      END;

      CREATE TRIGGER reservoir_non_active_no_update
      BEFORE UPDATE ON reservoir_entries
      WHEN EXISTS (
        SELECT 1 FROM daily_runs run
        WHERE run.id = OLD.run_id AND run.status != 'active'
      )
      BEGIN
        SELECT RAISE(ABORT, 'non-active run reservoir evidence is immutable');
      END;

      CREATE TRIGGER reservoir_no_delete
      BEFORE DELETE ON reservoir_entries
      BEGIN
        SELECT RAISE(ABORT, 'reservoir evidence is append-only');
      END;
    `,
  },
  {
    id: 6,
    name: "sealed-reconciliation-durable-application-and-event-integrity",
    sql: `
      DROP TRIGGER reconciliation_seal_guard;

      CREATE VIEW reconciliation_seal_integrity AS
      SELECT rec.id
      FROM reconciliations rec
      JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
      JOIN audit_snapshots audit
        ON audit.id = rec.audit_id
       AND audit.baseline_id = baseline.id
      JOIN causal_records baseline_receipt
        ON baseline_receipt.sequence = baseline.causal_sequence
      JOIN causal_records audit_receipt
        ON audit_receipt.sequence = audit.causal_sequence
      JOIN causal_records rec_receipt
        ON rec_receipt.sequence = rec.causal_sequence
      WHERE rec.complete = 1
        AND rec.competing_sender_absent = 1
        AND rec.attempt_count = 30
        AND baseline.run_id = rec.run_id
        AND baseline.competing_sender_absent = 1
        AND baseline.attempt_count_at_capture = 0
        AND audit.run_id = rec.run_id
        AND audit.complete = 1
        AND audit.competing_sender_absent = 1
        AND audit.contradictory_evidence = 0
        AND audit.people_count - baseline.people_count = rec.attempt_count
        AND baseline.causal_sequence IS NOT NULL
        AND audit.causal_sequence > baseline.causal_sequence
        AND rec.causal_sequence > audit.causal_sequence
        AND baseline_receipt.kind = 'audit_baseline'
        AND baseline_receipt.receipt_id = baseline.id
        AND json_valid(baseline_receipt.payload_json)
        AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
        AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
        AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
        AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
        AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
              IS baseline.competing_sender_absent
        AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
              IS baseline.attempt_count_at_capture
        AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
        AND audit_receipt.kind = 'audit_snapshot'
        AND audit_receipt.receipt_id = audit.id
        AND json_valid(audit_receipt.payload_json)
        AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
        AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
        AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
        AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
        AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
        AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
        AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
        AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
        AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
              IS audit.competing_sender_absent
        AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
              IS audit.contradictory_evidence
        AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        AND rec_receipt.kind = 'reconciliation'
        AND rec_receipt.receipt_id = rec.id
        AND json_valid(rec_receipt.payload_json)
        AND json_type(rec_receipt.payload_json, '$.evidence') = 'array'
        AND json_array_length(json_extract(rec_receipt.payload_json, '$.evidence'))
              = rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
        AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
        AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
        AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
        AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
        AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
        AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
              IS rec.competing_sender_absent
        AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
              IS rec.confirmed_attempt_ids_json
        AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
        AND json_valid(rec.confirmed_attempt_ids_json)
        AND json_type(rec.confirmed_attempt_ids_json) = 'array'
        AND json_array_length(rec.confirmed_attempt_ids_json) = rec.attempt_count
        AND (
          SELECT COUNT(DISTINCT confirmed.value)
          FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE typeof(confirmed.value) = 'text' AND length(trim(confirmed.value)) > 0
        ) = rec.attempt_count
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
        ) = rec.attempt_count
        AND (
          SELECT COUNT(*) FROM send_attempts attempt
          WHERE attempt.run_id = rec.run_id
        ) = rec.attempt_count
        AND NOT EXISTS (
          SELECT 1
          FROM send_attempts attempt
          WHERE attempt.run_id = rec.run_id
            AND (
              attempt.possible_causal_sequence IS NULL
              OR audit.causal_sequence <= attempt.possible_causal_sequence
              OR (rec.sealed = 0 AND attempt.state != 'possible')
              OR (rec.sealed = 1 AND (
                attempt.state != 'durable'
                OR attempt.resolution_causal_sequence IS NULL
                OR attempt.resolution_causal_sequence <= rec.causal_sequence
              ))
              OR NOT EXISTS (
                SELECT 1 FROM reconciliation_attempts child
                WHERE child.reconciliation_id = rec.id
                  AND child.attempt_id = attempt.id
              )
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation_attempts child
            JOIN send_attempts attempt ON attempt.id = child.attempt_id
            WHERE child.reconciliation_id = rec.id
              AND child.attempt_id = confirmed.value
              AND attempt.run_id = rec.run_id
          )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
            AND (
              NOT EXISTS (
                SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
                WHERE confirmed.value = child.attempt_id
              )
              OR NOT EXISTS (
                SELECT 1
                FROM json_each(rec_receipt.payload_json, '$.evidence') item
                WHERE json_extract(item.value, '$.attemptId') IS child.attempt_id
                  AND json_extract(item.value, '$.kind') IS child.evidence_kind
              )
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM json_each(rec_receipt.payload_json, '$.evidence') item
          WHERE typeof(json_extract(item.value, '$.attemptId')) != 'text'
             OR json_extract(item.value, '$.kind') NOT IN ('identity', 'name', 'aggregate')
             OR NOT EXISTS (
               SELECT 1 FROM reconciliation_attempts child
               WHERE child.reconciliation_id = rec.id
                 AND child.attempt_id = json_extract(item.value, '$.attemptId')
                 AND child.evidence_kind = json_extract(item.value, '$.kind')
             )
        );

      UPDATE reconciliations
      SET sealed = 0
      WHERE sealed = 1
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_seal_integrity valid WHERE valid.id = reconciliations.id
        );

      CREATE TRIGGER reconciliation_seal_guard
      BEFORE UPDATE OF sealed ON reconciliations
      WHEN NOT (
        OLD.sealed = 0
        AND NEW.sealed = 1
        AND EXISTS (
          SELECT 1 FROM reconciliation_seal_integrity valid WHERE valid.id = NEW.id
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation cannot seal before exact safe evidence materialization');
      END;

      CREATE TRIGGER send_attempt_plan_event_key_guard
      BEFORE INSERT ON send_attempts
      WHEN EXISTS (
        SELECT 1 FROM events
        WHERE id = 'event:attempt:' || NEW.id || ':planned'
           OR dedupe_key = 'attempt:' || NEW.id || ':planned'
      )
      BEGIN
        SELECT RAISE(ABORT, 'planned event key exists before its parent transition');
      END;

      CREATE TRIGGER send_attempt_possible_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'possible'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':possible'
            OR dedupe_key = 'attempt:' || NEW.id || ':possible'
       )
      BEGIN
        SELECT RAISE(ABORT, 'possible event key exists before its parent transition');
      END;

      CREATE TRIGGER send_attempt_durable_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':durable'
            OR dedupe_key = 'attempt:' || NEW.id || ':durable'
       )
      BEGIN
        SELECT RAISE(ABORT, 'durable event key exists before its parent transition');
      END;

      CREATE TRIGGER send_attempt_proven_no_send_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'proven_no_send'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':proven_no_send'
            OR dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
       )
      BEGIN
        SELECT RAISE(ABORT, 'proven-no-send event key exists before its parent transition');
      END;

      CREATE TRIGGER reconciliation_event_key_guard
      BEFORE UPDATE OF sealed ON reconciliations
      WHEN OLD.sealed = 0 AND NEW.sealed = 1
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:reconciliation:' || NEW.run_id || ':' || NEW.audit_id
            OR dedupe_key = 'reconciliation:' || NEW.run_id || ':' || NEW.audit_id
       )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation event key exists before sealing');
      END;

      CREATE TRIGGER send_attempt_durable_reconciliation_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
       AND NOT EXISTS (
         SELECT 1
         FROM reconciliations rec
         JOIN reconciliation_attempts child
           ON child.reconciliation_id = rec.id AND child.attempt_id = NEW.id
         JOIN audit_snapshots audit ON audit.id = rec.audit_id
         JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
         JOIN causal_records rec_receipt ON rec_receipt.sequence = rec.causal_sequence
         JOIN causal_records audit_receipt ON audit_receipt.sequence = audit.causal_sequence
         JOIN causal_records baseline_receipt ON baseline_receipt.sequence = baseline.causal_sequence
         JOIN json_each(rec.confirmed_attempt_ids_json) confirmed
           ON confirmed.value = NEW.id
         JOIN json_each(rec_receipt.payload_json, '$.evidence') item
           ON json_extract(item.value, '$.attemptId') = NEW.id
          AND json_extract(item.value, '$.kind') = child.evidence_kind
         WHERE rec.id = json_extract(NEW.evidence, '$.reconciliationId')
           AND rec.run_id = NEW.run_id
           AND rec.audit_id = json_extract(NEW.evidence, '$.auditId')
           AND rec.baseline_id = json_extract(NEW.evidence, '$.baselineId')
           AND child.evidence_kind = json_extract(NEW.evidence, '$.evidenceKind')
           AND NEW.evidence = json_object(
             'auditId', rec.audit_id,
             'baselineId', rec.baseline_id,
             'evidenceKind', child.evidence_kind,
             'reconciliationId', rec.id
           )
           AND rec.sealed = 1
           AND rec.complete = 1
           AND rec.competing_sender_absent = 1
           AND rec.attempt_count = 30
           AND audit.run_id = rec.run_id
           AND audit.baseline_id = baseline.id
           AND audit.complete = 1
           AND audit.competing_sender_absent = 1
           AND audit.contradictory_evidence = 0
           AND baseline.run_id = rec.run_id
           AND baseline.competing_sender_absent = 1
           AND baseline.attempt_count_at_capture = 0
           AND audit.people_count - baseline.people_count = rec.attempt_count
           AND NEW.possible_causal_sequence IS NOT NULL
           AND baseline.causal_sequence IS NOT NULL
           AND audit.causal_sequence > baseline.causal_sequence
           AND audit.causal_sequence > NEW.possible_causal_sequence
           AND rec.causal_sequence > audit.causal_sequence
           AND NEW.resolution_causal_sequence > rec.causal_sequence
           AND rec_receipt.kind = 'reconciliation'
           AND rec_receipt.receipt_id = rec.id
           AND json_valid(rec_receipt.payload_json)
           AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
           AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
           AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
           AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
           AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
           AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
           AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
           AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
                 IS rec.competing_sender_absent
           AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
                 IS rec.confirmed_attempt_ids_json
           AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
           AND audit_receipt.kind = 'audit_snapshot'
           AND audit_receipt.receipt_id = audit.id
           AND json_valid(audit_receipt.payload_json)
           AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
           AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
           AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
           AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
           AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
           AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
           AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
           AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
           AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
                 IS audit.competing_sender_absent
           AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
                 IS audit.contradictory_evidence
           AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
           AND baseline_receipt.kind = 'audit_baseline'
           AND baseline_receipt.receipt_id = baseline.id
           AND json_valid(baseline_receipt.payload_json)
           AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
           AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
           AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
           AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
           AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
                 IS baseline.competing_sender_absent
           AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
                 IS baseline.attempt_count_at_capture
           AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
       )
      BEGIN
        SELECT RAISE(ABORT, 'durable transition requires an exact sealed reconciliation');
      END;
    `,
  },
  {
    id: 7,
    name: "scoped-microbatch-and-final-reconciliation",
    sql: `
      ALTER TABLE reconciliations
        ADD COLUMN scope TEXT NOT NULL DEFAULT 'final'
          CHECK(scope IN ('microbatch', 'final'));
      ALTER TABLE reconciliation_attempts ADD COLUMN matched_value TEXT;

      DROP TRIGGER reconciliation_seal_guard;
      DROP VIEW reconciliation_seal_integrity;
      DROP TRIGGER reconciliation_attempt_payload_guard_v2;
      DROP TRIGGER reconciliation_seal_event;
      DROP TRIGGER send_attempt_durable_reconciliation_guard;

      CREATE TRIGGER reconciliation_scope_receipt_guard
      BEFORE INSERT ON reconciliations
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records receipt
        WHERE receipt.sequence = NEW.causal_sequence
          AND receipt.kind = 'reconciliation'
          AND receipt.receipt_id = NEW.id
          AND json_valid(receipt.payload_json)
          AND json_extract(receipt.payload_json, '$.scope') IS NEW.scope
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation scope must equal its canonical receipt');
      END;

      CREATE TRIGGER reconciliation_scope_no_update
      BEFORE UPDATE OF scope ON reconciliations
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation scope is immutable');
      END;

      CREATE TRIGGER reconciliation_attempt_payload_guard_v3
      BEFORE INSERT ON reconciliation_attempts
      WHEN EXISTS (
        SELECT 1 FROM reconciliations rec
        WHERE rec.id = NEW.reconciliation_id AND rec.sealed != 0
      )
      OR NOT EXISTS (
        SELECT 1
        FROM reconciliations rec
        JOIN causal_records receipt ON receipt.sequence = rec.causal_sequence
        JOIN json_each(receipt.payload_json, '$.evidence') item
        WHERE rec.id = NEW.reconciliation_id
          AND rec.sealed = 0
          AND json_extract(item.value, '$.attemptId') IS NEW.attempt_id
          AND json_extract(item.value, '$.kind') IS NEW.evidence_kind
          AND (
            (
              rec.scope = 'final'
              AND NEW.matched_value IS NULL
              AND json_type(item.value, '$.matchedValue') IS NULL
            )
            OR
            (
              rec.scope = 'microbatch'
              AND NEW.matched_value IS NOT NULL
              AND length(NEW.matched_value) > 0
              AND json_extract(item.value, '$.matchedValue') IS NEW.matched_value
            )
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation child must equal its scoped canonical evidence');
      END;

      CREATE VIEW reconciliation_seal_integrity AS
      SELECT rec.id
      FROM reconciliations rec
      JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
      JOIN audit_snapshots audit
        ON audit.id = rec.audit_id
       AND audit.baseline_id = baseline.id
      JOIN causal_records baseline_receipt
        ON baseline_receipt.sequence = baseline.causal_sequence
      JOIN causal_records audit_receipt
        ON audit_receipt.sequence = audit.causal_sequence
      JOIN causal_records rec_receipt
        ON rec_receipt.sequence = rec.causal_sequence
      WHERE rec.sealed = 0
        AND rec.competing_sender_absent = 1
        AND baseline.run_id = rec.run_id
        AND baseline.competing_sender_absent = 1
        AND baseline.attempt_count_at_capture = 0
        AND audit.run_id = rec.run_id
        AND audit.complete = 1
        AND audit.competing_sender_absent = 1
        AND audit.contradictory_evidence = 0
        AND baseline.causal_sequence IS NOT NULL
        AND audit.causal_sequence > baseline.causal_sequence
        AND rec.causal_sequence > audit.causal_sequence
        AND baseline_receipt.kind = 'audit_baseline'
        AND baseline_receipt.receipt_id = baseline.id
        AND json_valid(baseline_receipt.payload_json)
        AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
        AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
        AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
        AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
        AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
              IS baseline.competing_sender_absent
        AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
              IS baseline.attempt_count_at_capture
        AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
        AND audit_receipt.kind = 'audit_snapshot'
        AND audit_receipt.receipt_id = audit.id
        AND json_valid(audit_receipt.payload_json)
        AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
        AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
        AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
        AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
        AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
        AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
        AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
        AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
        AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
              IS audit.competing_sender_absent
        AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
              IS audit.contradictory_evidence
        AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        AND rec_receipt.kind = 'reconciliation'
        AND rec_receipt.receipt_id = rec.id
        AND json_valid(rec_receipt.payload_json)
        AND json_type(rec_receipt.payload_json, '$.evidence') = 'array'
        AND json_array_length(json_extract(rec_receipt.payload_json, '$.evidence'))
              = rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
        AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
        AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
        AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
        AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
        AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
        AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
              IS rec.competing_sender_absent
        AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
              IS rec.confirmed_attempt_ids_json
        AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
        AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
        AND json_valid(rec.confirmed_attempt_ids_json)
        AND json_type(rec.confirmed_attempt_ids_json) = 'array'
        AND (
          SELECT COUNT(*) FROM json_each(rec.confirmed_attempt_ids_json)
        ) = (
          SELECT COUNT(DISTINCT confirmed.value)
          FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE typeof(confirmed.value) = 'text' AND length(trim(confirmed.value)) > 0
        )
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
        ) = rec.attempt_count
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          JOIN send_attempts attempt ON attempt.id = child.attempt_id
          WHERE child.reconciliation_id = rec.id
            AND (
              attempt.run_id != rec.run_id
              OR attempt.state NOT IN ('possible', 'durable')
              OR attempt.possible_causal_sequence IS NULL
              OR audit.causal_sequence <= attempt.possible_causal_sequence
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation_attempts child
            JOIN send_attempts attempt ON attempt.id = child.attempt_id
            WHERE child.reconciliation_id = rec.id
              AND child.attempt_id = confirmed.value
              AND attempt.run_id = rec.run_id
              AND attempt.state = 'possible'
          )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          JOIN send_attempts attempt ON attempt.id = child.attempt_id
          WHERE child.reconciliation_id = rec.id
            AND attempt.state = 'possible'
            AND NOT EXISTS (
              SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
              WHERE confirmed.value = child.attempt_id
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
            AND NOT EXISTS (
              SELECT 1
              FROM json_each(rec_receipt.payload_json, '$.evidence') item
              WHERE json_extract(item.value, '$.attemptId') IS child.attempt_id
                AND json_extract(item.value, '$.kind') IS child.evidence_kind
                AND (
                  (
                    rec.scope = 'final'
                    AND child.matched_value IS NULL
                    AND json_type(item.value, '$.matchedValue') IS NULL
                  )
                  OR
                  (
                    rec.scope = 'microbatch'
                    AND child.matched_value IS NOT NULL
                    AND json_extract(item.value, '$.matchedValue') IS child.matched_value
                  )
                )
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM json_each(rec_receipt.payload_json, '$.evidence') item
          WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation_attempts child
            WHERE child.reconciliation_id = rec.id
              AND child.attempt_id = json_extract(item.value, '$.attemptId')
              AND child.evidence_kind = json_extract(item.value, '$.kind')
              AND (
                (
                  rec.scope = 'final'
                  AND child.matched_value IS NULL
                  AND json_type(item.value, '$.matchedValue') IS NULL
                )
                OR
                (
                  rec.scope = 'microbatch'
                  AND child.matched_value IS json_extract(item.value, '$.matchedValue')
                )
              )
          )
        )
        AND (
          (
            rec.scope = 'final'
            AND rec.complete = 1
            AND rec.attempt_count = 30
            AND audit.people_count - baseline.people_count = 30
            AND (SELECT COUNT(*) FROM send_attempts WHERE run_id = rec.run_id) = 30
            AND NOT EXISTS (
              SELECT 1 FROM send_attempts attempt
              WHERE attempt.run_id = rec.run_id
                AND (
                  attempt.state NOT IN ('possible', 'durable')
                  OR NOT EXISTS (
                    SELECT 1 FROM reconciliation_attempts child
                    WHERE child.reconciliation_id = rec.id
                      AND child.attempt_id = attempt.id
                  )
                )
            )
          )
          OR
          (
            rec.scope = 'microbatch'
            AND rec.complete = 0
            AND rec.mode = 'exact'
            AND rec.attempt_count BETWEEN 1 AND 30
            AND json_array_length(rec.confirmed_attempt_ids_json) > 0
            AND audit.people_count - baseline.people_count >=
                  json_array_length(rec.confirmed_attempt_ids_json)
            AND NOT EXISTS (
              SELECT 1 FROM send_attempts attempt
              WHERE attempt.run_id = rec.run_id
                AND attempt.state = 'possible'
                AND (
                  attempt.possible_causal_sequence IS NULL
                  OR audit.causal_sequence <= attempt.possible_causal_sequence
                )
            )
            AND NOT EXISTS (
              SELECT 1
              FROM reconciliation_attempts child
              JOIN send_attempts attempt ON attempt.id = child.attempt_id
              JOIN people person ON person.id = attempt.person_id
              WHERE child.reconciliation_id = rec.id
                AND (
                  child.evidence_kind = 'aggregate'
                  OR child.matched_value IS NULL
                  OR (
                    child.evidence_kind = 'identity'
                    AND (
                      (
                        child.matched_value IS NOT person.sales_nav_id
                        AND child.matched_value IS NOT person.public_url
                        AND child.matched_value IS NOT person.lead_key
                      )
                      OR (
                        SELECT COUNT(*) FROM json_each(audit.identities_json) identity
                        WHERE identity.value = child.matched_value
                      ) != 1
                    )
                  )
                  OR (
                    child.evidence_kind = 'name'
                    AND (
                      child.matched_value != person.name
                      OR (
                        SELECT COUNT(*) FROM json_each(audit.names_json) name
                        WHERE name.value = child.matched_value
                      ) != 1
                      OR (
                        SELECT COUNT(*)
                        FROM send_attempts peer
                        JOIN people peer_person ON peer_person.id = peer.person_id
                        WHERE peer.run_id = rec.run_id
                          AND peer.state IN ('possible', 'durable')
                          AND peer_person.name = child.matched_value
                      ) != 1
                    )
                  )
                )
            )
          )
        );

      CREATE TRIGGER reconciliation_seal_guard
      BEFORE UPDATE OF sealed ON reconciliations
      WHEN NOT (
        OLD.sealed = 0
        AND NEW.sealed = 1
        AND EXISTS (
          SELECT 1 FROM reconciliation_seal_integrity valid WHERE valid.id = NEW.id
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation cannot seal before exact scoped evidence materialization');
      END;

      CREATE TRIGGER reconciliation_seal_event
      AFTER UPDATE OF sealed ON reconciliations
      WHEN OLD.sealed = 0 AND NEW.sealed = 1
      BEGIN
        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:reconciliation:' || NEW.run_id || ':' || NEW.audit_id,
          NEW.run_id,
          'reconciliation_recorded',
          json_object(
            'auditId', NEW.audit_id,
            'baselineId', NEW.baseline_id,
            'complete', json(CASE WHEN NEW.complete = 1 THEN 'true' ELSE 'false' END),
            'reconciliationId', NEW.id,
            'referencedAttempts', NEW.attempt_count,
            'scope', NEW.scope
          ),
          NEW.created_at,
          'reconciliation:' || NEW.run_id || ':' || NEW.audit_id
        );
      END;

      CREATE TRIGGER daily_run_final_reconciliation_scope_guard
      BEFORE UPDATE OF final_reconciliation_id ON daily_runs
      WHEN NEW.final_reconciliation_id IS NOT NULL
       AND NOT EXISTS (
         SELECT 1 FROM reconciliations rec
         WHERE rec.id = NEW.final_reconciliation_id
           AND rec.run_id = NEW.id
           AND rec.scope = 'final'
       )
      BEGIN
        SELECT RAISE(ABORT, 'final reconciliation reference requires final scope');
      END;

      CREATE TRIGGER daily_run_done_reconciliation_scope_guard
      BEFORE UPDATE OF status ON daily_runs
      WHEN NEW.status = 'done'
       AND NOT EXISTS (
         SELECT 1 FROM reconciliations rec
         WHERE rec.id = NEW.final_reconciliation_id
           AND rec.run_id = NEW.id
           AND rec.scope = 'final'
       )
      BEGIN
        SELECT RAISE(ABORT, 'Done requires final reconciliation scope');
      END;

      CREATE TRIGGER send_attempt_durable_reconciliation_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
       AND NOT EXISTS (
         SELECT 1
         FROM reconciliations rec
         JOIN reconciliation_attempts child
           ON child.reconciliation_id = rec.id AND child.attempt_id = NEW.id
         JOIN audit_snapshots audit ON audit.id = rec.audit_id
         JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
         JOIN causal_records rec_receipt ON rec_receipt.sequence = rec.causal_sequence
         JOIN json_each(rec.confirmed_attempt_ids_json) confirmed
           ON confirmed.value = NEW.id
         WHERE rec.id = json_extract(NEW.evidence, '$.reconciliationId')
           AND rec.run_id = NEW.run_id
           AND rec.audit_id = json_extract(NEW.evidence, '$.auditId')
           AND rec.baseline_id = json_extract(NEW.evidence, '$.baselineId')
           AND rec.scope = json_extract(NEW.evidence, '$.reconciliationScope')
           AND child.evidence_kind = json_extract(NEW.evidence, '$.evidenceKind')
           AND NEW.evidence = json_object(
             'auditId', rec.audit_id,
             'baselineId', rec.baseline_id,
             'evidenceKind', child.evidence_kind,
             'reconciliationId', rec.id,
             'reconciliationScope', rec.scope
           )
           AND rec.sealed = 1
           AND rec.competing_sender_absent = 1
           AND audit.run_id = rec.run_id
           AND audit.baseline_id = baseline.id
           AND audit.complete = 1
           AND audit.competing_sender_absent = 1
           AND audit.contradictory_evidence = 0
           AND baseline.run_id = rec.run_id
           AND baseline.competing_sender_absent = 1
           AND baseline.attempt_count_at_capture = 0
           AND NEW.possible_causal_sequence IS NOT NULL
           AND audit.causal_sequence > NEW.possible_causal_sequence
           AND rec.causal_sequence > audit.causal_sequence
           AND NEW.resolution_causal_sequence > rec.causal_sequence
           AND rec_receipt.kind = 'reconciliation'
           AND rec_receipt.receipt_id = rec.id
           AND json_valid(rec_receipt.payload_json)
           AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
           AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
           AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
           AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
           AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
           AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
           AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
           AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
           AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
                 IS rec.competing_sender_absent
           AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
                 IS rec.confirmed_attempt_ids_json
           AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
           AND (
             (
               rec.scope = 'final'
               AND rec.complete = 1
               AND rec.attempt_count = 30
             )
             OR
             (
               rec.scope = 'microbatch'
               AND rec.complete = 0
               AND rec.mode = 'exact'
               AND child.evidence_kind IN ('identity', 'name')
               AND child.matched_value IS NOT NULL
               AND (
                 (
                   child.evidence_kind = 'identity'
                   AND EXISTS (
                     SELECT 1 FROM people person
                     WHERE person.id = NEW.person_id
                       AND child.matched_value IN (
                         person.sales_nav_id, person.public_url, person.lead_key
                       )
                   )
                   AND (
                     SELECT COUNT(*) FROM json_each(audit.identities_json) identity
                     WHERE identity.value = child.matched_value
                   ) = 1
                 )
                 OR
                 (
                   child.evidence_kind = 'name'
                   AND child.matched_value = (SELECT name FROM people WHERE id = NEW.person_id)
                   AND (
                     SELECT COUNT(*) FROM json_each(audit.names_json) name
                     WHERE name.value = child.matched_value
                   ) = 1
                 )
               )
             )
           )
       )
      BEGIN
        SELECT RAISE(ABORT, 'durable transition requires an exact sealed scoped reconciliation');
      END;
    `,
  },
  {
    id: 8,
    name: "planned-proven-no-send",
    sql: `
      DROP TRIGGER send_attempt_transition_guard_v2;
      DROP TRIGGER send_attempt_resolution_receipt_mutation_guard;

      CREATE TRIGGER send_attempt_transition_guard_v3
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state != OLD.state
       AND NOT (
         OLD.state = 'planned'
         AND NEW.state = 'possible'
         AND NEW.possible_evidence_json IS NOT NULL
         AND NEW.evidence IS NEW.possible_evidence_json
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.possible_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.planned_causal_sequence
             AND c.kind = 'attempt_possible'
             AND c.receipt_id = NEW.possible_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.possible_receipt_key
             AND json_extract(c.payload_json, '$.attemptedAt') IS NEW.attempted_at
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.possible_evidence_json
             AND json_extract(c.payload_json, '$.prepareReceiptJson') IS NEW.prepare_receipt_json
             AND json_extract(c.payload_json, '$.prepareBindingJson') IS NEW.prepare_binding_json
         )
       )
       AND NOT (
         OLD.state = 'possible'
         AND NEW.state IN ('durable', 'proven_no_send')
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.resolution_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.possible_causal_sequence
             AND c.kind = CASE NEW.state
               WHEN 'durable' THEN 'attempt_durable'
               ELSE 'attempt_proven_no_send'
             END
             AND c.receipt_id = NEW.resolution_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.resolution_receipt_key
             AND json_extract(c.payload_json, '$.resolvedAt') IS NEW.resolved_at
             AND json_extract(c.payload_json, '$.state') IS NEW.state
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.evidence
         )
       )
       AND NOT (
         OLD.state = 'planned'
         AND NEW.state = 'proven_no_send'
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.resolution_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.planned_causal_sequence
             AND c.kind = 'attempt_proven_no_send'
             AND c.receipt_id = NEW.resolution_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.resolution_receipt_key
             AND json_extract(c.payload_json, '$.resolvedAt') IS NEW.resolved_at
             AND json_extract(c.payload_json, '$.state') IS NEW.state
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.evidence
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid send attempt transition receipt');
      END;

      CREATE TRIGGER send_attempt_resolution_receipt_mutation_guard_v2
      BEFORE UPDATE OF resolved_at, resolution_receipt_key, resolution_causal_sequence
      ON send_attempts
      WHEN NOT (
        (
          (
            OLD.state = 'possible'
            AND NEW.state IN ('durable', 'proven_no_send')
          )
          OR (
            OLD.state = 'planned'
            AND NEW.state = 'proven_no_send'
          )
        )
        AND OLD.resolution_causal_sequence IS NULL
        AND NEW.resolution_causal_sequence IS NOT NULL
      )
      BEGIN
        SELECT RAISE(ABORT, 'resolution receipt fields may only be set during resolution');
      END;

      CREATE TRIGGER send_attempt_planned_proven_no_send_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'proven_no_send'
      BEGIN
        INSERT INTO relationship_facts
          (id, person_id, kind, effective_at, run_id, evidence)
        VALUES (
          'proven-no-send:' || NEW.id, NEW.person_id, 'proven_no_send',
          NEW.resolved_at, NEW.run_id, NEW.evidence
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM relationship_facts
          WHERE id = 'proven-no-send:' || NEW.id
            AND person_id = NEW.person_id
            AND kind = 'proven_no_send'
            AND effective_at = NEW.resolved_at
            AND run_id = NEW.run_id
            AND evidence = NEW.evidence
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact relationship fact') END;

        UPDATE reservoir_entries SET status = 'ineligible'
        WHERE id = NEW.reservoir_entry_id AND status = 'selected';

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':proven_no_send',
          NEW.run_id, 'send_proven_no_send', json_object('attemptId', NEW.id),
          NEW.resolved_at, 'attempt:' || NEW.id || ':proven_no_send'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
            AND run_id = NEW.run_id
            AND type = 'send_proven_no_send'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.resolved_at
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact event') END;
      END;

      CREATE TRIGGER send_attempt_planned_proven_no_send_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'proven_no_send'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':proven_no_send'
            OR dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
       )
      BEGIN
        SELECT RAISE(ABORT, 'proven-no-send event key exists before its parent transition');
      END;
    `,
  },
  {
    id: 9,
    name: "exact-name-reconciliation-without-count-delta",
    sql: `
      CREATE VIEW reconciliation_seal_integrity_exact_name_without_delta AS
      SELECT rec.id
      FROM reconciliations rec
      JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
      JOIN audit_snapshots audit
        ON audit.id = rec.audit_id
       AND audit.baseline_id = baseline.id
      JOIN causal_records baseline_receipt
        ON baseline_receipt.sequence = baseline.causal_sequence
      JOIN causal_records audit_receipt
        ON audit_receipt.sequence = audit.causal_sequence
      JOIN causal_records rec_receipt
        ON rec_receipt.sequence = rec.causal_sequence
      JOIN reconciliation_attempts child
        ON child.reconciliation_id = rec.id
      JOIN send_attempts attempt
        ON attempt.id = child.attempt_id
      JOIN people person
        ON person.id = attempt.person_id
      WHERE rec.sealed = 0
        AND rec.scope = 'microbatch'
        AND rec.complete = 0
        AND rec.mode = 'exact'
        AND rec.attempt_count BETWEEN 1 AND 30
        AND rec.competing_sender_absent = 1
        AND baseline.run_id = rec.run_id
        AND baseline.competing_sender_absent = 1
        AND baseline.attempt_count_at_capture = 0
        AND audit.run_id = rec.run_id
        AND audit.baseline_id = baseline.id
        AND audit.complete = 1
        AND audit.competing_sender_absent = 1
        AND audit.contradictory_evidence = 0
        AND baseline.causal_sequence IS NOT NULL
        AND audit.causal_sequence > baseline.causal_sequence
        AND rec.causal_sequence > audit.causal_sequence
        AND baseline_receipt.kind = 'audit_baseline'
        AND baseline_receipt.receipt_id = baseline.id
        AND json_valid(baseline_receipt.payload_json)
        AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
        AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
        AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
        AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
        AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
              IS baseline.competing_sender_absent
        AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
              IS baseline.attempt_count_at_capture
        AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
        AND audit_receipt.kind = 'audit_snapshot'
        AND audit_receipt.receipt_id = audit.id
        AND json_valid(audit_receipt.payload_json)
        AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
        AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
        AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
        AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
        AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
        AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
        AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
        AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
        AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
              IS audit.competing_sender_absent
        AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
              IS audit.contradictory_evidence
        AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        AND rec_receipt.kind = 'reconciliation'
        AND rec_receipt.receipt_id = rec.id
        AND json_valid(rec_receipt.payload_json)
        AND json_type(rec_receipt.payload_json, '$.evidence') = 'array'
        AND json_array_length(json_extract(rec_receipt.payload_json, '$.evidence'))
              = rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
        AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
        AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
        AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
        AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
        AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
        AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
              IS rec.competing_sender_absent
        AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
              IS rec.confirmed_attempt_ids_json
        AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
        AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
        AND json_valid(rec.confirmed_attempt_ids_json)
        AND json_type(rec.confirmed_attempt_ids_json) = 'array'
        AND json_array_length(rec.confirmed_attempt_ids_json) = rec.attempt_count
        AND (
          SELECT COUNT(*) FROM json_each(rec.confirmed_attempt_ids_json)
        ) = (
          SELECT COUNT(DISTINCT confirmed.value)
          FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE typeof(confirmed.value) = 'text' AND length(trim(confirmed.value)) > 0
        )
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child_row
          WHERE child_row.reconciliation_id = rec.id
        ) = rec.attempt_count
        AND NOT EXISTS (
          SELECT 1
          FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE NOT EXISTS (
            SELECT 1
            FROM reconciliation_attempts child_row
            JOIN send_attempts attempt_row ON attempt_row.id = child_row.attempt_id
            WHERE child_row.reconciliation_id = rec.id
              AND child_row.attempt_id = confirmed.value
              AND attempt_row.run_id = rec.run_id
              AND attempt_row.state = 'possible'
              AND attempt_row.possible_causal_sequence IS NOT NULL
              AND audit.causal_sequence > attempt_row.possible_causal_sequence
          )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM reconciliation_attempts child_row
          JOIN send_attempts attempt_row ON attempt_row.id = child_row.attempt_id
          WHERE child_row.reconciliation_id = rec.id
            AND (
              attempt_row.run_id != rec.run_id
              OR attempt_row.state != 'possible'
              OR attempt_row.possible_causal_sequence IS NULL
              OR audit.causal_sequence <= attempt_row.possible_causal_sequence
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM send_attempts attempt_row
          WHERE attempt_row.run_id = rec.run_id
            AND attempt_row.state = 'possible'
            AND NOT EXISTS (
              SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
              WHERE confirmed.value = attempt_row.id
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM reconciliation_attempts child_row
          WHERE child_row.reconciliation_id = rec.id
            AND NOT EXISTS (
              SELECT 1
              FROM json_each(rec_receipt.payload_json, '$.evidence') item
              WHERE json_extract(item.value, '$.attemptId') IS child_row.attempt_id
                AND json_extract(item.value, '$.kind') IS child_row.evidence_kind
                AND json_extract(item.value, '$.matchedValue') IS child_row.matched_value
            )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM json_each(rec_receipt.payload_json, '$.evidence') item
          WHERE NOT EXISTS (
            SELECT 1
            FROM reconciliation_attempts child_row
            WHERE child_row.reconciliation_id = rec.id
              AND child_row.attempt_id = json_extract(item.value, '$.attemptId')
              AND child_row.evidence_kind = json_extract(item.value, '$.kind')
              AND child_row.matched_value IS json_extract(item.value, '$.matchedValue')
          )
        )
        AND NOT EXISTS (
          SELECT 1
          FROM reconciliation_attempts invalid_child
          JOIN send_attempts invalid_attempt ON invalid_attempt.id = invalid_child.attempt_id
          JOIN people invalid_person ON invalid_person.id = invalid_attempt.person_id
          WHERE invalid_child.reconciliation_id = rec.id
            AND (
              invalid_child.evidence_kind != 'name'
              OR invalid_child.matched_value IS NULL
              OR invalid_child.matched_value IS NOT invalid_person.name
              OR (
                SELECT COUNT(*) FROM json_each(audit.names_json) name
                WHERE name.value = invalid_child.matched_value
              ) != 1
              OR (
                SELECT COUNT(*)
                FROM send_attempts peer
                JOIN people peer_person ON peer_person.id = peer.person_id
                WHERE peer.run_id = rec.run_id
                  AND peer.state IN ('possible', 'durable')
                  AND peer_person.name = invalid_child.matched_value
              ) != 1
            )
        )
        AND child.evidence_kind = 'name'
        AND child.matched_value IS person.name
        AND (
          SELECT COUNT(*) FROM json_each(audit.names_json) name
          WHERE name.value = child.matched_value
        ) = 1
        AND (
          SELECT COUNT(*)
          FROM send_attempts peer
          JOIN people peer_person ON peer_person.id = peer.person_id
          WHERE peer.run_id = rec.run_id
            AND peer.state IN ('possible', 'durable')
            AND peer_person.name = child.matched_value
        ) = 1;

      DROP TRIGGER reconciliation_seal_guard;

      CREATE TRIGGER reconciliation_seal_guard
      BEFORE UPDATE OF sealed ON reconciliations
      WHEN NOT (
        OLD.sealed = 0
        AND NEW.sealed = 1
        AND (
          EXISTS (
            SELECT 1 FROM reconciliation_seal_integrity valid WHERE valid.id = NEW.id
          )
          OR EXISTS (
            SELECT 1
            FROM reconciliation_seal_integrity_exact_name_without_delta valid
            WHERE valid.id = NEW.id
          )
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation cannot seal before exact scoped evidence materialization');
      END;
    `,
  },

  {
    id: 10,
    name: "audit-identity-delta-and-confirmed-attempt-count",
    sql: `
      ALTER TABLE audit_baselines
        ADD COLUMN identities_json TEXT NOT NULL DEFAULT '[]'
          CHECK(json_valid(identities_json) AND json_type(identities_json) = 'array');

      DROP TRIGGER audit_baseline_receipt_guard_v2;
      CREATE TRIGGER audit_baseline_receipt_guard_v3
      BEFORE INSERT ON audit_baselines
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = 'audit_baseline'
          AND c.receipt_id = NEW.id
          AND json_valid(c.payload_json)
          AND json_extract(c.payload_json, '$.baselineId') IS NEW.id
          AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
          AND json_extract(c.payload_json, '$.invocationId') IS NEW.invocation_id
          AND json_extract(c.payload_json, '$.peopleCount') IS NEW.people_count
          AND json_extract(c.payload_json, '$.identities') IS NEW.identities_json
          AND json_extract(c.payload_json, '$.competingSenderAbsent')
                IS NEW.competing_sender_absent
          AND json_extract(c.payload_json, '$.attemptCountAtCapture')
                IS NEW.attempt_count_at_capture
          AND json_extract(c.payload_json, '$.capturedAt') IS NEW.captured_at
      )
      BEGIN
        SELECT RAISE(ABORT, 'audit baseline fields must equal its immutable receipt');
      END;

      DROP TRIGGER send_attempt_possible_receipt_mutation_guard_v2;
      DROP TRIGGER send_attempt_evidence_mutation_guard;

      CREATE TRIGGER send_attempt_possible_receipt_mutation_guard_v3
      BEFORE UPDATE OF attempted_at, possible_receipt_key, possible_causal_sequence,
        possible_evidence_json, prepare_receipt_json, prepare_binding_json
      ON send_attempts
      WHEN NOT (
        (
          OLD.state = 'planned'
          AND NEW.state = 'possible'
          AND OLD.possible_causal_sequence IS NULL
          AND NEW.possible_causal_sequence IS NOT NULL
        )
        OR
        (
          OLD.state = 'possible'
          AND NEW.state = 'possible'
          AND OLD.commit_started_at IS NULL
          AND NEW.commit_started_at IS NULL
          AND OLD.possible_causal_sequence IS NOT NULL
          AND NEW.possible_causal_sequence IS NOT NULL
          AND NEW.possible_causal_sequence = (SELECT MAX(sequence) FROM causal_records)
          AND NEW.possible_evidence_json IS NOT NULL
          AND NEW.evidence IS NEW.possible_evidence_json
          AND NEW.prepare_receipt_json IS NOT NULL
          AND NEW.prepare_binding_json IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM causal_records c
            WHERE c.sequence = NEW.possible_causal_sequence
              AND c.kind = 'attempt_possible'
              AND c.receipt_id = NEW.possible_receipt_key
              AND json_valid(c.payload_json)
              AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
              AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
              AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
              AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
              AND json_extract(c.payload_json, '$.receiptId') IS NEW.possible_receipt_key
              AND json_extract(c.payload_json, '$.attemptedAt') IS NEW.attempted_at
              AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.possible_evidence_json
              AND json_extract(c.payload_json, '$.prepareReceiptJson') IS NEW.prepare_receipt_json
              AND json_extract(c.payload_json, '$.prepareBindingJson') IS NEW.prepare_binding_json
          )
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'possible receipt fields may only change on planned transition or pre-commit refresh');
      END;

      DROP TRIGGER daily_run_done_evidence_guard;
      CREATE TRIGGER daily_run_done_evidence_guard
      BEFORE UPDATE OF status ON daily_runs
      WHEN NEW.status = 'done'
       AND NEW.final_reconciliation_id IS NOT NULL
       AND NOT EXISTS (
         SELECT 1
         FROM reconciliations rec
         JOIN audit_snapshots audit ON audit.id = rec.audit_id
         JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
         WHERE rec.id = NEW.final_reconciliation_id
           AND rec.run_id = NEW.id
           AND audit.run_id = NEW.id
           AND baseline.run_id = NEW.id
           AND audit.baseline_id = baseline.id
           AND baseline.attempt_count_at_capture = 0
           AND baseline.competing_sender_absent = 1
           AND audit.complete = 1
           AND audit.competing_sender_absent = 1
           AND audit.contradictory_evidence = 0
           AND audit.people_count - baseline.people_count = 30
           AND rec.attempt_count = 30
           AND rec.complete = 1
           AND rec.competing_sender_absent = 1
           AND baseline.causal_sequence IS NOT NULL
           AND audit.causal_sequence > baseline.causal_sequence
           AND rec.causal_sequence > audit.causal_sequence
           AND (SELECT COUNT(*) FROM send_attempts a
                WHERE a.run_id = NEW.id AND a.state = 'durable') = 30
           AND (SELECT COUNT(*) FROM send_attempts a
                WHERE a.run_id = NEW.id AND a.state IN ('planned', 'possible')) = 0
           AND NOT EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.run_id = NEW.id
               AND attempt.state = 'durable'
               AND (
                 attempt.planned_causal_sequence IS NULL
                 OR attempt.planned_causal_sequence <= baseline.causal_sequence
                 OR attempt.possible_causal_sequence IS NULL
                 OR attempt.possible_causal_sequence <= baseline.causal_sequence
               )
           )
       )
      BEGIN
        SELECT RAISE(ABORT, 'final reconciliation lacks exact causal audit evidence');
      END;

      DROP VIEW IF EXISTS reconciliation_seal_integrity;
      CREATE VIEW reconciliation_seal_integrity AS
      SELECT rec.id
      FROM reconciliations rec
      JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
      JOIN audit_snapshots audit
        ON audit.id = rec.audit_id
       AND audit.baseline_id = baseline.id
      JOIN causal_records baseline_receipt
        ON baseline_receipt.sequence = baseline.causal_sequence
      JOIN causal_records audit_receipt
        ON audit_receipt.sequence = audit.causal_sequence
      JOIN causal_records rec_receipt
        ON rec_receipt.sequence = rec.causal_sequence
      WHERE rec.sealed = 0
        AND baseline.run_id = rec.run_id
        AND baseline.competing_sender_absent = 1
        AND baseline.attempt_count_at_capture = 0
        AND audit.run_id = rec.run_id
        AND audit.complete = 1
        AND audit.contradictory_evidence = 0
        AND (
          (
            rec.scope = 'final'
            AND rec.competing_sender_absent = 1
            AND audit.competing_sender_absent = 1
          )
          OR
          (
            rec.scope = 'microbatch'
            AND rec.mode = 'exact'
            AND rec.competing_sender_absent =
              CASE WHEN audit.competing_sender_absent = 1 THEN 1 ELSE 0 END
          )
        )
        AND baseline.causal_sequence IS NOT NULL
        AND audit.causal_sequence > baseline.causal_sequence
        AND rec.causal_sequence > audit.causal_sequence
        AND baseline_receipt.kind = 'audit_baseline'
        AND baseline_receipt.receipt_id = baseline.id
        AND json_valid(baseline_receipt.payload_json)
        AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
        AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
        AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
        AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
        AND json_extract(baseline_receipt.payload_json, '$.identities') IS baseline.identities_json
        AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
              IS baseline.competing_sender_absent
        AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
              IS baseline.attempt_count_at_capture
        AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
        AND audit_receipt.kind = 'audit_snapshot'
        AND audit_receipt.receipt_id = audit.id
        AND json_valid(audit_receipt.payload_json)
        AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
        AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
        AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
        AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
        AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
        AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
        AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
        AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
        AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
              IS audit.competing_sender_absent
        AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
              IS audit.contradictory_evidence
        AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        AND rec_receipt.kind = 'reconciliation'
        AND rec_receipt.receipt_id = rec.id
        AND json_valid(rec_receipt.payload_json)
        AND json_type(rec_receipt.payload_json, '$.evidence') = 'array'
        AND json_array_length(json_extract(rec_receipt.payload_json, '$.evidence'))
              = rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
        AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
        AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
        AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
        AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
        AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
        AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
              IS rec.competing_sender_absent
        AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
              IS rec.confirmed_attempt_ids_json
        AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
        AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
        AND json_valid(rec.confirmed_attempt_ids_json)
        AND json_type(rec.confirmed_attempt_ids_json) = 'array'
        AND (
          SELECT COUNT(*) FROM json_each(rec.confirmed_attempt_ids_json)
        ) = (
          SELECT COUNT(DISTINCT confirmed.value)
          FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE typeof(confirmed.value) = 'text' AND length(trim(confirmed.value)) > 0
        )
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
        ) = rec.attempt_count
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          JOIN send_attempts attempt ON attempt.id = child.attempt_id
          WHERE child.reconciliation_id = rec.id
            AND (
              attempt.run_id != rec.run_id
              OR attempt.state NOT IN ('possible', 'durable')
              OR attempt.possible_causal_sequence IS NULL
              OR audit.causal_sequence <= attempt.possible_causal_sequence
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation_attempts child
            JOIN send_attempts attempt ON attempt.id = child.attempt_id
            WHERE child.reconciliation_id = rec.id
              AND child.attempt_id = confirmed.value
              AND attempt.run_id = rec.run_id
              AND attempt.state = 'possible'
          )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          JOIN send_attempts attempt ON attempt.id = child.attempt_id
          WHERE child.reconciliation_id = rec.id
            AND attempt.state = 'possible'
            AND NOT EXISTS (
              SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
              WHERE confirmed.value = child.attempt_id
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
            AND NOT EXISTS (
              SELECT 1
              FROM json_each(rec_receipt.payload_json, '$.evidence') item
              WHERE json_extract(item.value, '$.attemptId') IS child.attempt_id
                AND json_extract(item.value, '$.kind') IS child.evidence_kind
                AND (
                  (
                    rec.scope = 'final'
                    AND child.matched_value IS NULL
                    AND json_type(item.value, '$.matchedValue') IS NULL
                  )
                  OR
                  (
                    rec.scope = 'microbatch'
                    AND child.matched_value IS NOT NULL
                    AND json_extract(item.value, '$.matchedValue') IS child.matched_value
                  )
                )
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM json_each(rec_receipt.payload_json, '$.evidence') item
          WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation_attempts child
            WHERE child.reconciliation_id = rec.id
              AND child.attempt_id = json_extract(item.value, '$.attemptId')
              AND child.evidence_kind = json_extract(item.value, '$.kind')
              AND (
                (
                  rec.scope = 'final'
                  AND child.matched_value IS NULL
                  AND json_type(item.value, '$.matchedValue') IS NULL
                )
                OR
                (
                  rec.scope = 'microbatch'
                  AND child.matched_value IS json_extract(item.value, '$.matchedValue')
                )
              )
          )
        )
        AND (
          (
            rec.scope = 'final'
            AND rec.complete = 1
            AND rec.attempt_count = 30
            AND audit.people_count - baseline.people_count = 30
            AND (SELECT COUNT(*) FROM send_attempts
                 WHERE run_id = rec.run_id AND state IN ('possible', 'durable')) = 30
            AND (SELECT COUNT(*) FROM send_attempts
                 WHERE run_id = rec.run_id AND state = 'planned') = 0
            AND NOT EXISTS (
              SELECT 1 FROM send_attempts attempt
              WHERE attempt.run_id = rec.run_id
                AND attempt.state IN ('possible', 'durable')
                AND NOT EXISTS (
                  SELECT 1 FROM reconciliation_attempts child
                  WHERE child.reconciliation_id = rec.id
                    AND child.attempt_id = attempt.id
                )
            )
          )
          OR
          (
            rec.scope = 'microbatch'
            AND rec.complete = 0
            AND rec.mode = 'exact'
            AND rec.attempt_count BETWEEN 1 AND 30
            AND json_array_length(rec.confirmed_attempt_ids_json) > 0
            AND (
              audit.people_count - baseline.people_count >=
                json_array_length(rec.confirmed_attempt_ids_json)
              OR (
                audit.people_count = baseline.people_count
                AND NOT EXISTS (
                  SELECT 1 FROM reconciliation_attempts child
                  WHERE child.reconciliation_id = rec.id
                    AND child.evidence_kind != 'name'
                )
              )
            )
            AND NOT EXISTS (
              SELECT 1 FROM send_attempts attempt
              WHERE attempt.run_id = rec.run_id
                AND attempt.state = 'possible'
                AND (
                  attempt.possible_causal_sequence IS NULL
                  OR audit.causal_sequence <= attempt.possible_causal_sequence
                )
            )
            AND NOT EXISTS (
              SELECT 1
              FROM reconciliation_attempts child
              JOIN send_attempts attempt ON attempt.id = child.attempt_id
              JOIN people person ON person.id = attempt.person_id
              WHERE child.reconciliation_id = rec.id
                AND (
                  child.evidence_kind = 'aggregate'
                  OR child.matched_value IS NULL
                  OR (
                    child.evidence_kind = 'identity'
                    AND (
                      (
                        child.matched_value IS NOT person.sales_nav_id
                        AND child.matched_value IS NOT person.public_url
                        AND child.matched_value IS NOT person.lead_key
                      )
                      OR (
                        SELECT COUNT(*) FROM json_each(audit.identities_json) identity
                        WHERE identity.value = child.matched_value
                      ) != 1
                    )
                  )
                  OR (
                    child.evidence_kind = 'name'
                    AND (
                      child.matched_value != person.name
                      OR (
                        SELECT COUNT(*) FROM json_each(audit.names_json) name
                        WHERE name.value = child.matched_value
                      ) != 1
                      OR (
                        SELECT COUNT(*)
                        FROM send_attempts peer
                        JOIN people peer_person ON peer_person.id = peer.person_id
                        WHERE peer.run_id = rec.run_id
                          AND peer.state IN ('possible', 'durable')
                          AND peer_person.name = child.matched_value
                      ) != 1
                    )
                  )
                )
            )
          )
        );

DROP VIEW IF EXISTS reconciliation_seal_integrity_exact_name_without_delta;
      CREATE VIEW reconciliation_seal_integrity_exact_name_without_delta AS
      SELECT rec.id
      FROM reconciliations rec
      JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
      JOIN audit_snapshots audit
        ON audit.id = rec.audit_id
       AND audit.baseline_id = baseline.id
      JOIN causal_records baseline_receipt
        ON baseline_receipt.sequence = baseline.causal_sequence
      JOIN causal_records audit_receipt
        ON audit_receipt.sequence = audit.causal_sequence
      JOIN causal_records rec_receipt
        ON rec_receipt.sequence = rec.causal_sequence
      JOIN reconciliation_attempts child
        ON child.reconciliation_id = rec.id
      JOIN send_attempts attempt
        ON attempt.id = child.attempt_id
      JOIN people person
        ON person.id = attempt.person_id
      WHERE rec.sealed = 0
        AND rec.scope = 'microbatch'
        AND rec.complete = 0
        AND rec.mode = 'exact'
        AND rec.attempt_count BETWEEN 1 AND 30
        AND rec.competing_sender_absent IN (0, 1)
        AND baseline.run_id = rec.run_id
        AND baseline.competing_sender_absent = 1
        AND baseline.attempt_count_at_capture = 0
        AND audit.run_id = rec.run_id
        AND audit.baseline_id = baseline.id
        AND audit.complete = 1
        AND audit.competing_sender_absent IN (0, 1)
        AND audit.contradictory_evidence = 0
        AND audit.people_count = baseline.people_count
        AND baseline.causal_sequence IS NOT NULL
        AND audit.causal_sequence > baseline.causal_sequence
        AND rec.causal_sequence > audit.causal_sequence
        AND baseline_receipt.kind = 'audit_baseline'
        AND baseline_receipt.receipt_id = baseline.id
        AND json_valid(baseline_receipt.payload_json)
        AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
        AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
        AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
        AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
        AND json_extract(baseline_receipt.payload_json, '$.identities') IS baseline.identities_json
        AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
              IS baseline.competing_sender_absent
        AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
              IS baseline.attempt_count_at_capture
        AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
        AND audit_receipt.kind = 'audit_snapshot'
        AND audit_receipt.receipt_id = audit.id
        AND json_valid(audit_receipt.payload_json)
        AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
        AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
        AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
        AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
        AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
        AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
        AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
        AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
        AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
              IS audit.competing_sender_absent
        AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
              IS audit.contradictory_evidence
        AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        AND rec_receipt.kind = 'reconciliation'
        AND rec_receipt.receipt_id = rec.id
        AND json_valid(rec_receipt.payload_json)
        AND json_type(rec_receipt.payload_json, '$.evidence') = 'array'
        AND json_array_length(json_extract(rec_receipt.payload_json, '$.evidence'))
              = rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
        AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
        AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
        AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
        AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
        AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
        AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
              IS rec.competing_sender_absent
        AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
              IS rec.confirmed_attempt_ids_json
        AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
        AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
        AND json_valid(rec.confirmed_attempt_ids_json)
        AND json_type(rec.confirmed_attempt_ids_json) = 'array'
        AND json_array_length(rec.confirmed_attempt_ids_json) = rec.attempt_count
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child_row
          WHERE child_row.reconciliation_id = rec.id
        ) = rec.attempt_count
        AND NOT EXISTS (
          SELECT 1
          FROM reconciliation_attempts invalid_child
          JOIN send_attempts invalid_attempt ON invalid_attempt.id = invalid_child.attempt_id
          JOIN people invalid_person ON invalid_person.id = invalid_attempt.person_id
          WHERE invalid_child.reconciliation_id = rec.id
            AND (
              invalid_child.evidence_kind != 'name'
              OR invalid_child.matched_value IS NULL
              OR invalid_child.matched_value IS NOT invalid_person.name
              OR (
                SELECT COUNT(*) FROM json_each(audit.names_json) name
                WHERE name.value = invalid_child.matched_value
              ) != 1
              OR (
                SELECT COUNT(*)
                FROM send_attempts peer
                JOIN people peer_person ON peer_person.id = peer.person_id
                WHERE peer.run_id = rec.run_id
                  AND peer.state IN ('possible', 'durable')
                  AND peer_person.name = invalid_child.matched_value
              ) != 1
            )
        )
        AND child.evidence_kind = 'name'
        AND child.matched_value IS person.name
        AND (
          SELECT COUNT(*) FROM json_each(audit.names_json) name
          WHERE name.value = child.matched_value
        ) = 1
        AND (
          SELECT COUNT(*)
          FROM send_attempts peer
          JOIN people peer_person ON peer_person.id = peer.person_id
          WHERE peer.run_id = rec.run_id
            AND peer.state IN ('possible', 'durable')
            AND peer_person.name = child.matched_value
        ) = 1;


      DROP TRIGGER send_attempt_durable_reconciliation_guard;
      CREATE TRIGGER send_attempt_durable_reconciliation_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
       AND NOT EXISTS (
         SELECT 1
         FROM reconciliations rec
         JOIN reconciliation_attempts child
           ON child.reconciliation_id = rec.id AND child.attempt_id = NEW.id
         JOIN audit_snapshots audit ON audit.id = rec.audit_id
         JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
         JOIN causal_records rec_receipt ON rec_receipt.sequence = rec.causal_sequence
         JOIN json_each(rec.confirmed_attempt_ids_json) confirmed
           ON confirmed.value = NEW.id
         WHERE rec.id = json_extract(NEW.evidence, '$.reconciliationId')
           AND rec.run_id = NEW.run_id
           AND rec.audit_id = json_extract(NEW.evidence, '$.auditId')
           AND rec.baseline_id = json_extract(NEW.evidence, '$.baselineId')
           AND rec.scope = json_extract(NEW.evidence, '$.reconciliationScope')
           AND child.evidence_kind = json_extract(NEW.evidence, '$.evidenceKind')
           AND NEW.evidence = json_object(
             'auditId', rec.audit_id,
             'baselineId', rec.baseline_id,
             'evidenceKind', child.evidence_kind,
             'reconciliationId', rec.id,
             'reconciliationScope', rec.scope
           )
           AND rec.sealed = 1
           AND (
             (
               rec.scope = 'final'
               AND rec.competing_sender_absent = 1
               AND audit.competing_sender_absent = 1
             )
             OR
             (
               rec.scope = 'microbatch'
               AND rec.mode = 'exact'
               AND rec.competing_sender_absent =
                 CASE WHEN audit.competing_sender_absent = 1 THEN 1 ELSE 0 END
               AND audit.competing_sender_absent IN (0, 1)
             )
           )
           AND audit.run_id = rec.run_id
           AND audit.baseline_id = baseline.id
           AND audit.complete = 1
           AND audit.contradictory_evidence = 0
           AND baseline.run_id = rec.run_id
           AND baseline.competing_sender_absent = 1
           AND baseline.attempt_count_at_capture = 0
           AND NEW.possible_causal_sequence IS NOT NULL
           AND audit.causal_sequence > NEW.possible_causal_sequence
           AND rec.causal_sequence > audit.causal_sequence
           AND NEW.resolution_causal_sequence > rec.causal_sequence
           AND rec_receipt.kind = 'reconciliation'
           AND rec_receipt.receipt_id = rec.id
           AND json_valid(rec_receipt.payload_json)
           AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
           AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
           AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
           AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
           AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
           AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
           AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
           AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
           AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
                 IS rec.competing_sender_absent
           AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
                 IS rec.confirmed_attempt_ids_json
           AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
           AND (
             (
               rec.scope = 'final'
               AND rec.complete = 1
               AND rec.attempt_count = 30
             )
             OR
             (
               rec.scope = 'microbatch'
               AND rec.complete = 0
               AND rec.mode = 'exact'
               AND child.evidence_kind IN ('identity', 'name')
               AND child.matched_value IS NOT NULL
               AND (
                 (
                   child.evidence_kind = 'identity'
                   AND EXISTS (
                     SELECT 1 FROM people person
                     WHERE person.id = NEW.person_id
                       AND child.matched_value IN (
                         person.sales_nav_id, person.public_url, person.lead_key
                       )
                   )
                   AND (
                     SELECT COUNT(*) FROM json_each(audit.identities_json) identity
                     WHERE identity.value = child.matched_value
                   ) = 1
                 )
                 OR
                 (
                   child.evidence_kind = 'name'
                   AND child.matched_value = (SELECT name FROM people WHERE id = NEW.person_id)
                   AND (
                     SELECT COUNT(*) FROM json_each(audit.names_json) name
                     WHERE name.value = child.matched_value
                   ) = 1
                 )
               )
             )
           )
       )
      BEGIN
        SELECT RAISE(ABORT, 'durable transition requires an exact sealed scoped reconciliation');
      END;

      DROP TRIGGER reconciliation_seal_guard;
      CREATE TRIGGER reconciliation_seal_guard
      BEFORE UPDATE OF sealed ON reconciliations
      WHEN NOT (
        OLD.sealed = 0
        AND NEW.sealed = 1
        AND (
          EXISTS (
            SELECT 1 FROM reconciliation_seal_integrity valid WHERE valid.id = NEW.id
          )
          OR EXISTS (
            SELECT 1
            FROM reconciliation_seal_integrity_exact_name_without_delta valid
            WHERE valid.id = NEW.id
          )
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation cannot seal before exact scoped evidence materialization');
      END;
    `,
  },
  {
    id: 11,
    name: "drop-source-contracts-table",
    sql: `
DROP TRIGGER IF EXISTS source_contract_insert_guard;
DROP TRIGGER IF EXISTS source_contract_update_guard;
DROP TRIGGER IF EXISTS source_contracts_done_no_update;
DROP TRIGGER IF EXISTS source_contracts_done_no_delete;

DROP VIEW IF EXISTS reconciliation_seal_integrity;
DROP VIEW IF EXISTS reconciliation_seal_integrity_exact_name_without_delta;

DROP TRIGGER IF EXISTS person_alias_done_no_insert;
DROP TRIGGER IF EXISTS person_alias_structured_evidence_guard_v2;
DROP TRIGGER IF EXISTS person_aliases_no_delete;
DROP TRIGGER IF EXISTS person_aliases_no_update;
DROP TRIGGER IF EXISTS reconciliation_attempt_causal_guard;
DROP TRIGGER IF EXISTS reconciliation_attempt_payload_guard_v3;
DROP TRIGGER IF EXISTS reconciliation_attempt_run_guard_v2;
DROP TRIGGER IF EXISTS reconciliation_attempts_done_no_insert;
DROP TRIGGER IF EXISTS reconciliation_attempts_no_delete;
DROP TRIGGER IF EXISTS reconciliation_attempts_no_update;
DROP TRIGGER IF EXISTS reservoir_active_run_insert_guard;
DROP TRIGGER IF EXISTS reservoir_capacity_update_guard_v2;
DROP TRIGGER IF EXISTS reservoir_done_no_delete;
DROP TRIGGER IF EXISTS reservoir_done_no_insert;
DROP TRIGGER IF EXISTS reservoir_done_no_update;
DROP TRIGGER IF EXISTS reservoir_identity_no_update;
DROP TRIGGER IF EXISTS reservoir_no_delete;
DROP TRIGGER IF EXISTS reservoir_non_active_no_update;
DROP TRIGGER IF EXISTS reservoir_observation_contract_guard;
DROP TRIGGER IF EXISTS reservoir_observation_update_guard;
DROP TRIGGER IF EXISTS reservoir_selected_at_guard_v2;
DROP TRIGGER IF EXISTS reservoir_source_insert_guard;
DROP TRIGGER IF EXISTS reservoir_status_transition_guard_v2;
DROP TRIGGER IF EXISTS reservoir_total_insert_guard;
DROP TRIGGER IF EXISTS send_attempt_active_run_insert_guard;
DROP TRIGGER IF EXISTS send_attempt_active_run_update_guard;
DROP TRIGGER IF EXISTS send_attempt_capacity_insert_guard;
DROP TRIGGER IF EXISTS send_attempt_capacity_update_guard_v2;
DROP TRIGGER IF EXISTS send_attempt_commit_start_guard;
DROP TRIGGER IF EXISTS send_attempt_durable_effects;
DROP TRIGGER IF EXISTS send_attempt_durable_event_key_guard;
DROP TRIGGER IF EXISTS send_attempt_durable_reconciliation_guard;
DROP TRIGGER IF EXISTS send_attempt_identity_no_update;
DROP TRIGGER IF EXISTS send_attempt_plan_event_key_guard;
DROP TRIGGER IF EXISTS send_attempt_plan_material_no_update;
DROP TRIGGER IF EXISTS send_attempt_plan_receipt_no_update;
DROP TRIGGER IF EXISTS send_attempt_planned_only_insert_guard;
DROP TRIGGER IF EXISTS send_attempt_planned_proven_no_send_effects;
DROP TRIGGER IF EXISTS send_attempt_planned_proven_no_send_event_key_guard;
DROP TRIGGER IF EXISTS send_attempt_possible_effects;
DROP TRIGGER IF EXISTS send_attempt_possible_event_key_guard;
DROP TRIGGER IF EXISTS send_attempt_possible_receipt_mutation_guard_v3;
DROP TRIGGER IF EXISTS send_attempt_proven_no_send_effects;
DROP TRIGGER IF EXISTS send_attempt_proven_no_send_event_key_guard;
DROP TRIGGER IF EXISTS send_attempt_resolution_receipt_mutation_guard_v2;
DROP TRIGGER IF EXISTS send_attempt_select_reservoir_after_insert;
DROP TRIGGER IF EXISTS send_attempt_transition_guard_v3;
DROP TRIGGER IF EXISTS send_attempts_done_no_delete;
DROP TRIGGER IF EXISTS send_attempts_done_no_insert;
DROP TRIGGER IF EXISTS send_attempts_done_no_update;
DROP TRIGGER IF EXISTS send_attempts_no_delete;
DROP TRIGGER IF EXISTS source_observation_receipt_guard_v2;
DROP TRIGGER IF EXISTS source_observations_controller_candidate_no_update;
DROP TRIGGER IF EXISTS source_observations_done_no_insert;
DROP TRIGGER IF EXISTS source_observations_done_no_person_update;
DROP TRIGGER IF EXISTS source_observations_fields_no_update;
DROP TRIGGER IF EXISTS source_observations_no_delete;
DROP TRIGGER IF EXISTS source_observations_person_finalize_guard;
DROP TRIGGER IF EXISTS terminal_observation_scope_guard;

CREATE TABLE source_observations__v11 (
        id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        person_id TEXT REFERENCES people(id),
        observed_name TEXT NOT NULL,
        observation_kind TEXT NOT NULL CHECK(observation_kind IN ('candidate', 'terminal')),
        row_state TEXT CHECK(row_state IN ('connectable', 'pending', 'connected', 'email_required', 'invalid', 'unknown')),
        page_identity TEXT,
        stable_row_ids_json TEXT,
        next_control TEXT CHECK(next_control IS NULL OR next_control IN ('available', 'missing', 'disabled')),
        observed_at TEXT NOT NULL, run_id TEXT REFERENCES daily_runs(id), source_contract_version INTEGER NOT NULL DEFAULT 1, reload_generation INTEGER NOT NULL DEFAULT 0, tick_id TEXT, row_order INTEGER, identity_evidence_json TEXT NOT NULL DEFAULT '{}', causal_sequence INTEGER REFERENCES causal_records(sequence), controller_candidate_json TEXT
        CHECK(controller_candidate_json IS NULL OR json_valid(controller_candidate_json)),
        UNIQUE(invocation_id, source_id, id)
      );
INSERT INTO source_observations__v11 (id, invocation_id, source_id, person_id, observed_name, observation_kind, row_state, page_identity, stable_row_ids_json, next_control, observed_at, run_id, source_contract_version, reload_generation, tick_id, row_order, identity_evidence_json, causal_sequence, controller_candidate_json) SELECT id, invocation_id, source_id, person_id, observed_name, observation_kind, row_state, page_identity, stable_row_ids_json, next_control, observed_at, run_id, source_contract_version, reload_generation, tick_id, row_order, identity_evidence_json, causal_sequence, controller_candidate_json FROM source_observations;

CREATE TABLE reservoir_entries__v11 (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        source_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES people(id),
        observation_id TEXT NOT NULL REFERENCES source_observations__v11(id),
        status TEXT NOT NULL CHECK(status IN ('available', 'selected', 'consumed', 'ineligible')),
        added_at TEXT NOT NULL,
        selected_at TEXT,
        UNIQUE(run_id, person_id)
      );
INSERT INTO reservoir_entries__v11 (id, run_id, source_id, person_id, observation_id, status, added_at, selected_at) SELECT id, run_id, source_id, person_id, observation_id, status, added_at, selected_at FROM reservoir_entries;

CREATE TABLE send_attempts__v11 (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES daily_runs(id),
        person_id TEXT NOT NULL REFERENCES people(id),
        source_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('planned', 'possible', 'durable', 'proven_no_send')),
        attempted_at TEXT,
        resolved_at TEXT,
        evidence TEXT NOT NULL, possible_receipt_key TEXT, resolution_receipt_key TEXT, planned_causal_sequence INTEGER REFERENCES causal_records(sequence), possible_causal_sequence INTEGER REFERENCES causal_records(sequence), resolution_causal_sequence INTEGER REFERENCES causal_records(sequence), reservoir_entry_id TEXT
        REFERENCES reservoir_entries__v11(id) ON UPDATE RESTRICT ON DELETE RESTRICT, plan_evidence_json TEXT
        CHECK(plan_evidence_json IS NULL OR json_valid(plan_evidence_json)), possible_evidence_json TEXT
        CHECK(possible_evidence_json IS NULL OR json_valid(possible_evidence_json)), prepare_receipt_json TEXT
        CHECK(prepare_receipt_json IS NULL OR json_valid(prepare_receipt_json)), prepare_binding_json TEXT
        CHECK(prepare_binding_json IS NULL OR json_valid(prepare_binding_json)), commit_started_at TEXT, commit_receipt_json TEXT
        CHECK(commit_receipt_json IS NULL OR json_valid(commit_receipt_json)), commit_causal_sequence INTEGER
        REFERENCES causal_records(sequence),
        UNIQUE(run_id, person_id)
      );
INSERT INTO send_attempts__v11 (id, run_id, person_id, source_id, state, attempted_at, resolved_at, evidence, possible_receipt_key, resolution_receipt_key, planned_causal_sequence, possible_causal_sequence, resolution_causal_sequence, reservoir_entry_id, plan_evidence_json, possible_evidence_json, prepare_receipt_json, prepare_binding_json, commit_started_at, commit_receipt_json, commit_causal_sequence) SELECT id, run_id, person_id, source_id, state, attempted_at, resolved_at, evidence, possible_receipt_key, resolution_receipt_key, planned_causal_sequence, possible_causal_sequence, resolution_causal_sequence, reservoir_entry_id, plan_evidence_json, possible_evidence_json, prepare_receipt_json, prepare_binding_json, commit_started_at, commit_receipt_json, commit_causal_sequence FROM send_attempts;

CREATE TABLE reconciliation_attempts__v11 (
        reconciliation_id TEXT NOT NULL REFERENCES reconciliations(id),
        attempt_id TEXT NOT NULL REFERENCES send_attempts__v11(id),
        evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('identity', 'name', 'aggregate')), matched_value TEXT,
        PRIMARY KEY(reconciliation_id, attempt_id)
      );
INSERT INTO reconciliation_attempts__v11 (reconciliation_id, attempt_id, evidence_kind, matched_value) SELECT reconciliation_id, attempt_id, evidence_kind, matched_value FROM reconciliation_attempts;

CREATE TABLE person_aliases__v11 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT NOT NULL REFERENCES people(id),
        kind TEXT NOT NULL CHECK(kind IN ('sales_nav_id', 'public_url', 'lead_key')),
        value TEXT NOT NULL,
        evidence TEXT NOT NULL,
        created_at TEXT NOT NULL, anchor_kind TEXT
        CHECK(anchor_kind IS NULL OR anchor_kind IN ('sales_nav_id', 'public_url', 'lead_key')), anchor_value TEXT, evidence_observation_id TEXT
        REFERENCES source_observations__v11(id), evidence_invocation_id TEXT, evidence_source_id TEXT
       ,
        UNIQUE(kind, value)
      );
INSERT INTO person_aliases__v11 (id, person_id, kind, value, evidence, created_at, anchor_kind, anchor_value, evidence_observation_id, evidence_invocation_id, evidence_source_id) SELECT id, person_id, kind, value, evidence, created_at, anchor_kind, anchor_value, evidence_observation_id, evidence_invocation_id, evidence_source_id FROM person_aliases;

DROP TABLE reconciliation_attempts;
DROP TABLE person_aliases;
DROP TABLE send_attempts;
DROP TABLE reservoir_entries;
DROP TABLE source_observations;
DROP TABLE source_contracts;

ALTER TABLE source_observations__v11 RENAME TO source_observations;
ALTER TABLE reservoir_entries__v11 RENAME TO reservoir_entries;
ALTER TABLE send_attempts__v11 RENAME TO send_attempts;
ALTER TABLE reconciliation_attempts__v11 RENAME TO reconciliation_attempts;
ALTER TABLE person_aliases__v11 RENAME TO person_aliases;

CREATE UNIQUE INDEX one_active_attempt_per_person
        ON send_attempts(person_id)
        WHERE state IN ('planned', 'possible');

CREATE VIEW reconciliation_seal_integrity AS
      SELECT rec.id
      FROM reconciliations rec
      JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
      JOIN audit_snapshots audit
        ON audit.id = rec.audit_id
       AND audit.baseline_id = baseline.id
      JOIN causal_records baseline_receipt
        ON baseline_receipt.sequence = baseline.causal_sequence
      JOIN causal_records audit_receipt
        ON audit_receipt.sequence = audit.causal_sequence
      JOIN causal_records rec_receipt
        ON rec_receipt.sequence = rec.causal_sequence
      WHERE rec.sealed = 0
        AND baseline.run_id = rec.run_id
        AND baseline.competing_sender_absent = 1
        AND baseline.attempt_count_at_capture = 0
        AND audit.run_id = rec.run_id
        AND audit.complete = 1
        AND audit.contradictory_evidence = 0
        AND (
          (
            rec.scope = 'final'
            AND rec.competing_sender_absent = 1
            AND audit.competing_sender_absent = 1
          )
          OR
          (
            rec.scope = 'microbatch'
            AND rec.mode = 'exact'
            AND rec.competing_sender_absent =
              CASE WHEN audit.competing_sender_absent = 1 THEN 1 ELSE 0 END
          )
        )
        AND baseline.causal_sequence IS NOT NULL
        AND audit.causal_sequence > baseline.causal_sequence
        AND rec.causal_sequence > audit.causal_sequence
        AND baseline_receipt.kind = 'audit_baseline'
        AND baseline_receipt.receipt_id = baseline.id
        AND json_valid(baseline_receipt.payload_json)
        AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
        AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
        AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
        AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
        AND json_extract(baseline_receipt.payload_json, '$.identities') IS baseline.identities_json
        AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
              IS baseline.competing_sender_absent
        AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
              IS baseline.attempt_count_at_capture
        AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
        AND audit_receipt.kind = 'audit_snapshot'
        AND audit_receipt.receipt_id = audit.id
        AND json_valid(audit_receipt.payload_json)
        AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
        AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
        AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
        AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
        AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
        AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
        AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
        AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
        AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
              IS audit.competing_sender_absent
        AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
              IS audit.contradictory_evidence
        AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        AND rec_receipt.kind = 'reconciliation'
        AND rec_receipt.receipt_id = rec.id
        AND json_valid(rec_receipt.payload_json)
        AND json_type(rec_receipt.payload_json, '$.evidence') = 'array'
        AND json_array_length(json_extract(rec_receipt.payload_json, '$.evidence'))
              = rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
        AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
        AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
        AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
        AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
        AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
        AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
              IS rec.competing_sender_absent
        AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
              IS rec.confirmed_attempt_ids_json
        AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
        AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
        AND json_valid(rec.confirmed_attempt_ids_json)
        AND json_type(rec.confirmed_attempt_ids_json) = 'array'
        AND (
          SELECT COUNT(*) FROM json_each(rec.confirmed_attempt_ids_json)
        ) = (
          SELECT COUNT(DISTINCT confirmed.value)
          FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE typeof(confirmed.value) = 'text' AND length(trim(confirmed.value)) > 0
        )
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
        ) = rec.attempt_count
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          JOIN send_attempts attempt ON attempt.id = child.attempt_id
          WHERE child.reconciliation_id = rec.id
            AND (
              attempt.run_id != rec.run_id
              OR attempt.state NOT IN ('possible', 'durable')
              OR attempt.possible_causal_sequence IS NULL
              OR audit.causal_sequence <= attempt.possible_causal_sequence
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
          WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation_attempts child
            JOIN send_attempts attempt ON attempt.id = child.attempt_id
            WHERE child.reconciliation_id = rec.id
              AND child.attempt_id = confirmed.value
              AND attempt.run_id = rec.run_id
              AND attempt.state = 'possible'
          )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          JOIN send_attempts attempt ON attempt.id = child.attempt_id
          WHERE child.reconciliation_id = rec.id
            AND attempt.state = 'possible'
            AND NOT EXISTS (
              SELECT 1 FROM json_each(rec.confirmed_attempt_ids_json) confirmed
              WHERE confirmed.value = child.attempt_id
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM reconciliation_attempts child
          WHERE child.reconciliation_id = rec.id
            AND NOT EXISTS (
              SELECT 1
              FROM json_each(rec_receipt.payload_json, '$.evidence') item
              WHERE json_extract(item.value, '$.attemptId') IS child.attempt_id
                AND json_extract(item.value, '$.kind') IS child.evidence_kind
                AND (
                  (
                    rec.scope = 'final'
                    AND child.matched_value IS NULL
                    AND json_type(item.value, '$.matchedValue') IS NULL
                  )
                  OR
                  (
                    rec.scope = 'microbatch'
                    AND child.matched_value IS NOT NULL
                    AND json_extract(item.value, '$.matchedValue') IS child.matched_value
                  )
                )
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM json_each(rec_receipt.payload_json, '$.evidence') item
          WHERE NOT EXISTS (
            SELECT 1 FROM reconciliation_attempts child
            WHERE child.reconciliation_id = rec.id
              AND child.attempt_id = json_extract(item.value, '$.attemptId')
              AND child.evidence_kind = json_extract(item.value, '$.kind')
              AND (
                (
                  rec.scope = 'final'
                  AND child.matched_value IS NULL
                  AND json_type(item.value, '$.matchedValue') IS NULL
                )
                OR
                (
                  rec.scope = 'microbatch'
                  AND child.matched_value IS json_extract(item.value, '$.matchedValue')
                )
              )
          )
        )
        AND (
          (
            rec.scope = 'final'
            AND rec.complete = 1
            AND rec.attempt_count = 30
            AND audit.people_count - baseline.people_count = 30
            AND (SELECT COUNT(*) FROM send_attempts
                 WHERE run_id = rec.run_id AND state IN ('possible', 'durable')) = 30
            AND (SELECT COUNT(*) FROM send_attempts
                 WHERE run_id = rec.run_id AND state = 'planned') = 0
            AND NOT EXISTS (
              SELECT 1 FROM send_attempts attempt
              WHERE attempt.run_id = rec.run_id
                AND attempt.state IN ('possible', 'durable')
                AND NOT EXISTS (
                  SELECT 1 FROM reconciliation_attempts child
                  WHERE child.reconciliation_id = rec.id
                    AND child.attempt_id = attempt.id
                )
            )
          )
          OR
          (
            rec.scope = 'microbatch'
            AND rec.complete = 0
            AND rec.mode = 'exact'
            AND rec.attempt_count BETWEEN 1 AND 30
            AND json_array_length(rec.confirmed_attempt_ids_json) > 0
            AND (
              audit.people_count - baseline.people_count >=
                json_array_length(rec.confirmed_attempt_ids_json)
              OR (
                audit.people_count = baseline.people_count
                AND NOT EXISTS (
                  SELECT 1 FROM reconciliation_attempts child
                  WHERE child.reconciliation_id = rec.id
                    AND child.evidence_kind != 'name'
                )
              )
            )
            AND NOT EXISTS (
              SELECT 1 FROM send_attempts attempt
              WHERE attempt.run_id = rec.run_id
                AND attempt.state = 'possible'
                AND (
                  attempt.possible_causal_sequence IS NULL
                  OR audit.causal_sequence <= attempt.possible_causal_sequence
                )
            )
            AND NOT EXISTS (
              SELECT 1
              FROM reconciliation_attempts child
              JOIN send_attempts attempt ON attempt.id = child.attempt_id
              JOIN people person ON person.id = attempt.person_id
              WHERE child.reconciliation_id = rec.id
                AND (
                  child.evidence_kind = 'aggregate'
                  OR child.matched_value IS NULL
                  OR (
                    child.evidence_kind = 'identity'
                    AND (
                      (
                        child.matched_value IS NOT person.sales_nav_id
                        AND child.matched_value IS NOT person.public_url
                        AND child.matched_value IS NOT person.lead_key
                      )
                      OR (
                        SELECT COUNT(*) FROM json_each(audit.identities_json) identity
                        WHERE identity.value = child.matched_value
                      ) != 1
                    )
                  )
                  OR (
                    child.evidence_kind = 'name'
                    AND (
                      child.matched_value != person.name
                      OR (
                        SELECT COUNT(*) FROM json_each(audit.names_json) name
                        WHERE name.value = child.matched_value
                      ) != 1
                      OR (
                        SELECT COUNT(*)
                        FROM send_attempts peer
                        JOIN people peer_person ON peer_person.id = peer.person_id
                        WHERE peer.run_id = rec.run_id
                          AND peer.state IN ('possible', 'durable')
                          AND peer_person.name = child.matched_value
                      ) != 1
                    )
                  )
                )
            )
          )
        );

CREATE VIEW reconciliation_seal_integrity_exact_name_without_delta AS
      SELECT rec.id
      FROM reconciliations rec
      JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
      JOIN audit_snapshots audit
        ON audit.id = rec.audit_id
       AND audit.baseline_id = baseline.id
      JOIN causal_records baseline_receipt
        ON baseline_receipt.sequence = baseline.causal_sequence
      JOIN causal_records audit_receipt
        ON audit_receipt.sequence = audit.causal_sequence
      JOIN causal_records rec_receipt
        ON rec_receipt.sequence = rec.causal_sequence
      JOIN reconciliation_attempts child
        ON child.reconciliation_id = rec.id
      JOIN send_attempts attempt
        ON attempt.id = child.attempt_id
      JOIN people person
        ON person.id = attempt.person_id
      WHERE rec.sealed = 0
        AND rec.scope = 'microbatch'
        AND rec.complete = 0
        AND rec.mode = 'exact'
        AND rec.attempt_count BETWEEN 1 AND 30
        AND rec.competing_sender_absent IN (0, 1)
        AND baseline.run_id = rec.run_id
        AND baseline.competing_sender_absent = 1
        AND baseline.attempt_count_at_capture = 0
        AND audit.run_id = rec.run_id
        AND audit.baseline_id = baseline.id
        AND audit.complete = 1
        AND audit.competing_sender_absent IN (0, 1)
        AND audit.contradictory_evidence = 0
        AND audit.people_count = baseline.people_count
        AND baseline.causal_sequence IS NOT NULL
        AND audit.causal_sequence > baseline.causal_sequence
        AND rec.causal_sequence > audit.causal_sequence
        AND baseline_receipt.kind = 'audit_baseline'
        AND baseline_receipt.receipt_id = baseline.id
        AND json_valid(baseline_receipt.payload_json)
        AND json_extract(baseline_receipt.payload_json, '$.baselineId') IS baseline.id
        AND json_extract(baseline_receipt.payload_json, '$.runId') IS baseline.run_id
        AND json_extract(baseline_receipt.payload_json, '$.invocationId') IS baseline.invocation_id
        AND json_extract(baseline_receipt.payload_json, '$.peopleCount') IS baseline.people_count
        AND json_extract(baseline_receipt.payload_json, '$.identities') IS baseline.identities_json
        AND json_extract(baseline_receipt.payload_json, '$.competingSenderAbsent')
              IS baseline.competing_sender_absent
        AND json_extract(baseline_receipt.payload_json, '$.attemptCountAtCapture')
              IS baseline.attempt_count_at_capture
        AND json_extract(baseline_receipt.payload_json, '$.capturedAt') IS baseline.captured_at
        AND audit_receipt.kind = 'audit_snapshot'
        AND audit_receipt.receipt_id = audit.id
        AND json_valid(audit_receipt.payload_json)
        AND json_extract(audit_receipt.payload_json, '$.auditId') IS audit.id
        AND json_extract(audit_receipt.payload_json, '$.runId') IS audit.run_id
        AND json_extract(audit_receipt.payload_json, '$.invocationId') IS audit.invocation_id
        AND json_extract(audit_receipt.payload_json, '$.baselineId') IS audit.baseline_id
        AND json_extract(audit_receipt.payload_json, '$.peopleCount') IS audit.people_count
        AND json_extract(audit_receipt.payload_json, '$.identities') IS audit.identities_json
        AND json_extract(audit_receipt.payload_json, '$.names') IS audit.names_json
        AND json_extract(audit_receipt.payload_json, '$.complete') IS audit.complete
        AND json_extract(audit_receipt.payload_json, '$.competingSenderAbsent')
              IS audit.competing_sender_absent
        AND json_extract(audit_receipt.payload_json, '$.contradictoryEvidence')
              IS audit.contradictory_evidence
        AND json_extract(audit_receipt.payload_json, '$.capturedAt') IS audit.captured_at
        AND rec_receipt.kind = 'reconciliation'
        AND rec_receipt.receipt_id = rec.id
        AND json_valid(rec_receipt.payload_json)
        AND json_type(rec_receipt.payload_json, '$.evidence') = 'array'
        AND json_array_length(json_extract(rec_receipt.payload_json, '$.evidence'))
              = rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
        AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
        AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
        AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
        AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
        AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
        AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
        AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
              IS rec.competing_sender_absent
        AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
              IS rec.confirmed_attempt_ids_json
        AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
        AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
        AND json_valid(rec.confirmed_attempt_ids_json)
        AND json_type(rec.confirmed_attempt_ids_json) = 'array'
        AND json_array_length(rec.confirmed_attempt_ids_json) = rec.attempt_count
        AND (
          SELECT COUNT(*) FROM reconciliation_attempts child_row
          WHERE child_row.reconciliation_id = rec.id
        ) = rec.attempt_count
        AND NOT EXISTS (
          SELECT 1
          FROM reconciliation_attempts invalid_child
          JOIN send_attempts invalid_attempt ON invalid_attempt.id = invalid_child.attempt_id
          JOIN people invalid_person ON invalid_person.id = invalid_attempt.person_id
          WHERE invalid_child.reconciliation_id = rec.id
            AND (
              invalid_child.evidence_kind != 'name'
              OR invalid_child.matched_value IS NULL
              OR invalid_child.matched_value IS NOT invalid_person.name
              OR (
                SELECT COUNT(*) FROM json_each(audit.names_json) name
                WHERE name.value = invalid_child.matched_value
              ) != 1
              OR (
                SELECT COUNT(*)
                FROM send_attempts peer
                JOIN people peer_person ON peer_person.id = peer.person_id
                WHERE peer.run_id = rec.run_id
                  AND peer.state IN ('possible', 'durable')
                  AND peer_person.name = invalid_child.matched_value
              ) != 1
            )
        )
        AND child.evidence_kind = 'name'
        AND child.matched_value IS person.name
        AND (
          SELECT COUNT(*) FROM json_each(audit.names_json) name
          WHERE name.value = child.matched_value
        ) = 1
        AND (
          SELECT COUNT(*)
          FROM send_attempts peer
          JOIN people peer_person ON peer_person.id = peer.person_id
          WHERE peer.run_id = rec.run_id
            AND peer.state IN ('possible', 'durable')
            AND peer_person.name = child.matched_value
        ) = 1;

CREATE TRIGGER person_alias_done_no_insert
      BEFORE INSERT ON person_aliases
      WHEN EXISTS (
        SELECT 1 FROM send_attempts a
        JOIN daily_runs r ON r.id = a.run_id
        WHERE a.person_id = NEW.person_id AND r.status = 'done'
      )
      BEGIN
        SELECT RAISE(ABORT, 'done run aliases are immutable');
      END;

CREATE TRIGGER person_alias_structured_evidence_guard_v2
      BEFORE INSERT ON person_aliases
      WHEN json_valid(NEW.evidence) != 1
        OR NEW.anchor_kind IS NULL
        OR NEW.anchor_value IS NULL
        OR NEW.evidence_observation_id IS NULL
        OR NEW.evidence_invocation_id IS NULL
        OR NEW.evidence_source_id IS NULL
        OR NEW.anchor_kind = NEW.kind
        OR NEW.anchor_value = NEW.value
        OR NEW.evidence IS NOT json_object(
          'anchorKind', NEW.anchor_kind,
          'anchorValue', NEW.anchor_value,
          'invocationId', NEW.evidence_invocation_id,
          'observationId', NEW.evidence_observation_id,
          'sourceId', NEW.evidence_source_id
        )
        OR NOT EXISTS (
          SELECT 1 FROM people p
          WHERE p.id = NEW.person_id
            AND CASE NEW.anchor_kind
              WHEN 'sales_nav_id' THEN p.sales_nav_id
              WHEN 'public_url' THEN p.public_url
              ELSE p.lead_key
            END = NEW.anchor_value
            AND CASE NEW.kind
              WHEN 'sales_nav_id' THEN p.sales_nav_id
              WHEN 'public_url' THEN p.public_url
              ELSE p.lead_key
            END = NEW.value
        )
        OR NOT EXISTS (
          SELECT 1 FROM source_observations o
          WHERE o.id = NEW.evidence_observation_id
            AND o.invocation_id = NEW.evidence_invocation_id
            AND o.source_id = NEW.evidence_source_id
            AND o.person_id = NEW.person_id
            AND CASE NEW.kind
              WHEN 'sales_nav_id' THEN json_extract(o.identity_evidence_json, '$.salesNavId')
              WHEN 'public_url' THEN json_extract(o.identity_evidence_json, '$.publicUrl')
              ELSE json_extract(o.identity_evidence_json, '$.leadKey')
            END = NEW.value
            AND CASE NEW.anchor_kind
              WHEN 'sales_nav_id' THEN json_extract(o.identity_evidence_json, '$.salesNavId')
              WHEN 'public_url' THEN json_extract(o.identity_evidence_json, '$.publicUrl')
              ELSE json_extract(o.identity_evidence_json, '$.leadKey')
            END = NEW.anchor_value
        )
      BEGIN
        SELECT RAISE(ABORT, 'alias requires canonical exact immutable source evidence');
      END;

CREATE TRIGGER person_aliases_no_delete
      BEFORE DELETE ON person_aliases
      BEGIN
        SELECT RAISE(ABORT, 'person aliases are immutable');
      END;

CREATE TRIGGER person_aliases_no_update
      BEFORE UPDATE ON person_aliases
      BEGIN
        SELECT RAISE(ABORT, 'person aliases are immutable');
      END;

CREATE TRIGGER reconciliation_attempt_causal_guard
      BEFORE INSERT ON reconciliation_attempts
      WHEN NOT EXISTS (
        SELECT 1
        FROM reconciliations r
        JOIN audit_snapshots s ON s.id = r.audit_id
        JOIN send_attempts a ON a.id = NEW.attempt_id
        WHERE r.id = NEW.reconciliation_id
          AND a.possible_causal_sequence IS NOT NULL
          AND s.causal_sequence IS NOT NULL
          AND s.causal_sequence > a.possible_causal_sequence
      )
      BEGIN
        SELECT RAISE(ABORT, 'audit must be causally after every confirmed attempt');
      END;

CREATE TRIGGER reconciliation_attempt_payload_guard_v3
      BEFORE INSERT ON reconciliation_attempts
      WHEN EXISTS (
        SELECT 1 FROM reconciliations rec
        WHERE rec.id = NEW.reconciliation_id AND rec.sealed != 0
      )
      OR NOT EXISTS (
        SELECT 1
        FROM reconciliations rec
        JOIN causal_records receipt ON receipt.sequence = rec.causal_sequence
        JOIN json_each(receipt.payload_json, '$.evidence') item
        WHERE rec.id = NEW.reconciliation_id
          AND rec.sealed = 0
          AND json_extract(item.value, '$.attemptId') IS NEW.attempt_id
          AND json_extract(item.value, '$.kind') IS NEW.evidence_kind
          AND (
            (
              rec.scope = 'final'
              AND NEW.matched_value IS NULL
              AND json_type(item.value, '$.matchedValue') IS NULL
            )
            OR
            (
              rec.scope = 'microbatch'
              AND NEW.matched_value IS NOT NULL
              AND length(NEW.matched_value) > 0
              AND json_extract(item.value, '$.matchedValue') IS NEW.matched_value
            )
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation child must equal its scoped canonical evidence');
      END;

CREATE TRIGGER reconciliation_attempt_run_guard_v2
      BEFORE INSERT ON reconciliation_attempts
      WHEN NOT EXISTS (
        SELECT 1
        FROM reconciliations rec
        JOIN send_attempts attempt ON attempt.id = NEW.attempt_id
        WHERE rec.id = NEW.reconciliation_id
          AND rec.sealed = 0
          AND rec.run_id = attempt.run_id
          AND attempt.state IN ('possible', 'durable')
      )
      BEGIN
        SELECT RAISE(ABORT, 'unsealed reconciliation attempt must be active and belong to the run');
      END;

CREATE TRIGGER reconciliation_attempts_done_no_insert
      BEFORE INSERT ON reconciliation_attempts
      WHEN EXISTS (
        SELECT 1 FROM reconciliations rec
        JOIN daily_runs r ON r.id = rec.run_id
        WHERE rec.id = NEW.reconciliation_id AND r.status = 'done'
      )
      BEGIN
        SELECT RAISE(ABORT, 'done run reconciliation evidence is immutable');
      END;

CREATE TRIGGER reconciliation_attempts_no_delete
      BEFORE DELETE ON reconciliation_attempts
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation attempts are immutable');
      END;

CREATE TRIGGER reconciliation_attempts_no_update
      BEFORE UPDATE ON reconciliation_attempts
      BEGIN
        SELECT RAISE(ABORT, 'reconciliation attempts are immutable');
      END;

CREATE TRIGGER reservoir_active_run_insert_guard
      BEFORE INSERT ON reservoir_entries
      WHEN NOT EXISTS (
        SELECT 1 FROM daily_runs run
        WHERE run.id = NEW.run_id AND run.status = 'active'
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir rows require an active run');
      END;

CREATE TRIGGER reservoir_capacity_update_guard_v2
      BEFORE UPDATE OF run_id, source_id, status ON reservoir_entries
      WHEN NEW.status IN ('available', 'selected')
       AND (
         (SELECT COUNT(*) FROM reservoir_entries
          WHERE run_id = NEW.run_id
            AND id != OLD.id
            AND status IN ('available', 'selected')) >= 60
         OR
         (SELECT COUNT(*) FROM reservoir_entries
          WHERE run_id = NEW.run_id
            AND source_id = NEW.source_id
            AND id != OLD.id
            AND status IN ('available', 'selected')) >= 30
       )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir update exceeds run or source capacity');
      END;

CREATE TRIGGER reservoir_done_no_delete
      BEFORE DELETE ON reservoir_entries
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run reservoir is immutable');
      END;

CREATE TRIGGER reservoir_done_no_insert
      BEFORE INSERT ON reservoir_entries
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run reservoir is immutable');
      END;

CREATE TRIGGER reservoir_done_no_update
      BEFORE UPDATE ON reservoir_entries
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run reservoir is immutable');
      END;

CREATE TRIGGER reservoir_identity_no_update
      BEFORE UPDATE OF id, run_id, source_id, person_id, observation_id, added_at
      ON reservoir_entries
      BEGIN
        SELECT RAISE(ABORT, 'reservoir identity and source evidence are immutable');
      END;

CREATE TRIGGER reservoir_no_delete
      BEFORE DELETE ON reservoir_entries
      BEGIN
        SELECT RAISE(ABORT, 'reservoir evidence is append-only');
      END;

CREATE TRIGGER reservoir_non_active_no_update
      BEFORE UPDATE ON reservoir_entries
      WHEN EXISTS (
        SELECT 1 FROM daily_runs run
        WHERE run.id = OLD.run_id AND run.status != 'active'
      )
      BEGIN
        SELECT RAISE(ABORT, 'non-active run reservoir evidence is immutable');
      END;

CREATE TRIGGER reservoir_observation_contract_guard
      BEFORE INSERT ON reservoir_entries
      WHEN NOT EXISTS (
        SELECT 1 FROM source_observations o
        WHERE o.id = NEW.observation_id
          AND o.run_id = NEW.run_id
          AND o.source_id = NEW.source_id
          AND o.person_id = NEW.person_id
          AND o.row_state = 'connectable'
          AND o.source_contract_version = (
            SELECT source_contract_version FROM daily_runs WHERE id = NEW.run_id
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir entry must match a connectable run observation');
      END;

CREATE TRIGGER reservoir_observation_update_guard
      BEFORE UPDATE OF run_id, source_id, person_id, observation_id, status ON reservoir_entries
      WHEN NOT EXISTS (
        SELECT 1 FROM source_observations o
        WHERE o.id = NEW.observation_id
          AND o.run_id = NEW.run_id
          AND o.source_id = NEW.source_id
          AND o.person_id = NEW.person_id
          AND o.row_state = 'connectable'
          AND o.source_contract_version = (
            SELECT source_contract_version FROM daily_runs WHERE id = NEW.run_id
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir update must preserve its observation contract');
      END;

CREATE TRIGGER reservoir_selected_at_guard_v2
      BEFORE UPDATE OF selected_at ON reservoir_entries
      WHEN NOT (
        OLD.status = 'available'
        AND NEW.status = 'selected'
        AND OLD.selected_at IS NULL
        AND NEW.selected_at IS NOT NULL
        AND EXISTS (
          SELECT 1
          FROM send_attempts attempt
          JOIN causal_records plan ON plan.sequence = attempt.planned_causal_sequence
          WHERE attempt.reservoir_entry_id = OLD.id
            AND attempt.run_id = OLD.run_id
            AND attempt.person_id = OLD.person_id
            AND attempt.source_id = OLD.source_id
            AND attempt.state = 'planned'
            AND plan.kind = 'attempt_plan'
            AND plan.receipt_id = attempt.id
            AND json_type(plan.payload_json, '$.plannedAt') = 'text'
            AND length(trim(json_extract(plan.payload_json, '$.plannedAt'))) > 0
            AND json_extract(plan.payload_json, '$.plannedAt') IS NEW.selected_at
            AND json_extract(plan.payload_json, '$.reservoirEntryId') IS OLD.id
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'reservoir selection requires its non-null canonical plan time');
      END;

CREATE TRIGGER reservoir_source_insert_guard
      BEFORE INSERT ON reservoir_entries
      WHEN (
        SELECT COUNT(*) FROM reservoir_entries
        WHERE run_id = NEW.run_id
          AND source_id = NEW.source_id
          AND status IN ('available', 'selected')
      ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'source reservoir capacity exceeded');
      END;

CREATE TRIGGER reservoir_status_transition_guard_v2
      BEFORE UPDATE OF status ON reservoir_entries
      WHEN NEW.status != OLD.status
       AND NOT (
         (
           OLD.status = 'available'
           AND NEW.status = 'selected'
           AND NEW.selected_at IS NOT NULL
           AND EXISTS (
             SELECT 1
             FROM send_attempts attempt
             JOIN causal_records plan ON plan.sequence = attempt.planned_causal_sequence
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.run_id = OLD.run_id
               AND attempt.person_id = OLD.person_id
               AND attempt.source_id = OLD.source_id
               AND attempt.state = 'planned'
               AND plan.kind = 'attempt_plan'
               AND plan.receipt_id = attempt.id
               AND json_type(plan.payload_json, '$.plannedAt') = 'text'
               AND length(trim(json_extract(plan.payload_json, '$.plannedAt'))) > 0
               AND json_extract(plan.payload_json, '$.plannedAt') IS NEW.selected_at
               AND json_extract(plan.payload_json, '$.reservoirEntryId') IS OLD.id
           )
         )
         OR (OLD.status = 'available' AND NEW.status = 'ineligible')
         OR
         (
           OLD.status = 'selected'
           AND NEW.status = 'ineligible'
           AND EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.state = 'proven_no_send'
           )
         )
         OR
         (
           OLD.status = 'selected'
           AND NEW.status = 'consumed'
           AND EXISTS (
             SELECT 1 FROM send_attempts attempt
             WHERE attempt.reservoir_entry_id = OLD.id
               AND attempt.state = 'durable'
           )
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid reservoir status transition');
      END;

CREATE TRIGGER reservoir_total_insert_guard
      BEFORE INSERT ON reservoir_entries
      WHEN (
        SELECT COUNT(*) FROM reservoir_entries
        WHERE run_id = NEW.run_id
          AND status IN ('available', 'selected')
      ) >= 60
      BEGIN
        SELECT RAISE(ABORT, 'run reservoir capacity exceeded');
      END;

CREATE TRIGGER send_attempt_active_run_insert_guard
      BEFORE INSERT ON send_attempts
      WHEN NOT EXISTS (
        SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'active'
      )
      BEGIN
        SELECT RAISE(ABORT, 'send attempts require an active run');
      END;

CREATE TRIGGER send_attempt_active_run_update_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state != OLD.state
       AND NOT EXISTS (
         SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'active'
       )
      BEGIN
        SELECT RAISE(ABORT, 'send attempt transitions require an active run');
      END;

CREATE TRIGGER send_attempt_capacity_insert_guard
      BEFORE INSERT ON send_attempts
      WHEN NEW.state IN ('planned', 'possible', 'durable')
       AND (
         SELECT COUNT(*) FROM send_attempts
         WHERE run_id = NEW.run_id
           AND state IN ('planned', 'possible', 'durable')
       ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'daily send capacity exceeded');
      END;

CREATE TRIGGER send_attempt_capacity_update_guard_v2
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state IN ('planned', 'possible', 'durable')
       AND (
         SELECT COUNT(*) FROM send_attempts
         WHERE run_id = NEW.run_id
           AND id != OLD.id
           AND state IN ('planned', 'possible', 'durable')
       ) >= 30
      BEGIN
        SELECT RAISE(ABORT, 'daily send capacity exceeded');
      END;

CREATE TRIGGER send_attempt_commit_start_guard
      BEFORE UPDATE OF commit_started_at, commit_receipt_json, commit_causal_sequence
      ON send_attempts
      WHEN NOT (
        OLD.state = 'possible'
        AND NEW.state = 'possible'
        AND OLD.commit_started_at IS NULL
        AND OLD.commit_receipt_json IS NULL
        AND OLD.commit_causal_sequence IS NULL
        AND NEW.commit_started_at IS NOT NULL
        AND NEW.commit_receipt_json IS OLD.prepare_receipt_json
        AND NEW.commit_causal_sequence IS NOT NULL
        AND EXISTS (
          SELECT 1 FROM causal_records c
          WHERE c.sequence = NEW.commit_causal_sequence
            AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
            AND c.sequence > NEW.possible_causal_sequence
            AND c.kind = 'attempt_commit_started'
            AND c.receipt_id = json_extract(NEW.commit_receipt_json, '$.receiptId')
            AND json_valid(c.payload_json)
            AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
            AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
            AND json_extract(c.payload_json, '$.startedAt') IS NEW.commit_started_at
            AND json_extract(c.payload_json, '$.commitReceiptJson') IS NEW.commit_receipt_json
            AND json_extract(c.payload_json, '$.prepareBindingJson') IS NEW.prepare_binding_json
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'commit start requires the exact persisted preparation receipt');
      END;

CREATE TRIGGER send_attempt_durable_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
      BEGIN
        INSERT INTO relationship_facts
          (id, person_id, kind, effective_at, run_id, evidence)
        VALUES (
          'pending:' || NEW.id, NEW.person_id, 'pending', NEW.resolved_at, NEW.run_id, NEW.evidence
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM relationship_facts
          WHERE id = 'pending:' || NEW.id
            AND person_id = NEW.person_id
            AND kind = 'pending'
            AND effective_at = NEW.resolved_at
            AND run_id = NEW.run_id
            AND evidence = NEW.evidence
        ) THEN RAISE(ABORT, 'durable transition lacks its exact relationship fact') END;

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':durable', NEW.run_id, 'send_durable',
          json_object('attemptId', NEW.id), NEW.resolved_at,
          'attempt:' || NEW.id || ':durable'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':durable'
            AND run_id = NEW.run_id
            AND type = 'send_durable'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.resolved_at
        ) THEN RAISE(ABORT, 'durable transition lacks its exact event') END;
      END;

CREATE TRIGGER send_attempt_durable_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':durable'
            OR dedupe_key = 'attempt:' || NEW.id || ':durable'
       )
      BEGIN
        SELECT RAISE(ABORT, 'durable event key exists before its parent transition');
      END;

CREATE TRIGGER send_attempt_durable_reconciliation_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'durable'
       AND NOT EXISTS (
         SELECT 1
         FROM reconciliations rec
         JOIN reconciliation_attempts child
           ON child.reconciliation_id = rec.id AND child.attempt_id = NEW.id
         JOIN audit_snapshots audit ON audit.id = rec.audit_id
         JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
         JOIN causal_records rec_receipt ON rec_receipt.sequence = rec.causal_sequence
         JOIN json_each(rec.confirmed_attempt_ids_json) confirmed
           ON confirmed.value = NEW.id
         WHERE rec.id = json_extract(NEW.evidence, '$.reconciliationId')
           AND rec.run_id = NEW.run_id
           AND rec.audit_id = json_extract(NEW.evidence, '$.auditId')
           AND rec.baseline_id = json_extract(NEW.evidence, '$.baselineId')
           AND rec.scope = json_extract(NEW.evidence, '$.reconciliationScope')
           AND child.evidence_kind = json_extract(NEW.evidence, '$.evidenceKind')
           AND NEW.evidence = json_object(
             'auditId', rec.audit_id,
             'baselineId', rec.baseline_id,
             'evidenceKind', child.evidence_kind,
             'reconciliationId', rec.id,
             'reconciliationScope', rec.scope
           )
           AND rec.sealed = 1
           AND (
             (
               rec.scope = 'final'
               AND rec.competing_sender_absent = 1
               AND audit.competing_sender_absent = 1
             )
             OR
             (
               rec.scope = 'microbatch'
               AND rec.mode = 'exact'
               AND rec.competing_sender_absent =
                 CASE WHEN audit.competing_sender_absent = 1 THEN 1 ELSE 0 END
               AND audit.competing_sender_absent IN (0, 1)
             )
           )
           AND audit.run_id = rec.run_id
           AND audit.baseline_id = baseline.id
           AND audit.complete = 1
           AND audit.contradictory_evidence = 0
           AND baseline.run_id = rec.run_id
           AND baseline.competing_sender_absent = 1
           AND baseline.attempt_count_at_capture = 0
           AND NEW.possible_causal_sequence IS NOT NULL
           AND audit.causal_sequence > NEW.possible_causal_sequence
           AND rec.causal_sequence > audit.causal_sequence
           AND NEW.resolution_causal_sequence > rec.causal_sequence
           AND rec_receipt.kind = 'reconciliation'
           AND rec_receipt.receipt_id = rec.id
           AND json_valid(rec_receipt.payload_json)
           AND json_extract(rec_receipt.payload_json, '$.scope') IS rec.scope
           AND json_extract(rec_receipt.payload_json, '$.reconciliationId') IS rec.id
           AND json_extract(rec_receipt.payload_json, '$.runId') IS rec.run_id
           AND json_extract(rec_receipt.payload_json, '$.baselineId') IS rec.baseline_id
           AND json_extract(rec_receipt.payload_json, '$.auditId') IS rec.audit_id
           AND json_extract(rec_receipt.payload_json, '$.mode') IS rec.mode
           AND json_extract(rec_receipt.payload_json, '$.attemptCount') IS rec.attempt_count
           AND json_extract(rec_receipt.payload_json, '$.finalComplete') IS rec.complete
           AND json_extract(rec_receipt.payload_json, '$.competingSenderAbsent')
                 IS rec.competing_sender_absent
           AND json_extract(rec_receipt.payload_json, '$.newlyConfirmedAttemptIds')
                 IS rec.confirmed_attempt_ids_json
           AND json_extract(rec_receipt.payload_json, '$.reconciledAt') IS rec.created_at
           AND (
             (
               rec.scope = 'final'
               AND rec.complete = 1
               AND rec.attempt_count = 30
             )
             OR
             (
               rec.scope = 'microbatch'
               AND rec.complete = 0
               AND rec.mode = 'exact'
               AND child.evidence_kind IN ('identity', 'name')
               AND child.matched_value IS NOT NULL
               AND (
                 (
                   child.evidence_kind = 'identity'
                   AND EXISTS (
                     SELECT 1 FROM people person
                     WHERE person.id = NEW.person_id
                       AND child.matched_value IN (
                         person.sales_nav_id, person.public_url, person.lead_key
                       )
                   )
                   AND (
                     SELECT COUNT(*) FROM json_each(audit.identities_json) identity
                     WHERE identity.value = child.matched_value
                   ) = 1
                 )
                 OR
                 (
                   child.evidence_kind = 'name'
                   AND child.matched_value = (SELECT name FROM people WHERE id = NEW.person_id)
                   AND (
                     SELECT COUNT(*) FROM json_each(audit.names_json) name
                     WHERE name.value = child.matched_value
                   ) = 1
                 )
               )
             )
           )
       )
      BEGIN
        SELECT RAISE(ABORT, 'durable transition requires an exact sealed scoped reconciliation');
      END;

CREATE TRIGGER send_attempt_identity_no_update
      BEFORE UPDATE OF id, run_id, person_id, source_id ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'send attempt identity is immutable');
      END;

CREATE TRIGGER send_attempt_plan_event_key_guard
      BEFORE INSERT ON send_attempts
      WHEN EXISTS (
        SELECT 1 FROM events
        WHERE id = 'event:attempt:' || NEW.id || ':planned'
           OR dedupe_key = 'attempt:' || NEW.id || ':planned'
      )
      BEGIN
        SELECT RAISE(ABORT, 'planned event key exists before its parent transition');
      END;

CREATE TRIGGER send_attempt_plan_material_no_update
      BEFORE UPDATE OF reservoir_entry_id, plan_evidence_json ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'planned reservoir evidence is immutable');
      END;

CREATE TRIGGER send_attempt_plan_receipt_no_update
      BEFORE UPDATE OF planned_causal_sequence ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'planned attempt receipt is immutable');
      END;

CREATE TRIGGER send_attempt_planned_only_insert_guard
      BEFORE INSERT ON send_attempts
      WHEN NEW.state != 'planned'
        OR NEW.reservoir_entry_id IS NULL
        OR NEW.plan_evidence_json IS NULL
        OR NEW.planned_causal_sequence IS NULL
        OR NEW.evidence IS NOT NEW.plan_evidence_json
        OR NEW.attempted_at IS NOT NULL
        OR NEW.possible_receipt_key IS NOT NULL
        OR NEW.possible_causal_sequence IS NOT NULL
        OR NEW.possible_evidence_json IS NOT NULL
        OR NEW.prepare_receipt_json IS NOT NULL
        OR NEW.prepare_binding_json IS NOT NULL
        OR NEW.resolved_at IS NOT NULL
        OR NEW.resolution_receipt_key IS NOT NULL
        OR NEW.resolution_causal_sequence IS NOT NULL
        OR NEW.commit_started_at IS NOT NULL
        OR NEW.commit_receipt_json IS NOT NULL
        OR NEW.commit_causal_sequence IS NOT NULL
        OR NEW.planned_causal_sequence != (SELECT MAX(sequence) FROM causal_records)
        OR NOT EXISTS (
          SELECT 1
          FROM causal_records plan
          JOIN reservoir_entries reservoir ON reservoir.id = NEW.reservoir_entry_id
          JOIN source_observations observation ON observation.id = reservoir.observation_id
          WHERE plan.sequence = NEW.planned_causal_sequence
            AND plan.kind = 'attempt_plan'
            AND plan.receipt_id = NEW.id
            AND json_valid(plan.payload_json)
            AND json_type(plan.payload_json, '$.plannedAt') = 'text'
            AND length(trim(json_extract(plan.payload_json, '$.plannedAt'))) > 0
            AND json_extract(plan.payload_json, '$.attemptId') IS NEW.id
            AND json_extract(plan.payload_json, '$.runId') IS NEW.run_id
            AND json_extract(plan.payload_json, '$.personId') IS NEW.person_id
            AND json_extract(plan.payload_json, '$.sourceId') IS NEW.source_id
            AND json_extract(plan.payload_json, '$.reservoirEntryId') IS NEW.reservoir_entry_id
            AND json_extract(plan.payload_json, '$.evidenceJson') IS NEW.plan_evidence_json
            AND reservoir.run_id = NEW.run_id
            AND reservoir.person_id = NEW.person_id
            AND reservoir.source_id = NEW.source_id
            AND reservoir.status = 'available'
            AND reservoir.selected_at IS NULL
            AND observation.controller_candidate_json IS NOT NULL
        )
      BEGIN
        SELECT RAISE(ABORT, 'send attempts must be inserted as planned with an exact plan receipt');
      END;

CREATE TRIGGER send_attempt_planned_proven_no_send_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'proven_no_send'
      BEGIN
        INSERT INTO relationship_facts
          (id, person_id, kind, effective_at, run_id, evidence)
        VALUES (
          'proven-no-send:' || NEW.id, NEW.person_id, 'proven_no_send',
          NEW.resolved_at, NEW.run_id, NEW.evidence
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM relationship_facts
          WHERE id = 'proven-no-send:' || NEW.id
            AND person_id = NEW.person_id
            AND kind = 'proven_no_send'
            AND effective_at = NEW.resolved_at
            AND run_id = NEW.run_id
            AND evidence = NEW.evidence
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact relationship fact') END;

        UPDATE reservoir_entries SET status = 'ineligible'
        WHERE id = NEW.reservoir_entry_id AND status = 'selected';

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':proven_no_send',
          NEW.run_id, 'send_proven_no_send', json_object('attemptId', NEW.id),
          NEW.resolved_at, 'attempt:' || NEW.id || ':proven_no_send'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
            AND run_id = NEW.run_id
            AND type = 'send_proven_no_send'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.resolved_at
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact event') END;
      END;

CREATE TRIGGER send_attempt_planned_proven_no_send_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'proven_no_send'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':proven_no_send'
            OR dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
       )
      BEGIN
        SELECT RAISE(ABORT, 'proven-no-send event key exists before its parent transition');
      END;

CREATE TRIGGER send_attempt_possible_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'possible'
      BEGIN
        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':possible', NEW.run_id, 'send_possible',
          json_object('attemptId', NEW.id), NEW.attempted_at,
          'attempt:' || NEW.id || ':possible'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':possible'
            AND run_id = NEW.run_id
            AND type = 'send_possible'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.attempted_at
        ) THEN RAISE(ABORT, 'possible transition lacks its exact event') END;
      END;

CREATE TRIGGER send_attempt_possible_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'planned' AND NEW.state = 'possible'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':possible'
            OR dedupe_key = 'attempt:' || NEW.id || ':possible'
       )
      BEGIN
        SELECT RAISE(ABORT, 'possible event key exists before its parent transition');
      END;

CREATE TRIGGER send_attempt_possible_receipt_mutation_guard_v3
      BEFORE UPDATE OF attempted_at, possible_receipt_key, possible_causal_sequence,
        possible_evidence_json, prepare_receipt_json, prepare_binding_json
      ON send_attempts
      WHEN NOT (
        (
          OLD.state = 'planned'
          AND NEW.state = 'possible'
          AND OLD.possible_causal_sequence IS NULL
          AND NEW.possible_causal_sequence IS NOT NULL
        )
        OR
        (
          OLD.state = 'possible'
          AND NEW.state = 'possible'
          AND OLD.commit_started_at IS NULL
          AND NEW.commit_started_at IS NULL
          AND OLD.possible_causal_sequence IS NOT NULL
          AND NEW.possible_causal_sequence IS NOT NULL
          AND NEW.possible_causal_sequence = (SELECT MAX(sequence) FROM causal_records)
          AND NEW.possible_evidence_json IS NOT NULL
          AND NEW.evidence IS NEW.possible_evidence_json
          AND NEW.prepare_receipt_json IS NOT NULL
          AND NEW.prepare_binding_json IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM causal_records c
            WHERE c.sequence = NEW.possible_causal_sequence
              AND c.kind = 'attempt_possible'
              AND c.receipt_id = NEW.possible_receipt_key
              AND json_valid(c.payload_json)
              AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
              AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
              AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
              AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
              AND json_extract(c.payload_json, '$.receiptId') IS NEW.possible_receipt_key
              AND json_extract(c.payload_json, '$.attemptedAt') IS NEW.attempted_at
              AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.possible_evidence_json
              AND json_extract(c.payload_json, '$.prepareReceiptJson') IS NEW.prepare_receipt_json
              AND json_extract(c.payload_json, '$.prepareBindingJson') IS NEW.prepare_binding_json
          )
        )
      )
      BEGIN
        SELECT RAISE(ABORT, 'possible receipt fields may only change on planned transition or pre-commit refresh');
      END;

CREATE TRIGGER send_attempt_proven_no_send_effects
      AFTER UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'proven_no_send'
      BEGIN
        INSERT INTO relationship_facts
          (id, person_id, kind, effective_at, run_id, evidence)
        VALUES (
          'proven-no-send:' || NEW.id, NEW.person_id, 'proven_no_send',
          NEW.resolved_at, NEW.run_id, NEW.evidence
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM relationship_facts
          WHERE id = 'proven-no-send:' || NEW.id
            AND person_id = NEW.person_id
            AND kind = 'proven_no_send'
            AND effective_at = NEW.resolved_at
            AND run_id = NEW.run_id
            AND evidence = NEW.evidence
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact relationship fact') END;

        UPDATE reservoir_entries SET status = 'ineligible'
        WHERE id = NEW.reservoir_entry_id AND status = 'selected';

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':proven_no_send',
          NEW.run_id, 'send_proven_no_send', json_object('attemptId', NEW.id),
          NEW.resolved_at, 'attempt:' || NEW.id || ':proven_no_send'
        );
        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM events
          WHERE dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
            AND run_id = NEW.run_id
            AND type = 'send_proven_no_send'
            AND payload_json = json_object('attemptId', NEW.id)
            AND occurred_at = NEW.resolved_at
        ) THEN RAISE(ABORT, 'proven-no-send transition lacks its exact event') END;
      END;

CREATE TRIGGER send_attempt_proven_no_send_event_key_guard
      BEFORE UPDATE OF state ON send_attempts
      WHEN OLD.state = 'possible' AND NEW.state = 'proven_no_send'
       AND EXISTS (
         SELECT 1 FROM events
         WHERE id = 'event:attempt:' || NEW.id || ':proven_no_send'
            OR dedupe_key = 'attempt:' || NEW.id || ':proven_no_send'
       )
      BEGIN
        SELECT RAISE(ABORT, 'proven-no-send event key exists before its parent transition');
      END;

CREATE TRIGGER send_attempt_resolution_receipt_mutation_guard_v2
      BEFORE UPDATE OF resolved_at, resolution_receipt_key, resolution_causal_sequence
      ON send_attempts
      WHEN NOT (
        (
          (
            OLD.state = 'possible'
            AND NEW.state IN ('durable', 'proven_no_send')
          )
          OR (
            OLD.state = 'planned'
            AND NEW.state = 'proven_no_send'
          )
        )
        AND OLD.resolution_causal_sequence IS NULL
        AND NEW.resolution_causal_sequence IS NOT NULL
      )
      BEGIN
        SELECT RAISE(ABORT, 'resolution receipt fields may only be set during resolution');
      END;

CREATE TRIGGER send_attempt_select_reservoir_after_insert
      AFTER INSERT ON send_attempts
      BEGIN
        UPDATE reservoir_entries
        SET status = 'selected',
            selected_at = (
              SELECT json_extract(payload_json, '$.plannedAt')
              FROM causal_records WHERE sequence = NEW.planned_causal_sequence
            )
        WHERE id = NEW.reservoir_entry_id AND status = 'available';

        SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM reservoir_entries
          WHERE id = NEW.reservoir_entry_id
            AND status = 'selected'
            AND selected_at IS (
              SELECT json_extract(payload_json, '$.plannedAt')
              FROM causal_records WHERE sequence = NEW.planned_causal_sequence
            )
        ) THEN RAISE(ABORT, 'planned attempt did not atomically select its reservoir row') END;

        INSERT INTO events
          (id, run_id, type, payload_json, occurred_at, dedupe_key)
        VALUES (
          'event:attempt:' || NEW.id || ':planned',
          NEW.run_id,
          'send_planned',
          json_object('attemptId', NEW.id, 'personId', NEW.person_id, 'sourceId', NEW.source_id),
          (SELECT json_extract(payload_json, '$.plannedAt')
           FROM causal_records WHERE sequence = NEW.planned_causal_sequence),
          'attempt:' || NEW.id || ':planned'
        );
      END;

CREATE TRIGGER send_attempt_transition_guard_v3
      BEFORE UPDATE OF state ON send_attempts
      WHEN NEW.state != OLD.state
       AND NOT (
         OLD.state = 'planned'
         AND NEW.state = 'possible'
         AND NEW.possible_evidence_json IS NOT NULL
         AND NEW.evidence IS NEW.possible_evidence_json
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.possible_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.planned_causal_sequence
             AND c.kind = 'attempt_possible'
             AND c.receipt_id = NEW.possible_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.possible_receipt_key
             AND json_extract(c.payload_json, '$.attemptedAt') IS NEW.attempted_at
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.possible_evidence_json
             AND json_extract(c.payload_json, '$.prepareReceiptJson') IS NEW.prepare_receipt_json
             AND json_extract(c.payload_json, '$.prepareBindingJson') IS NEW.prepare_binding_json
         )
       )
       AND NOT (
         OLD.state = 'possible'
         AND NEW.state IN ('durable', 'proven_no_send')
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.resolution_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.possible_causal_sequence
             AND c.kind = CASE NEW.state
               WHEN 'durable' THEN 'attempt_durable'
               ELSE 'attempt_proven_no_send'
             END
             AND c.receipt_id = NEW.resolution_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.resolution_receipt_key
             AND json_extract(c.payload_json, '$.resolvedAt') IS NEW.resolved_at
             AND json_extract(c.payload_json, '$.state') IS NEW.state
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.evidence
         )
       )
       AND NOT (
         OLD.state = 'planned'
         AND NEW.state = 'proven_no_send'
         AND EXISTS (
           SELECT 1 FROM causal_records c
           WHERE c.sequence = NEW.resolution_causal_sequence
             AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
             AND c.sequence > NEW.planned_causal_sequence
             AND c.kind = 'attempt_proven_no_send'
             AND c.receipt_id = NEW.resolution_receipt_key
             AND json_valid(c.payload_json)
             AND json_extract(c.payload_json, '$.attemptId') IS NEW.id
             AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
             AND json_extract(c.payload_json, '$.personId') IS NEW.person_id
             AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
             AND json_extract(c.payload_json, '$.receiptId') IS NEW.resolution_receipt_key
             AND json_extract(c.payload_json, '$.resolvedAt') IS NEW.resolved_at
             AND json_extract(c.payload_json, '$.state') IS NEW.state
             AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.evidence
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'invalid send attempt transition receipt');
      END;

CREATE TRIGGER send_attempts_done_no_delete
      BEFORE DELETE ON send_attempts
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run attempts are immutable');
      END;

CREATE TRIGGER send_attempts_done_no_insert
      BEFORE INSERT ON send_attempts
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run attempts are immutable');
      END;

CREATE TRIGGER send_attempts_done_no_update
      BEFORE UPDATE ON send_attempts
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run attempts are immutable');
      END;

CREATE TRIGGER send_attempts_no_delete
      BEFORE DELETE ON send_attempts
      BEGIN
        SELECT RAISE(ABORT, 'send attempts are immutable records');
      END;

CREATE TRIGGER source_observation_receipt_guard_v2
      BEFORE INSERT ON source_observations
      WHEN NOT EXISTS (
        SELECT 1 FROM causal_records c
        WHERE c.sequence = NEW.causal_sequence
          AND c.sequence = (SELECT MAX(sequence) FROM causal_records)
          AND c.kind = CASE NEW.observation_kind
            WHEN 'candidate' THEN 'candidate_observation'
            ELSE 'terminal_observation'
          END
          AND c.receipt_id = NEW.id
          AND json_valid(c.payload_json)
          AND json_extract(c.payload_json, '$.observationId') IS NEW.id
          AND json_extract(c.payload_json, '$.invocationId') IS NEW.invocation_id
          AND json_extract(c.payload_json, '$.runId') IS NEW.run_id
          AND json_extract(c.payload_json, '$.sourceId') IS NEW.source_id
          AND json_extract(c.payload_json, '$.observedAt') IS NEW.observed_at
          AND (
            (
              NEW.observation_kind = 'candidate'
              AND json_extract(c.payload_json, '$.name') IS NEW.observed_name
              AND json_extract(c.payload_json, '$.rowOrder') IS NEW.row_order
              AND json_extract(c.payload_json, '$.rowState') IS NEW.row_state
              AND json_extract(c.payload_json, '$.evidenceJson') IS NEW.identity_evidence_json
              AND json_extract(c.payload_json, '$.candidateJson') IS NEW.controller_candidate_json
            )
            OR
            (
              NEW.observation_kind = 'terminal'
              AND json_extract(c.payload_json, '$.pageIdentity') IS NEW.page_identity
              AND json_extract(c.payload_json, '$.stableRowIdsJson') IS NEW.stable_row_ids_json
              AND json_extract(c.payload_json, '$.nextControl') IS NEW.next_control
              AND json_extract(c.payload_json, '$.reloadGeneration') IS NEW.reload_generation
              AND json_extract(c.payload_json, '$.tickId') IS NEW.tick_id
              AND json_extract(c.payload_json, '$.sourceContractVersion')
                    IS NEW.source_contract_version
            )
          )
      )
      BEGIN
        SELECT RAISE(ABORT, 'source observation fields must equal its immutable receipt');
      END;

CREATE TRIGGER source_observations_controller_candidate_no_update
      BEFORE UPDATE OF controller_candidate_json ON source_observations
      BEGIN
        SELECT RAISE(ABORT, 'source observation controller candidate is immutable');
      END;

CREATE TRIGGER source_observations_done_no_insert
      BEFORE INSERT ON source_observations
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = NEW.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run observations are immutable');
      END;

CREATE TRIGGER source_observations_done_no_person_update
      BEFORE UPDATE OF person_id ON source_observations
      WHEN EXISTS (SELECT 1 FROM daily_runs WHERE id = OLD.run_id AND status = 'done')
      BEGIN
        SELECT RAISE(ABORT, 'done run observations are immutable');
      END;

CREATE TRIGGER source_observations_fields_no_update
      BEFORE UPDATE OF id, invocation_id, run_id, source_id, source_contract_version,
        observed_name, observation_kind, row_state, page_identity, stable_row_ids_json,
        next_control, observed_at, reload_generation, tick_id, row_order,
        identity_evidence_json, causal_sequence
      ON source_observations
      BEGIN
        SELECT RAISE(ABORT, 'source observation evidence is immutable');
      END;

CREATE TRIGGER source_observations_no_delete
      BEFORE DELETE ON source_observations
      BEGIN
        SELECT RAISE(ABORT, 'source observations are immutable');
      END;

CREATE TRIGGER source_observations_person_finalize_guard
      BEFORE UPDATE OF person_id ON source_observations
      WHEN OLD.person_id IS NOT NULL OR NEW.person_id IS NULL
      BEGIN
        SELECT RAISE(ABORT, 'source observation person may only be finalized once');
      END;

CREATE TRIGGER terminal_observation_scope_guard
      BEFORE INSERT ON source_observations
      WHEN NEW.observation_kind = 'terminal'
       AND (
         NEW.run_id IS NULL
         OR NEW.tick_id IS NULL
         OR NOT EXISTS (
           SELECT 1 FROM daily_runs r
           WHERE r.id = NEW.run_id
             AND r.source_contract_version = NEW.source_contract_version
         )
       )
      BEGIN
        SELECT RAISE(ABORT, 'terminal observation requires exact run and source contract version');
      END;

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
