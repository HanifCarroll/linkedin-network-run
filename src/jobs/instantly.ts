import type { Database } from "bun:sqlite";
import { createHash } from "node:crypto";
import { CliError } from "../core/errors.ts";
import { outreachKindFor } from "../view/grouping.ts";
import { JobsEngine } from "./engine.ts";
import { recipientProfileUrl } from "./recipient.ts";
import type { JobRow } from "./types.ts";

export type InstantlyReceiptInput = {
  readonly prospectId: string;
  readonly email?: string;
  readonly noEmail?: true;
  readonly campaignEnrollmentId?: string;
  readonly stopReplyStatus?: string;
  readonly error?: string;
};

type ReceiptRow = {
  prospect_id: string;
  job_id: string;
  email: string | null;
  no_email: number;
  campaign_enrollment_id: string | null;
  stop_reply_status: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export class InstantlyHandoffEngine {
  private readonly jobs: JobsEngine;
  constructor(private readonly database: Database) {
    this.jobs = new JobsEngine(database);
  }

  next(jobId: string | undefined, now: string): Record<string, unknown> | null {
    const existing =
      jobId === undefined
        ? this.database
            .query<ReceiptRow, []>(
              "SELECT * FROM instantly_handoffs WHERE completed_at IS NULL ORDER BY created_at, prospect_id LIMIT 1",
            )
            .get()
        : (this.database
            .prepare("SELECT * FROM instantly_handoffs WHERE prospect_id = ? OR job_id = ? LIMIT 1")
            .get(jobId, jobId) as ReceiptRow | null);
    const job = existing ? this.jobs.requireJob(existing.job_id) : this.candidate(jobId);
    if (job === null) return null;
    assertEligible(this.database, job);
    const prospectId = prospectIdForJob(job);
    let receipt =
      existing ??
      this.database
        .query<ReceiptRow, [string]>("SELECT * FROM instantly_handoffs WHERE prospect_id = ?")
        .get(prospectId);
    if (receipt === null) {
      this.database
        .prepare(
          "INSERT INTO instantly_handoffs (prospect_id, job_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
        )
        .run(prospectId, job.id, now, now);
      receipt = this.require(prospectId);
    }
    return packet(job, rowToReceipt(receipt));
  }

  record(input: InstantlyReceiptInput, now: string): Record<string, unknown> {
    const current = this.require(input.prospectId);
    if (current.completed_at !== null)
      throw new CliError(
        "INSTANTLY_HANDOFF_COMPLETE",
        `Instantly handoff ${input.prospectId} is already complete`,
        { exitCode: 2 },
      );
    if (input.email !== undefined && input.noEmail === true)
      throw new CliError("INSTANTLY_AMBIGUOUS_EMAIL", "email and no-email conflict", {
        exitCode: 2,
      });
    if (input.email === undefined && input.noEmail !== true && input.error === undefined)
      throw new CliError(
        "INSTANTLY_EMAIL_REQUIRED",
        "record exactly one work email, --no-email, or --error",
        { exitCode: 2 },
      );
    if (input.email !== undefined && !/^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/.test(input.email))
      throw new CliError("INSTANTLY_AMBIGUOUS_EMAIL", "record one unambiguous work email", {
        exitCode: 2,
      });
    const email = input.email ?? current.email;
    const noEmail = input.noEmail === true || current.no_email === 1;
    const enrollment = input.campaignEnrollmentId ?? current.campaign_enrollment_id;
    const status = input.stopReplyStatus ?? current.stop_reply_status;
    const error =
      input.error ??
      (input.email !== undefined || input.noEmail === true ? null : current.last_error);
    const complete = noEmail || (email !== null && enrollment !== null && status !== null);
    this.database
      .prepare(
        `UPDATE instantly_handoffs SET email=?, no_email=?, campaign_enrollment_id=?, stop_reply_status=?, last_error=?, updated_at=?, completed_at=? WHERE prospect_id=?`,
      )
      .run(
        email,
        noEmail ? 1 : 0,
        enrollment,
        status,
        error,
        now,
        complete ? (current.completed_at ?? now) : null,
        input.prospectId,
      );
    return rowToReceipt(this.require(input.prospectId));
  }

  private candidate(jobId?: string): JobRow | null {
    if (jobId !== undefined) return this.jobs.requireJob(jobId);
    for (const job of this.jobs.listJobs({ withHiringTeam: true, fit: "kept" })) {
      if (job.review !== "approved" || recipientProfileUrl(job) === null) continue;
      const hs = this.database
        .query<{ completed_at: string | null }, [string]>(
          "SELECT completed_at FROM hubspot_imports WHERE job_id = ?",
        )
        .get(job.id);
      if (
        hs?.completed_at !== null &&
        hs !== null &&
        this.database.query("SELECT 1 FROM instantly_handoffs WHERE job_id = ?").get(job.id) ===
          null
      )
        return job;
    }
    return null;
  }

  private require(prospectId: string): ReceiptRow {
    const row = this.database
      .query<ReceiptRow, [string]>("SELECT * FROM instantly_handoffs WHERE prospect_id = ?")
      .get(prospectId);
    if (row === null)
      throw new CliError(
        "INSTANTLY_HANDOFF_NOT_FOUND",
        `no Instantly handoff exists for ${prospectId}; run jobs instantly-next first`,
        { exitCode: 2 },
      );
    return row;
  }
}

export function prospectIdForJob(job: JobRow): string {
  const profile = recipientProfileUrl(job);
  if (profile === null)
    throw new CliError(
      "JOBS_NO_HIRING_TEAM",
      `job ${job.id} has no usable hiring-team profile URL`,
      { exitCode: 2 },
    );
  return `co:instantly:v1:${createHash("sha256").update(`${profile}\n${job.id}`).digest("hex")}`;
}

function assertEligible(database: Database, job: JobRow): void {
  if (job.fit !== "kept")
    throw new CliError("INSTANTLY_NOT_KEPT", `job ${job.id} is not kept`, { exitCode: 2 });
  if (job.review !== "approved")
    throw new CliError("INSTANTLY_NOT_APPROVED", `job ${job.id} is not approved`, { exitCode: 2 });
  if (job.company.trim() === "")
    throw new CliError("INSTANTLY_COMPANY_REQUIRED", `job ${job.id} has no company`, {
      exitCode: 2,
    });
  const route = outreachKindFor(job);
  if (route !== "direct" && route !== "application_followup")
    throw new CliError(
      "INSTANTLY_NOT_CONTRACT_OUTREACH",
      `job ${job.id} has no contract outreach route`,
      { exitCode: 2 },
    );
  const hs = database
    .query<{ completed_at: string | null }, [string]>(
      "SELECT completed_at FROM hubspot_imports WHERE job_id = ?",
    )
    .get(job.id);
  if (hs?.completed_at === null || hs === null)
    throw new CliError("INSTANTLY_HUBSPOT_REQUIRED", `job ${job.id} is not HubSpot-ready`, {
      exitCode: 2,
    });
}

function rowToReceipt(row: ReceiptRow): Record<string, unknown> {
  return {
    prospectId: row.prospect_id,
    jobId: row.job_id,
    email: row.email,
    noEmail: row.no_email === 1,
    campaignEnrollmentId: row.campaign_enrollment_id,
    stopReplyStatus: row.stop_reply_status,
    lastError: row.last_error,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    completedAt: row.completed_at,
  };
}

function packet(job: JobRow, receipt: Record<string, unknown>): Record<string, unknown> {
  const person = job.hiringTeam[0];
  if (!person) throw new CliError("JOBS_NO_HIRING_TEAM", "hiring team missing", { exitCode: 2 });
  return {
    command: "jobs instantly-next",
    prospect: {
      prospectId: receipt.prospectId,
      jobId: job.id,
      person: {
        name: person.name,
        headline: person.headline,
        linkedinUrl: recipientProfileUrl(job),
      },
      company: job.company,
      title: job.title,
      postingUrl: job.postingUrl,
      route: outreachKindFor(job),
    },
    receipt,
    instantly: {
      apiVersion: "v2",
      cadence: "use the selected campaign's existing cadence",
      stopOnReply: "verify campaign stop_on_reply=true; do not create local follow-ups",
      steps: [
        {
          method: "POST",
          path: "/api/v2/supersearch-enrichment/enrich-leads-from-supersearch",
          body: {
            work_email_enrichment: true,
            skip_rows_without_email: false,
            search_filters: { company: [job.company], linkedin_url: [recipientProfileUrl(job)] },
          },
          result: "record exactly one work email or explicit no-email; ambiguous results block",
        },
        {
          method: "POST",
          path: "/api/v2/leads/add",
          body: {
            campaign_id: "<approved-campaign-id>",
            leads: [{ email: "<recorded-work-email>" }],
          },
          result: "record campaign enrollment ID",
        },
        {
          method: "GET",
          path: "/api/v2/emails",
          result: "record stop/reply status and preserve campaign cadence",
        },
      ],
    },
  };
}
