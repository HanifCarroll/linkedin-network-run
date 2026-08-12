import { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { runMigrations } from "../../src/db/migrations.ts";
import { NetworkEngine } from "../../src/network/engine.ts";
import { NOW, recordBaseline, setup, walkSend } from "./helpers.ts";

describe("audits and reconciliation", () => {
  test("keeps partial exact identity evidence pending until final reconciliation", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId);
    walkSend(engine, runId, "hubspot-agency-ops", "agency-1", "Agency 1");
    walkSend(engine, runId, "hubspot-agency-ops", "agency-2", "Agency 2");
    engine.recordAudit({
      id: "audit-exact",
      invocationId: "audit-invocation",
      runId,
      baselineId,
      peopleCount: 101,
      identities: ["agency-1"],
      names: [],
      complete: false,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(engine.reconcile(runId, baselineId, "audit-exact", NOW, undefined, "final")).toEqual([]);
    expect(engine.projection(runId)).toMatchObject({
      durable: 0,
      provisional: 2,
      remainingCapacity: 28,
    });
    database.close();
  });

  test.each([
    { complete: false, competingSenderAbsent: true, contradictoryEvidence: false, delta: 2 },
    { complete: true, competingSenderAbsent: false, contradictoryEvidence: false, delta: 2 },
    { complete: true, competingSenderAbsent: true, contradictoryEvidence: false, delta: 1 },
    { complete: true, competingSenderAbsent: true, contradictoryEvidence: true, delta: 2 },
  ])("rejects unsafe aggregate preconditions %#", (scenario) => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId);
    walkSend(engine, runId, "hubspot-agency-ops", "agency-1", "Agency 1");
    walkSend(engine, runId, "hubspot-b2b-revops", "coo-1", "Coo 1");
    engine.recordAudit({
      id: "audit",
      invocationId: "audit-invocation",
      runId,
      baselineId,
      peopleCount: 100 + scenario.delta,
      identities: [],
      names: [],
      complete: scenario.complete,
      competingSenderAbsent: scenario.competingSenderAbsent,
      contradictoryEvidence: scenario.contradictoryEvidence,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(engine.reconcile(runId, baselineId, "audit", NOW, undefined, "final")).toEqual([]);
    database.close();
  });

  test("rejects aggregate confirmation after a proven failure or reversion", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId);
    const failed = walkSend(engine, runId, "hubspot-agency-ops", "agency-1", "Agency 1");
    engine.markProvenNoSend(failed, NOW, { receipt: "proven-no-send" });
    walkSend(engine, runId, "hubspot-b2b-revops", "coo-1", "Coo 1");
    engine.recordAudit({
      id: "audit-after-failure",
      invocationId: "audit-after-failure-invocation",
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
      engine.reconcile(runId, baselineId, "audit-after-failure", NOW, undefined, "final"),
    ).toEqual([]);
    database.close();
  });

  test("rejects aggregate confirmation when the stored baseline cannot exclude a competing sender", () => {
    const { database, engine, runId } = setup();
    engine.recordBaseline({
      id: "competing-baseline",
      invocationId: "competing-baseline-invocation",
      runId,
      peopleCount: 100,
      competingSenderAbsent: false,
      capturedAt: "2026-08-03T11:00:00Z",
    });
    walkSend(engine, runId, "hubspot-agency-ops", "agency-1", "Agency 1");
    engine.recordAudit({
      id: "competing-audit",
      invocationId: "competing-audit-invocation",
      runId,
      baselineId: "competing-baseline",
      peopleCount: 101,
      identities: [],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(
      engine.reconcile(runId, "competing-baseline", "competing-audit", NOW, undefined, "final"),
    ).toEqual([]);
    database.close();
  });

  test("requires names to be unique on both provisional and audit sides", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId);
    walkSend(engine, runId, "hubspot-agency-ops", "agency-1", "Duplicate Name");
    walkSend(engine, runId, "hubspot-b2b-revops", "coo-1", "Duplicate Name");
    engine.recordAudit({
      id: "audit-duplicate-provisional",
      invocationId: "audit-duplicate-provisional-invocation",
      runId,
      baselineId,
      peopleCount: 100,
      identities: [],
      names: ["Duplicate Name"],
      complete: false,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(
      engine.reconcile(runId, baselineId, "audit-duplicate-provisional", NOW, undefined, "final"),
    ).toEqual([]);
    database.close();

    const second = setup();
    const secondBaseline = recordBaseline(second.engine, second.runId);
    walkSend(second.engine, second.runId, "hubspot-agency-ops", "audit-dup", "Audit Duplicate");
    second.engine.recordAudit({
      id: "audit-duplicate-side",
      invocationId: "audit-duplicate-side-invocation",
      runId: second.runId,
      baselineId: secondBaseline,
      peopleCount: 100,
      identities: [],
      names: ["Audit Duplicate", " audit   duplicate "],
      complete: false,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(
      second.engine.reconcile(
        second.runId,
        secondBaseline,
        "audit-duplicate-side",
        NOW,
        undefined,
        "final",
      ),
    ).toEqual([]);
    second.database.close();
  });

  test("rejects an unrelated audit and wrong baseline", () => {
    const database = new Database(":memory:");
    database.exec("PRAGMA foreign_keys = ON");
    runMigrations(database);
    const engine = new NetworkEngine(database);
    const other = engine.openDailyRun("2026-08-04", "2026-08-04T10:00:00Z", "other-run");
    const run = engine.openDailyRun("2026-08-03", NOW, "run-today");
    const runId = run.id;
    const baselineId = recordBaseline(engine, runId);
    engine.recordBaseline({
      id: "other-baseline",
      invocationId: "other-baseline-invocation",
      runId: other.id,
      peopleCount: 200,
      competingSenderAbsent: true,
      capturedAt: "2026-08-04T11:00:00Z",
    });
    engine.recordAudit({
      id: "other-audit",
      invocationId: "other-audit-invocation",
      runId: other.id,
      baselineId: "other-baseline",
      peopleCount: 200,
      identities: [],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-04T12:00:00Z",
    });
    expect(() =>
      engine.reconcile(runId, baselineId, "other-audit", NOW, undefined, "final"),
    ).toThrow("run or baseline mismatch");
    expect(() =>
      engine.recordAudit({
        id: "wrong-baseline-audit",
        invocationId: "wrong-baseline-audit-invocation",
        runId,
        baselineId: "other-baseline",
        peopleCount: 100,
        identities: [],
        names: [],
        complete: true,
        competingSenderAbsent: true,
        capturedAt: "2026-08-03T13:00:00Z",
      }),
    ).toThrow("another run");
    database.close();
  });

  test("empty walk leaves capacity open and records no attempts", () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    expect(
      engine.recordWalkSends(runId, "hubspot-agency-ops", { sent: [], skipped: [] }, NOW),
    ).toEqual({ sent: 0, skipped: 0 });
    expect(engine.projection(runId)).toMatchObject({
      provisional: 0,
      remainingCapacity: 30,
    });
    database.close();
  });
});
