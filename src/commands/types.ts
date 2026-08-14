export type DoctorInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly networkSessionId?: number;
  readonly analyticsSessionId?: number;
  /** Optional path to the linkedin-network automation.toml for source-drift checks. */
  readonly automationPromptPath?: string;
};

export type NetworkReadInput = {
  readonly stateDir: string;
  readonly localDate: string;
};

export type NetworkIncidentStatusInput = {
  readonly stateDir: string;
  readonly pruneDays?: number;
};

export type NetworkIncidentClearInput = {
  readonly stateDir: string;
  readonly reason: string;
  readonly accountAccessConfirmed: boolean;
  readonly warningClearedConfirmed: boolean;
};
export type PlaywriterSessionSelection = number | "auto";

export type NetworkTickInput = NetworkReadInput & {
  readonly allowSend: true;
  readonly batchSize: number;
  readonly maxRealSends: number;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
};

export type NetworkReconcileInput = NetworkReadInput & {
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
};

export type NetworkRunEndInput = {
  readonly stateDir: string;
  readonly localDate: string;
  readonly reason: string;
};

export type NetworkSessionResetInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
};

export type NetworkOpenInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  readonly page: "sent" | "search";
  readonly sourceId?: "hubspot-agency-ops" | "hubspot-b2b-revops";
};

export type AnalyticsExportInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  readonly downloadRoots: readonly string[];
  readonly outputPath: string;
  readonly receiptPath: string;
  readonly recoveryStatePath?: string;
  readonly expectedAccount: string;
  readonly expectedStartDate: string;
  readonly expectedEndDate: string;
  readonly pollIntervalMs?: number;
  readonly maxPolls?: number;
};

export type JobsSearchInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  readonly keywords: string;
  readonly location: string;
  readonly postedWithinDays?: number;
  readonly remote?: boolean;
  readonly pages: number;
  readonly hiringTeamLimit: number;
  readonly hiringTeamTarget?: number;
  readonly skipIds?: readonly string[];
};

export type JobsCollectInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  readonly keywords: string;
  readonly location: string;
  readonly postedWithinDays?: number;
  readonly remote?: boolean;
  readonly pages: number;
};

export type JobsEnrichInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  /** Max jobs to enrich this run (default: all captured). */
  readonly limit?: number;
};

export type JobsListInput = {
  readonly stateDir: string;
  readonly status?: "captured" | "collected" | "favorite" | "drafted" | "sent";
  readonly withHiringTeam: boolean;
};

export type JobsCheckInput = {
  readonly stateDir: string;
  readonly playwriterBin: string;
  readonly sessionId: PlaywriterSessionSelection;
  readonly status?: "captured" | "collected" | "favorite" | "drafted" | "sent";
  readonly withHiringTeam: boolean;
  /** Max jobs to check this run (default: all matching). */
  readonly limit?: number;
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
  readonly id?: string;
  readonly allowSend: true;
};

export type MigrationDryRunInput = {
  readonly sourceRoot: string;
};

export interface CliOperations {
  doctor(input: DoctorInput): Promise<unknown>;
  networkStatus(input: NetworkReadInput): Promise<unknown>;
  networkReport(input: NetworkReadInput): Promise<unknown>;
  networkTick(input: NetworkTickInput): Promise<unknown>;
  networkReconcile(input: NetworkReconcileInput): Promise<unknown>;
  networkRunEnd(input: NetworkRunEndInput): Promise<unknown>;
  networkSessionReset(input: NetworkSessionResetInput): Promise<unknown>;
  networkOpen(input: NetworkOpenInput): Promise<unknown>;
  networkIncidentStatus(input: NetworkIncidentStatusInput): Promise<unknown>;
  networkIncidentClear(input: NetworkIncidentClearInput): Promise<unknown>;
  analyticsExport(input: AnalyticsExportInput): Promise<unknown>;
  migrationDryRun(input: MigrationDryRunInput): Promise<unknown>;
  jobsSearch(input: JobsSearchInput): Promise<unknown>;
  jobsCollect(input: JobsCollectInput): Promise<unknown>;
  jobsEnrich(input: JobsEnrichInput): Promise<unknown>;
  jobsList(input: JobsListInput): Promise<unknown>;
  jobsCheck(input: JobsCheckInput): Promise<unknown>;
  jobsFavorite(input: JobsFavoriteInput): Promise<unknown>;
  jobsDraft(input: JobsDraftInput): Promise<unknown>;
  jobsSend(input: JobsSendInput): Promise<unknown>;
}

export type ParsedInvocation =
  | { readonly kind: "help"; readonly text: string }
  | { readonly kind: "version" }
  | { readonly kind: "command"; readonly command: "doctor"; readonly input: DoctorInput }
  | {
      readonly kind: "command";
      readonly command: "network status" | "network report";
      readonly input: NetworkReadInput;
    }
  | { readonly kind: "command"; readonly command: "network tick"; readonly input: NetworkTickInput }
  | {
      readonly kind: "command";
      readonly command: "network reconcile";
      readonly input: NetworkReconcileInput;
    }
  | {
      readonly kind: "command";
      readonly command: "network run-end";
      readonly input: NetworkRunEndInput;
    }
  | {
      readonly kind: "command";
      readonly command: "network session-reset";
      readonly input: NetworkSessionResetInput;
    }
  | {
      readonly kind: "command";
      readonly command: "network open";
      readonly input: NetworkOpenInput;
    }
  | {
      readonly kind: "command";
      readonly command: "analytics export";
      readonly input: AnalyticsExportInput;
    }
  | {
      readonly kind: "command";
      readonly command: "migration dry-run";
      readonly input: MigrationDryRunInput;
    }
  | {
      readonly kind: "command";
      readonly command: "network incident-status";
      readonly input: NetworkIncidentStatusInput;
    }
  | {
      readonly kind: "command";
      readonly command: "network incident-clear";
      readonly input: NetworkIncidentClearInput;
    }
  | { readonly kind: "command"; readonly command: "jobs search"; readonly input: JobsSearchInput }
  | { readonly kind: "command"; readonly command: "jobs collect"; readonly input: JobsCollectInput }
  | { readonly kind: "command"; readonly command: "jobs enrich"; readonly input: JobsEnrichInput }
  | { readonly kind: "command"; readonly command: "jobs list"; readonly input: JobsListInput }
  | { readonly kind: "command"; readonly command: "jobs check"; readonly input: JobsCheckInput }
  | {
      readonly kind: "command";
      readonly command: "jobs favorite";
      readonly input: JobsFavoriteInput;
    }
  | { readonly kind: "command"; readonly command: "jobs draft"; readonly input: JobsDraftInput }
  | { readonly kind: "command"; readonly command: "jobs send"; readonly input: JobsSendInput };
