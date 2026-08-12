import { Database } from "bun:sqlite";
import { describe, expect, test } from "bun:test";
import { rmSync } from "node:fs";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  CommandNetworkBrowser,
  type CommandNetworkBrowserOperations,
  networkReconcile,
  networkReport,
  networkStatus,
  networkTick,
} from "../../src/commands/network.ts";
import { openDatabase } from "../../src/db/database.ts";
import { runMigrations } from "../../src/db/migrations.ts";
import {
  NETWORK_SOURCES,
  type NetworkBrowserPort,
  NetworkEngine,
  type SourceId,
} from "../../src/network/index.ts";
import { NetworkResultError, parseWalkResult } from "../../src/network/results.ts";
import type { PlaywriterClient } from "../../src/playwriter/client.ts";
import type { InvocationResult, NetworkCommand, TypedBlocker } from "../../src/playwriter/types.ts";
import { walkRows } from "../network/helpers.ts";

const NOW = "2026-08-03T12:00:00Z";

describe("network read commands", () => {
  test("return deterministic fresh-state results without browser work", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-read-"));
    try {
      const status = await networkStatus({ stateDir, localDate: "2026-08-03" });
      const report = await networkReport({ stateDir, localDate: "2026-08-03" });
      expect(status).toMatchObject({
        command: "network status",
        state: "not_started",
        target: 30,
        sources: [
          {
            id: "hubspot-agency-ops",
            name: "Consulting - HubSpot Agency Ops",
            savedSearchId: "1980844577",
            preferredAllocation: 15,
          },
          {
            id: "hubspot-b2b-revops",
            name: "Consulting - HubSpot B2B RevOps",
            savedSearchId: "1980870185",
            preferredAllocation: 15,
          },
        ],
      });
      expect(report).toMatchObject({
        command: "network report",
        state: "not_started",
        attempts: [],
      });
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });

  test("blocks a new day before browser work when the prior day has an unresolved send", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-prior-day-"));
    let sessionResolved = false;
    let browserCreated = false;
    try {
      const seeded = openDatabase(join(stateDir, "linkedin-tools.db"));
      const engine = new NetworkEngine(seeded.database);
      const runId = engine.openDailyRun("2026-08-03", NOW, "prior-run").id;
      engine.recordBaseline({
        id: "prior-baseline",
        invocationId: "inv-prior-baseline",
        runId,
        peopleCount: 100,
        competingSenderAbsent: true,
        capturedAt: NOW,
      });
      engine.recordWalkSends(
        runId,
        "hubspot-agency-ops",
        { sent: walkRows("prior", 1), skipped: [] },
        NOW,
      );
      seeded.database.close();

      await expect(
        networkTick(
          {
            allowSend: true,
            batchSize: 5,
            localDate: "2026-08-04",
            maxRealSends: 30,
            playwriterBin: "/tmp/fake-playwriter",
            sessionId: 7,
            stateDir,
            target: 30,
          },
          {
            openDatabase,
            createBrowser: () => {
              browserCreated = true;
              return {} as NetworkBrowserPort;
            },
            resolveSession: async () => {
              sessionResolved = true;
              return 7;
            },
            now: () => "2026-08-04T23:00:00Z",
            createId: () => "next-day-run",
          },
        ),
      ).rejects.toMatchObject({
        code: "NETWORK_PRIOR_DAY_NEEDS_AUDIT",
        details: {
          nextAction: "network reconcile --date <earlier-local-date>",
          runs: [
            {
              runId,
              localDate: "2026-08-03",
              durable: 0,
              planned: 0,
              provisional: 1,
            },
          ],
        },
      });
      expect(sessionResolved).toBe(false);
      expect(browserCreated).toBe(false);
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });
});

describe("walk-list command browser surface", () => {
  test("walkList invokes walk-list with source contract budget and pacing", async () => {
    const calls: unknown[] = [];
    const operations: CommandNetworkBrowserOperations = {
      invoke: async (_client, command, sessionId, input) => {
        calls.push({ command, sessionId, input });
        return fakeInvocation("walk-list", {
          sourceId: "hubspot-b2b-revops",
          sent: [{ rowIdentity: "urn:li:fs_salesProfile:ACwAA1", name: "Ada" }],
          skipped: [],
          pagesWalked: 1,
          complete: true,
        });
      },
    };
    const browser = new CommandNetworkBrowser({} as PlaywriterClient, 7, operations);
    const source = NETWORK_SOURCES.find((item) => item.id === "hubspot-b2b-revops");
    if (source === undefined) throw new Error("missing source");
    const result = await browser.walkList(source, 5, 0);
    expect(result.status).toBe("succeeded");
    expect(calls).toEqual([
      {
        command: "walk-list",
        sessionId: 7,
        input: {
          url: source.url,
          sourceContract: expect.objectContaining({
            sourceId: "hubspot-b2b-revops",
            savedSearchId: source.savedSearchId,
          }),
          budget: 5,
          pacingMs: 0,
        },
      },
    ]);
  });
});

describe("completion-capable daily network command", () => {
  test("one invocation reaches Done with exactly 30 durable requests at 15/15", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-complete-"));
    try {
      const scenario = new DailyNetworkScenario({
        "hubspot-agency-ops": walkRows("hubspot-agency", 15),
        "hubspot-b2b-revops": walkRows("hubspot-b2b", 15),
      });
      const output = await runDailyTick(stateDir, scenario);

      expect(output).toMatchObject({
        state: "done",
        target: 30,
        maxRealSends: 30,
        sendsThisTick: 30,
        realSendsToday: 30,
        projection: {
          run: { status: "done" },
          durable: 30,
          planned: 0,
          provisional: 0,
          finalReconciliation: true,
          bySource: {
            "hubspot-agency-ops": { durable: 15 },
            "hubspot-b2b-revops": { durable: 15 },
          },
        },
      });
      expect(scenario.sentBySource()).toEqual({
        "hubspot-agency-ops": 15,
        "hubspot-b2b-revops": 15,
      });
      expect(scenario.walkCalls).toBe(2);
      expect(reconciliationScopes(output)).toContain("microbatch");
      expect(reconciliationScopes(output)).toContain("final");
      assertStoredCompletion(stateDir, 30);
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });

  test("shortfall on source A carries over to source B", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-carryover-"));
    try {
      const scenario = new DailyNetworkScenario({
        "hubspot-agency-ops": walkRows("hubspot-agency", 7),
        "hubspot-b2b-revops": walkRows("hubspot-b2b", 23),
      });
      const output = await runDailyTick(stateDir, scenario);
      expect(output).toMatchObject({
        state: "done",
        sendsThisTick: 30,
        projection: {
          durable: 30,
          provisional: 0,
          finalReconciliation: true,
          bySource: {
            "hubspot-agency-ops": { durable: 7 },
            "hubspot-b2b-revops": { durable: 23 },
          },
        },
      });
      expect(scenario.sentBySource()).toEqual({
        "hubspot-agency-ops": 7,
        "hubspot-b2b-revops": 23,
      });
      assertStoredCompletion(stateDir, 30);
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });

  test("maxRealSends caps real commits and resume finishes the run", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-cap-"));
    try {
      const scenario = new DailyNetworkScenario({
        "hubspot-agency-ops": walkRows("hubspot-agency", 15),
        "hubspot-b2b-revops": walkRows("hubspot-b2b", 15),
      });
      const first = await runDailyTick(stateDir, scenario, { maxRealSends: 10 });
      expect(first).toMatchObject({
        state: "checkpoint",
        realSendsToday: 10,
        checkpoint: { kind: "max_real_sends_reached", limit: 10 },
      });
      const second = await runDailyTick(stateDir, scenario);
      expect(second).toMatchObject({
        state: "done",
        realSendsToday: 30,
        projection: { durable: 30, provisional: 0, finalReconciliation: true },
      });
      assertStoredCompletion(stateDir, 30);
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });

  test("absent after settle becomes proven_no_send and top-up reaches 30", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-topup-"));
    try {
      const scenario = new DailyNetworkScenario(
        {
          "hubspot-agency-ops": walkRows("hubspot-agency", 20),
          "hubspot-b2b-revops": walkRows("hubspot-b2b", 20),
        },
        { dropFirstSent: 5 },
      );
      const output = await runDailyTick(stateDir, scenario);
      expect(output).toMatchObject({
        state: "done",
        projection: { durable: 30, provisional: 0, finalReconciliation: true },
      });
      assertStoredCompletion(stateDir, 30);
      expect(scenario.walkCalls).toBeGreaterThan(2);
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });

  test("all sources walked with zero connectable yields typed terminal", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-empty-"));
    try {
      const scenario = new DailyNetworkScenario({
        "hubspot-agency-ops": [],
        "hubspot-b2b-revops": [],
      });
      const output = await runDailyTick(stateDir, scenario);
      expect(output).toMatchObject({
        state: "checkpoint",
        checkpoint: { kind: "source_refresh_no_progress" },
      });
      expect(scenario.walkCalls).toBeGreaterThanOrEqual(2);
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });

  test("standalone reconcile selects final for the complete 30-attempt candidate", async () => {
    const stateDir = await mkdtemp(join(tmpdir(), "linkedin-network-standalone-final-"));
    try {
      const scenario = new DailyNetworkScenario(
        {
          "hubspot-agency-ops": walkRows("hubspot-agency", 15),
          "hubspot-b2b-revops": walkRows("hubspot-b2b", 15),
        },
        { failFirstFinalAudit: true },
      );
      const interrupted = await runDailyTick(stateDir, scenario);
      expect(interrupted).toMatchObject({
        state: "checkpoint",
        checkpoint: { kind: "browser_blocker", phase: "capture_sent_list" },
        projection: {
          durable: 30,
          planned: 0,
          provisional: 0,
          finalReconciliation: false,
        },
      });

      const completed = await networkReconcile(
        {
          stateDir,
          localDate: "2026-08-03",
          playwriterBin: "/tmp/fake-playwriter",
          sessionId: 7,
        },
        {
          openDatabase,
          createBrowser: () =>
            new CommandNetworkBrowser({} as PlaywriterClient, 7, scenario.operations()),
          now: () => NOW,
          createId: () => scenario.nextControllerId(),
        },
      );
      expect(completed).toMatchObject({
        state: "done",
        result: { outcome: { kind: "reconciled", scope: "final" } },
        projection: {
          durable: 30,
          planned: 0,
          provisional: 0,
          finalReconciliation: true,
          run: { status: "done" },
        },
      });
      assertStoredCompletion(stateDir, 30);
    } finally {
      rmSync(stateDir, { recursive: true, force: true });
    }
  });
});

describe("walk engine ingestion", () => {
  test("walk outcome becomes possible then durable via sent-page audit", () => {
    const database = new Database(":memory:");
    database.exec("PRAGMA foreign_keys = ON");
    runMigrations(database);
    const engine = new NetworkEngine(database);
    const runId = engine.openDailyRun("2026-08-03", NOW, "walk-run").id;
    engine.recordBaseline({
      id: "baseline",
      invocationId: "inv-baseline",
      runId,
      peopleCount: 100,
      competingSenderAbsent: true,
      capturedAt: NOW,
    });
    const sent = walkRows("walk", 2);
    const recorded = engine.recordWalkSends(
      runId,
      "hubspot-agency-ops",
      {
        sent,
        skipped: [
          {
            rowIdentity: "urn:li:fs_salesProfile:skip1",
            name: "Skip One",
            reason: "already_pending",
          },
        ],
      },
      NOW,
    );
    expect(recorded).toEqual({ sent: 2, skipped: 1 });
    expect(engine.projection(runId).provisional).toBe(2);
    engine.recordAudit({
      id: "audit-1",
      invocationId: "inv-audit-1",
      runId,
      baselineId: "baseline",
      peopleCount: 102,
      identities: sent.map((row) => row.rowIdentity.replace("urn:li:fs_salesProfile:", "")),
      names: sent.map((row) => row.name),
      complete: true,
      competingSenderAbsent: true,
      capturedAt: NOW,
    });
    engine.reconcile(runId, "baseline", "audit-1", NOW, undefined, "microbatch");
    expect(engine.projection(runId)).toMatchObject({ durable: 2, provisional: 0 });
    expect(engine.resolveUnconfirmedAfterAudit(runId, "audit-1", NOW)).toBe(0);
    database.close();
  });

  test("unconfirmed possibles resolve to proven_no_send after settle audit", () => {
    const database = new Database(":memory:");
    database.exec("PRAGMA foreign_keys = ON");
    runMigrations(database);
    const engine = new NetworkEngine(database);
    const runId = engine.openDailyRun("2026-08-03", NOW, "absent-run").id;
    engine.recordBaseline({
      id: "baseline",
      invocationId: "inv-baseline",
      runId,
      peopleCount: 100,
      competingSenderAbsent: true,
      capturedAt: NOW,
    });
    const sent = walkRows("gone", 3);
    engine.recordWalkSends(runId, "hubspot-b2b-revops", { sent, skipped: [] }, NOW);
    engine.recordAudit({
      id: "audit-empty",
      invocationId: "inv-audit-empty",
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
    expect(engine.resolveUnconfirmedAfterAudit(runId, "audit-empty", NOW)).toBe(3);
    expect(engine.projection(runId)).toMatchObject({ durable: 0, provisional: 0 });
    database.close();
  });

  test("recordWalkSends is idempotent for the same person", () => {
    const database = new Database(":memory:");
    database.exec("PRAGMA foreign_keys = ON");
    runMigrations(database);
    const engine = new NetworkEngine(database);
    const runId = engine.openDailyRun("2026-08-03", NOW, "idem-run").id;
    engine.recordBaseline({
      id: "baseline",
      invocationId: "inv-baseline",
      runId,
      peopleCount: 100,
      competingSenderAbsent: true,
      capturedAt: NOW,
    });
    const row = walkRows("same", 1);
    expect(
      engine.recordWalkSends(runId, "hubspot-agency-ops", { sent: row, skipped: [] }, NOW).sent,
    ).toBe(1);
    expect(
      engine.recordWalkSends(runId, "hubspot-agency-ops", { sent: row, skipped: [] }, NOW).sent,
    ).toBe(0);
    expect(engine.projection(runId).provisional).toBe(1);
    database.close();
  });
});

describe("walk parser", () => {
  test("rejects malformed walk rows", () => {
    expect(() =>
      parseWalkResult(
        {
          sourceId: "hubspot-agency-ops",
          sent: [{ rowIdentity: "bad", name: "X" }],
          skipped: [],
          pagesWalked: 1,
          complete: true,
        },
        { id: "hubspot-agency-ops" },
      ),
    ).toThrow(NetworkResultError);
  });
});

type ScenarioRows = Readonly<
  Record<SourceId, readonly { readonly rowIdentity: string; readonly name: string }[]>
>;

class DailyNetworkScenario {
  readonly trace: string[] = [];
  readonly auditSentCounts: number[] = [];
  walkCalls = 0;

  private readonly remaining = new Map<SourceId, { rowIdentity: string; name: string }[]>();
  private readonly sentBySourceCounts = new Map<SourceId, number>();
  private readonly confirmed = new Map<
    string,
    { rowIdentity: string; name: string; salesLeadId: string }
  >();
  private activeOperations = 0;
  private finalAuditFailureInjected = false;
  private invocationSequence = 0;
  private controllerIdSequence = 0;
  private dropped = 0;

  constructor(
    rows: ScenarioRows,
    private readonly options: {
      readonly failFirstFinalAudit?: boolean;
      readonly dropFirstSent?: number;
    } = {},
  ) {
    for (const source of NETWORK_SOURCES) {
      this.remaining.set(source.id, [...rows[source.id]]);
      this.sentBySourceCounts.set(source.id, 0);
    }
  }

  operations(): CommandNetworkBrowserOperations {
    return {
      invoke: async (_client, command, sessionId, input = {}) => {
        expect(sessionId).toBe(7);
        this.activeOperations += 1;
        try {
          const invocationId = this.nextInvocation(command.replaceAll("-", "_"));
          if (command === "walk-list") {
            const contract = input.sourceContract;
            const budget = typeof input.budget === "number" ? input.budget : 0;
            if (
              contract === undefined ||
              typeof contract !== "object" ||
              contract === null ||
              !("sourceId" in contract) ||
              typeof contract.sourceId !== "string"
            ) {
              throw new Error("walk-list missing sourceContract");
            }
            return this.handleWalk(invocationId, contract.sourceId as SourceId, budget);
          }
          if (command === "capture-sent-list") {
            return this.handleAudit(command, invocationId);
          }
          this.trace.push(`browser:${command}`);
          return fakeInvocation(command, {}, { invocationId });
        } finally {
          this.activeOperations -= 1;
        }
      },
    };
  }

  nextControllerId(): string {
    this.controllerIdSequence += 1;
    return `controller-id-${this.controllerIdSequence.toString().padStart(6, "0")}`;
  }

  sentBySource(): Record<SourceId, number> {
    return Object.fromEntries(this.sentBySourceCounts) as Record<SourceId, number>;
  }

  private handleWalk(invocationId: string, sourceId: SourceId, budget: number): InvocationResult {
    this.walkCalls += 1;
    this.trace.push(`walk:${sourceId}:${budget}`);
    const queue = this.remaining.get(sourceId) ?? [];
    const sent = queue.splice(0, budget);
    for (const row of sent) {
      const salesLeadId = row.rowIdentity.replace("urn:li:fs_salesProfile:", "");
      if (this.options.dropFirstSent !== undefined && this.dropped < this.options.dropFirstSent) {
        this.dropped += 1;
        continue;
      }
      this.confirmed.set(salesLeadId, { ...row, salesLeadId });
      this.sentBySourceCounts.set(sourceId, (this.sentBySourceCounts.get(sourceId) ?? 0) + 1);
    }
    return fakeInvocation(
      "walk-list",
      {
        sourceId,
        sent,
        skipped: [],
        pagesWalked: 1,
        complete: queue.length === 0,
      },
      { invocationId },
    );
  }

  private handleAudit(command: NetworkCommand, invocationId: string): InvocationResult {
    const people = [...this.confirmed.values()];
    this.auditSentCounts.push(people.length);
    this.trace.push(`audit:${people.length}`);
    if (
      this.options.failFirstFinalAudit === true &&
      !this.finalAuditFailureInjected &&
      people.length === 30 &&
      this.auditSentCounts.filter((count) => count === 30).length === 2
    ) {
      this.finalAuditFailureInjected = true;
      return fakeInvocation(command, undefined, {
        outcome: "failed",
        blocker: blocker(
          "session_lost",
          "simulated interruption before final audit persistence",
          "safe_retry",
        ),
        result: null,
        invocationId,
      });
    }
    return fakeInvocation(
      command,
      {
        peopleCount: 100 + people.length,
        identities: people.map((person) => person.salesLeadId),
        names: people.map((person) => person.name),
        complete: true,
        competingSenderAbsent: true,
        contradictoryEvidence: false,
      },
      { invocationId },
    );
  }

  private nextInvocation(label: string): string {
    this.invocationSequence += 1;
    return `scenario_${label}_${this.invocationSequence.toString().padStart(6, "0")}`;
  }
}

async function runDailyTick(
  stateDir: string,
  scenario: DailyNetworkScenario,
  overrides: { readonly batchSize?: number; readonly maxRealSends?: number } = {},
): Promise<unknown> {
  return networkTick(
    {
      stateDir,
      localDate: "2026-08-03",
      allowSend: true,
      target: 30,
      batchSize: overrides.batchSize ?? 5,
      maxRealSends: overrides.maxRealSends ?? 30,
      playwriterBin: "/tmp/fake-playwriter",
      sessionId: 7,
    },
    {
      openDatabase,
      createBrowser: () =>
        new CommandNetworkBrowser({} as PlaywriterClient, 7, scenario.operations()),
      now: () => NOW,
      createId: () => scenario.nextControllerId(),
      pacingMs: 0,
      settleWaitMs: 0,
    },
  );
}

function reconciliationScopes(output: unknown): readonly string[] {
  if (output === null || typeof output !== "object" || !("steps" in output)) return [];
  const steps = output.steps;
  if (!Array.isArray(steps)) return [];
  return steps.flatMap((step) => {
    if (step === null || typeof step !== "object" || !("outcome" in step)) return [];
    const outcome = step.outcome;
    if (
      outcome === null ||
      typeof outcome !== "object" ||
      !("kind" in outcome) ||
      outcome.kind !== "reconciled" ||
      !("scope" in outcome) ||
      typeof outcome.scope !== "string"
    ) {
      return [];
    }
    return [outcome.scope];
  });
}

function assertStoredCompletion(stateDir: string, expectedRealSends: number): void {
  const opened = openDatabase(join(stateDir, "linkedin-tools.db"));
  try {
    const run = opened.database
      .query<{ id: string; status: string }, []>(
        "SELECT id, status FROM daily_runs WHERE local_date = '2026-08-03'",
      )
      .get();
    expect(run?.status).toBe("done");
    const realSends =
      opened.database
        .query<{ count: number }, [string]>(
          `SELECT COUNT(*) AS count FROM send_attempts
           WHERE run_id = ? AND commit_started_at IS NOT NULL
             AND state IN ('possible', 'durable')`,
        )
        .get(run?.id ?? "")?.count ?? 0;
    expect(realSends).toBe(expectedRealSends);
    const durable =
      opened.database
        .query<{ count: number }, [string]>(
          `SELECT COUNT(*) AS count FROM send_attempts
           WHERE run_id = ? AND state = 'durable'`,
        )
        .get(run?.id ?? "")?.count ?? 0;
    expect(durable).toBe(30);
  } finally {
    opened.database.close();
  }
}

function fakeInvocation(
  command: NetworkCommand | string,
  data: unknown,
  overrides: {
    readonly invocationId?: string;
    readonly outcome?: "succeeded" | "failed" | "critical_uncertainty";
    readonly blocker?: TypedBlocker;
    readonly result?: unknown;
  } = {},
): InvocationResult {
  const invocationId = overrides.invocationId ?? `inv_${command}`;
  const outcome = overrides.outcome ?? "succeeded";
  const result =
    overrides.result === undefined ? (data === undefined ? null : { data }) : overrides.result;
  const receipt = {
    schemaVersion: 1 as const,
    invocationId,
    command: command as NetworkCommand,
    definitionId: "test",
    action: "none" as const,
    startedAt: NOW,
    finishedAt: NOW,
    exitCode: outcome === "succeeded" ? 0 : 1,
    outcome,
    result: result as InvocationResult["receipt"]["result"],
    ...(overrides.blocker === undefined ? {} : { blocker: overrides.blocker }),
  };
  return {
    directory: "/tmp",
    config: {
      schemaVersion: 1,
      invocationId,
      command: command as NetworkCommand,
      definitionId: "test",
      action: "none",
      phaseContract: [],
      createdAt: NOW,
      sessionId: 7,
      input: {},
    },
    receipt,
    progress: [],
    stdout: "",
    stderr: "",
  };
}

function blocker(
  kind: TypedBlocker["kind"],
  evidence: string,
  retryability: TypedBlocker["retryability"],
): TypedBlocker {
  return { kind, evidence, retryability };
}
