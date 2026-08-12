import { createHash } from "node:crypto";

/** Invocation / operation id: lowercase start, then 7–63 of [a-z0-9_-]. */
export const INVOCATION_ID_RE = /^[a-z0-9][a-z0-9_-]{7,63}$/;

/** Lowercase hex SHA-256 digest. */
export const SHA256_HEX_RE = /^[a-f0-9]{64}$/;

/** Capturing form: pwprep:<prepareInvocationId>:<32-hex token>:<64-hex fingerprint>. */
export const SEND_PREPARATION_RECEIPT_ID_RE =
  /^pwprep:([a-z0-9][a-z0-9_-]{7,63}):([a-f0-9]{32}):([a-f0-9]{64})$/;

/** Non-capturing acceptance form for full receiptId strings. */
export const SEND_PREPARATION_RECEIPT_ID_ACCEPT_RE =
  /^pwprep:[a-z0-9][a-z0-9_-]{7,63}:[a-f0-9]{32}:[a-f0-9]{64}$/;

/** Sales Navigator row identity from data-scroll-into-view. */
export const SOURCE_ROW_ID_RE = /^urn:li:fs_salesProfile:[A-Za-z0-9_-]+$/;

/** Pagination cursor label, 1-based. */
export const PAGE_CURSOR_RE = /^Page [1-9][0-9]*$/;

/** True when `value` has exactly the given keys (order-independent). */
export function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

/** SHA-256 hex of `JSON.stringify(value)`. */
export function sha256Json(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

export type TerminalFingerprintInput = {
  readonly sourceContractFingerprint: string;
  readonly searchUrl: string;
  readonly pageIdentity: string;
  readonly cursorIdentity: string;
  readonly stableRowIds: readonly string[];
  readonly rowCount: number;
  readonly nextControl: "disabled";
};

/** Canonical terminal-observation fingerprint shared by network + playwriter. */
export function terminalFingerprint(input: TerminalFingerprintInput): string {
  return sha256Json({
    schemaVersion: 1,
    kind: "network_source_terminal_fingerprint",
    sourceContractFingerprint: input.sourceContractFingerprint,
    searchUrl: input.searchUrl,
    pageIdentity: input.pageIdentity,
    cursorIdentity: input.cursorIdentity,
    stableRowIds: input.stableRowIds,
    rowCount: input.rowCount,
    nextControl: input.nextControl,
  });
}
