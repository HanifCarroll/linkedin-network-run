import { join } from "node:path";
import { exportContentAnalytics } from "../analytics/exporter.ts";
import { PlaywriterAnalyticsRunner } from "../analytics/playwriter.ts";
import {
  type AnalyticsCommandRunner,
  type AnalyticsExportReceipt,
  AnalyticsNeedsReconciliationError,
  type ExportAnalyticsOptions,
} from "../analytics/types.ts";
import { CliError } from "../core/errors.ts";
import { PlaywriterClient } from "../playwriter/client.ts";
import { resolvePlaywriterSession, type SessionResolutionRequest } from "./sessions.ts";
import type { AnalyticsExportInput } from "./types.ts";

type ResolvedAnalyticsExportInput = Omit<AnalyticsExportInput, "sessionId"> & {
  readonly sessionId: number;
};

export type AnalyticsDependencies = {
  readonly createRunner: (input: ResolvedAnalyticsExportInput) => AnalyticsCommandRunner;
  readonly resolveSession?: (request: SessionResolutionRequest) => Promise<number>;
  readonly exportContentAnalytics: (
    options: ExportAnalyticsOptions,
  ) => Promise<AnalyticsExportReceipt>;
};

const defaultDependencies: AnalyticsDependencies = {
  createRunner: (input) =>
    new PlaywriterAnalyticsRunner(
      new PlaywriterClient({
        executable: input.playwriterBin,
        invocationRoot: join(input.stateDir, "receipts", "playwriter", "analytics"),
        stateDir: input.stateDir,
      }),
      input.sessionId,
    ),
  resolveSession: resolvePlaywriterSession,
  exportContentAnalytics,
};

export async function analyticsExport(
  input: AnalyticsExportInput,
  dependencies: AnalyticsDependencies = defaultDependencies,
): Promise<unknown> {
  try {
    const sessionId = await (dependencies.resolveSession ?? resolvePlaywriterSession)({
      workflow: "analytics",
      selection: input.sessionId,
      stateDir: input.stateDir,
      playwriterBin: input.playwriterBin,
    });
    const resolvedInput = { ...input, sessionId };
    const receipt = await dependencies.exportContentAnalytics({
      runner: dependencies.createRunner(resolvedInput),
      downloadRoots: input.downloadRoots,
      outputPath: input.outputPath,
      receiptPath: input.receiptPath,
      expectedAccount: input.expectedAccount,
      expectedStartDate: input.expectedStartDate,
      expectedEndDate: input.expectedEndDate,
      ...(input.recoveryStatePath === undefined
        ? {}
        : { recoveryStatePath: input.recoveryStatePath }),
      ...(input.pollIntervalMs === undefined ? {} : { pollIntervalMs: input.pollIntervalMs }),
      ...(input.maxPolls === undefined ? {} : { maxPolls: input.maxPolls }),
    });
    return {
      command: "analytics export",
      status: "completed",
      outputPath: receipt.outputPath,
      receiptPath: input.receiptPath,
      recoveryStatePath: input.recoveryStatePath ?? `${input.receiptPath}.state.json`,
      account: receipt.account,
      startDate: receipt.startDate,
      endDate: receipt.endDate,
      byteSize: receipt.byteSize,
      sha256: receipt.sha256,
      receipt,
    };
  } catch (error) {
    if (error instanceof AnalyticsNeedsReconciliationError) {
      throw new CliError(error.code, error.message, {
        details: {
          operationId: error.operationId,
          phase: error.phase,
          preConfirmSnapshotId: error.preConfirmSnapshotId,
          reason: error.reason,
        },
        exitCode: 5,
      });
    }
    if (error instanceof CliError) throw error;
    throw new CliError(
      "ANALYTICS_EXPORT_FAILED",
      error instanceof Error ? error.message : String(error),
      { exitCode: 5 },
    );
  }
}
