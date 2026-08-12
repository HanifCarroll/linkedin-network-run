export const PLAYWRITER_DEFAULT_EXECUTABLE = "/Users/hanifcarroll/.bun/bin/playwriter";
export const PLAYWRITER_EXECUTABLE_ENV = "LINKEDIN_TOOLS_PLAYWRITER_BIN";
export const SEND_PREPARATION_STATE_KEY = "networkPreparedSend";
export const SEND_PREPARATION_TTL_MS = 2 * 60 * 1_000;
export const SOURCE_CAPTURE_STATE_KEY = "networkSourceCaptureState";

/**
 * Per-command Playwriter evaluation timeout contract. Every command in
 * NETWORK_COMMANDS and ANALYTICS_COMMANDS must have an explicit entry so a new
 * command cannot silently fall through to the Playwriter default and die
 * mid-navigation (the original walk-list defect). Commands that walk, capture,
 * or mutate source pages use the long source-capture budget; navigation-only
 * and single-step commands use the Playwriter default.
 */
export const PLAYWRITER_DEFAULT_TIMEOUT_MS = 10_000;
export const SOURCE_CAPTURE_TIMEOUT_MS = 240_000;

export const COMMAND_TIMEOUT_MS: Readonly<Record<string, number>> = {
  // Source navigation is a single goto; keep the default budget.
  "navigate-candidate-results": PLAYWRITER_DEFAULT_TIMEOUT_MS,
  "navigate-sent-list": PLAYWRITER_DEFAULT_TIMEOUT_MS,
  // Captures and candidate walks scroll, wait for lazy rows, and paginate.
  "capture-candidate-results": SOURCE_CAPTURE_TIMEOUT_MS,
  "capture-candidate": SOURCE_CAPTURE_TIMEOUT_MS,
  "capture-sent-list": SOURCE_CAPTURE_TIMEOUT_MS,
  "walk-list": SOURCE_CAPTURE_TIMEOUT_MS,
  // Send-path phases open menus, modals, and observe post-send state.
  "click-connect-menu-item": SOURCE_CAPTURE_TIMEOUT_MS,
  "observe-connect-modal": SOURCE_CAPTURE_TIMEOUT_MS,
  "click-send": SOURCE_CAPTURE_TIMEOUT_MS,
  "prepare-send": SOURCE_CAPTURE_TIMEOUT_MS,
  "commit-send": SOURCE_CAPTURE_TIMEOUT_MS,
  "observe-post-send": SOURCE_CAPTURE_TIMEOUT_MS,
  // Analytics phases navigate and wait on export dialogs.
  "analytics-navigate": SOURCE_CAPTURE_TIMEOUT_MS,
  "analytics-open-export": SOURCE_CAPTURE_TIMEOUT_MS,
  "analytics-observe-dialog": SOURCE_CAPTURE_TIMEOUT_MS,
  "analytics-confirm-export": SOURCE_CAPTURE_TIMEOUT_MS,
};

export function commandTimeoutMs(command: string): number | null {
  return COMMAND_TIMEOUT_MS[command] ?? null;
}

export const NETWORK_COMMANDS = [
  "navigate-candidate-results",
  "capture-candidate-results",
  "capture-candidate",
  "click-connect-menu-item",
  "observe-connect-modal",
  /** Retained only so an un-migrated caller fails closed with an explicit error. */
  "click-send",
  "prepare-send",
  "commit-send",
  "observe-post-send",
  "navigate-sent-list",
  "capture-sent-list",
  "walk-list",
] as const;
export type NetworkCommand = (typeof NETWORK_COMMANDS)[number];
export const ANALYTICS_COMMANDS = [
  "analytics-navigate",
  "analytics-open-export",
  "analytics-observe-dialog",
  "analytics-confirm-export",
] as const;
export type AnalyticsPlaywriterCommand = (typeof ANALYTICS_COMMANDS)[number];
export type PlaywriterCommand = NetworkCommand | AnalyticsPlaywriterCommand | (string & {});

export interface CandidateIdentity {
  readonly sourceName: string;
  readonly savedSearchId: string;
  readonly searchUrl: string;
  readonly salesLeadUrl: string;
  readonly salesLeadId: string;
  readonly name: string;
  /** Exact value from the row descendant's data-scroll-into-view attribute. */
  readonly rowIdentity: string;
}

export type NetworkSourceId = "hubspot-agency-ops" | "hubspot-b2b-revops";

export interface NetworkSourceContract {
  readonly schemaVersion: 1;
  readonly kind: "network_source_contract";
  readonly contractVersion: 1;
  readonly sourceId: NetworkSourceId;
  readonly sourceName: "Consulting - HubSpot Agency Ops" | "Consulting - HubSpot B2B RevOps";
  readonly savedSearchId: string;
  readonly searchUrl: string;
  readonly contractFingerprint: string;
}

export interface SourceCaptureRow {
  readonly rowIdentity: string;
  readonly salesLeadUrl: string;
  readonly name: string;
}

export interface SourceReloadEvidence {
  readonly navigationInvocationId: string;
  readonly reloadIdentity: string;
  readonly reloadGeneration: number;
  readonly navigatedAt: string;
}

export interface SourceCapturePageEvidence {
  readonly stateKey: "networkCandidateResultsPage";
  readonly url: string;
  readonly resultsContainerCount: number;
  readonly resultsContainerVisible: boolean;
  readonly ariaBusy: "true" | "false" | null;
  readonly progressbarCount: number;
  readonly alertCount: number;
  readonly dialogCount: number;
  readonly fullyLoaded: boolean;
  readonly blockerFree: boolean;
  readonly cursorIdentity: string | null;
  readonly pageIdentity: string | null;
}

export interface SourceCapturePaginationEvidence {
  readonly navigationCount: number;
  readonly currentPageCount: number;
  readonly nextControlCount: number;
  readonly nextDisabled: boolean | null;
}

export interface SourceTerminalEvidence {
  readonly schemaVersion: 1;
  readonly kind: "network_source_terminal_observation";
  readonly captureInvocationId: string;
  readonly observedAt: string;
  readonly sourceId: NetworkSourceId;
  readonly sourceName: NetworkSourceContract["sourceName"];
  readonly savedSearchId: string;
  readonly searchUrl: string;
  readonly sourceContractVersion: 1;
  readonly sourceContractFingerprint: string;
  readonly terminalFingerprint: string;
  readonly pageIdentity: string;
  readonly cursorIdentity: string;
  readonly stableRowIds: readonly string[];
  readonly rowCount: number;
  readonly nextControl: "disabled";
  readonly navigationInvocationId: string;
  readonly reloadIdentity: string;
  readonly reloadGeneration: number;
  readonly navigatedAt: string;
}

export interface SourceCaptureResultData {
  readonly schemaVersion: 1;
  readonly kind: "network_source_capture";
  readonly captureInvocationId: string;
  readonly capturedAt: string;
  readonly sourceContract: NetworkSourceContract;
  readonly url: string;
  readonly items: readonly SourceCaptureRow[];
  readonly reload: SourceReloadEvidence | null;
  readonly page: SourceCapturePageEvidence;
  readonly pagination: SourceCapturePaginationEvidence;
  readonly terminalEvidence?: SourceTerminalEvidence;
}

export interface SendPreparationReceipt {
  readonly schemaVersion: 1;
  readonly kind: "network_send_prepared";
  /** pwprep:<prepare invocation id>:<128-bit token>:<SHA-256 fingerprint> */
  readonly receiptId: string;
  readonly attemptId: string;
  readonly preparedAt: string;
  readonly candidate: CandidateIdentity;
}

export interface CommitSendPostClickEvidence {
  readonly observedUrl: string;
  readonly modalCount: number;
  readonly sendControlCount: number;
  readonly pendingCount: number;
  readonly capturedAt: string;
}

export interface CommitSendResultData {
  readonly schemaVersion: 1;
  readonly kind: "network_send_commit";
  readonly receiptId: string;
  readonly attemptId: string;
  readonly candidate: CandidateIdentity;
  readonly clickDispatched: true;
  readonly postClickEvidence: CommitSendPostClickEvidence;
}

export type BrowserActionKind =
  | "none"
  | "navigate"
  | "connect"
  | "send"
  | "analytics_export"
  | "custom";
export const PROGRESS_STATES = [
  "invocation_created",
  "process_started",
  "observation_before",
  "navigation_started",
  "navigation_returned",
  "candidate_results_observed",
  "candidate_observed",
  "connect_started",
  "connect_returned",
  "modal_observed",
  "send_prepared",
  "send_commit_started",
  "send_click_dispatched",
  "send_post_click_observed",
  "post_send_observed",
  "sent_list_observed",
  "analytics_confirm_started",
  "analytics_confirm_returned",
  "observation_after",
  "logs_captured",
  "process_succeeded",
  "process_failed",
] as const;
export type ProgressState = (typeof PROGRESS_STATES)[number];

declare const descriptorBrand: unique symbol;
export interface CompiledScriptDescriptor<TCommand extends PlaywriterCommand = PlaywriterCommand> {
  readonly [descriptorBrand]: true;
  readonly command: TCommand;
  readonly action: BrowserActionKind;
  readonly phases: readonly ProgressState[];
  readonly source: string;
  readonly definitionId: string;
  readonly candidate?: CandidateIdentity;
  readonly sendPreparation?: SendPreparationReceipt;
  readonly sourceContract?: NetworkSourceContract;
}

export interface InvocationConfig {
  readonly schemaVersion: 1;
  readonly invocationId: string;
  readonly command: PlaywriterCommand;
  readonly definitionId: string;
  readonly action: BrowserActionKind;
  readonly phaseContract: readonly ProgressState[];
  readonly createdAt: string;
  readonly sessionId: number;
  readonly candidate?: CandidateIdentity;
  readonly sendPreparation?: SendPreparationReceipt;
  readonly sourceContract?: NetworkSourceContract;
  readonly input: Readonly<Record<string, unknown>>;
}
export interface ProgressEvent {
  readonly invocationId: string;
  readonly command: PlaywriterCommand;
  readonly state: ProgressState;
  readonly timestamp: string;
  readonly candidate?: CandidateIdentity;
  readonly detail?: Readonly<Record<string, unknown>>;
}
export type BlockerKind =
  | "rate_limit_429"
  | "weekly_limit"
  | "unusual_activity"
  | "login"
  | "checkpoint"
  | "security_verification"
  | "session_lost"
  | "page_closed"
  | "network_refusal"
  | "source_mismatch"
  | "wrong_page"
  | "selector_contract"
  | "evidence_corrupt"
  | "evidence_finalization"
  | "preparation_mismatch"
  | "preparation_stale"
  | "commit_uncertainty"
  | "already_pending"
  | "email_required"
  | "missing_more_actions"
  | "missing_connect_menu"
  | "missing_send"
  | "disabled_send"
  | "candidate_absent"
  | "no_rows"
  | "row_load_timeout"
  | "stalled_navigation"
  | "source_exhausted"
  | "unclear_confirmation";
export interface TypedBlocker {
  readonly kind: BlockerKind;
  readonly evidence: string;
  readonly retryability: "safe_retry" | "terminal" | "possible_send";
}
export interface InvocationReceipt {
  readonly schemaVersion: 1;
  readonly invocationId: string;
  readonly command: PlaywriterCommand;
  readonly definitionId: string;
  readonly action: BrowserActionKind;
  readonly startedAt: string;
  readonly finishedAt: string;
  readonly exitCode: number;
  readonly outcome: "succeeded" | "failed" | "critical_uncertainty";
  readonly result: Readonly<Record<string, unknown>> | null;
  readonly candidate?: CandidateIdentity;
  readonly blocker?: TypedBlocker;
}
export interface InvocationRequest {
  readonly sessionId: number;
  readonly descriptor: CompiledScriptDescriptor;
  /** If supplied, it must be deeply identical to the descriptor identity. */
  readonly candidate?: CandidateIdentity;
  readonly input?: Readonly<Record<string, unknown>>;
  /** Required for commit-send and forbidden for every other command. */
  readonly sendPreparation?: SendPreparationReceipt;
}
export interface InvocationResult {
  readonly directory: string;
  readonly config: InvocationConfig;
  readonly receipt: InvocationReceipt;
  readonly stdout: string;
  readonly stderr: string;
  readonly progress: readonly ProgressEvent[];
}
export interface SessionInfo {
  readonly id: number;
  readonly browser: string;
  readonly profile: string | null;
  readonly extensionId: string | null;
  readonly cwd: string | null;
  readonly stateKeys: readonly string[];
}

export interface PreparedSendInvocation {
  readonly invocation: InvocationResult;
  readonly receipt: SendPreparationReceipt | null;
}

export interface SourceCaptureInvocation {
  readonly navigation: InvocationResult;
  readonly capture: InvocationResult | null;
  readonly data: SourceCaptureResultData | null;
}

export interface SourceExhaustionEvidence {
  readonly sourceContract: NetworkSourceContract;
  readonly observations: readonly [SourceTerminalEvidence, SourceTerminalEvidence];
}
