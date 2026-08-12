import { describe, expect, test } from "bun:test";
import type { SourceId } from "../../src/network/config.ts";
import {
  type BrowserPortResult,
  type NETWORK_SOURCES,
  type NetworkBrowserPort,
  NetworkController,
} from "../../src/network/controller.ts";
import type {
  BrowserBlocker,
  SentListEvidence,
  WalkListResult,
} from "../../src/network/results.ts";
import { NOW, recordBaseline, setup, walkRows } from "./helpers.ts";

describe("network controller walkList", () => {
  test("walks a source, records possibles, and returns walked outcome", async () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    const browser = new FakeBrowser({
      walk: {
        "hubspot-agency-ops": {
          sourceId: "hubspot-agency-ops",
          sent: walkRows("agency", 2),
          skipped: [
            {
              rowIdentity: "urn:li:fs_salesProfile:skip1",
              name: "Skip",
              reason: "already_pending",
            },
          ],
          pagesWalked: 1,
          complete: true,
        },
      },
    });
    const controller = new NetworkController(engine, browser, () => NOW);
    const result = await controller.walkList(runId, "hubspot-agency-ops", 5, 0);
    expect(result).toMatchObject({
      state: "progress",
      outcome: {
        kind: "walked",
        sourceId: "hubspot-agency-ops",
        sent: 2,
        skipped: 1,
        complete: true,
      },
    });
    expect(engine.projection(runId).provisional).toBe(2);
    database.close();
  });

  test("walks without a pre-run baseline and records possibles", async () => {
    const { database, engine, runId } = setup();
    const browser = new FakeBrowser({
      walk: {
        "hubspot-b2b-revops": {
          sourceId: "hubspot-b2b-revops",
          sent: walkRows("coo", 3),
          skipped: [],
          pagesWalked: 1,
          complete: true,
        },
      },
    });
    const controller = new NetworkController(engine, browser, () => NOW);
    const result = await controller.walkList(runId, "hubspot-b2b-revops", 3, 0);
    expect(result).toMatchObject({
      state: "progress",
      outcome: { kind: "walked", sourceId: "hubspot-b2b-revops", sent: 3 },
    });
    expect(engine.projection(runId).provisional).toBe(3);
    database.close();
  });

  test("browser failure becomes checkpoint", async () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    const browser = new FakeBrowser();
    browser.fail("walkList");
    const controller = new NetworkController(engine, browser, () => NOW);
    expect(await controller.walkList(runId, "hubspot-agency-ops", 2, 0)).toMatchObject({
      state: "checkpoint",
      checkpoint: { kind: "browser_blocker", phase: "walk_list" },
    });
    database.close();
  });

  test("malformed walk payload is a source_contract checkpoint", async () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    const browser = new FakeBrowser({
      walkRaw: { sourceId: "hubspot-agency-ops", sent: "nope" },
    });
    const controller = new NetworkController(engine, browser, () => NOW);
    expect(await controller.walkList(runId, "hubspot-agency-ops", 2, 0)).toMatchObject({
      state: "checkpoint",
      checkpoint: { kind: "source_contract", phase: "walk_list" },
    });
    database.close();
  });

  test("captureBaseline and reconcile still work", async () => {
    const { database, engine, runId } = setup();
    const browser = new FakeBrowser({
      sentList: {
        peopleCount: 102,
        identities: ["agency1", "agency2"],
        names: ["Person agency 1", "Person agency 2"],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      },
    });
    const controller = new NetworkController(engine, browser, () => NOW);
    expect(await controller.captureBaseline(runId, "baseline-1")).toMatchObject({
      state: "progress",
      outcome: { kind: "baseline_captured", baselineId: "baseline-1" },
    });
    engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      { sent: walkRows("agency", 2), skipped: [] },
      NOW,
    );
    expect(await controller.reconcile(runId, "audit-1", "microbatch")).toMatchObject({
      state: "progress",
      outcome: { kind: "reconciled", auditId: "audit-1", scope: "microbatch" },
    });
    expect(engine.projection(runId)).toMatchObject({ durable: 2, provisional: 0 });
    database.close();
  });

  test("reconciles via exact match without a pre-run baseline", async () => {
    const { database, engine, runId } = setup();
    const browser = new FakeBrowser({
      sentList: {
        peopleCount: 102,
        identities: ["agency1", "agency2"],
        names: ["Person agency 1", "Person agency 2"],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      },
    });
    const controller = new NetworkController(engine, browser, () => NOW);
    // No captureBaseline: a fresh run sends, then audits once.
    engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      { sent: walkRows("agency", 2), skipped: [] },
      NOW,
    );
    expect(await controller.reconcile(runId, "audit-1", "microbatch")).toMatchObject({
      state: "progress",
      outcome: { kind: "reconciled", auditId: "audit-1", scope: "microbatch" },
    });
    expect(engine.projection(runId)).toMatchObject({ durable: 2, provisional: 0 });
    database.close();
  });

  test("resolveUnconfirmedAfterAudit marks absences after settle", async () => {
    const { database, engine, runId } = setup();
    recordBaseline(engine, runId);
    engine.recordWalkSends(
      runId,
      "hubspot-b2b-revops",
      { sent: walkRows("coo", 2), skipped: [] },
      NOW,
    );
    engine.recordAudit({
      id: "audit-empty",
      invocationId: "inv-empty",
      runId,
      baselineId: "baseline",
      peopleCount: 100,
      identities: [],
      names: [],
      complete: true,
      competingSenderAbsent: true,
      capturedAt: NOW,
    });
    engine.reconcile(runId, "baseline", "audit-empty", NOW, undefined, "microbatch");
    const controller = new NetworkController(engine, new FakeBrowser(), () => NOW);
    expect(controller.resolveUnconfirmedAfterAudit(runId, "audit-empty")).toBe(2);
    expect(engine.projection(runId)).toMatchObject({ durable: 0, provisional: 0 });
    database.close();
  });
});

describe("network controller with real engine finish path", () => {
  test("walk 15/15 then final reconcile can finish", async () => {
    const { database, engine, runId } = setup();
    const agency = walkRows("agency", 15);
    const coo = walkRows("coo", 15);
    const browser = new FakeBrowser({
      walk: {
        "hubspot-agency-ops": {
          sourceId: "hubspot-agency-ops",
          sent: agency,
          skipped: [],
          pagesWalked: 1,
          complete: true,
        },
        "hubspot-b2b-revops": {
          sourceId: "hubspot-b2b-revops",
          sent: coo,
          skipped: [],
          pagesWalked: 1,
          complete: true,
        },
      },
      sentList: {
        peopleCount: 130,
        identities: [...agency, ...coo].map((row) =>
          row.rowIdentity.replace("urn:li:fs_salesProfile:", ""),
        ),
        names: [],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      },
    });
    const controller = new NetworkController(engine, browser, () => NOW);
    await controller.captureBaseline(runId, "baseline");
    await controller.walkList(runId, "hubspot-agency-ops", 15, 0);
    await controller.walkList(runId, "hubspot-b2b-revops", 15, 0);
    expect(await controller.reconcile(runId, "final-audit", "final")).toMatchObject({
      state: "progress",
      outcome: { kind: "reconciled", scope: "final" },
    });
    engine.finish(runId, NOW);
    expect(engine.projection(runId).run.status).toBe("done");
    database.close();
  });
});

class FakeBrowser implements NetworkBrowserPort {
  private failures = new Set<string>();
  private invocation = 0;

  constructor(
    private readonly config: {
      readonly walk?: Partial<Record<SourceId, WalkListResult>>;
      readonly walkRaw?: unknown;
      readonly sentList?: SentListEvidence;
    } = {},
  ) {}

  fail(operation: string): void {
    this.failures.add(operation);
  }

  async walkList(
    source: (typeof NETWORK_SOURCES)[number],
    budget: number,
    _pacingMs: number,
  ): Promise<BrowserPortResult<unknown>> {
    const invocationId = this.nextId("walk");
    if (this.failures.has("walkList")) {
      return failed(invocationId, blocker("session_lost", "walk failed", "safe_retry"));
    }
    if (this.config.walkRaw !== undefined) {
      return succeeded(invocationId, this.config.walkRaw);
    }
    const configured = this.config.walk?.[source.id];
    if (configured === undefined) {
      return succeeded(invocationId, {
        sourceId: source.id,
        sent: [],
        skipped: [],
        pagesWalked: 1,
        complete: true,
      } satisfies WalkListResult);
    }
    const sent = configured.sent.slice(0, budget);
    return succeeded(invocationId, { ...configured, sent });
  }

  async captureSentList(): Promise<BrowserPortResult<unknown>> {
    const invocationId = this.nextId("sent");
    if (this.failures.has("captureSentList")) {
      return failed(invocationId, blocker("session_lost", "sent list failed", "safe_retry"));
    }
    return succeeded(
      invocationId,
      this.config.sentList ?? {
        peopleCount: 100,
        identities: [],
        names: [],
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      },
    );
  }

  private nextId(label: string): string {
    this.invocation += 1;
    return `${label}-${this.invocation}`;
  }
}

function succeeded(invocationId: string, value: unknown): BrowserPortResult<unknown> {
  return { status: "succeeded", invocationId, value };
}

function failed(invocationId: string, blockerValue: BrowserBlocker): BrowserPortResult<unknown> {
  return { status: "failed", invocationId, blocker: blockerValue };
}

function blocker(
  kind: BrowserBlocker["kind"],
  evidence: string,
  retryability: BrowserBlocker["retryability"],
): BrowserBlocker {
  return { kind, evidence, retryability };
}
