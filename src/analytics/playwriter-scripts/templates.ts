import { hasExactKeys, INVOCATION_ID_RE, SHA256_HEX_RE } from "../../core/evidence-contract.ts";
import type {
  AnalyticsPlaywriterCommand,
  BrowserActionKind,
  ProgressState,
} from "../../playwriter/types.ts";
import { LINKEDIN_CONTENT_ANALYTICS_CONTRACT } from "../contract.ts";
import type { AnalyticsCommandConfig, AnalyticsOperation } from "../types.ts";

export const ANALYTICS_COMMAND_BY_OPERATION: Readonly<
  Record<AnalyticsOperation, AnalyticsPlaywriterCommand>
> = Object.freeze({
  navigate: "analytics-navigate",
  open_export: "analytics-open-export",
  observe_dialog: "analytics-observe-dialog",
  confirm_export: "analytics-confirm-export",
});

export interface AnalyticsPlaywriterScriptTemplate {
  readonly command: AnalyticsPlaywriterCommand;
  readonly definitionId: string;
  readonly action: BrowserActionKind;
  readonly phases: readonly ProgressState[];
  readonly source: string;
}

const ANALYTICS_STATE_KEY = "linkedinToolsAnalyticsPage";
const OPERATION_ID = INVOCATION_ID_RE;
const SHA256 = SHA256_HEX_RE;

export function buildAnalyticsPlaywriterScript(
  config: AnalyticsCommandConfig,
): AnalyticsPlaywriterScriptTemplate {
  assertAnalyticsCommandConfig(config);
  const command = ANALYTICS_COMMAND_BY_OPERATION[config.operation];
  const setup = `const c=${literal(config)};const op=c.exportOperation;const actionStartedAt=new Date().toISOString();`;
  const exactPage = `const p=state[${literal(ANALYTICS_STATE_KEY)}];if(!p)throw new Error("WORKFLOW_PAGE_MISSING");if(p.url()!==c.contract.url)throw new Error("WRONG_PAGE");`;
  const visibleCount = `const visibleCount=async(locator)=>{let count=0;for(let index=0;index<await locator.count();index+=1)if(await locator.nth(index).isVisible())count+=1;return count;};`;
  const finish = (extraEvidence = "") =>
    `const actionCompletedAt=new Date().toISOString();const data={schemaVersion:1,operation:c.operation,status:"completed",observedUrl:p.url(),evidence:{operationId:op.operationId,account:op.account,startDate:op.startDate,endDate:op.endDate,requestedRange:op.requestedRange,resultUrl:op.resultUrl,actionStartedAt,actionCompletedAt${extraEvidence}}};const __logs=await getLatestLogs({page:p,sinceLastCall:true});${progress("logs_captured")}console.log(JSON.stringify({schemaVersion:1,command:${literal(command)},ok:true,data,logs:__logs}));`;

  switch (config.operation) {
    case "navigate":
      return template(
        command,
        "navigate",
        [
          "observation_before",
          "navigation_started",
          "navigation_returned",
          "observation_after",
          "logs_captured",
        ],
        `${setup}${progress("observation_before")}${progress("navigation_started")}const p=state[${literal(ANALYTICS_STATE_KEY)}]??page;state[${literal(ANALYTICS_STATE_KEY)}]=p;await p.goto(c.contract.url,{waitUntil:"domcontentloaded"});${progress("navigation_returned")}if(p.url()!==c.contract.url)throw new Error("WRONG_PAGE");${progress("observation_after")}${finish()}`,
      );
    case "open_export":
      return template(
        command,
        "none",
        ["observation_before", "observation_after", "logs_captured"],
        `${setup}${progress("observation_before")}${exactPage}const exportLink=p.getByRole(c.contract.exportLink.role,{name:c.contract.exportLink.name,exact:true});if(await exportLink.count()!==1||!(await exportLink.isVisible()))throw new Error("SELECTOR_CONTRACT");await exportLink.click();const range=p.getByRole(c.contract.dateRangeButton.role,{name:c.contract.dateRangeButton.name,exact:true});if(await range.count()!==1||!(await range.isVisible()))throw new Error("SELECTOR_CONTRACT");await range.click();if(p.url()!==c.contract.url)throw new Error("WRONG_PAGE");${progress("observation_after")}${finish()}`,
      );
    case "observe_dialog":
      return template(
        command,
        "none",
        ["observation_before", "observation_after", "logs_captured"],
        `${setup}${progress("observation_before")}${exactPage}${visibleCount}const message=p.getByText(c.contract.confirmationText.name,{exact:true});const confirmButton=p.getByRole(c.contract.confirmButton.role,{name:c.contract.confirmButton.name,exact:true});const confirmationTextVisibleCount=await visibleCount(message);const confirmButtonVisibleCount=await visibleCount(confirmButton);if(confirmationTextVisibleCount!==1||confirmButtonVisibleCount!==1)throw new Error("SELECTOR_CONTRACT");${progress("observation_after")}${finish(",confirmationTextVisibleCount,confirmButtonVisibleCount")}`,
      );
    case "confirm_export":
      return template(
        command,
        "analytics_export",
        [
          "observation_before",
          "analytics_confirm_started",
          "analytics_confirm_returned",
          "observation_after",
          "logs_captured",
        ],
        `${setup}${progress("observation_before")}${exactPage}${visibleCount}const pre=c.preConfirmEvidence;if(pre.operationId!==op.operationId)throw new Error("SOURCE_MISMATCH");const message=p.getByText(c.contract.confirmationText.name,{exact:true});const confirmButton=p.getByRole(c.contract.confirmButton.role,{name:c.contract.confirmButton.name,exact:true});const confirmationTextVisibleCount=await visibleCount(message);const confirmButtonVisibleCount=await visibleCount(confirmButton);if(confirmationTextVisibleCount!==1||confirmButtonVisibleCount!==1)throw new Error("SELECTOR_CONTRACT");if(p.url()!==c.contract.url)throw new Error("WRONG_PAGE");${progress("analytics_confirm_started")}await confirmButton.click();${progress("analytics_confirm_returned")}if(p.url()!==c.contract.url)throw new Error("WRONG_PAGE");${progress("observation_after")}${finish(",preConfirmSnapshotId:pre.snapshotId,confirmationTextVisibleCount,confirmButtonVisibleCount")}`,
      );
  }
}

function template(
  command: AnalyticsPlaywriterCommand,
  action: BrowserActionKind,
  phases: readonly ProgressState[],
  source: string,
): AnalyticsPlaywriterScriptTemplate {
  return Object.freeze({
    command,
    definitionId: `analytics.${command}.v1`,
    action,
    phases: Object.freeze([...phases]),
    source,
  });
}

function progress(state: ProgressState): string {
  return `await __progress(${literal(state)});`;
}

function literal(value: unknown): string {
  const encoded = JSON.stringify(value);
  if (encoded === undefined) throw new TypeError("analytics script value is not serializable");
  return encoded.replaceAll("<", "\\u003c");
}

function assertAnalyticsCommandConfig(config: AnalyticsCommandConfig): void {
  if (config.operation === "confirm_export" && config.preConfirmEvidence === undefined)
    throw new TypeError("analytics confirm requires snapshot evidence");
  exactKeys(config as unknown as Record<string, unknown>, [
    "schemaVersion",
    "operation",
    "contract",
    "exportOperation",
    ...(config.operation === "confirm_export" ? ["preConfirmEvidence"] : []),
  ]);
  if (
    config.schemaVersion !== 1 ||
    JSON.stringify(config.contract) !== JSON.stringify(LINKEDIN_CONTENT_ANALYTICS_CONTRACT)
  )
    throw new TypeError("invalid analytics browser contract");
  const operation = config.exportOperation;
  exactKeys(operation as unknown as Record<string, unknown>, [
    "schemaVersion",
    "operationId",
    "account",
    "startDate",
    "endDate",
    "requestedRange",
    "resultUrl",
    "createdAt",
  ]);
  if (
    operation.schemaVersion !== 1 ||
    !OPERATION_ID.test(operation.operationId) ||
    operation.account.length === 0 ||
    operation.account.trim() !== operation.account ||
    operation.requestedRange !== "7 days" ||
    operation.resultUrl !== LINKEDIN_CONTENT_ANALYTICS_CONTRACT.url ||
    !isTimestamp(operation.createdAt) ||
    !isSevenDayRange(operation.startDate, operation.endDate)
  )
    throw new TypeError("invalid analytics export operation");
  if (config.operation === "confirm_export") {
    const evidence = config.preConfirmEvidence;
    if (evidence === undefined) throw new TypeError("analytics confirm requires snapshot evidence");
    exactKeys(evidence as unknown as Record<string, unknown>, [
      "schemaVersion",
      "operationId",
      "snapshotId",
      "capturedAt",
      "entryCount",
    ]);
    if (
      evidence.schemaVersion !== 1 ||
      evidence.operationId !== operation.operationId ||
      !SHA256.test(evidence.snapshotId) ||
      !isTimestamp(evidence.capturedAt) ||
      Date.parse(evidence.capturedAt) < Date.parse(operation.createdAt) ||
      !Number.isSafeInteger(evidence.entryCount) ||
      evidence.entryCount < 0
    )
      throw new TypeError("invalid analytics pre-confirm evidence");
  } else if (config.preConfirmEvidence !== undefined) {
    throw new TypeError("analytics snapshot evidence is only valid for Confirm");
  }
}

function exactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  if (!hasExactKeys(value, expected)) throw new TypeError("analytics command fields are invalid");
}

function isTimestamp(value: string): boolean {
  try {
    return new Date(value).toISOString() === value;
  } catch {
    return false;
  }
}

function isSevenDayRange(startDate: string, endDate: string): boolean {
  const pattern = /^\d{4}-\d{2}-\d{2}$/;
  if (!pattern.test(startDate) || !pattern.test(endDate)) return false;
  const start = new Date(`${startDate}T00:00:00.000Z`);
  const end = new Date(`${endDate}T00:00:00.000Z`);
  return (
    start.toISOString().slice(0, 10) === startDate &&
    end.toISOString().slice(0, 10) === endDate &&
    end.valueOf() - start.valueOf() === 6 * 24 * 60 * 60 * 1_000
  );
}
