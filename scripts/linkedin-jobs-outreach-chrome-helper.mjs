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
  if (typeof config.endpointUrl !== "string" || config.endpointUrl.length < 1)
    return { ok: false, reason: "exact-endpoint-contract-required" };
  if (typeof config.action !== "function" || typeof config.threadCheck !== "function")
    return { ok: false, reason: "visible-action-and-thread-check-required" };
  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.enable");
  const armed = await cdp.readEvents({ limit: 1, timeoutMs: 0 });
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
      (config.route === "direct" || thread?.threadVisible === true),
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
        ...(config.route === "direct" ? {} : { threadVisible: thread?.threadVisible === true }),
        ...(config.route === "direct" ? {} : { subjectSha256: sha(config.subject ?? "") }),
      },
    },
  };
}

export async function sendAndRecordJobMessage(tab, config) {
  const result = await sendJobMessage(tab, config);
  if (!result.ok) return result;
  const packet = config.packet;
  return pipeRawBodyToCli(
    JSON.stringify({
      attemptId: packet.attemptId,
      jobId: packet.jobId,
      route: packet.route,
      transport: config.transport,
      ...(config.recipientUrn ? { recipientUrn: config.recipientUrn } : {}),
      draftFingerprint: packet.draftFingerprint,
      state: result.state,
      evidence: result.evidence,
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
}
