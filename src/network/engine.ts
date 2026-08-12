import type { Database } from "bun:sqlite";
import { inTransaction } from "../db/database.ts";
import { DAILY_TARGET, SOURCES, type SourceId } from "./config.ts";
import type { DurableControllerState } from "./controller.ts";
import { canonicalIdentity, type PersonRow, resolveOrCreatePerson } from "./identity.ts";
import {
  type NetworkCandidate,
  parsePrepareSendReceipt,
  type SendPreparationReceipt,
  type WalkSkipReason,
} from "./results.ts";
import type {
  AuditBaselineInput,
  AuditInput,
  DailyRun,
  ReconciliationScope,
  RunProjection,
} from "./types.ts";

type RunRow = {
  id: string;
  local_date: string;
  status: DailyRun["status"];
  target: 30;
  preferred_per_source: 15;
  source_contract_version: number;
  final_reconciliation_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

type AttemptState = "planned" | "possible" | "durable" | "proven_no_send";
type AttemptRow = PersonRow & {
  id: string;
  run_id: string;
  person_id: string;
  source_id: SourceId;
  state: AttemptState;
  attempted_at: string | null;
  resolved_at: string | null;
  possible_receipt_key: string | null;
  resolution_receipt_key: string | null;
  evidence: string;
  planned_causal_sequence: number | null;
  possible_causal_sequence: number | null;
  resolution_causal_sequence: number | null;
  reservoir_entry_id: string | null;
  plan_evidence_json: string | null;
  possible_evidence_json: string | null;
  prepare_receipt_json: string | null;
  prepare_binding_json: string | null;
  commit_started_at: string | null;
  commit_receipt_json: string | null;
  commit_causal_sequence: number | null;
};
type CountRow = { count: number };
type BaselineRow = {
  id: string;
  run_id: string;
  people_count: number;
  identities_json: string;
  competing_sender_absent: number;
  attempt_count_at_capture: number;
  captured_at: string;
  causal_sequence: number | null;
};
type AuditRow = {
  id: string;
  run_id: string;
  baseline_id: string | null;
  people_count: number;
  identities_json: string;
  names_json: string;
  complete: number;
  competing_sender_absent: number;
  contradictory_evidence: number;
  captured_at: string;
  causal_sequence: number | null;
};

type CausalRecord = {
  sequence: number;
  payload_json: string;
};

type ReconciliationEvidenceKind = "identity" | "name" | "aggregate";
type ReconciliationEvidence = {
  attemptId: string;
  kind: ReconciliationEvidenceKind;
  matchedValue?: string;
};

export type ParkedMissedRun = {
  runId: string;
  localDate: string;
  durable: number;
  planned: number;
  provisional: number;
};

export class PriorDayNeedsAuditError extends Error {
  readonly runs: readonly ParkedMissedRun[];

  constructor(runs: readonly ParkedMissedRun[]) {
    super("an earlier local-day run has an unresolved send; audit it before starting a new day");
    this.name = "PriorDayNeedsAuditError";
    this.runs = runs;
  }
}

function required<T>(value: T | null | undefined, message: string): T {
  if (value === null || value === undefined) throw new Error(message);
  return value;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stableValue(item)]),
    );
  }
  return value;
}

function stableJson(value: unknown): string {
  return JSON.stringify(stableValue(value)) ?? "null";
}

function salesNavIdFromRowIdentity(rowIdentity: string): string | null {
  const match = /^urn:li:fs_salesProfile:([A-Za-z0-9_-]+)$/.exec(rowIdentity);
  return match?.[1] ?? null;
}

function runFromRow(row: RunRow): DailyRun {
  return {
    id: row.id,
    localDate: row.local_date,
    status: row.status,
    target: row.target,
    preferredPerSource: row.preferred_per_source,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    completedAt: row.completed_at,
  };
}

function emit(
  database: Database,
  type: string,
  payload: unknown,
  occurredAt: string,
  runId: string | null,
  dedupeKey: string,
): void {
  const id = `event:${dedupeKey}`;
  const payloadJson = stableJson(payload);
  const existing = database
    .query<
      {
        dedupe_key: string | null;
        id: string;
        occurred_at: string;
        payload_json: string;
        run_id: string | null;
        type: string;
      },
      [string, string]
    >(
      `SELECT id, run_id, type, payload_json, occurred_at, dedupe_key
       FROM events WHERE id = ? OR dedupe_key = ? ORDER BY sequence`,
    )
    .all(id, dedupeKey);
  if (existing.length > 0) {
    if (
      existing.length !== 1 ||
      existing[0]?.id !== id ||
      existing[0]?.run_id !== runId ||
      existing[0]?.type !== type ||
      existing[0]?.payload_json !== payloadJson ||
      existing[0]?.occurred_at !== occurredAt ||
      existing[0]?.dedupe_key !== dedupeKey
    ) {
      throw new Error(`event receipt conflict for ${dedupeKey}`);
    }
    return;
  }
  database
    .query(
      `INSERT INTO events
       (id, run_id, type, payload_json, occurred_at, dedupe_key)
       VALUES (?, ?, ?, ?, ?, ?)`,
    )
    .run(id, runId, type, payloadJson, occurredAt, dedupeKey);
}

function sameStoredPayload(actual: unknown, expected: unknown, label: string): void {
  if (stableJson(actual) !== stableJson(expected)) {
    throw new Error(`${label} receipt replay payload mismatch`);
  }
}

export class NetworkEngine {
  constructor(private readonly database: Database) {}

  openDailyRun(localDate: string, now: string, id: string = crypto.randomUUID()): DailyRun {
    return this.prepareDailyRun(localDate, now, id).run;
  }

  prepareDailyRun(
    localDate: string,
    now: string,
    id: string = crypto.randomUUID(),
  ): { run: DailyRun; parkedRuns: readonly ParkedMissedRun[] } {
    return inTransaction(this.database, () => {
      const existing = this.database
        .query<RunRow, [string]>("SELECT * FROM daily_runs WHERE local_date = ?")
        .get(localDate);
      if (existing !== null) return { run: runFromRow(existing), parkedRuns: [] };

      const priorRuns = this.database
        .query<RunRow, [string]>(
          `SELECT * FROM daily_runs
           WHERE local_date < ? AND status = 'active'
           ORDER BY local_date, id`,
        )
        .all(localDate)
        .map((run) => this.missedRunProjection(run));
      const unresolved = priorRuns.filter((run) => run.planned > 0 || run.provisional > 0);
      if (unresolved.length > 0) throw new PriorDayNeedsAuditError(unresolved);

      for (const prior of priorRuns) {
        emit(
          this.database,
          "daily_run_missed",
          {
            durable: prior.durable,
            localDate: prior.localDate,
            planned: prior.planned,
            provisional: prior.provisional,
            reason: "missed_local_day",
          },
          now,
          prior.runId,
          `run:${prior.runId}:missed`,
        );
        this.database
          .query(
            "UPDATE daily_runs SET status = 'blocked', updated_at = ? WHERE id = ? AND status = 'active'",
          )
          .run(now, prior.runId);
      }

      this.database
        .query(
          `INSERT INTO daily_runs
           (id, local_date, status, target, preferred_per_source, source_contract_version,
            created_at, updated_at)
           VALUES (?, ?, 'active', 30, 15, 1, ?, ?)`,
        )
        .run(id, localDate, now, now);
      emit(this.database, "daily_run_opened", { localDate }, now, id, `run:${id}:opened`);
      return { run: runFromRow(this.runRow(id)), parkedRuns: priorRuns };
    });
  }

  /**
   * Deliberately end an active run so a later local day can start cleanly.
   * Records the end as an evidence event and marks the run blocked, which the
   * new-day preparation skips. This is the supported way to close a day's run
   * without resolving its remaining sends; the ledger and dedup records stay.
   */
  endRun(localDate: string, reason: string, now: string): DailyRun {
    return inTransaction(this.database, () => {
      const run = this.database
        .query<RunRow, [string]>("SELECT * FROM daily_runs WHERE local_date = ?")
        .get(localDate);
      if (run === null) throw new Error(`no daily run for ${localDate}`);
      if (run.status !== "active") throw new Error("run is not active");
      const updated = this.database
        .query(
          "UPDATE daily_runs SET status = 'blocked', updated_at = ? WHERE id = ? AND status = 'active'",
        )
        .run(now, run.id);
      if (updated.changes !== 1) throw new Error("run status update failed");
      const missed = this.missedRunProjection(run);
      emit(
        this.database,
        "daily_run_ended",
        {
          localDate,
          reason,
          durable: missed.durable,
          planned: missed.planned,
          provisional: missed.provisional,
        },
        now,
        run.id,
        `run:${run.id}:ended`,
      );
      return runFromRow(this.runRow(run.id));
    });
  }

  markPossible(
    attemptId: string,
    attemptedAt: string,
    evidence: unknown,
    receiptId: string = `possible:${attemptId}`,
  ): void {
    inTransaction(this.database, () => {
      const attempt = this.attempt(attemptId);
      const payload = {
        attemptId,
        attemptedAt,
        evidenceJson: stableJson(evidence),
        personId: attempt.person_id,
        prepareBindingJson: null as string | null,
        prepareReceiptJson: null as string | null,
        receiptId,
        runId: attempt.run_id,
        sourceId: attempt.source_id,
      };
      const preparation = this.preparationEvidence(attempt, attemptedAt, evidence, receiptId);
      payload.prepareReceiptJson = preparation.prepareReceiptJson;
      payload.prepareBindingJson = preparation.prepareBindingJson;
      if (attempt.possible_causal_sequence !== null) {
        const causal = this.recordCausal("attempt_possible", receiptId, payload, "possible");
        if (
          attempt.possible_causal_sequence !== causal.sequence ||
          attempt.possible_receipt_key !== receiptId
        ) {
          throw new Error("possible receipt replay payload mismatch");
        }
        return;
      }
      this.requireActiveRun(attempt.run_id);
      if (attempt.state !== "planned") throw new Error("attempt must be planned");
      const causal = this.recordCausal("attempt_possible", receiptId, payload, "possible");
      this.database
        .query(
          `UPDATE send_attempts SET state = 'possible', attempted_at = ?,
           possible_receipt_key = ?, possible_causal_sequence = ?, evidence = ?,
           possible_evidence_json = ?, prepare_receipt_json = ?, prepare_binding_json = ?
           WHERE id = ? AND state = 'planned'`,
        )
        .run(
          attemptedAt,
          receiptId,
          causal.sequence,
          payload.evidenceJson,
          payload.evidenceJson,
          preparation.prepareReceiptJson,
          preparation.prepareBindingJson,
          attemptId,
        );
      emit(
        this.database,
        "send_possible",
        { attemptId },
        attemptedAt,
        attempt.run_id,
        `attempt:${attemptId}:possible`,
      );
    });
  }

  refreshPreparation(
    attemptId: string,
    attemptedAt: string,
    evidence: unknown,
    receiptId: string,
  ): void {
    inTransaction(this.database, () => {
      const attempt = this.attempt(attemptId);
      this.requireActiveRun(attempt.run_id);
      if (attempt.state !== "possible") {
        throw new Error("refreshPreparation requires state possible");
      }
      if (attempt.commit_started_at !== null) {
        throw new Error("refreshPreparation rejects when commit already started");
      }
      const payload = {
        attemptId,
        attemptedAt,
        evidenceJson: stableJson(evidence),
        personId: attempt.person_id,
        prepareBindingJson: null as string | null,
        prepareReceiptJson: null as string | null,
        receiptId,
        runId: attempt.run_id,
        sourceId: attempt.source_id,
      };
      const preparation = this.preparationEvidence(attempt, attemptedAt, evidence, receiptId);
      if (preparation.prepareReceiptJson === null || preparation.prepareBindingJson === null) {
        throw new Error("refreshPreparation requires exact prepare receipt evidence");
      }
      payload.prepareReceiptJson = preparation.prepareReceiptJson;
      payload.prepareBindingJson = preparation.prepareBindingJson;
      const causal = this.recordCausal("attempt_possible", receiptId, payload, "possible");
      if (causal.replay) {
        if (
          attempt.possible_receipt_key === receiptId &&
          attempt.prepare_receipt_json === preparation.prepareReceiptJson &&
          attempt.prepare_binding_json === preparation.prepareBindingJson
        ) {
          return;
        }
        throw new Error("refreshPreparation receipt replay payload mismatch");
      }
      this.database
        .query(
          `UPDATE send_attempts SET attempted_at = ?,
           possible_receipt_key = ?, possible_causal_sequence = ?, evidence = ?,
           possible_evidence_json = ?, prepare_receipt_json = ?, prepare_binding_json = ?
           WHERE id = ? AND state = 'possible' AND commit_started_at IS NULL`,
        )
        .run(
          attemptedAt,
          receiptId,
          causal.sequence,
          payload.evidenceJson,
          payload.evidenceJson,
          preparation.prepareReceiptJson,
          preparation.prepareBindingJson,
          attemptId,
        );
      emit(
        this.database,
        "send_possible",
        { attemptId },
        attemptedAt,
        attempt.run_id,
        `attempt:${attemptId}:possible:${receiptId}`,
      );
    });
  }

  markProvenNoSend(
    attemptId: string,
    resolvedAt: string,
    evidence: unknown,
    receiptId?: string,
  ): void {
    this.resolveProvenNoSend(attemptId, resolvedAt, evidence, receiptId);
  }

  addRelationshipFact(input: {
    id?: string;
    personId: string;
    kind:
      | "pending"
      | "connected"
      | "do_not_contact"
      | "cross_workflow_message_sent"
      | "unresolved_send"
      | "proven_no_send";
    effectiveAt: string;
    runId?: string;
    evidence: unknown;
  }): void {
    inTransaction(this.database, () => {
      const id = input.id ?? crypto.randomUUID();
      const replay = this.database
        .query<
          {
            person_id: string;
            kind: string;
            effective_at: string;
            run_id: string | null;
            evidence: string;
          },
          [string]
        >(
          "SELECT person_id, kind, effective_at, run_id, evidence FROM relationship_facts WHERE id = ?",
        )
        .get(id);
      const expected = {
        effectiveAt: input.effectiveAt,
        evidence: stableJson(input.evidence),
        kind: input.kind,
        personId: input.personId,
        runId: input.runId ?? null,
      };
      if (replay !== null) {
        sameStoredPayload(
          {
            effectiveAt: replay.effective_at,
            evidence: replay.evidence,
            kind: replay.kind,
            personId: replay.person_id,
            runId: replay.run_id,
          },
          expected,
          "relationship fact",
        );
        return;
      }
      this.database
        .query(
          `INSERT INTO relationship_facts
           (id, person_id, kind, effective_at, run_id, evidence)
           VALUES (?, ?, ?, ?, ?, ?)`,
        )
        .run(
          id,
          input.personId,
          input.kind,
          input.effectiveAt,
          input.runId ?? null,
          expected.evidence,
        );
      emit(
        this.database,
        "relationship_fact_added",
        { kind: input.kind, personId: input.personId },
        input.effectiveAt,
        input.runId ?? null,
        `relationship-fact:${id}`,
      );
    });
  }

  recordBaseline(input: AuditBaselineInput): void {
    inTransaction(this.database, () => {
      const identities = [...(input.identities ?? [])]
        .map((value) => value.trim())
        .filter((value) => value.length > 0)
        .sort();
      if (new Set(identities).size !== identities.length) {
        throw new Error("baseline identities must be unique");
      }
      const identitiesJson = stableJson(identities);
      const replay = this.database
        .query<
          {
            attempt_count_at_capture: number;
            captured_at: string;
            competing_sender_absent: number;
            identities_json: string;
            invocation_id: string;
            people_count: number;
            run_id: string;
          },
          [string]
        >("SELECT * FROM audit_baselines WHERE id = ?")
        .get(input.id);
      if (replay !== null) {
        this.recordCausal(
          "audit_baseline",
          input.id,
          {
            attemptCountAtCapture: replay.attempt_count_at_capture,
            baselineId: input.id,
            capturedAt: input.capturedAt,
            competingSenderAbsent: input.competingSenderAbsent,
            identities,
            invocationId: input.invocationId,
            peopleCount: input.peopleCount,
            runId: input.runId,
          },
          "audit baseline",
        );
        return;
      }
      this.requireActiveRun(input.runId);
      const attemptCount = this.attemptCount(input.runId);
      if (attemptCount !== 0) throw new Error("baseline must be captured before send reservations");
      const causal = this.recordCausal(
        "audit_baseline",
        input.id,
        {
          attemptCountAtCapture: attemptCount,
          baselineId: input.id,
          capturedAt: input.capturedAt,
          competingSenderAbsent: input.competingSenderAbsent,
          identities,
          invocationId: input.invocationId,
          peopleCount: input.peopleCount,
          runId: input.runId,
        },
        "audit baseline",
      );
      this.database
        .query(
          `INSERT INTO audit_baselines
           (id, run_id, invocation_id, people_count, identities_json, competing_sender_absent,
            attempt_count_at_capture, captured_at, causal_sequence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run(
          input.id,
          input.runId,
          input.invocationId,
          input.peopleCount,
          identitiesJson,
          input.competingSenderAbsent ? 1 : 0,
          attemptCount,
          input.capturedAt,
          causal.sequence,
        );
      emit(
        this.database,
        "audit_baseline_recorded",
        { baselineId: input.id },
        input.capturedAt,
        input.runId,
        `baseline:${input.id}`,
      );
    });
  }

  recordAudit(input: AuditInput): void {
    inTransaction(this.database, () => {
      this.requireActiveRun(input.runId);
      const baseline = input.baselineId === null ? null : this.baseline(input.baselineId);
      if (baseline !== null && baseline.run_id !== input.runId) {
        throw new Error("audit baseline belongs to another run");
      }
      const identities = [...input.identities]
        .map((value) => value.trim())
        .filter((value) => value.length > 0);
      if (new Set(identities).size !== identities.length) {
        throw new Error("audit identities must be unique");
      }
      const baselineIdentities =
        baseline === null ? [] : this.parseIdentityList(baseline.identities_json);
      const competingSenderAbsent = this.computeCompetingSenderAbsent(
        input.runId,
        baselineIdentities,
        identities,
      );
      const expected = {
        auditId: input.id,
        baselineId: input.baselineId,
        capturedAt: input.capturedAt,
        competingSenderAbsent,
        complete: input.complete,
        contradictoryEvidence: input.contradictoryEvidence ?? false,
        identities: [...identities].sort(),
        invocationId: input.invocationId,
        names: [...input.names].sort(),
        peopleCount: input.peopleCount,
        runId: input.runId,
      };
      const replay = this.auditOrNull(input.id);
      if (replay !== null) {
        this.recordCausal("audit_snapshot", input.id, expected, "audit");
        return;
      }
      const causal = this.recordCausal("audit_snapshot", input.id, expected, "audit");
      this.database
        .query(
          `INSERT INTO audit_snapshots
           (id, run_id, invocation_id, baseline_id, people_count, identities_json,
            names_json, complete, competing_sender_absent, contradictory_evidence,
            captured_at, causal_sequence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run(
          input.id,
          input.runId,
          input.invocationId,
          input.baselineId,
          input.peopleCount,
          stableJson(expected.identities),
          stableJson(expected.names),
          input.complete ? 1 : 0,
          expected.competingSenderAbsent ? 1 : 0,
          input.contradictoryEvidence ? 1 : 0,
          input.capturedAt,
          causal.sequence,
        );
      emit(
        this.database,
        "audit_recorded",
        { auditId: input.id, baselineId: input.baselineId },
        input.capturedAt,
        input.runId,
        `audit:${input.id}`,
      );
    });
  }

  reconcile(
    runId: string,
    baselineId: string | null,
    auditId: string,
    now: string,
    reconciliationId: string = `reconciliation:${auditId}`,
    scope: ReconciliationScope = "microbatch",
  ): string[] {
    return inTransaction(this.database, () => {
      if (scope !== "microbatch" && scope !== "final") {
        throw new Error("unknown reconciliation scope");
      }
      const replay = this.database
        .query<
          {
            causal_sequence: number | null;
            complete: number;
            competing_sender_absent: number;
            confirmed_attempt_ids_json: string;
            id: string;
            mode: "exact" | "aggregate" | "mixed";
            scope: ReconciliationScope;
            sealed: number;
          },
          [string, string, string]
        >(
          `SELECT id, causal_sequence, complete, competing_sender_absent,
                  confirmed_attempt_ids_json, mode, scope, sealed
           FROM reconciliations
           WHERE id = ? OR (run_id = ? AND audit_id = ?)`,
        )
        .get(reconciliationId, runId, auditId);
      if (replay !== null) {
        if (replay.scope !== scope) throw new Error("reconciliation scope mismatch");
        const replayEvidenceRows = this.database
          .query<
            {
              attempt_id: string;
              evidence_kind: ReconciliationEvidenceKind;
              matched_value: string | null;
            },
            [string]
          >(
            `SELECT attempt_id, evidence_kind, matched_value FROM reconciliation_attempts
             WHERE reconciliation_id = ? ORDER BY attempt_id`,
          )
          .all(replay.id);
        const replayEvidence: ReconciliationEvidence[] = replayEvidenceRows.map((row) =>
          scope === "microbatch"
            ? {
                attemptId: row.attempt_id,
                kind: row.evidence_kind,
                matchedValue: required(
                  row.matched_value,
                  "microbatch reconciliation is missing its exact matched value",
                ),
              }
            : { attemptId: row.attempt_id, kind: row.evidence_kind },
        );
        const replayConfirmedAttemptIds = JSON.parse(replay.confirmed_attempt_ids_json) as string[];
        const causal = this.recordCausal(
          "reconciliation",
          reconciliationId,
          {
            auditId,
            attemptCount: replayEvidence.length,
            baselineId,
            competingSenderAbsent: replay.competing_sender_absent === 1,
            evidence: replayEvidence,
            finalComplete: replay.complete === 1,
            mode: replay.mode,
            newlyConfirmedAttemptIds: replayConfirmedAttemptIds,
            scope,
            reconciledAt: now,
            reconciliationId,
            runId,
          },
          "reconciliation",
        );
        if (replay.id !== reconciliationId || replay.causal_sequence !== causal.sequence) {
          throw new Error("reconciliation receipt replay payload mismatch");
        }
        const replayAudit = required(this.auditOrNull(auditId), "audit not found");
        if (replayAudit.complete !== 1 || replayAudit.contradictory_evidence !== 0) {
          return [];
        }
        if (
          (scope === "final" && replay.complete !== 1) ||
          (scope === "microbatch" && (replay.complete !== 0 || replay.mode !== "exact"))
        ) {
          return [];
        }
        if (replay.sealed === 0) {
          this.database
            .query("UPDATE reconciliations SET sealed = 1 WHERE id = ? AND sealed = 0")
            .run(replay.id);
        }
        const replayEvidenceByAttempt = new Map(
          replayEvidence.map((item) => [item.attemptId, item] as const),
        );
        for (const attemptId of replayConfirmedAttemptIds) {
          const attempt = this.attempt(attemptId);
          if (attempt.run_id !== runId || !["possible", "durable"].includes(attempt.state)) {
            throw new Error(
              "sealed reconciliation confirmation does not belong to an active attempt",
            );
          }
          const evidenceItem = required(
            replayEvidenceByAttempt.get(attemptId),
            "sealed reconciliation confirmation has no exact evidence",
          );
          if (attempt.state === "possible") {
            this.applySealedReconciliationConfirmation({
              attemptId,
              auditId,
              baselineId,
              evidenceKind: evidenceItem.kind,
              reconciliationId: replay.id,
              scope,
              resolvedAt: now,
            });
          }
        }
        if (scope === "final") {
          const run = this.runRow(runId);
          if (run.final_reconciliation_id === null) {
            this.database
              .query(
                "UPDATE daily_runs SET final_reconciliation_id = ?, updated_at = ? WHERE id = ?",
              )
              .run(replay.id, now, runId);
          } else if (run.final_reconciliation_id !== replay.id) {
            throw new Error("run already references a different final reconciliation");
          }
        }
        return replayConfirmedAttemptIds;
      }

      const baseline = baselineId === null ? null : this.baseline(baselineId);
      const audit = required(this.auditOrNull(auditId), "audit not found");
      this.requireActiveRun(runId);
      if (
        audit.run_id !== runId ||
        (baseline !== null && (baseline.run_id !== runId || audit.baseline_id !== baselineId))
      ) {
        throw new Error("reconciliation run or baseline mismatch");
      }
      if (
        baseline !== null &&
        (audit.captured_at < baseline.captured_at ||
          baseline.causal_sequence === null ||
          audit.causal_sequence === null ||
          audit.causal_sequence <= baseline.causal_sequence)
      ) {
        throw new Error("audit must be causally after its baseline");
      }
      if (audit.complete !== 1 || audit.contradictory_evidence !== 0) {
        return [];
      }

      const allBefore = this.attempts(runId);
      const provisionalAll = allBefore.filter((attempt) => attempt.state === "possible");
      const causallyAudited = (attempt: AttemptRow): boolean =>
        attempt.possible_causal_sequence !== null &&
        audit.causal_sequence !== null &&
        audit.causal_sequence > attempt.possible_causal_sequence;
      const planned = allBefore.filter((attempt) => attempt.state === "planned");
      const active = allBefore.filter((attempt) => ["possible", "durable"].includes(attempt.state));
      const auditIdentities = this.parseIdentityList(audit.identities_json);
      const baselineIdentities =
        baseline === null ? [] : this.parseIdentityList(baseline.identities_json);
      const newIdentities = auditIdentities.filter(
        (identity) => !baselineIdentities.includes(identity),
      );
      const auditNames = JSON.parse(audit.names_json) as string[];
      let confirmed: AttemptRow[];
      let evidenceRows: ReconciliationEvidence[];
      let finalComplete: boolean;

      if (scope === "microbatch") {
        if (
          (baseline !== null && baseline.attempt_count_at_capture !== 0) ||
          provisionalAll.length === 0 ||
          provisionalAll.some((attempt) => !causallyAudited(attempt))
        ) {
          return [];
        }
        const identityCounts = this.exactCounts(
          newIdentities.length > 0 ? newIdentities : auditIdentities,
        );
        const auditNameCounts = this.exactCounts(auditNames);
        const activeNameCounts = this.exactCounts(active.map((attempt) => attempt.name));
        const exactEvidence = new Map<string, ReconciliationEvidence>();
        for (const attempt of active) {
          if (!causallyAudited(attempt)) continue;
          const match = this.microbatchExactMatch(
            attempt,
            identityCounts,
            auditNameCounts,
            activeNameCounts,
          );
          if (match !== null) exactEvidence.set(attempt.id, { attemptId: attempt.id, ...match });
        }
        confirmed = provisionalAll.filter((attempt) => exactEvidence.has(attempt.id));
        if (confirmed.length === 0) return [];
        evidenceRows = [...exactEvidence.values()].sort((left, right) =>
          left.attemptId.localeCompare(right.attemptId),
        );
        finalComplete = false;
      } else {
        const matchIdentitySet = new Set(
          newIdentities.length > 0 ? newIdentities : auditIdentities,
        );
        const auditNameCounts = this.nameCounts(auditNames);
        const activeNameCounts = this.nameCounts(active.map((attempt) => attempt.name));
        const exactMatches = new Map<string, "identity" | "name">();
        for (const attempt of active) {
          const kind = this.exactMatchKind(
            attempt,
            matchIdentitySet,
            auditNameCounts,
            activeNameCounts,
          );
          if (kind !== null) exactMatches.set(attempt.id, kind);
        }
        // Per-attempt exact evidence is the only confirmation path. Count-delta /
        // competing-sender aggregate confirmation is unreachable with name-only
        // sent-list evidence (LinkedIn no longer exposes profile links/URNs on
        // the sent page) and is broken by real-world already-pending no-ops and
        // declines. Already-durable attempts keep prior sealed evidence and do
        // not need re-matching in the final audit.
        confirmed = provisionalAll.filter((attempt) => exactMatches.has(attempt.id));
        const confirmedIds = new Set(confirmed.map((attempt) => attempt.id));
        const predictedDurable = active.filter(
          (attempt) => attempt.state === "durable" || confirmedIds.has(attempt.id),
        );
        const unresolved = allBefore.filter(
          (attempt) =>
            attempt.state === "planned" ||
            (attempt.state === "possible" && !confirmedIds.has(attempt.id)),
        );
        finalComplete =
          predictedDurable.length === DAILY_TARGET &&
          active.length === DAILY_TARGET &&
          unresolved.length === 0 &&
          planned.length === 0 &&
          predictedDurable.every(causallyAudited) &&
          confirmed.every((attempt) => exactMatches.has(attempt.id));
        if (!finalComplete) return [];
        // Evidence rows cover newly confirmed provisionals only; already-durable
        // attempts retain their prior sealed evidence.
        evidenceRows = confirmed
          .map((attempt) => {
            const kind = exactMatches.get(attempt.id);
            if (kind === undefined) throw new Error("confirmed attempt missing exact evidence");
            return { attemptId: attempt.id, kind };
          })
          .sort((left, right) => left.attemptId.localeCompare(right.attemptId));
      }

      const evidenceKinds = new Set(evidenceRows.map(({ kind }) => kind));
      const mode =
        scope === "microbatch"
          ? "exact"
          : evidenceKinds.size === 1 && evidenceKinds.has("aggregate")
            ? "aggregate"
            : evidenceKinds.has("aggregate")
              ? "mixed"
              : "exact";
      const newlyConfirmedAttemptIds = confirmed.map((attempt) => attempt.id).sort();
      // Final seals attest the full durable set (30). Microbatch seals attest only
      // the newly confirmed provisionals in this audit.
      const attemptCount = scope === "final" && finalComplete ? DAILY_TARGET : evidenceRows.length;
      const causal = this.recordCausal(
        "reconciliation",
        reconciliationId,
        {
          auditId,
          attemptCount,
          baselineId,
          competingSenderAbsent:
            (baseline === null || baseline.competing_sender_absent === 1) &&
            audit.competing_sender_absent === 1,
          evidence: evidenceRows,
          finalComplete,
          mode,
          newlyConfirmedAttemptIds,
          scope,
          reconciledAt: now,
          reconciliationId,
          runId,
        },
        "reconciliation",
      );
      this.database
        .query(
          `INSERT INTO reconciliations
           (id, run_id, baseline_id, audit_id, mode, attempt_count, complete,
            competing_sender_absent, confirmed_attempt_ids_json, created_at, causal_sequence,
            scope)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run(
          reconciliationId,
          runId,
          baselineId,
          auditId,
          mode,
          attemptCount,
          finalComplete ? 1 : 0,
          (baseline === null || baseline.competing_sender_absent === 1) &&
            audit.competing_sender_absent === 1
            ? 1
            : 0,
          stableJson(newlyConfirmedAttemptIds),
          now,
          causal.sequence,
          scope,
        );
      for (const { attemptId, kind, matchedValue } of evidenceRows) {
        this.database
          .query(
            `INSERT INTO reconciliation_attempts
             (reconciliation_id, attempt_id, evidence_kind, matched_value) VALUES (?, ?, ?, ?)`,
          )
          .run(reconciliationId, attemptId, kind, matchedValue ?? null);
      }
      this.database
        .query("UPDATE reconciliations SET sealed = 1 WHERE id = ? AND sealed = 0")
        .run(reconciliationId);
      const evidenceByAttempt = new Map(
        evidenceRows.map((item) => [item.attemptId, item] as const),
      );
      for (const attempt of confirmed) {
        const item = required(
          evidenceByAttempt.get(attempt.id),
          "confirmed attempt has no reconciliation evidence",
        );
        this.applySealedReconciliationConfirmation({
          attemptId: attempt.id,
          auditId,
          baselineId,
          evidenceKind: item.kind,
          reconciliationId,
          scope,
          resolvedAt: now,
        });
      }
      if (scope === "final") {
        this.database
          .query("UPDATE daily_runs SET final_reconciliation_id = ?, updated_at = ? WHERE id = ?")
          .run(reconciliationId, now, runId);
      }
      emit(
        this.database,
        "reconciliation_recorded",
        {
          auditId,
          baselineId,
          complete: finalComplete,
          scope,
          reconciliationId,
          referencedAttempts: evidenceRows.length,
        },
        now,
        runId,
        `reconciliation:${runId}:${auditId}`,
      );
      return newlyConfirmedAttemptIds;
    });
  }

  readControllerState(runId: string): DurableControllerState {
    this.runRow(runId);
    const baseline = this.database
      .query<{ id: string }, [string]>(
        `SELECT id FROM audit_baselines
         WHERE run_id = ? ORDER BY causal_sequence, id LIMIT 1`,
      )
      .get(runId);
    const pendingAudit = this.database
      .query<{ baseline_id: string; id: string }, [string]>(
        `SELECT audit.id, audit.baseline_id
         FROM audit_snapshots audit
         WHERE audit.run_id = ?
           AND audit.baseline_id IS NOT NULL
           AND NOT EXISTS (
             SELECT 1 FROM reconciliations rec
             WHERE rec.audit_id = audit.id AND rec.sealed = 1
           )
         ORDER BY audit.causal_sequence DESC, audit.id DESC LIMIT 1`,
      )
      .get(runId);
    const openAttempts = this.attempts(runId)
      .filter((attempt) => attempt.state === "planned" || attempt.state === "possible")
      .sort(
        (left, right) =>
          (left.planned_causal_sequence ?? 0) - (right.planned_causal_sequence ?? 0) ||
          left.id.localeCompare(right.id),
      )
      .map((attempt) => {
        const candidate = this.candidateForAttempt(attempt);
        const prepareReceipt =
          attempt.prepare_receipt_json === null
            ? null
            : parsePrepareSendReceipt(JSON.parse(attempt.prepare_receipt_json), {
                attemptId: attempt.id,
                candidate,
              });
        const commitFields = [
          attempt.commit_started_at,
          attempt.commit_receipt_json,
          attempt.commit_causal_sequence,
        ];
        const present = commitFields.filter((value) => value !== null).length;
        const walkCommit =
          attempt.commit_started_at !== null &&
          attempt.commit_receipt_json === null &&
          attempt.commit_causal_sequence === null &&
          typeof attempt.possible_receipt_key === "string" &&
          attempt.possible_receipt_key.startsWith("walk:");
        if (present !== 0 && present !== commitFields.length && !walkCommit) {
          throw new Error("commit-start evidence is incomplete");
        }
        return {
          attemptId: attempt.id,
          runId: attempt.run_id,
          sourceId: attempt.source_id,
          state: attempt.state as "planned" | "possible",
          candidate,
          prepareReceipt,
          commitStarted: present === commitFields.length || walkCommit,
        };
      });
    return {
      baseline: baseline === null ? null : { id: baseline.id },
      pendingAudit:
        pendingAudit === null
          ? null
          : { id: pendingAudit.id, baselineId: pendingAudit.baseline_id },
      openAttempts,
    };
  }

  recordCommitStarted(input: {
    readonly attemptId: string;
    readonly runId: string;
    readonly receipt: SendPreparationReceipt;
    readonly startedAt: string;
  }): void {
    inTransaction(this.database, () => {
      const attempt = this.attempt(input.attemptId);
      if (attempt.run_id !== input.runId) throw new Error("commit-start run mismatch");
      if (attempt.state === "planned" || attempt.prepare_receipt_json === null) {
        throw new Error("commit cannot start before durable preparation");
      }
      const candidate = this.candidateForAttempt(attempt);
      const receipt = parsePrepareSendReceipt(input.receipt, {
        attemptId: input.attemptId,
        candidate,
      });
      const receiptJson = stableJson(receipt);
      if (
        receiptJson !== attempt.prepare_receipt_json ||
        receipt.receiptId !== attempt.possible_receipt_key ||
        receipt.preparedAt !== attempt.attempted_at ||
        attempt.prepare_binding_json === null
      ) {
        throw new Error("commit-start receipt does not equal durable preparation");
      }
      const payload = {
        attemptId: input.attemptId,
        commitReceiptJson: receiptJson,
        prepareBindingJson: attempt.prepare_binding_json,
        runId: input.runId,
        startedAt: input.startedAt,
      };
      if (attempt.commit_causal_sequence !== null) {
        const causal = this.recordCausal(
          "attempt_commit_started",
          receipt.receiptId,
          payload,
          "commit start",
        );
        if (
          causal.sequence !== attempt.commit_causal_sequence ||
          attempt.commit_started_at !== input.startedAt ||
          attempt.commit_receipt_json !== receiptJson
        ) {
          throw new Error("commit-start receipt replay payload mismatch");
        }
        return;
      }
      this.requireActiveRun(input.runId);
      if (attempt.state !== "possible") throw new Error("only possible attempts can start commit");
      const causal = this.recordCausal(
        "attempt_commit_started",
        receipt.receiptId,
        payload,
        "commit start",
      );
      this.database
        .query(
          `UPDATE send_attempts
           SET commit_started_at = ?, commit_receipt_json = ?, commit_causal_sequence = ?
           WHERE id = ? AND state = 'possible' AND commit_causal_sequence IS NULL`,
        )
        .run(input.startedAt, receiptJson, causal.sequence, input.attemptId);
      emit(
        this.database,
        "send_commit_started",
        { attemptId: input.attemptId, receiptId: receipt.receiptId },
        input.startedAt,
        input.runId,
        `attempt:${input.attemptId}:commit-started`,
      );
    });
  }

  recordWalkSends(
    runId: string,
    sourceId: SourceId,
    rows: {
      readonly sent: readonly { readonly rowIdentity: string; readonly name: string }[];
      readonly skipped: readonly {
        readonly rowIdentity: string;
        readonly name: string;
        readonly reason: WalkSkipReason;
      }[];
    },
    now: string,
  ): { readonly sent: number; readonly skipped: number } {
    return inTransaction(this.database, () => {
      this.assertSource(sourceId);
      this.requireActiveRun(runId);
      let sentCount = 0;
      let skippedCount = 0;
      for (const row of rows.sent) {
        const salesNavId = salesNavIdFromRowIdentity(row.rowIdentity);
        if (salesNavId === null) continue;
        if (this.remainingCapacity(runId) <= 0) break;
        const person = resolveOrCreatePerson(
          this.database,
          { name: row.name, salesNavId },
          now,
          crypto.randomUUID(),
        );
        const existing = this.database
          .query<{ id: string }, [string, string]>(
            "SELECT id FROM send_attempts WHERE run_id = ? AND person_id = ?",
          )
          .get(runId, person.id);
        if (existing !== null) continue;
        const activeElsewhere =
          this.database
            .query<CountRow, [string]>(
              `SELECT COUNT(*) AS count FROM send_attempts
               WHERE person_id = ? AND state IN ('planned', 'possible', 'durable')`,
            )
            .get(person.id)?.count ?? 0;
        if (activeElsewhere > 0) continue;
        const candidate = required(
          this.controllerCandidate(sourceId, row.name, salesNavId),
          "walk row lacks controller candidate",
        );
        const attemptId = crypto.randomUUID();
        const receiptId = `walk:${runId}:${row.rowIdentity}`;
        const evidence = {
          candidate,
          walk: { name: row.name, rowIdentity: row.rowIdentity, sourceId },
        };
        const evidenceJson = stableJson(evidence);
        const causal = this.recordCausal(
          "attempt_possible",
          receiptId,
          {
            attemptId,
            attemptedAt: now,
            evidenceJson,
            personId: person.id,
            prepareBindingJson: null,
            prepareReceiptJson: null,
            receiptId,
            runId,
            sourceId,
          },
          "possible",
        );
        this.database
          .query(
            `INSERT INTO send_attempts
             (id, run_id, person_id, source_id, state, evidence, attempted_at,
              possible_receipt_key, possible_causal_sequence, possible_evidence_json,
              commit_started_at, reservoir_entry_id, plan_evidence_json,
              planned_causal_sequence)
             VALUES (?, ?, ?, ?, 'possible', ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)`,
          )
          .run(
            attemptId,
            runId,
            person.id,
            sourceId,
            evidenceJson,
            now,
            receiptId,
            causal.sequence,
            evidenceJson,
            now,
          );
        emit(
          this.database,
          "send_possible",
          { attemptId },
          now,
          runId,
          `attempt:${attemptId}:possible`,
        );
        sentCount += 1;
      }
      for (const row of rows.skipped) {
        if (row.reason === "unreachable" && row.rowIdentity === "") {
          skippedCount += 1;
          continue;
        }
        const salesNavId = salesNavIdFromRowIdentity(row.rowIdentity);
        if (salesNavId === null) {
          skippedCount += 1;
          continue;
        }
        const person = resolveOrCreatePerson(
          this.database,
          { name: row.name === "unknown" ? salesNavId : row.name, salesNavId },
          now,
          crypto.randomUUID(),
        );
        const factId = `walk-skip:${runId}:${row.rowIdentity}:${row.reason}`;
        const evidence = {
          name: row.name,
          reason: row.reason,
          rowIdentity: row.rowIdentity,
          sourceId,
        };
        const replay = this.database
          .query<{ id: string }, [string]>("SELECT id FROM relationship_facts WHERE id = ?")
          .get(factId);
        if (replay === null) {
          this.database
            .query(
              `INSERT INTO relationship_facts
               (id, person_id, kind, effective_at, run_id, evidence)
               VALUES (?, ?, 'proven_no_send', ?, ?, ?)`,
            )
            .run(factId, person.id, now, runId, stableJson(evidence));
          emit(
            this.database,
            "relationship_fact_added",
            { kind: "proven_no_send", personId: person.id },
            now,
            runId,
            `relationship-fact:${factId}`,
          );
        }
        skippedCount += 1;
      }
      return { sent: sentCount, skipped: skippedCount };
    });
  }

  resolveUnconfirmedAfterAudit(runId: string, auditId: string, now: string): number {
    return inTransaction(this.database, () => {
      this.requireActiveRun(runId);
      const audit = required(this.auditOrNull(auditId), "audit not found");
      if (audit.run_id !== runId) throw new Error("audit belongs to another run");
      if (audit.causal_sequence === null) throw new Error("audit lacks causal sequence");
      const sealedConfirmed = new Set(
        this.database
          .query<{ attempt_id: string }, [string, string]>(
            `SELECT child.attempt_id AS attempt_id
             FROM reconciliations rec
             JOIN reconciliation_attempts child ON child.reconciliation_id = rec.id
             JOIN json_each(rec.confirmed_attempt_ids_json) confirmed
               ON confirmed.value = child.attempt_id
             WHERE rec.run_id = ? AND rec.audit_id = ? AND rec.sealed = 1`,
          )
          .all(runId, auditId)
          .map((row) => row.attempt_id),
      );
      const unconfirmed = this.attempts(runId).filter(
        (attempt) =>
          attempt.state === "possible" &&
          attempt.possible_causal_sequence !== null &&
          attempt.possible_causal_sequence < audit.causal_sequence! &&
          !sealedConfirmed.has(attempt.id),
      );
      let resolved = 0;
      for (const attempt of unconfirmed) {
        this.resolveProvenNoSend(
          attempt.id,
          now,
          { auditId, reason: "absent_after_settle" },
          `proven_no_send:${attempt.id}:absent_after_settle`,
        );
        resolved += 1;
      }
      return resolved;
    });
  }

  finish(runId: string, now: string): DailyRun {
    return inTransaction(this.database, () => {
      const projection = this.projection(runId);
      if (
        projection.durable !== DAILY_TARGET ||
        projection.provisional !== 0 ||
        projection.planned !== 0 ||
        !projection.finalReconciliation
      ) {
        throw new Error(
          "run cannot finish without exactly 30 durable, zero reservations, and exact final reconciliation",
        );
      }
      this.database
        .query(
          "UPDATE daily_runs SET status = 'done', updated_at = ?, completed_at = ? WHERE id = ?",
        )
        .run(now, now, runId);
      emit(
        this.database,
        "daily_run_done",
        { durable: DAILY_TARGET },
        now,
        runId,
        `run:${runId}:done`,
      );
      return runFromRow(this.runRow(runId));
    });
  }

  projection(runId: string): RunProjection {
    const runRow = this.runRow(runId);
    const bySource = Object.fromEntries(
      SOURCES.map((source) => {
        const states = this.database
          .query<{ state: string; count: number }, [string, string]>(
            `SELECT state, COUNT(*) AS count FROM send_attempts
             WHERE run_id = ? AND source_id = ? GROUP BY state`,
          )
          .all(runId, source.id);
        return [
          source.id,
          {
            available: 0,
            durable: states.find((row) => row.state === "durable")?.count ?? 0,
            planned: states.find((row) => row.state === "planned")?.count ?? 0,
            possible: states.find((row) => row.state === "possible")?.count ?? 0,
          },
        ];
      }),
    ) as RunProjection["bySource"];
    const durable = Object.values(bySource).reduce((sum, row) => sum + row.durable, 0);
    const planned = Object.values(bySource).reduce((sum, row) => sum + row.planned, 0);
    const provisional = Object.values(bySource).reduce((sum, row) => sum + row.possible, 0);
    const finalReconciliation =
      runRow.final_reconciliation_id !== null &&
      (this.database
        .query<CountRow, [string, string]>(
          `SELECT COUNT(*) AS count FROM reconciliations
           WHERE id = ? AND run_id = ? AND attempt_count >= 30
             AND complete = 1 AND sealed = 1
             AND scope = 'final'`,
        )
        .get(runRow.final_reconciliation_id, runId)?.count ?? 0) === 1;
    return {
      run: runFromRow(runRow),
      durable,
      planned,
      provisional,
      remainingCapacity: DAILY_TARGET - durable - planned - provisional,
      bySource,
      finalReconciliation,
    };
  }

  reportBytes(runId: string): Uint8Array {
    const projection = this.projection(runId);
    const attempts = this.attempts(runId).map((attempt) => ({
      canonicalIdentity: canonicalIdentity(attempt),
      id: attempt.id,
      sourceId: attempt.source_id,
      state: attempt.state,
    }));
    return new TextEncoder().encode(stableJson({ attempts, projection }));
  }

  private controllerCandidate(
    sourceId: SourceId,
    name: string,
    salesLeadId: string | undefined,
  ): NetworkCandidate | null {
    if (salesLeadId === undefined || !/^[A-Za-z0-9_-]+$/.test(salesLeadId)) return null;
    const source = required(
      SOURCES.find((item) => item.id === sourceId),
      "source contract not found",
    );
    return {
      sourceName: source.name,
      savedSearchId: source.savedSearchId,
      searchUrl: `https://www.linkedin.com/sales/search/people?savedSearchId=${source.savedSearchId}`,
      salesLeadUrl: `https://www.linkedin.com/sales/lead/${salesLeadId}`,
      salesLeadId,
      name,
      rowIdentity: `urn:li:fs_salesProfile:${salesLeadId}`,
    };
  }

  private candidateForAttempt(attempt: AttemptRow): NetworkCandidate {
    if (attempt.reservoir_entry_id !== null) {
      const row = this.database
        .query<
          {
            controller_candidate_json: string | null;
            observed_name: string;
            sales_nav_id: string | null;
            source_id: SourceId;
          },
          [string]
        >(
          `SELECT observation.controller_candidate_json, observation.observed_name,
                  person.sales_nav_id, observation.source_id
           FROM reservoir_entries reservoir
           JOIN source_observations observation ON observation.id = reservoir.observation_id
           JOIN people person ON person.id = reservoir.person_id
           WHERE reservoir.id = ?`,
        )
        .get(attempt.reservoir_entry_id);
      if (row === null || row.controller_candidate_json === null || row.sales_nav_id === null) {
        throw new Error("attempt lacks durable controller candidate evidence");
      }
      const expected = required(
        this.controllerCandidate(row.source_id, row.observed_name, row.sales_nav_id),
        "invalid durable controller candidate",
      );
      sameStoredPayload(
        JSON.parse(row.controller_candidate_json),
        expected,
        "controller candidate",
      );
      return expected;
    }
    const evidenceRaw = attempt.possible_evidence_json ?? attempt.evidence;
    const evidence = JSON.parse(evidenceRaw) as {
      candidate?: { name?: string; salesLeadId?: string };
    };
    if (
      evidence.candidate !== undefined &&
      typeof evidence.candidate.name === "string" &&
      typeof evidence.candidate.salesLeadId === "string"
    ) {
      const expected = required(
        this.controllerCandidate(
          attempt.source_id,
          evidence.candidate.name,
          evidence.candidate.salesLeadId,
        ),
        "invalid walk controller candidate",
      );
      sameStoredPayload(evidence.candidate, expected, "walk controller candidate");
      return expected;
    }
    throw new Error("attempt has no reservoir or walk candidate evidence");
  }

  private preparationEvidence(
    attempt: AttemptRow,
    attemptedAt: string,
    evidence: unknown,
    receiptId: string,
  ): { prepareBindingJson: string | null; prepareReceiptJson: string | null } {
    if (typeof evidence !== "object" || evidence === null || Array.isArray(evidence)) {
      return { prepareBindingJson: null, prepareReceiptJson: null };
    }
    const record = evidence as Record<string, unknown>;
    if (!("prepareReceipt" in record)) {
      return { prepareBindingJson: null, prepareReceiptJson: null };
    }
    if (Object.keys(record).length !== 1) {
      throw new Error("preparation evidence must contain only the exact prepare receipt");
    }
    const candidate = this.candidateForAttempt(attempt);
    const receipt = parsePrepareSendReceipt(record.prepareReceipt, {
      attemptId: attempt.id,
      candidate,
    });
    if (receipt.receiptId !== receiptId || receipt.preparedAt !== attemptedAt) {
      throw new Error("possible transition does not match the prepare receipt");
    }
    const sourceRow = this.database
      .query<
        {
          identity_evidence_json: string;
          invocation_id: string;
          observation_id: string;
          row_order: number;
          source_contract_version: number;
        },
        [string]
      >(
        `SELECT observation.id AS observation_id, observation.invocation_id,
                observation.row_order, observation.identity_evidence_json,
                observation.source_contract_version
         FROM reservoir_entries reservoir
         JOIN source_observations observation ON observation.id = reservoir.observation_id
         WHERE reservoir.id = ?`,
      )
      .get(required(attempt.reservoir_entry_id, "attempt has no reservoir entry"));
    const binding = {
      candidate,
      modalControl: {
        modalRole: "dialog",
        sendControlExactName: "Send",
        sendControlRole: "button",
      },
      prepareReceiptId: receipt.receiptId,
      sourceRow: {
        identityEvidence: JSON.parse(
          required(sourceRow, "source row evidence missing").identity_evidence_json,
        ),
        invocationId: required(sourceRow, "source row evidence missing").invocation_id,
        observationId: required(sourceRow, "source row evidence missing").observation_id,
        rowOrder: required(sourceRow, "source row evidence missing").row_order,
        sourceContractVersion: required(sourceRow, "source row evidence missing")
          .source_contract_version,
      },
    };
    return {
      prepareBindingJson: stableJson(binding),
      prepareReceiptJson: stableJson(receipt),
    };
  }

  private recordCausal(
    kind: string,
    receiptId: string,
    payload: unknown,
    label: string,
  ): { sequence: number; replay: boolean } {
    const existing = this.database
      .query<CausalRecord, [string, string]>(
        "SELECT sequence, payload_json FROM causal_records WHERE kind = ? AND receipt_id = ?",
      )
      .get(kind, receiptId);
    if (existing !== null) {
      sameStoredPayload(JSON.parse(existing.payload_json), payload, label);
      return { sequence: existing.sequence, replay: true };
    }
    const inserted = this.database
      .query("INSERT INTO causal_records (kind, receipt_id, payload_json) VALUES (?, ?, ?)")
      .run(kind, receiptId, stableJson(payload));
    return { sequence: Number(inserted.lastInsertRowid), replay: false };
  }

  private applySealedReconciliationConfirmation(input: {
    attemptId: string;
    auditId: string;
    baselineId: string | null;
    evidenceKind: ReconciliationEvidenceKind;
    reconciliationId: string;
    scope: ReconciliationScope;
    resolvedAt: string;
  }): void {
    const attempt = this.attempt(input.attemptId);
    const evidence = {
      auditId: input.auditId,
      baselineId: input.baselineId,
      evidenceKind: input.evidenceKind,
      reconciliationId: input.reconciliationId,
      reconciliationScope: input.scope,
    };
    const receiptId = `durable:${input.attemptId}`;
    const authorized =
      input.baselineId === null
        ? this.database
            .query<{ ok: number }, [string, string, string, string, string, string]>(
              `SELECT 1 AS ok
               FROM reconciliations rec
               JOIN reconciliation_attempts child
                 ON child.reconciliation_id = rec.id AND child.attempt_id = ?
               JOIN audit_snapshots audit ON audit.id = rec.audit_id
               JOIN json_each(rec.confirmed_attempt_ids_json) confirmed
                 ON confirmed.value = child.attempt_id
               WHERE rec.id = ? AND rec.run_id = ? AND rec.audit_id = ? AND rec.baseline_id IS NULL
                 AND rec.sealed = 1 AND child.evidence_kind = ? AND rec.scope = ?
                 AND audit.run_id = rec.run_id`,
            )
            .get(
              input.attemptId,
              input.reconciliationId,
              attempt.run_id,
              input.auditId,
              input.evidenceKind,
              input.scope,
            )
        : this.database
            .query<{ ok: number }, [string, string, string, string, string, string, string]>(
              `SELECT 1 AS ok
               FROM reconciliations rec
               JOIN reconciliation_attempts child
                 ON child.reconciliation_id = rec.id AND child.attempt_id = ?
               JOIN audit_snapshots audit ON audit.id = rec.audit_id
               JOIN audit_baselines baseline ON baseline.id = rec.baseline_id
               JOIN json_each(rec.confirmed_attempt_ids_json) confirmed
                 ON confirmed.value = child.attempt_id
               WHERE rec.id = ? AND rec.run_id = ? AND rec.audit_id = ? AND rec.baseline_id = ?
                 AND rec.sealed = 1 AND child.evidence_kind = ? AND rec.scope = ?
                 AND audit.run_id = rec.run_id AND audit.baseline_id = baseline.id
                 AND baseline.run_id = rec.run_id`,
            )
            .get(
              input.attemptId,
              input.reconciliationId,
              attempt.run_id,
              input.auditId,
              input.baselineId,
              input.evidenceKind,
              input.scope,
            );
    if (authorized === null) {
      throw new Error("durable attempt lacks an exact sealed reconciliation authorization");
    }
    const payload = {
      attemptId: input.attemptId,
      evidenceJson: stableJson(evidence),
      personId: attempt.person_id,
      receiptId,
      resolvedAt: input.resolvedAt,
      runId: attempt.run_id,
      sourceId: attempt.source_id,
      state: "durable",
    };
    if (attempt.resolution_causal_sequence !== null) {
      const causal = this.recordCausal("attempt_durable", receiptId, payload, "resolution");
      if (
        attempt.resolution_causal_sequence !== causal.sequence ||
        attempt.resolution_receipt_key !== receiptId ||
        attempt.state !== "durable"
      ) {
        throw new Error("resolution receipt replay payload mismatch");
      }
      return;
    }
    if (attempt.state !== "possible") throw new Error("only possible attempts can be resolved");
    const causal = this.recordCausal("attempt_durable", receiptId, payload, "resolution");
    this.database
      .query(
        `UPDATE send_attempts SET state = 'durable', resolved_at = ?,
         resolution_receipt_key = ?, resolution_causal_sequence = ?, evidence = ?
         WHERE id = ? AND state = 'possible'`,
      )
      .run(input.resolvedAt, receiptId, causal.sequence, payload.evidenceJson, input.attemptId);
    this.database
      .query(
        `INSERT INTO relationship_facts
         (id, person_id, kind, effective_at, run_id, evidence)
         VALUES (?, ?, 'pending', ?, ?, ?)`,
      )
      .run(
        `pending:${input.attemptId}`,
        attempt.person_id,
        input.resolvedAt,
        attempt.run_id,
        payload.evidenceJson,
      );
    emit(
      this.database,
      "send_durable",
      { attemptId: input.attemptId },
      input.resolvedAt,
      attempt.run_id,
      `attempt:${input.attemptId}:durable`,
    );
  }

  private resolveProvenNoSend(
    attemptId: string,
    resolvedAt: string,
    evidence: unknown,
    receiptId: string = `proven_no_send:${attemptId}`,
  ): void {
    inTransaction(this.database, () => {
      const attempt = this.attempt(attemptId);
      const state = "proven_no_send" as const;
      const payload = {
        attemptId,
        evidenceJson: stableJson(evidence),
        personId: attempt.person_id,
        receiptId,
        resolvedAt,
        runId: attempt.run_id,
        sourceId: attempt.source_id,
        state,
      };
      if (attempt.resolution_causal_sequence !== null) {
        const causal = this.recordCausal(`attempt_${state}`, receiptId, payload, "resolution");
        if (
          attempt.resolution_causal_sequence !== causal.sequence ||
          attempt.resolution_receipt_key !== receiptId ||
          attempt.state !== state
        ) {
          throw new Error("resolution receipt replay payload mismatch");
        }
        return;
      }
      this.requireActiveRun(attempt.run_id);
      if (attempt.state !== "possible" && attempt.state !== "planned")
        throw new Error("only planned or possible attempts can be resolved");
      const causal = this.recordCausal(`attempt_${state}`, receiptId, payload, "resolution");
      this.database
        .query(
          `UPDATE send_attempts SET state = ?, resolved_at = ?, resolution_receipt_key = ?,
           resolution_causal_sequence = ?, evidence = ? WHERE id = ? AND state IN ('planned', 'possible')`,
        )
        .run(state, resolvedAt, receiptId, causal.sequence, stableJson(evidence), attemptId);
      this.database
        .query(
          `INSERT INTO relationship_facts
           (id, person_id, kind, effective_at, run_id, evidence)
           VALUES (?, ?, 'proven_no_send', ?, ?, ?)`,
        )
        .run(
          `proven-no-send:${attemptId}`,
          attempt.person_id,
          resolvedAt,
          attempt.run_id,
          stableJson(evidence),
        );
      this.database
        .query(
          `UPDATE reservoir_entries SET status = 'ineligible'
           WHERE run_id = ? AND person_id = ?`,
        )
        .run(attempt.run_id, attempt.person_id);
      emit(
        this.database,
        "send_proven_no_send",
        { attemptId },
        resolvedAt,
        attempt.run_id,
        `attempt:${attemptId}:${state}`,
      );
    });
  }

  private attemptIdentitySet(attempt: AttemptRow): string[] {
    const values = [attempt.sales_nav_id, attempt.public_url, attempt.lead_key].filter(
      (value): value is string => typeof value === "string" && value.trim().length > 0,
    );
    return [...new Set(values)];
  }

  private parseIdentityList(raw: string | null | undefined): string[] {
    if (raw === null || raw === undefined || raw === "") return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (value): value is string => typeof value === "string" && value.trim().length > 0,
    );
  }

  private computeCompetingSenderAbsent(
    runId: string,
    baselineIdentities: readonly string[],
    auditIdentities: readonly string[],
  ): boolean {
    if (auditIdentities.length === 0) return false;
    const baselineSet = new Set(baselineIdentities);
    const newIdentities = auditIdentities.filter((identity) => !baselineSet.has(identity));
    if (newIdentities.length === 0) return true;
    const attemptIdentityUniverse = new Set<string>();
    for (const attempt of this.attempts(runId)) {
      if (!["planned", "possible", "durable"].includes(attempt.state)) continue;
      for (const identity of this.attemptIdentitySet(attempt))
        attemptIdentityUniverse.add(identity);
    }
    if (attemptIdentityUniverse.size === 0) return false;
    return newIdentities.every((identity) => attemptIdentityUniverse.has(identity));
  }

  private exactMatchKind(
    attempt: AttemptRow,
    auditIdentities: Set<string>,
    auditNameCounts: Map<string, number>,
    attemptNameCounts: Map<string, number>,
  ): "identity" | "name" | null {
    const shared = this.attemptIdentitySet(attempt).filter((identity) =>
      auditIdentities.has(identity),
    );
    if (shared.length > 0) return "identity";
    const name = this.normalizedName(attempt.name);
    if (attemptNameCounts.get(name) === 1 && auditNameCounts.get(name) === 1) return "name";
    return null;
  }

  private exactCounts(values: string[]): Map<string, number> {
    const counts = new Map<string, number>();
    for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
    return counts;
  }

  private microbatchExactMatch(
    attempt: AttemptRow,
    auditIdentityCounts: Map<string, number>,
    auditNameCounts: Map<string, number>,
    activeNameCounts: Map<string, number>,
  ): { kind: "identity" | "name"; matchedValue: string } | null {
    const uniqueShared = this.attemptIdentitySet(attempt).filter(
      (identity) => auditIdentityCounts.get(identity) === 1,
    );
    if (uniqueShared.length === 1) {
      return { kind: "identity", matchedValue: uniqueShared[0]! };
    }
    if (activeNameCounts.get(attempt.name) === 1 && auditNameCounts.get(attempt.name) === 1) {
      return { kind: "name", matchedValue: attempt.name };
    }
    return null;
  }

  private attempts(runId: string): AttemptRow[] {
    return this.database
      .query<AttemptRow, [string]>(
        `SELECT a.*, p.sales_nav_id, p.public_url, p.lead_key, p.name
         FROM send_attempts a JOIN people p ON p.id = a.person_id
         WHERE a.run_id = ?
         ORDER BY COALESCE(p.sales_nav_id, p.public_url, p.lead_key), a.id`,
      )
      .all(runId);
  }

  private attemptOrNull(id: string): AttemptRow | null {
    return this.database
      .query<AttemptRow, [string]>(
        `SELECT a.*, p.sales_nav_id, p.public_url, p.lead_key, p.name
         FROM send_attempts a JOIN people p ON p.id = a.person_id WHERE a.id = ?`,
      )
      .get(id);
  }

  private attempt(id: string): AttemptRow {
    return required(this.attemptOrNull(id), "attempt not found");
  }

  private attemptCount(runId: string): number {
    return (
      this.database
        .query<CountRow, [string]>("SELECT COUNT(*) AS count FROM send_attempts WHERE run_id = ?")
        .get(runId)?.count ?? 0
    );
  }

  private baseline(id: string): BaselineRow {
    return required(
      this.database
        .query<BaselineRow, [string]>("SELECT * FROM audit_baselines WHERE id = ?")
        .get(id),
      "audit baseline not found",
    );
  }

  private auditOrNull(id: string): AuditRow | null {
    return this.database
      .query<AuditRow, [string]>("SELECT * FROM audit_snapshots WHERE id = ?")
      .get(id);
  }

  private remainingCapacity(runId: string): number {
    const reserved =
      this.database
        .query<CountRow, [string]>(
          `SELECT COUNT(*) AS count FROM send_attempts
           WHERE run_id = ? AND state IN ('planned', 'possible', 'durable')`,
        )
        .get(runId)?.count ?? 0;
    return DAILY_TARGET - reserved;
  }

  private missedRunProjection(run: RunRow): ParkedMissedRun {
    const states = this.database
      .query<{ count: number; state: AttemptState }, [string]>(
        `SELECT state, COUNT(*) AS count
         FROM send_attempts WHERE run_id = ? GROUP BY state`,
      )
      .all(run.id);
    const count = (state: AttemptState): number =>
      states.find((row) => row.state === state)?.count ?? 0;
    return {
      runId: run.id,
      localDate: run.local_date,
      durable: count("durable"),
      planned: count("planned"),
      provisional: count("possible"),
    };
  }

  private runRow(id: string): RunRow {
    return required(
      this.database.query<RunRow, [string]>("SELECT * FROM daily_runs WHERE id = ?").get(id),
      "run not found",
    );
  }

  private requireActiveRun(id: string): RunRow {
    const run = this.runRow(id);
    if (run.status !== "active") throw new Error("active run required");
    return run;
  }

  private nameCounts(names: string[]): Map<string, number> {
    const counts = new Map<string, number>();
    for (const name of names) {
      const normalized = this.normalizedName(name);
      counts.set(normalized, (counts.get(normalized) ?? 0) + 1);
    }
    return counts;
  }

  private normalizedName(name: string): string {
    return name.trim().replace(/\s+/g, " ").toLocaleLowerCase();
  }

  private assertSource(sourceId: string): asserts sourceId is SourceId {
    if (!SOURCES.some((source) => source.id === sourceId)) throw new Error("unknown source");
  }

  /**
   * Persist the reason a tick stopped so status/report can answer "why did it
   * stop" without re-parsing truncated terminal output. The payload is the
   * full checkpoint or terminal object; the latest event per run wins.
   */
  recordTickStop(
    runId: string,
    state: "checkpoint" | "terminal",
    payload: unknown,
    now: string,
    id: string = crypto.randomUUID(),
  ): void {
    emit(this.database, "tick_stopped", { state, payload }, now, runId, `tick:${id}:stopped`);
  }

  latestTickStop(runId: string): {
    readonly state: "checkpoint" | "terminal";
    readonly payload: unknown;
    readonly occurredAt: string;
  } | null {
    const row = this.database
      .query<{ payload_json: string; occurred_at: string }, [string]>(
        `SELECT payload_json, occurred_at FROM events
         WHERE run_id = ? AND type = 'tick_stopped'
         ORDER BY sequence DESC LIMIT 1`,
      )
      .get(runId);
    if (row === null) return null;
    try {
      const value = JSON.parse(row.payload_json) as {
        readonly state: "checkpoint" | "terminal";
        readonly payload: unknown;
      };
      if (value.state !== "checkpoint" && value.state !== "terminal") return null;
      return { state: value.state, payload: value.payload, occurredAt: row.occurred_at };
    } catch {
      return null;
    }
  }
}
