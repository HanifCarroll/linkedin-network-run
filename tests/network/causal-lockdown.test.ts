import { describe, expect, test } from "bun:test";
import { NOW, recordBaseline, setup, walkRows, walkSend } from "./helpers.ts";

describe("causal audit order", () => {
  test("an audit inserted before the final attempt cannot confirm it or allow Done", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId);
    const agency = walkRows("agency", 15, "Agency");
    const coo = walkRows("coo", 15, "Coo");
    const identities = [...agency, ...coo].map((row) =>
      row.rowIdentity.replace("urn:li:fs_salesProfile:", ""),
    );
    const firstTwentyNineRows = [...agency, ...coo.slice(0, 14)];
    engine.recordWalkSends(runId, "hubspot-agency-ops", { sent: agency, skipped: [] }, NOW);
    engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      { sent: coo.slice(0, 14), skipped: [] },
      NOW,
    );
    const firstTwentyNine = engine
      .readControllerState(runId)
      .openAttempts.map((attempt) => attempt.attemptId)
      .sort();
    expect(firstTwentyNine).toHaveLength(29);

    engine.recordAudit({
      id: "stale-final-audit",
      invocationId: "stale-final-audit-invocation",
      runId,
      baselineId,
      peopleCount: 130,
      identities,
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-12-31T23:59:59Z",
    });
    const last = walkSend(
      engine,
      runId,
      "hubspot-b2b-revops",
      "coo15",
      "Coo coo 15",
      "2026-01-01T00:00:00Z",
    );

    expect(
      engine.reconcile(runId, baselineId, "stale-final-audit", NOW, undefined, "final"),
    ).toEqual([]);
    expect(engine.projection(runId)).toMatchObject({
      durable: 0,
      finalReconciliation: false,
      provisional: 30,
    });
    expect(() => engine.finish(runId, NOW)).toThrow("cannot finish");

    engine.recordAudit({
      id: "fresh-final-audit",
      invocationId: "fresh-final-audit-invocation",
      runId,
      baselineId,
      peopleCount: 130,
      identities,
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2027-01-01T00:00:00Z",
    });
    expect(
      engine.reconcile(runId, baselineId, "fresh-final-audit", NOW, undefined, "final"),
    ).toEqual([...firstTwentyNine, last].sort());
    expect(engine.finish(runId, NOW).status).toBe("done");
    void firstTwentyNineRows;
    database.close();
  });
});

describe("payload-complete receipts", () => {
  test("exact replays are no-ops and any changed receipt field fails", () => {
    const { database, engine, runId } = setup();
    const baseline = {
      id: "baseline",
      invocationId: "baseline-invocation",
      runId,
      peopleCount: 100,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T11:00:00Z",
    };
    engine.recordBaseline(baseline);
    const baselineEvents = eventCount(database);
    engine.recordBaseline(baseline);
    expect(eventCount(database)).toBe(baselineEvents);
    expect(() => engine.recordBaseline({ ...baseline, peopleCount: 101 })).toThrow(
      "payload mismatch",
    );

    const firstId = walkSend(
      engine,
      runId,
      "hubspot-agency-ops",
      "receipt-sales",
      "Receipt Person",
    );
    const walkEvents = eventCount(database);
    // Same walk row is a no-op (person already has an attempt in this run).
    expect(
      engine.recordWalkSends(
        runId,
        "hubspot-agency-ops",
        {
          sent: [
            {
              rowIdentity: "urn:li:fs_salesProfile:receipt-sales",
              name: "Receipt Person",
            },
          ],
          skipped: [],
        },
        NOW,
      ),
    ).toEqual({ sent: 0, skipped: 0 });
    expect(eventCount(database)).toBe(walkEvents);

    const attemptIds = [firstId];
    const identities = ["receipt-sales"];
    for (let index = 2; index <= 15; index++) {
      const salesLeadId = `agency${index}`;
      attemptIds.push(
        walkSend(engine, runId, "hubspot-agency-ops", salesLeadId, `Agency ${index}`),
      );
      identities.push(salesLeadId);
    }
    for (let index = 1; index <= 15; index++) {
      const salesLeadId = `coo${index}`;
      attemptIds.push(walkSend(engine, runId, "hubspot-b2b-revops", salesLeadId, `Coo ${index}`));
      identities.push(salesLeadId);
    }

    const audit = {
      id: "audit",
      invocationId: "audit-invocation",
      runId,
      baselineId: baseline.id,
      peopleCount: 130,
      identities,
      names: [] as string[],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    };
    engine.recordAudit(audit);
    const auditEvents = eventCount(database);
    engine.recordAudit(audit);
    expect(eventCount(database)).toBe(auditEvents);
    expect(() => engine.recordAudit({ ...audit, identities: [] })).toThrow("payload mismatch");
    expect(engine.reconcile(runId, baseline.id, audit.id, NOW, "reconciliation", "final")).toEqual(
      attemptIds.sort(),
    );
    const reconciliationEvents = eventCount(database);
    engine.reconcile(runId, baseline.id, audit.id, NOW, "reconciliation", "final");
    expect(eventCount(database)).toBe(reconciliationEvents);
    expect(() =>
      engine.reconcile(runId, baseline.id, audit.id, "changed", "reconciliation", "final"),
    ).toThrow("payload mismatch");

    const skip = {
      rowIdentity: "urn:li:fs_salesProfile:skip-person",
      name: "Skip Person",
      reason: "already_pending" as const,
    };
    engine.recordWalkSends(runId, "hubspot-b2b-revops", { sent: [], skipped: [skip] }, NOW);
    const skipEvents = eventCount(database);
    engine.recordWalkSends(runId, "hubspot-b2b-revops", { sent: [], skipped: [skip] }, NOW);
    expect(eventCount(database)).toBe(skipEvents);
    database.close();
  });
});

function eventCount(database: import("bun:sqlite").Database): number {
  return (
    database.query<{ count: number }, []>("SELECT COUNT(*) AS count FROM events").get()?.count ?? 0
  );
}
