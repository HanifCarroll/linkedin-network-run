import type { Database } from "bun:sqlite";
import { join } from "node:path";
import { CliError } from "../core/errors.ts";
import { type OpenDatabase, openDatabase } from "../db/database.ts";
import { PREFERRED_PER_SOURCE, SEND_PACING_MS } from "../network/config.ts";
import {
  type BrowserPortResult,
  type DailyRun,
  NETWORK_SOURCES,
  type NetworkBrowserPort,
  NetworkController,
  NetworkEngine,
  type ParkedMissedRun,
  PriorDayNeedsAuditError,
  type ReconciliationScope,
  type RunProjection,
  SOURCES,
  type SourceId,
  type TickResult,
} from "../network/index.ts";
import { PlaywriterClient } from "../playwriter/client.ts";
import { invokeNetworkStep } from "../playwriter/network.ts";
import { networkSourceContract } from "../playwriter/source-capture.ts";
import type {
  InvocationResult,
  NetworkCommand,
  NetworkSourceContract,
} from "../playwriter/types.ts";
import { resolvePlaywriterSession, type SessionResolutionRequest } from "./sessions.ts";
import type {
  NetworkReadInput,
  NetworkReconcileInput,
  NetworkRunEndInput,
  NetworkTickInput,
} from "./types.ts";

const DAILY_TARGET = 30;
const MAX_BATCH_SIZE = 5;
const MAX_ORCHESTRATION_STEPS = 500;
const DEFAULT_SETTLE_WAIT_MS = 60_000;

export type NetworkDependencies = {
  readonly openDatabase: (path: string) => OpenDatabase;
  readonly createBrowser: (
    input: ResolvedNetworkTickInput | ResolvedNetworkReconcileInput,
  ) => NetworkBrowserPort;
  readonly resolveSession?: (request: SessionResolutionRequest) => Promise<number>;
  readonly now: () => string;
  readonly createId: () => string;
  readonly pacingMs?: number;
  readonly settleWaitMs?: number;
};

type ResolvedNetworkTickInput = Omit<NetworkTickInput, "sessionId"> & {
  readonly sessionId: number;
};
type ResolvedNetworkReconcileInput = Omit<NetworkReconcileInput, "sessionId"> & {
  readonly sessionId: number;
};

const defaultDependencies: NetworkDependencies = {
  openDatabase,
  createBrowser: (input) =>
    new CommandNetworkBrowser(
      new PlaywriterClient({
        executable: input.playwriterBin,
        invocationRoot: join(input.stateDir, "receipts", "playwriter", "network"),
        stateDir: input.stateDir,
      }),
      input.sessionId,
    ),
  resolveSession: resolvePlaywriterSession,
  now: () => new Date().toISOString(),
  createId: () => crypto.randomUUID(),
};

export async function networkStatus(
  input: NetworkReadInput,
  dependencies: Pick<NetworkDependencies, "openDatabase"> = defaultDependencies,
): Promise<unknown> {
  const opened = dependencies.openDatabase(databasePath(input.stateDir));
  try {
    const runId = findRunId(opened.database, input.localDate);
    if (runId === null) return notStarted(input.localDate);
    const engine = new NetworkEngine(opened.database);
    const projection = engine.projection(runId);
    const lastStop = engine.latestTickStop(runId);
    return {
      command: "network status",
      localDate: input.localDate,
      state: projection.run.status,
      target: 30,
      preferredAllocation: 15,
      sources: sourceContract(),
      projection,
      lastStop,
    };
  } finally {
    opened.database.close();
  }
}

export async function networkReport(
  input: NetworkReadInput,
  dependencies: Pick<NetworkDependencies, "openDatabase"> = defaultDependencies,
): Promise<unknown> {
  const opened = dependencies.openDatabase(databasePath(input.stateDir));
  try {
    const runId = findRunId(opened.database, input.localDate);
    if (runId === null) {
      return {
        command: "network report",
        localDate: input.localDate,
        state: "not_started",
        target: 30,
        sources: sourceContract(),
        attempts: [],
        lastStop: null,
      };
    }
    const engine = new NetworkEngine(opened.database);
    const report = parseReport(engine.reportBytes(runId));
    return {
      command: "network report",
      localDate: input.localDate,
      state: engine.projection(runId).run.status,
      target: 30,
      sources: sourceContract(),
      report,
      lastStop: engine.latestTickStop(runId),
    };
  } finally {
    opened.database.close();
  }
}

export async function networkTick(
  input: NetworkTickInput,
  dependencies: NetworkDependencies = defaultDependencies,
): Promise<unknown> {
  if (input.allowSend !== true) {
    throw new CliError("SEND_NOT_AUTHORIZED", "network tick requires --allow-send", {
      exitCode: 3,
    });
  }
  assertTickBounds(input);
  const opened = dependencies.openDatabase(databasePath(input.stateDir));
  try {
    const engine = new NetworkEngine(opened.database);
    let prepared: {
      readonly run: DailyRun;
      readonly parkedRuns: readonly ParkedMissedRun[];
    };
    try {
      prepared = engine.prepareDailyRun(
        input.localDate,
        dependencies.now(),
        dependencies.createId(),
      );
    } catch (error) {
      if (error instanceof PriorDayNeedsAuditError) {
        throw new CliError(
          "NETWORK_PRIOR_DAY_NEEDS_AUDIT",
          "an earlier local-day run has an unresolved send; audit it before starting a new day",
          {
            details: {
              runs: error.runs,
              nextAction: "network reconcile --date <earlier-local-date>",
            },
            exitCode: 4,
          },
        );
      }
      throw error;
    }
    const resolvedInput = await resolveNetworkSession(input, dependencies);
    const browser = dependencies.createBrowser(resolvedInput);
    const controller = new NetworkController(
      engine,
      browser,
      dependencies.now,
      dependencies.createId,
    );
    const run = controller.open(input.localDate);
    const steps: TickResult[] = [];
    let projection = controller.status(run.id);
    let sendsThisTick = 0;
    let completedMicrobatches = 0;
    let auditsThisTick = 0;
    const dayTransition =
      prepared.parkedRuns.length === 0 ? {} : { parkedPriorRuns: prepared.parkedRuns };
    const pacingMs = dependencies.pacingMs ?? SEND_PACING_MS;
    const settleWaitMs = dependencies.settleWaitMs ?? DEFAULT_SETTLE_WAIT_MS;
    const exhaustedSources = new Set<SourceId>();

    const output = (
      state: "checkpoint" | "terminal" | "done",
      extra?: Readonly<Record<string, unknown>>,
    ): unknown => {
      if (state !== "done") {
        engine.recordTickStop(run.id, state, extra, dependencies.now(), dependencies.createId());
      }
      return tickOutput(
        input,
        state,
        sendsThisTick,
        steps,
        controller.status(run.id),
        countRealSends(opened.database, run.id),
        auditsThisTick,
        completedMicrobatches,
        { ...dayTransition, ...(extra ?? {}) },
      );
    };

    const audit = async (
      prefix: string,
      scope: ReconciliationScope,
    ): Promise<{ readonly stopped: unknown | null; readonly auditId: string | null }> => {
      const auditId = receiptId(prefix, dependencies.createId());
      const result = await controller.reconcile(run.id, auditId, scope);
      auditsThisTick += 1;
      steps.push(result);
      if (result.state === "checkpoint") {
        return {
          stopped: output("checkpoint", { checkpoint: result.checkpoint }),
          auditId: null,
        };
      }
      if (result.state === "terminal") {
        return { stopped: output("terminal", { terminal: result.terminal }), auditId: null };
      }
      if (result.state === "done") return { stopped: output("done"), auditId: null };
      if (result.state === "progress" && result.outcome.kind === "reconciled") {
        return { stopped: null, auditId: result.outcome.auditId };
      }
      return { stopped: null, auditId };
    };

    if (projection.run.status === "done") {
      return output("done");
    }
    if (projection.run.status === "blocked") {
      return output("checkpoint", {
        checkpoint: { kind: "run_blocked", runId: run.id },
      });
    }

    // No pre-run baseline gate: acfb5f5 dropped the pre-run sent-list baseline
    // audit in favor of per-attempt exact-match reconciliation (baseline_id is
    // nullable). A run may hold open attempts with no baseline and still
    // reconcile correctly.

    for (
      let orchestrationStep = 0;
      orchestrationStep < MAX_ORCHESTRATION_STEPS;
      orchestrationStep += 1
    ) {
      projection = controller.status(run.id);
      if (projection.run.status === "done") return output("done");

      if (countRealSends(opened.database, run.id) > DAILY_TARGET) {
        throw new CliError(
          "NETWORK_PROTOCOL_ERROR",
          "durable commit-start count exceeds the daily hard cap",
          { details: { runId: run.id }, exitCode: 4 },
        );
      }

      if (isCompletionCandidate(projection)) {
        if (!projection.finalReconciliation) {
          const stopped = await audit("final-audit", "final");
          if (stopped.stopped !== null) return stopped.stopped;
          projection = controller.status(run.id);
        }
        if (isCompletionCandidate(projection) && projection.finalReconciliation) {
          engine.finish(run.id, dependencies.now());
          return output("done");
        }
        return output("checkpoint", {
          checkpoint: { kind: "final_reconciliation_incomplete" },
        });
      }

      let realSends = countRealSends(opened.database, run.id);
      if (realSends >= input.maxRealSends) {
        if (projection.provisional > 0) {
          await sleep(settleWaitMs);
          const micro = await audit("audit", "microbatch");
          if (micro.stopped !== null) return micro.stopped;
          if (micro.auditId !== null) {
            engine.resolveUnconfirmedAfterAudit(run.id, micro.auditId, dependencies.now());
          }
          completedMicrobatches += 1;
          projection = controller.status(run.id);
          if (isCompletionCandidate(projection)) continue;
        }
        return output("checkpoint", {
          checkpoint: {
            kind: "max_real_sends_reached",
            limit: input.maxRealSends,
            realSendsToday: realSends,
          },
        });
      }

      let budget = Math.max(0, DAILY_TARGET - projection.durable - projection.provisional);
      let walkProgressed = false;
      let anyConnectable = false;

      while (budget > 0 && realSends < input.maxRealSends) {
        const source = pickSource(projection, exhaustedSources);
        if (source === null) break;
        const preferredRemaining = Math.max(
          0,
          PREFERRED_PER_SOURCE -
            (projection.bySource[source].durable + projection.bySource[source].possible),
        );
        const sourceBudget = Math.min(
          budget,
          preferredRemaining > 0 ? preferredRemaining : budget,
          input.maxRealSends - realSends,
        );
        if (sourceBudget < 1) {
          exhaustedSources.add(source);
          continue;
        }

        const walked = await controller.walkList(run.id, source, sourceBudget, pacingMs);
        steps.push(walked);
        if (walked.state === "terminal") {
          return output("terminal", { terminal: walked.terminal });
        }
        if (walked.state === "done") return output("done");
        if (walked.state === "checkpoint") {
          return output("checkpoint", { checkpoint: walked.checkpoint });
        }
        if (walked.state !== "progress" || walked.outcome.kind !== "walked") {
          throw new CliError(
            "NETWORK_PROTOCOL_ERROR",
            "walk orchestration returned an unsupported progress outcome",
            {
              details: {
                runId: run.id,
                outcome: walked.state === "progress" ? walked.outcome.kind : "non_progress",
              },
              exitCode: 4,
            },
          );
        }

        const sent = walked.outcome.sent;
        sendsThisTick += sent;
        realSends = countRealSends(opened.database, run.id);
        projection = controller.status(run.id);
        budget = Math.max(0, DAILY_TARGET - projection.durable - projection.provisional);

        if (sent > 0) {
          walkProgressed = true;
          anyConnectable = true;
          continue;
        }
        if (walked.outcome.skipped > 0) {
          anyConnectable = true;
        }
        if (walked.outcome.complete) {
          exhaustedSources.add(source);
        } else {
          exhaustedSources.add(source);
        }
        if (exhaustedSources.size >= NETWORK_SOURCES.length) break;
      }

      projection = controller.status(run.id);
      realSends = countRealSends(opened.database, run.id);
      if (projection.provisional > 0) {
        await sleep(settleWaitMs);
        const micro = await audit("audit", "microbatch");
        if (micro.stopped !== null) return micro.stopped;
        if (micro.auditId !== null) {
          engine.resolveUnconfirmedAfterAudit(run.id, micro.auditId, dependencies.now());
        }
        completedMicrobatches += 1;
        projection = controller.status(run.id);
        realSends = countRealSends(opened.database, run.id);
        if (isCompletionCandidate(projection)) continue;
        if (
          projection.durable + projection.provisional < DAILY_TARGET &&
          realSends < input.maxRealSends
        ) {
          exhaustedSources.clear();
          continue;
        }
      }

      if (isCompletionCandidate(projection)) continue;

      if (!walkProgressed && exhaustedSources.size >= NETWORK_SOURCES.length) {
        return output("checkpoint", {
          checkpoint: {
            kind: "source_refresh_no_progress",
            sourceIds: NETWORK_SOURCES.map((source) => source.id),
          },
        });
      }

      if (realSends >= input.maxRealSends) {
        return output("checkpoint", {
          checkpoint: {
            kind: "max_real_sends_reached",
            limit: input.maxRealSends,
            realSendsToday: realSends,
          },
        });
      }

      if (!anyConnectable && exhaustedSources.size >= NETWORK_SOURCES.length) {
        return output("terminal", {
          terminal: {
            kind: "no_eligible_capacity",
            remainingCapacity: projection.remainingCapacity,
            exhaustedSources: NETWORK_SOURCES.map((source) => source.id),
          },
        });
      }

      if (!walkProgressed) {
        return output("checkpoint", {
          checkpoint: {
            kind: "source_refresh_no_progress",
            sourceIds: NETWORK_SOURCES.map((source) => source.id),
          },
        });
      }
    }

    return output("checkpoint", {
      checkpoint: {
        kind: "orchestration_step_limit",
        limit: MAX_ORCHESTRATION_STEPS,
      },
    });
  } finally {
    opened.database.close();
  }
}

export async function networkReconcile(
  input: NetworkReconcileInput,
  dependencies: NetworkDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = dependencies.openDatabase(databasePath(input.stateDir));
  try {
    const runId = findRunId(opened.database, input.localDate);
    if (runId === null) {
      throw new CliError("NETWORK_RUN_NOT_FOUND", "no network run exists for the requested date", {
        details: { localDate: input.localDate },
        exitCode: 4,
      });
    }
    const engine = new NetworkEngine(opened.database);
    const durable = engine.readControllerState(runId);
    let projection = engine.projection(runId);
    const scope = standaloneReconciliationScope(projection, countRealSends(opened.database, runId));
    const controller = new NetworkController(
      engine,
      dependencies.createBrowser(await resolveNetworkSession(input, dependencies)),
      dependencies.now,
      dependencies.createId,
    );
    const result = await controller.reconcile(
      runId,
      durable.pendingAudit?.id ?? receiptId("audit", dependencies.createId()),
      scope,
    );
    if (result.state === "progress" && result.outcome.kind === "reconciled") {
      engine.resolveUnconfirmedAfterAudit(runId, result.outcome.auditId, dependencies.now());
    }
    projection = engine.projection(runId);
    let state: "checkpoint" | "progress" | "done" =
      result.state === "checkpoint" ? "checkpoint" : "progress";
    if (
      result.state !== "checkpoint" &&
      projection.durable === 30 &&
      projection.planned === 0 &&
      projection.provisional === 0 &&
      projection.finalReconciliation
    ) {
      engine.finish(runId, dependencies.now());
      projection = engine.projection(runId);
      state = "done";
    }
    return {
      command: "network reconcile",
      localDate: input.localDate,
      state,
      result,
      projection,
    };
  } finally {
    opened.database.close();
  }
}

async function resolveNetworkSession<T extends NetworkTickInput | NetworkReconcileInput>(
  input: T,
  dependencies: Pick<NetworkDependencies, "resolveSession">,
): Promise<Omit<T, "sessionId"> & { readonly sessionId: number }> {
  const sessionId = await (dependencies.resolveSession ?? resolvePlaywriterSession)({
    workflow: "network",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
    warn: (message) => console.error(`[network] ${message}`),
  });
  return { ...input, sessionId };
}

export async function networkRunEnd(
  input: NetworkRunEndInput,
  dependencies: Pick<NetworkDependencies, "openDatabase" | "now"> = defaultDependencies,
): Promise<unknown> {
  const opened = dependencies.openDatabase(databasePath(input.stateDir));
  try {
    const engine = new NetworkEngine(opened.database);
    const run = engine.endRun(input.localDate, input.reason, dependencies.now());
    return { command: "network run-end", localDate: input.localDate, run };
  } finally {
    opened.database.close();
  }
}

export type CommandNetworkBrowserOperations = {
  readonly invoke: typeof invokeNetworkStep;
};

const commandNetworkBrowserOperations: CommandNetworkBrowserOperations = {
  invoke: invokeNetworkStep,
};

export class CommandNetworkBrowser implements NetworkBrowserPort {
  constructor(
    private readonly client: PlaywriterClient,
    private readonly sessionId: number,
    private readonly operations: CommandNetworkBrowserOperations = commandNetworkBrowserOperations,
  ) {}

  async walkList(
    source: (typeof NETWORK_SOURCES)[number],
    budget: number,
    pacingMs: number,
  ): Promise<BrowserPortResult<unknown>> {
    const contract = networkSourceContract(source.id);
    if (
      contract.sourceName !== source.name ||
      contract.savedSearchId !== source.savedSearchId ||
      contract.searchUrl !== source.url ||
      contract.contractVersion !== source.sourceContractVersion
    ) {
      throw new TypeError("command source does not match the exact Playwriter source contract");
    }
    const invokeOnce = async (): Promise<BrowserPortResult<unknown>> => {
      try {
        const invocation = await this.operations.invoke(this.client, "walk-list", this.sessionId, {
          url: contract.searchUrl,
          sourceContract: contract,
          budget,
          pacingMs,
        });
        if (
          invocation.receipt.outcome !== "succeeded" ||
          invocation.receipt.blocker !== undefined ||
          invocation.receipt.result === null ||
          !("data" in invocation.receipt.result)
        ) {
          return preCommitInvocationFailure(invocation, "walk_list");
        }
        return {
          status: "succeeded",
          invocationId: invocation.receipt.invocationId,
          value: invocation.receipt.result.data,
        };
      } catch (error) {
        return preCommitException("walk_list", error);
      }
    };
    const first = await invokeOnce();
    // The playwriter extension drops its CDP connection mid-walk (page closed,
    // session lost). One reset + retry recovers without human intervention.
    if (
      first.status === "failed" &&
      (first.blocker.kind === "page_closed" || first.blocker.kind === "session_lost")
    ) {
      try {
        await this.client.resetSession(this.sessionId);
      } catch {
        return first;
      }
      return invokeOnce();
    }
    return first;
  }

  async captureSentList(): Promise<BrowserPortResult<unknown>> {
    const url = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";
    const navigation = await this.invokeVoid("navigate-sent-list", { url });
    if (navigation.status !== "succeeded") return navigation;
    return this.invokeData("capture-sent-list", { url });
  }

  private async invokeVoid(
    command: NetworkCommand,
    input: {
      readonly url?: string;
      readonly sourceContract?: NetworkSourceContract;
    },
  ): Promise<BrowserPortResult<void>> {
    try {
      const invocation = await this.operations.invoke(this.client, command, this.sessionId, input);
      if (invocation.receipt.outcome !== "succeeded" || invocation.receipt.blocker !== undefined) {
        return preCommitInvocationFailure(invocation, command);
      }
      return {
        status: "succeeded",
        invocationId: invocation.receipt.invocationId,
        value: undefined,
      };
    } catch (error) {
      return preCommitException(command, error);
    }
  }

  private async invokeData(
    command: NetworkCommand,
    input: { readonly url?: string },
  ): Promise<BrowserPortResult<unknown>> {
    try {
      const invocation = await this.operations.invoke(this.client, command, this.sessionId, input);
      if (
        invocation.receipt.outcome !== "succeeded" ||
        invocation.receipt.blocker !== undefined ||
        invocation.receipt.result === null ||
        !("data" in invocation.receipt.result)
      ) {
        return preCommitInvocationFailure(invocation, command);
      }
      return {
        status: "succeeded",
        invocationId: invocation.receipt.invocationId,
        value: invocation.receipt.result.data,
      };
    } catch (error) {
      return preCommitException(command, error);
    }
  }
}

function pickSource(projection: RunProjection, exhausted: ReadonlySet<SourceId>): SourceId | null {
  const ranked = NETWORK_SOURCES.map((source, order) => {
    const used = projection.bySource[source.id].durable + projection.bySource[source.id].possible;
    const preferredRemaining = Math.max(0, PREFERRED_PER_SOURCE - used);
    return {
      id: source.id,
      preferredRemaining,
      used,
      order,
      exhausted: exhausted.has(source.id),
    };
  })
    .filter((source) => !source.exhausted)
    .sort(
      (left, right) =>
        right.preferredRemaining - left.preferredRemaining ||
        left.used - right.used ||
        left.order - right.order,
    );
  return ranked[0]?.id ?? null;
}

function sleep(ms: number): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

function preCommitInvocationFailure(
  invocation: InvocationResult,
  phase: string,
): BrowserPortResult<never> {
  const blocker = invocation.receipt.blocker;
  const fallbackEvidence =
    blocker?.evidence ??
    (() => {
      const tail = invocation.stderr.trim().split("\n").filter(Boolean).slice(-3).join(" | ");
      return tail.length > 0
        ? `Playwriter ${phase} ended with ${invocation.receipt.outcome} before send commit: ${tail}`
        : `Playwriter ${phase} ended with ${invocation.receipt.outcome} before send commit`;
    })();
  return {
    status: "failed",
    invocationId: invocation.receipt.invocationId,
    blocker: {
      kind: blocker?.kind ?? "browser_failure",
      evidence: fallbackEvidence,
      retryability: blocker?.retryability === "terminal" ? "terminal" : "safe_retry",
    },
  };
}

function preCommitException(phase: string, error: unknown): BrowserPortResult<never> {
  return {
    status: "failed",
    invocationId: `adapter:${phase}`,
    blocker: {
      kind: "browser_exception",
      evidence: conciseError(error),
      retryability: "safe_retry",
    },
  };
}

function conciseError(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).trim().slice(0, 500);
}

function databasePath(stateDir: string): string {
  return join(stateDir, "linkedin-tools.db");
}

function findRunId(database: Database, localDate: string): string | null {
  return (
    database
      .query<{ id: string }, [string]>("SELECT id FROM daily_runs WHERE local_date = ?")
      .get(localDate)?.id ?? null
  );
}

function parseReport(bytes: Uint8Array): unknown {
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new CliError("NETWORK_REPORT_INVALID", "network report bytes are not valid JSON", {
      exitCode: 4,
    });
  }
}

function assertTickBounds(input: NetworkTickInput): void {
  if (input.target !== DAILY_TARGET) {
    throw new CliError("INVALID_ARGUMENT", "network target must be exactly 30", { exitCode: 2 });
  }
  if (
    !Number.isSafeInteger(input.batchSize) ||
    input.batchSize < 1 ||
    input.batchSize > MAX_BATCH_SIZE
  ) {
    throw new CliError("INVALID_ARGUMENT", "network batch size must be between 1 and 5", {
      exitCode: 2,
    });
  }
  if (
    !Number.isSafeInteger(input.maxRealSends) ||
    input.maxRealSends < 1 ||
    input.maxRealSends > DAILY_TARGET
  ) {
    throw new CliError("INVALID_ARGUMENT", "network real-send cap must be between 1 and 30", {
      exitCode: 2,
    });
  }
}

function isCompletionCandidate(projection: RunProjection): boolean {
  return (
    projection.durable === DAILY_TARGET && projection.planned === 0 && projection.provisional === 0
  );
}

function standaloneReconciliationScope(
  projection: RunProjection,
  realSendsToday: number,
): ReconciliationScope {
  const reservedOrAttempted = projection.durable + projection.planned + projection.provisional;
  return projection.run.status === "active" &&
    realSendsToday === DAILY_TARGET &&
    reservedOrAttempted === DAILY_TARGET &&
    projection.planned === 0
    ? "final"
    : "microbatch";
}

function countRealSends(database: Database, runId: string): number {
  return (
    database
      .query<{ count: number }, [string]>(
        `SELECT COUNT(*) AS count FROM send_attempts
         WHERE run_id = ? AND commit_started_at IS NOT NULL
           AND state IN ('possible', 'durable')`,
      )
      .get(runId)?.count ?? 0
  );
}

function sourceContract(): readonly unknown[] {
  return SOURCES.map((source) => ({
    id: source.id,
    name: source.name,
    savedSearchId: source.savedSearchId,
    preferredAllocation: source.preferredAllocation,
  }));
}

function notStarted(localDate: string): unknown {
  return {
    command: "network status",
    localDate,
    state: "not_started",
    target: 30,
    preferredAllocation: 15,
    sources: sourceContract(),
    projection: null,
    lastStop: null,
  };
}

function tickOutput(
  input: NetworkTickInput,
  state: "checkpoint" | "terminal" | "done",
  sendsThisTick: number,
  steps: readonly TickResult[],
  projection: unknown,
  realSendsToday: number,
  auditsThisTick: number,
  completedMicrobatches: number,
  extra?: Readonly<Record<string, unknown>>,
): unknown {
  const checkpoint = extra?.checkpoint as
    | {
        readonly phase?: string;
        readonly blocker?: { readonly kind?: string; readonly evidence?: string };
      }
    | undefined;
  const terminal = extra?.terminal as { readonly reason?: string } | undefined;
  const summary = humanTickSummary({
    state,
    sendsThisTick,
    realSendsToday,
    target: input.target,
    ...(checkpoint === undefined ? {} : { checkpoint }),
    ...(terminal === undefined ? {} : { terminal }),
  });
  return {
    command: "network tick",
    localDate: input.localDate,
    state,
    summary,
    target: input.target,
    batchSize: input.batchSize,
    maxRealSends: input.maxRealSends,
    sendsThisTick,
    realSendsToday,
    auditsThisTick,
    completedMicrobatches,
    sources: sourceContract(),
    steps,
    projection,
    ...(extra ?? {}),
  };
}

function humanTickSummary(info: {
  readonly state: "checkpoint" | "terminal" | "done";
  readonly sendsThisTick: number;
  readonly realSendsToday: number;
  readonly target: number;
  readonly checkpoint?: {
    readonly phase?: string;
    readonly blocker?: { readonly kind?: string; readonly evidence?: string };
  };
  readonly terminal?: { readonly reason?: string };
}): string {
  const progress = `${info.realSendsToday}/${info.target} durable`;
  if (info.state === "done") return `done: ${progress}, target reached`;
  if (info.state === "terminal") {
    return `terminal: ${progress}; ${info.terminal?.reason ?? "run ended"}`;
  }
  const blocker = info.checkpoint?.blocker;
  const evidence = blocker?.evidence ?? "";
  const cause = evidence.length > 0 ? `: ${evidence.slice(0, 160)}` : "";
  return `checkpoint: ${info.checkpoint?.phase ?? "unknown"} blocked (${blocker?.kind ?? "browser_failure"}${cause}) — ${progress}`;
}

function receiptId(prefix: string, value: string): string {
  return `${prefix}:${value}`;
}
