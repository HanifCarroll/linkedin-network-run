import type { Database } from "bun:sqlite";
import { createHash, randomUUID } from "node:crypto";
import { CliError } from "../core/errors.ts";
import { outreachKindFor } from "../view/grouping.ts";
import { JobsEngine, recipientProfileUrl } from "./engine.ts";
import type { JobRow } from "./types.ts";

const sha = (value: string) => createHash("sha256").update(value).digest("hex");
const draftFingerprint = (job: JobRow) =>
  sha(JSON.stringify({ id: job.id, subject: job.subject, message: job.message }));
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
const exactKeys = (value: Record<string, unknown>, keys: readonly string[], name: string) => {
  if (Object.keys(value).some((key) => !keys.includes(key)))
    throw new CliError("INVALID_ARGUMENT", `${name} contains an unknown field`, { exitCode: 2 });
};

function currentJob(database: Database, jobId: string) {
  const job = new JobsEngine(database).requireJob(jobId);
  const recipient = recipientProfileUrl(job);
  if (recipient === null || job.message === null)
    throw new CliError("JOBS_NO_DRAFT", `job ${job.id} has no usable recipient or draft`, {
      exitCode: 2,
    });
  return { job, recipient, route: outreachKindFor(job), fingerprint: draftFingerprint(job) };
}

export type ChromeSendContracts = Readonly<
  Partial<
    Record<
      "dm" | "inmail",
      { readonly urls: readonly string[]; readonly requireRecipientUrn: boolean }
    >
  >
>;

function validateEvidence(
  value: Record<string, unknown>,
  transport: "dm" | "inmail",
  complete: boolean,
  provenNoSend = false,
  contracts: ChromeSendContracts = {},
) {
  const evidence = object(value.evidence, "evidence");
  if (provenNoSend) {
    exactKeys(evidence, ["reconciliation"], "evidence");
    const reconciliation = object(evidence.reconciliation, "evidence.reconciliation");
    exactKeys(
      reconciliation,
      ["reason", "noRequestObserved", "operatorConfirmed"],
      "evidence.reconciliation",
    );
    if (
      typeof reconciliation.reason !== "string" ||
      reconciliation.reason.trim() === "" ||
      reconciliation.reason.length > 500 ||
      reconciliation.noRequestObserved !== true ||
      reconciliation.operatorConfirmed !== true
    )
      throw new CliError(
        "INVALID_ARGUMENT",
        "proven_no_send requires explicit reconciliation evidence",
        { exitCode: 2 },
      );
    return evidence;
  }
  exactKeys(evidence, ["request", "thread"], "evidence");
  const request = object(evidence.request, "evidence.request");
  const thread = object(evidence.thread, "evidence.thread");
  const requestKeys =
    transport === "dm"
      ? ["method", "url", "status", "bodySha256", "recipientUrn"]
      : ["method", "url", "status", "bodySha256", "recipientUrn", "subjectSha256"];
  exactKeys(request, requestKeys, "evidence.request");
  exactKeys(
    thread,
    transport === "dm"
      ? ["composerGone", "messageVisible"]
      : ["composerGone", "messageVisible", "threadVisible"],
    "evidence.thread",
  );
  if (!complete) return evidence;
  if (
    request.method !== "POST" ||
    typeof request.url !== "string" ||
    request.url.trim() === "" ||
    typeof request.status !== "number" ||
    !Number.isInteger(request.status) ||
    request.status < 200 ||
    request.status > 299 ||
    typeof request.bodySha256 !== "string"
  )
    throw new CliError("INVALID_ARGUMENT", "request evidence is incomplete", { exitCode: 2 });
  if (transport === "inmail" && typeof request.subjectSha256 !== "string")
    throw new CliError("INVALID_ARGUMENT", "InMail evidence requires subjectSha256", {
      exitCode: 2,
    });
  const contract = contracts[transport];
  if (contract === undefined || !contract.urls.includes(request.url))
    throw new CliError(
      "JOBS_SEND_ENDPOINT_UNVERIFIED",
      `${transport} endpoint is not in the approved exact contract`,
      { exitCode: 2 },
    );
  if (
    contract.requireRecipientUrn &&
    (typeof request.recipientUrn !== "string" || request.recipientUrn.trim() === "")
  )
    throw new CliError(
      "JOBS_SEND_RECIPIENT_UNBOUND",
      `${transport} evidence lacks a bound recipient URN`,
      { exitCode: 2 },
    );
  if (
    thread.composerGone !== true ||
    thread.messageVisible !== true ||
    (transport === "inmail" && thread.threadVisible !== true)
  )
    throw new CliError("INVALID_ARGUMENT", "thread evidence is incomplete", { exitCode: 2 });
  return evidence;
}

export function prepareChromeSend(database: Database, id: string | undefined, now: string) {
  const engine = new JobsEngine(database);
  const job = id === undefined ? engine.approvedDrafts()[0] : engine.requireJob(id);
  if (job === undefined)
    throw new CliError("JOBS_NOTHING_TO_SEND", "no approved drafts to send", { exitCode: 2 });
  if (job.status !== "drafted" || job.review !== "approved")
    throw new CliError("JOBS_NOT_APPROVED", `job ${job.id} is not an approved draft`, {
      exitCode: 2,
    });
  if (outreachKindFor(job) === "application_followup" && job.appliedAt === null)
    throw new CliError(
      "JOBS_APPLICATION_REQUIRED",
      `job ${job.id} requires jobs applied before sending`,
      { exitCode: 2 },
    );
  const { recipient, route, fingerprint } = currentJob(database, job.id);
  const open = database
    .query<{ attempt_id: string }, [string]>(
      "SELECT attempt_id FROM jobs_chrome_send_receipts WHERE job_id = ? AND state IN ('prepared', 'possible')",
    )
    .get(job.id);
  if (open !== null)
    throw new CliError(
      "JOBS_SEND_RECONCILIATION_REQUIRED",
      `job ${job.id} has unresolved send attempt ${open.attempt_id}`,
      { exitCode: 2 },
    );
  const attemptId = `jobs-send-v1:${randomUUID()}`;
  database
    .prepare(
      "INSERT INTO jobs_chrome_send_receipts (attempt_id, job_id, recipient_url, route, transport, draft_fingerprint, state, evidence_json, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, ?, 'prepared', '{}', ?, ?)",
    )
    .run(attemptId, job.id, recipient, route, fingerprint, now, now);
  return {
    attemptId,
    createdAt: now,
    jobId: job.id,
    route,
    recipientUrl: recipient,
    recipientName: job.hiringTeam[0]?.name ?? "",
    subject: job.subject,
    message: job.message,
    draftFingerprint: fingerprint,
    transportRequired: "dm|inmail",
    evidenceContract: "transport-specific-v2",
  };
}

export function recordChromeSend(
  database: Database,
  payload: unknown,
  now: string,
  contracts: ChromeSendContracts = {},
) {
  const value = object(payload, "send record");
  exactKeys(
    value,
    [
      "attemptId",
      "jobId",
      "route",
      "transport",
      "recipientUrn",
      "draftFingerprint",
      "state",
      "evidence",
    ],
    "send record",
  );
  const attemptId = text(value.attemptId, "attemptId", 200);
  const jobId = text(value.jobId, "jobId", 200);
  const transport = value.transport;
  if (transport !== "dm" && transport !== "inmail")
    throw new CliError("INVALID_ARGUMENT", "transport must be dm or inmail", { exitCode: 2 });
  const state = value.state;
  if (!["possible", "confirmed", "proven_no_send"].includes(String(state)))
    throw new CliError("INVALID_ARGUMENT", "state must be possible, confirmed, or proven_no_send", {
      exitCode: 2,
    });
  const existing = database
    .query<
      {
        job_id: string;
        state: string;
        transport: string | null;
        route: string;
        draft_fingerprint: string;
        evidence_json: string;
      },
      [string]
    >(
      "SELECT job_id, state, transport, route, draft_fingerprint, evidence_json FROM jobs_chrome_send_receipts WHERE attempt_id = ?",
    )
    .get(attemptId);
  if (existing !== null) {
    if (existing.job_id !== jobId)
      throw new CliError("INVALID_ARGUMENT", "attemptId is bound to another job", { exitCode: 2 });
    if (
      existing.state === state &&
      existing.transport === transport &&
      existing.route === value.route &&
      existing.draft_fingerprint === value.draftFingerprint &&
      existing.evidence_json === JSON.stringify(value.evidence)
    )
      return { attemptId, jobId, state, idempotent: true };
    if (
      !(
        existing.state === "prepared" &&
        ["possible", "confirmed", "proven_no_send"].includes(String(state))
      ) &&
      !(existing.state === "possible" && ["confirmed", "proven_no_send"].includes(String(state)))
    )
      throw new CliError(
        "JOBS_SEND_CONFLICTING_REPLAY",
        "attempt replay conflicts with its stored receipt",
        { exitCode: 2 },
      );
  }
  const { job, route, fingerprint } = currentJob(database, jobId);
  if (
    text(value.route, "route", 40) !== route ||
    text(value.draftFingerprint, "draftFingerprint", 100) !== fingerprint
  )
    throw new CliError(
      "JOBS_SEND_ATTEMPT_MISMATCH",
      "record does not match the current prepared draft",
      { exitCode: 2 },
    );
  const evidence = validateEvidence(
    value,
    transport,
    state === "confirmed",
    state === "proven_no_send",
    contracts,
  );
  const evidenceRecord = object(evidence, "evidence");
  const observedUrn =
    evidenceRecord.request === undefined
      ? undefined
      : object(evidenceRecord.request, "evidence.request").recipientUrn;
  if (
    observedUrn !== undefined &&
    (typeof observedUrn !== "string" || observedUrn !== value.recipientUrn)
  )
    throw new CliError(
      "JOBS_SEND_RECIPIENT_UNBOUND",
      "observed recipient URN does not match the prepared recipient binding",
      { exitCode: 2 },
    );
  if (state === "possible") {
    database
      .prepare(
        "UPDATE jobs_chrome_send_receipts SET transport = ?, state = 'possible', evidence_json = ?, updated_at = ? WHERE attempt_id = ? AND state IN ('prepared', 'possible')",
      )
      .run(transport, JSON.stringify(evidence), now, attemptId);
  } else if (state === "confirmed") {
    database
      .prepare(
        "UPDATE jobs_chrome_send_receipts SET transport = ?, state = 'confirmed', evidence_json = ?, updated_at = ? WHERE attempt_id = ? AND state IN ('prepared', 'possible')",
      )
      .run(transport, JSON.stringify(evidence), now, attemptId);
    new JobsEngine(database).markSent(job.id, now);
  } else {
    database
      .prepare(
        "UPDATE jobs_chrome_send_receipts SET transport = ?, state = 'proven_no_send', evidence_json = ?, updated_at = ? WHERE attempt_id = ? AND state IN ('prepared', 'possible')",
      )
      .run(transport, JSON.stringify(evidence), now, attemptId);
  }
  return { attemptId, jobId, state, idempotent: false };
}
