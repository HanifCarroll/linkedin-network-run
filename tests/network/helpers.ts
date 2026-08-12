import { Database } from "bun:sqlite";
import { runMigrations } from "../../src/db/migrations.ts";
import type { SourceId } from "../../src/network/config.ts";
import { NetworkEngine } from "../../src/network/engine.ts";

export const NOW = "2026-08-03T12:00:00Z";

export function setup(): { database: Database; engine: NetworkEngine; runId: string } {
  const database = new Database(":memory:");
  database.exec("PRAGMA foreign_keys = ON");
  runMigrations(database);
  const engine = new NetworkEngine(database);
  const run = engine.openDailyRun("2026-08-03", NOW, "run-today");
  return { database, engine, runId: run.id };
}

export function walkRows(
  prefix: string,
  count: number,
  namePrefix = "Person",
): { readonly rowIdentity: string; readonly name: string }[] {
  return Array.from({ length: count }, (_, index) => {
    const id = `${prefix}${index + 1}`;
    return {
      rowIdentity: `urn:li:fs_salesProfile:${id}`,
      name: `${namePrefix} ${prefix} ${index + 1}`,
    };
  });
}

export function walkNamed(
  salesLeadId: string,
  name: string,
): { readonly rowIdentity: string; readonly name: string } {
  return {
    rowIdentity: `urn:li:fs_salesProfile:${salesLeadId}`,
    name,
  };
}

/** Record a walk send as a possible attempt; returns the attempt id. */
export function walkSend(
  engine: NetworkEngine,
  runId: string,
  sourceId: SourceId,
  salesLeadId: string,
  name: string,
  now = NOW,
): string {
  const before = attemptIds(engine, runId);
  const recorded = engine.recordWalkSends(
    runId,
    sourceId,
    { sent: [walkNamed(salesLeadId, name)], skipped: [] },
    now,
  );
  if (recorded.sent !== 1) throw new Error(`expected 1 walk send, got ${recorded.sent}`);
  const after = attemptIds(engine, runId);
  const created = after.find((id) => !before.includes(id));
  if (created === undefined) throw new Error("walk send did not create an attempt");
  return created;
}

export function attemptIds(engine: NetworkEngine, runId: string): string[] {
  return engine.readControllerState(runId).openAttempts.map((attempt) => attempt.attemptId);
}

export function recordBaseline(
  engine: NetworkEngine,
  runId: string,
  peopleCount = 100,
  id = "baseline",
): string {
  engine.recordBaseline({
    id,
    invocationId: `${id}-invocation`,
    runId,
    peopleCount,
    competingSenderAbsent: true,
    capturedAt: "2026-08-03T11:00:00Z",
  });
  return id;
}

export function completeRun(
  engine: NetworkEngine,
  runId: string,
): {
  attemptIds: string[];
  auditId: string;
  baselineId: string;
  reconciliationId: string;
  identities: string[];
} {
  const baselineId = recordBaseline(engine, runId, 100, "final-baseline");
  const agency = walkRows("agency", 15, "Agency");
  const coo = walkRows("coo", 15, "Coo");
  engine.recordWalkSends(runId, "hubspot-agency-ops", { sent: agency, skipped: [] }, NOW);
  engine.recordWalkSends(runId, "hubspot-b2b-revops", { sent: coo, skipped: [] }, NOW);
  const identities = [...agency, ...coo].map((row) =>
    row.rowIdentity.replace("urn:li:fs_salesProfile:", ""),
  );
  const openIds = attemptIds(engine, runId);
  const auditId = "final-audit";
  engine.recordAudit({
    id: auditId,
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
  const reconciliationId = "final-reconciliation";
  engine.reconcile(runId, baselineId, auditId, "2026-08-03T13:01:00Z", reconciliationId, "final");
  engine.finish(runId, "2026-08-03T13:02:00Z");
  return { attemptIds: openIds, auditId, baselineId, reconciliationId, identities };
}
