export interface PostSendCandidateUrl {
  readonly searchUrl: string;
  readonly salesLeadId: string;
}

/** Canonical origin for post-send page checks (TS + generated page-JS). */
export const LINKEDIN_WWW_ORIGIN = "https://www.linkedin.com";

/** Canonical sent-invitations path (exact pathname, empty search/hash). */
export const SENT_INVITATIONS_PATHNAME = "/mynetwork/invitation-manager/sent/";

/**
 * Shared post-send URL forms: candidate results (optional sessionId), sales lead, sent list.
 * Single source for TS validation and generated page-JS so the checks cannot drift.
 */
export function matchPostSendUrl(
  observed: URL,
  source: URL,
  salesLeadId: string,
): {
  readonly isCandidateResultsPage: boolean;
  readonly isSalesLeadPage: boolean;
  readonly isSentInvitationsPage: boolean;
} {
  const observedKeys = [...observed.searchParams.keys()];
  const isCandidateResultsPage =
    observed.origin === source.origin &&
    observed.pathname === source.pathname &&
    observed.hash === "" &&
    observed.searchParams.get("savedSearchId") === source.searchParams.get("savedSearchId") &&
    (observedKeys.length === 1 ||
      (observedKeys.length === 2 &&
        observedKeys.includes("sessionId") &&
        (observed.searchParams.get("sessionId")?.trim().length ?? 0) > 0));
  const isSalesLeadPage =
    observed.origin === LINKEDIN_WWW_ORIGIN &&
    observed.hash === "" &&
    new RegExp(`^/sales/lead/${salesLeadId}/?$`).test(observed.pathname);
  const isSentInvitationsPage =
    observed.origin === LINKEDIN_WWW_ORIGIN &&
    observed.pathname === SENT_INVITATIONS_PATHNAME &&
    observed.hash === "" &&
    observed.search === "";
  return { isCandidateResultsPage, isSalesLeadPage, isSentInvitationsPage };
}

export function isExpectedPostSendUrl(raw: string, candidate: PostSendCandidateUrl): boolean {
  let observed: URL;
  let source: URL;
  try {
    observed = new URL(raw);
    source = new URL(candidate.searchUrl);
  } catch {
    return false;
  }
  const match = matchPostSendUrl(observed, source, candidate.salesLeadId);
  return match.isCandidateResultsPage || match.isSalesLeadPage || match.isSentInvitationsPage;
}

/**
 * Generated page-JS for post-send URL validation.
 * Defines `postCandidate`, `observedPostSendUrl`, `isCandidateResultsPage`, `expectedPostSendPage`
 * and throws WRONG_PAGE when the observed URL is not one of the three allowed forms.
 * Must stay equivalent to `isExpectedPostSendUrl` / `matchPostSendUrl`.
 *
 * Note: page-JS builds the sales-lead path regex by concatenating the runtime lead id
 * (same as the prior hand-written script). TS escapes the id when building the RegExp.
 */
export function buildPostSendPageValidationJs(candidateExpression: string): string {
  const origin = JSON.stringify(LINKEDIN_WWW_ORIGIN);
  const sentPath = JSON.stringify(SENT_INVITATIONS_PATHNAME);
  return (
    `const postCandidate=${candidateExpression};` +
    `const observedPostSendUrl=p.url();` +
    `let isCandidateResultsPage=false;` +
    `let expectedPostSendPage=false;` +
    `try{` +
    `const observed=new URL(observedPostSendUrl);` +
    `const source=new URL(postCandidate.searchUrl);` +
    `const observedKeys=[...observed.searchParams.keys()];` +
    `isCandidateResultsPage=observed.origin===source.origin&&observed.pathname===source.pathname&&observed.hash===""&&observed.searchParams.get("savedSearchId")===source.searchParams.get("savedSearchId")&&(observedKeys.length===1||(observedKeys.length===2&&observedKeys.includes("sessionId")&&(observed.searchParams.get("sessionId")?.trim().length??0)>0));` +
    `const isSalesLeadPage=observed.origin===${origin}&&observed.hash===""&&new RegExp("^/sales/lead/"+postCandidate.salesLeadId+"/?$").test(observed.pathname);` +
    `const isSentInvitationsPage=observed.origin===${origin}&&observed.pathname===${sentPath}&&observed.hash===""&&observed.search==="";` +
    `expectedPostSendPage=isCandidateResultsPage||isSalesLeadPage||isSentInvitationsPage;` +
    `}catch{}` +
    `if(!expectedPostSendPage)throw new Error("WRONG_PAGE observed_url="+observedPostSendUrl);`
  );
}
