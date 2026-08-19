import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const cliPath = fileURLToPath(new URL("../dist/cli.js", import.meta.url));
const MAX_BODY_BYTES = 2_000_000;
const STUDIO_SEARCH_QUERIES = new Set([
  '("product development" OR "software development" OR "web development" OR "application development") NOT (staffing OR recruiting OR "digital marketing")',
  '("product studio" OR "digital product" OR "custom software" OR "web application") NOT (staffing OR recruiting OR "digital marketing")',
]);
const decodeQuery = (value) => {
  let decoded = value;
  for (let i = 0; i < 3; i++) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    } catch {
      break;
    }
  }
  return decoded;
};
const filterIds = (query, type) => {
  const matches = [...query.matchAll(/\btype:([A-Z_]+)\b/g)];
  const index = matches.findIndex((match) => match[1] === type);
  if (index < 0) return [];
  const start = (matches[index]?.index ?? 0) + (matches[index]?.[0].length ?? 0);
  const end = matches[index + 1]?.index ?? query.length;
  return [...query.slice(start, end).matchAll(/\bid:([A-Za-z0-9_-]+)\b/g)].map(
    (match) => match[1] ?? "",
  );
};
const exactSet = (actual, expected) =>
  actual.length === expected.length && expected.every((value) => actual.includes(value));
const sourceContract = (value) => {
  try {
    const u = new URL(value),
      query = decodeQuery(u.searchParams.get("query") ?? ""),
      marker = query.lastIndexOf("keywords:"),
      end = query.endsWith(")") ? query.length - 1 : query.length,
      keywords = marker < 0 ? "" : query.slice(marker + "keywords:".length, end).trim(),
      hasControlCharacter = [...keywords].some((character) => {
        const code = character.charCodeAt(0);
        return code < 32 || code === 127;
      });
    if (
      !(
        u.protocol === "https:" &&
        u.hostname === "www.linkedin.com" &&
        u.pathname === "/sales/search/company" &&
        query.includes("keywords:") &&
        keywords.length > 0 &&
        keywords.length <= 500 &&
        !hasControlCharacter
      )
    ) return null;
    if (
      exactSet(filterIds(query, "COMPANY_HEADCOUNT"), ["C", "D"]) &&
      exactSet(filterIds(query, "INDUSTRY"), ["104"]) &&
      exactSet(filterIds(query, "REGION"), ["103644278"])
    ) return { lane: "staffing", query };
    if (
      exactSet(filterIds(query, "COMPANY_HEADCOUNT"), ["C"]) &&
      exactSet(filterIds(query, "INDUSTRY"), ["96", "7"]) &&
      exactSet(filterIds(query, "REGION"), ["103644278"]) &&
      STUDIO_SEARCH_QUERIES.has(keywords)
    ) return { lane: "studio", query };
    return null;
  } catch {
    return null;
  }
};
const responseOk = (value, start) => {
  try {
    const u = new URL(value);
    return (
      u.protocol === "https:" &&
      u.hostname === "www.linkedin.com" &&
      u.pathname === "/sales-api/salesApiAccountSearch" &&
      u.searchParams.get("q") === "searchQuery" &&
      u.searchParams.get("count") === "25" &&
      u.searchParams.get("decorationId") ===
        "com.linkedin.sales.deco.desktop.searchv2.AccountSearchResult-4" &&
      /^\d+$/.test(u.searchParams.get("start") ?? "") &&
      Number(u.searchParams.get("start")) === start
    );
  } catch {
    return false;
  }
};
const bodyText = (v) =>
  v?.base64Encoded ? Buffer.from(v.body ?? "", "base64").toString("utf8") : (v?.body ?? "");
const pipe = (body, args, executable = cliPath) =>
  new Promise((resolve, reject) => {
    if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES)
      return resolve({ ok: false, reason: "body-too-large", status: 0 });
    const child = spawn(executable, args, { stdio: ["pipe", "pipe", "pipe"] });
    let out = "",
      err = "";
    child.stdout.on("data", (x) => {
      out += x;
    });
    child.stderr.on("data", (x) => {
      err += x;
    });
    child.stdin.end(body);
    child.once("error", reject);
    child.once("close", (status) => {
      try {
        const envelope = JSON.parse(out);
        if (status !== 0)
          return reject(Object.assign(new Error(envelope.error?.message ?? err), { envelope }));
        resolve(envelope);
      } catch {
        reject(new Error(err || `capture CLI exited ${status}`));
      }
    });
  });
export async function captureAndIngestSalesNavAccountPage(tab, action, config) {
  const tabUrl = await tab.url(),
    sourceUrl = config.sourceUrl ?? tabUrl,
    tabContract = sourceContract(tabUrl),
    source = sourceContract(sourceUrl);
  if (!tabContract || !source || tabContract.lane !== source.lane || tabContract.query !== source.query)
    return { ok: false, reason: "source-url-mismatch", status: 0 };
  if (typeof action !== "function") return { ok: false, reason: "action-required", status: 0 };
  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.enable");
  let cursor = (await cdp.readEvents({ limit: 1, timeoutMs: 0 })).cursor;
  const actionPromise = Promise.resolve().then(action);
  const responses = new Map();
  let done = false;
  actionPromise.finally(() => {
    done = true;
  });
  for (let attempt = 0; attempt < 60; attempt++) {
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
      if (
        event.method === "Network.responseReceived" &&
        responseOk(event.params.response?.url, config.start)
      )
        responses.set(id, event.params.response);
      if (event.method === "Network.loadingFailed" && responses.has(id))
        return { ok: false, reason: "response-load-failed", status: responses.get(id).status };
    }
    for (const [id, response] of responses) {
      if (
        !batch.events.some(
          (e) => e.method === "Network.loadingFinished" && e.params?.requestId === id,
        )
      )
        continue;
      if (response.status < 200 || response.status >= 300)
        return { ok: false, reason: "http-status", status: response.status };
      const body = bodyText(
        await cdp.send("Network.getResponseBody", { requestId: id }, { timeoutMs: 10_000 }),
      );
      await actionPromise;
      const current = sourceContract(await tab.url());
      if (!current || current.lane !== source.lane || current.query !== source.query)
        return { ok: false, reason: "source-url-mismatch", status: 0 };
      return pipe(
        body,
        [
          "--json",
          "salesnav",
          source.lane,
          "account-capture-ingest",
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
    if (done && attempt >= 5) break;
  }
  await actionPromise;
  return { ok: false, reason: "matching-response-not-found", status: 0 };
}
