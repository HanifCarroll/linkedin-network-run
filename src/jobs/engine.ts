import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";
import { recipientProfileUrl } from "./recipient.ts";
import {
  CLASSIFICATION_MAX_LENGTH,
  DRAFT_MAX_LENGTH,
  SUBJECT_MAX_LENGTH,
  SUMMARY_MAX_LENGTH,
} from "./types.ts";
import type {
  CapturedJob,
  CollectedJob,
  HiringTeamMember,
  JobDetail,
  JobRow,
  JobStatus,
  ReviewDecision,
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
  readonly work_focus: string;
  readonly product_system: string;
  readonly work_summary: string;
  readonly product_summary: string;
  readonly subject: string;
  readonly review: string;
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
        status = CASE
          WHEN jobs.status IN ('favorite', 'drafted', 'sent') THEN jobs.status
          ELSE 'collected'
        END,
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
      let removed = 0;
      for (const id of ids) removed += stmt.run(id).changes;
      return removed;
    });
    return tx();
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

  storeDraft(id: string, subject: string, message: string, now: string): JobRow {
    const body = message.trim();
    const subj = subject.trim();
    if (body.length === 0)
      throw new CliError("INVALID_ARGUMENT", "draft requires a non-empty message");
    if (body.length > DRAFT_MAX_LENGTH)
      throw new CliError(
        "INVALID_ARGUMENT",
        `draft message must be at most ${DRAFT_MAX_LENGTH} characters`,
      );
    if (subj.length > SUBJECT_MAX_LENGTH)
      throw new CliError(
        "INVALID_ARGUMENT",
        `draft subject must be at most ${SUBJECT_MAX_LENGTH} characters`,
      );
    const existing = this.requireJob(id);
    if (existing.status === "sent") {
      throw new CliError(
        "JOBS_ALREADY_SENT",
        `job ${id} was already sent and cannot be redrafted`,
        {
          exitCode: 2,
        },
      );
    }
    this.database
      .prepare(
        `UPDATE jobs SET status = 'drafted', message = ?, subject = ?, review = 'needs_review', updated_at = ? WHERE id = ?`,
      )
      .run(body, subj, now, id);
    return this.requireJob(id);
  }

  approvedDrafts(): JobRow[] {
    const rows = this.database
      .query<JobRowRaw, []>(
        `SELECT * FROM jobs WHERE status = 'drafted' AND review = 'approved' ORDER BY updated_at ASC`,
      )
      .all();
    return rows.map(rowToJob);
  }

  setReview(id: string, review: ReviewDecision, now: string, replaceId?: string): JobRow {
    const job = this.requireJob(id);
    if (job.status === "sent") {
      throw new CliError(
        "JOBS_ALREADY_SENT",
        `job ${id} was already sent and its review cannot change`,
        { exitCode: 2 },
      );
    }
    if (review === "approved") {
      if (job.status !== "drafted") {
        throw new CliError("JOBS_NOT_DRAFTED", `job ${id} is not a pending draft`, {
          exitCode: 2,
        });
      }
      if (job.message === null || job.message.trim().length === 0) {
        throw new CliError("JOBS_NO_DRAFT", `job ${id} has no drafted message`, {
          exitCode: 2,
        });
      }
      const profile = recipientProfileUrl(job);
      if (profile === null) {
        throw new CliError(
          "JOBS_NO_HIRING_TEAM",
          `job ${id} has no usable hiring-team profile URL`,
          { exitCode: 2 },
        );
      }
      const conflict = this.conflictingJobForRecipient(profile, id);
      if (conflict !== null) {
        if (conflict.status === "sent") {
          throw new CliError(
            "DUPLICATE_APPROVED_PROFILE",
            `job ${id} shares hiring-team profile ${profile} with already-sent job ${conflict.id}`,
            { exitCode: 2, details: conflictDetails(conflict) },
          );
        }
        if (replaceId === conflict.id) {
          return this.replaceApproval(conflict.id, id, now);
        }
        if (replaceId !== undefined) {
          throw new CliError(
            "DUPLICATE_REPLACE_STALE",
            `replacement target ${replaceId} is not the current conflict ${conflict.id} for job ${id}`,
            { exitCode: 2 },
          );
        }
        throw new CliError(
          "DUPLICATE_APPROVED_PROFILE",
          `job ${id} shares hiring-team profile ${profile} with already-approved job ${conflict.id}`,
          { exitCode: 2, details: conflictDetails(conflict) },
        );
      }
      if (replaceId !== undefined) {
        throw new CliError(
          "DUPLICATE_REPLACE_STALE",
          `no conflicting approval ${replaceId} for job ${id}`,
          { exitCode: 2 },
        );
      }
    }
    this.database
      .prepare(`UPDATE jobs SET review = ?, updated_at = ? WHERE id = ?`)
      .run(review, now, id);
    return this.requireJob(id);
  }

  /** Replace one approved draft with another, in one transaction. */
  private replaceApproval(conflictId: string, currentId: string, now: string): JobRow {
    const tx = this.database.transaction(() => {
      this.database
        .prepare(`UPDATE jobs SET review = 'needs_review', updated_at = ? WHERE id = ?`)
        .run(now, conflictId);
      this.database
        .prepare(`UPDATE jobs SET review = 'approved', updated_at = ? WHERE id = ?`)
        .run(now, currentId);
    });
    tx();
    return this.requireJob(currentId);
  }

  /**
   * Atomically apply a group-level decision to every non-sent role that shares
   * the anchor job's normalized first hiring-team profile. A job with no
   * usable recipient profile is its own single-role group. Sent roles are
   * never mutated. One SQLite transaction; returns the refreshed group.
   */
  setGroupReview(jobId: string, review: "skipped" | "needs_review", now: string): JobRow[] {
    const anchor = this.requireJob(jobId);
    const profile = recipientProfileUrl(anchor);
    const group =
      profile === null
        ? [anchor]
        : this.listJobs({ withHiringTeam: false }).filter(
            (job) => recipientProfileUrl(job) === profile,
          );
    // Whole-recipient immutability: a covered (sent) recipient group cannot be
    // skipped or returned to review. Reject before any mutation so an unsent
    // sibling in a sent group is never touched.
    const sent = group.find((job) => job.status === "sent");
    if (sent !== undefined) {
      throw new CliError(
        "JOBS_ALREADY_SENT",
        `recipient group for job ${jobId} includes already-sent job ${sent.id}; group review is not allowed`,
        { exitCode: 2 },
      );
    }
    const update = this.database.prepare(
      `UPDATE jobs SET review = ?, updated_at = ? WHERE id = ?`,
    );
    const tx = this.database.transaction(() => {
      for (const job of group) update.run(review, now, job.id);
    });
    tx();
    return group.map((job) => this.requireJob(job.id));
  }

  sentJobs(): JobRow[] {
    const rows = this.database
      .query<JobRowRaw, []>(`SELECT * FROM jobs WHERE status = 'sent' ORDER BY sent_at ASC`)
      .all();
    return rows.map(rowToJob);
  }

  private conflictingJobForRecipient(profileUrl: string, excludeId: string): JobRow | null {
    const candidates = [...this.approvedDrafts(), ...this.sentJobs()];
    return (
      candidates.find((job) => job.id !== excludeId && recipientProfileUrl(job) === profileUrl) ??
      null
    );
  }

  markSent(id: string, now: string): JobRow {
    this.database
      .prepare(`UPDATE jobs SET status = 'sent', sent_at = ?, updated_at = ? WHERE id = ?`)
      .run(now, now, id);
    return this.requireJob(id);
  }

  classifyJob(
    id: string,
    workFocus: string,
    productSystem: string,
    workSummary: string,
    productSummary: string,
    now: string,
  ): JobRow {
    const focus = workFocus.trim();
    const system = productSystem.trim();
    const summary = workSummary.trim();
    const product = productSummary.trim();
    if (focus.length === 0 || system.length === 0 || summary.length === 0 || product.length === 0) {
      throw new CliError(
        "INVALID_ARGUMENT",
        "classify requires a non-empty --work-focus, --product-system, --work-summary, and --product-summary",
      );
    }
    if (focus.length > CLASSIFICATION_MAX_LENGTH || system.length > CLASSIFICATION_MAX_LENGTH) {
      throw new CliError(
        "INVALID_ARGUMENT",
        `classify work-focus and product-system must be at most ${CLASSIFICATION_MAX_LENGTH} characters`,
      );
    }
    if (summary.length > SUMMARY_MAX_LENGTH || product.length > SUMMARY_MAX_LENGTH) {
      throw new CliError(
        "INVALID_ARGUMENT",
        `classify summaries must be at most ${SUMMARY_MAX_LENGTH} characters`,
      );
    }
    this.requireJob(id);
    this.database
      .prepare(
        `UPDATE jobs SET work_focus = ?, product_system = ?, work_summary = ?, product_summary = ?, updated_at = ? WHERE id = ?`,
      )
      .run(focus, system, summary, product, now, id);
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

function conflictDetails(job: JobRow): Record<string, unknown> {
  return {
    jobId: job.id,
    title: job.title,
    company: job.company,
    status: job.status,
    review: job.review,
  };
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
    workFocus: row.work_focus,
    productSystem: row.product_system,
    workSummary: row.work_summary,
    productSummary: row.product_summary,
    subject: row.subject,
    review: row.review as ReviewDecision,
  };
}

export { normalizeProfileUrl, recipientProfileUrl } from "./recipient.ts";
