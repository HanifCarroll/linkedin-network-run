import type { SourceId } from "./config.ts";

export type IdentityInput = {
  salesNavId?: string;
  publicUrl?: string;
  leadKey?: string;
  name: string;
};

export type DailyRun = {
  id: string;
  localDate: string;
  status: "active" | "done" | "blocked";
  target: 30;
  preferredPerSource: 15;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
};

export type RunProjection = {
  run: DailyRun;
  durable: number;
  planned: number;
  provisional: number;
  remainingCapacity: number;
  bySource: Record<
    SourceId,
    { durable: number; planned: number; possible: number; available: number }
  >;
  finalReconciliation: boolean;
};

export type ExhaustionObservation = {
  id: string;
  invocationId: string;
  runId: string;
  sourceId: SourceId;
  sourceContractVersion: number;
  pageIdentity: string;
  stableRowIds: string[];
  nextControl: "missing" | "disabled";
  reloadGeneration: number;
  tickId: string;
  observedAt: string;
};

export type AuditBaselineInput = {
  id: string;
  invocationId: string;
  runId: string;
  peopleCount: number;
  identities?: readonly string[];
  competingSenderAbsent: boolean;
  capturedAt: string;
};

export type AuditInput = {
  id: string;
  invocationId: string;
  runId: string;
  baselineId: string;
  peopleCount: number;
  identities: string[];
  names: string[];
  complete: boolean;
  competingSenderAbsent: boolean;
  contradictoryEvidence?: boolean;
  capturedAt: string;
};

export type ReconciliationScope = "microbatch" | "final";
