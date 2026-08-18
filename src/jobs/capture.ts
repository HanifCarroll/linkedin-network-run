import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";

export const CAPTURE_PARSER_VERSION = "jobs-xhr-v1";

type CaptureRun = {
  readonly id: string;
  readonly sourceUrl: string;
  readonly searchConfig: Record<string, unknown>;
  readonly state: string;
  readonly startedAt: string;
  readonly updatedAt: string;
  readonly completedAt: string | null;
  readonly checkpoint: Record<string, unknown>;
  readonly error: string | null;
};

function jsonObject(value: string, name: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new CliError("INVALID_ARGUMENT", `${name} must be valid JSON`, { exitCode: 2 });
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new CliError("INVALID_ARGUMENT", `${name} must be a JSON object`, { exitCode: 2 });
  }
  return parsed as Record<string, unknown>;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function containsCheckpoint(
  current: Record<string, unknown>,
  expected: Record<string, unknown>,
): boolean {
  return Object.entries(expected).every(
    ([key, value]) => key in current && canonicalJson(current[key]) === canonicalJson(value),
  );
}
const mergedCheckpoint = (base: Record<string, unknown>, update?: Record<string, unknown>) => ({
  ...base,
  ...(update ?? {}),
});

function payloadObject(text: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new CliError("INVALID_ARGUMENT", "capture payload must be valid JSON", { exitCode: 2 });
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new CliError("INVALID_ARGUMENT", "capture payload must be a JSON object", {
      exitCode: 2,
    });
  }
  const object = parsed as Record<string, unknown>;
  const hasJobIncluded =
    Array.isArray(object.included) &&
    object.included.some(
      (item) =>
        item !== null &&
        typeof item === "object" &&
        (item as Record<string, unknown>).$type === "com.linkedin.voyager.dash.jobs.JobPosting",
    );
  const hasElements = Array.isArray(object.elements);
  const hasNestedElements =
    object.data !== null &&
    typeof object.data === "object" &&
    Array.isArray((object.data as Record<string, unknown>).elements);
  if (!hasJobIncluded && !hasElements && !hasNestedElements) {
    throw new CliError("INVALID_ARGUMENT", "capture payload is not a Jobs search response", {
      exitCode: 2,
    });
  }
  return object;
}

function itemCount(payload: Record<string, unknown>): number | null {
  if (Array.isArray(payload.included)) {
    const count = payload.included.filter(
      (item) =>
        item !== null &&
        typeof item === "object" &&
        (item as Record<string, unknown>).$type === "com.linkedin.voyager.dash.jobs.JobPosting",
    ).length;
    if (count > 0) return count;
  }
  if (Array.isArray(payload.elements)) return payload.elements.length;
  if (payload.data !== null && typeof payload.data === "object") {
    const elements = (payload.data as Record<string, unknown>).elements;
    if (Array.isArray(elements)) return elements.length;
  }
  return null;
}

const conflict = (code: string, message: string): never => {
  throw new CliError(code, message, { exitCode: 2 });
};

export class JobsCaptureStore {
  constructor(private readonly database: Database) {}

  startRun(input: {
    id: string;
    sourceUrl: string;
    searchConfigJson?: string;
    checkpointJson?: string;
    now: string;
  }): CaptureRun {
    const config =
      input.searchConfigJson === undefined
        ? {}
        : jsonObject(input.searchConfigJson, "--search-config");
    const checkpoint =
      input.checkpointJson === undefined ? {} : jsonObject(input.checkpointJson, "--checkpoint");
    const existing = this.database
      .query<Record<string, unknown>, [string]>("SELECT * FROM capture_runs WHERE id = ?")
      .get(input.id);
    if (existing !== null) {
      const existingConfig = JSON.parse(String(existing.search_config_json)) as Record<
        string,
        unknown
      >;
      const existingCheckpoint = JSON.parse(String(existing.checkpoint_json)) as Record<
        string,
        unknown
      >;
      if (
        existing.source_url !== input.sourceUrl ||
        canonicalJson(existingConfig) !== canonicalJson(config) ||
        (input.checkpointJson !== undefined && !containsCheckpoint(existingCheckpoint, checkpoint))
      ) {
        return conflict(
          "CAPTURE_RUN_CONFLICT",
          `capture run ${input.id} already has different metadata`,
        );
      }
      return this.requireRun(input.id);
    }
    this.database
      .prepare(
        `INSERT INTO capture_runs
          (id, source_url, search_config_json, started_at, updated_at, checkpoint_json)
         VALUES (?, ?, ?, ?, ?, ?)`,
      )
      .run(
        input.id,
        input.sourceUrl,
        canonicalJson(config),
        input.now,
        input.now,
        canonicalJson(checkpoint),
      );
    return this.requireRun(input.id);
  }

  ingestPage(input: {
    runId: string;
    pageIdentity: string;
    cursor?: string;
    sourceUrl: string;
    responseUrl: string;
    capturedAt: string;
    payloadText: string;
  }): { inserted: boolean; itemCount: number | null; run: CaptureRun } {
    const payload = payloadObject(input.payloadText);
    const count = itemCount(payload);
    const cursor = input.cursor ?? null;
    const existingRun = this.requireRun(input.runId);
    const existingPage = this.database
      .query<Record<string, unknown>, [string, string]>(
        "SELECT * FROM capture_pages WHERE run_id = ? AND page_identity = ?",
      )
      .get(input.runId, input.pageIdentity);
    if (existingPage !== null) {
      if (
        existingPage.cursor !== cursor ||
        existingPage.source_url !== input.sourceUrl ||
        existingPage.response_url !== input.responseUrl ||
        existingPage.payload_json !== input.payloadText
      ) {
        return conflict(
          "CAPTURE_PAGE_CONFLICT",
          `capture page ${input.runId}/${input.pageIdentity} already has different content`,
        );
      }
      if (existingRun.state === "active") {
        this.database
          .prepare(
            `UPDATE capture_runs
             SET updated_at = ?, checkpoint_json = json_patch(checkpoint_json, json_object('last_page', ?, 'last_cursor', ?))
             WHERE id = ?`,
          )
          .run(input.capturedAt, input.pageIdentity, cursor, input.runId);
      }
      return {
        inserted: false,
        itemCount: existingPage.item_count === null ? null : Number(existingPage.item_count),
        run: this.requireRun(input.runId),
      };
    }
    if (existingRun.state !== "active") {
      return conflict(
        "CAPTURE_RUN_NOT_ACTIVE",
        `capture run ${input.runId} is ${existingRun.state}`,
      );
    }
    const tx = this.database.transaction(() => {
      this.database
        .prepare(
          `INSERT INTO capture_pages
            (run_id, page_identity, cursor, source_url, response_url, captured_at,
             parser_version, item_count, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run(
          input.runId,
          input.pageIdentity,
          cursor,
          input.sourceUrl,
          input.responseUrl,
          input.capturedAt,
          CAPTURE_PARSER_VERSION,
          count,
          input.payloadText,
        );
      this.database
        .prepare(
          `UPDATE capture_runs
           SET updated_at = ?, checkpoint_json = json_patch(checkpoint_json, json_object('last_page', ?, 'last_cursor', ?))
           WHERE id = ?`,
        )
        .run(input.capturedAt, input.pageIdentity, cursor, input.runId);
    });
    tx();
    return { inserted: true, itemCount: count, run: this.requireRun(input.runId) };
  }

  finishRun(input: {
    id: string;
    state: "complete" | "failed";
    checkpointJson?: string;
    error?: string;
    now: string;
  }): CaptureRun {
    const requestedCheckpoint =
      input.checkpointJson === undefined
        ? undefined
        : jsonObject(input.checkpointJson, "--checkpoint");
    const existing = this.requireRun(input.id);
    if (existing.state !== "active") {
      const checkpoint = mergedCheckpoint(existing.checkpoint, requestedCheckpoint);
      if (
        existing.state !== input.state ||
        canonicalJson(existing.checkpoint) !== canonicalJson(checkpoint) ||
        (existing.error ?? null) !== (input.error ?? null)
      ) {
        return conflict(
          "CAPTURE_FINISH_CONFLICT",
          `capture run ${input.id} already finished differently`,
        );
      }
      return existing;
    }
    const checkpoint = mergedCheckpoint(existing.checkpoint, requestedCheckpoint);
    this.database
      .prepare(
        `UPDATE capture_runs
         SET state = ?, completed_at = ?, updated_at = ?, checkpoint_json = ?, error = ?
         WHERE id = ? AND state = 'active'`,
      )
      .run(
        input.state,
        input.now,
        input.now,
        canonicalJson(checkpoint),
        input.error ?? null,
        input.id,
      );
    return this.requireRun(input.id);
  }

  private requireRun(id: string): CaptureRun {
    const row = this.database
      .query<Record<string, unknown>, [string]>("SELECT * FROM capture_runs WHERE id = ?")
      .get(id);
    if (row === null)
      throw new CliError("CAPTURE_RUN_NOT_FOUND", `capture run ${id} does not exist`, {
        exitCode: 2,
      });
    return {
      id: String(row.id),
      sourceUrl: String(row.source_url),
      searchConfig: JSON.parse(String(row.search_config_json)) as Record<string, unknown>,
      state: String(row.state),
      startedAt: String(row.started_at),
      updatedAt: String(row.updated_at),
      completedAt: row.completed_at === null ? null : String(row.completed_at),
      checkpoint: JSON.parse(String(row.checkpoint_json)) as Record<string, unknown>,
      error: row.error === null ? null : String(row.error),
    };
  }
}

export function parseCaptureMetadata(value: string | undefined, name: string): string | undefined {
  if (value === undefined) return undefined;
  jsonObject(value, name);
  return value;
}
