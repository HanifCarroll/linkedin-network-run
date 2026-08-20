import type { PlaywriterSessionSelection } from "../commands/types.ts";

/** Max characters for one classification phrase (work focus / product system). */
export const CLASSIFICATION_MAX_LENGTH = 80;

/** Max characters for one classification summary (work / product summary). */
export const SUMMARY_MAX_LENGTH = 320;

/** Max characters for a draft subject line. */
export const SUBJECT_MAX_LENGTH = 300;

/** Max characters for a draft body (generous; the four-paragraph shape is far shorter). */
export const DRAFT_MAX_LENGTH = 5000;

/** A review decision is orthogonal to the status lifecycle. */
export type ReviewDecision = "needs_review" | "approved" | "skipped";
export type TriageBucket = "pending" | "strong" | "possible" | "weak";
export const TRIAGE_POLICY_VERSION = "jobs-triage-v1-20260819";

export const REVIEW_DECISIONS: readonly ReviewDecision[] = ["needs_review", "approved", "skipped"];

/** One person listed on a job posting's "Meet the hiring team" section. */
export type HiringTeamMember = {
  readonly name: string;
  readonly profileUrl: string;
  /** Connection degree as rendered by LinkedIn: "1st" | "2nd" | "3rd" | "". */
  readonly degree: string;
  /** The member's headline/role line, when rendered. */
  readonly headline: string;
};

/** A job as collected from the search XHR plus its direct-view enrichment. */
export type CollectedJob = {
  readonly id: string;
  readonly title: string;
  readonly company: string;
  readonly location: string;
  readonly postingUrl: string;
  readonly hiringTeam: readonly HiringTeamMember[];
  readonly hasHiringTeam: boolean;
};
/** A raw job captured from the search XHR, before direct-view enrichment. */
export type CapturedJob = {
  readonly id: string;
  readonly title: string;
};

/** Full posting-page details captured by the detail-enrich pass. */
export type EnrichmentOutcome =
  | "complete_hiring_team"
  | "complete_no_hiring_team"
  | "retry_required"
  | "closed";

export type JobEnrichment = {
  readonly id: string;
  readonly sourceUrl: string;
  readonly outcome: EnrichmentOutcome;
  readonly title: string;
  readonly company: string;
  readonly location: string;
  readonly postingUrl: string;
  readonly description: string;
  readonly workplaceType: string;
  readonly employmentType: string;
  readonly applyMethod: string;
  readonly promoted: boolean;
  readonly activelyReviewing: boolean;
  readonly postedAt: string;
  readonly applicantCount: string;
  readonly benefits: readonly string[];
  readonly hiringTeam: readonly HiringTeamMember[];
  readonly companyProfileUrl: string;
  readonly companyEvidence: readonly string[];
  readonly externalApplicationUrl: string;
  readonly applicantTrackingSystem: string;
  readonly geoId: string;
  readonly rawResponses?: readonly JobEnrichmentResponse[];
  readonly capturedAt: string;
  readonly parserVersion: string;
  readonly sourceEvidence: readonly string[];
};

export type JobEnrichmentResponse = {
  readonly component:
    | "document"
    | "aboutTheJob"
    | "aboutTheCompanyForJobDetails"
    | "peopleWhoCanHelp";
  readonly sourceUrl: string;
  readonly responseUrl: string;
  readonly status: number;
  readonly capturedAt: string;
  readonly parserVersion: string;
  readonly body: string;
};

export type JobsListInput = {
  readonly stateDir: string;
  readonly status?: JobStatus;
  readonly withHiringTeam: boolean;
};

export type JobsFavoriteInput = {
  readonly stateDir: string;
  readonly ids: readonly string[];
};

export type JobsRemoveInput = {
  readonly stateDir: string;
  readonly ids: readonly string[];
};

export type JobsDraftInput = {
  readonly stateDir: string;
  readonly id: string;
  /** Optional subject line; only used when the composer exposes a subject field. */
  readonly subject: string;
  readonly message: string;
};

export type JobsDraftNextInput = { readonly stateDir: string; readonly id?: string };

export type JobsAppliedInput = {
  readonly stateDir: string;
  readonly id: string;
  readonly applicationUrl?: string;
  readonly appliedAt: string;
};

export type JobsSendInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  /** Send one job; default sends every drafted job. */
  readonly id?: string;
  readonly allowSend: true;
};

export type JobStatus = "captured" | "collected" | "favorite" | "drafted" | "sent";

export type JobRow = {
  readonly id: string;
  readonly title: string;
  readonly company: string;
  readonly location: string;
  readonly postingUrl: string;
  readonly hiringTeam: readonly HiringTeamMember[];
  readonly hasHiringTeam: boolean;
  readonly status: JobStatus;
  readonly message: string | null;
  readonly collectedAt: string;
  readonly updatedAt: string;
  readonly sentAt: string | null;
  readonly description: string;
  readonly workplaceType: string;
  readonly employmentType: string;
  readonly applyMethod: string;
  readonly promoted: boolean;
  readonly activelyReviewing: boolean;
  readonly postedAt: string;
  readonly applicantCount: string;
  readonly benefits: readonly string[];
  readonly enrichmentOutcome: EnrichmentOutcome;
  readonly enrichmentCapturedAt: string | null;
  readonly enrichmentParserVersion: string;
  readonly enrichmentEvidence: readonly string[];
  readonly companyProfileUrl: string;
  readonly companyEvidence: readonly string[];
  readonly externalApplicationUrl: string;
  readonly applicantTrackingSystem: string;
  readonly geoId: string;
  /** Functional area the role centers on (e.g. "Growth", "Backend platform"). */
  readonly workFocus: string;
  /** Tool or platform the role is built around (e.g. "Salesforce", "HubSpot"). */
  readonly productSystem: string;
  /** Longer prose on what the role does day to day. */
  readonly workSummary: string;
  /** Longer prose on what the role builds. */
  readonly productSummary: string;
  /** Editable draft subject line ('' when the DM composer has no subject). */
  readonly subject: string;
  /** Review decision, orthogonal to status. */
  readonly review: ReviewDecision;
  readonly fit: "pending" | "kept" | "dropped";
  readonly filterReason: string;
  readonly matchedTerm: string;
  readonly filterPolicyVersion: string;
  readonly filteredAt: string | null;
  readonly triageBucket: TriageBucket;
  readonly companySummary: string;
  readonly responsibilities: readonly string[];
  readonly skillMatches: readonly string[];
  readonly skillGaps: readonly string[];
  readonly triageReason: string;
  readonly triagePolicyVersion: string;
  readonly triagedAt: string | null;
  /** Application checkpoint for application-followup roles. */
  readonly appliedAt: string | null;
  readonly applicationUrl: string | null;
};
