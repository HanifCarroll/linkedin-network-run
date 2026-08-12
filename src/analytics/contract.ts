import type {
  AnalyticsBrowserContract,
  AnalyticsCommandConfig,
  AnalyticsExportOperation,
  AnalyticsOperation,
  AnalyticsPreConfirmEvidence,
} from "./types.ts";

export const LINKEDIN_CONTENT_ANALYTICS_CONTRACT: AnalyticsBrowserContract = Object.freeze({
  url: "https://www.linkedin.com/analytics/creator/content/",
  exportLink: Object.freeze({ role: "link", name: "Export", exact: true }),
  dateRangeButton: Object.freeze({ role: "button", name: "7 days", exact: true }),
  confirmationText: Object.freeze({
    name: "Your analytics export is being prepared. Confirm to begin downloading.",
    exact: true,
  }),
  confirmButton: Object.freeze({ role: "button", name: "Confirm", exact: true }),
});

export function commandConfig(
  operation: AnalyticsOperation,
  exportOperation: AnalyticsExportOperation,
  preConfirmEvidence?: AnalyticsPreConfirmEvidence,
): AnalyticsCommandConfig {
  return Object.freeze({
    schemaVersion: 1,
    operation,
    contract: LINKEDIN_CONTENT_ANALYTICS_CONTRACT,
    exportOperation,
    ...(preConfirmEvidence === undefined ? {} : { preConfirmEvidence }),
  });
}
