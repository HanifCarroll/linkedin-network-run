import { describe, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openDatabase } from "../../src/db/database.ts";
import { NetworkEngine, PriorDayNeedsAuditError } from "../../src/network/engine.ts";
import { NOW, recordBaseline, setup, walkRows, walkSend } from "./helpers.ts";

describe("run lifecycle and restart persistence", () => {
  test("possible walk rows can be proven no-send and release capacity", () => {
    const { database, engine, runId } = setup();
    const attemptId = walkSend(engine, runId, "hubspot-agency-ops", "lead-1", "Person 1");

    engine.markProvenNoSend(attemptId, NOW, { reason: "already_pending" });

    expect(engine.projection(runId)).toMatchObject({
      planned: 0,
      provisional: 0,
      remainingCapacity: 30,
    });
    expect(
      database
        .query<{ state: string }, [string]>("SELECT state FROM send_attempts WHERE id = ?")
        .get(attemptId),
    ).toEqual({ state: "proven_no_send" });
    database.close();
  });

  test("walk possibles consume capacity until proven no-send", () => {
    const { database, engine, runId } = setup();
    const attemptId = walkSend(engine, runId, "hubspot-agency-ops", "lead-1", "Person 1");
    expect(engine.projection(runId)).toMatchObject({ provisional: 1, remainingCapacity: 29 });
    engine.markProvenNoSend(attemptId, NOW, { receipt: "not-sent" });
    expect(engine.projection(runId).remainingCapacity).toBe(30);
    database.close();
  });

  test("survives restart with a possible send reserved", () => {
    const path = join(tmpdir(), `linkedin-network-${crypto.randomUUID()}.sqlite`);
    const first = openDatabase(path);
    const firstEngine = new NetworkEngine(first.database);
    const run = firstEngine.openDailyRun("2026-08-03", NOW, "run");
    walkSend(firstEngine, run.id, "hubspot-agency-ops", "lead-1", "Person 1");
    first.database.close();

    const second = openDatabase(path);
    const secondEngine = new NetworkEngine(second.database);
    expect(secondEngine.openDailyRun("2026-08-03", NOW).id).toBe("run");
    expect(secondEngine.projection("run")).toMatchObject({
      provisional: 1,
      remainingCapacity: 29,
    });
    second.database.close();
    for (const suffix of ["", "-wal", "-shm"]) rmSync(`${path}${suffix}`, { force: true });
  });

  test("parks an unfinished prior day when no send is unresolved", () => {
    const { database, engine, runId } = setup();
    const attemptId = walkSend(engine, runId, "hubspot-agency-ops", "lead-1", "Person 1");
    engine.markProvenNoSend(attemptId, NOW, { reason: "already_pending" });

    const next = engine.prepareDailyRun("2026-08-04", "2026-08-04T10:00:00Z", "next-run");

    expect(next.parkedRuns).toEqual([
      {
        runId,
        localDate: "2026-08-03",
        durable: 0,
        planned: 0,
        provisional: 0,
      },
    ]);
    expect(next.run.localDate).toBe("2026-08-04");
    expect(
      database
        .query<{ status: string }, [string]>("SELECT status FROM daily_runs WHERE id = ?")
        .get(runId)?.status,
    ).toBe("blocked");
    expect(
      database
        .query<{ type: string; payload_json: string }, [string]>(
          "SELECT type, payload_json FROM events WHERE id = ?",
        )
        .get(`event:run:${runId}:missed`),
    ).toEqual({
      type: "daily_run_missed",
      payload_json: JSON.stringify({
        durable: 0,
        localDate: "2026-08-03",
        planned: 0,
        provisional: 0,
        reason: "missed_local_day",
      }),
    });
    database.close();
  });

  test("refuses to open a new day while a prior send is unresolved", () => {
    const { database, engine, runId } = setup();
    walkSend(engine, runId, "hubspot-agency-ops", "lead-1", "Person 1");

    expect(() => engine.prepareDailyRun("2026-08-04", "2026-08-04T10:00:00Z", "next-run")).toThrow(
      PriorDayNeedsAuditError,
    );
    expect(
      database
        .query<{ count: number }, [string]>(
          "SELECT COUNT(*) AS count FROM daily_runs WHERE local_date = ?",
        )
        .get("2026-08-04")?.count,
    ).toBe(0);
    expect(engine.projection(runId).provisional).toBe(1);
    database.close();
  });

  test("Done is bound to one complete, same-baseline reconciliation of all 30 attempts", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId);
    expect(() => engine.finish(runId, NOW)).toThrow();
    const agency = walkRows("agency", 15, "Agency");
    const coo = walkRows("coo", 15, "Coo");
    engine.recordWalkSends(runId, "hubspot-agency-ops", { sent: agency, skipped: [] }, NOW);
    engine.recordWalkSends(runId, "hubspot-b2b-revops", { sent: coo, skipped: [] }, NOW);
    const ids = engine.readControllerState(runId).openAttempts.map((attempt) => attempt.attemptId);
    const identities = [...agency, ...coo].map((row) =>
      row.rowIdentity.replace("urn:li:fs_salesProfile:", ""),
    );
    engine.recordAudit({
      id: "final-audit",
      invocationId: "final-audit-invocation",
      runId,
      baselineId,
      peopleCount: 130,
      identities,
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(engine.reconcile(runId, baselineId, "final-audit", NOW, undefined, "final")).toEqual(
      ids.sort(),
    );
    expect(engine.projection(runId)).toMatchObject({
      durable: 30,
      finalReconciliation: true,
      planned: 0,
      provisional: 0,
    });
    expect(engine.finish(runId, NOW).status).toBe("done");
    const completedReport = engine.reportBytes(runId);
    expect(engine.reconcile(runId, baselineId, "final-audit", NOW, undefined, "final")).toEqual(
      ids.sort(),
    );
    expect(engine.reportBytes(runId)).toEqual(completedReport);
    expect(engine.openDailyRun("2026-08-03", NOW).status).toBe("done");
    database.close();
  });
});
