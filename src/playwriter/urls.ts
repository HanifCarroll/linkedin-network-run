export type WorkflowPage =
  | "savedSearchCatalog"
  | "candidateResults"
  | "sentInvitations"
  | "salesLead";
export const WORKFLOW_STATE_KEYS = {
  savedSearchCatalog: "networkSavedSearchCatalogPage",
  candidateResults: "networkCandidateResultsPage",
  sentInvitations: "networkSentInvitationsPage",
  salesLead: "networkSalesLeadPage",
} as const;

export function isAllowedWorkflowUrl(page: WorkflowPage, raw: string): boolean {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }
  if (
    u.protocol !== "https:" ||
    u.hostname !== "www.linkedin.com" ||
    u.username ||
    u.password ||
    u.port ||
    u.hash
  )
    return false;
  if (page === "sentInvitations")
    return u.pathname === "/mynetwork/invitation-manager/sent/" && !u.search;
  if (page === "salesLead")
    return /^\/sales\/lead\/[A-Za-z0-9_-]+\/?$/.test(u.pathname) && !u.search;
  if (u.pathname !== "/sales/search/people") return false;
  const entries = [...u.searchParams.entries()];
  if (page === "savedSearchCatalog") return entries.length === 0;
  return (
    entries.length === 1 && entries[0]?.[0] === "savedSearchId" && entries[0][1].trim().length > 0
  );
}
export function assertAllowedWorkflowUrl(page: WorkflowPage, raw: string): void {
  if (!isAllowedWorkflowUrl(page, raw)) throw new TypeError(`URL is not allowed for ${page}`);
}
