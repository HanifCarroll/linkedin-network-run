import {
  hasExactKeys,
  INVOCATION_ID_RE,
  PAGE_CURSOR_RE,
  SEND_PREPARATION_RECEIPT_ID_ACCEPT_RE,
  SHA256_HEX_RE,
  SOURCE_ROW_ID_RE,
  sha256Json,
  terminalFingerprint,
} from "../core/evidence-contract.ts";
import { isExpectedPostSendUrl } from "../core/linkedin-url.ts";
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

export type StrictSourceRow = NetworkCandidate & { readonly rowOrder: number };

/**
 * Wire capture payload. Structurally identical to browser capture result data
 * so adapters can pass capture data through without field-by-field mapping.
 */
export type SourceCapturePayload = {
  readonly schemaVersion: 1;
  readonly kind: "network_source_capture";
  readonly captureInvocationId: string;
  readonly capturedAt: string;
  readonly sourceContract: {
    readonly schemaVersion: 1;
    readonly kind: "network_source_contract";
    readonly contractVersion: 1;
    readonly sourceId: SourceId;
    readonly sourceName: string;
    readonly savedSearchId: string;
    readonly searchUrl: string;
    readonly contractFingerprint: string;
  };
  readonly url: string;
  readonly items: readonly {
    readonly rowIdentity: string;
    readonly salesLeadUrl: string;
    readonly name: string;
  }[];
  readonly reload: {
    readonly navigationInvocationId: string;
    readonly reloadIdentity: string;
    readonly reloadGeneration: number;
    readonly navigatedAt: string;
  } | null;
  readonly page: {
    readonly stateKey: "networkCandidateResultsPage";
    readonly url: string;
    readonly resultsContainerCount: number;
    readonly resultsContainerVisible: boolean;
    readonly ariaBusy: "true" | "false" | null;
    readonly progressbarCount: number;
    readonly alertCount: number;
    readonly dialogCount: number;
    readonly fullyLoaded: boolean;
    readonly blockerFree: boolean;
    readonly cursorIdentity: string | null;
    readonly pageIdentity: string | null;
  };
  readonly pagination: {
    readonly navigationCount: number;
    readonly currentPageCount: number;
    readonly nextControlCount: number;
    readonly nextDisabled: boolean | null;
  };
  readonly terminalEvidence?: {
    readonly schemaVersion: 1;
    readonly kind: "network_source_terminal_observation";
    readonly captureInvocationId: string;
    readonly observedAt: string;
    readonly sourceId: SourceId;
    readonly sourceName: string;
    readonly savedSearchId: string;
    readonly searchUrl: string;
    readonly sourceContractVersion: 1;
    readonly sourceContractFingerprint: string;
    readonly terminalFingerprint: string;
    readonly pageIdentity: string;
    readonly cursorIdentity: string;
    readonly stableRowIds: readonly string[];
    readonly rowCount: number;
    readonly nextControl: "disabled";
    readonly navigationInvocationId: string;
    readonly reloadIdentity: string;
    readonly reloadGeneration: number;
    readonly navigatedAt: string;
  };
};

export { terminalFingerprint as networkTerminalFingerprint };

export type AuthoritativeTerminalObservation = {
  readonly sourceId: SourceId;
  readonly sourceContractVersion: number;
  readonly stableTerminalFingerprint: string;
  readonly pageIdentity: string;
  readonly stableRowIds: readonly string[];
  readonly nextControl: "missing" | "disabled";
  readonly pageCursor: string;
  readonly reloadGeneration: number;
  readonly observedAt: string;
};

export type SourceCaptureEvidence = {
  readonly captureInvocationId: string;
  readonly capturedAt: string;
  readonly sourceId: SourceId;
  readonly sourceContractVersion: number;
  readonly sourceUrl: string;
  readonly rows: readonly StrictSourceRow[];
  readonly terminal?: AuthoritativeTerminalObservation;
};

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

export type CommitSendEvidence = {
  readonly schemaVersion: 1;
  readonly kind: "network_send_commit";
  readonly receiptId: string;
  readonly attemptId: string;
  readonly candidate: NetworkCandidate;
  readonly clickDispatched: true;
  readonly postClickEvidence: {
    readonly observedUrl: string;
    readonly modalCount: number;
    readonly sendControlCount: number;
    readonly pendingCount: number;
    readonly capturedAt: string;
  };
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

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return [...left].sort().join("\0") === [...right].sort().join("\0");
}

const INVOCATION_ID = INVOCATION_ID_RE;
const SOURCE_ROW_ID = SOURCE_ROW_ID_RE;
const PAGE_CURSOR = PAGE_CURSOR_RE;
const SHA256 = SHA256_HEX_RE;

function isIsoUtc(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    return new Date(value).toISOString() === value;
  } catch {
    return false;
  }
}

function isCount(value: unknown, maximum?: number): value is number {
  return (
    Number.isSafeInteger(value) &&
    (value as number) >= 0 &&
    (maximum === undefined || (value as number) <= maximum)
  );
}

export function networkSourceContractFingerprint(source: {
  readonly id: SourceId;
  readonly name: string;
  readonly savedSearchId: string;
  readonly url: string;
  readonly sourceContractVersion: number;
}): string {
  if (source.sourceContractVersion !== 1) {
    throw new NetworkResultError("source_mismatch", "unsupported source contract version");
  }
  return sha256Json({
    schemaVersion: 1,
    kind: "network_source_contract",
    contractVersion: 1,
    sourceId: source.id,
    sourceName: source.name,
    savedSearchId: source.savedSearchId,
    searchUrl: source.url,
  });
}

export function parseSourceRows(
  value: unknown,
  source: {
    readonly id: SourceId;
    readonly name: string;
    readonly savedSearchId: string;
    readonly url: string;
  },
): StrictSourceRow[] {
  const root = object(value, "invalid_source_capture");
  exactKeys(root, ["url", "items"], "invalid_source_capture");
  if (root.url !== source.url || !Array.isArray(root.items)) {
    throw new NetworkResultError("source_mismatch", "captured source URL does not match");
  }
  if (root.items.length > 30) {
    throw new NetworkResultError("invalid_source_capture", "source capture exceeds 30 rows");
  }
  return root.items.map((raw, rowOrder) => {
    const row = object(raw, "invalid_source_capture");
    exactKeys(row, ["rowIdentity", "name", "salesLeadUrl"], "invalid_source_capture");
    if (
      typeof row.rowIdentity !== "string" ||
      !SOURCE_ROW_ID.test(row.rowIdentity) ||
      typeof row.name !== "string" ||
      row.name.length === 0 ||
      row.name.trim() !== row.name ||
      typeof row.salesLeadUrl !== "string"
    ) {
      throw new NetworkResultError(
        "invalid_source_capture",
        "source row has a missing required field",
      );
    }
    let salesLeadUrl: URL;
    try {
      salesLeadUrl = new URL(row.salesLeadUrl);
    } catch {
      throw new NetworkResultError(
        "invalid_source_capture",
        "source row has an invalid sales lead URL",
      );
    }
    const match = /^\/sales\/lead\/([A-Za-z0-9_-]+)\/?$/.exec(salesLeadUrl.pathname);
    if (
      salesLeadUrl.origin !== "https://www.linkedin.com" ||
      salesLeadUrl.search ||
      salesLeadUrl.hash ||
      match?.[1] === undefined
    ) {
      throw new NetworkResultError(
        "invalid_source_capture",
        "source row has an invalid sales lead URL",
      );
    }
    return {
      sourceName: source.name,
      savedSearchId: source.savedSearchId,
      searchUrl: source.url,
      salesLeadUrl: salesLeadUrl.href,
      salesLeadId: match[1],
      name: row.name,
      rowIdentity: row.rowIdentity,
      rowOrder,
    };
  });
}

export function parseSourceCapture(
  value: unknown,
  source: {
    readonly id: SourceId;
    readonly name: string;
    readonly savedSearchId: string;
    readonly url: string;
    readonly sourceContractVersion: number;
  },
): SourceCaptureEvidence {
  const root = object(value, "invalid_source_capture");
  const requiredKeys = [
    "schemaVersion",
    "kind",
    "captureInvocationId",
    "capturedAt",
    "sourceContract",
    "url",
    "items",
    "reload",
    "page",
    "pagination",
  ] as const;
  const validKeySets = [
    [...requiredKeys].sort().join("\0"),
    [...requiredKeys, "terminalEvidence"].sort().join("\0"),
  ];
  if (!validKeySets.includes(Object.keys(root).sort().join("\0"))) {
    throw new NetworkResultError(
      "invalid_source_capture",
      "result fields do not match the strict source-capture contract",
    );
  }
  if (
    root.schemaVersion !== 1 ||
    root.kind !== "network_source_capture" ||
    typeof root.captureInvocationId !== "string" ||
    !INVOCATION_ID.test(root.captureInvocationId) ||
    !isIsoUtc(root.capturedAt) ||
    root.url !== source.url ||
    !Array.isArray(root.items)
  ) {
    throw new NetworkResultError(
      "invalid_source_capture",
      "source capture identity or timestamp is invalid",
    );
  }

  const sourceContract = object(root.sourceContract, "invalid_source_capture");
  exactKeys(
    sourceContract,
    [
      "schemaVersion",
      "kind",
      "contractVersion",
      "sourceId",
      "sourceName",
      "savedSearchId",
      "searchUrl",
      "contractFingerprint",
    ],
    "invalid_source_capture",
  );
  const expectedContractFingerprint = networkSourceContractFingerprint(source);
  if (
    sourceContract.schemaVersion !== 1 ||
    sourceContract.kind !== "network_source_contract" ||
    sourceContract.contractVersion !== source.sourceContractVersion ||
    sourceContract.sourceId !== source.id ||
    sourceContract.sourceName !== source.name ||
    sourceContract.savedSearchId !== source.savedSearchId ||
    sourceContract.searchUrl !== source.url ||
    sourceContract.contractFingerprint !== expectedContractFingerprint
  ) {
    throw new NetworkResultError(
      "source_mismatch",
      "source capture does not match the exact configured source contract",
    );
  }

  const rows = parseSourceRows({ url: source.url, items: root.items }, source);
  const rowIdentities = rows.map((row) => row.rowIdentity);
  if (
    new Set(rowIdentities).size !== rowIdentities.length ||
    new Set(rows.map((row) => row.salesLeadUrl)).size !== rows.length
  ) {
    throw new NetworkResultError(
      "invalid_source_capture",
      "source capture rows must have unique identities and lead URLs",
    );
  }

  let reload: {
    readonly navigationInvocationId: string;
    readonly reloadIdentity: string;
    readonly reloadGeneration: number;
    readonly navigatedAt: string;
  } | null = null;
  if (root.reload !== null) {
    const rawReload = object(root.reload, "invalid_source_capture");
    exactKeys(
      rawReload,
      ["navigationInvocationId", "reloadIdentity", "reloadGeneration", "navigatedAt"],
      "invalid_source_capture",
    );
    if (
      typeof rawReload.navigationInvocationId !== "string" ||
      !INVOCATION_ID.test(rawReload.navigationInvocationId) ||
      rawReload.reloadIdentity !== `${rawReload.navigationInvocationId}:reload` ||
      !Number.isSafeInteger(rawReload.reloadGeneration) ||
      (rawReload.reloadGeneration as number) < 1 ||
      !isIsoUtc(rawReload.navigatedAt) ||
      Date.parse(rawReload.navigatedAt) > Date.parse(root.capturedAt)
    ) {
      throw new NetworkResultError("invalid_source_capture", "source reload evidence is invalid");
    }
    reload = {
      navigationInvocationId: rawReload.navigationInvocationId,
      reloadIdentity: rawReload.reloadIdentity as string,
      reloadGeneration: rawReload.reloadGeneration as number,
      navigatedAt: rawReload.navigatedAt,
    };
  }

  const page = object(root.page, "invalid_source_capture");
  exactKeys(
    page,
    [
      "stateKey",
      "url",
      "resultsContainerCount",
      "resultsContainerVisible",
      "ariaBusy",
      "progressbarCount",
      "alertCount",
      "dialogCount",
      "fullyLoaded",
      "blockerFree",
      "cursorIdentity",
      "pageIdentity",
    ],
    "invalid_source_capture",
  );
  const expectedFullyLoaded =
    page.resultsContainerCount === 1 &&
    page.resultsContainerVisible === true &&
    page.ariaBusy !== "true" &&
    page.progressbarCount === 0;
  const expectedBlockerFree = page.alertCount === 0 && page.dialogCount === 0;
  if (
    page.stateKey !== "networkCandidateResultsPage" ||
    page.url !== source.url ||
    !isCount(page.resultsContainerCount, 1) ||
    typeof page.resultsContainerVisible !== "boolean" ||
    (page.ariaBusy !== null && page.ariaBusy !== "true" && page.ariaBusy !== "false") ||
    !isCount(page.progressbarCount) ||
    !isCount(page.alertCount) ||
    !isCount(page.dialogCount) ||
    page.fullyLoaded !== expectedFullyLoaded ||
    page.blockerFree !== expectedBlockerFree
  ) {
    throw new NetworkResultError("invalid_source_capture", "source page evidence is invalid");
  }
  if (
    (page.cursorIdentity === null && page.pageIdentity !== null) ||
    (page.cursorIdentity !== null &&
      (typeof page.cursorIdentity !== "string" ||
        !PAGE_CURSOR.test(page.cursorIdentity) ||
        page.pageIdentity !==
          `salesnav-saved-search:${source.savedSearchId}:${page.cursorIdentity}`))
  ) {
    throw new NetworkResultError("invalid_source_capture", "source page identity is invalid");
  }

  const pagination = object(root.pagination, "invalid_source_capture");
  exactKeys(
    pagination,
    ["navigationCount", "currentPageCount", "nextControlCount", "nextDisabled"],
    "invalid_source_capture",
  );
  if (
    !isCount(pagination.navigationCount, 1) ||
    !isCount(pagination.currentPageCount, 1) ||
    !isCount(pagination.nextControlCount, 1) ||
    (pagination.navigationCount === 0 &&
      (pagination.currentPageCount !== 0 ||
        pagination.nextControlCount !== 0 ||
        pagination.nextDisabled !== null)) ||
    (pagination.navigationCount === 1 &&
      pagination.nextDisabled !== null &&
      typeof pagination.nextDisabled !== "boolean") ||
    (pagination.nextControlCount === 0 && pagination.nextDisabled !== null) ||
    (pagination.nextControlCount === 1 && typeof pagination.nextDisabled !== "boolean") ||
    (pagination.currentPageCount === 0 && page.cursorIdentity !== null) ||
    (pagination.currentPageCount === 1 && page.cursorIdentity === null)
  ) {
    throw new NetworkResultError("invalid_source_capture", "source pagination evidence is invalid");
  }

  const terminalEligible =
    page.fullyLoaded === true &&
    page.blockerFree === true &&
    rows.length > 0 &&
    reload !== null &&
    pagination.navigationCount === 1 &&
    pagination.currentPageCount === 1 &&
    pagination.nextControlCount === 1 &&
    pagination.nextDisabled === true &&
    page.pageIdentity !== null &&
    page.cursorIdentity !== null;
  if ((root.terminalEvidence !== undefined) !== terminalEligible) {
    throw new NetworkResultError(
      "invalid_terminal_observation",
      terminalEligible
        ? "authoritative terminal evidence is missing"
        : "terminal evidence is not allowed for this page state",
    );
  }

  const baseEvidence = {
    captureInvocationId: root.captureInvocationId,
    capturedAt: root.capturedAt,
    sourceId: source.id,
    sourceContractVersion: source.sourceContractVersion,
    sourceUrl: source.url,
    rows,
  } as const;
  if (root.terminalEvidence === undefined) return baseEvidence;

  const terminal = object(root.terminalEvidence, "invalid_terminal_observation");
  exactKeys(
    terminal,
    [
      "schemaVersion",
      "kind",
      "captureInvocationId",
      "observedAt",
      "sourceId",
      "sourceName",
      "savedSearchId",
      "searchUrl",
      "sourceContractVersion",
      "sourceContractFingerprint",
      "terminalFingerprint",
      "pageIdentity",
      "cursorIdentity",
      "stableRowIds",
      "rowCount",
      "nextControl",
      "navigationInvocationId",
      "reloadIdentity",
      "reloadGeneration",
      "navigatedAt",
    ],
    "invalid_terminal_observation",
  );
  const stableRowIds = terminal.stableRowIds;
  const sortedRowIdentities = [...rowIdentities].sort();
  if (
    terminal.schemaVersion !== 1 ||
    terminal.kind !== "network_source_terminal_observation" ||
    terminal.captureInvocationId !== root.captureInvocationId ||
    terminal.observedAt !== root.capturedAt ||
    !isIsoUtc(terminal.observedAt) ||
    terminal.sourceId !== source.id ||
    terminal.sourceName !== source.name ||
    terminal.savedSearchId !== source.savedSearchId ||
    terminal.searchUrl !== source.url ||
    terminal.sourceContractVersion !== source.sourceContractVersion ||
    terminal.sourceContractFingerprint !== expectedContractFingerprint ||
    typeof terminal.terminalFingerprint !== "string" ||
    !SHA256.test(terminal.terminalFingerprint) ||
    terminal.pageIdentity !== page.pageIdentity ||
    terminal.cursorIdentity !== page.cursorIdentity ||
    !Array.isArray(stableRowIds) ||
    stableRowIds.length === 0 ||
    stableRowIds.some((rowId) => typeof rowId !== "string" || !SOURCE_ROW_ID.test(rowId)) ||
    new Set(stableRowIds).size !== stableRowIds.length ||
    !sameStrings(stableRowIds as string[], sortedRowIdentities) ||
    JSON.stringify(stableRowIds) !== JSON.stringify(sortedRowIdentities) ||
    terminal.rowCount !== rows.length ||
    terminal.nextControl !== "disabled" ||
    reload === null ||
    terminal.navigationInvocationId !== reload.navigationInvocationId ||
    terminal.reloadIdentity !== reload.reloadIdentity ||
    terminal.reloadGeneration !== reload.reloadGeneration ||
    terminal.navigatedAt !== reload.navigatedAt
  ) {
    throw new NetworkResultError(
      "invalid_terminal_observation",
      "terminal observation is incomplete or does not bind the captured source page",
    );
  }
  const expectedTerminalFingerprint = terminalFingerprint({
    sourceContractFingerprint: expectedContractFingerprint,
    searchUrl: source.url,
    pageIdentity: terminal.pageIdentity as string,
    cursorIdentity: terminal.cursorIdentity as string,
    stableRowIds: stableRowIds as string[],
    rowCount: rows.length,
    nextControl: "disabled",
  });
  if (terminal.terminalFingerprint !== expectedTerminalFingerprint) {
    throw new NetworkResultError(
      "invalid_terminal_observation",
      "terminal fingerprint does not match its exact source evidence",
    );
  }
  return {
    ...baseEvidence,
    terminal: {
      sourceId: source.id,
      sourceContractVersion: source.sourceContractVersion,
      stableTerminalFingerprint: terminal.terminalFingerprint,
      pageIdentity: terminal.pageIdentity as string,
      stableRowIds: stableRowIds as string[],
      nextControl: "disabled",
      pageCursor: terminal.cursorIdentity as string,
      reloadGeneration: terminal.reloadGeneration as number,
      observedAt: terminal.observedAt as string,
    },
  };
}

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

export function parseCommitSendEvidence(
  value: unknown,
  receipt: SendPreparationReceipt,
): CommitSendEvidence {
  const root = object(value, "invalid_commit_evidence");
  exactKeys(
    root,
    [
      "schemaVersion",
      "kind",
      "receiptId",
      "attemptId",
      "candidate",
      "clickDispatched",
      "postClickEvidence",
    ],
    "invalid_commit_evidence",
  );
  const candidate = exactCandidate(root.candidate, "invalid_commit_evidence");
  const post = object(root.postClickEvidence, "invalid_commit_evidence");
  exactKeys(
    post,
    ["observedUrl", "modalCount", "sendControlCount", "pendingCount", "capturedAt"],
    "invalid_commit_evidence",
  );
  if (
    root.schemaVersion !== 1 ||
    root.kind !== "network_send_commit" ||
    root.receiptId !== receipt.receiptId ||
    root.attemptId !== receipt.attemptId ||
    root.clickDispatched !== true ||
    !sameCandidate(candidate, receipt.candidate) ||
    typeof post.observedUrl !== "string" ||
    !isExpectedPostSendUrl(post.observedUrl, receipt.candidate) ||
    ![post.modalCount, post.sendControlCount, post.pendingCount].every(
      (count) => Number.isSafeInteger(count) && (count as number) >= 0,
    ) ||
    typeof post.capturedAt !== "string" ||
    !Number.isFinite(Date.parse(post.capturedAt))
  ) {
    throw new NetworkResultError(
      "invalid_commit_evidence",
      "commit evidence does not match the exact preparation receipt",
    );
  }
  return {
    schemaVersion: 1,
    kind: "network_send_commit",
    receiptId: receipt.receiptId,
    attemptId: receipt.attemptId,
    candidate,
    clickDispatched: true,
    postClickEvidence: {
      observedUrl: post.observedUrl as string,
      modalCount: post.modalCount as number,
      sendControlCount: post.sendControlCount as number,
      pendingCount: post.pendingCount as number,
      capturedAt: post.capturedAt,
    },
  };
}

export type WalkSkipReason = "already_pending" | "email_required" | "unreachable";

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
