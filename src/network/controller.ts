import { SOURCES, type SourceId } from "./config.ts";
import {
  type BrowserBlocker,
  type ControllerCheckpoint,
  type NetworkCandidate,
  NetworkResultError,
  parseSentList,
  parseWalkResult,
  type SendPreparationReceipt,
  type SentListEvidence,
  type WalkListResult,
} from "./results.ts";
import type {
  AuditBaselineInput,
  AuditInput,
  DailyRun,
  ReconciliationScope,
  RunProjection,
} from "./types.ts";

export const NETWORK_SOURCES = SOURCES.map((source) => ({
  ...source,
  sourceContractVersion: 1 as const,
  url: `https://www.linkedin.com/sales/search/people?savedSearchId=${source.savedSearchId}`,
}));

export type BrowserPortResult<T> =
  | { readonly status: "succeeded"; readonly invocationId: string; readonly value: T }
  | {
      readonly status: "failed" | "critical_uncertainty";
      readonly invocationId: string;
      readonly blocker: BrowserBlocker;
    };

export interface NetworkBrowserPort {
  walkList(
    source: (typeof NETWORK_SOURCES)[number],
    budget: number,
    pacingMs: number,
  ): Promise<BrowserPortResult<unknown>>;
  captureSentList(): Promise<BrowserPortResult<unknown>>;
}

export type DurableControllerAttempt = {
  readonly attemptId: string;
  readonly runId: string;
  readonly sourceId: SourceId;
  readonly state: "planned" | "possible";
  readonly candidate: NetworkCandidate;
  readonly prepareReceipt: SendPreparationReceipt | null;
  readonly commitStarted: boolean;
};

export type DurableControllerState = {
  readonly baseline: { readonly id: string } | null;
  readonly pendingAudit: {
    readonly id: string;
    readonly baselineId: string;
  } | null;
  readonly openAttempts: readonly DurableControllerAttempt[];
};

export interface NetworkControllerEngine {
  openDailyRun(localDate: string, now: string, id?: string): DailyRun;
  projection(runId: string): RunProjection;
  reportBytes(runId: string): Uint8Array;
  recordWalkSends(
    runId: string,
    sourceId: SourceId,
    rows: {
      readonly sent: readonly { readonly rowIdentity: string; readonly name: string }[];
      readonly skipped: readonly {
        readonly rowIdentity: string;
        readonly name: string;
        readonly reason: "already_pending" | "email_required" | "unreachable";
      }[];
    },
    now: string,
  ): { readonly sent: number; readonly skipped: number };
  resolveUnconfirmedAfterAudit(runId: string, auditId: string, now: string): number;
  markPossible?(
    attemptId: string,
    attemptedAt: string,
    evidence: unknown,
    receiptId?: string,
  ): void;
  markProvenNoSend?(
    attemptId: string,
    resolvedAt: string,
    evidence: unknown,
    receiptId?: string,
  ): void;
  recordBaseline(input: AuditBaselineInput): void;
  recordAudit(input: AuditInput): void;
  reconcile(
    runId: string,
    baselineId: string,
    auditId: string,
    now: string,
    reconciliationId: string | undefined,
    scope: ReconciliationScope,
  ): string[];
  finish(runId: string, now: string): DailyRun;
  readControllerState?: ((runId: string) => DurableControllerState) | undefined;
  recordCommitStarted?:
    | ((input: {
        readonly attemptId: string;
        readonly runId: string;
        readonly receipt: SendPreparationReceipt;
        readonly startedAt: string;
      }) => void)
    | undefined;
  refreshPreparation?:
    | ((attemptId: string, attemptedAt: string, evidence: unknown, receiptId: string) => void)
    | undefined;
}

export type ControllerProgressOutcome =
  | {
      readonly kind: "walked";
      readonly sourceId: SourceId;
      readonly sent: number;
      readonly skipped: number;
      readonly complete: boolean;
      readonly pagesWalked: number;
    }
  | { readonly kind: "baseline_captured"; readonly baselineId: string }
  | { readonly kind: "baseline_reused"; readonly baselineId: string }
  | {
      readonly kind: "reconciled";
      readonly auditId: string;
      readonly scope: ReconciliationScope;
      readonly resolvedUnconfirmed?: number;
    };

export type ControllerTerminalOutcome = {
  readonly kind: "no_eligible_capacity";
  readonly remainingCapacity: number;
  readonly exhaustedSources: readonly SourceId[];
};

export type TickResult =
  | {
      readonly state: "progress";
      readonly runId: string;
      readonly action: string;
      readonly outcome: ControllerProgressOutcome;
    }
  | {
      readonly state: "checkpoint";
      readonly runId: string;
      readonly checkpoint: ControllerCheckpoint;
    }
  | {
      readonly state: "terminal";
      readonly runId: string;
      readonly terminal: ControllerTerminalOutcome;
    }
  | { readonly state: "done"; readonly runId: string };

export class NetworkController {
  constructor(
    private readonly engine: NetworkControllerEngine,
    private readonly browser: NetworkBrowserPort,
    private readonly now: () => string = () => new Date().toISOString(),
    _id: () => string = () => crypto.randomUUID(),
  ) {}

  open(localDate: string): DailyRun {
    return this.engine.openDailyRun(localDate, this.now());
  }

  status(runId: string): RunProjection {
    return this.engine.projection(runId);
  }

  report(runId: string): Uint8Array {
    return this.engine.reportBytes(runId);
  }

  async walkList(
    runId: string,
    sourceId: SourceId,
    budget: number,
    pacingMs: number,
  ): Promise<TickResult> {
    const durable = this.readState(runId);
    if ("state" in durable) return durable;
    if (durable.value.baseline === null) {
      return { state: "checkpoint", runId, checkpoint: { kind: "baseline_required" } };
    }
    if (!Number.isSafeInteger(budget) || budget < 1) {
      return this.invalidState(runId, "walk budget must be a positive integer");
    }
    const source = this.source(sourceId);
    const walk = await this.browserCall(runId, "walk_list", () =>
      this.browser.walkList(source, budget, pacingMs),
    );
    if ("state" in walk) return walk;
    let parsed: WalkListResult;
    try {
      parsed = parseWalkResult(walk.value.value, source);
    } catch (error) {
      return this.contractFailure(runId, "walk_list", error);
    }
    const recorded = this.engine.recordWalkSends(
      runId,
      sourceId,
      { sent: parsed.sent, skipped: parsed.skipped },
      this.now(),
    );
    return {
      state: "progress",
      runId,
      action: `walked:${sourceId}:${recorded.sent}`,
      outcome: {
        kind: "walked",
        sourceId,
        sent: recorded.sent,
        skipped: recorded.skipped,
        complete: parsed.complete,
        pagesWalked: parsed.pagesWalked,
      },
    };
  }

  async captureBaseline(runId: string, baselineId: string): Promise<TickResult> {
    const durable = this.readState(runId);
    if ("state" in durable) return durable;
    if (durable.value.baseline !== null) {
      return {
        state: "progress",
        runId,
        action: `baseline_reused:${durable.value.baseline.id}`,
        outcome: { kind: "baseline_reused", baselineId: durable.value.baseline.id },
      };
    }
    if (durable.value.openAttempts.length > 0) {
      return this.invalidState(runId, "baseline is missing after an attempt was reserved");
    }
    const evidence = await this.captureSentEvidence(runId);
    if ("state" in evidence) return evidence;
    this.engine.recordBaseline({
      id: baselineId,
      invocationId: evidence.value.invocationId,
      runId,
      peopleCount: evidence.value.evidence.peopleCount,
      competingSenderAbsent: evidence.value.evidence.competingSenderAbsent,
      capturedAt: this.now(),
    });
    return {
      state: "progress",
      runId,
      action: "baseline_captured",
      outcome: { kind: "baseline_captured", baselineId },
    };
  }

  async reconcile(runId: string, auditId: string, scope: ReconciliationScope): Promise<TickResult> {
    const durable = this.readState(runId);
    if ("state" in durable) return durable;
    if (durable.value.baseline === null) {
      return { state: "checkpoint", runId, checkpoint: { kind: "baseline_required" } };
    }
    const baselineId = durable.value.baseline.id;
    const pendingAudit = durable.value.pendingAudit;
    if (pendingAudit !== null && pendingAudit.baselineId !== baselineId) {
      return this.invalidState(runId, "pending audit does not match the durable baseline");
    }
    const reusePendingAudit = pendingAudit !== null && pendingAudit.id === auditId;
    let durableAuditId = reusePendingAudit ? pendingAudit.id : undefined;
    if (!reusePendingAudit) {
      const evidence = await this.captureSentEvidence(runId);
      if ("state" in evidence) return evidence;
      const value = evidence.value.evidence;
      this.engine.recordAudit({
        id: auditId,
        invocationId: evidence.value.invocationId,
        runId,
        baselineId,
        peopleCount: value.peopleCount,
        identities: [...value.identities],
        names: [...value.names],
        complete: value.complete,
        competingSenderAbsent: value.competingSenderAbsent,
        contradictoryEvidence: value.contradictoryEvidence,
        capturedAt: this.now(),
      });
      durableAuditId = auditId;
    }
    if (durableAuditId === undefined) {
      return this.invalidState(runId, "reconciliation has no durable audit");
    }
    this.engine.reconcile(runId, baselineId, durableAuditId, this.now(), undefined, scope);
    return {
      state: "progress",
      runId,
      action: "reconciled",
      outcome: { kind: "reconciled", auditId: durableAuditId, scope },
    };
  }

  resolveUnconfirmedAfterAudit(runId: string, auditId: string): number {
    return this.engine.resolveUnconfirmedAfterAudit(runId, auditId, this.now());
  }

  private async captureSentEvidence(
    runId: string,
  ): Promise<
    | { readonly value: { readonly invocationId: string; readonly evidence: SentListEvidence } }
    | TickResult
  > {
    const capture = await this.browserCall(runId, "capture_sent_list", () =>
      this.browser.captureSentList(),
    );
    if ("state" in capture) return capture;
    try {
      return {
        value: {
          invocationId: capture.value.invocationId,
          evidence: parseSentList(capture.value.value),
        },
      };
    } catch (error) {
      return this.contractFailure(runId, "capture_sent_list", error);
    }
  }

  private async browserCall<T>(
    runId: string,
    phase: string,
    operation: () => Promise<BrowserPortResult<T>>,
  ): Promise<
    { readonly value: { readonly invocationId: string; readonly value: T } } | TickResult
  > {
    let result: BrowserPortResult<T>;
    try {
      result = await operation();
    } catch (error) {
      return {
        state: "checkpoint",
        runId,
        checkpoint: {
          kind: "browser_blocker",
          phase,
          blocker: {
            kind: "browser_exception",
            evidence: error instanceof Error ? error.message : String(error),
            retryability: "safe_retry",
          },
        },
      };
    }
    if (result.status !== "succeeded") {
      return {
        state: "checkpoint",
        runId,
        checkpoint: { kind: "browser_blocker", phase, blocker: result.blocker },
      };
    }
    return { value: { invocationId: result.invocationId, value: result.value } };
  }

  private readState(runId: string): { readonly value: DurableControllerState } | TickResult {
    if (this.engine.readControllerState === undefined) {
      return this.integrationRequired(runId, "read_controller_state");
    }
    return this.validatedState(runId, this.engine.readControllerState(runId));
  }

  private validatedState(
    runId: string,
    state: DurableControllerState,
  ): { readonly value: DurableControllerState } | TickResult {
    const projection = this.engine.projection(runId);
    const planned = state.openAttempts.filter((attempt) => attempt.state === "planned").length;
    const possible = state.openAttempts.filter((attempt) => attempt.state === "possible").length;
    if (planned !== projection.planned || possible !== projection.provisional) {
      return this.invalidState(runId, "controller state does not match engine projection");
    }
    if (state.openAttempts.some((attempt) => attempt.runId !== runId)) {
      return this.invalidState(runId, "open attempt belongs to another run");
    }
    for (const attempt of state.openAttempts) {
      const source = this.source(attempt.sourceId);
      const candidate = attempt.candidate;
      if (
        candidate.sourceName !== source.name ||
        candidate.savedSearchId !== source.savedSearchId ||
        candidate.searchUrl !== source.url ||
        candidate.salesLeadUrl !== `https://www.linkedin.com/sales/lead/${candidate.salesLeadId}`
      ) {
        return this.invalidState(runId, "open attempt candidate violates its source contract");
      }
    }
    return { value: state };
  }

  private integrationRequired(
    runId: string,
    capability: "read_controller_state" | "record_commit_started",
  ): TickResult {
    return {
      state: "checkpoint",
      runId,
      checkpoint: { kind: "engine_integration_required", capability },
    };
  }

  private invalidState(runId: string, evidence: string): TickResult {
    return { state: "checkpoint", runId, checkpoint: { kind: "engine_state_invalid", evidence } };
  }

  private contractFailure(runId: string, phase: string, error: unknown): TickResult {
    return {
      state: "checkpoint",
      runId,
      checkpoint: {
        kind: "source_contract",
        phase,
        evidence:
          error instanceof NetworkResultError || error instanceof Error
            ? error.message
            : String(error),
      },
    };
  }

  private source(sourceId: SourceId): (typeof NETWORK_SOURCES)[number] {
    const source = NETWORK_SOURCES.find((item) => item.id === sourceId);
    if (source === undefined) throw new TypeError("unknown source");
    return source;
  }
}
