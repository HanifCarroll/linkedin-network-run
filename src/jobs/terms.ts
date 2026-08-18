/**
 * Canonical LinkedIn Jobs search phrases for contract product/software
 * engineering outreach. Each entry is an exact-phrase query — the embedded
 * quotes force LinkedIn's phrase matching instead of loose keyword OR, which
 * keeps results on-role (product/software/fullstack/frontend/backend).
 *
 * These remain reusable metadata configuration for capture-start/search-config;
 * they are not production-approved intake terms yet.
 */
export const JOB_SEARCH_TERMS: readonly string[] = [
  '"product engineer"',
  '"software engineer"',
  '"software developer"',
  '"full stack developer"',
  '"full stack engineer"',
  '"frontend engineer"',
  '"backend engineer"',
  '"ai product engineer"',
];
