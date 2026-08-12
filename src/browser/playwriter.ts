/**
 * The sole browser boundary for this repository.
 *
 * Foundation scope deliberately exposes no live browser operations. Future browser work must be
 * implemented through the pinned `playwriter` package/CLI. Do not add Playwright, direct CDP,
 * browser leases, cross-automation locks, or Chrome-control fallbacks here.
 */
export const browserBoundary = {
  packageName: "playwriter",
  version: "0.4.0",
  implemented: false,
} as const;
