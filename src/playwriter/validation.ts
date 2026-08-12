import {
  INVOCATION_ID_RE,
  SHA256_HEX_RE,
  SOURCE_ROW_ID_RE,
  terminalFingerprint,
} from "../core/evidence-contract.ts";
import { isExpectedPostSendUrl } from "../core/linkedin-url.ts";
import { browserSendPreparation, deepFreeze, parseSendPreparationReceiptId } from "./send.ts";
import { assertNetworkSourceContract } from "./source-capture.ts";
import {
  type BlockerKind,
  type CandidateIdentity,
  type CommitSendResultData,
  type InvocationConfig,
  type InvocationReceipt,
  NETWORK_COMMANDS,
  type NetworkSourceContract,
  PROGRESS_STATES,
  type ProgressEvent,
  type SendPreparationReceipt,
  type SourceCaptureResultData,
  type SourceExhaustionEvidence,
  type SourceReloadEvidence,
  type SourceTerminalEvidence,
  type TypedBlocker,
} from "./types.ts";
import { assertAllowedWorkflowUrl } from "./urls.ts";

const INVOCATION_ID = INVOCATION_ID_RE;
const COMMAND = /^[a-z][a-z0-9-]{1,63}$/;
const LEAD_ID = /^[A-Za-z0-9_-]{2,160}$/;
const ATTEMPT_ID = /^[A-Za-z0-9:_-]{8,200}$/;
function invariant(c: unknown, m: string): asserts c {
  if (!c) throw new TypeError(m);
}
function record(v: unknown, m = "object"): Record<string, unknown> {
  invariant(typeof v === "object" && v !== null && !Array.isArray(v), `${m} must be object`);
  return v as Record<string, unknown>;
}
function exact(
  v: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
) {
  const keys = Object.keys(v),
    allowed = new Set([...required, ...optional]);
  invariant(
    keys.every((k) => allowed.has(k)) && required.every((k) => k in v),
    "object fields are invalid",
  );
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
export function assertTimestamp(v: unknown, f = "timestamp"): asserts v is string {
  invariant(typeof v === "string" && new Date(v).toISOString() === v, `${f} must be ISO 8601 UTC`);
}
export function assertInvocationId(v: unknown): asserts v is string {
  invariant(typeof v === "string" && INVOCATION_ID.test(v), "invalid invocationId");
}
export function assertCommand(v: unknown): asserts v is string {
  invariant(typeof v === "string" && COMMAND.test(v), "invalid command");
}
export function assertNetworkCommand(v: unknown): asserts v is (typeof NETWORK_COMMANDS)[number] {
  invariant(
    typeof v === "string" && NETWORK_COMMANDS.includes(v as never),
    "invalid network command",
  );
}
export function assertCandidateIdentity(v: unknown): asserts v is CandidateIdentity {
  const c = record(v, "candidate");
  exact(c, [
    "sourceName",
    "savedSearchId",
    "searchUrl",
    "salesLeadUrl",
    "salesLeadId",
    "name",
    "rowIdentity",
  ]);
  for (const k of ["sourceName", "savedSearchId", "name", "rowIdentity"]) {
    invariant(
      typeof c[k] === "string" &&
        (c[k] as string).trim() === (c[k] as string) &&
        (c[k] as string).length > 0,
      `invalid candidate ${k}`,
    );
  }
  invariant(
    typeof c.salesLeadId === "string" && LEAD_ID.test(c.salesLeadId),
    "invalid salesLeadId",
  );
  invariant(typeof c.searchUrl === "string", "invalid searchUrl");
  assertAllowedWorkflowUrl("candidateResults", c.searchUrl);
  invariant(
    new URL(c.searchUrl).searchParams.get("savedSearchId") === c.savedSearchId,
    "savedSearchId mismatch",
  );
  invariant(typeof c.salesLeadUrl === "string", "invalid salesLeadUrl");
  assertAllowedWorkflowUrl("salesLead", c.salesLeadUrl);
  invariant(
    new URL(c.salesLeadUrl).pathname.replace(/\/$/, "") === `/sales/lead/${c.salesLeadId}`,
    "salesLeadId mismatch",
  );
}

export function assertSendPreparationReceipt(v: unknown): asserts v is SendPreparationReceipt {
  const receipt = record(v, "send preparation receipt");
  exact(receipt, ["schemaVersion", "kind", "receiptId", "attemptId", "preparedAt", "candidate"]);
  invariant(receipt.schemaVersion === 1, "invalid send preparation schemaVersion");
  invariant(receipt.kind === "network_send_prepared", "invalid send preparation kind");
  invariant(typeof receipt.receiptId === "string", "invalid send preparation receiptId");
  const id = parseSendPreparationReceiptId(receipt.receiptId);
  assertInvocationId(id.prepareInvocationId);
  invariant(
    typeof receipt.attemptId === "string" && ATTEMPT_ID.test(receipt.attemptId),
    "invalid send preparation attemptId",
  );
  assertTimestamp(receipt.preparedAt, "preparedAt");
  assertCandidateIdentity(receipt.candidate);
}

export function assertSendPreparationBinding(
  value: unknown,
  sessionId: number,
): asserts value is SendPreparationReceipt {
  assertSendPreparationReceipt(value);
  invariant(Number.isSafeInteger(sessionId) && sessionId > 0, "invalid sessionId");
  browserSendPreparation(value, sessionId);
}

export function immutableSendPreparationReceipt(value: unknown): SendPreparationReceipt {
  assertSendPreparationReceipt(value);
  return deepFreeze(structuredClone(value)) as SendPreparationReceipt;
}

export function assertCommitSendResultData(
  value: unknown,
  expected: SendPreparationReceipt,
): asserts value is CommitSendResultData {
  const data = record(value, "commit send result");
  exact(data, [
    "schemaVersion",
    "kind",
    "receiptId",
    "attemptId",
    "candidate",
    "clickDispatched",
    "postClickEvidence",
  ]);
  invariant(
    data.schemaVersion === 1 && data.kind === "network_send_commit",
    "invalid commit result",
  );
  invariant(
    data.receiptId === expected.receiptId &&
      data.attemptId === expected.attemptId &&
      same(data.candidate, expected.candidate),
    "commit preparation identity mismatch",
  );
  invariant(data.clickDispatched === true, "commit click was not dispatched");
  const post = record(data.postClickEvidence, "post-click evidence");
  exact(post, ["observedUrl", "modalCount", "sendControlCount", "pendingCount", "capturedAt"]);
  invariant(
    typeof post.observedUrl === "string" &&
      isExpectedPostSendUrl(post.observedUrl, expected.candidate),
    "post-click URL mismatch",
  );
  for (const field of ["modalCount", "sendControlCount", "pendingCount"])
    invariant(
      Number.isSafeInteger(post[field]) && (post[field] as number) >= 0,
      `invalid ${field}`,
    );
  assertTimestamp(post.capturedAt, "post-click capturedAt");
}

const candidateCommands = new Set([
  "capture-candidate",
  "click-connect-menu-item",
  "observe-connect-modal",
  "click-send",
  "prepare-send",
  "commit-send",
  "observe-post-send",
]);
export function commandNeedsCandidate(c: string) {
  return candidateCommands.has(c);
}
export function assertBlocker(v: unknown): asserts v is TypedBlocker {
  const b = record(v, "blocker");
  exact(b, ["kind", "evidence", "retryability"]);
  const kinds: readonly BlockerKind[] = [
    "rate_limit_429",
    "weekly_limit",
    "unusual_activity",
    "login",
    "checkpoint",
    "security_verification",
    "session_lost",
    "network_refusal",
    "source_mismatch",
    "wrong_page",
    "selector_contract",
    "evidence_corrupt",
    "evidence_finalization",
    "preparation_mismatch",
    "preparation_stale",
    "commit_uncertainty",
    "already_pending",
    "email_required",
    "missing_more_actions",
    "missing_connect_menu",
    "missing_send",
    "disabled_send",
    "candidate_absent",
    "no_rows",
    "row_load_timeout",
    "stalled_navigation",
    "source_exhausted",
    "unclear_confirmation",
  ];
  invariant(kinds.includes(b.kind as BlockerKind), "invalid blocker kind");
  invariant(typeof b.evidence === "string" && b.evidence.length > 0, "invalid blocker evidence");
  invariant(
    ["safe_retry", "terminal", "possible_send"].includes(b.retryability as string),
    "invalid blocker retryability",
  );
}
export function assertInvocationConfig(v: unknown): asserts v is InvocationConfig {
  const c = record(v, "config");
  exact(
    c,
    [
      "schemaVersion",
      "invocationId",
      "command",
      "definitionId",
      "action",
      "phaseContract",
      "createdAt",
      "sessionId",
      "input",
    ],
    ["candidate", "sendPreparation", "sourceContract"],
  );
  invariant(c.schemaVersion === 1, "unsupported schemaVersion");
  assertInvocationId(c.invocationId);
  assertCommand(c.command);
  invariant(
    typeof c.definitionId === "string" && c.definitionId.length > 3,
    "invalid definitionId",
  );
  invariant(
    ["none", "navigate", "connect", "send", "analytics_export", "custom"].includes(
      c.action as string,
    ),
    "invalid action",
  );
  invariant(
    Array.isArray(c.phaseContract) && c.phaseContract.every((x) => PROGRESS_STATES.includes(x)),
    "invalid phase contract",
  );
  assertTimestamp(c.createdAt, "createdAt");
  invariant(Number.isSafeInteger(c.sessionId) && (c.sessionId as number) > 0, "invalid sessionId");
  record(c.input, "input");
  if (commandNeedsCandidate(c.command as string)) {
    invariant(c.candidate !== undefined, "candidate required");
    assertCandidateIdentity(c.candidate);
  } else invariant(c.candidate === undefined, "candidate forbidden");
  if (c.command === "commit-send") {
    invariant(c.sendPreparation !== undefined, "send preparation required");
    assertSendPreparationBinding(c.sendPreparation, c.sessionId as number);
    invariant(
      same((c.sendPreparation as SendPreparationReceipt).candidate, c.candidate),
      "send preparation candidate mismatch",
    );
  } else invariant(c.sendPreparation === undefined, "send preparation forbidden");
  if (
    c.command === "navigate-candidate-results" ||
    c.command === "capture-candidate-results" ||
    c.command === "walk-list"
  ) {
    invariant(c.sourceContract !== undefined, "source contract required");
    assertNetworkSourceContract(c.sourceContract);
  } else invariant(c.sourceContract === undefined, "source contract forbidden");
}
export function assertInvocationReceipt(v: unknown): asserts v is InvocationReceipt {
  const r = record(v, "receipt");
  exact(
    r,
    [
      "schemaVersion",
      "invocationId",
      "command",
      "definitionId",
      "action",
      "startedAt",
      "finishedAt",
      "exitCode",
      "outcome",
      "result",
    ],
    ["candidate", "blocker"],
  );
  invariant(r.schemaVersion === 1, "unsupported schemaVersion");
  assertInvocationId(r.invocationId);
  assertCommand(r.command);
  invariant(typeof r.definitionId === "string", "invalid definitionId");
  invariant(typeof r.action === "string", "invalid action");
  assertTimestamp(r.startedAt, "startedAt");
  assertTimestamp(r.finishedAt, "finishedAt");
  invariant(
    Date.parse(r.finishedAt as string) >= Date.parse(r.startedAt as string),
    "receipt timestamps reversed",
  );
  invariant(Number.isInteger(r.exitCode), "invalid exitCode");
  invariant(
    ["succeeded", "failed", "critical_uncertainty"].includes(r.outcome as string),
    "invalid outcome",
  );
  if (r.candidate !== undefined) assertCandidateIdentity(r.candidate);
  if (r.blocker !== undefined) assertBlocker(r.blocker);
  invariant(
    r.result === null ||
      (typeof r.result === "object" && r.result !== null && !Array.isArray(r.result)),
    "invalid result",
  );
}
export function assertStdoutResult(
  v: unknown,
  command: string,
): asserts v is Record<string, unknown> {
  const x = record(v, "stdout result");
  exact(x, ["schemaVersion", "command", "ok", "data", "logs"]);
  invariant(
    x.schemaVersion === 1 && x.command === command && x.ok === true,
    "stdout result identity invalid",
  );
  invariant(
    typeof x.data === "object" && x.data !== null && !Array.isArray(x.data),
    "stdout data invalid",
  );
  const logs = record(x.logs, "stdout diagnostic summary");
  exact(logs, [
    "schemaVersion",
    "kind",
    "selectionStep",
    "sourceCount",
    "relevantCount",
    "otherCount",
    "genericNetErrFailedCount",
    "sha256",
    "artifact",
  ]);
  invariant(
    logs.schemaVersion === 1 &&
      logs.kind === "playwriter_diagnostic_summary" &&
      logs.selectionStep === "terminal-preserving-diagnostic-selection-v2" &&
      [logs.sourceCount, logs.relevantCount, logs.otherCount, logs.genericNetErrFailedCount].every(
        (count) => Number.isSafeInteger(count) && (count as number) >= 0,
      ) &&
      typeof logs.sha256 === "string" &&
      SHA256_HEX_RE.test(logs.sha256) &&
      logs.artifact === "diagnostics.json",
    "stdout diagnostic summary invalid",
  );
}
export function parseProgress(raw: string): { events: ProgressEvent[]; corrupt?: string } {
  const events: ProgressEvent[] = [];
  for (const [i, line] of raw.split(/\r?\n/).entries()) {
    if (!line) continue;
    try {
      const e = JSON.parse(line);
      assertProgressEvent(e);
      events.push(e);
    } catch (error) {
      return {
        events,
        corrupt: `line ${i + 1}: ${error instanceof Error ? error.message : String(error)}`,
      };
    }
  }
  return { events };
}
function assertProgressEvent(v: unknown): asserts v is ProgressEvent {
  const e = record(v, "progress");
  exact(e, ["invocationId", "command", "state", "timestamp"], ["candidate", "detail"]);
  assertInvocationId(e.invocationId);
  assertCommand(e.command);
  invariant(PROGRESS_STATES.includes(e.state as never), "invalid progress state");
  assertTimestamp(e.timestamp);
  if (e.candidate !== undefined) assertCandidateIdentity(e.candidate);
  if (e.detail !== undefined) record(e.detail, "detail");
}
export function assertProgressSequence(events: readonly ProgressEvent[]): void {
  invariant(events.length >= 3, "progress incomplete");
  let time = -Infinity;
  for (const [i, e] of events.entries()) {
    assertProgressEvent(e);
    const t = Date.parse(e.timestamp);
    invariant(t >= time, "progress timestamps not monotonic");
    time = t;
    if (i === 0) invariant(e.state === "invocation_created", "invalid initial progress");
    if (i === 1) invariant(e.state === "process_started", "process start missing");
    if (i > 0) {
      const p = events[i - 1];
      invariant(p !== undefined, "previous progress missing");
      invariant(
        e.invocationId === p.invocationId &&
          e.command === p.command &&
          same(e.candidate, p.candidate),
        "progress identity changed",
      );
    }
  }
  invariant(
    ["process_succeeded", "process_failed"].includes(events.at(-1)?.state ?? ""),
    "terminal progress missing",
  );
}
export function assertInvocationEvidence(
  config: InvocationConfig,
  receipt: InvocationReceipt,
  events: readonly ProgressEvent[],
): void {
  assertInvocationConfig(config);
  assertInvocationReceipt(receipt);
  assertProgressSequence(events);
  invariant(
    receipt.invocationId === config.invocationId &&
      receipt.command === config.command &&
      receipt.definitionId === config.definitionId &&
      receipt.action === config.action,
    "receipt/config mismatch",
  );
  invariant(same(receipt.candidate, config.candidate), "receipt candidate mismatch");
  for (const e of events) {
    invariant(
      e.invocationId === config.invocationId &&
        e.command === config.command &&
        same(e.candidate, config.candidate),
      "progress/config mismatch",
    );
    invariant(Date.parse(e.timestamp) >= Date.parse(config.createdAt), "progress predates config");
  }
  invariant(
    Date.parse(config.createdAt) <= Date.parse(receipt.startedAt) &&
      Date.parse(receipt.startedAt) <= Date.parse(receipt.finishedAt),
    "timestamp ordering invalid",
  );
  const actual = events.slice(2, -1).map((e) => e.state);
  invariant(
    actual.every((x, i) => x === config.phaseContract[i]) &&
      actual.length <= config.phaseContract.length,
    "phase contract mismatch",
  );
  if (receipt.outcome === "succeeded") {
    invariant(
      receipt.exitCode === 0 &&
        receipt.result !== null &&
        actual.length === config.phaseContract.length &&
        events.at(-1)?.state === "process_succeeded",
      "invalid success evidence",
    );
  } else invariant(events.at(-1)?.state === "process_failed", "invalid failure terminal");
  const boundary =
    config.command === "commit-send" || events.some((e) => e.state === "analytics_confirm_started");
  invariant(
    (receipt.outcome === "critical_uncertainty") === (boundary && receipt.outcome !== "succeeded"),
    "send uncertainty equivalence invalid",
  );
  if (receipt.outcome === "critical_uncertainty")
    invariant(
      receipt.blocker?.retryability === "possible_send" || receipt.blocker === undefined,
      "uncertainty blocker invalid",
    );
}

function assertCount(value: unknown, field: string): asserts value is number {
  invariant(Number.isSafeInteger(value) && (value as number) >= 0, `invalid ${field}`);
}

function assertSourceReloadEvidence(
  value: unknown,
  capturedAt: string,
): asserts value is SourceReloadEvidence {
  const reload = record(value, "source reload");
  exact(reload, ["navigationInvocationId", "reloadIdentity", "reloadGeneration", "navigatedAt"]);
  assertInvocationId(reload.navigationInvocationId);
  invariant(
    reload.reloadIdentity === `${reload.navigationInvocationId}:reload`,
    "invalid reload identity",
  );
  invariant(
    Number.isSafeInteger(reload.reloadGeneration) && (reload.reloadGeneration as number) >= 1,
    "invalid reload generation",
  );
  assertTimestamp(reload.navigatedAt, "navigatedAt");
  invariant(
    Date.parse(reload.navigatedAt as string) <= Date.parse(capturedAt),
    "reload after capture",
  );
}

export function assertSourceTerminalEvidence(
  value: unknown,
  sourceContract: NetworkSourceContract,
): asserts value is SourceTerminalEvidence {
  assertNetworkSourceContract(sourceContract);
  const evidence = record(value, "source terminal evidence");
  exact(evidence, [
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
  ]);
  invariant(
    evidence.schemaVersion === 1 && evidence.kind === "network_source_terminal_observation",
    "invalid source terminal identity",
  );
  assertInvocationId(evidence.captureInvocationId);
  assertTimestamp(evidence.observedAt, "observedAt");
  invariant(
    evidence.sourceId === sourceContract.sourceId &&
      evidence.sourceName === sourceContract.sourceName &&
      evidence.savedSearchId === sourceContract.savedSearchId &&
      evidence.searchUrl === sourceContract.searchUrl &&
      evidence.sourceContractVersion === sourceContract.contractVersion &&
      evidence.sourceContractFingerprint === sourceContract.contractFingerprint,
    "terminal source contract mismatch",
  );
  invariant(
    typeof evidence.cursorIdentity === "string" &&
      /^Page [1-9][0-9]*$/.test(evidence.cursorIdentity),
    "invalid cursor identity",
  );
  invariant(
    evidence.pageIdentity ===
      `salesnav-saved-search:${sourceContract.savedSearchId}:${evidence.cursorIdentity}`,
    "invalid page identity",
  );
  invariant(
    Array.isArray(evidence.stableRowIds) &&
      evidence.stableRowIds.length > 0 &&
      evidence.stableRowIds.every(
        (item) => typeof item === "string" && SOURCE_ROW_ID_RE.test(item),
      ) &&
      new Set(evidence.stableRowIds).size === evidence.stableRowIds.length &&
      same(evidence.stableRowIds, [...evidence.stableRowIds].sort()),
    "invalid stable terminal rows",
  );
  invariant(
    evidence.rowCount === evidence.stableRowIds.length && evidence.nextControl === "disabled",
    "invalid terminal row/control evidence",
  );
  assertInvocationId(evidence.navigationInvocationId);
  invariant(
    evidence.reloadIdentity === `${evidence.navigationInvocationId}:reload`,
    "invalid terminal reload identity",
  );
  invariant(
    Number.isSafeInteger(evidence.reloadGeneration) && (evidence.reloadGeneration as number) >= 1,
    "invalid terminal reload generation",
  );
  assertTimestamp(evidence.navigatedAt, "terminal navigatedAt");
  invariant(
    Date.parse(evidence.navigatedAt as string) <= Date.parse(evidence.observedAt as string),
    "terminal reload after observation",
  );
  invariant(
    typeof evidence.terminalFingerprint === "string" &&
      SHA256_HEX_RE.test(evidence.terminalFingerprint) &&
      evidence.terminalFingerprint ===
        terminalFingerprint({
          sourceContractFingerprint: sourceContract.contractFingerprint,
          searchUrl: sourceContract.searchUrl,
          pageIdentity: evidence.pageIdentity as string,
          cursorIdentity: evidence.cursorIdentity as string,
          stableRowIds: evidence.stableRowIds as string[],
          rowCount: evidence.rowCount as number,
          nextControl: "disabled",
        }),
    "invalid terminal fingerprint",
  );
}

export function assertSourceCaptureResultData(
  value: unknown,
  expected: { readonly invocationId: string; readonly sourceContract: NetworkSourceContract },
): asserts value is SourceCaptureResultData {
  assertInvocationId(expected.invocationId);
  assertNetworkSourceContract(expected.sourceContract);
  const data = record(value, "source capture result");
  exact(
    data,
    [
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
    ],
    ["terminalEvidence"],
  );
  invariant(
    data.schemaVersion === 1 && data.kind === "network_source_capture",
    "invalid source capture identity",
  );
  invariant(data.captureInvocationId === expected.invocationId, "capture invocation mismatch");
  assertTimestamp(data.capturedAt, "capturedAt");
  assertNetworkSourceContract(data.sourceContract);
  invariant(same(data.sourceContract, expected.sourceContract), "capture source contract mismatch");
  invariant(
    isEquivalentCapturedSourceUrl(data.url, expected.sourceContract.searchUrl),
    "capture source URL mismatch",
  );
  invariant(Array.isArray(data.items) && data.items.length <= 30, "invalid source capture rows");
  const rowIdentities = new Set<string>();
  const leadUrls = new Set<string>();
  for (const raw of data.items) {
    const row = record(raw, "source capture row");
    exact(row, ["rowIdentity", "salesLeadUrl", "name"]);
    invariant(
      typeof row.rowIdentity === "string" &&
        SOURCE_ROW_ID_RE.test(row.rowIdentity) &&
        !rowIdentities.has(row.rowIdentity),
      "invalid or duplicate row identity",
    );
    invariant(
      typeof row.name === "string" && row.name.trim() === row.name && row.name.length > 0,
      "invalid source row name",
    );
    invariant(typeof row.salesLeadUrl === "string", "invalid source row lead URL");
    assertAllowedWorkflowUrl("salesLead", row.salesLeadUrl as string);
    invariant(!leadUrls.has(row.salesLeadUrl as string), "duplicate source row lead URL");
    rowIdentities.add(row.rowIdentity);
    leadUrls.add(row.salesLeadUrl as string);
  }
  if (data.reload !== null) assertSourceReloadEvidence(data.reload, data.capturedAt as string);

  const page = record(data.page, "source capture page");
  exact(page, [
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
  ]);
  invariant(page.stateKey === "networkCandidateResultsPage", "invalid source page state key");
  invariant(
    isEquivalentCapturedSourceUrl(page.url, expected.sourceContract.searchUrl),
    "source page URL mismatch",
  );
  for (const field of ["resultsContainerCount", "progressbarCount", "alertCount", "dialogCount"])
    assertCount(page[field], field);
  invariant((page.resultsContainerCount as number) <= 1, "ambiguous results container");
  invariant(typeof page.resultsContainerVisible === "boolean", "invalid results visibility");
  invariant(
    page.ariaBusy === null || page.ariaBusy === "true" || page.ariaBusy === "false",
    "invalid results busy state",
  );
  invariant(
    page.fullyLoaded ===
      (page.resultsContainerCount === 1 &&
        page.resultsContainerVisible === true &&
        page.ariaBusy !== "true" &&
        page.progressbarCount === 0),
    "invalid fully-loaded evidence",
  );
  invariant(
    page.blockerFree === (page.alertCount === 0 && page.dialogCount === 0),
    "invalid blocker-free evidence",
  );
  if (page.cursorIdentity === null)
    invariant(page.pageIdentity === null, "page identity requires cursor identity");
  else {
    invariant(
      typeof page.cursorIdentity === "string" && /^Page [1-9][0-9]*$/.test(page.cursorIdentity),
      "invalid page cursor identity",
    );
    invariant(
      page.pageIdentity ===
        `salesnav-saved-search:${expected.sourceContract.savedSearchId}:${page.cursorIdentity}`,
      "invalid source page identity",
    );
  }

  const pagination = record(data.pagination, "source capture pagination");
  exact(pagination, ["navigationCount", "currentPageCount", "nextControlCount", "nextDisabled"]);
  for (const field of ["navigationCount", "currentPageCount", "nextControlCount"])
    assertCount(pagination[field], field);
  invariant(
    (pagination.navigationCount as number) <= 1 &&
      (pagination.currentPageCount as number) <= 1 &&
      (pagination.nextControlCount as number) <= 1,
    "ambiguous pagination evidence",
  );
  if (pagination.navigationCount === 0)
    invariant(
      pagination.currentPageCount === 0 &&
        pagination.nextControlCount === 0 &&
        pagination.nextDisabled === null,
      "pagination descendants without navigation",
    );
  else
    invariant(
      pagination.nextDisabled === null || typeof pagination.nextDisabled === "boolean",
      "invalid next control state",
    );
  invariant(
    (pagination.nextControlCount === 0 && pagination.nextDisabled === null) ||
      (pagination.nextControlCount === 1 && typeof pagination.nextDisabled === "boolean"),
    "next control state mismatch",
  );
  invariant(
    (pagination.currentPageCount === 0 && page.cursorIdentity === null) ||
      (pagination.currentPageCount === 1 && page.cursorIdentity !== null),
    "current page cursor mismatch",
  );

  const terminalEligible =
    page.fullyLoaded === true &&
    page.blockerFree === true &&
    data.items.length > 0 &&
    data.reload !== null &&
    pagination.navigationCount === 1 &&
    pagination.currentPageCount === 1 &&
    pagination.nextControlCount === 1 &&
    pagination.nextDisabled === true &&
    page.pageIdentity !== null &&
    page.cursorIdentity !== null;
  invariant(
    (data.terminalEvidence !== undefined) === terminalEligible,
    terminalEligible ? "terminal evidence required" : "terminal evidence forbidden",
  );
  if (data.terminalEvidence !== undefined) {
    assertSourceTerminalEvidence(data.terminalEvidence, expected.sourceContract);
    invariant(
      data.terminalEvidence.captureInvocationId === expected.invocationId &&
        data.terminalEvidence.observedAt === data.capturedAt &&
        data.terminalEvidence.pageIdentity === page.pageIdentity &&
        data.terminalEvidence.cursorIdentity === page.cursorIdentity &&
        same(data.terminalEvidence.stableRowIds, [...rowIdentities].sort()) &&
        data.terminalEvidence.rowCount === data.items.length &&
        data.reload !== null &&
        data.terminalEvidence.navigationInvocationId === data.reload.navigationInvocationId &&
        data.terminalEvidence.reloadIdentity === data.reload.reloadIdentity &&
        data.terminalEvidence.reloadGeneration === data.reload.reloadGeneration &&
        data.terminalEvidence.navigatedAt === data.reload.navigatedAt,
      "terminal evidence does not bind capture",
    );
  }
}

export function immutableSourceCaptureResultData(
  value: unknown,
  expected: { readonly invocationId: string; readonly sourceContract: NetworkSourceContract },
): SourceCaptureResultData {
  assertSourceCaptureResultData(value, expected);
  const canonical = structuredClone(value) as SourceCaptureResultData;
  const canonicalPage = canonical.page as { url: string };
  (canonical as { url: string }).url = expected.sourceContract.searchUrl;
  canonicalPage.url = expected.sourceContract.searchUrl;
  return deepFreeze(canonical);
}

function isEquivalentCapturedSourceUrl(value: unknown, expected: string): boolean {
  if (typeof value !== "string") return false;
  if (value === expected) return true;
  try {
    const observed = new URL(value);
    const contract = new URL(expected);
    if (
      observed.origin !== contract.origin ||
      observed.pathname !== contract.pathname ||
      observed.hash !== "" ||
      observed.searchParams.get("savedSearchId") !== contract.searchParams.get("savedSearchId")
    )
      return false;
    const keys = [...observed.searchParams.keys()];
    return (
      keys.length === 2 &&
      keys.includes("savedSearchId") &&
      keys.includes("sessionId") &&
      (observed.searchParams.get("sessionId")?.trim().length ?? 0) > 0
    );
  } catch {
    return false;
  }
}

export function assertSourceExhaustionEvidence(v: SourceExhaustionEvidence): void {
  const top = record(v, "source exhaustion");
  exact(top, ["sourceContract", "observations"]);
  assertNetworkSourceContract(v.sourceContract);
  invariant(
    Array.isArray(v.observations) && v.observations.length === 2,
    "two observations required",
  );
  const [a, b] = v.observations;
  assertSourceTerminalEvidence(a, v.sourceContract);
  assertSourceTerminalEvidence(b, v.sourceContract);
  invariant(
    a.captureInvocationId !== b.captureInvocationId &&
      a.navigationInvocationId !== b.navigationInvocationId &&
      a.reloadIdentity !== b.reloadIdentity &&
      b.reloadGeneration > a.reloadGeneration &&
      Date.parse(b.observedAt) >= Date.parse(a.observedAt),
    "observations must be distinct ordered reloads",
  );
  invariant(
    a.terminalFingerprint === b.terminalFingerprint &&
      a.pageIdentity === b.pageIdentity &&
      a.cursorIdentity === b.cursorIdentity &&
      same(a.stableRowIds, b.stableRowIds) &&
      a.rowCount === b.rowCount &&
      a.nextControl === b.nextControl,
    "terminal evidence mismatch",
  );
}
