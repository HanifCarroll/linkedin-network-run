import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";

const JOB_TYPE = "com.linkedin.voyager.dash.jobs.JobPosting";
export const NORMALIZE_PARSER_VERSION = "jobs-normalize-v1";

type JsonObject = Record<string, unknown>;
type Page = { run_id: string; page_identity: string; captured_at: string; payload_json: string };

const object = (value: unknown): value is JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function jobId(value: unknown): string {
  const urn = object(value) && typeof value.entityUrn === "string" ? value.entityUrn : "";
  return /(?:fsd_jobPosting:|jobPosting:)(\d+)/.exec(urn)?.[1] ?? "";
}

function jobsFromPayload(payload: string): Array<{ id: string; title: string }> {
  const value: unknown = JSON.parse(payload);
  if (!object(value)) return [];
  const jobs = new Map<string, { id: string; title: string }>();
  const add = (item: unknown) => {
    const id = jobId(item);
    if (!id || !object(item)) return;
    const title = typeof item.title === "string" ? item.title : "";
    const current = jobs.get(id);
    if (current === undefined || (current.title === "" && title !== ""))
      jobs.set(id, { id, title });
  };
  if (Array.isArray(value.included)) {
    for (const item of value.included) {
      if (object(item) && item.$type === JOB_TYPE) add(item);
    }
  }
  const elements = Array.isArray(value.elements)
    ? value.elements
    : object(value.data) && Array.isArray(value.data.elements)
      ? value.data.elements
      : [];
  for (const item of elements) add(item);
  return [...jobs.values()].sort((left, right) => left.id.localeCompare(right.id));
}

export type NormalizeResult = {
  readonly command: "jobs normalize";
  readonly pagesProcessed: number;
  readonly jobsObserved: number;
  readonly newlyInserted: number;
  readonly deduplicated: number;
  readonly remainingPages: number;
};

export class JobsNormalizer {
  constructor(private readonly database: Database) {}

  normalize(input: { runId: string; limit?: number; now: string }): NormalizeResult {
    const run = this.database.query("SELECT 1 FROM capture_runs WHERE id = ?").get(input.runId);
    if (run === null) {
      throw new CliError("CAPTURE_RUN_NOT_FOUND", `capture run ${input.runId} does not exist`, {
        exitCode: 2,
      });
    }
    const pages = this.database
      .query<Page, [string, number | null]>(
        `SELECT p.run_id, p.page_identity, p.captured_at, p.payload_json
         FROM capture_pages p
         LEFT JOIN capture_page_normalizations n
           ON n.run_id = p.run_id AND n.page_identity = p.page_identity
         WHERE p.run_id = ? AND n.run_id IS NULL
         ORDER BY p.captured_at ASC, p.page_identity ASC
         LIMIT COALESCE(?, -1)`,
      )
      .all(input.runId, input.limit ?? null);
    let pagesProcessed = 0;
    let jobsObserved = 0;
    let newlyInserted = 0;
    let deduplicated = 0;
    for (const page of pages) {
      const result = this.processPage(page, input.now);
      pagesProcessed += 1;
      jobsObserved += result.observed;
      newlyInserted += result.inserted;
      deduplicated += result.deduplicated;
    }
    const remaining = this.database
      .query<{ count: number }, [string]>(
        `SELECT COUNT(*) AS count FROM capture_pages p
         LEFT JOIN capture_page_normalizations n
           ON n.run_id = p.run_id AND n.page_identity = p.page_identity
         WHERE p.run_id = ? AND n.run_id IS NULL`,
      )
      .get(input.runId);
    return {
      command: "jobs normalize",
      pagesProcessed,
      jobsObserved,
      newlyInserted,
      deduplicated,
      remainingPages: Number(remaining?.count ?? 0),
    };
  }

  private processPage(
    page: Page,
    now: string,
  ): { observed: number; inserted: number; deduplicated: number } {
    const jobs = jobsFromPayload(page.payload_json);
    return this.database.transaction(() => {
      const insertJob = this.database.prepare(`
        INSERT INTO jobs (id, title, company, location, posting_url, hiring_team_json,
          has_hiring_team, status, message, collected_at, updated_at, sent_at)
        VALUES (?, ?, '', '', ?, '[]', 0, 'captured', NULL, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
          title = CASE WHEN jobs.title = '' THEN excluded.title ELSE jobs.title END,
          posting_url = CASE WHEN jobs.posting_url = '' THEN excluded.posting_url ELSE jobs.posting_url END,
          updated_at = CASE WHEN jobs.title = '' OR jobs.posting_url = '' THEN excluded.updated_at ELSE jobs.updated_at END
      `);
      const insertObservation = this.database.prepare(`
        INSERT INTO job_observations
          (run_id, page_identity, job_id, observed_title, observed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(run_id, page_identity, job_id) DO UPDATE SET
          observed_title = CASE WHEN job_observations.observed_title = '' THEN excluded.observed_title
            ELSE job_observations.observed_title END
      `);
      let inserted = 0;
      let deduplicated = 0;
      const existingJob = this.database.prepare("SELECT 1 FROM jobs WHERE id = ?");
      for (const job of jobs) {
        const existed = existingJob.get(job.id) !== null;
        insertJob.run(
          job.id,
          job.title,
          `https://www.linkedin.com/jobs/view/${job.id}/`,
          page.captured_at,
          now,
        );
        if (existed) deduplicated += 1;
        else inserted += 1;
        insertObservation.run(page.run_id, page.page_identity, job.id, job.title, page.captured_at);
      }
      this.database
        .prepare(`INSERT INTO capture_page_normalizations
          (run_id, page_identity, parser_version, observed_count, normalized_at)
         VALUES (?, ?, ?, ?, ?)`)
        .run(page.run_id, page.page_identity, NORMALIZE_PARSER_VERSION, jobs.length, now);
      return { observed: jobs.length, inserted, deduplicated };
    })();
  }
}

export { jobsFromPayload };
