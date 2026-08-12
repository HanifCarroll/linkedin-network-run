import type { PlaywriterClient } from "../playwriter/client.ts";
import { compileAnalyticsScript } from "../playwriter/scripts.ts";
import { assertCommandResult } from "./exporter.ts";
import { ANALYTICS_COMMAND_BY_OPERATION } from "./playwriter-scripts/templates.ts";
import type {
  AnalyticsCommandConfig,
  AnalyticsCommandResult,
  AnalyticsCommandRunner,
} from "./types.ts";

export class PlaywriterAnalyticsRunner implements AnalyticsCommandRunner {
  constructor(
    private readonly client: PlaywriterClient,
    private readonly sessionId: number,
  ) {
    if (!Number.isSafeInteger(sessionId) || sessionId < 1)
      throw new TypeError("invalid analytics Playwriter sessionId");
  }

  async execute(config: AnalyticsCommandConfig): Promise<AnalyticsCommandResult> {
    const descriptor = compileAnalyticsScript(config);
    const invocation = await this.client.invoke({
      sessionId: this.sessionId,
      descriptor,
      input: { analytics: config },
    });
    if (invocation.receipt.outcome !== "succeeded") {
      throw new Error(
        `analytics Playwriter command ${config.operation} failed: ${invocation.receipt.outcome}`,
      );
    }
    const result = parseAnalyticsCommandEnvelope(invocation.receipt.result, config);
    assertCommandResult(
      result,
      config.operation,
      config.exportOperation,
      config.preConfirmEvidence,
    );
    return result;
  }
}

export function parseAnalyticsCommandOutput(
  stdout: string,
  config: AnalyticsCommandConfig,
): AnalyticsCommandResult {
  const lines = stdout.split(/\r?\n/).filter((line) => line.length > 0);
  if (lines.length !== 1)
    throw new Error(`analytics Playwriter command ${config.operation} returned invalid stdout`);
  let value: unknown;
  try {
    value = JSON.parse(lines[0] ?? "");
  } catch {
    throw new Error(`analytics Playwriter command ${config.operation} returned invalid JSON`);
  }
  const result = parseAnalyticsCommandEnvelope(value, config);
  assertCommandResult(result, config.operation, config.exportOperation, config.preConfirmEvidence);
  return result;
}

function parseAnalyticsCommandEnvelope(
  value: unknown,
  config: AnalyticsCommandConfig,
): AnalyticsCommandResult {
  if (!isRecord(value))
    throw new Error(`analytics Playwriter command ${config.operation} returned no result envelope`);
  const keys = Object.keys(value).sort();
  if (
    JSON.stringify(keys) !== JSON.stringify(["command", "data", "logs", "ok", "schemaVersion"]) ||
    value.schemaVersion !== 1 ||
    value.command !== ANALYTICS_COMMAND_BY_OPERATION[config.operation] ||
    value.ok !== true ||
    !Array.isArray(value.logs) ||
    !isRecord(value.data)
  )
    throw new Error(
      `analytics Playwriter command ${config.operation} returned an invalid envelope`,
    );
  return value.data as unknown as AnalyticsCommandResult;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
