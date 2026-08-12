import { describe, expect, test } from "bun:test";
import {
  commandConfig,
  LINKEDIN_CONTENT_ANALYTICS_CONTRACT,
} from "../../src/analytics/contract.ts";
import { parseAnalyticsCommandOutput } from "../../src/analytics/playwriter.ts";
import type {
  AnalyticsBrowserEvidence,
  AnalyticsCommandConfig,
  AnalyticsExportOperation,
  AnalyticsPreConfirmEvidence,
} from "../../src/analytics/types.ts";
import {
  compileAnalyticsScript,
  isControlledCompiledScript,
} from "../../src/playwriter/scripts.ts";

const operation: AnalyticsExportOperation = Object.freeze({
  schemaVersion: 1,
  operationId: "analytics_compiler_test",
  account: "Hanif",
  startDate: "2026-07-20",
  endDate: "2026-07-26",
  requestedRange: "7 days",
  resultUrl: LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url,
  createdAt: "2026-08-03T12:00:00.000Z",
});

const snapshot: AnalyticsPreConfirmEvidence = Object.freeze({
  schemaVersion: 1,
  operationId: operation.operationId,
  snapshotId: "a".repeat(64),
  capturedAt: "2026-08-03T12:00:01.000Z",
  entryCount: 0,
});

function browserEvidence(config: AnalyticsCommandConfig): AnalyticsBrowserEvidence {
  return {
    operationId: operation.operationId,
    account: operation.account,
    startDate: operation.startDate,
    endDate: operation.endDate,
    requestedRange: "7 days",
    resultUrl: operation.resultUrl,
    actionStartedAt: "2026-08-03T12:00:02.000Z",
    actionCompletedAt: "2026-08-03T12:00:03.000Z",
    ...(config.operation === "confirm_export"
      ? {
          preConfirmSnapshotId: snapshot.snapshotId,
          confirmationTextVisibleCount: 1,
          confirmButtonVisibleCount: 1,
        }
      : {}),
  };
}

describe("analytics Playwriter compiler", () => {
  test("compiles all split operations through the central controlled compiler", () => {
    const configs = [
      commandConfig("navigate", operation),
      commandConfig("open_export", operation),
      commandConfig("observe_dialog", operation),
      commandConfig("confirm_export", operation, snapshot),
    ] as const;
    for (const config of configs) {
      const descriptor = compileAnalyticsScript(config);
      expect(isControlledCompiledScript(descriptor)).toBeTrue();
      expect(Object.isFrozen(descriptor)).toBeTrue();
      expect(Object.isFrozen(descriptor.phases)).toBeTrue();
      expect(descriptor.definitionId).toBe(`analytics.${descriptor.command}.v1`);
      expect(descriptor.source).toContain(LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url);
      expect(descriptor.source).toContain('"exact":true');
      expect(descriptor.source).toContain("schemaVersion:1");
      expect(descriptor.source).not.toMatch(
        /@ts-nocheck|from\s+["']playwright|require\(["']playwright/i,
      );
    }
  });

  test("revalidates URL, exact confirmation text, and one Confirm immediately before click", () => {
    const descriptor = compileAnalyticsScript(commandConfig("confirm_export", operation, snapshot));
    expect(descriptor.action).toBe("analytics_export");
    expect(descriptor.phases).toEqual([
      "observation_before",
      "analytics_confirm_started",
      "analytics_confirm_returned",
      "observation_after",
      "logs_captured",
    ]);
    const source = descriptor.source;
    const exactMessage = source.lastIndexOf(
      `getByText(c.contract.confirmationText.name,{exact:true})`,
    );
    const exactButton = source.lastIndexOf(
      `getByRole(c.contract.confirmButton.role,{name:c.contract.confirmButton.name,exact:true})`,
    );
    const counts = source.lastIndexOf(
      `if(confirmationTextVisibleCount!==1||confirmButtonVisibleCount!==1)`,
    );
    const boundary = source.indexOf(`await __progress("analytics_confirm_started")`);
    const url = source.lastIndexOf(`if(p.url()!==c.contract.url)`, boundary);
    const click = source.indexOf(`await confirmButton.click()`);
    expect(exactMessage).toBeGreaterThan(0);
    expect(exactButton).toBeGreaterThan(exactMessage);
    expect(counts).toBeGreaterThan(exactButton);
    expect(url).toBeGreaterThan(counts);
    expect(boundary).toBeGreaterThan(url);
    expect(click).toBeGreaterThan(boundary);
  });

  test("rejects changed contracts and Confirm without snapshot evidence", () => {
    expect(() => compileAnalyticsScript(commandConfig("confirm_export", operation))).toThrow(
      "snapshot evidence",
    );
    const changed = {
      ...commandConfig("navigate", operation),
      contract: {
        ...LINKEDIN_CONTENT_ANALYTICS_CONTRACT,
        exportLink: { role: "link", name: "Export data", exact: true },
      },
    } as unknown as AnalyticsCommandConfig;
    expect(() => compileAnalyticsScript(changed)).toThrow("browser contract");
  });

  test("parses only the exact central result envelope and fully validates its data", () => {
    const config = commandConfig("confirm_export", operation, snapshot);
    const data = {
      schemaVersion: 1,
      operation: config.operation,
      status: "completed",
      observedUrl: LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url,
      evidence: browserEvidence(config),
    } as const;
    const envelope = {
      schemaVersion: 1,
      command: "analytics-confirm-export",
      ok: true,
      data,
      logs: [],
    };
    expect(parseAnalyticsCommandOutput(JSON.stringify(envelope), config)).toEqual(data);
    expect(() =>
      parseAnalyticsCommandOutput(
        JSON.stringify({ ...envelope, command: "analytics-observe-dialog" }),
        config,
      ),
    ).toThrow("invalid envelope");
    expect(() =>
      parseAnalyticsCommandOutput(
        JSON.stringify({
          ...envelope,
          data: {
            ...data,
            evidence: { ...data.evidence, preConfirmSnapshotId: "b".repeat(64) },
          },
        }),
        config,
      ),
    ).toThrow("snapshot evidence mismatch");
  });
});
