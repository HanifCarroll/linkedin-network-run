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
  readonly target: 30;
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
  networkIncidentStatus(input: NetworkIncidentStatusInput): Promise<unknown>;
  networkIncidentClear(input: NetworkIncidentClearInput): Promise<unknown>;
  analyticsExport(input: AnalyticsExportInput): Promise<unknown>;
  migrationDryRun(input: MigrationDryRunInput): Promise<unknown>;
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
    };
