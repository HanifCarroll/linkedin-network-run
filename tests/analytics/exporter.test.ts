import { describe, expect, test } from "bun:test";
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { LINKEDIN_CONTENT_ANALYTICS_CONTRACT } from "../../src/analytics/contract.ts";
import { exportContentAnalytics } from "../../src/analytics/exporter.ts";
import type {
  AnalyticsBrowserEvidence,
  AnalyticsCommandConfig,
  AnalyticsCommandResult,
  AnalyticsCommandRunner,
} from "../../src/analytics/types.ts";
import { writeWorkbook } from "./helpers.ts";

interface ResultOptions {
  readonly observedUrl?: string;
  readonly evidence?: Partial<AnalyticsBrowserEvidence>;
}

function completedResult(
  config: AnalyticsCommandConfig,
  actionStartedAt: string,
  options: ResultOptions = {},
): AnalyticsCommandResult {
  const operation = config.exportOperation;
  return {
    schemaVersion: 1,
    operation: config.operation,
    status: "completed",
    observedUrl: options.observedUrl ?? config.contract.url,
    evidence: {
      operationId: operation.operationId,
      account: operation.account,
      startDate: operation.startDate,
      endDate: operation.endDate,
      requestedRange: operation.requestedRange,
      resultUrl: operation.resultUrl,
      actionStartedAt,
      actionCompletedAt: new Date().toISOString(),
      ...(config.operation === "confirm_export"
        ? {
            preConfirmSnapshotId: config.preConfirmEvidence?.snapshotId ?? "missing",
            confirmationTextVisibleCount: 1,
            confirmButtonVisibleCount: 1,
          }
        : {}),
      ...options.evidence,
    },
  };
}

class FakeRunner implements AnalyticsCommandRunner {
  readonly calls: AnalyticsCommandConfig[] = [];

  constructor(
    private readonly onConfirm?: (config: AnalyticsCommandConfig) => Promise<void>,
    private readonly resultOptions?: (config: AnalyticsCommandConfig) => ResultOptions | undefined,
  ) {}

  async execute(config: AnalyticsCommandConfig): Promise<AnalyticsCommandResult> {
    this.calls.push(config);
    const actionStartedAt = new Date().toISOString();
    if (config.operation === "confirm_export") await this.onConfirm?.(config);
    return completedResult(config, actionStartedAt, this.resultOptions?.(config) ?? {});
  }
}

const expected = {
  expectedAccount: "Hanif",
  expectedStartDate: "2026-07-20",
  expectedEndDate: "2026-07-26",
} as const;

describe("content analytics export", () => {
  test("binds the exact browser operation, download evidence, workbook, and immutable receipt", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-export-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const native = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    const runner = new FakeRunner(async () => writeWorkbook(native));
    const output = join(root, "out", "analytics.xlsx");
    const receiptPath = join(root, "receipts", "receipt.json");
    const receipt = await exportContentAnalytics({
      runner,
      downloadRoots: [downloads],
      outputPath: output,
      receiptPath,
      ...expected,
      pollIntervalMs: 0,
      maxPolls: 5,
      sleep: async () => {},
      createReceiptId: () => "analytics_test",
      createOperationId: () => "analytics_testoperation",
    });
    expect(runner.calls.map((call) => call.operation)).toEqual([
      "navigate",
      "open_export",
      "observe_dialog",
      "confirm_export",
    ]);
    expect(runner.calls[0]?.contract).toEqual(LINKEDIN_CONTENT_ANALYTICS_CONTRACT);
    expect(Object.isFrozen(receipt)).toBeTrue();
    expect(Object.isFrozen(receipt.downloadEvidence)).toBeTrue();
    expect(receipt).toMatchObject({
      browserConfirmation: "performed",
      confirmationEvidence: "browser_completed",
      operationId: "analytics_testoperation",
      account: "Hanif",
      byteSize: expect.any(Number),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
      downloadEvidence: {
        operationId: "analytics_testoperation",
        resultUrl: LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url,
        correlation: "bounded_temporal_association",
        changedCandidateCount: 1,
        baselineStatus: "new",
      },
    });
    expect(await readFile(output)).toEqual(await readFile(native));
    expect(JSON.parse(await readFile(receiptPath, "utf8"))).toEqual(receipt);
  });

  test("persists uncertainty, never reconfirms after a crash, and later reconciles the operation", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-process-recovery-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const native = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    const outputPath = join(root, "out.xlsx");
    const receiptPath = join(root, "receipt.json");
    const recoveryStatePath = `${receiptPath}.state.json`;
    let confirmCount = 0;
    const first = new FakeRunner(async () => {
      confirmCount += 1;
      throw new Error("process crashed after Confirm began");
    });
    await expect(
      exportContentAnalytics({
        runner: first,
        downloadRoots: [downloads],
        outputPath,
        receiptPath,
        ...expected,
        maxPolls: 1,
        sleep: async () => {},
      }),
    ).rejects.toThrow("process crashed");
    expect(confirmCount).toBe(1);
    expect(JSON.parse(await readFile(recoveryStatePath, "utf8")).phase).toBe("confirm_started");

    const second = new FakeRunner();
    await expect(
      exportContentAnalytics({
        runner: second,
        downloadRoots: [downloads],
        outputPath,
        receiptPath,
        ...expected,
        maxPolls: 2,
        sleep: async () => {},
      }),
    ).rejects.toMatchObject({
      code: "ANALYTICS_NEEDS_RECONCILIATION",
      phase: "confirm_started",
      reason: "no_workbook_observed",
    });
    expect(second.calls).toHaveLength(0);
    expect(confirmCount).toBe(1);
    expect(JSON.parse(await readFile(recoveryStatePath, "utf8")).phase).toBe(
      "needs_reconciliation",
    );

    await writeWorkbook(native);
    const third = new FakeRunner();
    const receipt = await exportContentAnalytics({
      runner: third,
      downloadRoots: [downloads],
      outputPath,
      receiptPath,
      ...expected,
      maxPolls: 2,
      sleep: async () => {},
    });
    expect(receipt.browserConfirmation).toBe("recovered_from_download");
    expect(receipt.confirmationEvidence).toBe("download_reconciled");
    expect(receipt.downloadEvidence.confirmationPhase).toBe("confirm_started");
    expect(third.calls).toHaveLength(0);
    expect(confirmCount).toBe(1);
  });

  test("does not let an unrelated changed workbook satisfy or hide the operation", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-unrelated-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const wrong = join(downloads, "AggregateAnalytics_Other_2026-07-20_2026-07-26.xlsx");
    const matching = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    let confirmCount = 0;
    const first = new FakeRunner(async () => {
      confirmCount += 1;
      await writeWorkbook(wrong);
    });
    const options = {
      downloadRoots: [downloads],
      outputPath: join(root, "out.xlsx"),
      receiptPath: join(root, "receipt.json"),
      ...expected,
      maxPolls: 1,
      sleep: async () => {},
    } as const;
    await expect(exportContentAnalytics({ runner: first, ...options })).rejects.toMatchObject({
      code: "ANALYTICS_NEEDS_RECONCILIATION",
      reason: "changed_workbook_mismatch",
    });
    await writeWorkbook(matching);
    const second = new FakeRunner();
    await expect(exportContentAnalytics({ runner: second, ...options })).rejects.toMatchObject({
      code: "ANALYTICS_NEEDS_RECONCILIATION",
      reason: "changed_workbook_mismatch",
    });
    expect(second.calls).toHaveLength(0);
    expect(confirmCount).toBe(1);
  });

  test("rejects multiple changed workbooks as an ambiguous operation", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-multiple-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const runner = new FakeRunner(async () => {
      await writeWorkbook(join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx"));
      await writeWorkbook(
        join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26 (1).xlsx"),
      );
    });
    await expect(
      exportContentAnalytics({
        runner,
        downloadRoots: [downloads],
        outputPath: join(root, "out.xlsx"),
        receiptPath: join(root, "receipt.json"),
        ...expected,
        maxPolls: 2,
        sleep: async () => {},
      }),
    ).rejects.toMatchObject({ reason: "multiple_changed_workbooks" });
  });

  test("makes a candidate arriving after publication-temp hashing sticky before the atomic link", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-late-contamination-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const matching = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    const contaminant = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26 (1).xlsx");
    const receiptPath = join(root, "receipt.json");
    const outputPath = join(root, "out.xlsx");
    const options = {
      downloadRoots: [downloads],
      outputPath,
      receiptPath,
      ...expected,
      sleep: async () => {},
    } as const;
    const first = new FakeRunner(async () => writeWorkbook(matching));
    let finalWindowEntered = false;
    let verifiedTemp: { readonly byteSize: number; readonly sha256: string } | undefined;
    await expect(
      exportContentAnalytics({
        runner: first,
        ...options,
        afterPublicationTempVerified: async (evidence) => {
          finalWindowEntered = true;
          verifiedTemp = evidence;
          await writeWorkbook(contaminant);
        },
      }),
    ).rejects.toMatchObject({
      code: "ANALYTICS_NEEDS_RECONCILIATION",
      reason: "multiple_changed_workbooks",
    });
    expect(finalWindowEntered).toBeTrue();
    expect(verifiedTemp).toMatchObject({
      byteSize: expect.any(Number),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    });
    await expect(readFile(outputPath)).rejects.toThrow();
    const checkpoint = JSON.parse(await readFile(`${receiptPath}.state.json`, "utf8"));
    expect(checkpoint.phase).toBe("needs_reconciliation");
    expect(checkpoint.reconciliation.observedCandidates).toHaveLength(2);

    await rm(contaminant);
    const second = new FakeRunner();
    await expect(exportContentAnalytics({ runner: second, ...options })).rejects.toMatchObject({
      code: "ANALYTICS_NEEDS_RECONCILIATION",
      reason: "multiple_changed_workbooks",
    });
    expect(second.calls).toHaveLength(0);
  });

  test("terminates with explicit reconciliation state when no workbook appears", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-zero-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const runner = new FakeRunner();
    const receiptPath = join(root, "receipt.json");
    await expect(
      exportContentAnalytics({
        runner,
        downloadRoots: [downloads],
        outputPath: join(root, "out.xlsx"),
        receiptPath,
        ...expected,
        maxPolls: 2,
        sleep: async () => {},
      }),
    ).rejects.toMatchObject({
      code: "ANALYTICS_NEEDS_RECONCILIATION",
      reason: "no_workbook_observed",
    });
    expect(JSON.parse(await readFile(`${receiptPath}.state.json`, "utf8")).phase).toBe(
      "needs_reconciliation",
    );
  });

  test("waits for a growing workbook to stabilize without losing file identity", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-growing-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const native = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    let sleeps = 0;
    const runner = new FakeRunner(async () => {
      await Bun.write(native, "growing");
    });
    await exportContentAnalytics({
      runner,
      downloadRoots: [downloads],
      outputPath: join(root, "out.xlsx"),
      receiptPath: join(root, "receipt.json"),
      ...expected,
      maxPolls: 6,
      sleep: async () => {
        sleeps += 1;
        if (sleeps === 1) await writeWorkbook(native);
      },
    });
    expect(sleeps).toBeGreaterThanOrEqual(2);
  });

  test("recovers output published before its receipt and never overwrites it", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-output-recovery-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const native = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    const outputPath = join(root, "out.xlsx");
    const receiptPath = join(root, "receipt.json");
    const first = new FakeRunner(async () => writeWorkbook(native));
    await expect(
      exportContentAnalytics({
        runner: first,
        downloadRoots: [downloads],
        outputPath,
        receiptPath,
        ...expected,
        sleep: async () => {},
        createReceiptId: () => "analytics_prepared_receipt",
        afterOutputPublished: () => {
          throw new Error("crash before receipt");
        },
      }),
    ).rejects.toThrow("crash before receipt");
    const bytes = await readFile(outputPath);
    const recovery = JSON.parse(await readFile(`${receiptPath}.state.json`, "utf8"));
    const preparedReceipt = recovery.publication.preparedReceipt;
    expect(recovery.phase).toBe("publish_started");
    const second = new FakeRunner();
    const receipt = await exportContentAnalytics({
      runner: second,
      downloadRoots: [downloads],
      outputPath,
      receiptPath,
      ...expected,
      createReceiptId: () => {
        throw new Error("receipt identity must not be regenerated");
      },
    });
    expect(receipt).toEqual(preparedReceipt);
    expect(JSON.parse(await readFile(receiptPath, "utf8"))).toEqual(preparedReceipt);
    expect(receipt.browserConfirmation).toBe("performed");
    expect(await readFile(outputPath)).toEqual(bytes);
    expect(second.calls).toHaveLength(0);
  });

  test("verifies the independently published bytes again before writing a receipt", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-output-tamper-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const native = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    const outputPath = join(root, "out.xlsx");
    const receiptPath = join(root, "receipt.json");
    const runner = new FakeRunner(async () => writeWorkbook(native));
    await expect(
      exportContentAnalytics({
        runner,
        downloadRoots: [downloads],
        outputPath,
        receiptPath,
        ...expected,
        sleep: async () => {},
        afterOutputPublished: async () => writeFile(outputPath, "tampered"),
      }),
    ).rejects.toThrow("changed before receipt creation");
    await expect(readFile(receiptPath)).rejects.toThrow();
    const recovery = JSON.parse(await readFile(`${receiptPath}.state.json`, "utf8"));
    expect(recovery.phase).toBe("publish_started");
    await expect(readFile(recovery.publication.stagingPath)).resolves.not.toEqual(
      Buffer.from("tampered"),
    );
  });

  test("fails closed when the selected source path is swapped after its no-follow open", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-source-swap-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const native = join(downloads, "AggregateAnalytics_Hanif_2026-07-20_2026-07-26.xlsx");
    const displaced = `${native}.displaced`;
    const receiptPath = join(root, "receipt.json");
    const outputPath = join(root, "out.xlsx");
    const runner = new FakeRunner(async () => writeWorkbook(native));
    await expect(
      exportContentAnalytics({
        runner,
        downloadRoots: [downloads],
        outputPath,
        receiptPath,
        ...expected,
        sleep: async () => {},
        afterSourceOpened: async () => {
          await rename(native, displaced);
          await writeWorkbook(native, undefined, {
            payload: (member) => Buffer.from(`<replacement>${member}</replacement>`),
          });
        },
      }),
    ).rejects.toThrow("identity changed while being staged");
    expect(JSON.parse(await readFile(`${receiptPath}.state.json`, "utf8"))).toMatchObject({
      phase: "needs_reconciliation",
      reconciliation: { reason: "candidate_set_changed" },
    });
    await expect(readFile(outputPath)).rejects.toThrow();
  });

  test("rejects output and receipt publication conflicts before browser work", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-conflict-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const outputPath = join(root, "out.xlsx");
    await writeWorkbook(outputPath);
    const runner = new FakeRunner();
    await expect(
      exportContentAnalytics({
        runner,
        downloadRoots: [downloads],
        outputPath,
        receiptPath: join(root, "receipt.json"),
        ...expected,
      }),
    ).rejects.toThrow("without a matching publication checkpoint");
    expect(runner.calls).toHaveLength(0);

    const other = await mkdtemp(join(tmpdir(), "analytics-receipt-conflict-"));
    const otherDownloads = join(other, "downloads");
    await mkdir(otherDownloads);
    await writeFile(join(other, "receipt.json"), "{}");
    const otherRunner = new FakeRunner();
    await expect(
      exportContentAnalytics({
        runner: otherRunner,
        downloadRoots: [otherDownloads],
        outputPath: join(other, "out.xlsx"),
        receiptPath: join(other, "receipt.json"),
        ...expected,
      }),
    ).rejects.toThrow("receipt exists without");
    expect(otherRunner.calls).toHaveLength(0);
  });

  test("requires output, receipt, and recovery state outside download roots", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-artifact-containment-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const runner = new FakeRunner();
    await expect(
      exportContentAnalytics({
        runner,
        downloadRoots: [downloads],
        outputPath: join(downloads, "out.xlsx"),
        receiptPath: join(root, "receipt.json"),
        ...expected,
      }),
    ).rejects.toThrow("outside download roots");
    expect(runner.calls).toHaveLength(0);
  });

  test("rejects wrong account, period, result page, duplicate Confirm, and malformed results", async () => {
    for (const filename of [
      "AggregateAnalytics_Other_2026-07-20_2026-07-26.xlsx",
      "AggregateAnalytics_Hanif_2026-07-21_2026-07-27.xlsx",
    ]) {
      const root = await mkdtemp(join(tmpdir(), "analytics-wrong-workbook-"));
      const downloads = join(root, "downloads");
      await mkdir(downloads);
      const runner = new FakeRunner(async () => writeWorkbook(join(downloads, filename)));
      await expect(
        exportContentAnalytics({
          runner,
          downloadRoots: [downloads],
          outputPath: join(root, "out.xlsx"),
          receiptPath: join(root, "receipt.json"),
          ...expected,
          maxPolls: 1,
          sleep: async () => {},
        }),
      ).rejects.toMatchObject({ reason: "changed_workbook_mismatch" });
    }

    const root = await mkdtemp(join(tmpdir(), "analytics-result-contract-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const wrongUrl = new FakeRunner(undefined, () => ({
      observedUrl: "https://www.linkedin.com/feed/",
    }));
    await expect(
      exportContentAnalytics({
        runner: wrongUrl,
        downloadRoots: [downloads],
        outputPath: join(root, "url.xlsx"),
        receiptPath: join(root, "url.json"),
        ...expected,
      }),
    ).rejects.toThrow("wrong URL");

    const duplicate = new FakeRunner(undefined, (config) =>
      config.operation === "confirm_export"
        ? { evidence: { confirmButtonVisibleCount: 2 } }
        : undefined,
    );
    await expect(
      exportContentAnalytics({
        runner: duplicate,
        downloadRoots: [downloads],
        outputPath: join(root, "duplicate.xlsx"),
        receiptPath: join(root, "duplicate.json"),
        ...expected,
        maxPolls: 1,
        sleep: async () => {},
      }),
    ).rejects.toThrow("one visible");

    const wrongOperation = new FakeRunner(undefined, () => ({
      evidence: { operationId: "analytics_unrelated" },
    }));
    await expect(
      exportContentAnalytics({
        runner: wrongOperation,
        downloadRoots: [downloads],
        outputPath: join(root, "operation.xlsx"),
        receiptPath: join(root, "operation.json"),
        ...expected,
      }),
    ).rejects.toThrow("evidence mismatch for operationId");

    const invalidShape: AnalyticsCommandRunner = {
      async execute(config) {
        return {
          operation: config.operation,
          status: "completed",
          observedUrl: config.contract.url,
        } as never;
      },
    };
    await expect(
      exportContentAnalytics({
        runner: invalidShape,
        downloadRoots: [downloads],
        outputPath: join(root, "shape.xlsx"),
        receiptPath: join(root, "shape.json"),
        ...expected,
      }),
    ).rejects.toThrow("schemaVersion");
  });

  test("rejects an invalid requested period before any browser command", async () => {
    const root = await mkdtemp(join(tmpdir(), "analytics-request-period-"));
    const downloads = join(root, "downloads");
    await mkdir(downloads);
    const runner = new FakeRunner();
    await expect(
      exportContentAnalytics({
        runner,
        downloadRoots: [downloads],
        outputPath: join(root, "out.xlsx"),
        receiptPath: join(root, "receipt.json"),
        expectedAccount: "Hanif",
        expectedStartDate: "2026-07-20",
        expectedEndDate: "2026-07-27",
      }),
    ).rejects.toThrow("seven inclusive days");
    expect(runner.calls).toHaveLength(0);
  });
});
