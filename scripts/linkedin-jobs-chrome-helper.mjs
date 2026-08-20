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

const ENRICHMENT_PARSER_VERSION = "jobs-chrome-enrichment-v2";
const bounded = (value, max = 2000) =>
  String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
const canonicalUrl = (value) => {
  try {
    const url = new URL(value);
    url.hostname = url.hostname.toLowerCase().replace(/^www\./, "");
    url.search = "";
    url.hash = "";
    url.pathname = url.pathname.replace(/\/+$/, "/");
    return url.toString();
  } catch {
    return bounded(value, 1000);
  }
};

async function readStableSnapshot(tab, attempts = 3) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await tab.playwright.evaluate(snapshotFromPage, undefined, { timeoutMs: 10_000 });
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
  }
  throw lastError;
}

function parseMemberText(innerText, profileUrl) {
  const lines = String(innerText ?? "")
    .split(/\r?\n/)
    .map((line) => bounded(line, 300))
    .filter(Boolean);
  const name = lines[0] ?? "";
  const degreeLine = lines.find((line) => /^(?:•\s*)?[123](?:st|nd|rd)$/i.test(line)) ?? "";
  const degree = degreeLine.replace(/^•\s*/, "");
  const headline = lines
    .filter(
      (line, index) =>
        index > 0 &&
        line !== degreeLine &&
        !/^(job poster|message|follow|connect|show more|view profile)$/i.test(line),
    )
    .join(" ");
  return { name, profileUrl: canonicalUrl(profileUrl), degree, headline: bounded(headline, 300) };
}

/** Pure parser for bounded job-page snapshots; safe to fixture without a browser. */
export function parseJobSnapshot(snapshot, expected = {}) {
  const lines = String(snapshot.text ?? "")
    .split(/\r?\n/)
    .map((line) => bounded(line, 2000))
    .filter(Boolean);
  const documentTitle = bounded(snapshot.documentTitle, 500).replace(/\s*\|\s*LinkedIn\s*$/i, "");
  const titleParts = documentTitle.split(/\s*\|\s*/).filter(Boolean);
  const company = titleParts.length > 1 ? titleParts.pop() : "";
  const title = titleParts.join(" | ");
  const metaIndex = lines.findIndex((line) =>
    /·\s*(?:reposted\s+)?(?:just now|\d+\s+(?:minute|hour|day|week|month)s?\s+ago|today|yesterday)/i.test(
      line,
    ),
  );
  const meta = metaIndex >= 0 ? lines[metaIndex] : "";
  const metaParts = meta
    .split("·")
    .map((part) => part.trim())
    .filter(Boolean);
  const location = metaParts[0] ?? "";
  const postedAt =
    metaParts.find((part) => /(?:reposted\s+)?(?:ago|today|yesterday|just now)/i.test(part)) ?? "";
  const applicantCount =
    metaParts.find((part) => /applicant|people clicked apply|person clicked apply/i.test(part)) ??
    "";
  const exact = (value) => lines.find((line) => line.toLowerCase() === value.toLowerCase()) ?? "";
  const workplaceType = exact("On-site") || exact("Remote") || exact("Hybrid");
  const employmentType =
    exact("Full-time") ||
    exact("Part-time") ||
    exact("Contract") ||
    exact("Temporary") ||
    exact("Internship") ||
    exact("Other");
  const applyMethod = exact("Easy Apply") || exact("Apply");
  const promo =
    lines.find((line) => /promoted by hirer|actively reviewing applicants/i.test(line)) ?? "";
  const range = (start, ends) => {
    const from = lines.findIndex((line) => line.toLowerCase() === start.toLowerCase());
    if (from < 0) return [];
    const to = lines.findIndex(
      (line, index) => index > from && ends.some((end) => line.toLowerCase() === end.toLowerCase()),
    );
    return lines
      .slice(from + 1, to < 0 ? Math.min(lines.length, from + 200) : to)
      .filter((line) => !/^(… more|show more|see more)$/i.test(line));
  };
  const description = range("About the job", [
    "Benefits found in job post",
    "Set alert for similar jobs",
    "Similar jobs",
  ])
    .join("\n")
    .trim()
    .slice(0, 50_000);
  const benefitLines = range("Benefits found in job post", [
    "Set alert for similar jobs",
    "Similar jobs",
  ]);
  const members = (snapshot.team ?? [])
    .map((member) => parseMemberText(member.innerText, member.profileUrl))
    .filter((member) => member.name && /linkedin\.com\/in\//i.test(member.profileUrl));
  const companyLines = range("About the company", [
    "Set alert for similar jobs",
    "Similar jobs",
  ]).slice(0, 20);
  const sourceEvidence = [
    title,
    company,
    meta,
    promo,
    workplaceType,
    employmentType,
    applyMethod,
    "About the job",
    description.slice(0, 1000),
    ...members.map((member) => `${member.name} | ${member.degree} | ${member.headline}`),
  ]
    .filter(Boolean)
    .slice(0, 50);
  const closed = /no longer accepting applications|job is no longer available|job not found/i.test(
    lines.join(" "),
  );
  const jobId =
    String(snapshot.url ?? "").match(/jobs\/view\/(\d+)/)?.[1] ?? String(expected.id ?? "");
  const postingUrl = canonicalUrl(snapshot.url ?? expected.sourceUrl ?? "");
  const complete = Boolean(description) && Boolean(title) && Boolean(company) && Boolean(location);
  return {
    id: jobId,
    sourceUrl: postingUrl,
    title,
    company,
    location,
    postingUrl,
    description,
    workplaceType,
    employmentType,
    applyMethod,
    promoted: /promoted/i.test(promo),
    activelyReviewing: /actively reviewing/i.test(promo),
    postedAt,
    applicantCount,
    benefits: benefitLines.slice(0, 50).map((line) => line.slice(0, 300)),
    hiringTeam: members,
    companyProfileUrl: canonicalUrl(snapshot.companyProfileUrl ?? ""),
    companyEvidence: companyLines.map((line) => line.slice(0, 1000)),
    sourceEvidence,
    outcome: closed
      ? "closed"
      : complete
        ? members.length
          ? "complete_hiring_team"
          : "complete_no_hiring_team"
        : "retry_required",
    parserVersion: ENRICHMENT_PARSER_VERSION,
    stable: `${title}|${company}|${meta}|${description}|${members
      .map((member) => member.profileUrl)
      .sort()
      .join(",")}`,
  };
}

const snapshotFromPage = () => {
  const clean = (value) =>
    String(value ?? "")
      .replace(/\s+/g, " ")
      .trim();
  const main =
    document.querySelector("main, [role='main']") ??
    [...document.querySelectorAll("h1,h2,h3,h4")]
      .find((node) => clean(node.textContent) === "About the job")
      ?.closest("article, section") ??
    null;
  if (!main) return { text: "", documentTitle: document.title, url: location.href, team: [] };
  const exactHeading = [...main.querySelectorAll("h1,h2,h3,h4,div,span,section")].find(
    (node) => clean(node.textContent) === "Meet the hiring team",
  );
  let teamRegion = exactHeading?.parentElement ?? null;
  for (let up = 0; up < 5 && teamRegion; up += 1) {
    const regionText = clean(teamRegion.textContent);
    if (
      /People also viewed|People you may know|Similar jobs|About the job|About the company|Set alert|Explore more/i.test(
        regionText,
      )
    )
      break;
    if (teamRegion.querySelector("a[href*='/in/']")) break;
    teamRegion = teamRegion.parentElement;
  }
  const teamRoot = teamRegion ?? main;
  let teamLinks = [...main.querySelectorAll("a[href*='/in/']")].filter((link) =>
    /job poster/i.test(link.textContent ?? ""),
  );
  if (!teamLinks.length && exactHeading && teamRegion)
    teamLinks = [...teamRoot.querySelectorAll("a[href*='/in/']")];
  const companyLink = main.querySelector("a[href*='/company/']");
  return {
    text: (main.innerText ?? "").slice(0, 80_000),
    documentTitle: document.title,
    url: location.href,
    companyProfileUrl: companyLink?.href ?? "",
    team: teamLinks
      .slice(0, 50)
      .map((link) => ({ profileUrl: link.href, innerText: (link.innerText ?? "").slice(0, 1000) })),
  };
};

function unescapeRsc(value) {
  return String(value ?? "")
    .replace(/\\u([0-9a-f]{4})/gi, (_, hex) => String.fromCharCode(Number.parseInt(hex, 16)))
    .replace(/\\n/g, "\n")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, "\\");
}

function rscValue(text, keys) {
  for (const key of keys) {
    const match = String(text).match(
      new RegExp(
        `(?:[\\"']${key}[\\"']|${key})\\s*[:=]\\s*[\\"']((?:\\\\.|[^\\\\\\"'])*)[\\"']`,
        "i",
      ),
    );
    if (match?.[1]) {
      try {
        return bounded(JSON.parse(`"${match[1]}"`), 50_000);
      } catch {
        return bounded(unescapeRsc(match[1]), 50_000);
      }
    }
  }
  return "";
}

function flightLines(body) {
  return String(body ?? "")
    .split(/\r?\n/)
    .flatMap((line) => {
      const separator = line.indexOf(":");
      if (separator < 1) return [];
      const token = line.slice(separator + 1).trim();
      try {
        return [JSON.parse(token)];
      } catch {
        return [];
      }
    });
}
function parseFlightRecords(body) {
  const records = flightLines(body);
  if (records.length) return records;
  try {
    return [JSON.parse(String(body ?? ""))];
  } catch {
    return [];
  }
}

function renderFlightText(value, joiner = "\n") {
  if (typeof value === "string") return /^\$/.test(value) ? "" : unescapeRsc(value);
  if (Array.isArray(value)) {
    if (value[0] === "$" && value.length >= 4) {
      const tag = typeof value[1] === "string" ? value[1] : "";
      if (tag === "br") return "\n";
      const props = value[3] && typeof value[3] === "object" ? value[3] : {};
      const content = props.textProps
        ? renderFlightText(props.textProps.children ?? "", "\n")
        : renderFlightText(props.children ?? "", /^(?:p|li|h[1-6])$/i.test(tag) ? "" : "\n");
      return /^(?:p|li|h[1-6])$/i.test(tag) ? `${content}\n` : content;
    }
    return value.map((item) => renderFlightText(item, joiner)).join(joiner);
  }
  if (value && typeof value === "object") {
    if ("textProps" in value) return renderFlightText(value.textProps);
    if ("props" in value) return renderFlightText(value.props);
    if ("children" in value) return renderFlightText(value.children);
    return [
      "text",
      "label",
      "title",
      "name",
      "profileUrl",
      "url",
      "degree",
      "headline",
      "description",
    ]
      .filter((key) => key in value)
      .map((key) => `${renderFlightText(value[key])}\n`)
      .join("");
  }
  return "";
}

const structuralToken = (value) =>
  !value ||
  value.length > 10_000 ||
  /^\$/.test(value) ||
  /^(?:[a-z-]+:|css-|className|aria-|button|div|span|section|undefined|null|true|false|show more|view profile|message|connect|follow)$/i.test(
    value,
  ) ||
  (/^[-_a-z]+$/.test(value) && value.includes("-"));
function renderedRecordLines(record) {
  const rawLines = renderFlightText(record)
    .split(/\r?\n/)
    .filter((value) => value.trim());
  const lines = [];
  for (const [index, raw] of rawLines.entries()) {
    const value = raw.trim();
    const previous = lines.at(-1);
    if (!previous) {
      lines.push(value);
      continue;
    }
    const joinedBySourceWhitespace = /^\s/.test(raw) || /\s$/.test(rawLines[index - 1] ?? "");
    if (joinedBySourceWhitespace) lines[lines.length - 1] = `${previous} ${value}`;
    else if (
      !/[.!?:;)\]]$/.test(previous) &&
      /^[a-z]/.test(value) &&
      !/^https?:/i.test(value) &&
      !/^(?:about the job|about the company|meet the hiring team|people you can reach out to)$/i.test(
        previous,
      )
    )
      lines[lines.length - 1] = `${previous}${value}`;
    else lines.push(value);
  }
  return lines.map((value) => value.replace(/([.!?])([A-Z])/g, "$1 $2"));
}
function visibleFlightText(records) {
  return [
    ...new Set(records.flatMap(renderedRecordLines).filter((value) => !structuralToken(value))),
  ];
}
function scopedDescription(strings) {
  const start = strings.findIndex((value) => /^about the job$/i.test(value));
  if (start < 0) return "";
  const stop = strings.findIndex(
    (value, index) =>
      index > start &&
      /^(about the company|meet the hiring team|similar jobs|set alert)/i.test(value),
  );
  const content = strings.slice(start + 1, stop < 0 ? start + 250 : stop);
  if (/^benefits found in job post$/i.test(content[0] ?? "")) {
    content.shift();
    while (content.length && content[0].length < 80) content.shift();
  }
  return content.join("\n").trim().slice(0, 50_000);
}
function parseFlightContacts(strings) {
  const members = [];
  const seen = new Set();
  for (let index = 0; index < strings.length; index += 1) {
    const urlMatch = strings[index].match(/^https?:\/\/(?:www\.)?linkedin\.com\/in\/[^?\s]+/i);
    if (!urlMatch) continue;
    const profileUrl = canonicalUrl(urlMatch[0]);
    if (seen.has(profileUrl)) continue;
    const values = strings.slice(index + 1, index + 7).filter((value) => !structuralToken(value));
    const degreeIndex = values.findIndex((value) => /^(?:•\s*)?[123](?:st|nd|rd)$/i.test(value));
    const degree = degreeIndex < 0 ? "" : values[degreeIndex].replace(/^•\s*/, "");
    const name =
      values.find(
        (value) =>
          !/^(?:•\s*)?[123](?:st|nd|rd)$/i.test(value) &&
          !/^(?:job poster|message|connect|follow)$/i.test(value),
      ) ?? "";
    const headline =
      values.find(
        (value, valueIndex) =>
          valueIndex > degreeIndex &&
          value !== name &&
          !/^(?:job poster|message|connect|follow)$/i.test(value),
      ) ?? "";
    if (name && degree) {
      members.push({ name, profileUrl, degree, headline: bounded(headline, 300) });
      seen.add(profileUrl);
    }
  }
  return members.slice(0, 50);
}

function parseFlightContactsFromBody(body, strings) {
  const urls = [
    ...String(body).matchAll(/https?:\/\/(?:www\.)?linkedin\.com\/in\/[^"\\?\s]+/gi),
  ].map((match) => canonicalUrl(match[0]));
  const uniqueUrls = [...new Set(urls)];
  const degrees = strings
    .map((value, index) => ({ value, index }))
    .filter(({ value }) => /^(?:•\s*)?[123](?:st|nd|rd)$/i.test(value));
  return uniqueUrls
    .flatMap((profileUrl, index) => {
      const degreeEntry = degrees[index];
      if (!degreeEntry) return [];
      const name = strings[degreeEntry.index - 1] ?? "";
      const headline = strings[degreeEntry.index + 1] ?? "";
      if (!name || /^(?:meet the hiring team|people you can reach out to)$/i.test(name)) return [];
      return [
        {
          name: bounded(name, 300),
          profileUrl,
          degree: degreeEntry.value.replace(/^•\s*/, ""),
          headline: bounded(headline, 300),
        },
      ];
    })
    .slice(0, 50);
}

/** Parse real LinkedIn React Flight line records, plus legacy keyed payloads. */
export function parseScopedRsc(responses = []) {
  const body = (component) => responses.find((item) => item.component === component)?.body ?? "";
  const documentBody = body("document");
  const jobBody = body("aboutTheJob");
  const companyBody = body("aboutTheCompanyForJobDetails");
  const teamBody = body("peopleWhoCanHelp");
  const documentRecords = parseFlightRecords(documentBody);
  const jobRecords = parseFlightRecords(jobBody);
  const companyRecords = parseFlightRecords(companyBody);
  const teamRecords = parseFlightRecords(teamBody);
  const documentStrings = visibleFlightText(documentRecords);
  const jobStrings = visibleFlightText(jobRecords);
  const companyStrings = visibleFlightText(companyRecords);
  const teamStrings = visibleFlightText(teamRecords);
  const jobText = jobStrings.length ? jobStrings : visibleFlightText(flightLines(jobBody));
  const description =
    scopedDescription(jobText) ||
    scopedDescription(documentStrings) ||
    rscValue(jobBody, ["description", "descriptionText", "jobDescription"]);
  const metadataScan = unescapeRsc(`${documentBody}\n${jobBody}`);
  const externalApplicationUrl = rscValue(metadataScan, [
    "offsiteApplyUrl",
    "externalApplyUrl",
    "externalApplicationUrl",
    "applyUrl",
    "applicationUrl",
  ]);
  const applicantTrackingSystem = rscValue(metadataScan, [
    "applicantTrackingSystemName",
    "applicantTrackingSystem",
    "atsName",
  ]);
  const geoId = rscValue(metadataScan, ["jobGeoId", "geoId", "locationId"]);
  const hiringHeadingIndex = teamStrings.findIndex((value) =>
    /^meet the hiring team$/i.test(value),
  );
  const hiringBodyIndex = teamBody.indexOf("Meet the hiring team");
  const hiringStrings = hiringHeadingIndex < 0 ? [] : teamStrings.slice(hiringHeadingIndex + 1);
  const hiringBody = hiringBodyIndex < 0 ? "" : teamBody.slice(hiringBodyIndex);
  const members = parseFlightContacts(hiringStrings);
  if (!members.length) members.push(...parseFlightContactsFromBody(hiringBody, hiringStrings));
  const teamHasUrls = /https?:\/\/(?:www\.)?linkedin\.com\/in\//i.test(hiringBody);
  const hasHiringTeamSection = hiringHeadingIndex >= 0 || hiringBodyIndex >= 0;
  const companyEvidence = companyStrings
    .filter(
      (value) =>
        !/^(?:about the company|show more|see more|follow(?:ing)?|message|interested|notinterested|notfollowing|company photos|learn more|•)$/i.test(
          value,
        ) &&
        !/(?:interested in working with us|limit of 50|suggest prioritizing|express interest in this company|share that they’re interested)/i.test(
          value,
        ),
    )
    .slice(0, 20)
    .map((value) => value.slice(0, 1000));
  return {
    description,
    companyEvidence,
    hiringTeam: members,
    externalApplicationUrl: canonicalUrl(externalApplicationUrl),
    applicantTrackingSystem,
    geoId,
    hasPeopleResponse: responses.some((item) => item.component === "peopleWhoCanHelp"),
    peopleHasUrls: teamHasUrls,
    peopleConclusiveEmpty: !hasHiringTeamSection,
    hasCompanyResponse: responses.some((item) => item.component === "aboutTheCompanyForJobDetails"),
    text: [...documentStrings, ...jobStrings, ...companyStrings, ...teamStrings].join("\n"),
  };
}

function mergeEnrichment(dom, rsc) {
  const merged = { ...dom };
  if (rsc.description && rsc.description.length >= 40) merged.description = rsc.description;
  if (rsc.companyEvidence.length) merged.companyEvidence = rsc.companyEvidence;
  if (rsc.hiringTeam.length) merged.hiringTeam = rsc.hiringTeam;
  if (rsc.externalApplicationUrl) merged.externalApplicationUrl = rsc.externalApplicationUrl;
  if (rsc.applicantTrackingSystem) merged.applicantTrackingSystem = rsc.applicantTrackingSystem;
  if (rsc.geoId) merged.geoId = rsc.geoId;
  if (
    !merged.hiringTeam.length &&
    (!rsc.hasPeopleResponse || rsc.peopleHasUrls || !rsc.peopleConclusiveEmpty)
  )
    merged.outcome = "retry_required";
  else if (merged.description && merged.title && merged.company && merged.location)
    merged.outcome = merged.hiringTeam.length ? "complete_hiring_team" : "complete_no_hiring_team";
  return merged;
}

export async function extractJobEnrichment(tab, expected = {}, captured = []) {
  const url = (await tab.url()) ?? "";
  const title = await tab.title();
  if (blocked(url, title)) return { ok: false, reason: "login-or-checkpoint", sourceUrl: url };
  const first = await readStableSnapshot(tab);
  if (!first.text) return { ok: false, reason: "incomplete", sourceUrl: url };
  const parsed = parseJobSnapshot(first, expected);
  if (expected.id && parsed.id !== String(expected.id))
    return { ok: false, reason: "job-id-mismatch", sourceUrl: url };
  if (expected.sourceUrl && canonicalUrl(parsed.postingUrl) !== canonicalUrl(expected.sourceUrl))
    return { ok: false, reason: "source-mismatch", sourceUrl: url };
  await new Promise((resolve) =>
    setTimeout(resolve, Math.min(Math.max(Number(expected.stableWaitMs ?? 700), 200), 3000)),
  );
  const second = await readStableSnapshot(tab);
  const parsedSecond = parseJobSnapshot(second, expected);
  if (!parsedSecond.stable || parsed.stable !== parsedSecond.stable)
    return { ok: false, reason: "unstable-page", sourceUrl: url };
  const rsc = parseScopedRsc(captured);
  const merged = mergeEnrichment(parsedSecond, rsc);
  if (merged.outcome === "retry_required" && !(merged.title && merged.company && merged.location))
    return { ok: false, reason: "incomplete", sourceUrl: url };
  return { ok: true, ...merged, rawResponses: captured, capturedAt: new Date().toISOString() };
}

/** Read-only explicit-id probe. It only inspects the current page and never writes or navigates. */
export async function probeJobId(tab, jobId) {
  const url = (await tab.url()) ?? "";
  const title = await tab.title();
  if (blocked(url, title)) return { ok: false, reason: "login-or-checkpoint", url };
  const page = await tab.playwright.evaluate(
    () => ({
      url: location.href,
      pathId: location.pathname.match(/jobs\/view\/(\d+)/)?.[1] ?? "",
      hasDescription: Boolean(
        document.querySelector("main .jobs-description__content, main [class*='jobs-description']"),
      ),
      hasApplyControl: Boolean(document.querySelector("main button, main a")),
    }),
    undefined,
  );
  return { ok: page.pathId === String(jobId), ...page, requestedId: String(jobId) };
}

const RESPONSE_LIMITS = {
  document: 1_000_000,
  aboutTheJob: 120_000,
  aboutTheCompanyForJobDetails: 120_000,
  peopleWhoCanHelp: 120_000,
};
const responseComponent = (url) => {
  if (/componentId=aboutTheJob|aboutTheJob/i.test(url)) return "aboutTheJob";
  if (/componentId=aboutTheCompanyForJobDetails|aboutTheCompanyForJobDetails/i.test(url))
    return "aboutTheCompanyForJobDetails";
  if (/componentId=peopleWhoCanHelp|peopleWhoCanHelp/i.test(url)) return "peopleWhoCanHelp";
  return /jobs\/(?:view|details)\/\d+/i.test(url) || /linkedin\.com\/$/i.test(url)
    ? "document"
    : "";
};

export async function captureDirectPage(tab, sourceUrl, action) {
  const cdp = await tab.capabilities.get("cdp");
  await cdp.send("Network.enable");
  const methods = ["Network.responseReceived", "Network.loadingFinished", "Network.loadingFailed"];
  const armed = await cdp.readEvents({ limit: 1, timeoutMs: 0, methods });
  let actionDone = false;
  let actionCompletedAt = 0;
  let actionError;
  const actionPromise = Promise.resolve()
    .then(action)
    .then(
      () => {
        actionDone = true;
        actionCompletedAt = Date.now();
      },
      (error) => {
        actionDone = true;
        actionCompletedAt = Date.now();
        actionError = error;
      },
    );
  let cursor = armed.cursor;
  const pending = new Map();
  const capturedByComponent = new Map();
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const batch = await cdp.readEvents({
      afterSequence: cursor,
      limit: 1000,
      timeoutMs: 1000,
      methods,
    });
    cursor = batch.cursor;
    if (batch.truncated)
      return { captured: [...capturedByComponent.values()], failure: "cdp-truncated" };
    for (const event of batch.events) {
      const requestId = event.params?.requestId;
      if (!requestId) continue;
      if (event.method === "Network.responseReceived") {
        const response = event.params?.response;
        const component = response ? responseComponent(String(response.url)) : "";
        if (component) pending.set(requestId, { component, response });
      } else if (event.method === "Network.loadingFailed") pending.delete(requestId);
      else if (event.method === "Network.loadingFinished" && pending.has(requestId)) {
        const item = pending.get(requestId);
        pending.delete(requestId);
        try {
          const body = decodeResponseBody(await cdp.send("Network.getResponseBody", { requestId }));
          if (body.length > RESPONSE_LIMITS[item.component])
            return { captured: [...capturedByComponent.values()], failure: "response-too-large" };
          capturedByComponent.set(item.component, {
            component: item.component,
            sourceUrl,
            responseUrl: String(item.response.url),
            status: Number(item.response.status),
            capturedAt: new Date().toISOString(),
            parserVersion: ENRICHMENT_PARSER_VERSION,
            body,
          });
        } catch {
          /* body unavailable: DOM fallback still applies */
        }
      }
    }
    if (actionError) throw actionError;
    if (actionDone && capturedByComponent.size === 4) break;
    const settledFor = actionCompletedAt ? Date.now() - actionCompletedAt : 0;
    if (
      actionDone &&
      ["document", "aboutTheJob", "peopleWhoCanHelp"].every((component) =>
        capturedByComponent.has(component),
      ) &&
      settledFor >= 2_000
    )
      break;
    if (actionDone && settledFor >= 5_000) break;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  await actionPromise;
  const captured = [...capturedByComponent.values()];
  return {
    captured,
    failure: captured.some((item) => item.component === "document") ? "" : "document-not-captured",
  };
}

export async function navigateAndExtractJob(tab, config) {
  if (!config?.sourceUrl) return { ok: false, reason: "source-required" };
  if (blocked(config.sourceUrl))
    return { ok: false, reason: "login-or-checkpoint", sourceUrl: config.sourceUrl };
  let captured = [];
  try {
    // Arm CDP before the caller-owned navigation. No tab is created or closed.
    try {
      const capture = await captureDirectPage(tab, config.sourceUrl, async () => {
        // A same-URL reload is required to emit fresh response events.
        await tab.goto(config.sourceUrl);
        await tab.playwright.waitForLoadState({ state: "domcontentloaded", timeoutMs: 30_000 });
        await new Promise((resolve) => setTimeout(resolve, 500));
        for (let index = 0; index < 4; index += 1) {
          if (tab.dom_cua?.scroll) await tab.dom_cua.scroll({ x: 0, y: 900 });
          await new Promise((resolve) => setTimeout(resolve, 250));
        }
      });
      captured = capture.captured;
    } catch {
      // CDP is best-effort; stable DOM remains a valid fallback for team pages.
    }
    if (canonicalUrl((await tab.url()) ?? "") !== canonicalUrl(config.sourceUrl))
      await tab.goto(config.sourceUrl);
    await tab.playwright.waitForLoadState({ state: "domcontentloaded", timeoutMs: 30_000 });
    await new Promise((resolve) => setTimeout(resolve, 500));
    for (let index = 0; index < 4; index += 1) {
      if (tab.dom_cua?.scroll) await tab.dom_cua.scroll({ x: 0, y: 900 });
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return await extractJobEnrichment(tab, config, captured);
  } catch (error) {
    return {
      ok: false,
      reason: "browser-read-failed",
      sourceUrl: config.sourceUrl,
      detail: bounded(error instanceof Error ? error.message : error, 500),
    };
  }
}

/** Extract and hand one result to the CLI; the caller owns tab selection and navigation. */
export async function enrichAndRecordJob(tab, config) {
  const result = await navigateAndExtractJob(tab, config);
  if (!result.ok || config.readOnly) return result;
  const { ok: _ok, reason: _reason, stable: _stable, ...payload } = result;
  return pipeRawBodyToCli(
    JSON.stringify(payload),
    [
      "--json",
      "jobs",
      "enrich-record",
      "--payload",
      "-",
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
