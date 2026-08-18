import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const blocked = (url, title = "") =>
  /\/(?:login|checkpoint|authwall|challenge)(?:[/?#]|$)/i.test(url) ||
  /(?:sign in|checkpoint|authwall|challenge|security verification)/i.test(title);

export function decodeResponseBody(body) {
  return body?.base64Encoded
    ? Buffer.from(body.body ?? "", "base64").toString("utf8")
    : (body?.body ?? "");
}

/**
 * Codex Chrome handoff: arm CDP, let the caller perform a visible DOM action,
 * and return the matching Jobs XHR body for linkedin-tools capture-ingest.
 * This helper never creates/closes tabs and never returns cookies or headers.
 */
const cliPath = fileURLToPath(new URL("../dist/cli.js", import.meta.url));

export async function pipeRawBodyToCli(rawBody, cliArgs, executable = cliPath) {
  const child = spawn(executable, cliArgs, { stdio: ["pipe", "pipe", "pipe"] });
  const result = await new Promise((resolve, reject) => {
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => {
      out += chunk;
    });
    child.stderr.on("data", (chunk) => {
      err += chunk;
    });
    child.once("error", reject);
    child.once("close", (status) => resolve({ out, err, status }));
    child.stdin.end(rawBody);
  });
  const { out, err, status } = result;
  let envelope;
  try {
    envelope = JSON.parse(out);
  } catch {
    throw new Error(err || `capture CLI exited ${status}`);
  }
  if (status !== 0)
    throw Object.assign(new Error(envelope.error?.message || `capture CLI exited ${status}`), {
      envelope,
    });
  return envelope;
}

export async function captureAndIngestJobsPage(tab, action, config) {
  const capture = await captureJobsResponse(
    tab,
    action,
    config.urlIncludes ?? "voyagerJobsDashJobCards",
  );
  if (!capture.ok) return capture;
  return pipeRawBodyToCli(
    capture.rawBody,
    [
      "--json",
      "jobs",
      "capture-ingest",
      "--run-id",
      config.runId,
      "--page",
      config.pageIdentity,
      "--payload",
      "-",
      "--source-url",
      capture.sourceUrl,
      "--response-url",
      capture.responseUrl,
      ...(config.cursor === undefined ? [] : ["--cursor", config.cursor]),
      ...(config.stateDir === undefined ? [] : ["--state-dir", config.stateDir]),
    ],
    config.executable,
  );
}

export async function captureJobsResponse(tab, action, urlIncludes = "voyagerJobsDashJobCards") {
  const sourceUrl = (await tab.url()) ?? "";
  if (blocked(sourceUrl, await tab.title())) {
    return { ok: false, sourceUrl, responseUrl: "", status: 0, reason: "login-or-checkpoint" };
  }
  if (typeof action !== "function") {
    return { ok: false, sourceUrl, responseUrl: "", status: 0, reason: "action-required" };
  }
  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.enable");
  const armed = await cdp.readEvents({ limit: 1, timeoutMs: 0 });
  await action();
  let afterSequence = armed.cursor;
  let hit = null;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const batch = await cdp.readEvents({
      afterSequence,
      limit: 100,
      timeoutMs: 1000,
      methods: ["Network.responseReceived", "Network.loadingFinished", "Network.loadingFailed"],
    });
    afterSequence = batch.cursor;
    if (batch.truncated)
      return { ok: false, sourceUrl, responseUrl: "", status: 0, reason: "cdp-truncated" };
    hit ??=
      batch.events.find((event) => {
        const response = event.params?.response;
        return response && String(response.url).includes(urlIncludes);
      }) ?? null;
    if (!hit) continue;
    const response = hit.params.response;
    const requestId = hit.params.requestId;
    if (response.status === 429)
      return { ok: false, sourceUrl, responseUrl: response.url, status: 429, reason: "http-429" };
    if (
      batch.events.some(
        (event) =>
          event.method === "Network.loadingFailed" && event.params?.requestId === requestId,
      )
    ) {
      return {
        ok: false,
        sourceUrl,
        responseUrl: response.url,
        status: response.status,
        reason: "response-load-failed",
      };
    }
    if (
      !batch.events.some(
        (event) =>
          event.method === "Network.loadingFinished" && event.params?.requestId === requestId,
      )
    )
      continue;
    let body;
    try {
      body = await cdp.send("Network.getResponseBody", { requestId });
    } catch {
      return {
        ok: false,
        sourceUrl,
        responseUrl: response.url,
        status: response.status,
        reason: "body-unavailable",
      };
    }
    return {
      ok: true,
      sourceUrl,
      responseUrl: response.url,
      status: response.status,
      rawBody: decodeResponseBody(body),
    };
  }
  return {
    ok: false,
    sourceUrl,
    responseUrl: "",
    status: 0,
    reason: "matching-response-not-found",
  };
}
