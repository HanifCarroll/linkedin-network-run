import type { JobRow } from "./types.ts";

const LINKEDIN_PROFILE_RE = /^https:\/\/(?:www\.)?linkedin\.com\/in\//i;

/**
 * Normalize a LinkedIn profile URL to a canonical comparison key: trim, strip
 * query/hash/trailing slash, and case-fold. Returns "" for empty input and
 * falls back to a case-folded trim when the URL is not parseable.
 */
export function normalizeProfileUrl(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length === 0) return "";
  try {
    const url = new URL(trimmed);
    url.hash = "";
    url.search = "";
    if (url.hostname.toLowerCase() === "linkedin.com") url.hostname = "www.linkedin.com";
    const path = url.pathname.replace(/\/+$/, "");
    return `${url.protocol}//${url.host}${path}`.toLowerCase();
  } catch {
    return trimmed.toLowerCase();
  }
}

/**
 * The normalized first hiring-team profile URL for a job, or null when the
 * job has no usable LinkedIn /in/ profile to message.
 */
export function recipientProfileUrl(job: Pick<JobRow, "hiringTeam">): string | null {
  const raw = job.hiringTeam[0]?.profileUrl ?? "";
  if (raw.trim().length === 0 || !LINKEDIN_PROFILE_RE.test(raw.trim())) return null;
  return normalizeProfileUrl(raw);
}
