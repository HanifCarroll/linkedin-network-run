import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";
import { outreachKindFor } from "../view/grouping.ts";
import { JobsEngine } from "./engine.ts";
import type { JobRow } from "./types.ts";

type HubSpotRow = {
  prospect_id: string;
  job_id: string;
  company_id: string | null;
  contact_id: string | null;
  deal_id: string | null;
  completed_at: string | null;
};
type InstantlyRow = {
  email: string | null;
  no_email: number;
  completed_at: string | null;
  campaign_stop_on_reply: number;
};
type ReceiptRow = {
  prospect_id: string;
  job_id: string;
  mode: string;
  tasks_json: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type FollowupRecordInput = {
  readonly prospectId: string;
  readonly tasks: readonly {
    stage: string;
    taskId: string;
    associationsComplete: true;
    associations: { companyId: string; contactId: string; dealId: string };
  }[];
};

export class JobsFollowupEngine {
  private readonly jobs: JobsEngine;
  constructor(private readonly database: Database) {
    this.jobs = new JobsEngine(database);
  }

  next(jobId: string | undefined): Record<string, unknown> | null {
    const row =
      jobId === undefined
        ? this.database
            .query<HubSpotRow, []>(
              "SELECT h.* FROM hubspot_imports h JOIN instantly_handoffs i ON i.job_id = h.job_id WHERE h.completed_at IS NOT NULL AND i.completed_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs_followup_receipts f WHERE f.prospect_id = h.prospect_id AND f.completed_at IS NOT NULL) ORDER BY h.created_at, h.prospect_id LIMIT 1",
            )
            .get()
        : (this.database
            .prepare("SELECT * FROM hubspot_imports WHERE job_id = ? OR prospect_id = ? LIMIT 1")
            .get(jobId, jobId) as HubSpotRow | null);
    if (row === null) return null;
    const job = this.jobs.requireJob(row.job_id);
    const instantly = this.database
      .query<InstantlyRow, [string]>(
        "SELECT email, no_email, completed_at, campaign_stop_on_reply FROM instantly_handoffs WHERE job_id = ?",
      )
      .get(row.job_id);
    if (instantly === null || instantly.completed_at === null)
      throw new CliError(
        "FOLLOWUP_INSTANTLY_REQUIRED",
        "Instantly Day 1 handoff must be complete first",
        { exitCode: 2 },
      );
    if (row.company_id === null || row.contact_id === null || row.deal_id === null)
      throw new CliError(
        "FOLLOWUP_HUBSPOT_IDS_REQUIRED",
        "HubSpot company, contact, and deal IDs are required",
        { exitCode: 2 },
      );
    const existing = this.database
      .query<ReceiptRow, [string]>("SELECT * FROM jobs_followup_receipts WHERE prospect_id = ?")
      .get(row.prospect_id);
    return {
      command: "jobs followup-next",
      found: true,
      packet: packet(job, row, instantly, existing),
    };
  }

  record(input: FollowupRecordInput, now: string): Record<string, unknown> {
    const hs = this.database
      .query<HubSpotRow, [string]>("SELECT * FROM hubspot_imports WHERE prospect_id = ?")
      .get(input.prospectId);
    if (hs === null || hs.completed_at === null)
      throw new CliError("FOLLOWUP_HUBSPOT_REQUIRED", "complete the HubSpot Day 1 receipt first", {
        exitCode: 2,
      });
    if (hs.company_id === null || hs.contact_id === null || hs.deal_id === null)
      throw new CliError(
        "FOLLOWUP_HUBSPOT_IDS_REQUIRED",
        "HubSpot company, contact, and deal IDs are required",
        { exitCode: 2 },
      );
    const inst = this.database
      .query<InstantlyRow, [string]>(
        "SELECT email, no_email, completed_at, campaign_stop_on_reply FROM instantly_handoffs WHERE job_id = ?",
      )
      .get(hs.job_id);
    if (inst === null || inst.completed_at === null)
      throw new CliError(
        "FOLLOWUP_INSTANTLY_REQUIRED",
        "Instantly Day 1 handoff must be complete first",
        { exitCode: 2 },
      );
    const expected = inst.no_email === 1 ? ["day-5-7", "day-8-10", "day-12-14"] : ["monitor"];
    if (
      input.tasks.length !== expected.length ||
      input.tasks.some(
        (task, i) =>
          task.stage !== expected[i] ||
          !/^\d{1,40}$/.test(task.taskId) ||
          task.associations.companyId !== hs.company_id ||
          task.associations.contactId !== hs.contact_id ||
          task.associations.dealId !== hs.deal_id,
      )
    )
      throw new CliError(
        "FOLLOWUP_TASKS_INVALID",
        `record exactly ${expected.length} ordered HubSpot task receipts`,
        { exitCode: 2 },
      );
    const existing = this.database
      .query<ReceiptRow, [string]>("SELECT * FROM jobs_followup_receipts WHERE prospect_id = ?")
      .get(input.prospectId);
    if (existing !== null && existing.tasks_json !== JSON.stringify(input.tasks))
      throw new CliError(
        "FOLLOWUP_RECEIPT_CONFLICT",
        "follow-up task receipt conflicts with the existing receipt",
        { exitCode: 2 },
      );
    if (existing === null)
      this.database
        .prepare(
          "INSERT INTO jobs_followup_receipts (prospect_id, job_id, mode, tasks_json, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )
        .run(
          input.prospectId,
          hs.job_id,
          inst.no_email === 1 ? "linkedin_sequence" : "instantly_monitor",
          JSON.stringify(input.tasks),
          now,
          now,
          now,
        );
    else
      this.database
        .prepare(
          "UPDATE jobs_followup_receipts SET updated_at = ?, completed_at = COALESCE(completed_at, ?) WHERE prospect_id = ?",
        )
        .run(now, now, input.prospectId);
    return this.receipt(input.prospectId);
  }

  private receipt(prospectId: string): Record<string, unknown> {
    const row = this.database
      .query<ReceiptRow, [string]>("SELECT * FROM jobs_followup_receipts WHERE prospect_id = ?")
      .get(prospectId);
    if (row === null)
      throw new CliError("FOLLOWUP_NOT_FOUND", "follow-up receipt was not stored", { exitCode: 1 });
    return {
      prospectId: row.prospect_id,
      jobId: row.job_id,
      mode: row.mode,
      tasks: JSON.parse(row.tasks_json),
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      completedAt: row.completed_at,
    };
  }
}

function packet(
  job: JobRow,
  hs: HubSpotRow,
  inst: InstantlyRow,
  existing: ReceiptRow | null,
): Record<string, unknown> {
  const person = job.hiringTeam[0];
  if (!person) throw new CliError("JOBS_NO_HIRING_TEAM", "hiring team missing", { exitCode: 2 });
  const email = inst.email !== null && inst.email.trim() !== "";
  if (hs.company_id === null || hs.contact_id === null || hs.deal_id === null)
    throw new CliError("FOLLOWUP_HUBSPOT_IDS_REQUIRED", "HubSpot IDs are required", {
      exitCode: 2,
    });
  const associations = { companyId: hs.company_id, contactId: hs.contact_id, dealId: hs.deal_id };
  const anchor = new Date(
    Math.max(Date.parse(hs.completed_at ?? ""), Date.parse(inst.completed_at ?? "")),
  );
  if (Number.isNaN(anchor.getTime()))
    throw new CliError(
      "FOLLOWUP_ANCHOR_INVALID",
      "Day 1 receipts need valid completion timestamps",
      { exitCode: 2 },
    );
  const task = (
    stage: string,
    timing: string,
    offset: number,
    type: "TODO" | "LINKED_IN_MESSAGE",
    action: string,
  ) => {
    const marker = `${hs.prospect_id}:followup:${stage}`;
    const subject = email
      ? `Monitor reply: ${person.name} at ${job.company.trim()}`
      : `LinkedIn follow-up (${timing}): ${person.name} at ${job.company.trim()}`;
    return {
      stage,
      timing,
      marker,
      subject,
      type,
      associations,
      properties: {
        hs_task_body: [
          `Follow-up marker: ${marker}`,
          `LinkedIn: ${person.profileUrl}`,
          `Job: ${job.postingUrl}`,
          action,
        ].join("\n"),
        hs_task_subject: subject,
        hs_task_status: "NOT_STARTED",
        hs_task_type: type,
        hubspot_owner_id: "96636780",
        hs_timestamp: new Date(anchor.getTime() + offset * 86_400_000).toISOString(),
      },
      action,
    };
  };
  const tasks = email
    ? [
        task(
          "monitor",
          "Day 5",
          5,
          "TODO",
          "Check Instantly reply/stop_on_reply status; do not create or send a duplicate email.",
        ),
      ]
    : [
        task(
          "day-5-7",
          "Days 5-7",
          5,
          "LINKED_IN_MESSAGE",
          "Check for any reply; if there is one, stop and complete all remaining follow-up tasks. Check connection state before action: send a DM only when connected, otherwise send InMail.",
        ),
        task(
          "day-8-10",
          "Days 8-10",
          8,
          "LINKED_IN_MESSAGE",
          "Check for any reply; if there is one, stop and complete all remaining follow-up tasks. Check connection state before action: send a DM only when connected, otherwise send InMail.",
        ),
        task(
          "day-12-14",
          "Days 12-14",
          12,
          "LINKED_IN_MESSAGE",
          "Check for any reply; if there is one, stop and complete all remaining follow-up tasks. Check connection state before action: send a DM only when connected, otherwise send InMail.",
        ),
      ];
  return {
    command: "jobs followup-next",
    state: existing?.completed_at ? "complete" : "pending",
    prospect: {
      prospectId: hs.prospect_id,
      jobId: job.id,
      person: { name: person.name, linkedinUrl: person.profileUrl },
      company: job.company,
      title: job.title,
      route: outreachKindFor(job),
    },
    receipt: existing
      ? { tasks: JSON.parse(existing.tasks_json), completedAt: existing.completed_at }
      : null,
    hubspot: {
      portalId: "51829251",
      existingIds: { companyId: hs.company_id, contactId: hs.contact_id, dealId: hs.deal_id },
      taskPolicy:
        "lookup exact marker before create; associate every task to company, contact, and deal",
      tasks,
      stopOnReply: true,
    },
  };
}
