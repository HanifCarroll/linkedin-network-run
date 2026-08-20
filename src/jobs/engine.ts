import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";
import { groupJobs, outreachKindFor, primaryRoleFor } from "../view/grouping.ts";
import { recipientProfileUrl } from "./recipient.ts";
import type {
  CapturedJob,
  CollectedJob,
  HiringTeamMember,
  JobEnrichment,
  JobRow,
  JobStatus,
  ReviewDecision,
} from "./types.ts";
import {
  CLASSIFICATION_MAX_LENGTH,
  DRAFT_MAX_LENGTH,
  SUBJECT_MAX_LENGTH,
  SUMMARY_MAX_LENGTH,
  TRIAGE_POLICY_VERSION,
  type TriageBucket,
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
  readonly enrichment_outcome: string;
  readonly enrichment_captured_at: string | null;
  readonly enrichment_parser_version: string;
  readonly enrichment_evidence_json: string;
  readonly company_profile_url: string;
  readonly company_evidence_json: string;
  readonly external_application_url: string;
  readonly applicant_tracking_system: string;
  readonly geo_id: string;
  readonly work_focus: string;
  readonly product_system: string;
  readonly work_summary: string;
  readonly product_summary: string;
  readonly subject: string;
  readonly review: string;
  readonly fit: string;
  readonly filter_reason: string;
  readonly matched_term: string;
  readonly filter_policy_version: string;
  readonly filtered_at: string | null;
  readonly triage_bucket: string;
  readonly company_summary: string;
  readonly responsibilities_json: string;
  readonly skill_matches_json: string;
  readonly skill_gaps_json: string;
  readonly triage_reason: string;
  readonly triage_policy_version: string;
  readonly triaged_at: string | null;
  readonly applied_at: string | null;
  readonly application_url: string | null;
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
        const current = this.requireJob(job.id);
        const profile = recipientProfileUrl(current);
        if (profile !== null && current.review === "needs_review") {
          const siblings = this.listJobs({ withHiringTeam: false }).filter(
            (candidate) =>
              candidate.id !== current.id && recipientProfileUrl(candidate) === profile,
          );
          const rejected = siblings.some((candidate) => candidate.review === "skipped");
          const covered = siblings.some(
            (candidate) => candidate.review === "approved" || candidate.status === "sent",
          );
          if (rejected && !covered) {
            this.database
              .prepare(`UPDATE jobs SET review = 'skipped', updated_at = ? WHERE id = ?`)
              .run(now, current.id);
          }
        }
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
  recordEnrichment(enrichment: JobEnrichment, now: string): JobRow {
    const current = this.requireJob(enrichment.id);
    if (current.fit === "dropped")
      throw new CliError(
        "JOB_NOT_ELIGIBLE",
        `job ${enrichment.id} was dropped and cannot be enriched`,
        { exitCode: 2 },
      );
    if (
      normalizeJobUrl(current.postingUrl) !== normalizeJobUrl(enrichment.sourceUrl) ||
      normalizeJobUrl(enrichment.postingUrl) !== normalizeJobUrl(enrichment.sourceUrl)
    ) {
      throw new CliError(
        "JOBS_SOURCE_MISMATCH",
        `enrichment source does not match job ${enrichment.id}`,
        { exitCode: 2 },
      );
    }
    const complete =
      enrichment.outcome === "complete_hiring_team" ||
      enrichment.outcome === "complete_no_hiring_team";
    if (enrichment.rawResponses && enrichment.rawResponses.length > 4)
      throw new CliError("INVALID_ARGUMENT", "at most four raw enrichment responses are allowed", {
        exitCode: 2,
      });
    for (const response of enrichment.rawResponses ?? []) {
      if (
        response.body.length > 1_000_000 ||
        (response.component !== "document" && response.body.length > 120_000)
      )
        throw new CliError("INVALID_ARGUMENT", "raw enrichment response is too large", {
          exitCode: 2,
        });
      if (response.status < 100 || response.status > 599)
        throw new CliError("INVALID_ARGUMENT", "raw enrichment response has invalid status", {
          exitCode: 2,
        });
    }
    if (complete && enrichment.description.trim() === "")
      throw new CliError(
        "JOBS_ENRICHMENT_INCOMPLETE",
        `complete enrichment for ${enrichment.id} requires a description`,
        { exitCode: 2 },
      );
    if (enrichment.outcome === "complete_hiring_team" && enrichment.hiringTeam.length === 0)
      throw new CliError(
        "JOBS_ENRICHMENT_INCOMPLETE",
        `hiring-team outcome for ${enrichment.id} requires members`,
        { exitCode: 2 },
      );
    if (enrichment.outcome === "complete_no_hiring_team" && enrichment.hiringTeam.length !== 0)
      throw new CliError(
        "JOBS_ENRICHMENT_INVALID",
        `no-team outcome for ${enrichment.id} cannot include members`,
        { exitCode: 2 },
      );
    const peopleResponse = (enrichment.rawResponses ?? []).find(
      (response) => response.component === "peopleWhoCanHelp",
    );
    if (enrichment.outcome === "complete_no_hiring_team" && !peopleResponse)
      throw new CliError(
        "JOBS_ENRICHMENT_INVALID",
        `no-team outcome for ${enrichment.id} requires a captured peopleWhoCanHelp response`,
        { exitCode: 2 },
      );
    if (
      enrichment.outcome === "complete_no_hiring_team" &&
      (peopleResponse?.status !== 200 || /meet the hiring team/i.test(peopleResponse.body))
    )
      throw new CliError(
        "JOBS_ENRICHMENT_INVALID",
        `no-team outcome for ${enrichment.id} requires a conclusive empty hiring-team response`,
        { exitCode: 2 },
      );
    const same = enrichmentMatches(current, enrichment);
    if (same) {
      for (const response of enrichment.rawResponses ?? []) {
        this.database
          .prepare(
            `INSERT INTO job_enrichment_responses (job_id, source_url, response_url, status, component, captured_at, parser_version, body, body_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, component) DO UPDATE SET source_url=excluded.source_url, response_url=excluded.response_url, status=excluded.status, captured_at=excluded.captured_at, parser_version=excluded.parser_version, body=excluded.body, body_bytes=excluded.body_bytes`,
          )
          .run(
            enrichment.id,
            response.sourceUrl,
            response.responseUrl,
            response.status,
            response.component,
            response.capturedAt,
            response.parserVersion,
            response.body,
            new TextEncoder().encode(response.body).byteLength,
          );
      }
      return current;
    }
    if (current.enrichmentOutcome !== "retry_required" || current.triageBucket !== "pending") {
      throw new CliError(
        "JOBS_ENRICHMENT_CONFLICT",
        `enrichment for ${enrichment.id} would overwrite completed or triaged state`,
        { exitCode: 2 },
      );
    }
    if (enrichment.outcome === "retry_required") {
      this.database
        .prepare(
          `UPDATE jobs SET enrichment_outcome=?, enrichment_captured_at=?, enrichment_parser_version=?, enrichment_evidence_json=?, external_application_url=?, applicant_tracking_system=?, geo_id=?, updated_at=? WHERE id=?`,
        )
        .run(
          enrichment.outcome,
          enrichment.capturedAt,
          enrichment.parserVersion,
          JSON.stringify(enrichment.sourceEvidence),
          enrichment.externalApplicationUrl,
          enrichment.applicantTrackingSystem,
          enrichment.geoId,
          now,
          enrichment.id,
        );
      for (const response of enrichment.rawResponses ?? []) {
        this.database
          .prepare(
            `INSERT INTO job_enrichment_responses (job_id, source_url, response_url, status, component, captured_at, parser_version, body, body_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, component) DO UPDATE SET source_url=excluded.source_url, response_url=excluded.response_url, status=excluded.status, captured_at=excluded.captured_at, parser_version=excluded.parser_version, body=excluded.body, body_bytes=excluded.body_bytes`,
          )
          .run(
            enrichment.id,
            response.sourceUrl,
            response.responseUrl,
            response.status,
            response.component,
            response.capturedAt,
            response.parserVersion,
            response.body,
            new TextEncoder().encode(response.body).byteLength,
          );
      }
      return this.requireJob(enrichment.id);
    }
    const saved =
      enrichment.outcome === "closed"
        ? {
            ...enrichment,
            title: enrichment.title || current.title,
            company: enrichment.company || current.company,
            location: enrichment.location || current.location,
            postingUrl: current.postingUrl,
            description: enrichment.description || current.description,
            workplaceType: enrichment.workplaceType || current.workplaceType,
            employmentType: enrichment.employmentType || current.employmentType,
            applyMethod: enrichment.applyMethod || current.applyMethod,
            postedAt: enrichment.postedAt || current.postedAt,
            applicantCount: enrichment.applicantCount || current.applicantCount,
            benefits: enrichment.benefits.length ? enrichment.benefits : current.benefits,
            hiringTeam: enrichment.hiringTeam.length ? enrichment.hiringTeam : current.hiringTeam,
            companyProfileUrl: enrichment.companyProfileUrl || current.companyProfileUrl,
            companyEvidence: enrichment.companyEvidence.length
              ? enrichment.companyEvidence
              : current.companyEvidence,
          }
        : enrichment;
    this.database
      .prepare(
        `UPDATE jobs SET title=?, company=?, location=?, posting_url=?, description=?, workplace_type=?, employment_type=?, apply_method=?, promoted=?, actively_reviewing=?, posted_at=?, applicant_count=?, benefits_json=?, hiring_team_json=?, has_hiring_team=?, enrichment_outcome=?, enrichment_captured_at=?, enrichment_parser_version=?, enrichment_evidence_json=?, company_profile_url=?, company_evidence_json=?, external_application_url=?, applicant_tracking_system=?, geo_id=?, updated_at=? WHERE id=?`,
      )
      .run(
        saved.title,
        saved.company,
        saved.location,
        normalizeJobUrl(saved.postingUrl),
        saved.description,
        saved.workplaceType,
        saved.employmentType,
        saved.applyMethod,
        saved.promoted ? 1 : 0,
        saved.activelyReviewing ? 1 : 0,
        saved.postedAt,
        saved.applicantCount,
        JSON.stringify(saved.benefits),
        JSON.stringify(saved.hiringTeam),
        saved.hiringTeam.length > 0 ? 1 : 0,
        saved.outcome,
        saved.capturedAt,
        saved.parserVersion,
        JSON.stringify(saved.sourceEvidence),
        saved.companyProfileUrl,
        JSON.stringify(saved.companyEvidence),
        saved.externalApplicationUrl,
        saved.applicantTrackingSystem,
        saved.geoId,
        now,
        saved.id,
      );
    for (const response of enrichment.rawResponses ?? []) {
      this.database
        .prepare(
          `INSERT INTO job_enrichment_responses (job_id, source_url, response_url, status, component, captured_at, parser_version, body, body_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, component) DO UPDATE SET source_url=excluded.source_url, response_url=excluded.response_url, status=excluded.status, captured_at=excluded.captured_at, parser_version=excluded.parser_version, body=excluded.body, body_bytes=excluded.body_bytes`,
        )
        .run(
          enrichment.id,
          response.sourceUrl,
          response.responseUrl,
          response.status,
          response.component,
          response.capturedAt,
          response.parserVersion,
          response.body,
          new TextEncoder().encode(response.body).byteLength,
        );
    }
    return this.requireJob(enrichment.id);
  }

  listJobs(options: {
    readonly status?: JobStatus;
    readonly withHiringTeam: boolean;
    readonly uncheckedOnly?: boolean;
    readonly needsDetail?: boolean;
    readonly fit?: "pending" | "kept" | "dropped";
    readonly triageBucket?: TriageBucket;
    readonly jobIds?: readonly string[];
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
    if (options.fit !== undefined) {
      clauses.push("fit = ?");
      params.push(options.fit);
    }
    if (options.triageBucket !== undefined) {
      clauses.push("triage_bucket = ?");
      params.push(options.triageBucket);
    }
    if (options.jobIds !== undefined) {
      if (options.jobIds.length === 0) return [];
      clauses.push(`id IN (${options.jobIds.map(() => "?").join(",")})`);
      params.push(...options.jobIds);
    }
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
    return rows
      .map(rowToJob)
      .filter((job) => outreachKindFor(job) === "direct" || job.appliedAt !== null);
  }

  recordApplied(
    id: string,
    applicationUrl: string | undefined,
    appliedAt: string,
    now: string,
  ): JobRow {
    const current = this.requireJob(id);
    if (outreachKindFor(current) !== "application_followup")
      throw new CliError(
        "JOBS_APPLICATION_NOT_REQUIRED",
        `job ${id} does not require an application checkpoint`,
        { exitCode: 2 },
      );
    if (current.status === "sent")
      throw new CliError("JOBS_ALREADY_SENT", `job ${id} was already sent`, { exitCode: 2 });
    const normalizedAppliedAt = normalizeAppliedAt(appliedAt);
    if (normalizedAppliedAt === null)
      throw new CliError("INVALID_ARGUMENT", "appliedAt must be a valid ISO timestamp", {
        exitCode: 2,
      });
    let normalizedUrl: string | null = current.applicationUrl;
    if (applicationUrl !== undefined) {
      try {
        const url = new URL(applicationUrl);
        if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error();
        normalizedUrl = url.toString();
      } catch {
        throw new CliError("INVALID_ARGUMENT", "applicationUrl must be an HTTP(S) URL", {
          exitCode: 2,
        });
      }
    }
    this.database
      .prepare(`UPDATE jobs SET applied_at = ?, application_url = ?, updated_at = ? WHERE id = ?`)
      .run(normalizedAppliedAt, normalizedUrl, now, id);
    return this.requireJob(id);
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
    const update = this.database.prepare(`UPDATE jobs SET review = ?, updated_at = ? WHERE id = ?`);
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

  draftNext(id?: string): {
    readonly packet: {
      readonly job: JobRow;
      readonly route: "direct" | "application_followup";
      readonly person: HiringTeamMember;
      readonly company: {
        readonly name: string;
        readonly profileUrl: string;
        readonly evidence: readonly string[];
      };
      readonly jobEvidence: Record<string, unknown>;
      readonly writingInstructions: readonly string[];
    } | null;
    readonly blockedApplications: number;
  } {
    const jobs = this.listJobs({ withHiringTeam: true, fit: "kept" });
    const groups = groupJobs(jobs).filter((group) => {
      const primary = primaryRoleFor(group.jobs);
      return (
        recipientProfileUrl(primary) !== null &&
        group.jobs.every((job) => job.status !== "sent" && job.message === null) &&
        primary.review === "approved" &&
        primary.triageBucket !== "pending"
      );
    });
    let blockedApplications = 0;
    for (const group of groups) {
      const selected =
        id === undefined ? primaryRoleFor(group.jobs) : group.jobs.find((job) => job.id === id);
      if (selected === undefined || selected.review !== "approved") continue;
      const route = outreachKindFor(selected);
      if (route === "application_followup" && selected.appliedAt === null) {
        blockedApplications += 1;
        if (id !== undefined)
          throw new CliError(
            "JOB_NOT_ELIGIBLE",
            `job ${id} requires jobs applied before drafting`,
            { exitCode: 2 },
          );
        continue;
      }
      const person = selected.hiringTeam[0];
      if (person === undefined) continue;
      const proof =
        selected.skillMatches[0] ?? selected.responsibilities[0] ?? selected.workSummary;
      return {
        blockedApplications,
        packet: {
          job: selected,
          route,
          person,
          company: {
            name: selected.company,
            profileUrl: selected.companyProfileUrl,
            evidence: selected.companyEvidence,
          },
          jobEvidence: {
            title: selected.title,
            company: selected.company,
            location: selected.location,
            postingUrl: selected.postingUrl,
            employmentType: selected.employmentType,
            description: selected.description,
            responsibilities: selected.responsibilities,
            skillMatches: selected.skillMatches,
            companyEvidence: selected.companyEvidence,
          },
          writingInstructions:
            route === "application_followup"
              ? [
                  "Name the role and company.",
                  `Use one specific stored proof: ${proof || "choose one concrete stored fit detail"}.`,
                  "Put a name to the application; do not ask about contract help or request a call.",
                ]
              : [
                  "Offer short-term contract help while the full-time role is being filled.",
                  "Use one concrete stored job or company detail and connect it to one stored proof.",
                  "Keep it concise and do not invent evidence.",
                ],
        },
      };
    }
    if (id !== undefined)
      throw new CliError("JOB_NOT_ELIGIBLE", `job ${id} is not an eligible draft opportunity`, {
        exitCode: 2,
      });
    return { packet: null, blockedApplications };
  }

  triageNext(runId?: string): JobRow | null {
    const ids = runId === undefined ? undefined : this.jobIdsForRun(runId);
    const rows = this.listJobs({
      withHiringTeam: true,
      fit: "kept",
      triageBucket: "pending",
      ...(ids === undefined ? {} : { jobIds: ids }),
    }).filter(
      (job) =>
        job.review === "needs_review" && job.status !== "sent" && job.description.trim() !== "",
    );
    return rows[0] ?? null;
  }

  recordTriage(input: {
    id: string;
    bucket: Exclude<TriageBucket, "pending">;
    companySummary: string;
    workSummary: string;
    responsibilities: readonly string[];
    skillMatches: readonly string[];
    skillGaps: readonly string[];
    reason: string;
    policyVersion: string;
    now: string;
  }): JobRow {
    const companySummary = input.companySummary.trim();
    const workSummary = input.workSummary.trim();
    const reason = input.reason.trim();
    const responsibilities = normalizeTriageList(input.responsibilities, "responsibilities", 5, 1);
    const skillMatches = normalizeTriageList(input.skillMatches, "skill matches", 8);
    const skillGaps = normalizeTriageList(input.skillGaps, "skill gaps", 8);
    if (
      !companySummary ||
      !workSummary ||
      !reason ||
      companySummary.length > 500 ||
      workSummary.length > 500 ||
      reason.length > 500
    ) {
      throw new CliError(
        "INVALID_ARGUMENT",
        "triage company summary, work summary, and reason must be non-empty and at most 500 characters",
        { exitCode: 2 },
      );
    }
    if (input.bucket !== "strong" && input.bucket !== "possible" && input.bucket !== "weak") {
      throw new CliError("INVALID_ARGUMENT", "triage bucket must be strong, possible, or weak", {
        exitCode: 2,
      });
    }
    if (input.policyVersion !== TRIAGE_POLICY_VERSION)
      throw new CliError(
        "JOBS_TRIAGE_POLICY_VERSION",
        `triage policy version must be ${TRIAGE_POLICY_VERSION}`,
        { exitCode: 2 },
      );
    const current = this.requireJob(input.id);
    const same =
      current.triageBucket !== "pending" &&
      current.triageBucket === input.bucket &&
      current.companySummary === companySummary &&
      current.workSummary === workSummary &&
      JSON.stringify(current.responsibilities) === JSON.stringify(responsibilities) &&
      JSON.stringify(current.skillMatches) === JSON.stringify(skillMatches) &&
      JSON.stringify(current.skillGaps) === JSON.stringify(skillGaps) &&
      current.triageReason === reason &&
      current.triagePolicyVersion === input.policyVersion;
    if (same) return current;
    if (
      current.triageBucket === "pending" &&
      (current.fit !== "kept" ||
        !current.hasHiringTeam ||
        current.description.trim() === "" ||
        current.review !== "needs_review" ||
        current.status === "sent")
    ) {
      throw new CliError("JOBS_TRIAGE_NOT_ELIGIBLE", `job ${input.id} is not eligible for triage`, {
        exitCode: 2,
      });
    }
    if (
      current.triageBucket !== "pending" ||
      current.fit !== "kept" ||
      !current.hasHiringTeam ||
      current.description.trim() === "" ||
      current.review !== "needs_review" ||
      current.status === "sent"
    ) {
      throw new CliError(
        "JOBS_TRIAGE_CONFLICT",
        `job ${input.id} already has a different triage result or is not eligible`,
        { exitCode: 2 },
      );
    }
    this.database
      .prepare(
        `UPDATE jobs SET triage_bucket=?, company_summary=?, work_summary=?, responsibilities_json=?, skill_matches_json=?, skill_gaps_json=?, triage_reason=?, triage_policy_version=?, triaged_at=?, updated_at=? WHERE id=?`,
      )
      .run(
        input.bucket,
        companySummary,
        workSummary,
        JSON.stringify(responsibilities),
        JSON.stringify(skillMatches),
        JSON.stringify(skillGaps),
        reason,
        input.policyVersion,
        input.now,
        input.now,
        input.id,
      );
    return this.requireJob(input.id);
  }

  private jobIdsForRun(runId: string): string[] {
    return this.database
      .query<{ job_id: string }, [string]>(
        "SELECT DISTINCT job_id FROM job_observations WHERE run_id = ? ORDER BY job_id",
      )
      .all(runId)
      .map((row) => row.job_id);
  }

  classifyJob(
    id: string,
    workFocus: string,
    productSystem: string,
    workSummary: string,
    productSummary: string,
    now: string,
  ): JobRow {
    const current = this.requireJob(id);
    if (current.triageBucket !== "pending") {
      throw new CliError(
        "JOBS_TRIAGE_CONFLICT",
        `job ${id} is already triaged; classification cannot replace its fit brief`,
        { exitCode: 2 },
      );
    }
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

function normalizeTriageList(
  values: readonly string[],
  label: string,
  maximum: number,
  minimum = 0,
): string[] {
  if (values.length < minimum || values.length > maximum) {
    throw new CliError(
      "INVALID_ARGUMENT",
      `triage ${label} must contain between ${minimum} and ${maximum} items`,
      { exitCode: 2 },
    );
  }
  const normalized = values.map((value) => value.trim());
  if (normalized.some((value) => value.length === 0 || value.length > 200)) {
    throw new CliError(
      "INVALID_ARGUMENT",
      `triage ${label} items must be non-empty and at most 200 characters`,
      { exitCode: 2 },
    );
  }
  if (new Set(normalized).size !== normalized.length) {
    throw new CliError("INVALID_ARGUMENT", `triage ${label} must not contain duplicate items`, {
      exitCode: 2,
    });
  }
  return normalized;
}

export function normalizeJobUrl(value: string): string {
  try {
    const url = new URL(value);
    url.hash = "";
    url.search = "";
    url.hostname = url.hostname.toLowerCase().replace(/^www\./, "");
    url.pathname = url.pathname.replace(/\/+$/, "/");
    return url.toString();
  } catch {
    return value
      .trim()
      .replace(/[?#].*$/, "")
      .replace(/\/+$/, "/");
  }
}

function enrichmentMatches(current: JobRow, incoming: JobEnrichment): boolean {
  const metadata =
    current.enrichmentOutcome === incoming.outcome &&
    current.enrichmentCapturedAt === incoming.capturedAt &&
    current.enrichmentParserVersion === incoming.parserVersion &&
    JSON.stringify(current.enrichmentEvidence) === JSON.stringify(incoming.sourceEvidence) &&
    normalizeJobUrl(current.postingUrl) === normalizeJobUrl(incoming.sourceUrl);
  if (!metadata) return false;
  if (incoming.outcome === "closed")
    return (
      (!incoming.title || incoming.title === current.title) &&
      (!incoming.company || incoming.company === current.company) &&
      (!incoming.location || incoming.location === current.location) &&
      (!incoming.description || incoming.description === current.description) &&
      (!incoming.postingUrl ||
        normalizeJobUrl(incoming.postingUrl) === normalizeJobUrl(current.postingUrl))
    );
  return (
    current.title === incoming.title &&
    current.company === incoming.company &&
    current.location === incoming.location &&
    current.description === incoming.description &&
    current.workplaceType === incoming.workplaceType &&
    current.employmentType === incoming.employmentType &&
    current.applyMethod === incoming.applyMethod &&
    current.promoted === incoming.promoted &&
    current.activelyReviewing === incoming.activelyReviewing &&
    current.postedAt === incoming.postedAt &&
    current.applicantCount === incoming.applicantCount &&
    JSON.stringify(current.benefits) === JSON.stringify(incoming.benefits) &&
    JSON.stringify(current.hiringTeam) === JSON.stringify(incoming.hiringTeam) &&
    current.companyProfileUrl === incoming.companyProfileUrl &&
    JSON.stringify(current.companyEvidence) === JSON.stringify(incoming.companyEvidence) &&
    current.externalApplicationUrl === incoming.externalApplicationUrl &&
    current.applicantTrackingSystem === incoming.applicantTrackingSystem &&
    current.geoId === incoming.geoId
  );
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

function normalizeAppliedAt(value: string): string | null {
  const trimmed = value.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    const parsed = new Date(`${trimmed}T00:00:00.000Z`);
    return Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== trimmed
      ? null
      : trimmed;
  }
  if (!/^\d{4}-\d{2}-\d{2}T/.test(trimmed)) return null;
  const parsed = new Date(trimmed);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function rowToJob(row: JobRowRaw): JobRow {
  let hiringTeam: readonly HiringTeamMember[] = [];
  try {
    const parsed = JSON.parse(row.hiring_team_json) as unknown;
    if (Array.isArray(parsed)) hiringTeam = parsed as HiringTeamMember[];
  } catch {
    hiringTeam = [];
  }
  const strings = (value: string): readonly string[] => {
    try {
      const parsed = JSON.parse(value) as unknown;
      return Array.isArray(parsed)
        ? parsed.filter((item): item is string => typeof item === "string")
        : [];
    } catch {
      return [];
    }
  };
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
    enrichmentOutcome: (row.enrichment_outcome || "retry_required") as JobRow["enrichmentOutcome"],
    enrichmentCapturedAt: row.enrichment_captured_at,
    enrichmentParserVersion: row.enrichment_parser_version,
    enrichmentEvidence: strings(row.enrichment_evidence_json),
    companyProfileUrl: row.company_profile_url,
    companyEvidence: strings(row.company_evidence_json),
    externalApplicationUrl: row.external_application_url,
    applicantTrackingSystem: row.applicant_tracking_system,
    geoId: row.geo_id,
    workFocus: row.work_focus,
    productSystem: row.product_system,
    workSummary: row.work_summary,
    productSummary: row.product_summary,
    subject: row.subject,
    review: row.review as ReviewDecision,
    fit: row.fit as "pending" | "kept" | "dropped",
    filterReason: row.filter_reason,
    matchedTerm: row.matched_term,
    filterPolicyVersion: row.filter_policy_version,
    filteredAt: row.filtered_at,
    triageBucket: row.triage_bucket as JobRow["triageBucket"],
    companySummary: row.company_summary,
    responsibilities: strings(row.responsibilities_json),
    skillMatches: strings(row.skill_matches_json),
    skillGaps: strings(row.skill_gaps_json),
    triageReason: row.triage_reason,
    triagePolicyVersion: row.triage_policy_version,
    triagedAt: row.triaged_at,
    appliedAt: row.applied_at,
    applicationUrl: row.application_url,
  };
}

export { normalizeProfileUrl, recipientProfileUrl } from "./recipient.ts";
