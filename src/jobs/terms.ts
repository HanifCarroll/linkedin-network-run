/**
 * Canonical LinkedIn Jobs search phrases for contract product/software
 * engineering outreach. Each entry is an exact-phrase query — the embedded
 * quotes force LinkedIn's phrase matching instead of loose keyword OR, which
 * keeps results on-role (product/software/fullstack/frontend/backend).
 *
 * Collect with:
 *   jobs collect --keywords "<phrase>" --location "United States" ...
 *
 * Reorder or extend freely; the agent runs the full list to build the pool.
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
