import { recipientProfileUrl } from "../jobs/recipient.ts";
import type { JobRow } from "../jobs/types.ts";

/** A recipient bucket folds the whole group into one review decision. */
export type Bucket = "sent" | "approved" | "skipped" | "needs_review";

/** Which queue section a role belongs to: direct prospecting or post-application follow-up. */
export type OutreachKind = "direct" | "application_followup";

const CONTRACT_TO_HIRE_RE = /\bcontract\s+to\s+hire\b/i;
const C2C_1099_NEGATION_RE =
  /\b(?:not|no|never|without|cannot|can'?t|doesn'?t|don'?t|isn'?t|aren'?t|won'?t|exclud(?:es|ing|ed))\b[^.\n]{0,40}\b(?:c2c|1099)\b/i;
const C2C_1099_POSITIVE_RE =
  /\b(?:open\s+to|open\s+for|accepts?|accepting|allows?|allowed|will\s+consider|considering|welcomes?|willing\s+to|ok(?:ay)?\s+with|fine\s+with|eligible\s+for|available\s+(?:for|as|on))\b[^.\n]{0,60}\b(?:c2c|1099)\b/i;
const C2C_1099_CANDIDATES_RE = /\b(?:c2c|1099)\b[^.\n]{0,40}\bcandidates?\b/i;

/**
 * C2C/1099 is a contractor payment signal, but a bare mention is not enough:
 * the posting must frame it as a positive engagement (open to / accepts /
 * allows / candidates, etc.). Clear negation — "not a C2C", "no C2C", "not
 * open to C2C" — never counts. Both checks stay within one sentence.
 */
function c2c1099Engagement(text: string): boolean {
  if (C2C_1099_NEGATION_RE.test(text)) return false;
  return C2C_1099_POSITIVE_RE.test(text) || C2C_1099_CANDIDATES_RE.test(text);
}

/**
 * Classify one role. Application follow-up covers contract engagements: an
 * explicit Contract employment type, a contract-to-hire arrangement, or a
 * C2C/1099 (contractor) payment engagement framed positively. Full-time roles
 * that mention "contract" only in passing — a "not a C2C or contract role"
 * note, a "contractor portal", "contract coverage", or "contract value" —
 * stay direct.
 */
export function outreachKindFor(
  job: Pick<JobRow, "employmentType" | "title" | "description">,
): OutreachKind {
  if (job.employmentType.trim().toLowerCase() === "contract") return "application_followup";
  const text = `${job.title ?? ""}\n${job.description ?? ""}`;
  if (CONTRACT_TO_HIRE_RE.test(text)) return "application_followup";
  if (c2c1099Engagement(text)) return "application_followup";
  return "direct";
}

/**
 * Section for a recipient group. Any contract/application-follow-up role
 * (sent or unsent) pulls the whole group into application follow-up, so a
 * contract person never appears under Direct outreach. Sent/approved/override
 * precedence for the primary role is handled separately in primaryRoleFor.
 */
export function groupOutreachKind(jobs: readonly JobRow[]): OutreachKind {
  return jobs.some((job) => outreachKindFor(job) === "application_followup")
    ? "application_followup"
    : "direct";
}

/** One queue item: all job roles that share a normalized first hiring profile. */
export type RecipientGroup = {
  readonly key: string;
  readonly jobs: readonly JobRow[];
};

/**
 * Stable key for one queue item. A job whose first hiring-team profile is not
 * a usable LinkedIn /in/ URL gets its own per-job fallback key so it never
 * silently disappears from the queue.
 */
export function groupKeyFor(job: JobRow): string {
  return recipientProfileUrl(job) ?? `job:${job.id}`;
}

/** Group jobs by recipient. Groups are ordered by their primary role's most
 *  recent update (descending), with the key as a deterministic tiebreak. */
export function groupJobs(jobs: readonly JobRow[]): RecipientGroup[] {
  const map = new Map<string, JobRow[]>();
  for (const job of jobs) {
    const key = groupKeyFor(job);
    const existing = map.get(key);
    if (existing === undefined) map.set(key, [job]);
    else existing.push(job);
  }
  return [...map.entries()]
    .map(([key, group]) => ({ key, jobs: group }))
    .sort((a, b) => {
      const byTime =
        updatedOf(primaryRoleFor(b.jobs, undefined)) - updatedOf(primaryRoleFor(a.jobs, undefined));
      return byTime !== 0 ? byTime : a.key.localeCompare(b.key);
    });
}

/**
 * Group bucket precedence: sent if any role is sent; otherwise approved if any
 * role is approved; skipped only when every role is skipped; otherwise review.
 */
export function bucketFor(jobs: readonly JobRow[]): Bucket {
  if (jobs.some((job) => job.status === "sent")) return "sent";
  if (jobs.some((job) => job.review === "approved")) return "approved";
  if (jobs.every((job) => job.review === "skipped")) return "skipped";
  return "needs_review";
}

/** The role that owns the single approved/sent message for a recipient, if any. */
export function messageOwnerId(jobs: readonly JobRow[]): string | null {
  const sent = jobs.find((job) => job.status === "sent");
  if (sent !== undefined) return sent.id;
  const approved = jobs.find((job) => job.review === "approved");
  return approved !== undefined ? approved.id : null;
}

/**
 * Draft editability and action label for the currently selected role. A sent
 * group is read-only; otherwise the selected role is editable exactly when its
 * own review state is needs_review. When a different role owns the approval,
 * the Approve action replaces it (the duplicate-confirmation flow).
 */
export type DraftAction = {
  readonly editable: boolean;
  readonly replace: boolean;
  readonly canReturn: boolean;
};

export function draftActionFor(
  jobs: readonly JobRow[],
  selectedId: string | undefined,
): DraftAction {
  const selected = jobs.find((job) => job.id === selectedId);
  if (selected === undefined || bucketFor(jobs) === "sent") {
    return { editable: false, replace: false, canReturn: false };
  }
  if (selected.review === "needs_review") {
    const owner = messageOwnerId(jobs);
    return { editable: true, replace: owner !== null && owner !== selected.id, canReturn: false };
  }
  return { editable: false, replace: false, canReturn: true };
}

/**
 * Primary role for a group. Sent is absolute (a sent group is covered and
 * immutable). Otherwise the caller's in-memory selection wins, then the
 * approved role, then the most recently updated non-skipped role, then any
 * role (most recently updated for determinism). The selection-outranks-
 * approved ordering is what lets a user pick a different sibling and replace
 * the existing approval atomically.
 */
export function primaryRoleFor(jobs: readonly JobRow[], overrideId?: string): JobRow {
  const sent = jobs.find((job) => job.status === "sent");
  if (sent !== undefined) return sent;
  if (overrideId !== undefined) {
    const override = jobs.find((job) => job.id === overrideId);
    if (override !== undefined) return override;
  }
  const approved = jobs.find((job) => job.review === "approved");
  if (approved !== undefined) return approved;
  const nonSkipped = jobs.filter((job) => job.review !== "skipped");
  if (nonSkipped.length > 0) return mostRecent(nonSkipped);
  return mostRecent(jobs);
}

function mostRecent(jobs: readonly JobRow[]): JobRow {
  let best = jobs[0];
  for (const job of jobs) {
    if (best === undefined || primaryPreferred(job, best)) best = job;
  }
  if (best === undefined) throw new Error("mostRecent of an empty group");
  return best;
}

/**
 * Recency fallback tiebreak: prefer an application-follow-up (contract) role
 * so a mixed group defaults its primary selection to the contract role. A
 * sent, approved, or explicitly selected role already owns the decision and
 * is returned before this fallback runs. Equal kind → most recent wins.
 */
function primaryPreferred(candidate: JobRow, current: JobRow): boolean {
  const candidateFollowup = outreachKindFor(candidate) === "application_followup";
  const currentFollowup = outreachKindFor(current) === "application_followup";
  if (candidateFollowup !== currentFollowup) return candidateFollowup;
  return updatedOf(candidate) > updatedOf(current);
}

function updatedOf(job: JobRow): number {
  const value = Date.parse(job.updatedAt);
  return Number.isNaN(value) ? 0 : value;
}
