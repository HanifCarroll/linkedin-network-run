import { describe, expect, test } from "bun:test";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openDatabase } from "../../src/db/database.ts";
import { NetworkEngine } from "../../src/network/engine.ts";
import { NOW, recordBaseline, walkRows, walkSend } from "./helpers.ts";

describe("capacity across connections", () => {
  test("serializes a truly overlapping 30th reservation across two processes", async () => {
    const directory = mkdtempSync(join(tmpdir(), "linkedin-capacity-race-"));
    const path = join(directory, "state.sqlite");
    const opened = openDatabase(path);
    const engine = new NetworkEngine(opened.database);
    const run = engine.openDailyRun("2026-08-03", NOW, "run");
    recordBaseline(engine, run.id);
    engine.recordWalkSends(
      run.id,
      "hubspot-agency-ops",
      { sent: walkRows("seed", 29, "Seed"), skipped: [] },
      NOW,
    );
    opened.database.close();

    const go = join(directory, "go");
    const ready = [join(directory, "ready-a"), join(directory, "ready-b")];
    const worker = join(import.meta.dir, "capacity-racer.ts");
    const processes = ready.map((readyPath, index) =>
      Bun.spawn([process.execPath, worker, path, readyPath, go, `racer-${index}`], {
        stderr: "pipe",
        stdout: "pipe",
      }),
    );
    await waitUntil(() => ready.every(existsSync));
    writeFileSync(go, "go");
    const outputs = await Promise.all(
      processes.map(
        (process) => new Response(process.stdout).json() as Promise<{ reserved: boolean }>,
      ),
    );
    const exits = await Promise.all(processes.map((process) => process.exited));
    expect(exits).toEqual([0, 0]);
    expect(outputs.filter((output) => output.reserved)).toHaveLength(1);
    const verified = openDatabase(path);
    expect(new NetworkEngine(verified.database).projection(run.id)).toMatchObject({
      provisional: 30,
      remainingCapacity: 0,
    });
    verified.database.close();
    rmSync(directory, { force: true, recursive: true });
  }, 15_000);

  test("serializes the 30th walk reservation across two SQLite connections", () => {
    const path = join(tmpdir(), `capacity-${crypto.randomUUID()}.sqlite`);
    const first = openDatabase(path);
    const second = openDatabase(path);
    const firstEngine = new NetworkEngine(first.database);
    const secondEngine = new NetworkEngine(second.database);
    const run = firstEngine.openDailyRun("2026-08-03", NOW, "run");
    recordBaseline(firstEngine, run.id);
    firstEngine.recordWalkSends(
      run.id,
      "hubspot-agency-ops",
      { sent: walkRows("seed", 29, "Seed"), skipped: [] },
      NOW,
    );
    const results = [
      firstEngine.recordWalkSends(
        run.id,
        "hubspot-agency-ops",
        {
          sent: [{ rowIdentity: "urn:li:fs_salesProfile:final-a", name: "Final A" }],
          skipped: [],
        },
        NOW,
      ),
      secondEngine.recordWalkSends(
        run.id,
        "hubspot-agency-ops",
        {
          sent: [{ rowIdentity: "urn:li:fs_salesProfile:final-b", name: "Final B" }],
          skipped: [],
        },
        NOW,
      ),
    ];
    expect(results.filter((result) => result.sent === 1)).toHaveLength(1);
    expect(firstEngine.projection(run.id)).toMatchObject({
      provisional: 30,
      remainingCapacity: 0,
    });
    expect(
      secondEngine.recordWalkSends(
        run.id,
        "hubspot-agency-ops",
        {
          sent: [{ rowIdentity: "urn:li:fs_salesProfile:overflow", name: "Overflow" }],
          skipped: [],
        },
        NOW,
      ).sent,
    ).toBe(0);
    expect(secondEngine.projection(run.id).remainingCapacity).toBe(0);
    first.database.close();
    second.database.close();
    for (const suffix of ["", "-wal", "-shm"]) rmSync(`${path}${suffix}`, { force: true });
  });
});

async function waitUntil(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 5_000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("capacity racers did not reach the barrier");
    await Bun.sleep(5);
  }
}

describe("receipt replay", () => {
  test("walk, audit, and reconciliation receipts replay without duplicate events", () => {
    const path = join(tmpdir(), `replay-${crypto.randomUUID()}.sqlite`);
    const opened = openDatabase(path);
    const engine = new NetworkEngine(opened.database);
    const run = engine.openDailyRun("2026-08-03", NOW, "run");
    const baselineId = recordBaseline(engine, run.id);
    const agency = walkRows("agency", 15, "Agency");
    const coo = walkRows("coo", 15, "Coo");
    engine.recordWalkSends(run.id, "hubspot-agency-ops", { sent: agency, skipped: [] }, NOW);
    const afterWalk = eventCount(opened.database);
    engine.recordWalkSends(run.id, "hubspot-agency-ops", { sent: agency, skipped: [] }, NOW);
    expect(eventCount(opened.database)).toBe(afterWalk);
    engine.recordWalkSends(run.id, "hubspot-b2b-revops", { sent: coo, skipped: [] }, NOW);
    const attemptIds = engine
      .readControllerState(run.id)
      .openAttempts.map((attempt) => attempt.attemptId);
    const identities = [...agency, ...coo].map((row) =>
      row.rowIdentity.replace("urn:li:fs_salesProfile:", ""),
    );
    expect(attemptIds).toHaveLength(30);

    const audit = {
      id: "audit",
      invocationId: "audit-invocation",
      runId: run.id,
      baselineId,
      peopleCount: 130,
      identities,
      names: [] as string[],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    };
    engine.recordAudit(audit);
    const afterAudit = eventCount(opened.database);
    engine.recordAudit(audit);
    expect(eventCount(opened.database)).toBe(afterAudit);
    expect(() => engine.recordAudit({ ...audit, peopleCount: 102 })).toThrow("payload mismatch");

    expect(engine.reconcile(run.id, baselineId, audit.id, NOW, undefined, "final")).toEqual(
      attemptIds.sort(),
    );
    const afterReconcile = eventCount(opened.database);
    expect(engine.reconcile(run.id, baselineId, audit.id, NOW, undefined, "final")).toEqual(
      attemptIds.sort(),
    );
    expect(eventCount(opened.database)).toBe(afterReconcile);
    opened.database.close();
    for (const suffix of ["", "-wal", "-shm"]) rmSync(`${path}${suffix}`, { force: true });
  });

  test("proven-no-send and baseline receipts also replay as no-ops", () => {
    const path = join(tmpdir(), `resolution-replay-${crypto.randomUUID()}.sqlite`);
    const opened = openDatabase(path);
    const engine = new NetworkEngine(opened.database);
    const run = engine.openDailyRun("2026-08-03", NOW, "run");
    recordBaseline(engine, run.id);
    recordBaseline(engine, run.id);
    const attemptId = walkSend(engine, run.id, "hubspot-agency-ops", "lead-1", "Person 1");
    engine.markProvenNoSend(attemptId, NOW, { receipt: "no-send" });
    const count = eventCount(opened.database);
    engine.markProvenNoSend(attemptId, NOW, { receipt: "no-send" });
    expect(eventCount(opened.database)).toBe(count);
    opened.database.close();
    for (const suffix of ["", "-wal", "-shm"]) rmSync(`${path}${suffix}`, { force: true });
  });
});

function eventCount(database: import("bun:sqlite").Database): number {
  return (
    database.query<{ count: number }, []>("SELECT COUNT(*) AS count FROM events").get()?.count ?? 0
  );
}
