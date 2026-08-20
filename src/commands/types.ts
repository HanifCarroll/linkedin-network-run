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
  readonly sourceId?: "b2b-saas-founders" | "b2b-saas-engineering-product-leaders";
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

export type SalesNavInput = import("../salesnav.ts").SalesNavInput;

export type JobsCaptureStartInput = {
  readonly stateDir: string;
  readonly runId: string;
  readonly sourceUrl: string;
  readonly searchConfigJson?: string | undefined;
  readonly checkpointJson?: string | undefined;
};

export type JobsCaptureIngestInput = {
  readonly stateDir: string;
  readonly runId: string;
  readonly pageIdentity: string;
  readonly payloadPath: string;
  readonly sourceUrl: string;
  readonly responseUrl: string;
  readonly cursor?: string | undefined;
  readonly capturedAt?: string | undefined;
};

export type JobsCaptureFinishInput = {
  readonly stateDir: string;
  readonly runId: string;
  readonly state: "complete" | "failed";
  readonly checkpointJson?: string | undefined;
  readonly error?: string | undefined;
};
export type JobsNormalizeInput = {
  readonly stateDir: string;
  readonly runId: string;
  readonly limit?: number;
};

export type JobsFilterInput = {
  readonly stateDir: string;
  readonly runId: string;
  readonly terms: readonly string[];
  readonly policyVersion: string;
  readonly maxAgeDays?: number;
};

export type JobsEnrichNextInput = {
  readonly stateDir: string;
  readonly runId?: string;
  readonly id?: string;
};
export type JobsEnrichRecordInput = {
  readonly stateDir: string;
  readonly payloadPath: string;
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
export type JobsApplicationNextInput = { readonly stateDir: string; readonly id?: string };

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
  readonly id?: string;
  readonly allowSend: true;
};
export type JobsSendPrepareInput = {
  readonly stateDir: string;
  readonly id?: string;
  readonly allowSend: true;
};
export type JobsSendRecordInput = { readonly stateDir: string; readonly payloadPath: string };
export type JobsContractOutreachPrepareInput = {
  readonly stateDir: string;
  readonly id?: string;
  readonly allowSend: true;
};
export type JobsContractOutreachRecordInput = {
  readonly stateDir: string;
  readonly payloadPath: string;
};

export type JobsClassifyInput = {
  readonly stateDir: string;
  readonly id: string;
  readonly workFocus: string;
  readonly productSystem: string;
  readonly workSummary: string;
  readonly productSummary: string;
};

export type JobsTriageNextInput = { readonly stateDir: string; readonly runId?: string };
export type JobsTriageRecordInput = {
  readonly stateDir: string;
  readonly id: string;
  readonly bucket: "strong" | "possible" | "weak";
  readonly companySummary: string;
  readonly workSummary: string;
  readonly responsibilities: readonly string[];
  readonly skillMatches: readonly string[];
  readonly skillGaps: readonly string[];
  readonly reason: string;
  readonly policyVersion: string;
};

export type JobsHubSpotNextInput = {
  readonly stateDir: string;
  readonly id?: string;
};

export type JobsInstantlyNextInput = {
  readonly stateDir: string;
  readonly campaignId: string;
  readonly id?: string;
};
export type JobsInstantlyRecordInput = {
  readonly stateDir: string;
  readonly prospectId: string;
  readonly payloadPath: string;
};
export type JobsFollowupNextInput = { readonly stateDir: string; readonly id?: string };
export type JobsFollowupRecordInput = {
  readonly stateDir: string;
  readonly prospectId: string;
  readonly payloadPath: string;
};

export type JobsHubSpotRecordInput = {
  readonly stateDir: string;
  readonly prospectId: string;
  readonly companyId?: string;
  readonly contactId?: string;
  readonly dealId?: string;
  readonly taskId?: string;
  readonly associationsComplete?: true;
  readonly error?: string;
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
  salesnav(input: SalesNavInput): Promise<unknown>;
  jobsCaptureStart(input: JobsCaptureStartInput): Promise<unknown>;
  jobsCaptureIngest(input: JobsCaptureIngestInput): Promise<unknown>;
  jobsCaptureFinish(input: JobsCaptureFinishInput): Promise<unknown>;
  jobsNormalize(input: JobsNormalizeInput): Promise<unknown>;
  jobsFilter(input: JobsFilterInput): Promise<unknown>;
  jobsEnrichNext(input: JobsEnrichNextInput): Promise<unknown>;
  jobsEnrichRecord(input: JobsEnrichRecordInput): Promise<unknown>;
  jobsList(input: JobsListInput): Promise<unknown>;
  jobsCheck(input: JobsCheckInput): Promise<unknown>;
  jobsFavorite(input: JobsFavoriteInput): Promise<unknown>;
  jobsRemove(input: JobsRemoveInput): Promise<unknown>;
  jobsDraft(input: JobsDraftInput): Promise<unknown>;
  jobsDraftNext(input: JobsDraftNextInput): Promise<unknown>;
  jobsApplicationNext(input: JobsApplicationNextInput): Promise<unknown>;
  jobsApplied(input: JobsAppliedInput): Promise<unknown>;
  jobsSendPrepare(input: JobsSendPrepareInput): Promise<unknown>;
  jobsSendRecord(input: JobsSendRecordInput): Promise<unknown>;
  jobsContractOutreachPrepare(input: JobsContractOutreachPrepareInput): Promise<unknown>;
  jobsContractOutreachRecord(input: JobsContractOutreachRecordInput): Promise<unknown>;
  jobsClassify(input: JobsClassifyInput): Promise<unknown>;
  jobsHubSpotNext(input: JobsHubSpotNextInput): Promise<unknown>;
  jobsTriageNext(input: JobsTriageNextInput): Promise<unknown>;
  jobsTriageRecord(input: JobsTriageRecordInput): Promise<unknown>;
  jobsHubSpotRecord(input: JobsHubSpotRecordInput): Promise<unknown>;
  jobsInstantlyNext(input: JobsInstantlyNextInput): Promise<unknown>;
  jobsInstantlyRecord(input: JobsInstantlyRecordInput): Promise<unknown>;
  jobsFollowupNext(input: JobsFollowupNextInput): Promise<unknown>;
  jobsFollowupRecord(input: JobsFollowupRecordInput): Promise<unknown>;
}

export type ParsedInvocation =
  | { readonly kind: "help"; readonly text: string }
  | { readonly kind: "version" }
  | {
      readonly kind: "command";
      readonly command:
        | "salesnav staffing capture-start"
        | "salesnav staffing capture-ingest"
        | "salesnav staffing capture-finish"
        | "salesnav staffing normalize"
        | "salesnav staffing qualify"
        | "salesnav staffing status"
        | "salesnav staffing account-capture-start"
        | "salesnav staffing account-capture-ingest"
        | "salesnav staffing account-capture-finish"
        | "salesnav staffing account-normalize"
        | "salesnav staffing account-status"
        | "salesnav staffing account-qualify-next"
        | "salesnav staffing account-qualify-record"
        | "salesnav staffing account-people-candidates"
        | "salesnav staffing firm-research-record"
        | "salesnav staffing account-people-capture-start"
        | "salesnav staffing account-people-capture-ingest"
        | "salesnav staffing account-people-capture-finish"
        | "salesnav staffing account-people-normalize"
        | "salesnav staffing account-people-next"
        | "salesnav staffing account-people-review"
        | "salesnav studio capture-start"
        | "salesnav studio capture-ingest"
        | "salesnav studio capture-finish"
        | "salesnav studio normalize"
        | "salesnav studio qualify"
        | "salesnav studio status"
        | "salesnav studio account-capture-start"
        | "salesnav studio account-capture-ingest"
        | "salesnav studio account-capture-finish"
        | "salesnav studio account-normalize"
        | "salesnav studio account-status"
        | "salesnav studio account-qualify-next"
        | "salesnav studio account-qualify-record"
        | "salesnav studio account-people-candidates"
        | "salesnav studio firm-research-record"
        | "salesnav studio account-people-capture-start"
        | "salesnav studio account-people-capture-ingest"
        | "salesnav studio account-people-capture-finish"
        | "salesnav studio account-people-normalize"
        | "salesnav studio account-people-next"
        | "salesnav studio account-people-review";
      readonly input: SalesNavInput;
    }
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
  | {
      readonly kind: "command";
      readonly command: "jobs capture-start";
      readonly input: JobsCaptureStartInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs capture-ingest";
      readonly input: JobsCaptureIngestInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs capture-finish";
      readonly input: JobsCaptureFinishInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs normalize";
      readonly input: JobsNormalizeInput;
    }
  | { readonly kind: "command"; readonly command: "jobs filter"; readonly input: JobsFilterInput }
  | {
      readonly kind: "command";
      readonly command: "jobs enrich-next";
      readonly input: JobsEnrichNextInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs enrich-record";
      readonly input: JobsEnrichRecordInput;
    }
  | { readonly kind: "command"; readonly command: "jobs list"; readonly input: JobsListInput }
  | { readonly kind: "command"; readonly command: "jobs check"; readonly input: JobsCheckInput }
  | {
      readonly kind: "command";
      readonly command: "jobs favorite";
      readonly input: JobsFavoriteInput;
    }
  | { readonly kind: "command"; readonly command: "jobs remove"; readonly input: JobsRemoveInput }
  | { readonly kind: "command"; readonly command: "jobs draft"; readonly input: JobsDraftInput }
  | {
      readonly kind: "command";
      readonly command: "jobs draft-next";
      readonly input: JobsDraftNextInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs application-next";
      readonly input: JobsApplicationNextInput;
    }
  | { readonly kind: "command"; readonly command: "jobs applied"; readonly input: JobsAppliedInput }
  | {
      readonly kind: "command";
      readonly command: "jobs send-prepare";
      readonly input: JobsSendPrepareInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs send-record";
      readonly input: JobsSendRecordInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs contract-outreach-prepare";
      readonly input: JobsContractOutreachPrepareInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs contract-outreach-record";
      readonly input: JobsContractOutreachRecordInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs classify";
      readonly input: JobsClassifyInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs triage-next";
      readonly input: JobsTriageNextInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs triage-record";
      readonly input: JobsTriageRecordInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs hubspot-next";
      readonly input: JobsHubSpotNextInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs hubspot-record";
      readonly input: JobsHubSpotRecordInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs instantly-next";
      readonly input: JobsInstantlyNextInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs instantly-record";
      readonly input: JobsInstantlyRecordInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs followup-next";
      readonly input: JobsFollowupNextInput;
    }
  | {
      readonly kind: "command";
      readonly command: "jobs followup-record";
      readonly input: JobsFollowupRecordInput;
    };
