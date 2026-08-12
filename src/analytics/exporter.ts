import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants, type Stats } from "node:fs";
import {
  copyFile,
  link,
  lstat,
  mkdir,
  open,
  readFile,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { INVOCATION_ID_RE, SHA256_HEX_RE } from "../core/evidence-contract.ts";
import { commandConfig, LINKEDIN_CONTENT_ANALYTICS_CONTRACT } from "./contract.ts";
import { changedDownloads, isContainedPath, snapshotDownloads } from "./downloads.ts";
import {
  type AnalyticsBrowserEvidence,
  type AnalyticsCommandResult,
  type AnalyticsConfirmAttempt,
  type AnalyticsDownloadEvidence,
  type AnalyticsExportOperation,
  type AnalyticsExportReceipt,
  AnalyticsNeedsReconciliationError,
  type AnalyticsOperation,
  type AnalyticsPreConfirmEvidence,
  type AnalyticsReconciliationCheckpoint,
  type AnalyticsRecoveryPhase,
  type AnalyticsRecoveryState,
  type DownloadSnapshotEntry,
  type ExportAnalyticsOptions,
} from "./types.ts";
import {
  assertInclusiveSevenDayRange,
  assertRequestedWorkbook,
  MAX_WORKBOOK_BYTES,
  parseWorkbookFilename,
  validateWorkbookZip,
} from "./workbook.ts";

const OPERATION_ID = INVOCATION_ID_RE;
const SHA256 = SHA256_HEX_RE;
export const ANALYTICS_RECONCILIATION_WINDOW_MS = 15 * 60 * 1_000;

interface CandidateObservation {
  readonly candidate: DownloadSnapshotEntry;
  readonly confirmationPhase: "confirm_started" | "confirm_completed";
  readonly baselineStatus: "new" | "changed";
}

interface CandidatePollOutcome {
  readonly observation?: CandidateObservation;
  readonly reason?: AnalyticsReconciliationCheckpoint["reason"];
  readonly candidates: readonly DownloadSnapshotEntry[];
}

class AnalyticsSourceIdentityChangedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AnalyticsSourceIdentityChangedError";
  }
}

export async function exportContentAnalytics(
  options: ExportAnalyticsOptions,
): Promise<AnalyticsExportReceipt> {
  validateRequest(options);
  const recoveryStatePath = options.recoveryStatePath ?? `${options.receiptPath}.state.json`;
  const canonicalRoots = await preflightPaths(options, recoveryStatePath);
  let recovery = await readRecoveryState(recoveryStatePath, options, canonicalRoots);
  const existing = await recoverPublishedExport(options, recoveryStatePath, recovery);
  if (existing !== undefined) return existing;
  if (recovery?.phase === "publish_started" || recovery?.phase === "completed")
    return completePreparedPublication(options, recoveryStatePath, recovery);

  const sleep = options.sleep ?? ((milliseconds) => Bun.sleep(milliseconds));
  let dialogEstablished = false;
  if (recovery === undefined) {
    const exportOperation = createExportOperation(options);
    await establishConfirmationDialog(options, exportOperation);
    dialogEstablished = true;
    const baseline = await snapshotDownloads(canonicalRoots);
    const preConfirmEvidence = createPreConfirmEvidence(options, exportOperation, baseline);
    recovery = await persistRecoveryState(recoveryStatePath, {
      schemaVersion: 1,
      expectedAccount: options.expectedAccount,
      expectedStartDate: options.expectedStartDate,
      expectedEndDate: options.expectedEndDate,
      outputPath: resolve(options.outputPath),
      receiptPath: resolve(options.receiptPath),
      downloadRoots: Object.freeze([...canonicalRoots]),
      exportOperation,
      preConfirmEvidence,
      phase: "ready_to_confirm",
      preConfirmSnapshot: freezeSnapshot(baseline),
      updatedAt: timestamp(options),
    });
  }

  const baseline = snapshotMap(recovery.preConfirmSnapshot);
  let observation: CandidateObservation | undefined;
  let browserConfirmation: AnalyticsExportReceipt["browserConfirmation"] = "performed";
  let confirmationEvidence: AnalyticsExportReceipt["confirmationEvidence"];
  let confirmationStartedAt: string;
  let confirmationCompletedAt: string;

  if (
    recovery.phase === "confirm_started" ||
    recovery.phase === "confirm_completed" ||
    recovery.phase === "needs_reconciliation"
  ) {
    if (
      recovery.phase === "needs_reconciliation" &&
      recovery.reconciliation !== undefined &&
      isStickyReconciliationReason(recovery.reconciliation.reason)
    )
      throw needsReconciliation(recovery);
    const outcome = await pollForOperationCandidate(
      options,
      canonicalRoots,
      recovery,
      baseline,
      sleep,
    );
    if (outcome.observation === undefined) {
      recovery = await markNeedsReconciliation(
        recoveryStatePath,
        options,
        recovery,
        outcome.reason ?? "no_workbook_observed",
        outcome.candidates,
      );
      throw needsReconciliation(recovery);
    }
    observation = outcome.observation;
    browserConfirmation = "recovered_from_download";
    confirmationEvidence = recovery.confirmEvidence ? "browser_completed" : "download_reconciled";
    confirmationStartedAt = requireConfirmAttempt(recovery).startedAt;
    confirmationCompletedAt =
      recovery.confirmEvidence?.actionCompletedAt ??
      new Date(observation.candidate.modifiedAtMs).toISOString();
  } else if (recovery.phase === "ready_to_confirm") {
    if (!dialogEstablished) await establishConfirmationDialog(options, recovery.exportOperation);
    const startedAt = timestamp(options);
    const confirmAttempt = Object.freeze({
      operationId: recovery.exportOperation.operationId,
      preConfirmSnapshotId: recovery.preConfirmEvidence.snapshotId,
      startedAt,
      reconciliationDeadlineAt: new Date(
        Date.parse(startedAt) + ANALYTICS_RECONCILIATION_WINDOW_MS,
      ).toISOString(),
    });
    recovery = await persistRecoveryState(
      recoveryStatePath,
      transitionRecovery(recovery, {
        phase: "confirm_started",
        confirmAttempt,
        updatedAt: timestamp(options),
      }),
    );
    const result = await runStep(
      options,
      "confirm_export",
      recovery.exportOperation,
      recovery.preConfirmEvidence,
    );
    assertConfirmTiming(result.evidence, confirmAttempt);
    recovery = await persistRecoveryState(
      recoveryStatePath,
      transitionRecovery(recovery, {
        phase: "confirm_completed",
        confirmEvidence: result.evidence,
        updatedAt: timestamp(options),
      }),
    );
    const outcome = await pollForOperationCandidate(
      options,
      canonicalRoots,
      recovery,
      baseline,
      sleep,
    );
    if (outcome.observation === undefined) {
      recovery = await markNeedsReconciliation(
        recoveryStatePath,
        options,
        recovery,
        outcome.reason ?? "no_workbook_observed",
        outcome.candidates,
      );
      throw needsReconciliation(recovery);
    }
    observation = outcome.observation;
    confirmationEvidence = "browser_completed";
    confirmationStartedAt = confirmAttempt.startedAt;
    confirmationCompletedAt = result.evidence.actionCompletedAt;
  } else {
    throw new Error(`unsupported analytics recovery phase: ${String(recovery.phase)}`);
  }

  if (observation === undefined) throw new Error("analytics workbook observation disappeared");
  const stagingPath = stagingPathFor(options.outputPath, recovery.exportOperation.operationId);
  try {
    const sourcePath = await validateCandidateAtUse(observation.candidate);
    await waitForStableNonzeroSize(sourcePath, options, sleep);
    const stableCandidate = await refreshCandidateAtUse(observation.candidate);
    await copyCandidateToPrivateStage(stableCandidate, stagingPath, options);
    const identity = parseWorkbookFilename(sourcePath);
    assertRequestedWorkbook(identity, expectedWorkbook(options));
    await validateWorkbookZip(stagingPath);
    const { byteSize, sha256 } = await hashRegularFileNoFollow(stagingPath);
    const finalOutcome = await inspectOperationCandidateSet(canonicalRoots, baseline, recovery);
    const finalCandidate = finalOutcome.observation?.candidate;
    if (finalCandidate === undefined || !sameCandidate(finalCandidate, stableCandidate)) {
      const reason =
        finalOutcome.reason === undefined || finalOutcome.reason === "no_workbook_observed"
          ? "candidate_set_changed"
          : finalOutcome.reason;
      recovery = await markNeedsReconciliation(
        recoveryStatePath,
        options,
        recovery,
        reason,
        finalOutcome.candidates,
      );
      throw needsReconciliation(recovery);
    }
    const downloadEvidence = createDownloadEvidence(
      options,
      recovery,
      observation,
      stableCandidate,
    );
    const publicationMaterial = Object.freeze({
      sourcePath,
      filename: identity.filename,
      account: identity.account,
      startDate: identity.startDate,
      endDate: identity.endDate,
      byteSize,
      sha256,
      browserConfirmation,
      confirmationEvidence,
      operationId: recovery.exportOperation.operationId,
      preConfirmSnapshotId: recovery.preConfirmEvidence.snapshotId,
      resultUrl: recovery.exportOperation.resultUrl,
      confirmationStartedAt,
      confirmationCompletedAt,
      downloadEvidence,
    });
    const preparedReceipt = createReceipt(options, publicationMaterial);
    const publication = Object.freeze({
      ...publicationMaterial,
      stagingPath,
      preparedReceipt,
    });
    recovery = await persistRecoveryState(
      recoveryStatePath,
      transitionRecovery(recovery, {
        phase: "publish_started",
        publication,
        updatedAt: timestamp(options),
      }),
    );
  } catch (error) {
    if (error instanceof AnalyticsSourceIdentityChangedError) {
      let candidates: readonly DownloadSnapshotEntry[] = Object.freeze([]);
      try {
        candidates = (await inspectOperationCandidateSet(canonicalRoots, baseline, recovery))
          .candidates;
      } catch {
        // The changed source may itself make a safe snapshot impossible; uncertainty stays sticky.
      }
      recovery = await markNeedsReconciliation(
        recoveryStatePath,
        options,
        recovery,
        "candidate_set_changed",
        candidates,
      );
    }
    if (recovery.publication === undefined) await rm(stagingPath, { force: true });
    throw error;
  }
  return completePreparedPublication(options, recoveryStatePath, recovery);
}

async function establishConfirmationDialog(
  options: ExportAnalyticsOptions,
  exportOperation: AnalyticsExportOperation,
): Promise<void> {
  await runStep(options, "navigate", exportOperation);
  await runStep(options, "open_export", exportOperation);
  await runStep(options, "observe_dialog", exportOperation);
}

async function runStep(
  options: ExportAnalyticsOptions,
  operation: AnalyticsOperation,
  exportOperation: AnalyticsExportOperation,
  preConfirmEvidence?: AnalyticsPreConfirmEvidence,
): Promise<AnalyticsCommandResult> {
  const result: unknown = await options.runner.execute(
    commandConfig(operation, exportOperation, preConfirmEvidence),
  );
  assertCommandResult(result, operation, exportOperation, preConfirmEvidence);
  return result;
}

export function assertCommandResult(
  result: unknown,
  operation: AnalyticsOperation,
  exportOperation: AnalyticsExportOperation,
  preConfirmEvidence?: AnalyticsPreConfirmEvidence,
): asserts result is AnalyticsCommandResult {
  if (!isRecord(result)) throw new Error(`analytics ${operation} returned a non-object result`);
  if (result.schemaVersion !== 1)
    throw new Error(`analytics ${operation} returned an invalid schemaVersion`);
  if (result.operation !== operation)
    throw new Error(`analytics command returned the wrong operation: ${String(result.operation)}`);
  if (
    !(<readonly unknown[]>["completed", "blocked", "contract_changed", "failed"]).includes(
      result.status,
    )
  )
    throw new Error(`analytics ${operation} returned an invalid status`);
  if (result.status !== "completed")
    throw new Error(`analytics ${operation} ${String(result.status)}`);
  if (result.observedUrl !== LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url)
    throw new Error(`analytics ${operation} observed the wrong URL: ${String(result.observedUrl)}`);
  if (!isRecord(result.evidence))
    throw new Error(`analytics ${operation} omitted typed operation evidence`);
  const evidence = result.evidence;
  const bindings: Readonly<Record<string, unknown>> = {
    operationId: exportOperation.operationId,
    account: exportOperation.account,
    startDate: exportOperation.startDate,
    endDate: exportOperation.endDate,
    requestedRange: exportOperation.requestedRange,
    resultUrl: exportOperation.resultUrl,
  };
  for (const [field, expected] of Object.entries(bindings))
    if (evidence[field] !== expected)
      throw new Error(`analytics ${operation} evidence mismatch for ${field}`);
  assertTimestamp(evidence.actionStartedAt, `${operation} actionStartedAt`);
  assertTimestamp(evidence.actionCompletedAt, `${operation} actionCompletedAt`);
  if (Date.parse(evidence.actionCompletedAt) < Date.parse(evidence.actionStartedAt))
    throw new Error(`analytics ${operation} evidence timestamps are reversed`);
  if (operation === "confirm_export") {
    if (preConfirmEvidence === undefined)
      throw new Error("analytics confirm_export has no expected pre-confirm evidence");
    if (evidence.preConfirmSnapshotId !== preConfirmEvidence.snapshotId)
      throw new Error("analytics confirm_export snapshot evidence mismatch");
    if (evidence.confirmationTextVisibleCount !== 1 || evidence.confirmButtonVisibleCount !== 1)
      throw new Error(
        "analytics confirm_export did not prove one visible confirmation message and Confirm button",
      );
  }
  if (result.detail !== undefined && !isRecord(result.detail))
    throw new Error(`analytics ${operation} returned invalid detail`);
}

function createExportOperation(options: ExportAnalyticsOptions): AnalyticsExportOperation {
  const operationId =
    options.createOperationId?.() ?? `analytics_${randomUUID().replaceAll("-", "")}`;
  if (!OPERATION_ID.test(operationId)) throw new Error("invalid analytics operationId");
  return Object.freeze({
    schemaVersion: 1,
    operationId,
    account: options.expectedAccount,
    startDate: options.expectedStartDate,
    endDate: options.expectedEndDate,
    requestedRange: "7 days",
    resultUrl: LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url,
    createdAt: timestamp(options),
  });
}

function createPreConfirmEvidence(
  options: ExportAnalyticsOptions,
  exportOperation: AnalyticsExportOperation,
  baseline: ReadonlyMap<string, DownloadSnapshotEntry>,
): AnalyticsPreConfirmEvidence {
  const capturedAt = timestamp(options);
  return Object.freeze({
    schemaVersion: 1,
    operationId: exportOperation.operationId,
    snapshotId: snapshotDigest(exportOperation.operationId, capturedAt, [...baseline.values()]),
    capturedAt,
    entryCount: baseline.size,
  });
}

function snapshotDigest(
  operationId: string,
  capturedAt: string,
  entries: readonly DownloadSnapshotEntry[],
): string {
  const canonical = [...entries]
    .sort((left, right) => left.path.localeCompare(right.path))
    .map(({ path, realPath, rootRealPath, device, inode, birthtimeMs, size, modifiedAtMs }) => ({
      path,
      realPath,
      rootRealPath,
      device,
      inode,
      birthtimeMs,
      size,
      modifiedAtMs,
    }));
  return createHash("sha256")
    .update(JSON.stringify({ operationId, capturedAt, entries: canonical }))
    .digest("hex");
}

async function pollForOperationCandidate(
  options: ExportAnalyticsOptions,
  roots: readonly string[],
  recovery: AnalyticsRecoveryState,
  baseline: ReadonlyMap<string, DownloadSnapshotEntry>,
  sleep: (milliseconds: number) => Promise<void>,
): Promise<CandidatePollOutcome> {
  const maxPolls = options.maxPolls ?? 20;
  for (let poll = 0; poll < maxPolls; poll += 1) {
    const outcome = await inspectOperationCandidateSet(roots, baseline, recovery);
    if (outcome.observation !== undefined || outcome.reason !== "no_workbook_observed")
      return outcome;
    await sleep(options.pollIntervalMs ?? 250);
  }
  return Object.freeze({ reason: "no_workbook_observed", candidates: Object.freeze([]) });
}

async function inspectOperationCandidateSet(
  roots: readonly string[],
  baseline: ReadonlyMap<string, DownloadSnapshotEntry>,
  recovery: AnalyticsRecoveryState,
): Promise<CandidatePollOutcome> {
  const changed = Object.freeze(changedDownloads(baseline, await snapshotDownloads(roots)));
  if (changed.length === 0)
    return Object.freeze({ reason: "no_workbook_observed", candidates: changed });
  if (changed.length > 1)
    return Object.freeze({ reason: "multiple_changed_workbooks", candidates: changed });
  const candidate = changed[0];
  if (candidate === undefined)
    return Object.freeze({ reason: "no_workbook_observed", candidates: changed });
  const attempt = requireConfirmAttempt(recovery);
  if (
    candidate.modifiedAtMs < Date.parse(attempt.startedAt) ||
    candidate.modifiedAtMs > Date.parse(attempt.reconciliationDeadlineAt)
  )
    return Object.freeze({ reason: "workbook_outside_operation_window", candidates: changed });
  try {
    assertRequestedWorkbook(parseWorkbookFilename(candidate.path), {
      account: recovery.exportOperation.account,
      startDate: recovery.exportOperation.startDate,
      endDate: recovery.exportOperation.endDate,
    });
  } catch {
    return Object.freeze({ reason: "changed_workbook_mismatch", candidates: changed });
  }
  return Object.freeze({
    observation: Object.freeze({
      candidate,
      confirmationPhase: recoveryConfirmationPhase(recovery),
      baselineStatus: baseline.has(candidate.path) ? "changed" : "new",
    }),
    candidates: changed,
  });
}

function recoveryConfirmationPhase(
  recovery: AnalyticsRecoveryState,
): "confirm_started" | "confirm_completed" {
  if (recovery.phase === "confirm_started" || recovery.phase === "confirm_completed")
    return recovery.phase;
  if (recovery.phase === "needs_reconciliation" && recovery.reconciliation !== undefined)
    return recovery.reconciliation.confirmationPhase;
  if (
    (recovery.phase === "publish_started" || recovery.phase === "completed") &&
    recovery.publication !== undefined
  )
    return recovery.publication.downloadEvidence.confirmationPhase;
  throw new Error("analytics recovery has no confirmation phase");
}

async function markNeedsReconciliation(
  path: string,
  options: ExportAnalyticsOptions,
  recovery: AnalyticsRecoveryState,
  reason: AnalyticsReconciliationCheckpoint["reason"],
  observedCandidates: readonly DownloadSnapshotEntry[],
): Promise<AnalyticsRecoveryState> {
  if (recovery.phase === "needs_reconciliation" && recovery.reconciliation !== undefined) {
    if (isStickyReconciliationReason(recovery.reconciliation.reason)) return recovery;
    if (recovery.reconciliation.reason === reason) return recovery;
  }
  const reconciliation = Object.freeze({
    confirmationPhase: recoveryConfirmationPhase(recovery),
    reason,
    recordedAt: timestamp(options),
    observedCandidates: freezeSnapshot(snapshotMap(observedCandidates)),
  });
  return persistRecoveryState(
    path,
    transitionRecovery(recovery, {
      phase: "needs_reconciliation",
      reconciliation,
      updatedAt: timestamp(options),
    }),
  );
}

function isStickyReconciliationReason(
  reason: AnalyticsReconciliationCheckpoint["reason"],
): boolean {
  return reason !== "no_workbook_observed";
}

function needsReconciliation(recovery: AnalyticsRecoveryState): AnalyticsNeedsReconciliationError {
  const phase = recoveryConfirmationPhase(recovery);
  const reason = recovery.reconciliation?.reason ?? "no_workbook_observed";
  return new AnalyticsNeedsReconciliationError(
    recovery.exportOperation.operationId,
    phase,
    recovery.preConfirmEvidence.snapshotId,
    reason,
  );
}

function assertConfirmTiming(
  evidence: AnalyticsBrowserEvidence,
  attempt: AnalyticsConfirmAttempt,
): void {
  if (
    Date.parse(evidence.actionStartedAt) < Date.parse(attempt.startedAt) ||
    Date.parse(evidence.actionCompletedAt) > Date.parse(attempt.reconciliationDeadlineAt)
  )
    throw new Error("analytics confirm_export evidence is outside its durable operation window");
}

function requireConfirmAttempt(recovery: AnalyticsRecoveryState): AnalyticsConfirmAttempt {
  if (recovery.confirmAttempt === undefined)
    throw new Error("analytics recovery state has no confirm attempt evidence");
  return recovery.confirmAttempt;
}

function requirePublication(
  recovery: AnalyticsRecoveryState,
): NonNullable<AnalyticsRecoveryState["publication"]> {
  if (recovery.publication === undefined)
    throw new Error("analytics recovery state has no publication checkpoint");
  return recovery.publication;
}

async function preflightPaths(
  options: ExportAnalyticsOptions,
  recoveryStatePath: string,
): Promise<string[]> {
  if (options.downloadRoots.length === 0)
    throw new Error("at least one analytics download root is required");
  const roots: string[] = [];
  for (const root of options.downloadRoots) roots.push(await realpath(root));
  const targets = [options.outputPath, options.receiptPath, recoveryStatePath];
  const canonicalTargets: string[] = [];
  for (const target of targets) {
    const canonicalTarget = await canonicalProspectivePath(target);
    canonicalTargets.push(canonicalTarget);
    for (const root of roots)
      if (isContainedPath(root, canonicalTarget))
        throw new Error(`analytics output artifacts must be outside download roots: ${target}`);
    await rejectExistingSymlink(target);
  }
  if (new Set(canonicalTargets).size !== canonicalTargets.length)
    throw new Error("analytics output, receipt, and recovery paths must differ");
  for (const target of targets) await mkdir(dirname(target), { recursive: true });
  return roots;
}

async function recoverPublishedExport(
  options: ExportAnalyticsOptions,
  recoveryStatePath: string,
  recovery: AnalyticsRecoveryState | undefined,
): Promise<AnalyticsExportReceipt | undefined> {
  const outputExists = await pathExists(options.outputPath);
  const receiptExists = await pathExists(options.receiptPath);
  if (receiptExists && !outputExists)
    throw new Error("analytics receipt exists without its output workbook");
  if (receiptExists && outputExists) {
    const receipt = await readAndValidateReceipt(
      options.receiptPath,
      options,
      recovery?.publication?.preparedReceipt,
    );
    if (recovery?.publication !== undefined && recovery.phase !== "completed") {
      await persistRecoveryState(
        recoveryStatePath,
        transitionRecovery(recovery, { phase: "completed", updatedAt: timestamp(options) }),
      );
      await rm(recovery.publication.stagingPath, { force: true });
    }
    return receipt;
  }
  if (outputExists) return recoverOutputWithoutReceipt(options, recoveryStatePath, recovery);
  return undefined;
}

async function recoverOutputWithoutReceipt(
  options: ExportAnalyticsOptions,
  recoveryStatePath: string,
  recovery: AnalyticsRecoveryState | undefined,
): Promise<AnalyticsExportReceipt> {
  await rejectExistingSymlink(options.outputPath);
  if (
    recovery?.publication === undefined ||
    !(recovery.phase === "publish_started" || recovery.phase === "completed")
  )
    throw new Error("analytics output exists without a matching publication checkpoint or receipt");
  await validateWorkbookZip(options.outputPath);
  const { byteSize, sha256 } = await hashRegularFileNoFollow(options.outputPath);
  if (byteSize !== recovery.publication.byteSize || sha256 !== recovery.publication.sha256)
    throw new Error("analytics output conflicts with its publication checkpoint");
  const receipt = await writeReceiptWithoutOverwrite(
    options.receiptPath,
    recovery.publication.preparedReceipt,
    options,
  );
  await persistRecoveryState(
    recoveryStatePath,
    transitionRecovery(recovery, { phase: "completed", updatedAt: timestamp(options) }),
  );
  await rm(recovery.publication.stagingPath, { force: true });
  return receipt;
}

async function readAndValidateReceipt(
  path: string,
  options: ExportAnalyticsOptions,
  expectedReceipt?: AnalyticsExportReceipt,
): Promise<AnalyticsExportReceipt> {
  await rejectExistingSymlink(path);
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    throw new Error(
      `analytics receipt is unreadable: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (!isRecord(parsed) || parsed.schemaVersion !== 1 || parsed.status !== "completed")
    throw new Error("analytics receipt has an invalid shape");
  const stringFields = [
    "receiptId",
    "createdAt",
    "browserConfirmation",
    "confirmationEvidence",
    "operationId",
    "preConfirmSnapshotId",
    "resultUrl",
    "confirmationStartedAt",
    "confirmationCompletedAt",
    "sourcePath",
    "outputPath",
    "filename",
    "account",
    "startDate",
    "endDate",
    "sha256",
  ] as const;
  for (const field of stringFields)
    if (typeof parsed[field] !== "string" || parsed[field].length === 0)
      throw new Error(`analytics receipt has invalid ${field}`);
  if (!SHA256.test(parsed.preConfirmSnapshotId as string) || !SHA256.test(parsed.sha256 as string))
    throw new Error("analytics receipt has an invalid digest");
  if (!OPERATION_ID.test(parsed.operationId as string))
    throw new Error("analytics receipt has an invalid operationId");
  if (
    !(<readonly unknown[]>["performed", "recovered_from_download"]).includes(
      parsed.browserConfirmation,
    ) ||
    !(<readonly unknown[]>["browser_completed", "download_reconciled"]).includes(
      parsed.confirmationEvidence,
    )
  )
    throw new Error("analytics receipt has invalid confirmation evidence");
  if (parsed.resultUrl !== LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url)
    throw new Error("analytics receipt has an invalid result URL");
  assertTimestamp(parsed.confirmationStartedAt, "receipt confirmationStartedAt");
  assertTimestamp(parsed.confirmationCompletedAt, "receipt confirmationCompletedAt");
  if (Date.parse(parsed.confirmationCompletedAt) < Date.parse(parsed.confirmationStartedAt))
    throw new Error("analytics receipt confirmation timestamps are reversed");
  if (!Number.isSafeInteger(parsed.byteSize) || (parsed.byteSize as number) <= 0)
    throw new Error("analytics receipt has invalid byteSize");
  if (resolve(parsed.outputPath as string) !== resolve(options.outputPath))
    throw new Error("analytics receipt output path does not match the request");
  if (
    parsed.account !== options.expectedAccount ||
    parsed.startDate !== options.expectedStartDate ||
    parsed.endDate !== options.expectedEndDate
  )
    throw new Error("analytics receipt does not match the requested account and period");
  const downloadEvidence = parseDownloadEvidence(parsed.downloadEvidence, {
    operationId: parsed.operationId as string,
    preConfirmSnapshotId: parsed.preConfirmSnapshotId as string,
    resultUrl: LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url,
    confirmationStartedAt: parsed.confirmationStartedAt as string,
  });
  const actual = await hashRegularFileNoFollow(options.outputPath);
  if (actual.byteSize !== parsed.byteSize || actual.sha256 !== parsed.sha256)
    throw new Error("analytics output does not match its receipt");
  const receipt = Object.freeze({
    ...parsed,
    downloadEvidence,
  }) as unknown as AnalyticsExportReceipt;
  if (expectedReceipt !== undefined && JSON.stringify(receipt) !== JSON.stringify(expectedReceipt))
    throw new Error("analytics receipt conflicts with its prepared publication checkpoint");
  return receipt;
}

async function writeReceiptWithoutOverwrite(
  path: string,
  receipt: AnalyticsExportReceipt,
  options: ExportAnalyticsOptions,
): Promise<AnalyticsExportReceipt> {
  try {
    await writeFile(path, `${JSON.stringify(receipt, null, 2)}\n`, { flag: "wx" });
    return receipt;
  } catch (error) {
    if (!isAlreadyExists(error)) throw error;
    return readAndValidateReceipt(path, options, receipt);
  }
}

function createReceipt(
  options: ExportAnalyticsOptions,
  publication: Omit<
    NonNullable<AnalyticsRecoveryState["publication"]>,
    "stagingPath" | "preparedReceipt"
  >,
): AnalyticsExportReceipt {
  return Object.freeze({
    schemaVersion: 1,
    receiptId: options.createReceiptId?.() ?? `analytics_${randomUUID().replaceAll("-", "")}`,
    status: "completed",
    createdAt: timestamp(options),
    outputPath: options.outputPath,
    ...publication,
  });
}

async function completePreparedPublication(
  options: ExportAnalyticsOptions,
  recoveryStatePath: string,
  recovery: AnalyticsRecoveryState,
): Promise<AnalyticsExportReceipt> {
  const publication = requirePublication(recovery);
  await assertPreparedStage(publication.stagingPath, publication.byteSize, publication.sha256);
  try {
    await publishPreparedBytes(
      publication.stagingPath,
      options.outputPath,
      publication.byteSize,
      publication.sha256,
      async (verifiedTemp) => {
        await options.afterPublicationTempVerified?.(verifiedTemp);
        const finalOutcome = await inspectOperationCandidateSet(
          recovery.downloadRoots,
          snapshotMap(recovery.preConfirmSnapshot),
          recovery,
        );
        if (
          finalOutcome.observation !== undefined &&
          candidateMatchesDownloadEvidence(
            finalOutcome.observation.candidate,
            publication.downloadEvidence,
          )
        )
          return;
        const reason =
          finalOutcome.reason === undefined || finalOutcome.reason === "no_workbook_observed"
            ? "candidate_set_changed"
            : finalOutcome.reason;
        const marked = await markNeedsReconciliation(
          recoveryStatePath,
          options,
          recovery,
          reason,
          finalOutcome.candidates,
        );
        await rm(publication.stagingPath, { force: true });
        throw needsReconciliation(marked);
      },
    );
  } catch (error) {
    if (!isAlreadyExists(error)) throw error;
  }
  await validateWorkbookZip(options.outputPath);
  const publishedIdentity = await hashRegularFileNoFollow(options.outputPath);
  if (
    publishedIdentity.byteSize !== publication.byteSize ||
    publishedIdentity.sha256 !== publication.sha256
  )
    throw new Error("analytics published output does not match its immutable prepared stage");
  await options.afterOutputPublished?.();
  const beforeReceiptIdentity = await hashRegularFileNoFollow(options.outputPath);
  if (
    beforeReceiptIdentity.byteSize !== publication.byteSize ||
    beforeReceiptIdentity.sha256 !== publication.sha256
  )
    throw new Error("analytics published output changed before receipt creation");
  const receipt = await writeReceiptWithoutOverwrite(
    options.receiptPath,
    publication.preparedReceipt,
    options,
  );
  await persistRecoveryState(
    recoveryStatePath,
    transitionRecovery(recovery, { phase: "completed", updatedAt: timestamp(options) }),
  );
  await rm(publication.stagingPath, { force: true });
  return receipt;
}

async function publishPreparedBytes(
  stagingPath: string,
  outputPath: string,
  expectedSize: number,
  expectedSha256: string,
  beforeAtomicLink: (evidence: {
    readonly byteSize: number;
    readonly sha256: string;
  }) => Promise<void>,
): Promise<void> {
  const temporary = join(dirname(outputPath), `.${basename(outputPath)}.${randomUUID()}.publish`);
  try {
    await copyFile(stagingPath, temporary, fsConstants.COPYFILE_EXCL);
    const copied = await hashRegularFileNoFollow(temporary);
    if (copied.byteSize !== expectedSize || copied.sha256 !== expectedSha256)
      throw new Error("analytics publication copy does not match its immutable prepared stage");
    await beforeAtomicLink(copied);
    await link(temporary, outputPath);
  } finally {
    await rm(temporary, { force: true });
  }
}

async function assertPreparedStage(
  stagingPath: string,
  expectedSize: number,
  expectedSha256: string,
): Promise<void> {
  await rejectExistingSymlink(stagingPath);
  await validateWorkbookZip(stagingPath);
  const actual = await hashRegularFileNoFollow(stagingPath);
  if (actual.byteSize !== expectedSize || actual.sha256 !== expectedSha256)
    throw new Error("analytics immutable prepared stage conflicts with its checkpoint");
}

async function persistRecoveryState(
  path: string,
  state: AnalyticsRecoveryState,
): Promise<AnalyticsRecoveryState> {
  const frozen = deepFreezeRecovery(state);
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, `${JSON.stringify(frozen, null, 2)}\n`, { flag: "wx" });
    await rename(temporary, path);
  } finally {
    await rm(temporary, { force: true });
  }
  return frozen;
}

function transitionRecovery(
  state: AnalyticsRecoveryState,
  patch: Partial<AnalyticsRecoveryState> & Pick<AnalyticsRecoveryState, "phase" | "updatedAt">,
): AnalyticsRecoveryState {
  return { ...state, ...patch };
}

function deepFreezeRecovery(state: AnalyticsRecoveryState): AnalyticsRecoveryState {
  return Object.freeze({
    ...state,
    downloadRoots: Object.freeze([...state.downloadRoots]),
    exportOperation: Object.freeze({ ...state.exportOperation }),
    preConfirmEvidence: Object.freeze({ ...state.preConfirmEvidence }),
    preConfirmSnapshot: Object.freeze(
      state.preConfirmSnapshot.map((entry) => Object.freeze({ ...entry })),
    ),
    ...(state.confirmAttempt ? { confirmAttempt: Object.freeze({ ...state.confirmAttempt }) } : {}),
    ...(state.confirmEvidence
      ? { confirmEvidence: Object.freeze({ ...state.confirmEvidence }) }
      : {}),
    ...(state.reconciliation
      ? {
          reconciliation: Object.freeze({
            ...state.reconciliation,
            observedCandidates: Object.freeze(
              state.reconciliation.observedCandidates.map((entry) => Object.freeze({ ...entry })),
            ),
          }),
        }
      : {}),
    ...(state.publication
      ? {
          publication: Object.freeze({
            ...state.publication,
            downloadEvidence: Object.freeze({ ...state.publication.downloadEvidence }),
            preparedReceipt: Object.freeze({
              ...state.publication.preparedReceipt,
              downloadEvidence: Object.freeze({
                ...state.publication.preparedReceipt.downloadEvidence,
              }),
            }),
          }),
        }
      : {}),
  });
}

async function readRecoveryState(
  path: string,
  options: ExportAnalyticsOptions,
  canonicalRoots: readonly string[],
): Promise<AnalyticsRecoveryState | undefined> {
  if (!(await pathExists(path))) return undefined;
  await rejectExistingSymlink(path);
  let value: unknown;
  try {
    value = JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    throw new Error(
      `analytics recovery state is unreadable: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (!isRecord(value) || value.schemaVersion !== 1)
    throw new Error("analytics recovery state has an invalid shape");
  if (
    value.expectedAccount !== options.expectedAccount ||
    value.expectedStartDate !== options.expectedStartDate ||
    value.expectedEndDate !== options.expectedEndDate ||
    value.outputPath !== resolve(options.outputPath) ||
    value.receiptPath !== resolve(options.receiptPath)
  )
    throw new Error("analytics recovery state belongs to another request");
  if (
    !Array.isArray(value.downloadRoots) ||
    JSON.stringify(value.downloadRoots) !== JSON.stringify(canonicalRoots) ||
    !Array.isArray(value.preConfirmSnapshot)
  )
    throw new Error("analytics recovery state has invalid roots or snapshot");
  const entries = value.preConfirmSnapshot.map(parseSnapshotEntry);
  for (const entry of entries)
    if (
      !canonicalRoots.includes(entry.rootRealPath) ||
      !isContainedPath(entry.rootRealPath, entry.realPath)
    )
      throw new Error("analytics recovery snapshot escaped its download root");
  const exportOperation = parseExportOperation(value.exportOperation, options);
  const preConfirmEvidence = parsePreConfirmEvidence(value.preConfirmEvidence, exportOperation);
  if (
    preConfirmEvidence.entryCount !== entries.length ||
    preConfirmEvidence.snapshotId !==
      snapshotDigest(exportOperation.operationId, preConfirmEvidence.capturedAt, entries)
  )
    throw new Error("analytics recovery pre-confirm snapshot digest mismatch");
  const phase = parseRecoveryPhase(value.phase);
  const confirmAttempt =
    value.confirmAttempt === undefined
      ? undefined
      : parseConfirmAttempt(value.confirmAttempt, exportOperation, preConfirmEvidence);
  if (
    [
      "confirm_started",
      "confirm_completed",
      "needs_reconciliation",
      "publish_started",
      "completed",
    ].includes(phase) &&
    confirmAttempt === undefined
  )
    throw new Error("analytics recovery state is missing its confirm attempt");
  const confirmEvidence =
    value.confirmEvidence === undefined
      ? undefined
      : parseBrowserEvidence(value.confirmEvidence, exportOperation, preConfirmEvidence);
  if (phase === "confirm_completed" && confirmEvidence === undefined)
    throw new Error("analytics completed confirmation has no browser evidence");
  const reconciliation =
    value.reconciliation === undefined
      ? undefined
      : parseReconciliation(value.reconciliation, canonicalRoots);
  if (phase === "needs_reconciliation" && reconciliation === undefined)
    throw new Error("analytics recovery state is missing its reconciliation checkpoint");
  if (reconciliation?.confirmationPhase === "confirm_completed" && confirmEvidence === undefined)
    throw new Error("analytics reconciliation lost completed browser evidence");
  const publication =
    value.publication === undefined
      ? undefined
      : parsePublication(
          value.publication,
          exportOperation,
          preConfirmEvidence,
          options,
          canonicalRoots,
        );
  if (["publish_started", "completed"].includes(phase) && publication === undefined)
    throw new Error("analytics recovery state is missing its publication checkpoint");
  if (typeof value.updatedAt !== "string")
    throw new Error("analytics recovery state has invalid updatedAt");
  assertTimestamp(value.updatedAt, "recovery updatedAt");
  return deepFreezeRecovery({
    schemaVersion: 1,
    expectedAccount: options.expectedAccount,
    expectedStartDate: options.expectedStartDate,
    expectedEndDate: options.expectedEndDate,
    outputPath: resolve(options.outputPath),
    receiptPath: resolve(options.receiptPath),
    downloadRoots: canonicalRoots,
    exportOperation,
    preConfirmEvidence,
    phase,
    preConfirmSnapshot: entries,
    ...(confirmAttempt ? { confirmAttempt } : {}),
    ...(confirmEvidence ? { confirmEvidence } : {}),
    ...(reconciliation ? { reconciliation } : {}),
    ...(publication ? { publication } : {}),
    updatedAt: value.updatedAt,
  });
}

function parseExportOperation(
  value: unknown,
  options: ExportAnalyticsOptions,
): AnalyticsExportOperation {
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    !OPERATION_ID.test(String(value.operationId))
  )
    throw new Error("analytics recovery export operation is invalid");
  if (
    value.account !== options.expectedAccount ||
    value.startDate !== options.expectedStartDate ||
    value.endDate !== options.expectedEndDate ||
    value.requestedRange !== "7 days" ||
    value.resultUrl !== LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url
  )
    throw new Error("analytics recovery export operation binding is invalid");
  assertTimestamp(value.createdAt, "operation createdAt");
  return Object.freeze(value as unknown as AnalyticsExportOperation);
}

function parsePreConfirmEvidence(
  value: unknown,
  operation: AnalyticsExportOperation,
): AnalyticsPreConfirmEvidence {
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    value.operationId !== operation.operationId ||
    !SHA256.test(String(value.snapshotId)) ||
    !Number.isSafeInteger(value.entryCount) ||
    (value.entryCount as number) < 0
  )
    throw new Error("analytics recovery pre-confirm evidence is invalid");
  assertTimestamp(value.capturedAt, "pre-confirm capturedAt");
  return Object.freeze(value as unknown as AnalyticsPreConfirmEvidence);
}

function parseConfirmAttempt(
  value: unknown,
  operation: AnalyticsExportOperation,
  preConfirm: AnalyticsPreConfirmEvidence,
): AnalyticsConfirmAttempt {
  if (
    !isRecord(value) ||
    value.operationId !== operation.operationId ||
    value.preConfirmSnapshotId !== preConfirm.snapshotId
  )
    throw new Error("analytics recovery confirm attempt is invalid");
  assertTimestamp(value.startedAt, "confirm attempt startedAt");
  assertTimestamp(value.reconciliationDeadlineAt, "confirm attempt reconciliationDeadlineAt");
  if (
    Date.parse(value.reconciliationDeadlineAt) - Date.parse(value.startedAt) !==
    ANALYTICS_RECONCILIATION_WINDOW_MS
  )
    throw new Error("analytics recovery confirm attempt has an invalid operation window");
  return Object.freeze(value as unknown as AnalyticsConfirmAttempt);
}

function parseReconciliation(
  value: unknown,
  roots: readonly string[],
): AnalyticsReconciliationCheckpoint {
  if (
    !isRecord(value) ||
    !(<readonly unknown[]>["confirm_started", "confirm_completed"]).includes(
      value.confirmationPhase,
    ) ||
    !(<readonly unknown[]>[
      "no_workbook_observed",
      "multiple_changed_workbooks",
      "changed_workbook_mismatch",
      "candidate_set_changed",
      "workbook_outside_operation_window",
    ]).includes(value.reason) ||
    !Array.isArray(value.observedCandidates)
  )
    throw new Error("analytics reconciliation checkpoint is invalid");
  assertTimestamp(value.recordedAt, "reconciliation recordedAt");
  const observedCandidates = value.observedCandidates.map(parseSnapshotEntry);
  for (const candidate of observedCandidates)
    if (
      !roots.includes(candidate.rootRealPath) ||
      !isContainedPath(candidate.rootRealPath, candidate.realPath)
    )
      throw new Error("analytics reconciliation candidate escaped its download root");
  return Object.freeze({
    confirmationPhase: value.confirmationPhase,
    reason: value.reason,
    recordedAt: value.recordedAt,
    observedCandidates: Object.freeze(observedCandidates),
  }) as AnalyticsReconciliationCheckpoint;
}

function parseBrowserEvidence(
  value: unknown,
  operation: AnalyticsExportOperation,
  preConfirm: AnalyticsPreConfirmEvidence,
): AnalyticsBrowserEvidence {
  assertCommandResult(
    {
      schemaVersion: 1,
      operation: "confirm_export",
      status: "completed",
      observedUrl: operation.resultUrl,
      evidence: value,
    },
    "confirm_export",
    operation,
    preConfirm,
  );
  return Object.freeze(value as AnalyticsBrowserEvidence);
}

function parsePublication(
  value: unknown,
  operation: AnalyticsExportOperation,
  preConfirm: AnalyticsPreConfirmEvidence,
  options: ExportAnalyticsOptions,
  roots: readonly string[],
): NonNullable<AnalyticsRecoveryState["publication"]> {
  if (!isRecord(value)) throw new Error("analytics publication checkpoint is invalid");
  const stringFields = [
    "stagingPath",
    "sourcePath",
    "filename",
    "account",
    "startDate",
    "endDate",
    "sha256",
    "browserConfirmation",
    "confirmationEvidence",
    "operationId",
    "preConfirmSnapshotId",
    "resultUrl",
    "confirmationStartedAt",
    "confirmationCompletedAt",
  ] as const;
  for (const field of stringFields)
    if (typeof value[field] !== "string")
      throw new Error(`analytics publication checkpoint has invalid ${field}`);
  if (
    value.operationId !== operation.operationId ||
    value.preConfirmSnapshotId !== preConfirm.snapshotId ||
    value.resultUrl !== operation.resultUrl ||
    value.account !== options.expectedAccount ||
    value.startDate !== options.expectedStartDate ||
    value.endDate !== options.expectedEndDate ||
    !SHA256.test(value.sha256 as string) ||
    !Number.isSafeInteger(value.byteSize) ||
    (value.byteSize as number) <= 0 ||
    resolve(value.stagingPath as string) !==
      resolve(stagingPathFor(options.outputPath, operation.operationId)) ||
    roots.some((root) => isContainedPath(root, value.stagingPath as string)) ||
    !roots.some((root) => isContainedPath(root, value.sourcePath as string))
  )
    throw new Error("analytics publication checkpoint binding is invalid");
  assertTimestamp(value.confirmationStartedAt, "publication confirmationStartedAt");
  assertTimestamp(value.confirmationCompletedAt, "publication confirmationCompletedAt");
  if (
    Date.parse(value.confirmationCompletedAt) < Date.parse(value.confirmationStartedAt) ||
    !(<readonly unknown[]>["performed", "recovered_from_download"]).includes(
      value.browserConfirmation,
    ) ||
    !(<readonly unknown[]>["browser_completed", "download_reconciled"]).includes(
      value.confirmationEvidence,
    )
  )
    throw new Error("analytics publication confirmation evidence is invalid");
  const downloadEvidence = parseDownloadEvidence(
    value.downloadEvidence,
    {
      operationId: operation.operationId,
      preConfirmSnapshotId: preConfirm.snapshotId,
      resultUrl: operation.resultUrl,
      confirmationStartedAt: value.confirmationStartedAt as string,
    },
    roots,
  );
  if (value.sourcePath !== downloadEvidence.candidateRealPath)
    throw new Error("analytics publication source does not match its download evidence");
  const preparedReceipt = parsePreparedReceiptCheckpoint(
    value.preparedReceipt,
    value,
    downloadEvidence,
    options,
  );
  return Object.freeze({ ...value, downloadEvidence, preparedReceipt }) as unknown as NonNullable<
    AnalyticsRecoveryState["publication"]
  >;
}

function parsePreparedReceiptCheckpoint(
  value: unknown,
  publication: Record<string, unknown>,
  downloadEvidence: AnalyticsDownloadEvidence,
  options: ExportAnalyticsOptions,
): AnalyticsExportReceipt {
  if (!isRecord(value) || value.schemaVersion !== 1 || value.status !== "completed")
    throw new Error("analytics prepared receipt checkpoint is invalid");
  const exactBindings: Readonly<Record<string, unknown>> = {
    browserConfirmation: publication.browserConfirmation,
    confirmationEvidence: publication.confirmationEvidence,
    operationId: publication.operationId,
    preConfirmSnapshotId: publication.preConfirmSnapshotId,
    resultUrl: publication.resultUrl,
    confirmationStartedAt: publication.confirmationStartedAt,
    confirmationCompletedAt: publication.confirmationCompletedAt,
    sourcePath: publication.sourcePath,
    outputPath: options.outputPath,
    filename: publication.filename,
    account: publication.account,
    startDate: publication.startDate,
    endDate: publication.endDate,
    byteSize: publication.byteSize,
    sha256: publication.sha256,
  };
  for (const [field, expected] of Object.entries(exactBindings))
    if (value[field] !== expected)
      throw new Error(`analytics prepared receipt checkpoint mismatch for ${field}`);
  if (typeof value.receiptId !== "string" || value.receiptId.length === 0)
    throw new Error("analytics prepared receipt checkpoint has invalid receiptId");
  assertTimestamp(value.createdAt, "prepared receipt createdAt");
  const parsedEvidence = parseDownloadEvidence(value.downloadEvidence, {
    operationId: publication.operationId as string,
    preConfirmSnapshotId: publication.preConfirmSnapshotId as string,
    resultUrl: LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url,
    confirmationStartedAt: publication.confirmationStartedAt as string,
  });
  if (JSON.stringify(parsedEvidence) !== JSON.stringify(downloadEvidence))
    throw new Error("analytics prepared receipt download evidence mismatch");
  return Object.freeze({
    ...value,
    downloadEvidence: parsedEvidence,
  }) as unknown as AnalyticsExportReceipt;
}

function parseDownloadEvidence(
  value: unknown,
  binding: {
    readonly operationId: string;
    readonly preConfirmSnapshotId: string;
    readonly resultUrl: AnalyticsExportOperation["resultUrl"];
    readonly confirmationStartedAt: string;
  },
  roots?: readonly string[],
): AnalyticsDownloadEvidence {
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    value.operationId !== binding.operationId ||
    value.preConfirmSnapshotId !== binding.preConfirmSnapshotId ||
    value.resultUrl !== binding.resultUrl ||
    value.correlation !== "bounded_temporal_association" ||
    value.confirmationStartedAt !== binding.confirmationStartedAt ||
    !(<readonly unknown[]>["confirm_started", "confirm_completed"]).includes(
      value.confirmationPhase,
    ) ||
    !(<readonly unknown[]>["new", "changed"]).includes(value.baselineStatus) ||
    value.changedCandidateCount !== 1 ||
    typeof value.candidatePath !== "string" ||
    typeof value.candidateRealPath !== "string" ||
    typeof value.candidateRootRealPath !== "string" ||
    !Number.isSafeInteger(value.candidateDevice) ||
    !Number.isSafeInteger(value.candidateInode) ||
    typeof value.candidateBirthtimeMs !== "number" ||
    typeof value.candidateModifiedAtMs !== "number" ||
    !Number.isSafeInteger(value.candidateSize) ||
    (value.candidateSize as number) <= 0
  )
    throw new Error("analytics download operation evidence is invalid");
  assertTimestamp(value.confirmationStartedAt, "download evidence confirmationStartedAt");
  assertTimestamp(value.reconciliationDeadlineAt, "download evidence reconciliationDeadlineAt");
  assertTimestamp(value.observedAt, "download evidence observedAt");
  const startedAt = Date.parse(value.confirmationStartedAt);
  const deadline = Date.parse(value.reconciliationDeadlineAt);
  if (
    deadline - startedAt !== ANALYTICS_RECONCILIATION_WINDOW_MS ||
    (value.candidateModifiedAtMs as number) < startedAt ||
    (value.candidateModifiedAtMs as number) > deadline ||
    !isContainedPath(value.candidateRootRealPath, value.candidateRealPath) ||
    (roots !== undefined && !roots.includes(value.candidateRootRealPath))
  )
    throw new Error("analytics download operation evidence binding is invalid");
  return Object.freeze(value as unknown as AnalyticsDownloadEvidence);
}

function parseSnapshotEntry(value: unknown): DownloadSnapshotEntry {
  if (
    !isRecord(value) ||
    typeof value.path !== "string" ||
    typeof value.realPath !== "string" ||
    typeof value.rootRealPath !== "string" ||
    !Number.isSafeInteger(value.device) ||
    !Number.isSafeInteger(value.inode) ||
    typeof value.birthtimeMs !== "number" ||
    typeof value.size !== "number" ||
    typeof value.modifiedAtMs !== "number"
  )
    throw new Error("analytics recovery snapshot entry is invalid");
  return Object.freeze(value as unknown as DownloadSnapshotEntry);
}

function parseRecoveryPhase(value: unknown): AnalyticsRecoveryPhase {
  const phases: readonly AnalyticsRecoveryPhase[] = [
    "ready_to_confirm",
    "confirm_started",
    "confirm_completed",
    "needs_reconciliation",
    "publish_started",
    "completed",
  ];
  if (!phases.includes(value as AnalyticsRecoveryPhase))
    throw new Error("analytics recovery state has an invalid phase");
  return value as AnalyticsRecoveryPhase;
}

async function waitForStableNonzeroSize(
  path: string,
  options: ExportAnalyticsOptions,
  sleep: (milliseconds: number) => Promise<void>,
): Promise<void> {
  let previous: number | undefined;
  const maxPolls = options.maxPolls ?? 20;
  for (let poll = 0; poll < maxPolls; poll += 1) {
    let info: Stats;
    try {
      info = await lstat(path);
    } catch {
      throw new AnalyticsSourceIdentityChangedError(
        `analytics workbook candidate disappeared: ${basename(path)}`,
      );
    }
    if (info.isSymbolicLink() || !info.isFile())
      throw new AnalyticsSourceIdentityChangedError(
        `analytics workbook candidate changed type: ${basename(path)}`,
      );
    if (info.size > 0 && info.size === previous) return;
    previous = info.size;
    await sleep(options.pollIntervalMs ?? 250);
  }
  throw new Error(`analytics workbook did not reach a stable nonzero size: ${basename(path)}`);
}

async function validateCandidateAtUse(candidate: DownloadSnapshotEntry): Promise<string> {
  let current: DownloadSnapshotEntry;
  try {
    current = await readCandidateAtUse(candidate.path, candidate.rootRealPath);
  } catch {
    throw new AnalyticsSourceIdentityChangedError(
      "analytics download candidate identity changed before staging",
    );
  }
  if (
    current.realPath !== candidate.realPath ||
    current.device !== candidate.device ||
    current.inode !== candidate.inode
  )
    throw new AnalyticsSourceIdentityChangedError(
      `analytics download candidate identity changed: ${candidate.path}`,
    );
  return current.realPath;
}

async function refreshCandidateAtUse(
  candidate: DownloadSnapshotEntry,
): Promise<DownloadSnapshotEntry> {
  let current: DownloadSnapshotEntry;
  try {
    current = await readCandidateAtUse(candidate.path, candidate.rootRealPath);
  } catch {
    throw new AnalyticsSourceIdentityChangedError(
      "analytics download candidate identity changed before staging",
    );
  }
  if (
    current.realPath !== candidate.realPath ||
    current.device !== candidate.device ||
    current.inode !== candidate.inode
  )
    throw new AnalyticsSourceIdentityChangedError(
      `analytics download candidate identity changed: ${candidate.path}`,
    );
  return current;
}

async function readCandidateAtUse(
  path: string,
  rootRealPath: string,
): Promise<DownloadSnapshotEntry> {
  const info = await lstat(path);
  if (info.isSymbolicLink() || !info.isFile())
    throw new Error(`analytics download candidate must remain a regular non-symlink file: ${path}`);
  const currentRealPath = await realpath(path);
  if (!isContainedPath(rootRealPath, currentRealPath))
    throw new Error(`analytics download candidate escaped its root: ${path}`);
  return Object.freeze({
    path,
    realPath: currentRealPath,
    rootRealPath,
    device: info.dev,
    inode: info.ino,
    birthtimeMs: info.birthtimeMs,
    size: info.size,
    modifiedAtMs: info.mtimeMs,
  });
}

function createDownloadEvidence(
  options: ExportAnalyticsOptions,
  recovery: AnalyticsRecoveryState,
  observation: CandidateObservation,
  candidate: DownloadSnapshotEntry,
): AnalyticsDownloadEvidence {
  const attempt = requireConfirmAttempt(recovery);
  const start = Date.parse(attempt.startedAt);
  const deadline = Date.parse(attempt.reconciliationDeadlineAt);
  if (candidate.modifiedAtMs < start || candidate.modifiedAtMs > deadline)
    throw new Error("analytics workbook falls outside its export operation window");
  if (
    observation.confirmationPhase === "confirm_completed" &&
    (recovery.confirmEvidence === undefined ||
      candidate.modifiedAtMs < Date.parse(recovery.confirmEvidence.actionStartedAt))
  )
    throw new Error("analytics workbook predates its completed browser export operation");
  assertRequestedWorkbook(parseWorkbookFilename(candidate.path), {
    account: recovery.exportOperation.account,
    startDate: recovery.exportOperation.startDate,
    endDate: recovery.exportOperation.endDate,
  });
  return Object.freeze({
    schemaVersion: 1,
    operationId: recovery.exportOperation.operationId,
    preConfirmSnapshotId: recovery.preConfirmEvidence.snapshotId,
    resultUrl: recovery.exportOperation.resultUrl,
    correlation: "bounded_temporal_association",
    confirmationPhase: observation.confirmationPhase,
    confirmationStartedAt: attempt.startedAt,
    reconciliationDeadlineAt: attempt.reconciliationDeadlineAt,
    observedAt: timestamp(options),
    candidatePath: candidate.path,
    candidateRealPath: candidate.realPath,
    candidateRootRealPath: candidate.rootRealPath,
    candidateDevice: candidate.device,
    candidateInode: candidate.inode,
    candidateBirthtimeMs: candidate.birthtimeMs,
    candidateModifiedAtMs: candidate.modifiedAtMs,
    candidateSize: candidate.size,
    baselineStatus: observation.baselineStatus,
    changedCandidateCount: 1,
  });
}

function freezeSnapshot(
  baseline: ReadonlyMap<string, DownloadSnapshotEntry>,
): readonly DownloadSnapshotEntry[] {
  return Object.freeze(
    [...baseline.values()]
      .sort((left, right) => left.path.localeCompare(right.path))
      .map((entry) => Object.freeze({ ...entry })),
  );
}

function snapshotMap(
  entries: readonly DownloadSnapshotEntry[],
): ReadonlyMap<string, DownloadSnapshotEntry> {
  return new Map(entries.map((entry) => [entry.path, entry]));
}

function stagingPathFor(outputPath: string, operationId: string): string {
  return join(dirname(outputPath), `.${basename(outputPath)}.${operationId}.prepared`);
}

async function copyCandidateToPrivateStage(
  candidate: DownloadSnapshotEntry,
  stagingPath: string,
  options: ExportAnalyticsOptions,
): Promise<void> {
  if (candidate.size <= 0 || candidate.size > MAX_WORKBOOK_BYTES)
    throw new Error("analytics download candidate has an unsafe staging size");
  let source: Awaited<ReturnType<typeof open>>;
  try {
    source = await open(candidate.path, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  } catch {
    throw new AnalyticsSourceIdentityChangedError(
      "analytics download candidate identity changed before staging",
    );
  }
  let stage: Awaited<ReturnType<typeof open>> | undefined;
  try {
    const before = await source.stat();
    assertSourceStatMatchesCandidate(before, candidate);
    await options.afterSourceOpened?.();
    stage = await open(
      stagingPath,
      fsConstants.O_WRONLY | fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_NOFOLLOW,
      0o600,
    );
    const buffer = Buffer.allocUnsafe(64 * 1024);
    let position = 0;
    while (position < before.size) {
      const requested = Math.min(buffer.length, before.size - position);
      const { bytesRead } = await source.read(buffer, 0, requested, position);
      if (bytesRead === 0)
        throw new AnalyticsSourceIdentityChangedError(
          "analytics download candidate changed while being staged",
        );
      await writeFully(stage, buffer, bytesRead, position);
      position += bytesRead;
    }
    const extra = Buffer.alloc(1);
    if ((await source.read(extra, 0, 1, position)).bytesRead !== 0)
      throw new AnalyticsSourceIdentityChangedError(
        "analytics download candidate grew while being staged",
      );
    await stage.sync();
    const after = await source.stat();
    if (!sameFileStat(before, after))
      throw new AnalyticsSourceIdentityChangedError(
        "analytics download candidate changed while being staged",
      );
    let current: DownloadSnapshotEntry;
    try {
      current = await readCandidateAtUse(candidate.path, candidate.rootRealPath);
    } catch {
      throw new AnalyticsSourceIdentityChangedError(
        "analytics download candidate identity changed while being staged",
      );
    }
    if (!sameCandidate(current, candidate))
      throw new AnalyticsSourceIdentityChangedError(
        "analytics download candidate identity changed while being staged",
      );
  } finally {
    await stage?.close();
    await source.close();
  }
}

async function writeFully(
  file: Awaited<ReturnType<typeof open>>,
  buffer: Buffer,
  length: number,
  position: number,
): Promise<void> {
  let written = 0;
  while (written < length) {
    const result = await file.write(buffer, written, length - written, position + written);
    if (result.bytesWritten === 0) throw new Error("analytics private staging write stalled");
    written += result.bytesWritten;
  }
}

function assertSourceStatMatchesCandidate(stat: Stats, candidate: DownloadSnapshotEntry): void {
  if (
    !stat.isFile() ||
    stat.dev !== candidate.device ||
    stat.ino !== candidate.inode ||
    stat.birthtimeMs !== candidate.birthtimeMs ||
    stat.mtimeMs !== candidate.modifiedAtMs ||
    stat.size !== candidate.size
  )
    throw new AnalyticsSourceIdentityChangedError(
      "analytics download candidate identity changed before staging",
    );
}

function assertUnchangedFileStat(before: Stats, after: Stats, message: string): void {
  if (!sameFileStat(before, after)) throw new Error(message);
}

function sameFileStat(before: Stats, after: Stats): boolean {
  return (
    after.isFile() &&
    before.dev === after.dev &&
    before.ino === after.ino &&
    before.birthtimeMs === after.birthtimeMs &&
    before.mtimeMs === after.mtimeMs &&
    before.size === after.size
  );
}

function sameCandidate(left: DownloadSnapshotEntry, right: DownloadSnapshotEntry): boolean {
  return (
    left.path === right.path &&
    left.realPath === right.realPath &&
    left.rootRealPath === right.rootRealPath &&
    left.device === right.device &&
    left.inode === right.inode &&
    left.birthtimeMs === right.birthtimeMs &&
    left.modifiedAtMs === right.modifiedAtMs &&
    left.size === right.size
  );
}

function candidateMatchesDownloadEvidence(
  candidate: DownloadSnapshotEntry,
  evidence: AnalyticsDownloadEvidence,
): boolean {
  return (
    candidate.path === evidence.candidatePath &&
    candidate.realPath === evidence.candidateRealPath &&
    candidate.rootRealPath === evidence.candidateRootRealPath &&
    candidate.device === evidence.candidateDevice &&
    candidate.inode === evidence.candidateInode &&
    candidate.birthtimeMs === evidence.candidateBirthtimeMs &&
    candidate.modifiedAtMs === evidence.candidateModifiedAtMs &&
    candidate.size === evidence.candidateSize
  );
}

async function hashRegularFileNoFollow(
  path: string,
): Promise<{ readonly byteSize: number; readonly sha256: string }> {
  const file = await open(path, fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW);
  try {
    const before = await file.stat();
    if (!before.isFile()) throw new Error(`analytics artifact is not a regular file: ${path}`);
    if (before.size <= 0 || before.size > MAX_WORKBOOK_BYTES)
      throw new Error(`analytics artifact has an unsafe size: ${path}`);
    const hash = createHash("sha256");
    const buffer = Buffer.allocUnsafe(64 * 1024);
    let byteSize = 0;
    while (byteSize < before.size) {
      const requested = Math.min(buffer.length, before.size - byteSize);
      const { bytesRead } = await file.read(buffer, 0, requested, byteSize);
      if (bytesRead === 0) throw new Error(`analytics artifact changed while hashing: ${path}`);
      hash.update(buffer.subarray(0, bytesRead));
      byteSize += bytesRead;
    }
    if ((await file.read(Buffer.alloc(1), 0, 1, byteSize)).bytesRead !== 0)
      throw new Error(`analytics artifact grew while hashing: ${path}`);
    const after = await file.stat();
    assertUnchangedFileStat(before, after, `analytics artifact changed while hashing: ${path}`);
    const pathStat = await lstat(path);
    if (pathStat.isSymbolicLink() || pathStat.dev !== after.dev || pathStat.ino !== after.ino)
      throw new Error(`analytics artifact identity changed while hashing: ${path}`);
    return Object.freeze({ byteSize, sha256: hash.digest("hex") });
  } finally {
    await file.close();
  }
}

function validateRequest(options: ExportAnalyticsOptions): void {
  if (
    typeof options.expectedAccount !== "string" ||
    options.expectedAccount.trim() !== options.expectedAccount ||
    options.expectedAccount.length === 0
  )
    throw new Error("analytics expected account is required exactly");
  assertInclusiveSevenDayRange(options.expectedStartDate, options.expectedEndDate);
  for (const path of [
    options.outputPath,
    options.receiptPath,
    ...(options.recoveryStatePath === undefined ? [] : [options.recoveryStatePath]),
  ])
    if (!isAbsolute(path)) throw new Error(`analytics artifact path must be absolute: ${path}`);
}

function expectedWorkbook(options: ExportAnalyticsOptions) {
  return {
    account: options.expectedAccount,
    startDate: options.expectedStartDate,
    endDate: options.expectedEndDate,
  } as const;
}

function timestamp(options: ExportAnalyticsOptions): string {
  return (options.now?.() ?? new Date()).toISOString();
}

function assertTimestamp(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || new Date(value).toISOString() !== value)
    throw new Error(`analytics ${field} must be ISO 8601 UTC`);
}

async function canonicalProspectivePath(path: string): Promise<string> {
  const pending: string[] = [basename(path)];
  let ancestor = dirname(path);
  while (!(await pathExists(ancestor))) {
    const parent = dirname(ancestor);
    if (parent === ancestor)
      throw new Error(`analytics artifact path has no existing ancestor: ${path}`);
    pending.unshift(basename(ancestor));
    ancestor = parent;
  }
  return join(await realpath(ancestor), ...pending);
}

async function rejectExistingSymlink(path: string): Promise<void> {
  try {
    if ((await lstat(path)).isSymbolicLink())
      throw new Error(`analytics artifact path must not be a symlink: ${path}`);
  } catch (error) {
    if (!isMissing(error)) throw error;
  }
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (isMissing(error)) return false;
    throw error;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMissing(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

function isAlreadyExists(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "EEXIST";
}
