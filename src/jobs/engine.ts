import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";
import type {
  CapturedJob,
  CollectedJob,
  HiringTeamMember,
  JobDetail,
  JobRow,
  JobStatus,
} from "./types.ts";

type JobRowRaw = {
  readonly id: string;
  readonly title: string;
  readonly company: string;
  readonly location: string;
  readonly posting_url: string;
  readonly hiring_team_json: string;
  readonly has_hiring_team: number;
  readonly status: string;
  readonly message: string | null;
  readonly collected_at: string;
  readonly updated_at: string;
  readonly sent_at: string | null;
  readonly description: string;
  readonly workplace_type: string;
  readonly employment_type: string;
  readonly apply_method: string;
  readonly promoted: number;
  readonly actively_reviewing: number;
  readonly posted_at: string;
  readonly applicant_count: string;
  readonly benefits_json: string;
};

export class JobsEngine {
  constructor(private readonly database: Database) {}

  upsertJobs(jobs: readonly CollectedJob[], now: string): number {
    const stmt = this.database.prepare(`
      INSERT INTO jobs (
        id, title, company, location, posting_url, hiring_team_json, has_hiring_team,
        status, message, collected_at, updated_at, sent_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, 'collected', NULL, ?, ?, NULL)
      ON CONFLICT(id) DO UPDATE SET
        title = excluded.title,
        company = excluded.company,
        location = excluded.location,
        posting_url = excluded.posting_url,
        hiring_team_json = excluded.hiring_team_json,
        has_hiring_team = excluded.has_hiring_team,
        status = 'collected',
        updated_at = excluded.updated_at
    `);
    const tx = this.database.transaction(() => {
      for (const job of jobs) {
        stmt.run(
          job.id,
          job.title,
          job.company,
          job.location,
          job.postingUrl,
          JSON.stringify(job.hiringTeam),
          job.hasHiringTeam ? 1 : 0,
          now,
          now,
        );
      }
    });
    tx();
    return jobs.length;
  }

  storeCapturedJobs(jobs: readonly CapturedJob[], now: string): number {
    const stmt = this.database.prepare(`
      INSERT INTO jobs (
        id, title, company, location, posting_url, hiring_team_json, has_hiring_team,
        status, message, collected_at, updated_at, sent_at
      ) VALUES (?, ?, '', '', ?, '[]', 0, 'captured', NULL, ?, ?, NULL)
      ON CONFLICT(id) DO NOTHING
    `);
    const tx = this.database.transaction(() => {
      for (const job of jobs) {
        stmt.run(job.id, job.title, `https://www.linkedin.com/jobs/view/${job.id}/`, now, now);
      }
    });
    tx();
    return jobs.length;
  }

  deleteJobs(ids: readonly string[]): number {
    if (ids.length === 0) return 0;
    const stmt = this.database.prepare(`DELETE FROM jobs WHERE id = ?`);
    const tx = this.database.transaction(() => {
      for (const id of ids) stmt.run(id);
    });
    tx();
    return ids.length;
  }
  storeJobDetails(details: readonly JobDetail[], now: string): number {
    if (details.length === 0) return 0;
    const stmt = this.database.prepare(`
      UPDATE jobs SET
        description = ?, workplace_type = ?, employment_type = ?, apply_method = ?,
        promoted = ?, actively_reviewing = ?, posted_at = ?, applicant_count = ?,
        benefits_json = ?, updated_at = ?
      WHERE id = ?
    `);
    const tx = this.database.transaction(() => {
      for (const d of details) {
        stmt.run(
          d.description,
          d.workplaceType,
          d.employmentType,
          d.applyMethod,
          d.promoted ? 1 : 0,
          d.activelyReviewing ? 1 : 0,
          d.postedAt,
          d.applicantCount,
          JSON.stringify(d.benefits),
          now,
          d.id,
        );
      }
    });
    tx();
    return details.length;
  }

  listJobs(options: {
    readonly status?: JobStatus;
    readonly withHiringTeam: boolean;
    readonly uncheckedOnly?: boolean;
    readonly needsDetail?: boolean;
  }): JobRow[] {
    const clauses: string[] = [];
    const params: string[] = [];
    if (options.status !== undefined) {
      clauses.push("status = ?");
      params.push(options.status);
    }
    if (options.withHiringTeam) clauses.push("has_hiring_team = 1");
    if (options.uncheckedOnly) clauses.push("checked_at IS NULL");
    if (options.needsDetail) clauses.push("description = ''");
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const rows = this.database
      .query<JobRowRaw, string[]>(`SELECT * FROM jobs ${where} ORDER BY collected_at DESC`)
      .all(...params);
    return rows.map(rowToJob);
  }

  markChecked(ids: readonly string[], now: string): number {
    if (ids.length === 0) return 0;
    const stmt = this.database.prepare(`UPDATE jobs SET checked_at = ? WHERE id = ?`);
    const tx = this.database.transaction(() => {
      for (const id of ids) stmt.run(now, id);
    });
    tx();
    return ids.length;
  }

  favoriteJobs(ids: readonly string[], now: string): JobRow[] {
    if (ids.length === 0)
      throw new CliError("INVALID_ARGUMENT", "favorite requires at least one --id");
    const tx = this.database.transaction(() => {
      for (const id of ids) this.setStatus(id, "favorite", now);
    });
    tx();
    return ids.map((id) => this.requireJob(id));
  }

  storeDraft(id: string, message: string, now: string): JobRow {
    if (message.trim().length === 0)
      throw new CliError("INVALID_ARGUMENT", "draft requires a non-empty --message");
    this.requireJob(id);
    this.database
      .prepare(`UPDATE jobs SET status = 'drafted', message = ?, updated_at = ? WHERE id = ?`)
      .run(message.trim(), now, id);
    return this.requireJob(id);
  }

  draftedJobs(): JobRow[] {
    const rows = this.database
      .query<JobRowRaw, []>(`SELECT * FROM jobs WHERE status = 'drafted' ORDER BY updated_at ASC`)
      .all();
    return rows.map(rowToJob);
  }

  markSent(id: string, now: string): JobRow {
    this.database
      .prepare(`UPDATE jobs SET status = 'sent', sent_at = ?, updated_at = ? WHERE id = ?`)
      .run(now, now, id);
    return this.requireJob(id);
  }

  requireJob(id: string): JobRow {
    const row = this.database.query<JobRowRaw, [string]>(`SELECT * FROM jobs WHERE id = ?`).get(id);
    if (row === null)
      throw new CliError("JOB_NOT_FOUND", `no collected job with id ${id}`, { exitCode: 2 });
    return rowToJob(row);
  }

  private setStatus(id: string, status: JobStatus, now: string): void {
    this.database
      .prepare(`UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?`)
      .run(status, now, id);
  }
}

function rowToJob(row: JobRowRaw): JobRow {
  let hiringTeam: readonly HiringTeamMember[] = [];
  try {
    const parsed = JSON.parse(row.hiring_team_json) as unknown;
    if (Array.isArray(parsed)) hiringTeam = parsed as HiringTeamMember[];
  } catch {
    hiringTeam = [];
  }
  let benefits: readonly string[] = [];
  try {
    const parsed = JSON.parse(row.benefits_json) as unknown;
    if (Array.isArray(parsed)) benefits = parsed.filter((b): b is string => typeof b === "string");
  } catch {
    benefits = [];
  }
  return {
    id: row.id,
    title: row.title,
    company: row.company,
    location: row.location,
    postingUrl: row.posting_url,
    hiringTeam,
    hasHiringTeam: row.has_hiring_team === 1,
    status: row.status as JobStatus,
    message: row.message,
    collectedAt: row.collected_at,
    updatedAt: row.updated_at,
    sentAt: row.sent_at,
    description: row.description,
    workplaceType: row.workplace_type,
    employmentType: row.employment_type,
    applyMethod: row.apply_method,
    promoted: row.promoted === 1,
    activelyReviewing: row.actively_reviewing === 1,
    postedAt: row.posted_at,
    applicantCount: row.applicant_count,
    benefits,
  };
}
