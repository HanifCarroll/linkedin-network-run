import type { Database } from "bun:sqlite";
import { createHash, randomUUID } from "node:crypto";
import { CliError } from "../core/errors.ts";
import { outreachKindFor } from "../view/grouping.ts";
import { JobsEngine, recipientProfileUrl } from "./engine.ts";
import type { JobRow } from "./types.ts";

const sha = (value: string) => createHash("sha256").update(value).digest("hex");
const fingerprint = (job: JobRow, recipient: string) =>
  sha(JSON.stringify({ id: job.id, recipient, connectionNote: job.connectionNote }));
const object = (value: unknown, name: string): Record<string, unknown> => {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new CliError("INVALID_ARGUMENT", `${name} must be a JSON object`, { exitCode: 2 });
  return value as Record<string, unknown>;
};
const text = (value: unknown, name: string, max: number) => {
  if (typeof value !== "string" || value.trim() === "" || value.length > max)
    throw new CliError(
      "INVALID_ARGUMENT",
      `${name} must be a non-empty string of at most ${max} characters`,
      { exitCode: 2 },
    );
  return value.trim();
};
const exact = (value: Record<string, unknown>, keys: readonly string[], name: string) => {
  if (Object.keys(value).length !== keys.length || keys.some((key) => !Object.hasOwn(value, key)))
    throw new CliError("INVALID_ARGUMENT", `${name} contains an unknown or missing field`, {
      exitCode: 2,
    });
};

/** Exact production endpoint observed during the approved caller-owned Chrome spike on 2026-08-20. */
export const CONTRACT_OUTREACH_ENDPOINTS: readonly string[] = [
  "https://www.linkedin.com/voyager/api/voyagerRelationshipsDashMemberRelationships?action=verifyQuotaAndCreateV2&decorationId=com.linkedin.voyager.dash.deco.relationships.InvitationCreationResultWithInvitee-2",
];
export type ContractOutreachContracts = {
  readonly urls: readonly string[];
  readonly requireRecipientUrn: boolean;
};

function current(database: Database, id: string | undefined) {
  const job =
    id === undefined
      ? new JobsEngine(database)
          .approvedDrafts()
          .find(
            (candidate) =>
              outreachKindFor(candidate) === "direct" ||
              (outreachKindFor(candidate) === "application_followup" &&
                candidate.appliedAt !== null),
          )
      : new JobsEngine(database).requireJob(id);
  if (!job)
    throw new CliError("JOBS_NOTHING_TO_SEND", "no approved prospects to send", {
      exitCode: 2,
    });
  if (job.status !== "drafted" || job.review !== "approved")
    throw new CliError("JOBS_NOT_APPROVED", `job ${job.id} is not an approved draft`, {
      exitCode: 2,
    });
  if (outreachKindFor(job) === "application_followup" && job.appliedAt === null)
    throw new CliError(
      "JOBS_CONTRACT_APPLICATION_REQUIRED",
      `job ${job.id} requires jobs applied before contract outreach`,
      { exitCode: 2 },
    );
  const recipient = recipientProfileUrl(job);
  if (!recipient || job.message === null)
    throw new CliError("JOBS_NO_DRAFT", `job ${job.id} has no usable recipient or draft`, {
      exitCode: 2,
    });
  if (job.connectionNote === "")
    throw new CliError("JOBS_NO_CONNECTION_NOTE", `job ${job.id} has no reviewed connection note`, {
      exitCode: 2,
    });
  return { job, recipient, fingerprint: fingerprint(job, recipient) };
}

export function prepareContractOutreach(database: Database, id: string | undefined, now: string) {
  const { job, recipient, fingerprint: draftFingerprint } = current(database, id);
  const open = database
    .query<{ attempt_id: string }, [string]>(
      "SELECT attempt_id FROM jobs_contract_outreach_receipts WHERE recipient_url = ? AND state IN ('prepared','possible')",
    )
    .get(recipient);
  if (open)
    throw new CliError(
      "JOBS_CONTRACT_OUTREACH_RECONCILIATION_REQUIRED",
      `job ${job.id} has unresolved invitation attempt ${open.attempt_id}`,
      { exitCode: 2 },
    );
  const duplicate = database
    .query<{ attempt_id: string }, [string]>(
      "SELECT attempt_id FROM jobs_contract_outreach_receipts WHERE recipient_url = ? AND state = 'confirmed'",
    )
    .get(recipient);
  if (duplicate)
    throw new CliError(
      "JOBS_CONTRACT_OUTREACH_RECIPIENT_DUPLICATE",
      `recipient ${recipient} already has a confirmed invitation`,
      { exitCode: 2 },
    );
  const attemptId = `jobs-contract-outreach-v1:${randomUUID()}`;
  database
    .prepare(
      "INSERT INTO jobs_contract_outreach_receipts (attempt_id,job_id,recipient_url,draft_fingerprint,state,evidence_json,created_at,updated_at) VALUES (?,?,?,?, 'prepared','{}',?,?)",
    )
    .run(attemptId, job.id, recipient, draftFingerprint, now, now);
  return {
    attemptId,
    createdAt: now,
    jobId: job.id,
    route: outreachKindFor(job),
    recipientUrl: recipient,
    recipientName: job.hiringTeam[0]?.name ?? "",
    note: job.connectionNote,
    draftFingerprint,
    action: "visible LinkedIn Connect/Send invitation",
    evidenceContract: "connection-request-recipient-bound-plus-dialog-closed-v2",
  };
}

function evidence(
  value: Record<string, unknown>,
  complete: boolean,
  provenNoSend: boolean,
  contracts: ContractOutreachContracts,
) {
  const e = object(value.evidence, "evidence");
  if (provenNoSend) {
    exact(e, ["reconciliation"], "evidence");
    const r = object(e.reconciliation, "evidence.reconciliation");
    exact(r, ["reason", "noRequestObserved", "operatorConfirmed"], "evidence.reconciliation");
    if (
      typeof r.reason !== "string" ||
      !r.reason.trim() ||
      r.noRequestObserved !== true ||
      r.operatorConfirmed !== true
    )
      throw new CliError(
        "INVALID_ARGUMENT",
        "proven_no_send requires explicit reconciliation evidence",
        { exitCode: 2 },
      );
    return e;
  }
  if (!complete) {
    exact(e, ["commitStarted"], "evidence");
    if (e.commitStarted !== true)
      throw new CliError("INVALID_ARGUMENT", "possible evidence must mark commitStarted", {
        exitCode: 2,
      });
    return e;
  }
  exact(e, ["request", "invitation"], "evidence");
  const request = object(e.request, "evidence.request");
  const invitation = object(e.invitation, "evidence.invitation");
  if (
    Object.keys(request).some(
      (key) => !["method", "url", "status", "bodySha256", "recipientUrn"].includes(key),
    )
  )
    throw new CliError("INVALID_ARGUMENT", "evidence.request contains an unknown field", {
      exitCode: 2,
    });
  if (["method", "url", "status", "bodySha256"].some((key) => !Object.hasOwn(request, key)))
    throw new CliError("INVALID_ARGUMENT", "evidence.request is missing a required field", {
      exitCode: 2,
    });
  exact(invitation, ["dialogClosed"], "evidence.invitation");
  if (
    request.method !== "POST" ||
    typeof request.url !== "string" ||
    !Number.isInteger(request.status) ||
    Number(request.status) < 200 ||
    Number(request.status) > 299 ||
    typeof request.bodySha256 !== "string"
  )
    throw new CliError("INVALID_ARGUMENT", "request evidence is incomplete", { exitCode: 2 });
  if (!contracts.urls.includes(request.url))
    throw new CliError(
      "JOBS_CONTRACT_OUTREACH_ENDPOINT_UNVERIFIED",
      "connection endpoint is not in the approved exact contract",
      { exitCode: 2 },
    );
  if (
    contracts.requireRecipientUrn &&
    (typeof request.recipientUrn !== "string" || !request.recipientUrn.trim())
  )
    throw new CliError(
      "JOBS_CONTRACT_OUTREACH_RECIPIENT_UNBOUND",
      "connection evidence lacks a bound recipient URN",
      { exitCode: 2 },
    );
  if (invitation.dialogClosed !== true)
    throw new CliError("INVALID_ARGUMENT", "the invitation dialog must close after the request", {
      exitCode: 2,
    });
  return e;
}

export function recordContractOutreach(
  database: Database,
  payload: unknown,
  now: string,
  contracts: ContractOutreachContracts = {
    urls: CONTRACT_OUTREACH_ENDPOINTS,
    requireRecipientUrn: true,
  },
) {
  const value = object(payload, "contract outreach record");
  exact(
    value,
    ["attemptId", "jobId", "route", "draftFingerprint", "state", "evidence"],
    "contract outreach record",
  );
  const attemptId = text(value.attemptId, "attemptId", 200);
  const jobId = text(value.jobId, "jobId", 200);
  const state = value.state;
  if (!["possible", "confirmed", "proven_no_send"].includes(String(state)))
    throw new CliError("INVALID_ARGUMENT", "state must be possible, confirmed, or proven_no_send", {
      exitCode: 2,
    });
  const row = database
    .query<
      { job_id: string; state: string; draft_fingerprint: string; evidence_json: string },
      [string]
    >(
      "SELECT job_id,state,draft_fingerprint,evidence_json FROM jobs_contract_outreach_receipts WHERE attempt_id = ?",
    )
    .get(attemptId);
  if (!row)
    throw new CliError(
      "JOBS_CONTRACT_OUTREACH_ATTEMPT_UNKNOWN",
      "unknown contract outreach attempt",
      { exitCode: 2 },
    );
  const { job, fingerprint: currentFingerprint } = current(database, jobId);
  if (
    row.job_id !== jobId ||
    value.route !== outreachKindFor(job) ||
    value.draftFingerprint !== currentFingerprint ||
    row.draft_fingerprint !== currentFingerprint
  )
    throw new CliError(
      "JOBS_CONTRACT_OUTREACH_ATTEMPT_MISMATCH",
      "record does not match the prepared contract prospect",
      { exitCode: 2 },
    );
  const complete = state === "confirmed";
  const provenNoSend = state === "proven_no_send";
  const ev = evidence(value, complete, provenNoSend, contracts);
  const serialized = JSON.stringify(ev);
  if (row.state === state && row.evidence_json === serialized)
    return { attemptId, jobId, state, idempotent: true };
  if (
    !(
      (row.state === "prepared" &&
        ["possible", "confirmed", "proven_no_send"].includes(String(state))) ||
      (row.state === "possible" && ["confirmed", "proven_no_send"].includes(String(state)))
    )
  )
    throw new CliError(
      "JOBS_CONTRACT_OUTREACH_CONFLICTING_REPLAY",
      "attempt replay conflicts with its stored receipt",
      { exitCode: 2 },
    );
  database
    .prepare(
      "UPDATE jobs_contract_outreach_receipts SET state=?,evidence_json=?,updated_at=? WHERE attempt_id=?",
    )
    .run(String(state), serialized, now, attemptId);
  return { attemptId, jobId, state, idempotent: false };
}
