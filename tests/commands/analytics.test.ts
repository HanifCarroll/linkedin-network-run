import { describe, expect, test } from "bun:test";
import type { AnalyticsCommandRunner, ExportAnalyticsOptions } from "../../src/analytics/types.ts";
import { analyticsExport } from "../../src/commands/analytics.ts";

describe("analytics command adapter", () => {
  test("maps every current public export option without browser work", async () => {
    let received: ExportAnalyticsOptions | undefined;
    const runner: AnalyticsCommandRunner = {
      execute: async () => {
        throw new Error("fake runner must not execute");
      },
    };
    const result = await analyticsExport(
      {
        stateDir: "/tmp/state",
        playwriterBin: "/tmp/playwriter",
        sessionId: 9,
        downloadRoots: ["/tmp/downloads-a", "/tmp/downloads-b"],
        outputPath: "/tmp/out.xlsx",
        receiptPath: "/tmp/receipt.json",
        recoveryStatePath: "/tmp/recovery.json",
        expectedAccount: "Hanif",
        expectedStartDate: "2026-07-27",
        expectedEndDate: "2026-08-02",
        pollIntervalMs: 500,
        maxPolls: 20,
      },
      {
        createRunner: () => runner,
        exportContentAnalytics: async (options) => {
          received = options;
          return {
            schemaVersion: 1,
            receiptId: "receipt_test",
            status: "completed",
            createdAt: "2026-08-03T12:00:00Z",
            browserConfirmation: "performed",
            confirmationEvidence: "browser_completed",
            operationId: "operation_test",
            preConfirmSnapshotId: "snapshot_test",
            resultUrl: "https://www.linkedin.com/analytics/creator/content/",
            confirmationStartedAt: "2026-08-03T12:00:00Z",
            confirmationCompletedAt: "2026-08-03T12:00:01Z",
            downloadEvidence: {} as never,
            sourcePath: "/tmp/downloads-a/source.xlsx",
            outputPath: options.outputPath,
            filename: "AggregateAnalytics_Hanif_2026-07-27_2026-08-02.xlsx",
            account: options.expectedAccount,
            startDate: options.expectedStartDate,
            endDate: options.expectedEndDate,
            byteSize: 10,
            sha256: "a".repeat(64),
          };
        },
      },
    );
    expect(received).toMatchObject({
      runner,
      downloadRoots: ["/tmp/downloads-a", "/tmp/downloads-b"],
      outputPath: "/tmp/out.xlsx",
      receiptPath: "/tmp/receipt.json",
      recoveryStatePath: "/tmp/recovery.json",
      expectedAccount: "Hanif",
      expectedStartDate: "2026-07-27",
      expectedEndDate: "2026-08-02",
      pollIntervalMs: 500,
      maxPolls: 20,
    });
    expect(result).toMatchObject({
      command: "analytics export",
      status: "completed",
      outputPath: "/tmp/out.xlsx",
    });
  });
});
