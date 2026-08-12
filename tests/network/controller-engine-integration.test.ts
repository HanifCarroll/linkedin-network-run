import { describe, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openDatabase } from "../../src/db/database.ts";
import { NetworkEngine } from "../../src/network/engine.ts";
import { NOW, recordBaseline, walkSend } from "./helpers.ts";

describe("durable controller engine APIs", () => {
  test("walk creates possible attempts with candidate evidence and commit already started", () => {
    const path = join(tmpdir(), `linkedin-tools-controller-${crypto.randomUUID()}.sqlite`);
    const first = openDatabase(path);
    const engine = new NetworkEngine(first.database);
    const run = engine.openDailyRun("2026-08-03", NOW, "controller-run");
    recordBaseline(engine, run.id, 100, "controller-baseline");
    const attemptId = walkSend(engine, run.id, "hubspot-agency-ops", "sales-ada", "Ada Example");

    const state = engine.readControllerState(run.id);
    expect(state).toMatchObject({
      baseline: { id: "controller-baseline" },
      pendingAudit: null,
      openAttempts: [
        {
          attemptId,
          state: "possible",
          prepareReceipt: null,
          commitStarted: true,
          candidate: {
            sourceName: "Consulting - HubSpot Agency Ops",
            savedSearchId: "1980844577",
            salesLeadId: "sales-ada",
            name: "Ada Example",
            rowIdentity: "urn:li:fs_salesProfile:sales-ada",
          },
        },
      ],
    });
    const attemptRow = first.database
      .query<
        {
          commit_started_at: string | null;
          possible_evidence_json: string | null;
          prepare_receipt_json: string | null;
          state: string;
        },
        [string]
      >(
        `SELECT state, commit_started_at, prepare_receipt_json, possible_evidence_json
         FROM send_attempts WHERE id = ?`,
      )
      .get(attemptId);
    expect(attemptRow).toMatchObject({
      state: "possible",
      commit_started_at: NOW,
      prepare_receipt_json: null,
    });
    expect(JSON.parse(attemptRow?.possible_evidence_json ?? "{}")).toMatchObject({
      candidate: {
        salesLeadId: "sales-ada",
        name: "Ada Example",
      },
      walk: {
        rowIdentity: "urn:li:fs_salesProfile:sales-ada",
        sourceId: "hubspot-agency-ops",
      },
    });
    first.database.close();

    const second = openDatabase(path);
    const restarted = new NetworkEngine(second.database);
    expect(restarted.readControllerState(run.id).openAttempts[0]).toMatchObject({
      attemptId,
      state: "possible",
      prepareReceipt: null,
      commitStarted: true,
    });

    restarted.recordAudit({
      id: "pending-audit",
      invocationId: "pending-audit-invocation",
      runId: run.id,
      baselineId: "controller-baseline",
      peopleCount: 101,
      identities: ["sales-ada"],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T12:01:00Z",
    });
    expect(restarted.readControllerState(run.id).pendingAudit).toEqual({
      id: "pending-audit",
      baselineId: "controller-baseline",
    });
    second.database.close();
    for (const suffix of ["", "-wal", "-shm"]) rmSync(`${path}${suffix}`, { force: true });
  });

  test("walk candidate projection survives reopen and rejects commit without preparation", () => {
    const path = join(tmpdir(), `linkedin-tools-walk-proj-${crypto.randomUUID()}.sqlite`);
    const opened = openDatabase(path);
    try {
      const engine = new NetworkEngine(opened.database);
      const run = engine.openDailyRun("2026-08-03", NOW, "walk-proj-run");
      recordBaseline(engine, run.id, 100, "walk-proj-baseline");
      const attemptId = walkSend(engine, run.id, "hubspot-b2b-revops", "coo-lead-1", "Coo Lead");
      const candidate = engine.readControllerState(run.id).openAttempts[0]?.candidate;
      if (candidate === undefined) throw new Error("missing candidate");
      expect(candidate).toMatchObject({
        salesLeadId: "coo-lead-1",
        name: "Coo Lead",
        rowIdentity: "urn:li:fs_salesProfile:coo-lead-1",
      });
      expect(() =>
        engine.recordCommitStarted({
          runId: run.id,
          attemptId,
          startedAt: "2026-08-03T12:00:01Z",
          receipt: {
            schemaVersion: 1,
            kind: "network_send_prepared",
            receiptId: "pwprep:unused",
            attemptId,
            preparedAt: NOW,
            candidate,
          },
        }),
      ).toThrow(/before durable preparation|commit-start receipt/);
      expect(engine.projection(run.id)).toMatchObject({
        provisional: 1,
        remainingCapacity: 29,
        bySource: {
          "hubspot-b2b-revops": { possible: 1 },
        },
      });
    } finally {
      opened.database.close();
      rmSync(path, { force: true });
    }
  });
});
