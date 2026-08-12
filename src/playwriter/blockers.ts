import type { BlockerKind, TypedBlocker } from "./types.ts";

const rules: readonly [BlockerKind, TypedBlocker["retryability"], RegExp][] = [
  ["rate_limit_429", "safe_retry", /\b429\b|too many requests/i],
  ["weekly_limit", "terminal", /weekly (?:invitation|connection) limit/i],
  ["security_verification", "terminal", /security verification|quick security check|captcha/i],
  ["checkpoint", "terminal", /linkedin\.com\/checkpoint|checkpoint/i],
  ["unusual_activity", "terminal", /unusual activity|temporarily restricted/i],
  ["login", "terminal", /sign in|join linkedin|session expired|login required/i],
  [
    "session_lost",
    "safe_retry",
    /session \d+ not found|extension is not connected|relay.*restart/i,
  ],
  [
    "page_closed",
    "safe_retry",
    /Target page, context or browser has been closed|No open pages remain|page\.goto: Timeout/i,
  ],
  [
    "network_refusal",
    "terminal",
    /net::ERR_(?:CONNECTION_REFUSED|INTERNET_DISCONNECTED|NAME_NOT_RESOLVED|NETWORK_CHANGED)/i,
  ],
  ["source_mismatch", "terminal", /SOURCE_MISMATCH/],
  ["wrong_page", "terminal", /WRONG_PAGE|WORKFLOW_PAGE_(?:MISSING|AMBIGUOUS)/],
  ["already_pending", "terminal", /ALREADY_PENDING/],
  ["email_required", "terminal", /EMAIL_REQUIRED/],
  ["missing_more_actions", "safe_retry", /MISSING_MORE_ACTIONS/],
  ["missing_connect_menu", "safe_retry", /MISSING_CONNECT_MENU/],
  ["missing_send", "safe_retry", /MISSING_SEND/],
  ["disabled_send", "safe_retry", /DISABLED_SEND/],
  ["candidate_absent", "safe_retry", /CANDIDATE_ABSENT/],
  ["row_load_timeout", "safe_retry", /ROW_LOAD_TIMEOUT/],
  ["no_rows", "safe_retry", /NO_ROWS/],
  ["stalled_navigation", "safe_retry", /STALLED_NAVIGATION/],
  ["source_exhausted", "terminal", /SOURCE_EXHAUSTED/],
  ["unclear_confirmation", "possible_send", /UNCLEAR_CONFIRMATION/],
  ["preparation_mismatch", "terminal", /PREPARATION_MISMATCH/],
  ["preparation_stale", "terminal", /PREPARATION_STALE/],
  ["commit_uncertainty", "possible_send", /COMMIT_SEND_UNCERTAIN/],
  [
    "evidence_corrupt",
    "safe_retry",
    /EVIDENCE_CORRUPT|DIAGNOSTIC_(?:LOGS?_INVALID|ARTIFACT_TOO_LARGE)/,
  ],
  ["evidence_finalization", "safe_retry", /EVIDENCE_FINALIZATION/],
  ["selector_contract", "terminal", /SELECTOR_CONTRACT|strict mode violation|locator.*not found/i],
];

function diagnosticEntries(value: string): readonly string[] {
  try {
    const parsed: unknown = JSON.parse(value);
    if (Array.isArray(parsed)) {
      return parsed.map((entry: unknown) => {
        if (typeof entry === "string") return entry;
        if (typeof entry === "object" && entry !== null && "text" in entry) {
          const text = entry.text;
          if (typeof text === "string") return text;
        }
        return JSON.stringify(entry) ?? String(entry);
      });
    }
  } catch {
    // Non-JSON stdout/stderr remains a valid blocker input.
  }
  return [value];
}

function isBenignRecaptchaCspNotice(value: string): boolean {
  return (
    /(?:recaptcha|google\.com\/recaptcha)/i.test(value) &&
    /violates the following content security policy directive/i.test(value) &&
    /policy is report-only/i.test(value)
  );
}

export function detectBlocker(...texts: readonly (string | undefined)[]): TypedBlocker | undefined {
  const text = texts
    .filter((value): value is string => Boolean(value))
    .flatMap(diagnosticEntries)
    .filter((value) => !isBenignRecaptchaCspNotice(value))
    .join("\n");
  for (const [kind, retryability, pattern] of rules) {
    const m = pattern.exec(text);
    if (m) return { kind, evidence: m[0], retryability };
  }
}
