import type { Identity } from "./types.ts";

const SALES_ID = /\/sales\/lead\/([^,/?#]+)/i;
const PUBLIC_PROFILE = /^https:\/\/(?:(?:[a-z]{2}|www)\.)?linkedin\.com\/in\/([^/?#]+)/i;

export function canonicalPublicUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const match = value.trim().match(PUBLIC_PROFILE);
  return match?.[1] ? `https://www.linkedin.com/in/${match[1].toLowerCase()}` : null;
}

export function salesNavigatorId(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value !== "string") continue;
    const match = value.match(SALES_ID);
    if (match?.[1]) return match[1];
    const urn = value.match(/(?:sales_profile|lead):([^,\s]+)/i);
    if (urn?.[1]) return urn[1];
  }
  return null;
}

export function buildIdentity(input: {
  profileUrl?: unknown;
  publicProfileUrl?: unknown;
  salesProfileUrn?: unknown;
  leadKey?: unknown;
}): Identity | null {
  const salesId = salesNavigatorId(input.profileUrl, input.salesProfileUrn);
  const publicUrl = canonicalPublicUrl(input.publicProfileUrl);
  const leadKey = typeof input.leadKey === "string" && input.leadKey.trim() ? input.leadKey : null;
  const canonicalKey = salesId
    ? `sales:${salesId}`
    : publicUrl
      ? `public:${publicUrl}`
      : leadKey
        ? `lead:${leadKey}`
        : null;
  return canonicalKey
    ? { canonicalKey, salesNavigatorId: salesId, publicProfileUrl: publicUrl, leadKey }
    : null;
}
