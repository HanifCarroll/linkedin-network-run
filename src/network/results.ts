import {
  hasExactKeys,
  SEND_PREPARATION_RECEIPT_ID_ACCEPT_RE,
  SOURCE_ROW_ID_RE,
  terminalFingerprint,
} from "../core/evidence-contract.ts";
import type { SourceId } from "./config.ts";

export type NetworkCandidate = {
  readonly sourceName: string;
  readonly savedSearchId: string;
  readonly searchUrl: string;
  readonly salesLeadUrl: string;
  readonly salesLeadId: string;
  readonly name: string;
  readonly rowIdentity: string;
};

export { terminalFingerprint as networkTerminalFingerprint };

export type SentListEvidence = {
  readonly peopleCount: number;
  readonly identities: readonly string[];
  readonly names: readonly string[];
  readonly complete: boolean;
  readonly competingSenderAbsent: boolean;
  readonly contradictoryEvidence: boolean;
};

export type BrowserBlocker = {
  readonly kind: string;
  readonly evidence: string;
  readonly retryability: "safe_retry" | "terminal" | "possible_send";
};

export type SendPreparationReceipt = {
  readonly schemaVersion: 1;
  readonly kind: "network_send_prepared";
  readonly receiptId: string;
  readonly attemptId: string;
  readonly preparedAt: string;
  readonly candidate: NetworkCandidate;
};

export type EngineIntegrationCapability = "read_controller_state" | "record_commit_started";

export type ControllerCheckpoint =
  | { readonly kind: "possible_send"; readonly attemptId: string }
  | { readonly kind: "send_not_allowed" }
  | { readonly kind: "baseline_required" }
  | {
      readonly kind: "engine_integration_required";
      readonly capability: EngineIntegrationCapability;
    }
  | { readonly kind: "engine_state_invalid"; readonly evidence: string }
  | { readonly kind: "browser_blocker"; readonly phase: string; readonly blocker: BrowserBlocker }
  | { readonly kind: "source_contract"; readonly phase: string; readonly evidence: string };

export class NetworkResultError extends Error {
  constructor(
    readonly code:
      | "invalid_source_capture"
      | "invalid_terminal_observation"
      | "source_mismatch"
      | "invalid_sent_list"
      | "invalid_prepare_receipt"
      | "invalid_commit_evidence"
      | "invalid_walk_result",
    message: string,
  ) {
    super(message);
    this.name = "NetworkResultError";
  }
}

function object(value: unknown, code: NetworkResultError["code"]): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new NetworkResultError(code, "result must be an object");
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
  code: NetworkResultError["code"],
): void {
  if (!hasExactKeys(value, keys)) {
    throw new NetworkResultError(code, "result fields do not match the strict contract");
  }
}

function exactCandidate(value: unknown, code: NetworkResultError["code"]): NetworkCandidate {
  const candidate = object(value, code);
  const keys = [
    "sourceName",
    "savedSearchId",
    "searchUrl",
    "salesLeadUrl",
    "salesLeadId",
    "name",
    "rowIdentity",
  ] as const;
  exactKeys(candidate, keys, code);
  if (!keys.every((key) => typeof candidate[key] === "string" && candidate[key].trim() !== "")) {
    throw new NetworkResultError(code, "candidate has a missing required field");
  }
  return Object.fromEntries(keys.map((key) => [key, candidate[key]])) as NetworkCandidate;
}

function sameCandidate(left: NetworkCandidate, right: NetworkCandidate): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

const SOURCE_ROW_ID = SOURCE_ROW_ID_RE;

export function parseSentList(value: unknown): SentListEvidence {
  const root = object(value, "invalid_sent_list");
  exactKeys(
    root,
    [
      "peopleCount",
      "identities",
      "names",
      "complete",
      "competingSenderAbsent",
      "contradictoryEvidence",
    ],
    "invalid_sent_list",
  );
  const MAX_SENT_LIST_ENTRIES = 2500;
  const strings = (items: unknown): items is string[] =>
    Array.isArray(items) &&
    items.length <= MAX_SENT_LIST_ENTRIES &&
    items.every(
      (item) => typeof item === "string" && item.trim().length > 0 && item.trim() === item,
    );
  if (
    !Number.isSafeInteger(root.peopleCount) ||
    (root.peopleCount as number) < 0 ||
    !strings(root.identities) ||
    !strings(root.names) ||
    ![root.complete, root.competingSenderAbsent, root.contradictoryEvidence].every(
      (item) => typeof item === "boolean",
    )
  ) {
    throw new NetworkResultError(
      "invalid_sent_list",
      "sent-list evidence is incomplete or malformed",
    );
  }
  if (new Set(root.identities as string[]).size !== (root.identities as string[]).length) {
    throw new NetworkResultError("invalid_sent_list", "sent-list identities must be unique");
  }
  return {
    peopleCount: root.peopleCount as number,
    identities: root.identities as string[],
    names: root.names as string[],
    complete: root.complete as boolean,
    competingSenderAbsent: root.competingSenderAbsent as boolean,
    contradictoryEvidence: root.contradictoryEvidence as boolean,
  };
}

export function parsePrepareSendReceipt(
  value: unknown,
  expected: { readonly attemptId: string; readonly candidate: NetworkCandidate },
): SendPreparationReceipt {
  const root = object(value, "invalid_prepare_receipt");
  exactKeys(
    root,
    ["schemaVersion", "kind", "receiptId", "attemptId", "preparedAt", "candidate"],
    "invalid_prepare_receipt",
  );
  const candidate = exactCandidate(root.candidate, "invalid_prepare_receipt");
  if (
    root.schemaVersion !== 1 ||
    root.kind !== "network_send_prepared" ||
    typeof root.receiptId !== "string" ||
    !SEND_PREPARATION_RECEIPT_ID_ACCEPT_RE.test(root.receiptId) ||
    root.attemptId !== expected.attemptId ||
    typeof root.preparedAt !== "string" ||
    !Number.isFinite(Date.parse(root.preparedAt)) ||
    !sameCandidate(candidate, expected.candidate)
  ) {
    throw new NetworkResultError(
      "invalid_prepare_receipt",
      "prepare receipt does not match the exact planned attempt",
    );
  }
  return {
    schemaVersion: 1,
    kind: "network_send_prepared",
    receiptId: root.receiptId,
    attemptId: expected.attemptId,
    preparedAt: root.preparedAt,
    candidate,
  };
}

export type WalkSkipReason =
  | "already_pending"
  | "email_required"
  | "unreachable"
  | "api_error"
  | `api_${number}`;

export type WalkSentRow = {
  readonly rowIdentity: string;
  readonly name: string;
};

export type WalkSkippedRow = {
  readonly rowIdentity: string;
  readonly name: string;
  readonly reason: WalkSkipReason;
};

export type WalkListResult = {
  readonly sourceId: SourceId;
  readonly sent: readonly WalkSentRow[];
  readonly skipped: readonly WalkSkippedRow[];
  readonly pagesWalked: number;
  readonly complete: boolean;
};

const WALK_SKIP_REASONS = new Set<WalkSkipReason>([
  "already_pending",
  "email_required",
  "unreachable",
  "api_error",
]);

export function parseWalkResult(value: unknown, source: { readonly id: SourceId }): WalkListResult {
  const root = object(value, "invalid_walk_result");
  exactKeys(
    root,
    ["sourceId", "sent", "skipped", "pagesWalked", "complete"],
    "invalid_walk_result",
  );
  if (root.sourceId !== source.id) {
    throw new NetworkResultError("invalid_walk_result", "walk sourceId does not match source");
  }
  if (!Array.isArray(root.sent) || root.sent.length > 30) {
    throw new NetworkResultError("invalid_walk_result", "sent must be an array of at most 30");
  }
  if (!Array.isArray(root.skipped) || root.skipped.length > 500) {
    throw new NetworkResultError("invalid_walk_result", "skipped must be a bounded array");
  }
  if (
    !Number.isSafeInteger(root.pagesWalked) ||
    (root.pagesWalked as number) < 0 ||
    (root.pagesWalked as number) > 10
  ) {
    throw new NetworkResultError("invalid_walk_result", "pagesWalked out of range");
  }
  if (typeof root.complete !== "boolean") {
    throw new NetworkResultError("invalid_walk_result", "complete must be boolean");
  }
  const seen = new Set<string>();
  const sent: WalkSentRow[] = root.sent.map((raw) => {
    const row = object(raw, "invalid_walk_result");
    exactKeys(row, ["rowIdentity", "name"], "invalid_walk_result");
    if (typeof row.rowIdentity !== "string" || !SOURCE_ROW_ID.test(row.rowIdentity)) {
      throw new NetworkResultError("invalid_walk_result", "sent rowIdentity invalid");
    }
    if (typeof row.name !== "string" || row.name.trim() === "") {
      throw new NetworkResultError("invalid_walk_result", "sent name required");
    }
    if (seen.has(row.rowIdentity)) {
      throw new NetworkResultError("invalid_walk_result", "duplicate rowIdentity in walk result");
    }
    seen.add(row.rowIdentity);
    return { rowIdentity: row.rowIdentity, name: row.name.trim() };
  });
  const skipped: WalkSkippedRow[] = root.skipped.map((raw) => {
    const row = object(raw, "invalid_walk_result");
    exactKeys(row, ["rowIdentity", "name", "reason"], "invalid_walk_result");
    if (typeof row.name !== "string" || row.name.trim() === "") {
      throw new NetworkResultError("invalid_walk_result", "skipped name required");
    }
    if (typeof row.reason !== "string" || !WALK_SKIP_REASONS.has(row.reason as WalkSkipReason)) {
      throw new NetworkResultError("invalid_walk_result", "skipped reason invalid");
    }
    if (typeof row.rowIdentity !== "string") {
      throw new NetworkResultError("invalid_walk_result", "skipped rowIdentity required");
    }
    if (row.rowIdentity !== "" && !SOURCE_ROW_ID.test(row.rowIdentity)) {
      throw new NetworkResultError("invalid_walk_result", "skipped rowIdentity invalid");
    }
    if (row.rowIdentity !== "" && seen.has(row.rowIdentity)) {
      throw new NetworkResultError("invalid_walk_result", "duplicate rowIdentity in walk result");
    }
    if (row.rowIdentity !== "") seen.add(row.rowIdentity);
    return {
      rowIdentity: row.rowIdentity,
      name: row.name.trim(),
      reason: row.reason as WalkSkipReason,
    };
  });
  return {
    sourceId: source.id,
    sent,
    skipped,
    pagesWalked: root.pagesWalked as number,
    complete: root.complete as boolean,
  };
}
