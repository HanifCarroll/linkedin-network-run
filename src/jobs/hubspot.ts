import type { Database } from "bun:sqlite";
import { createHash } from "node:crypto";
import { CliError } from "../core/errors.ts";
import { outreachKindFor } from "../view/grouping.ts";
import { JobsEngine } from "./engine.ts";
import { normalizeProfileUrl, recipientProfileUrl } from "./recipient.ts";
import type { JobRow } from "./types.ts";

export const HUBSPOT_PORTAL_ID = "51829251";
export const HUBSPOT_PIPELINE_ID = "default";
export const HUBSPOT_INITIAL_STAGE_ID = "appointmentscheduled";

export type HubSpotImportReceipt = {
  readonly prospectId: string;
  readonly jobId: string;
  readonly companyId: string | null;
  readonly contactId: string | null;
  readonly dealId: string | null;
  readonly taskId: string | null;
  readonly associationsComplete: boolean;
  readonly lastError: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly completedAt: string | null;
};

export type HubSpotRecordInput = {
  readonly prospectId: string;
  readonly companyId?: string;
  readonly contactId?: string;
  readonly dealId?: string;
  readonly taskId?: string;
  readonly associationsComplete?: true;
  readonly error?: string;
};

type ReceiptRow = {
  readonly prospect_id: string;
  readonly job_id: string;
  readonly company_id: string | null;
  readonly contact_id: string | null;
  readonly deal_id: string | null;
  readonly task_id: string | null;
  readonly associations_complete: number;
  readonly last_error: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly completed_at: string | null;
};

export class HubSpotImportEngine {
  private readonly jobs: JobsEngine;

  constructor(private readonly database: Database) {
    this.jobs = new JobsEngine(database);
  }

  next(jobId: string | undefined, now: string): Record<string, unknown> | null {
    const selected = jobId === undefined ? this.nextCandidate() : this.requestedCandidate(jobId);
    if (selected === null) return null;
    const { job, receipt: existing } = selected;
    const profileUrl = requiredProfileUrl(job);
    const prospectId = prospectIdForProfile(profileUrl);
    let receipt = existing ?? this.receiptByProspect(prospectId);
    if (receipt === null) {
      assertEligible(job);
      this.database
        .prepare(
          `INSERT INTO hubspot_imports (
            prospect_id, job_id, created_at, updated_at
          ) VALUES (?, ?, ?, ?)`,
        )
        .run(prospectId, job.id, now, now);
      receipt = this.requireReceipt(prospectId);
    }
    return buildPacket(job, rowToReceipt(receipt));
  }

  record(input: HubSpotRecordInput, now: string): HubSpotImportReceipt {
    const current = this.requireReceipt(input.prospectId);
    if (input.error !== undefined && current.completed_at !== null) {
      throw new CliError(
        "HUBSPOT_IMPORT_COMPLETE",
        `HubSpot import ${input.prospectId} is already complete`,
        { exitCode: 2 },
      );
    }
    const suppliedIds = {
      company_id: input.companyId,
      contact_id: input.contactId,
      deal_id: input.dealId,
      task_id: input.taskId,
    } as const;
    for (const [column, supplied] of Object.entries(suppliedIds)) {
      if (supplied === undefined) continue;
      const existing = current[column as keyof typeof suppliedIds] as string | null;
      if (existing !== null && existing !== supplied) {
        throw new CliError(
          "HUBSPOT_RECEIPT_CONFLICT",
          `${column} for ${input.prospectId} is already ${existing}, not ${supplied}`,
          { exitCode: 2 },
        );
      }
    }

    const next = {
      companyId: input.companyId ?? current.company_id,
      contactId: input.contactId ?? current.contact_id,
      dealId: input.dealId ?? current.deal_id,
      taskId: input.taskId ?? current.task_id,
      associationsComplete:
        input.associationsComplete === true || current.associations_complete === 1,
      lastError: input.error ?? (hasSuccessReceipt(input) ? null : current.last_error),
    };
    const completed =
      next.companyId !== null &&
      next.contactId !== null &&
      next.dealId !== null &&
      next.taskId !== null &&
      next.associationsComplete;
    const completedAt = completed ? (current.completed_at ?? now) : null;
    const unchanged =
      next.companyId === current.company_id &&
      next.contactId === current.contact_id &&
      next.dealId === current.deal_id &&
      next.taskId === current.task_id &&
      next.associationsComplete === (current.associations_complete === 1) &&
      next.lastError === current.last_error &&
      completedAt === current.completed_at;
    if (!unchanged) {
      this.database
        .prepare(
          `UPDATE hubspot_imports SET
            company_id = ?, contact_id = ?, deal_id = ?, task_id = ?,
            associations_complete = ?, last_error = ?, updated_at = ?, completed_at = ?
          WHERE prospect_id = ?`,
        )
        .run(
          next.companyId,
          next.contactId,
          next.dealId,
          next.taskId,
          next.associationsComplete ? 1 : 0,
          next.lastError,
          now,
          completedAt,
          input.prospectId,
        );
    }
    return rowToReceipt(this.requireReceipt(input.prospectId));
  }

  private requestedCandidate(jobId: string): { job: JobRow; receipt: ReceiptRow | null } {
    const job = this.jobs.requireJob(jobId);
    const byJob = this.receiptByJob(job.id);
    if (byJob !== null) return { job, receipt: byJob };
    const profileUrl = requiredProfileUrl(job);
    const byProspect = this.receiptByProspect(prospectIdForProfile(profileUrl));
    if (byProspect !== null) {
      return { job: this.jobs.requireJob(byProspect.job_id), receipt: byProspect };
    }
    assertEligible(job);
    return { job, receipt: null };
  }

  private nextCandidate(): { job: JobRow; receipt: ReceiptRow | null } | null {
    const pending = this.database
      .query<ReceiptRow, []>(
        `SELECT * FROM hubspot_imports
         WHERE completed_at IS NULL
         ORDER BY created_at ASC, prospect_id ASC
         LIMIT 1`,
      )
      .get();
    if (pending !== null) return { job: this.jobs.requireJob(pending.job_id), receipt: pending };
    for (const job of this.jobs.listJobs({ withHiringTeam: true, fit: "kept" })) {
      if (job.review !== "approved" || job.company.trim() === "") continue;
      const profileUrl = recipientProfileUrl(job);
      if (profileUrl === null) continue;
      const existing = this.receiptByProspect(prospectIdForProfile(profileUrl));
      if (existing === null) return { job, receipt: null };
    }
    return null;
  }

  private receiptByJob(jobId: string): ReceiptRow | null {
    return this.database
      .query<ReceiptRow, [string]>("SELECT * FROM hubspot_imports WHERE job_id = ?")
      .get(jobId);
  }

  private receiptByProspect(prospectId: string): ReceiptRow | null {
    return this.database
      .query<ReceiptRow, [string]>("SELECT * FROM hubspot_imports WHERE prospect_id = ?")
      .get(prospectId);
  }

  private requireReceipt(prospectId: string): ReceiptRow {
    const row = this.receiptByProspect(prospectId);
    if (row === null) {
      throw new CliError(
        "HUBSPOT_IMPORT_NOT_FOUND",
        `no HubSpot import exists for ${prospectId}; run jobs hubspot-next first`,
        { exitCode: 2 },
      );
    }
    return row;
  }
}

export function prospectIdForProfile(profileUrl: string): string {
  const normalized = normalizeProfileUrl(profileUrl);
  if (normalized === "") {
    throw new CliError("JOBS_NO_HIRING_TEAM", "a usable hiring-team profile URL is required", {
      exitCode: 2,
    });
  }
  const hash = createHash("sha256").update(normalized).digest("hex");
  return `co:need-led:v1:${hash}`;
}

function assertEligible(job: JobRow): void {
  if (job.fit !== "kept") {
    throw new CliError("HUBSPOT_NOT_KEPT", `job ${job.id} is not kept`, { exitCode: 2 });
  }
  if (job.review !== "approved") {
    throw new CliError("HUBSPOT_NOT_APPROVED", `job ${job.id} is not approved`, { exitCode: 2 });
  }
  if (job.company.trim() === "") {
    throw new CliError("HUBSPOT_COMPANY_REQUIRED", `job ${job.id} has no company`, { exitCode: 2 });
  }
  requiredProfileUrl(job);
}

function requiredProfileUrl(job: JobRow): string {
  const profileUrl = recipientProfileUrl(job);
  if (profileUrl === null) {
    throw new CliError(
      "JOBS_NO_HIRING_TEAM",
      `job ${job.id} has no usable hiring-team profile URL`,
      { exitCode: 2 },
    );
  }
  return profileUrl;
}

function rowToReceipt(row: ReceiptRow): HubSpotImportReceipt {
  return {
    prospectId: row.prospect_id,
    jobId: row.job_id,
    companyId: row.company_id,
    contactId: row.contact_id,
    dealId: row.deal_id,
    taskId: row.task_id,
    associationsComplete: row.associations_complete === 1,
    lastError: row.last_error,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    completedAt: row.completed_at,
  };
}

function hasSuccessReceipt(input: HubSpotRecordInput): boolean {
  return (
    input.companyId !== undefined ||
    input.contactId !== undefined ||
    input.dealId !== undefined ||
    input.taskId !== undefined ||
    input.associationsComplete === true
  );
}

function buildPacket(job: JobRow, receipt: HubSpotImportReceipt): Record<string, unknown> {
  const member = job.hiringTeam[0];
  if (member === undefined) throw new CliError("JOBS_NO_HIRING_TEAM", "hiring team missing");
  const name = splitName(member.name);
  const marker = `${receipt.prospectId}:day-1`;
  const route = outreachKindFor(job);
  const hasReceipt =
    receipt.companyId !== null ||
    receipt.contactId !== null ||
    receipt.dealId !== null ||
    receipt.taskId !== null ||
    receipt.associationsComplete;
  return {
    command: "jobs hubspot-next",
    state:
      receipt.completedAt !== null
        ? "complete"
        : receipt.lastError !== null
          ? "blocked"
          : hasReceipt
            ? "partial"
            : "pending",
    prospect: {
      prospectId: receipt.prospectId,
      lane: "Need-led",
      person: {
        name: member.name,
        firstName: name.firstName,
        lastName: name.lastName,
        headline: member.headline,
        linkedinUrl: requiredProfileUrl(job),
      },
      company: job.company,
      opportunity: {
        jobId: job.id,
        title: job.title,
        sourceUrl: job.postingUrl,
        employmentType: job.employmentType,
        postedAt: job.postedAt,
        matchedTerm: job.matchedTerm,
        filterReason: job.filterReason,
        workSummary: job.workSummary,
        productSummary: job.productSummary,
        outreachKind: route,
        application: {
          required: route === "application_followup",
          appliedAt: job.appliedAt,
          applicationUrl: job.applicationUrl,
        },
      },
    },
    receipt,
    hubspot: {
      portalId: HUBSPOT_PORTAL_ID,
      retryRule: "Always lookup before create. Two or more matches block the import.",
      company: {
        match: { property: "name", operator: "EQ", value: job.company.trim() },
        zeroMatches: { action: "create", properties: { name: job.company.trim() } },
        oneMatch: { action: "reuse", updatePolicy: "fill blank identity fields only" },
        multipleMatches: "block",
      },
      contact: {
        match: { property: "hs_linkedin_url", operator: "EQ", value: requiredProfileUrl(job) },
        zeroMatches: {
          action: "create",
          properties: {
            firstname: name.firstName,
            ...(name.lastName === "" ? {} : { lastname: name.lastName }),
            ...(member.headline.trim() === "" ? {} : { jobtitle: member.headline.trim() }),
            hs_linkedin_url: requiredProfileUrl(job),
          },
        },
        oneMatch: { action: "reuse", updatePolicy: "fill blank identity fields only" },
        multipleMatches: "block",
      },
      deal: {
        match: {
          property: "contract_outreach_local_prospect_id",
          operator: "EQ",
          value: receipt.prospectId,
        },
        zeroMatches: {
          action: "create",
          properties: {
            dealname: `${job.company.trim()} — ${member.name} — ${job.title}`,
            pipeline: HUBSPOT_PIPELINE_ID,
            dealstage: HUBSPOT_INITIAL_STAGE_ID,
            contract_outreach_lane: "Need-led",
            contract_outreach_source_url: job.postingUrl,
            contract_outreach_local_prospect_id: receipt.prospectId,
          },
        },
        oneMatch: { action: "reuse", updatePolicy: "integration-owned properties only" },
        multipleMatches: "block",
      },
      associations: {
        type: "default",
        required: [
          "company-contact",
          "deal-company",
          "deal-contact",
          "task-company",
          "task-contact",
          "task-deal",
        ],
      },
      task: {
        marker,
        match: "Search associated tasks for the exact marker before create; ambiguity blocks.",
        properties: {
          hs_task_subject:
            route === "application_followup"
              ? job.appliedAt === null
                ? `Day 1: Submit application — ${member.name} at ${job.company.trim()}`
                : `Day 1: Send application follow-up — ${member.name} at ${job.company.trim()}`
              : `Day 1: Offer short-term contract help — ${member.name} at ${job.company.trim()}`,
          hs_task_body: [
            `Import marker: ${marker}`,
            `LinkedIn: ${requiredProfileUrl(job)}`,
            `Job: ${job.postingUrl}`,
            `Role: ${job.title}`,
            `Route: ${route}`,
            route === "application_followup"
              ? job.appliedAt === null
                ? "Action: apply first; after the application is recorded, send the application follow-up."
                : `Action: send the application follow-up; applied at ${job.appliedAt}${job.applicationUrl === null ? "" : ` (${job.applicationUrl})`}.`
              : "Action: offer short-term contract help while the company fills the full-time role.",
            "After sending, HubSpot owns follow-up timing and stops on reply.",
          ].join("\n"),
          hs_task_status: "NOT_STARTED",
          hs_task_type:
            route === "application_followup" && job.appliedAt === null
              ? "TODO"
              : "LINKED_IN_CONNECT",
          hs_timestamp: receipt.createdAt,
        },
      },
    },
  };
}

function splitName(value: string): { firstName: string; lastName: string } {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  return { firstName: parts[0] ?? value.trim(), lastName: parts.slice(1).join(" ") };
}
