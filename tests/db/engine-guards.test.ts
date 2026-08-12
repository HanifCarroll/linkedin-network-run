import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { PriorDayNeedsAuditError } from "../../src/network/engine.ts";
import { completeRun, NOW, recordBaseline, setup, walkRows, walkSend } from "../network/helpers.ts";

describe("engine-level invariant guards", () => {
  test("endRun blocks an active run with unresolved sends and lets the next day start", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    walkSend(engine, runId, "hubspot-agency-ops", "planned-1", "Planned Person");

    expect(() => engine.prepareDailyRun("2026-08-04", NOW)).toThrow(PriorDayNeedsAuditError);

    const ended = engine.endRun("2026-08-03", "ended for test", "2026-08-03T14:00:00Z");
    expect(ended.status).toBe("blocked");

    const next = engine.prepareDailyRun("2026-08-04", NOW);
    expect(next.run.localDate).toBe("2026-08-04");
    expect(next.run.status).toBe("active");
    expect(next.parkedRuns).toHaveLength(0);

    const events = database
      .query<{ type: string; payload_json: string }, []>(
        "SELECT type, payload_json FROM events WHERE type = 'daily_run_ended'",
      )
      .all();
    expect(events).toHaveLength(1);
    const payload = JSON.parse(events[0]?.payload_json ?? "{}");
    expect(payload).toMatchObject({
      localDate: "2026-08-03",
      reason: "ended for test",
      provisional: 1,
    });
    database.close();
  });

  test("endRun rejects missing and non-active runs", () => {
    const { database, engine, runId } = setup();
    expect(() => engine.endRun("1999-01-01", "nope", NOW)).toThrow(/no daily run/);

    completeRun(engine, runId);
    expect(() => engine.endRun("2026-08-03", "already done", NOW)).toThrow(/not active/);
    database.close();
  });
  test("(a) send capacity rejects the 31st walk reservation", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    const first = engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      { sent: walkRows("agency", 20), skipped: [] },
      NOW,
    );
    const second = engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      { sent: walkRows("coo", 20), skipped: [] },
      NOW,
    );
    expect(first.sent + second.sent).toBe(30);
    expect(engine.projection(runId)).toMatchObject({
      provisional: 30,
      remainingCapacity: 0,
    });
    expect(
      engine.recordWalkSends(
        runId,
        "hubspot-agency-ops",
        {
          sent: [{ rowIdentity: "urn:li:fs_salesProfile:overflow", name: "Overflow" }],
          skipped: [],
        },
        NOW,
      ).sent,
    ).toBe(0);
    expect(
      database
        .query<{ count: number }, [string]>(
          `SELECT COUNT(*) AS count FROM send_attempts
           WHERE run_id = ? AND state IN ('planned', 'possible', 'durable')`,
        )
        .get(runId)?.count,
    ).toBe(30);
    database.close();
  });

  test("(b) walk hard-caps at 30 remaining capacity for the run", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      { sent: walkRows("agency", 30), skipped: [] },
      NOW,
    );
    expect(engine.projection(runId).provisional).toBe(30);
    const overflow = engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      { sent: walkRows("coo", 5), skipped: [] },
      NOW,
    );
    expect(overflow.sent).toBe(0);
    expect(engine.projection(runId).remainingCapacity).toBe(0);
    database.close();
  });

  test("(c) legal send_attempt transitions only for walk possibles", () => {
    const { database, engine, runId } = setup();
    const attemptId = walkSend(engine, runId, "hubspot-agency-ops", "lead-1", "Person 1");

    expect(() => engine.refreshPreparation(attemptId, NOW, { receipt: "prep" }, "prep-1")).toThrow(
      /requires exact prepare receipt evidence|commit already started/,
    );

    // Walk already created possible; markPossible requires planned.
    expect(() =>
      engine.markPossible(attemptId, NOW, { receipt: "again" }, "possible-again"),
    ).toThrow(/attempt must be planned|possible receipt replay payload mismatch/);

    engine.markProvenNoSend(attemptId, NOW, { reason: "no-send" });
    expect(
      database
        .query<{ state: string }, [string]>("SELECT state FROM send_attempts WHERE id = ?")
        .get(attemptId)?.state,
    ).toBe("proven_no_send");

    expect(() =>
      engine.markPossible(attemptId, NOW, { receipt: "after-proven" }, "after-proven"),
    ).toThrow(/attempt must be planned|payload mismatch/);
    database.close();
  });

  test("(c) commit requires possible preparation receipt", () => {
    const { database, engine, runId } = setup();
    const attemptId = walkSend(engine, runId, "hubspot-agency-ops", "lead-1", "Person 1");
    expect(() =>
      engine.recordCommitStarted({
        attemptId,
        runId,
        startedAt: NOW,
        receipt: {
          receiptId: "missing",
          preparedAt: NOW,
          candidateBinding: "x",
          composeReady: true,
        } as never,
      }),
    ).toThrow(/before durable preparation|commit-start receipt/);
    database.close();
  });

  test("(d) finish requires durable 30, zero provisional/planned, sealed final reconciliation", () => {
    const { database, engine, runId } = setup();
    walkSend(engine, runId, "hubspot-agency-ops", "lead-1", "Person 1");
    expect(() => engine.finish(runId, NOW)).toThrow(
      /cannot finish without exactly 30 durable, zero reservations, and exact final reconciliation/,
    );
    database.close();
  });

  test("(d) mutations reject non-active runs", () => {
    const { database, engine, runId } = setup();
    completeRun(engine, runId);
    expect(engine.projection(runId).run.status).toBe("done");
    expect(() =>
      engine.recordWalkSends(
        runId,
        "hubspot-agency-ops",
        {
          sent: [{ rowIdentity: "urn:li:fs_salesProfile:after-done", name: "After" }],
          skipped: [],
        },
        NOW,
      ),
    ).toThrow(/active run required/);
    expect(() =>
      engine.recordBaseline({
        id: "late-baseline",
        runId,
        invocationId: "late",
        peopleCount: 1,
        competingSenderAbsent: true,
        capturedAt: NOW,
      }),
    ).toThrow(/active run required/);
    database.close();
  });

  test("(e)(f) causal receipt replay is idempotent; conflicting replay is rejected", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, "replay-baseline");
    engine.recordBaseline({
      id: baselineId,
      runId,
      invocationId: "replay-baseline-invocation",
      peopleCount: 100,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T11:00:00Z",
    });
    expect(() =>
      engine.recordBaseline({
        id: baselineId,
        runId,
        invocationId: "replay-baseline-invocation",
        peopleCount: 101,
        competingSenderAbsent: true,
        capturedAt: "2026-08-03T11:00:00Z",
      }),
    ).toThrow(/payload mismatch/);

    const attemptId = walkSend(engine, runId, "hubspot-agency-ops", "replay-lead", "Replay");
    const before =
      database.query<{ count: number }, []>("SELECT COUNT(*) AS count FROM events").get()?.count ??
      0;
    // Replaying the same walk row is a no-op (existing attempt for person).
    expect(
      engine.recordWalkSends(
        runId,
        "hubspot-agency-ops",
        {
          sent: [
            {
              rowIdentity: "urn:li:fs_salesProfile:replay-lead",
              name: "Replay",
            },
          ],
          skipped: [],
        },
        NOW,
      ).sent,
    ).toBe(0);
    expect(
      database.query<{ count: number }, []>("SELECT COUNT(*) AS count FROM events").get()?.count ??
        0,
    ).toBe(before);
    expect(
      database
        .query<{ count: number }, [string]>(
          "SELECT COUNT(*) AS count FROM send_attempts WHERE id = ?",
        )
        .get(attemptId)?.count,
    ).toBe(1);

    engine.markProvenNoSend(attemptId, NOW, { receipt: "p" }, "proven-replay");
    engine.markProvenNoSend(attemptId, NOW, { receipt: "p" }, "proven-replay");
    expect(() =>
      engine.markProvenNoSend(attemptId, NOW, { receipt: "other" }, "proven-other"),
    ).toThrow(/payload mismatch/);
    database.close();
  });

  test("(e) poisoned event dedupe keys abort the surrounding transaction", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    // Poison the walk possible event key that would be used for a fresh attempt id is unknown;
    // instead poison baseline-style path is less useful. Poison a known walk event pattern by
    // pre-inserting after generating a deterministic conflict on relationship fact emission.
    database
      .query(
        `INSERT INTO events (id, run_id, type, payload_json, occurred_at, dedupe_key)
         VALUES (?, ?, 'poisoned', '{}', ?, ?)`,
      )
      .run(
        "poison-walk-event",
        runId,
        NOW,
        "relationship-fact:walk-skip:run-today:urn:li:fs_salesProfile:poison-skip:already_pending",
      );

    expect(() =>
      engine.recordWalkSends(
        runId,
        "hubspot-agency-ops",
        {
          sent: [],
          skipped: [
            {
              rowIdentity: "urn:li:fs_salesProfile:poison-skip",
              name: "Poison",
              reason: "already_pending",
            },
          ],
        },
        NOW,
      ),
    ).toThrow(/event receipt conflict/);
    expect(
      database
        .query<{ count: number }, []>(
          "SELECT COUNT(*) AS count FROM relationship_facts WHERE id LIKE 'walk-skip:%'",
        )
        .get()?.count,
    ).toBe(0);
    database.close();
  });

  test("(f) audit must follow baseline causally; baseline requires zero attempts", () => {
    const { database, engine, runId } = setup();
    walkSend(engine, runId, "hubspot-agency-ops", "before-baseline", "Too Early");
    expect(() => recordBaseline(engine, runId, 100, "too-late")).toThrow(
      /baseline must be captured before send reservations/,
    );

    const fresh = setup();
    const baselineId = recordBaseline(fresh.engine, fresh.runId, 10, "order-baseline");
    walkSend(fresh.engine, fresh.runId, "hubspot-agency-ops", "order-lead", "Order Person");
    fresh.engine.recordAudit({
      id: "order-audit",
      invocationId: "order-audit-inv",
      runId: fresh.runId,
      baselineId,
      peopleCount: 11,
      identities: ["order-lead"],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(() =>
      fresh.engine.reconcile(
        fresh.runId,
        "missing-baseline",
        "order-audit",
        NOW,
        "bad-rec",
        "microbatch",
      ),
    ).toThrow();
    fresh.database.close();
    database.close();
  });

  test("(g) engine mutation paths never UPDATE/DELETE append-only tables", () => {
    const engineSource = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../../src/network/engine.ts"),
      "utf8",
    );
    expect(engineSource).not.toMatch(/DELETE\s+FROM\s+events/i);
    expect(engineSource).not.toMatch(/UPDATE\s+events\s+SET/i);
    expect(engineSource).not.toMatch(/DELETE\s+FROM\s+source_observations/i);
    expect(engineSource).not.toMatch(/UPDATE\s+source_observations\s+SET/i);
    expect(engineSource).not.toMatch(/DELETE\s+FROM\s+reconciliations/i);
    expect(engineSource).not.toMatch(/DELETE\s+FROM\s+reservoir_entries/i);
    const reconciliationUpdates = [
      ...engineSource.matchAll(/UPDATE\s+reconciliations\s+SET\s+([^;]+)/gi),
    ].map((match) => match[1] ?? "");
    expect(reconciliationUpdates.length).toBeGreaterThan(0);
    for (const clause of reconciliationUpdates) {
      expect(clause).toMatch(/sealed\s*=\s*1/);
      expect(clause).not.toMatch(/\bmode\b|\battempt_count\b|\bcomplete\b|\bscope\b/);
    }
  });

  test("(h) exact-30 final evidence is required before Done", () => {
    const { database, engine, runId } = setup();
    const finished = completeRun(engine, runId);
    expect(engine.projection(runId)).toMatchObject({
      durable: 30,
      planned: 0,
      provisional: 0,
      remainingCapacity: 0,
    });
    expect(engine.projection(runId).run.status).toBe("done");
    expect(engine.projection(runId).finalReconciliation).toBe(true);
    const rec = database
      .query<
        {
          attempt_count: number;
          complete: number;
          competing_sender_absent: number;
          scope: string;
          sealed: number;
        },
        [string]
      >(
        `SELECT attempt_count, complete, competing_sender_absent, scope, sealed
         FROM reconciliations WHERE id = ?`,
      )
      .get(finished.reconciliationId);
    expect(rec).toEqual({
      attempt_count: 30,
      complete: 1,
      competing_sender_absent: 1,
      scope: "final",
      sealed: 1,
    });
    database.close();
  });
});
