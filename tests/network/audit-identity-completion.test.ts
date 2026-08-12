import { describe, expect, test } from "bun:test";
import { parseSentList } from "../../src/network/results.ts";
import { NOW, recordBaseline, setup, walkSend } from "./helpers.ts";

describe("audit identity confirmation and completion", () => {
  test("confirms two same-name candidates via distinct identities", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 200, "dup-name-baseline");
    const first = walkSend(engine, runId, "hubspot-agency-ops", "sales-dup-a", "Alex Same");
    const second = walkSend(engine, runId, "hubspot-agency-ops", "sales-dup-b", "Alex Same");
    engine.recordAudit({
      id: "dup-name-audit",
      invocationId: "dup-name-audit-invocation",
      runId,
      baselineId,
      peopleCount: 202,
      identities: ["sales-dup-a", "sales-dup-b"],
      names: ["Alex Same", "Alex Same"],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    expect(
      engine.reconcile(
        runId,
        baselineId,
        "dup-name-audit",
        "2026-08-03T13:01:00Z",
        "dup-name-reconciliation",
        "microbatch",
      ),
    ).toEqual([first, second].sort());
    expect(engine.projection(runId)).toMatchObject({ durable: 2, provisional: 0 });
    database.close();
  });

  test("exact matches still confirm when a stranger identity disables competingSenderAbsent", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 50, "stranger-baseline");
    const attemptId = walkSend(
      engine,
      runId,
      "hubspot-agency-ops",
      "sales-known-1",
      "Known Person",
    );
    engine.recordAudit({
      id: "stranger-audit",
      invocationId: "stranger-audit-invocation",
      runId,
      baselineId,
      peopleCount: 52,
      identities: ["sales-known-1", "stranger-identity"],
      names: ["Known Person", "Stranger"],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T13:00:00Z",
    });
    const competing = database
      .query<{ competing_sender_absent: number }, [string]>(
        "SELECT competing_sender_absent FROM audit_snapshots WHERE id = ?",
      )
      .get("stranger-audit");
    expect(competing?.competing_sender_absent).toBe(0);
    expect(
      engine.reconcile(
        runId,
        baselineId,
        "stranger-audit",
        "2026-08-03T13:01:00Z",
        "stranger-reconciliation",
        "microbatch",
      ),
    ).toEqual([attemptId]);
    expect(engine.projection(runId)).toMatchObject({ durable: 1, provisional: 0 });
    database.close();
  });

  test("final completion ignores proven_no_send and finishes at exactly 30 durable", () => {
    const { database, engine, runId } = setup();
    const baselineId = recordBaseline(engine, runId, 100, "proven-baseline");
    const rejected = walkSend(
      engine,
      runId,
      "hubspot-agency-ops",
      "rejected-lead",
      "Rejected Person",
    );
    engine.markProvenNoSend(rejected, NOW, { reason: "ALREADY_PENDING" }, "rejected-proven");

    const attemptIds: string[] = [];
    const identities: string[] = [];
    for (let index = 0; index < 15; index++) {
      const salesLeadId = `agency-ok-${index}`;
      attemptIds.push(
        walkSend(engine, runId, "hubspot-agency-ops", salesLeadId, `Agency ${index}`),
      );
      identities.push(salesLeadId);
    }
    for (let index = 0; index < 15; index++) {
      const salesLeadId = `coo-ok-${index}`;
      attemptIds.push(walkSend(engine, runId, "hubspot-b2b-revops", salesLeadId, `Coo ${index}`));
      identities.push(salesLeadId);
    }

    engine.recordAudit({
      id: "proven-final-audit",
      invocationId: "proven-final-audit-invocation",
      runId,
      baselineId,
      peopleCount: 130,
      identities,
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: "2026-08-03T14:00:00Z",
    });
    expect(
      engine.reconcile(
        runId,
        baselineId,
        "proven-final-audit",
        "2026-08-03T14:01:00Z",
        "proven-final-reconciliation",
        "final",
      ),
    ).toEqual([...attemptIds].sort());
    expect(engine.projection(runId)).toMatchObject({
      durable: 30,
      provisional: 0,
      planned: 0,
      finalReconciliation: true,
    });
    expect(engine.finish(runId, "2026-08-03T14:02:00Z").status).toBe("done");
    const attemptCount = database
      .query<{ attempt_count: number }, [string]>(
        "SELECT attempt_count FROM reconciliations WHERE id = ?",
      )
      .get("proven-final-reconciliation");
    expect(attemptCount?.attempt_count).toBe(30);
    database.close();
  });

  test("sentListCaptureBody emits identity extraction and load bounds", async () => {
    const { compileNetworkScript } = await import("../../src/playwriter/scripts.ts");
    const compiled = compileNetworkScript("capture-sent-list");
    expect(compiled.source).toContain("MAX_SENT_NAMES=2500");
    expect(compiled.source).toContain("MAX_STAGNANT=12");
    expect(compiled.source).toContain("Load more");
    expect(compiled.source).toContain("urn:li:fsd_invitation:");
    expect(compiled.source).toContain("identities");
    expect(compiled.source).toContain("competingSenderAbsent=false");
    expect(
      parseSentList({
        peopleCount: 1,
        identities: ["https://www.linkedin.com/in/example"],
        names: ["Example"],
        complete: true,
        competingSenderAbsent: false,
        contradictoryEvidence: false,
      }).identities,
    ).toEqual(["https://www.linkedin.com/in/example"]);
  });
});
