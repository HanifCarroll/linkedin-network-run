export type AnalyticsOperation = "navigate" | "open_export" | "observe_dialog" | "confirm_export";

export interface ExactTextContract {
  readonly role?: "link" | "button";
  readonly name: string;
  readonly exact: true;
}

export interface AnalyticsBrowserContract {
  readonly url: "https://www.linkedin.com/analytics/creator/content/";
  readonly exportLink: ExactTextContract & { readonly role: "link" };
  readonly dateRangeButton: ExactTextContract & { readonly role: "button" };
  readonly confirmationText: ExactTextContract;
  readonly confirmButton: ExactTextContract & { readonly role: "button" };
}

export interface AnalyticsCommandConfig {
  readonly schemaVersion: 1;
  readonly operation: AnalyticsOperation;
  readonly contract: AnalyticsBrowserContract;
  readonly exportOperation: AnalyticsExportOperation;
  readonly preConfirmEvidence?: AnalyticsPreConfirmEvidence;
}

export interface AnalyticsExportOperation {
  readonly schemaVersion: 1;
  readonly operationId: string;
  readonly account: string;
  readonly startDate: string;
  readonly endDate: string;
  readonly requestedRange: "7 days";
  readonly resultUrl: AnalyticsBrowserContract["url"];
  readonly createdAt: string;
}

export interface AnalyticsPreConfirmEvidence {
  readonly schemaVersion: 1;
  readonly operationId: string;
  readonly snapshotId: string;
  readonly capturedAt: string;
  readonly entryCount: number;
}

export interface AnalyticsBrowserEvidence {
  readonly operationId: string;
  readonly account: string;
  readonly startDate: string;
  readonly endDate: string;
  readonly requestedRange: "7 days";
  readonly resultUrl: AnalyticsBrowserContract["url"];
  readonly preConfirmSnapshotId?: string;
  readonly confirmationTextVisibleCount?: number;
  readonly confirmButtonVisibleCount?: number;
  readonly actionStartedAt: string;
  readonly actionCompletedAt: string;
}

export interface AnalyticsCommandResult {
  readonly schemaVersion: 1;
  readonly operation: AnalyticsOperation;
  readonly status: "completed" | "blocked" | "contract_changed" | "failed";
  readonly observedUrl: string;
  readonly evidence: AnalyticsBrowserEvidence;
  readonly detail?: Readonly<Record<string, unknown>>;
}

export interface AnalyticsCommandRunner {
  execute(config: AnalyticsCommandConfig): Promise<AnalyticsCommandResult>;
}

export interface DownloadSnapshotEntry {
  readonly path: string;
  readonly realPath: string;
  readonly rootRealPath: string;
  readonly device: number;
  readonly inode: number;
  readonly birthtimeMs: number;
  readonly size: number;
  readonly modifiedAtMs: number;
}

export interface AnalyticsDownloadEvidence {
  readonly schemaVersion: 1;
  readonly operationId: string;
  readonly preConfirmSnapshotId: string;
  readonly resultUrl: AnalyticsBrowserContract["url"];
  readonly correlation: "bounded_temporal_association";
  readonly confirmationPhase: "confirm_started" | "confirm_completed";
  readonly confirmationStartedAt: string;
  readonly reconciliationDeadlineAt: string;
  readonly observedAt: string;
  readonly candidatePath: string;
  readonly candidateRealPath: string;
  readonly candidateRootRealPath: string;
  readonly candidateDevice: number;
  readonly candidateInode: number;
  readonly candidateBirthtimeMs: number;
  readonly candidateModifiedAtMs: number;
  readonly candidateSize: number;
  readonly baselineStatus: "new" | "changed";
  readonly changedCandidateCount: 1;
}

export interface WorkbookIdentity {
  readonly filename: string;
  readonly account: string;
  readonly startDate: string;
  readonly endDate: string;
}

export interface AnalyticsExportReceipt {
  readonly schemaVersion: 1;
  readonly receiptId: string;
  readonly status: "completed";
  readonly createdAt: string;
  readonly browserConfirmation: "performed" | "recovered_from_download";
  readonly confirmationEvidence: "browser_completed" | "download_reconciled";
  readonly operationId: string;
  readonly preConfirmSnapshotId: string;
  readonly resultUrl: AnalyticsBrowserContract["url"];
  readonly confirmationStartedAt: string;
  readonly confirmationCompletedAt: string;
  readonly downloadEvidence: AnalyticsDownloadEvidence;
  readonly sourcePath: string;
  readonly outputPath: string;
  readonly filename: string;
  readonly account: string;
  readonly startDate: string;
  readonly endDate: string;
  readonly byteSize: number;
  readonly sha256: string;
}

export interface ExportAnalyticsOptions {
  readonly runner: AnalyticsCommandRunner;
  readonly downloadRoots: readonly string[];
  readonly outputPath: string;
  readonly receiptPath: string;
  readonly expectedAccount: string;
  readonly expectedStartDate: string;
  readonly expectedEndDate: string;
  readonly recoveryStatePath?: string;
  readonly pollIntervalMs?: number;
  readonly maxPolls?: number;
  readonly now?: () => Date;
  readonly createReceiptId?: () => string;
  readonly createOperationId?: () => string;
  readonly sleep?: (milliseconds: number) => Promise<void>;
  readonly afterPublicationTempVerified?: (
    evidence: AnalyticsVerifiedPublicationTemp,
  ) => void | Promise<void>;
  readonly afterSourceOpened?: () => void | Promise<void>;
  readonly afterOutputPublished?: () => void | Promise<void>;
}

export interface AnalyticsVerifiedPublicationTemp {
  readonly byteSize: number;
  readonly sha256: string;
}

export type AnalyticsRecoveryPhase =
  | "ready_to_confirm"
  | "confirm_started"
  | "confirm_completed"
  | "needs_reconciliation"
  | "publish_started"
  | "completed";

export interface AnalyticsPublicationCheckpoint {
  readonly stagingPath: string;
  readonly sourcePath: string;
  readonly filename: string;
  readonly account: string;
  readonly startDate: string;
  readonly endDate: string;
  readonly byteSize: number;
  readonly sha256: string;
  readonly browserConfirmation: AnalyticsExportReceipt["browserConfirmation"];
  readonly confirmationEvidence: AnalyticsExportReceipt["confirmationEvidence"];
  readonly operationId: string;
  readonly preConfirmSnapshotId: string;
  readonly resultUrl: AnalyticsBrowserContract["url"];
  readonly confirmationStartedAt: string;
  readonly confirmationCompletedAt: string;
  readonly downloadEvidence: AnalyticsDownloadEvidence;
  readonly preparedReceipt: AnalyticsExportReceipt;
}

export interface AnalyticsConfirmAttempt {
  readonly operationId: string;
  readonly preConfirmSnapshotId: string;
  readonly startedAt: string;
  readonly reconciliationDeadlineAt: string;
}

export interface AnalyticsReconciliationCheckpoint {
  readonly confirmationPhase: "confirm_started" | "confirm_completed";
  readonly reason:
    | "no_workbook_observed"
    | "multiple_changed_workbooks"
    | "changed_workbook_mismatch"
    | "candidate_set_changed"
    | "workbook_outside_operation_window";
  readonly recordedAt: string;
  readonly observedCandidates: readonly DownloadSnapshotEntry[];
}

export interface AnalyticsRecoveryState {
  readonly schemaVersion: 1;
  readonly expectedAccount: string;
  readonly expectedStartDate: string;
  readonly expectedEndDate: string;
  readonly outputPath: string;
  readonly receiptPath: string;
  readonly downloadRoots: readonly string[];
  readonly exportOperation: AnalyticsExportOperation;
  readonly preConfirmEvidence: AnalyticsPreConfirmEvidence;
  readonly phase: AnalyticsRecoveryPhase;
  readonly preConfirmSnapshot: readonly DownloadSnapshotEntry[];
  readonly confirmAttempt?: AnalyticsConfirmAttempt;
  readonly confirmEvidence?: AnalyticsBrowserEvidence;
  readonly reconciliation?: AnalyticsReconciliationCheckpoint;
  readonly publication?: AnalyticsPublicationCheckpoint;
  readonly updatedAt: string;
}

export class AnalyticsNeedsReconciliationError extends Error {
  readonly code = "ANALYTICS_NEEDS_RECONCILIATION";
  constructor(
    readonly operationId: string,
    readonly phase: "confirm_started" | "confirm_completed",
    readonly preConfirmSnapshotId: string,
    readonly reason: AnalyticsReconciliationCheckpoint["reason"] = "no_workbook_observed",
    message = `analytics export needs reconciliation (${reason}); Confirm will not be repeated`,
  ) {
    super(message);
    this.name = "AnalyticsNeedsReconciliationError";
  }
}
