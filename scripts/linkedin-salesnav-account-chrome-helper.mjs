import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const cliPath = fileURLToPath(new URL("../dist/cli.js", import.meta.url));
const MAX_BODY_BYTES = 2_000_000;
const STUDIO_SAVED_SEARCH_ID = "2006497026";
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
const detailsFromQuery = (query) => {
  const marker = query.lastIndexOf("keywords:"),
    end = query.endsWith(")") ? query.length - 1 : query.length,
    keywords = marker < 0 ? "" : query.slice(marker + "keywords:".length, end).trim(),
    hasControlCharacter = [...keywords].some((character) => {
      const code = character.charCodeAt(0);
      return code < 32 || code === 127;
    });
  if (!query.includes("keywords:") || !keywords || keywords.length > 500 || hasControlCharacter)
    return null;
  if (
    exactSet(filterIds(query, "COMPANY_HEADCOUNT"), ["C", "D"]) &&
    exactSet(filterIds(query, "INDUSTRY"), ["104"]) &&
    exactSet(filterIds(query, "REGION"), ["103644278"])
  )
    return { lane: "staffing", keywords };
  if (
    exactSet(filterIds(query, "COMPANY_HEADCOUNT"), ["C"]) &&
    exactSet(filterIds(query, "INDUSTRY"), ["96", "99"]) &&
    exactSet(filterIds(query, "REGION"), ["103644278"]) &&
    STUDIO_SEARCH_QUERIES.has(keywords)
  )
    return { lane: "studio", keywords };
  return null;
};
const sourceContract = (value, config = {}) => {
  try {
    const u = new URL(value),
      query = decodeQuery(u.searchParams.get("query") ?? "");
    if (
      u.protocol !== "https:" ||
      u.hostname !== "www.linkedin.com" ||
      u.pathname !== "/sales/search/company"
    )
      return null;
    const details = query
      ? detailsFromQuery(query)
      : u.searchParams.get("savedSearchId") === STUDIO_SAVED_SEARCH_ID &&
          config.lane === "studio" &&
          STUDIO_SEARCH_QUERIES.has(config.keywordQuery)
        ? { lane: "studio", keywords: config.keywordQuery }
        : null;
    if (
      !details ||
      (config.lane && details.lane !== config.lane) ||
      (config.keywordQuery && details.keywords !== config.keywordQuery)
    )
      return null;
    return { ...details, query, savedSearchId: u.searchParams.get("savedSearchId") };
  } catch {
    return null;
  }
};
const accountPage = (value) => {
  try {
    const u = new URL(value);
    return (
      u.protocol === "https:" &&
      u.hostname === "www.linkedin.com" &&
      (u.pathname === "/sales/home" || u.pathname === "/sales/search/company")
    );
  } catch {
    return false;
  }
};
const responseContract = (value, start) => {
  if (!responseOk(value, start)) return null;
  try {
    return detailsFromQuery(decodeQuery(new URL(value).searchParams.get("query") ?? ""));
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
const decodeRepeatedly = (value) => {
  let out = value;
  for (let i = 0; i < 3; i++) {
    try {
      const next = decodeURIComponent(out);
      if (next === out) break;
      out = next;
    } catch {
      break;
    }
  }
  return out;
};
export const currentCompanyIds = (value) => {
  const match = decodeRepeatedly(value).match(/type:CURRENT_COMPANY,values:List\((.*?)\)\)\)/);
  return match
    ? [...match[1].matchAll(/\bid:([^,)]+)/g)].map((item) => item[1].trim().split(":").at(-1) ?? "")
    : [];
};
const peopleContract = (value, accountId, response = false, start = 0) => {
  try {
    const u = new URL(value),
      ids = currentCompanyIds(u.searchParams.get("query") ?? "");
    return (
      u.protocol === "https:" &&
      u.hostname === "www.linkedin.com" &&
      u.pathname === (response ? "/sales-api/salesApiLeadSearch" : "/sales/search/people") &&
      (!response ||
        (u.searchParams.get("q") === "searchQuery" &&
          u.searchParams.get("count") === "25" &&
          Number(u.searchParams.get("start")) === start)) &&
      ids.length === 1 &&
      ids[0] === String(accountId)
    );
  } catch {
    return false;
  }
};
const currentCursor = async (cdp) => {
  const options = {
    limit: 1000,
    timeoutMs: 0,
    methods: ["Fetch.requestPaused"],
  };
  let batch = await cdp.readEvents(options),
    cursor = batch.cursor;
  for (let page = 0; batch.hasMore && page < 20; page++) {
    batch = await cdp.readEvents({ ...options, afterSequence: cursor });
    cursor = batch.cursor;
  }
  return cursor;
};
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
    source = sourceContract(sourceUrl, config);
  if (!accountPage(tabUrl) || !source)
    return { ok: false, reason: "source-url-mismatch", status: 0 };
  if (typeof action !== "function") return { ok: false, reason: "action-required", status: 0 };
  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.disable").catch(() => {});
  await cdp.send("Fetch.enable", {
    patterns: [
      {
        urlPattern: "*://www.linkedin.com/sales-api/salesApiAccountSearch*",
        resourceType: "XHR",
        requestStage: "Response",
      },
    ],
  });
  let cursor = await currentCursor(cdp);
  const actionPromise = Promise.resolve().then(action);
  let done = false;
  actionPromise.finally(() => {
    done = true;
  });
  try {
    for (let attempt = 0; attempt < 60; attempt++) {
      const batch = await cdp.readEvents({
        afterSequence: cursor,
        limit: 1000,
        timeoutMs: 1000,
        methods: ["Fetch.requestPaused"],
      });
      cursor = batch.cursor;
      for (const event of batch.events) {
        const id = event.params?.requestId,
          responseUrl = event.params?.request?.url,
          status = event.params?.responseStatusCode ?? 0,
          observed = responseContract(responseUrl, config.start);
        if (!id) continue;
        if (!observed || observed.lane !== source.lane || observed.keywords !== source.keywords) {
          continue;
        }
        if (status < 200 || status >= 300) {
          return { ok: false, reason: "http-status", status };
        }
        const responseBody = await cdp.send(
            "Fetch.getResponseBody",
            { requestId: id },
            { timeoutMs: 10_000 },
          ),
          body = bodyText(responseBody);
        await cdp.send("Fetch.fulfillRequest", {
          requestId: id,
          responseCode: status,
          responseHeaders: (event.params?.responseHeaders ?? []).filter(
            (header) => !["content-encoding", "content-length"].includes(header.name.toLowerCase()),
          ),
          body: responseBody?.base64Encoded
            ? responseBody.body
            : Buffer.from(responseBody?.body ?? "", "utf8").toString("base64"),
        });
        await actionPromise;
        const currentUrl = await tab.url();
        if (!accountPage(currentUrl))
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
            responseUrl,
            ...(config.stateDir ? ["--state-dir", config.stateDir] : []),
          ],
          config.executable,
        );
      }
      // Sales Navigator can finish the visible navigation several seconds before
      // its account-search request is issued.
      if (done && attempt >= 15) break;
    }
    await actionPromise;
    return { ok: false, reason: "matching-response-not-found", status: 0 };
  } finally {
    await cdp.send("Fetch.enable", { patterns: [] }).catch(() => {});
  }
}

export async function captureAndIngestSalesNavAccountPeoplePage(tab, action, config) {
  const tabUrl = await tab.url(),
    sourceUrl = config.sourceUrl ?? tabUrl;
  if (!peopleContract(sourceUrl, config.accountId))
    return { ok: false, reason: "source-url-mismatch", status: 0 };
  if (typeof action !== "function") return { ok: false, reason: "action-required", status: 0 };
  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.disable").catch(() => {});
  await cdp.send("Fetch.enable", {
    patterns: [
      {
        urlPattern: "*://www.linkedin.com/sales-api/salesApiLeadSearch*",
        resourceType: "XHR",
        requestStage: "Response",
      },
    ],
  });
  let cursor = await currentCursor(cdp);
  const actionPromise = Promise.resolve().then(action);
  try {
    for (let attempt = 0; attempt < 60; attempt++) {
      const batch = await cdp.readEvents({
        afterSequence: cursor,
        limit: 1000,
        timeoutMs: 1000,
        methods: ["Fetch.requestPaused"],
      });
      cursor = batch.cursor;
      if (batch.truncated) return { ok: false, reason: "cdp-truncated", status: 0 };
      for (const event of batch.events) {
        const id = event.params?.requestId,
          responseUrl = event.params?.request?.url;
        if (
          !id ||
          !responseUrl ||
          !peopleContract(responseUrl, config.accountId, true, config.start)
        )
          continue;
        const status = event.params?.responseStatusCode ?? 0;
        if (status < 200 || status >= 300) return { ok: false, reason: "http-status", status };
        const raw = await cdp.send("Fetch.getResponseBody", { requestId: id });
        const body = raw?.base64Encoded
          ? Buffer.from(raw.body ?? "", "base64").toString("utf8")
          : (raw?.body ?? "");
        await cdp.send("Fetch.fulfillRequest", {
          requestId: id,
          responseCode: status,
          responseHeaders: (event.params?.responseHeaders ?? []).filter(
            (header) => !["content-encoding", "content-length"].includes(header.name.toLowerCase()),
          ),
          body: raw?.base64Encoded
            ? raw.body
            : Buffer.from(raw?.body ?? "", "utf8").toString("base64"),
        });
        await actionPromise;
        if (!peopleContract(await tab.url(), config.accountId))
          return { ok: false, reason: "source-url-mismatch", status: 0 };
        return pipe(
          body,
          [
            "--json",
            "salesnav",
            config.lane,
            "account-people-capture-ingest",
            "--run-id",
            config.runId,
            "--start",
            String(config.start),
            "--payload",
            "-",
            "--source-url",
            sourceUrl,
            "--response-url",
            responseUrl,
            ...(config.stateDir ? ["--state-dir", config.stateDir] : []),
          ],
          config.executable,
        );
      }
      if (batch.truncated) return { ok: false, reason: "cdp-truncated", status: 0 };
    }
  } finally {
    await cdp.send("Fetch.enable", { patterns: [] }).catch(() => {});
  }
  await actionPromise;
  return { ok: false, reason: "matching-response-not-found", status: 0 };
}
