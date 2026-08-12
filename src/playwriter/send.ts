import { createHash } from "node:crypto";
import { SEND_PREPARATION_RECEIPT_ID_RE } from "../core/evidence-contract.ts";
import type { CandidateIdentity, SendPreparationReceipt } from "./types.ts";
import { SEND_PREPARATION_TTL_MS } from "./types.ts";

const RECEIPT_ID = SEND_PREPARATION_RECEIPT_ID_RE;

export const SEND_PREPARATION_TARGET = Object.freeze({
  page: Object.freeze({
    stateKey: "networkCandidateResultsPage" as const,
  }),
  modal: Object.freeze({
    selector: "[role='dialog'], .artdeco-modal, [data-test-modal]" as const,
    count: 1 as const,
    visible: true as const,
  }),
  control: Object.freeze({
    selector: "button" as const,
    labels: ["Send Invitation", "Send invite", "Send now", "Send without a note", "Send"] as const,
    count: 1 as const,
    visible: true as const,
    enabled: true as const,
  }),
});

export interface SendPreparationReceiptId {
  readonly prepareInvocationId: string;
  readonly token: string;
  readonly fingerprint: string;
}

export interface BrowserSendPreparationBase {
  readonly schemaVersion: 1;
  readonly kind: "playwriter_network_send_preparation";
  readonly prepareInvocationId: string;
  readonly sessionId: number;
  readonly attemptId: string;
  readonly candidate: CandidateIdentity;
  readonly preparedAt: string;
  readonly expiresAt: string;
  readonly token: string;
  readonly page: {
    readonly stateKey: "networkCandidateResultsPage";
    readonly url: string;
  };
  readonly modal: typeof SEND_PREPARATION_TARGET.modal;
  readonly control: typeof SEND_PREPARATION_TARGET.control;
}

export type BrowserSendPreparation = BrowserSendPreparationBase & {
  readonly fingerprint: string;
};

export function parseSendPreparationReceiptId(value: string): SendPreparationReceiptId {
  const match = RECEIPT_ID.exec(value);
  if (match?.[1] === undefined || match[2] === undefined || match[3] === undefined)
    throw new TypeError("invalid send preparation receiptId");
  return {
    prepareInvocationId: match[1],
    token: match[2],
    fingerprint: match[3],
  };
}

export function sendPreparationExpiresAt(preparedAt: string): string {
  return new Date(Date.parse(preparedAt) + SEND_PREPARATION_TTL_MS).toISOString();
}

export function sendPreparationFingerprint(base: BrowserSendPreparationBase): string {
  return createHash("sha256").update(JSON.stringify(base)).digest("hex");
}

export function browserSendPreparation(
  receipt: SendPreparationReceipt,
  sessionId: number,
): BrowserSendPreparation {
  const id = parseSendPreparationReceiptId(receipt.receiptId);
  const base: BrowserSendPreparationBase = {
    schemaVersion: 1,
    kind: "playwriter_network_send_preparation",
    prepareInvocationId: id.prepareInvocationId,
    sessionId,
    attemptId: receipt.attemptId,
    candidate: receipt.candidate,
    preparedAt: receipt.preparedAt,
    expiresAt: sendPreparationExpiresAt(receipt.preparedAt),
    token: id.token,
    page: {
      stateKey: SEND_PREPARATION_TARGET.page.stateKey,
      url: receipt.candidate.searchUrl,
    },
    modal: SEND_PREPARATION_TARGET.modal,
    control: SEND_PREPARATION_TARGET.control,
  };
  if (sendPreparationFingerprint(base) !== id.fingerprint)
    throw new TypeError("send preparation fingerprint mismatch");
  return Object.freeze({ ...base, fingerprint: id.fingerprint });
}

export function deepFreeze<T>(value: T): Readonly<T> {
  if (typeof value !== "object" || value === null || Object.isFrozen(value)) return value;
  for (const child of Object.values(value as Record<string, unknown>)) deepFreeze(child);
  return Object.freeze(value);
}
