import type { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { NetworkEngine } from "../../src/network/engine.ts";
import { NOW, recordBaseline, setup, walkRows, walkSend } from "./helpers.ts";

describe("scoped microbatch reconciliation", () => {
  test("confirms an exact unique sent-list name when the list count is unchanged", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 483, "unchanged-count-baseline");
    const attemptId = walkSend(
      engine,
      runId,
      "hubspot-agency-ops",
      "george-rekouts",
      "George Rekouts",
    );
    engine.recordAudit({
      id: "unchanged-count-audit",
      invocationId: "unchanged-count-audit-invocation",
      runId,
      baselineId,
      peopleCount: 483,
      identities: [],
      names: ["George Rekouts"],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });

    expect(
      engine.reconcile(
        runId,
        baselineId,
        "unchanged-count-audit",
        "2026-08-03T13:01:00Z",
        "unchanged-count-reconciliation",
        "microbatch",
      ),
    ).toEqual([attemptId]);
    expect(engine.projection(runId)).toMatchObject({
      durable: 1,
      provisional: 0,
      finalReconciliation: false,
    });
    database.close();
  });

  test("promotes only exact evidence, replays event-free, and cannot finish", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, "batch-baseline");
    const exact = walkSend(engine, runId, "hubspot-agency-ops", "exact-person", "Exact Person");
    walkSend(engine, runId, "hubspot-agency-ops", "missing-person", "Missing Person");
    engine.recordAudit({
      id: "batch-audit",
      invocationId: "batch-audit-invocation",
      runId,
      baselineId,
      peopleCount: 101,
      identities: ["exact-person"],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });

    expect(
      engine.reconcile(
        runId,
        baselineId,
        "batch-audit",
        "2026-08-03T13:01:00Z",
        "batch-reconciliation",
        "microbatch",
      ),
    ).toEqual([exact]);
    const events = eventCount(database);
    expect(
      engine.reconcile(
        runId,
        baselineId,
        "batch-audit",
        "2026-08-03T13:01:00Z",
        "batch-reconciliation",
        "microbatch",
      ),
    ).toEqual([exact]);
    expect(eventCount(database)).toBe(events);
    expect(engine.projection(runId)).toMatchObject({
      durable: 1,
      provisional: 1,
      finalReconciliation: false,
    });
    expect(() => engine.finish(runId, NOW)).toThrow("cannot finish");
    expect(
      database
        .query<{ scope: string }, []>(
          "SELECT scope FROM reconciliations WHERE id = 'batch-reconciliation'",
        )
        .get()?.scope,
    ).toBe("microbatch");
    database.close();
  });

  test("aggregate-only microbatch evidence stays unsealed and leaves the audit pending", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, "aggregate-baseline");
    walkSend(engine, runId, "hubspot-agency-ops", "aggregate-1", "Aggregate Person");
    engine.recordAudit({
      id: "aggregate-audit",
      invocationId: "aggregate-audit-invocation",
      runId,
      baselineId,
      peopleCount: 101,
      identities: [],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(
      engine.reconcile(
        runId,
        baselineId,
        "aggregate-audit",
        NOW,
        "aggregate-reconciliation",
        "microbatch",
      ),
    ).toEqual([]);
    expect(engine.readControllerState(runId).pendingAudit?.id).toBe("aggregate-audit");
    expect(
      database
        .query<{ count: number }, []>(
          "SELECT COUNT(*) AS count FROM reconciliations WHERE sealed = 1",
        )
        .get()?.count,
    ).toBe(0);
    database.close();
  });

  test.each([
    {
      label: "missing",
      names: [] as string[],
      complete: true,
      competing: true,
      contradictory: false,
    },
    {
      label: "ambiguous-name",
      names: ["Same Name", "Same Name"],
      complete: true,
      competing: true,
      contradictory: false,
    },
    {
      label: "incomplete",
      names: ["Same Name"],
      complete: false,
      competing: true,
      contradictory: false,
    },
    {
      label: "competing",
      names: ["Same Name"],
      complete: true,
      competing: false,
      contradictory: false,
    },
    {
      label: "contradictory",
      names: ["Same Name"],
      complete: true,
      competing: true,
      contradictory: true,
    },
  ])("rejects $label audit evidence", (scenario) => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, `${scenario.label}-baseline`);
    const attemptId = walkSend(
      engine,
      runId,
      "hubspot-agency-ops",
      `${scenario.label}-lead`,
      "Same Name",
    );
    engine.recordAudit({
      id: `${scenario.label}-audit`,
      invocationId: `${scenario.label}-audit-invocation`,
      runId,
      baselineId,
      peopleCount: 101,
      identities: [],
      names: [...scenario.names],
      complete: scenario.complete,
      competingSenderAbsent: scenario.competing,
      contradictoryEvidence: scenario.contradictory,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    const confirmed = engine.reconcile(
      runId,
      baselineId,
      `${scenario.label}-audit`,
      NOW,
      `${scenario.label}-reconciliation`,
      "microbatch",
    );
    if (scenario.label === "competing") {
      expect(confirmed).toEqual([attemptId]);
      expect(engine.projection(runId).provisional).toBe(0);
    } else {
      expect(confirmed).toEqual([]);
      expect(engine.projection(runId).provisional).toBe(1);
      expect(engine.readControllerState(runId).pendingAudit?.id).toBe(`${scenario.label}-audit`);
    }
    database.close();
  });

  test("rejects an audit that causally predates any open possible attempt", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, "stale-batch-baseline");
    walkSend(engine, runId, "hubspot-agency-ops", "stale-1", "First");
    engine.recordAudit({
      id: "stale-batch-audit",
      invocationId: "stale-batch-audit-invocation",
      runId,
      baselineId,
      peopleCount: 102,
      identities: ["stale-1", "stale-2"],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    walkSend(engine, runId, "hubspot-agency-ops", "stale-2", "Second");
    expect(
      engine.reconcile(
        runId,
        baselineId,
        "stale-batch-audit",
        NOW,
        "stale-batch-reconciliation",
        "microbatch",
      ),
    ).toEqual([]);
    expect(engine.projection(runId)).toMatchObject({ durable: 0, provisional: 2 });
    database.close();
  });

  test("six five-send microbatches plus one final reconciliation reach Done", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, "six-batch-baseline");
    const agency = walkRows("agency", 15, "Agency");
    const coo = walkRows("coo", 15, "Coo");
    const all = [...agency, ...coo];
    for (let batch = 0; batch < 6; batch++) {
      const batchRows = all.slice(batch * 5, batch * 5 + 5);
      const sourceId =
        batch < 3 ? ("hubspot-agency-ops" as const) : ("hubspot-b2b-revops" as const);
      // Agency fills first three batches (15), COO last three (15).
      const rows =
        batch < 3
          ? agency.slice(batch * 5, batch * 5 + 5)
          : coo.slice((batch - 3) * 5, (batch - 3) * 5 + 5);
      void batchRows;
      const before = new Set(
        engine.readControllerState(runId).openAttempts.map((item) => item.attemptId),
      );
      engine.recordWalkSends(runId, sourceId, { sent: rows, skipped: [] }, NOW);
      const attemptIds = engine
        .readControllerState(runId)
        .openAttempts.map((item) => item.attemptId)
        .filter((id) => !before.has(id));
      const identities = rows.map((row) => row.rowIdentity.replace("urn:li:fs_salesProfile:", ""));
      const auditId = `six-batch-audit-${batch}`;
      engine.recordAudit({
        id: auditId,
        invocationId: `${auditId}-invocation`,
        runId,
        baselineId,
        peopleCount: 105 + batch * 5,
        identities,
        names: [],
        complete: true,
        competingSenderAbsent: true,
        capturedAt: `2026-08-03T13:0${batch}:00Z`,
      });
      expect(
        engine.reconcile(
          runId,
          baselineId,
          auditId,
          `2026-08-03T13:0${batch}:30Z`,
          `six-batch-reconciliation-${batch}`,
          "microbatch",
        ),
      ).toEqual(attemptIds.sort());
    }
    expect(engine.projection(runId)).toMatchObject({ durable: 30, finalReconciliation: false });
    const finalIdentities = all.map((row) =>
      row.rowIdentity.replace("urn:li:fs_salesProfile:", ""),
    );
    engine.recordAudit({
      id: "six-batch-final-audit",
      invocationId: "six-batch-final-audit-invocation",
      runId,
      baselineId,
      peopleCount: 130,
      identities: finalIdentities,
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T14:00:00Z",
    });
    expect(
      engine.reconcile(
        runId,
        baselineId,
        "six-batch-final-audit",
        "2026-08-03T14:01:00Z",
        "six-batch-final-reconciliation",
        "final",
      ),
    ).toEqual([]);
    expect(engine.finish(runId, "2026-08-03T14:02:00Z").status).toBe("done");
    expect(
      database
        .query<{ count: number }, []>(
          "SELECT COUNT(*) AS count FROM reconciliations WHERE scope = 'microbatch' AND sealed = 1",
        )
        .get()?.count,
    ).toBe(6);
    database.close();
  });

  test("restart replay finishes a pre-sealed microbatch without duplicate transitions", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, "restart-baseline");
    const attemptId = walkSend(
      engine,
      runId,
      "hubspot-agency-ops",
      "restart-lead",
      "Restart Person",
    );
    engine.recordAudit({
      id: "restart-audit",
      invocationId: "restart-audit-invocation",
      runId,
      baselineId,
      peopleCount: 101,
      identities: ["restart-lead"],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    const payload = {
      auditId: "restart-audit",
      attemptCount: 1,
      baselineId,
      competingSenderAbsent: true,
      evidence: [
        {
          attemptId,
          kind: "identity",
          matchedValue: "restart-lead",
        },
      ],
      finalComplete: false,
      mode: "exact",
      newlyConfirmedAttemptIds: [attemptId],
      reconciledAt: "2026-08-03T13:01:00Z",
      reconciliationId: "restart-reconciliation",
      runId,
      scope: "microbatch",
    };
    const sequence = insertCausal(database, "reconciliation", "restart-reconciliation", payload);
    database
      .query(
        `INSERT INTO reconciliations
         (id, run_id, baseline_id, audit_id, mode, attempt_count, complete,
          competing_sender_absent, confirmed_attempt_ids_json, created_at, causal_sequence, scope)
         VALUES ('restart-reconciliation', ?, ?, 'restart-audit', 'exact', 1, 0, 1, ?,
                 '2026-08-03T13:01:00Z', ?, 'microbatch')`,
      )
      .run(runId, baselineId, JSON.stringify([attemptId]), sequence);
    database
      .query(
        `INSERT INTO reconciliation_attempts
         (reconciliation_id, attempt_id, evidence_kind, matched_value)
         VALUES ('restart-reconciliation', ?, 'identity',
                 'restart-lead')`,
      )
      .run(attemptId);
    database
      .query("UPDATE reconciliations SET sealed = 1 WHERE id = 'restart-reconciliation'")
      .run();
    const beforeReplay = eventCount(database);
    const restarted = new NetworkEngine(database);
    expect(
      restarted.reconcile(
        runId,
        baselineId,
        "restart-audit",
        "2026-08-03T13:01:00Z",
        "restart-reconciliation",
        "microbatch",
      ),
    ).toEqual([attemptId]);
    expect(eventCount(database)).toBe(beforeReplay + 1);
    const afterApply = eventCount(database);
    expect(
      restarted.reconcile(
        runId,
        baselineId,
        "restart-audit",
        "2026-08-03T13:01:00Z",
        "restart-reconciliation",
        "microbatch",
      ),
    ).toEqual([attemptId]);
    expect(eventCount(database)).toBe(afterApply);
    expect(restarted.projection(runId)).toMatchObject({ durable: 1, provisional: 0 });
    database.close();
  });
});

function eventCount(database: Database): number {
  return (
    database.query<{ count: number }, []>("SELECT COUNT(*) AS count FROM events").get()?.count ?? 0
  );
}

function insertCausal(
  database: Database,
  kind: string,
  receiptId: string,
  payload: unknown,
): number {
  return Number(
    database
      .query("INSERT INTO causal_records (kind, receipt_id, payload_json) VALUES (?, ?, ?)")
      .run(kind, receiptId, JSON.stringify(payload)).lastInsertRowid,
  );
}
