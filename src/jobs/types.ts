import type { PlaywriterSessionSelection } from "../commands/types.ts";

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

export type JobsSearchInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  readonly keywords: string;
  readonly location: string;
  /** LinkedIn time-posted filter: 1 | 7 | 14 | 30 days; omit for any time. */
  readonly postedWithinDays?: number;
  /** Include only remote postings (f_WT=2). */
  readonly remote?: boolean;
  /** Search pages to collect, 1..10 (25 jobs per page). */
  readonly pages: number;
  /** How many jobs to enrich with a hiring-team check, 1..200. */
  readonly hiringTeamLimit: number;
  /**
   * Keep enriching until this many jobs WITH a listed hiring team are found
   * (0 disables: enrich exactly `hiringTeamLimit` jobs). The run stops early
   * once the target is met; `targetMet` in the result says whether it was.
   */
  readonly hiringTeamTarget?: number;
  /** Job ids to skip during enrichment (resume after an interrupted run). */
  readonly skipIds?: readonly string[];
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

export type JobsDraftInput = {
  readonly stateDir: string;
  readonly id: string;
  readonly message: string;
};

export type JobsSendInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  /** Send one job; default sends every drafted job. */
  readonly id?: string;
  readonly allowSend: true;
};

export type JobStatus = "collected" | "favorite" | "drafted" | "sent";

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
};

export type JobsSearchSpec = {
  readonly keywords: string;
  readonly location: string;
  readonly postedWithinDays?: number;
  readonly remote?: boolean;
};
