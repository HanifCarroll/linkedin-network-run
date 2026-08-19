import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const cliPath = fileURLToPath(new URL("../dist/cli.js", import.meta.url));
const endpointPath = "/sales-api/salesApiLeadSearch";
const savedSearchId = "2006360906";
const MAX_BODY_BYTES = 2_000_000;
const blocked = (url, title = "") =>
  /\/(?:login|checkpoint|authwall|challenge)(?:[/?#]|$)/i.test(url) ||
  /(?:sign in|checkpoint|authwall|challenge|security verification)/i.test(title);

const isSupportedSearchUrl = (value) => {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.hostname === "www.linkedin.com" &&
      url.pathname === "/sales/search/people" &&
      url.searchParams.get("savedSearchId") === savedSearchId
    );
  } catch {
    return false;
  }
};

export const decodeResponseBody = (body) =>
  body?.base64Encoded
    ? Buffer.from(body.body ?? "", "base64").toString("utf8")
    : (body?.body ?? "");

export async function pipeRawBodyToCli(body, args, executable = cliPath) {
  if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES)
    return { ok: false, reason: "body-too-large", status: 0 };
  const child = spawn(executable, args, { stdio: ["pipe", "pipe", "pipe"] });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => (stdout += chunk));
  child.stderr.on("data", (chunk) => (stderr += chunk));
  child.stdin.end(body);
  const status = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", resolve);
  });
  let envelope;
  try {
    envelope = JSON.parse(stdout);
  } catch {
    throw new Error(stderr || `capture CLI exited ${status}`);
  }
  if (status !== 0) throw Object.assign(new Error(envelope.error?.message), { envelope });
  return envelope;
}

function isMatchingResponseUrl(value, expectedStart) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.hostname === "www.linkedin.com" &&
      url.pathname === endpointPath &&
      url.searchParams.get("q") === "savedSearchId" &&
      url.searchParams.get("savedSearchId") === savedSearchId &&
      /^\d+$/.test(url.searchParams.get("start") ?? "") &&
      Number(url.searchParams.get("start")) === expectedStart &&
      Number(url.searchParams.get("start")) <= 1_000_000
    );
  } catch {
    return false;
  }
}

export async function captureAndIngestSalesNavPage(tab, action, config) {
  const tabUrl = (await tab.url()) ?? "";
  const sourceUrl = config.sourceUrl ?? tabUrl;
  if (!isSupportedSearchUrl(tabUrl) || !isSupportedSearchUrl(sourceUrl))
    return { ok: false, reason: "source-url-mismatch", status: 0 };
  if (blocked(tabUrl, await tab.title()))
    return { ok: false, reason: "login-or-checkpoint", status: 0 };
  if (typeof action !== "function") return { ok: false, reason: "action-required", status: 0 };

  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.enable");
  let cursor = (await cdp.readEvents({ limit: 1, timeoutMs: 0 })).cursor;
  const actionPromise = Promise.resolve().then(action);
  const responses = new Map();
  let actionDone = false;
  actionPromise.finally(() => (actionDone = true));

  for (let attempt = 0; attempt < 60; attempt += 1) {
    const batch = await cdp.readEvents({
      afterSequence: cursor,
      limit: 1000,
      timeoutMs: 1000,
      methods: ["Network.responseReceived", "Network.loadingFinished", "Network.loadingFailed"],
    });
    cursor = batch.cursor;
    if (batch.truncated) return { ok: false, reason: "cdp-truncated", status: 0 };
    for (const event of batch.events) {
      const id = event.params?.requestId;
      if (!id) continue;
      if (event.method === "Network.responseReceived") {
        const response = event.params.response;
        if (isMatchingResponseUrl(response?.url, config.start)) responses.set(id, response);
      } else if (event.method === "Network.loadingFailed" && responses.has(id)) {
        return { ok: false, reason: "response-load-failed", status: responses.get(id).status };
      }
    }
    for (const [requestId, response] of responses) {
      const finished = batch.events.some(
        (event) =>
          event.method === "Network.loadingFinished" && event.params?.requestId === requestId,
      );
      if (!finished) continue;
      if (response.status === 429) return { ok: false, reason: "http-429", status: 429 };
      if (response.status < 200 || response.status >= 300)
        return { ok: false, reason: "http-status", status: response.status };
      let body;
      try {
        body = decodeResponseBody(await cdp.send("Network.getResponseBody", { requestId }));
      } catch {
        return { ok: false, reason: "body-unavailable", status: response.status };
      }
      if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES)
        return { ok: false, reason: "body-too-large", status: response.status };
      return pipeRawBodyToCli(
        body,
        [
          "--json",
          "salesnav",
          "staffing",
          "capture-ingest",
          "--run-id",
          config.runId,
          "--start",
          String(config.start),
          "--payload",
          "-",
          "--source-url",
          sourceUrl,
          "--response-url",
          response.url,
          ...(config.stateDir ? ["--state-dir", config.stateDir] : []),
        ],
        config.executable,
      );
    }
    if (actionDone && attempt >= 5) break;
  }
  await actionPromise;
  return { ok: false, reason: "matching-response-not-found", status: 0 };
}
