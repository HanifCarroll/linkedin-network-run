import { createHash } from "node:crypto";
import { pipeRawBodyToCli } from "./linkedin-jobs-chrome-helper.mjs";

const sha = (value) => createHash("sha256").update(String(value)).digest("hex");
const bounded = (value, max = 500) =>
  String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);

/**
 * Caller-owned Jobs outreach handoff. The caller supplies the already-observed
 * exact endpoint URL and visible UI action. This helper never creates or
 * closes tabs, reads cookies/headers, or replays a private write request.
 */
export async function sendJobMessage(tab, config) {
  if (!config || !["direct", "application_followup"].includes(config.route))
    return { ok: false, reason: "route-required" };
  if (!config || !["dm", "inmail"].includes(config.transport))
    return { ok: false, reason: "transport-required" };
  if (typeof config.endpointUrl !== "string" || config.endpointUrl.length < 1)
    return { ok: false, reason: "exact-endpoint-contract-required" };
  if (
    typeof config.beforeAction !== "function" ||
    typeof config.action !== "function" ||
    typeof config.threadCheck !== "function"
  )
    return { ok: false, reason: "reservation-action-and-thread-check-required" };
  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.enable");
  const armed = await cdp.readEvents({ limit: 1, timeoutMs: 0 });
  await config.beforeAction();
  await config.action();
  let cursor = armed.cursor;
  let request = null;
  let response = null;
  for (let i = 0; i < 30; i += 1) {
    const batch = await cdp.readEvents({
      afterSequence: cursor,
      limit: 200,
      timeoutMs: 1000,
      methods: [
        "Network.requestWillBeSent",
        "Network.responseReceived",
        "Network.loadingFinished",
        "Network.loadingFailed",
      ],
    });
    cursor = batch.cursor;
    if (batch.truncated) return { ok: false, reason: "cdp-truncated" };
    for (const event of batch.events) {
      const params = event.params ?? {};
      if (
        event.method === "Network.requestWillBeSent" &&
        params.request?.method === "POST" &&
        String(params.request.url) === config.endpointUrl
      ) {
        const body = typeof params.request.postData === "string" ? params.request.postData : "";
        const urn = body.match(/urn:li:[^"'&\\]+/)?.[0] ?? "";
        request = {
          requestId: String(params.requestId),
          method: "POST",
          url: String(params.request.url),
          bodySha256: body ? sha(body) : "",
          recipientUrn: urn,
        };
      }
      if (
        event.method === "Network.responseReceived" &&
        request &&
        params.requestId === request.requestId
      )
        response = { status: Number(params.response?.status ?? 0) };
    }
    if (request && response) break;
  }
  const thread = await config.threadCheck();
  const confirmed = Boolean(
    request &&
      response &&
      response.status >= 200 &&
      response.status <= 299 &&
      thread?.composerGone === true &&
      thread?.messageVisible === true &&
      bounded(thread?.profileUrl, 1000) === bounded(config.packet.recipientUrl, 1000) &&
      (config.transport === "dm" || thread?.threadVisible === true),
  );
  return {
    ok: true,
    state: confirmed ? "confirmed" : "possible",
    evidence: {
      request: request
        ? {
            method: request.method,
            url: bounded(request.url, 1000),
            status: response?.status ?? 0,
            bodySha256: request.bodySha256,
            ...(request.recipientUrn ? { recipientUrn: request.recipientUrn } : {}),
          }
        : { method: "POST", url: "", status: 0, bodySha256: "" },
      thread: {
        composerGone: thread?.composerGone === true,
        messageVisible: thread?.messageVisible === true,
        profileUrl: bounded(thread?.profileUrl, 1000),
        messageSha256: sha(config.packet.message ?? ""),
        ...(config.transport === "dm"
          ? {}
          : {
              threadVisible: thread?.threadVisible === true,
              subjectSha256: sha(config.packet.subject ?? ""),
            }),
      },
    },
  };
}

export async function sendAndRecordJobMessage(tab, config) {
  const packet = config.packet;
  const record = (state, evidence, recipientUrn = config.recipientUrn) =>
    pipeRawBodyToCli(
      JSON.stringify({
        attemptId: packet.attemptId,
        jobId: packet.jobId,
        route: packet.route,
        transport: config.transport,
        ...(recipientUrn ? { recipientUrn } : {}),
        draftFingerprint: packet.draftFingerprint,
        state,
        evidence,
      }),
      [
        "--json",
        "jobs",
        "send-record",
        "--payload",
        "-",
        ...(config.stateDir ? ["--state-dir", config.stateDir] : []),
      ],
      config.executable,
    );
  const result = await sendJobMessage(tab, {
    ...config,
    beforeAction: () => record("possible", { commitStarted: true }),
  });
  if (!result.ok) return result;
  if (result.state === "possible") return result;
  const observedUrn = result.evidence.request.recipientUrn;
  return record("confirmed", result.evidence, observedUrn);
}

/** Caller-owned connection-request handoff. It observes a request caused by visible UI only. */
export async function sendContractOutreachInvitation(tab, config) {
  if (!config || config.route !== "application_followup") return { ok: false, reason: "contract-route-required" };
  if (typeof config.endpointUrl !== "string" || config.endpointUrl.length < 1 || typeof config.beforeAction !== "function" || typeof config.action !== "function" || typeof config.invitationCheck !== "function") return { ok: false, reason: "exact-endpoint-reservation-action-and-invitation-check-required" };
  const cdp = await tab.capabilities.get("cdp"); await cdp.send("Network.enable"); const armed = await cdp.readEvents({ limit: 1, timeoutMs: 0 }); await config.beforeAction(); await config.action();
  let cursor = armed.cursor; let request = null; let response = null;
  for (let i = 0; i < 30; i += 1) { const batch = await cdp.readEvents({ afterSequence: cursor, limit: 200, timeoutMs: 1000, methods: ["Network.requestWillBeSent", "Network.responseReceived", "Network.loadingFailed"] }); cursor = batch.cursor; if (batch.truncated) return { ok: false, reason: "cdp-truncated" }; for (const event of batch.events) { const p = event.params ?? {}; if (event.method === "Network.requestWillBeSent" && p.request?.method === "POST" && String(p.request.url) === config.endpointUrl) { const body = typeof p.request.postData === "string" ? p.request.postData : ""; request = { requestId: String(p.requestId), method: "POST", url: String(p.request.url), bodySha256: sha(body), recipientUrn: body.match(/urn:li:[^"'&\\]+/)?.[0] ?? "" }; } if (event.method === "Network.responseReceived" && request && String(p.requestId) === request.requestId) response = { status: Number(p.response?.status ?? 0) }; } if (request && response) break; }
  const invitation = await config.invitationCheck(); const confirmed = Boolean(request && response && response.status >= 200 && response.status <= 299 && invitation?.pending === true && bounded(invitation?.profileUrl, 1000) === bounded(config.packet.recipientUrl, 1000));
  return { ok: true, state: confirmed ? "confirmed" : "possible", evidence: { request: request ? { method: request.method, url: bounded(request.url, 1000), status: response?.status ?? 0, bodySha256: request.bodySha256, ...(request.recipientUrn ? { recipientUrn: request.recipientUrn } : {}) } : { method: "POST", url: "", status: 0, bodySha256: "" }, invitation: { pending: invitation?.pending === true, profileUrl: bounded(invitation?.profileUrl, 1000) } } };
}

export async function sendAndRecordContractOutreachInvitation(tab, config) {
  const record = (state, evidence) => pipeRawBodyToCli(JSON.stringify({ attemptId: config.packet.attemptId, jobId: config.packet.jobId, route: config.packet.route, draftFingerprint: config.packet.draftFingerprint, state, evidence }), ["--json", "jobs", "contract-outreach-record", "--payload", "-", ...(config.stateDir ? ["--state-dir", config.stateDir] : [])], config.executable);
  const result = await sendContractOutreachInvitation(tab, { ...config, beforeAction: () => record("possible", { commitStarted: true }) }); if (!result.ok || result.state === "possible") return result; return record("confirmed", result.evidence);
}
