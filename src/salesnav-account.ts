import type { Database } from "bun:sqlite";
import { randomUUID } from "node:crypto";
import { CliError } from "./core/errors.ts";
import { inTransaction } from "./db/database.ts";
import type { SalesNavInput } from "./salesnav.ts";

export const ACCOUNT_PARSER_VERSION = "salesnav-account-v1";
export const STUDIO_SEARCH_QUERIES = [
  '("product development" OR "software development" OR "web development" OR "application development") NOT (staffing OR recruiting OR "digital marketing")',
  '("product studio" OR "digital product" OR "custom software" OR "web application") NOT (staffing OR recruiting OR "digital marketing")',
] as const;
export const STUDIO_SEARCH_CONFIG = {
  country: "US",
  headcount: ["11-50"],
  industries: ["IT Services & IT Consulting", "Design Services"],
} as const;
const MAX_BODY_BYTES = 2_000_000;
const fail = (code: string, message: string): never => {
  throw new CliError(code, message, { exitCode: 2 });
};
const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const text = (v: unknown): string => (typeof v === "string" ? v.replace(/\s+/g, " ").trim() : "");
const canonical = (value: unknown): string =>
  Array.isArray(value)
    ? `[${value.map(canonical).join(",")}]`
    : value && typeof value === "object"
      ? `{${Object.entries(value as Record<string, unknown>)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
          .join(",")}}`
      : (JSON.stringify(value) ?? "null");
const boundedJsonObject = (value: string, name: string): Record<string, unknown> => {
  if (Buffer.byteLength(value, "utf8") > 32_000) fail("INVALID_ARGUMENT", `${name} exceeds 32 KB`);
  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    return parsed;
  } catch {
    return fail("INVALID_ARGUMENT", `${name} must be a JSON object`);
  }
};
const parseBody = (
  body: string,
): { parsed: Record<string, unknown>; paging: Record<string, unknown> } => {
  if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES)
    fail("SALESNAV_BODY_TOO_LARGE", "capture body exceeds 2 MB");
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return fail("SALESNAV_INVALID_BODY", "capture body must be valid JSON");
  }
  const row = obj(parsed),
    paging = obj(row.paging);
  if (
    !row.metadata ||
    typeof row.metadata !== "object" ||
    Array.isArray(row.metadata) ||
    !Array.isArray(row.elements) ||
    !Number.isInteger(paging.start) ||
    !Number.isInteger(paging.count) ||
    !Number.isInteger(paging.total) ||
    Number(paging.start) < 0 ||
    Number(paging.count) < 0 ||
    Number(paging.count) > 1000 ||
    Number(paging.total) < 0 ||
    Number(paging.total) > 1_000_000
  )
    fail("SALESNAV_INVALID_SCHEMA", "body requires bounded elements and paging");
  return { parsed: row, paging };
};

const decodeQuery = (value: string): string => {
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

const filterIds = (query: string, type: string): string[] => {
  const typePattern = /\btype:([A-Z_]+)\b/g;
  const matches = [...query.matchAll(typePattern)];
  const matchIndex = matches.findIndex((match) => match[1] === type);
  if (matchIndex < 0) return [];
  const start = (matches[matchIndex]?.index ?? 0) + (matches[matchIndex]?.[0].length ?? 0);
  const end = matches[matchIndex + 1]?.index ?? query.length;
  return [...query.slice(start, end).matchAll(/\bid:([A-Za-z0-9_-]+)\b/g)].map(
    (match) => match[1] ?? "",
  );
};

const exactSet = (actual: readonly string[], expected: readonly string[]): boolean =>
  actual.length === expected.length && expected.every((value) => actual.includes(value));

const accountSearchKeywords = (query: string): string => {
  const marker = query.lastIndexOf("keywords:");
  if (marker < 0) return "";
  const end = query.endsWith(")") ? query.length - 1 : query.length;
  const keywords = query.slice(marker + "keywords:".length, end).trim();
  const hasControlCharacter = [...keywords].some((character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127;
  });
  return keywords.length <= 500 && !hasControlCharacter ? keywords : "";
};

export function accountSearchDetails(
  value: string,
): {
  lane: "staffing" | "studio";
  keywordQuery: string;
  searchConfig: Record<string, unknown>;
} | null {
  try {
    const u = new URL(value),
      query = decodeQuery(u.searchParams.get("query") ?? "");
    const keywords = accountSearchKeywords(query);
    if (
      !(
        u.protocol === "https:" &&
        u.hostname === "www.linkedin.com" &&
        u.pathname === "/sales/search/company" &&
        query.includes("keywords:")
      )
    )
      return null;
    if (
      exactSet(filterIds(query, "COMPANY_HEADCOUNT"), ["C", "D"]) &&
      exactSet(filterIds(query, "INDUSTRY"), ["104"]) &&
      exactSet(filterIds(query, "REGION"), ["103644278"]) &&
      keywords.length > 0
    )
      return {
        lane: "staffing",
        keywordQuery: keywords,
        searchConfig: { region: "US", headcount: ["C", "D"], industryIds: ["104"] },
      };
    if (
      STUDIO_SEARCH_QUERIES.includes(keywords as (typeof STUDIO_SEARCH_QUERIES)[number]) &&
      exactSet(filterIds(query, "COMPANY_HEADCOUNT"), ["C"]) &&
      exactSet(filterIds(query, "INDUSTRY"), ["96", "7"]) &&
      exactSet(filterIds(query, "REGION"), ["103644278"])
    )
      return {
        lane: "studio",
        keywordQuery: keywords,
        searchConfig: STUDIO_SEARCH_CONFIG as unknown as Record<string, unknown>,
      };
    return null;
  } catch {
    return null;
  }
}
export function isAccountSearchUrl(value: string): boolean {
  return accountSearchDetails(value) !== null;
}
export function isAccountResponseUrl(value: string, start: number): boolean {
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
      Number(u.searchParams.get("start")) === start &&
      start <= 1_000_000
    );
  } catch {
    return false;
  }
}

const accountId = (v: unknown): string =>
  text(v).match(/urn:li:fs_salesCompany:([A-Za-z0-9_-]+)/i)?.[1] ?? "";
const normalized = (v: string) =>
  v
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
const evidence = (a: Record<string, unknown>) => ({
  companyName: text(a.companyName),
  description: text(a.description),
  industry: text(a.industry),
  employeeCountRange: a.employeeCountRange ?? null,
  employeeDisplayCount: text(a.employeeDisplayCount),
  entityUrn: text(a.entityUrn),
  spotlightBadges: a.spotlightBadges ?? [],
  companyPictureDisplayImage: a.companyPictureDisplayImage ?? null,
  trackingId: text(a.trackingId),
});

type Row = Record<string, unknown>;
export class SalesNavAccountStore {
  constructor(private readonly db: Database) {}
  private run(id: string): Row {
    const row = this.db
      .query<Row, [string]>("SELECT * FROM salesnav_account_runs WHERE id=?")
      .get(id);
    if (!row) return fail("SALESNAV_RUN_NOT_FOUND", `account run ${id} does not exist`);
    return row;
  }
  start(input: SalesNavInput, now: string): Row {
    const details = accountSearchDetails(input.sourceUrl ?? "");
    if (!details || (input.lane && input.lane !== details.lane))
      return fail("SALESNAV_WRONG_SEARCH", "source URL must match the account search contract");
    const checkpoint = input.checkpointJson
      ? boundedJsonObject(input.checkpointJson, "--checkpoint")
      : {};
    const checkpointJson = canonical(checkpoint);
    const lane = input.lane ?? details.lane;
    const searchConfigJson = canonical(details.searchConfig);
    const keywordQuery = details.keywordQuery;
    const existing = this.db
      .query<Row, [string]>("SELECT * FROM salesnav_account_runs WHERE id=?")
      .get(input.runId);
    if (existing) {
      const oldCheckpoint = JSON.parse(String(existing.checkpoint_json)) as Record<string, unknown>;
      if (
        existing.source_url !== input.sourceUrl ||
        (existing.lane && existing.lane !== lane) ||
        (existing.keyword_query && existing.keyword_query !== keywordQuery) ||
        (input.checkpointJson &&
          !Object.entries(checkpoint).every(
            ([key, value]) => canonical(oldCheckpoint[key]) === canonical(value),
          ))
      )
        fail("SALESNAV_RUN_CONFLICT", "capture run metadata conflicts");
      return existing;
    }
    const sourceUrl = input.sourceUrl ?? fail("INVALID_ARGUMENT", "--source-url is required");
    this.db
      .prepare(
        "INSERT INTO salesnav_account_runs(id,source_url,lane,search_config_json,keyword_query,checkpoint_json,started_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
      )
      .run(input.runId, sourceUrl, lane, searchConfigJson, keywordQuery, checkpointJson, now, now);
    return this.run(input.runId);
  }
  ingest(input: SalesNavInput, body: string, now: string) {
    const run = this.run(input.runId);
    if (run.state !== "active")
      fail("SALESNAV_RUN_NOT_ACTIVE", `capture run ${input.runId} is ${run.state}`);
    const start = input.start ?? -1;
    if (input.sourceUrl !== run.source_url)
      fail("SALESNAV_SOURCE_CONFLICT", "ingest source URL differs from run");
    if (!isAccountResponseUrl(input.responseUrl ?? "", start))
      fail("SALESNAV_WRONG_RESPONSE", "response URL must match the account-search contract");
    const { paging } = parseBody(body);
    if (paging.start !== start || paging.count !== 25)
      fail("SALESNAV_WRONG_START", "paging does not match --start or count 25");
    const existing = this.db
      .query<Row, [string, number]>(
        "SELECT * FROM salesnav_account_pages WHERE run_id=? AND start=?",
      )
      .get(input.runId, start);
    if (existing) {
      if (existing.response_url !== input.responseUrl || existing.payload_json !== body)
        fail("SALESNAV_PAGE_CONFLICT", "retry conflicts with captured page");
      return { inserted: false, start };
    }
    const sourceUrl = input.sourceUrl ?? fail("INVALID_ARGUMENT", "--source-url is required"),
      responseUrl = input.responseUrl ?? fail("INVALID_ARGUMENT", "--response-url is required");
    this.db
      .prepare(
        "INSERT INTO salesnav_account_pages(run_id,start,source_url,response_url,status,paging_count,paging_total,parser_version,payload_json,captured_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
      )
      .run(
        input.runId,
        start,
        sourceUrl,
        responseUrl,
        200,
        Number(paging.count),
        Number(paging.total),
        ACCOUNT_PARSER_VERSION,
        body,
        input.capturedAt ?? now,
      );
    this.db
      .prepare(
        "UPDATE salesnav_account_runs SET updated_at=?,checkpoint_json=json_set(checkpoint_json,'$.last_start',?) WHERE id=?",
      )
      .run(now, start, input.runId);
    return { inserted: true, start, count: paging.count, total: paging.total };
  }
  finish(input: SalesNavInput, now: string): Row {
    const existing = this.run(input.runId),
      state = input.state ?? fail("INVALID_ARGUMENT", "--state is required");
    const update = input.checkpointJson
      ? boundedJsonObject(input.checkpointJson, "--checkpoint")
      : {};
    const oldCheckpoint = JSON.parse(String(existing.checkpoint_json)) as Record<string, unknown>;
    const checkpointJson = canonical({ ...oldCheckpoint, ...update });
    if (existing.state !== "active") {
      if (
        existing.state !== state ||
        existing.error !== (input.error ?? null) ||
        existing.checkpoint_json !== checkpointJson
      )
        fail("SALESNAV_FINISH_CONFLICT", "capture finish conflicts");
      return existing;
    }
    this.db
      .prepare(
        "UPDATE salesnav_account_runs SET state=?,error=?,checkpoint_json=?,completed_at=?,updated_at=? WHERE id=?",
      )
      .run(state, input.error ?? null, checkpointJson, now, now, input.runId);
    return this.run(input.runId);
  }
  normalize(input: SalesNavInput, now: string) {
    this.run(input.runId);
    const pages = this.db
      .query<Row, [string, string, number]>(
        "SELECT * FROM salesnav_account_pages WHERE run_id=? AND start NOT IN (SELECT start FROM salesnav_account_page_normalizations WHERE run_id=?) ORDER BY start LIMIT ?",
      )
      .all(input.runId, input.runId, input.limit ?? 500);
    for (const page of pages)
      inTransaction(this.db, () => {
        const { parsed } = parseBody(String(page.payload_json));
        let count = 0;
        for (const raw of parsed.elements as unknown[]) {
          const a = obj(raw),
            id = accountId(a.entityUrn);
          const name = text(a.companyName);
          if (!id || !name) continue;
          const urn = `urn:li:fs_salesCompany:${id}`,
            norm = normalized(name);
          let org = this.db
            .query<Row, [string]>("SELECT * FROM organizations WHERE company_urn=?")
            .get(urn);
          if (!org)
            org = this.db
              .query<Row, [string]>(
                "SELECT * FROM organizations WHERE normalized_name=? AND company_urn IS NULL LIMIT 1",
              )
              .get(norm);
          const ev = evidence(a),
            evJson = JSON.stringify({ account: ev });
          if (!org) {
            const oid = randomUUID();
            this.db
              .prepare(
                "INSERT INTO organizations(id,dedupe_key,company_urn,normalized_name,name,linkedin_company_url,industry,size_text,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              )
              .run(
                oid,
                urn,
                urn,
                norm,
                name,
                `https://www.linkedin.com/sales/company/${id}`,
                text(a.industry),
                text(a.employeeDisplayCount) || text(a.employeeCountRange),
                evJson,
                now,
                now,
              );
            org = { id: oid };
          } else {
            const merged = { ...obj(JSON.parse(String(org?.evidence_json || "{}"))), account: ev };
            this.db
              .prepare(
                "UPDATE organizations SET company_urn=COALESCE(company_urn,?),dedupe_key=?,name=CASE WHEN name='' THEN ? ELSE name END,linkedin_company_url=CASE WHEN linkedin_company_url='' THEN ? ELSE linkedin_company_url END,industry=CASE WHEN industry='' THEN ? ELSE industry END,size_text=CASE WHEN size_text='' THEN ? ELSE size_text END,evidence_json=?,updated_at=? WHERE id=?",
              )
              .run(
                urn,
                urn,
                name,
                `https://www.linkedin.com/sales/company/${id}`,
                text(a.industry),
                text(a.employeeDisplayCount) || text(a.employeeCountRange),
                JSON.stringify(merged),
                now,
                String(org?.id ?? ""),
              );
          }
          const organizationId = String(
            org?.id ?? fail("SALESNAV_ACCOUNT_NOT_FOUND", "organization was not created"),
          );
          this.db
            .prepare(
              "INSERT OR IGNORE INTO salesnav_account_observations(run_id,start,account_id,organization_id,observed_at,source_evidence_json) VALUES(?,?,?,?,?,?)",
            )
            .run(input.runId, Number(page.start), id, organizationId, now, evJson);
          count++;
        }
        this.db
          .prepare(
            "INSERT INTO salesnav_account_page_normalizations(run_id,start,parser_version,observed_count,normalized_at) VALUES(?,?,?,?,?)",
          )
          .run(input.runId, Number(page.start), ACCOUNT_PARSER_VERSION, count, now);
      });
    return { runId: input.runId, normalizedPages: pages.length };
  }
  next(input: SalesNavInput) {
    const run = this.run(input.runId);
    const lane = String(run.lane ?? input.lane ?? "staffing");
    return (
      this.db
        .query<Row, [string, string]>(
          "SELECT o.run_id,o.account_id,o.organization_id,org.name,org.linkedin_company_url,org.website_url,org.industry,org.size_text,org.evidence_json,o.source_evidence_json FROM salesnav_account_observations o JOIN organizations org ON org.id=o.organization_id WHERE o.run_id=? AND NOT EXISTS (SELECT 1 FROM salesnav_account_lane_qualifications q WHERE q.lane=? AND q.organization_id=o.organization_id) ORDER BY o.start,o.account_id LIMIT 1",
        )
        .get(input.runId, lane) ?? null
    );
  }
  record(input: SalesNavInput, now: string) {
    const run = this.run(input.runId);
    const lane = String(run.lane ?? input.lane ?? "staffing");
    const evidenceJson = canonical(boundedJsonObject(input.evidenceJson ?? "{}", "--evidence")),
      rawUnknownsJson = input.unknownsJson ?? "[]";
    let unknowns: unknown;
    try {
      unknowns = JSON.parse(rawUnknownsJson);
    } catch {
      fail("INVALID_ARGUMENT", "--unknowns must be a JSON array");
    }
    if (
      !Array.isArray(unknowns) ||
      unknowns.some((v) => typeof v !== "string") ||
      Buffer.byteLength(rawUnknownsJson, "utf8") > 16_000
    )
      fail("INVALID_ARGUMENT", "--unknowns must be a bounded JSON array of strings");
    const unknownsJson = canonical(unknowns);
    const organizationId =
      input.organizationId ?? fail("INVALID_ARGUMENT", "--organization-id is required");
    const org = this.db
      .query<Row, [string, string, string]>(
        "SELECT account_id,organization_id FROM salesnav_account_observations WHERE run_id=? AND (account_id=? OR organization_id=?) LIMIT 1",
      )
      .get(input.runId, organizationId, organizationId);
    if (!org) fail("SALESNAV_ACCOUNT_NOT_FOUND", "account was not observed in this run");
    const fit = input.fit ?? fail("INVALID_ARGUMENT", "--fit is required"),
      reason = input.reason ?? fail("INVALID_ARGUMENT", "--reason is required"),
      policy = input.policyVersion ?? fail("INVALID_ARGUMENT", "--policy-version is required");
    if (
      lane === "studio" &&
      fit === "kept" &&
      !this.db
        .query<Row, [string]>(
          "SELECT organization_id FROM salesnav_studio_firm_research WHERE organization_id=? AND lane='studio'",
        )
        .get(String(org?.organization_id))
    )
      fail("SALESNAV_FIRM_RESEARCH_REQUIRED", "studio keep requires firm research");
    const prior = this.db
      .query<Row, [string, string]>(
        "SELECT * FROM salesnav_account_lane_qualifications WHERE lane=? AND organization_id=?",
      )
      .get(lane, String(org?.organization_id));
    if (prior) {
      if (
        prior.fit !== fit ||
        prior.evidence_json !== evidenceJson ||
        prior.unknowns_json !== unknownsJson ||
        prior.reason !== reason ||
        prior.policy_version !== policy
      )
        fail(
          "SALESNAV_QUALIFICATION_CONFLICT",
          "organization qualification conflicts with prior decision",
        );
      return {
        runId: input.runId,
        accountId: org?.account_id,
        organizationId: org?.organization_id,
        fit,
      };
    }
    this.db
      .prepare(
        "INSERT INTO salesnav_account_lane_qualifications(lane,organization_id,source_run_id,account_id,fit,evidence_json,unknowns_json,reason,policy_version,filtered_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
      )
      .run(
        lane,
        String(org?.organization_id),
        input.runId,
        String(org?.account_id),
        fit,
        evidenceJson,
        unknownsJson,
        reason,
        policy,
        now,
      );
    if (lane === "staffing")
      this.db
        .prepare(
          "INSERT OR IGNORE INTO salesnav_account_qualifications(organization_id,source_run_id,account_id,fit,evidence_json,unknowns_json,reason,policy_version,filtered_at) VALUES(?,?,?,?,?,?,?,?,?)",
        )
        .run(
          String(org?.organization_id),
          input.runId,
          String(org?.account_id),
          fit,
          evidenceJson,
          unknownsJson,
          reason,
          policy,
          now,
        );
    return {
      runId: input.runId,
      accountId: org?.account_id,
      organizationId: org?.organization_id,
      fit,
    };
  }
  firmResearch(input: SalesNavInput, now: string) {
    const run = this.run(input.runId);
    if (String(run.lane ?? "staffing") !== "studio")
      fail("SALESNAV_WRONG_LANE", "firm research is only available for studio runs");
    const organizationId =
      input.organizationId ?? fail("INVALID_ARGUMENT", "--organization-id is required");
    const urls = input.sourceUrlsJson ?? fail("INVALID_ARGUMENT", "--source-urls is required");
    let parsedUrls: unknown, unknowns: unknown;
    try {
      parsedUrls = JSON.parse(urls);
      unknowns = JSON.parse(input.unknownsJson ?? "[]");
    } catch {
      fail("INVALID_ARGUMENT", "firm research JSON is malformed");
    }
    if (
      !Array.isArray(parsedUrls) ||
      parsedUrls.length === 0 ||
      parsedUrls.some((v) => typeof v !== "string") ||
      !Array.isArray(unknowns) ||
      unknowns.some((v) => typeof v !== "string")
    )
      fail("INVALID_ARGUMENT", "firm research URLs and unknowns must be JSON arrays of strings");
    const services = input.services?.trim() ?? "",
      concreteFact = input.concreteFact?.trim() ?? "";
    if (!services || !concreteFact) fail("INVALID_ARGUMENT", "--services and --fact are required");
    if (
      !this.db
        .query<Row, [string, string]>(
          "SELECT 1 FROM salesnav_account_observations WHERE run_id=? AND organization_id=?",
        )
        .get(input.runId, organizationId)
    )
      fail("SALESNAV_ACCOUNT_NOT_FOUND", "organization was not observed in this run");
    const values = [
      canonical(parsedUrls),
      services,
      concreteFact,
      canonical(unknowns),
      input.reviewedAt ?? now,
    ];
    const old = this.db
      .query<Row, [string]>(
        "SELECT * FROM salesnav_studio_firm_research WHERE lane='studio' AND organization_id=?",
      )
      .get(organizationId);
    if (old) {
      if (
        [
          old.source_urls_json,
          old.services,
          old.concrete_fact,
          old.unknowns_json,
          old.reviewed_at,
        ].some((v, i) => String(v) !== values[i])
      )
        fail("SALESNAV_FIRM_RESEARCH_CONFLICT", "firm research conflicts with prior record");
      return old;
    }
    this.db
      .prepare(
        "INSERT INTO salesnav_studio_firm_research(lane,organization_id,source_urls_json,services,concrete_fact,unknowns_json,reviewed_at) VALUES('studio',?,?,?,?,?,?)",
      )
      .run(organizationId, ...values);
    return { lane: "studio", organizationId, reviewedAt: values[4] };
  }
  peopleCandidates(input: SalesNavInput) {
    const run = this.run(input.runId),
      lane = String(run.lane ?? input.lane ?? "staffing");
    const accounts = this.db
      .query<Row, [string, string]>(
        `SELECT DISTINCT o.account_id,o.organization_id,org.name,org.linkedin_company_url,
          org.website_url,org.industry,org.size_text,q.reason,q.policy_version
         FROM salesnav_account_observations o
         JOIN organizations org ON org.id=o.organization_id
         JOIN salesnav_account_lane_qualifications q
           ON q.organization_id=o.organization_id AND q.lane=? AND q.fit='kept'
         WHERE o.run_id=?
         ORDER BY org.name,o.account_id`,
      )
      .all(lane, input.runId);
    return { runId: input.runId, lane, accounts };
  }
  status(runId: string) {
    const run = this.run(runId),
      lane = String(run.lane ?? "staffing"),
      count = (sql: string, params: string[] = [runId]) =>
        Number(this.db.query<Row, string[]>(sql).get(...params)?.n ?? 0);
    return {
      run,
      pages: count("SELECT count(*) n FROM salesnav_account_pages WHERE run_id=?"),
      normalizedPages: count(
        "SELECT count(*) n FROM salesnav_account_page_normalizations WHERE run_id=?",
      ),
      accounts: count(
        "SELECT count(DISTINCT account_id) n FROM salesnav_account_observations WHERE run_id=?",
      ),
      pending: count(
        "SELECT count(*) n FROM salesnav_account_observations o WHERE o.run_id=? AND NOT EXISTS (SELECT 1 FROM salesnav_account_lane_qualifications q WHERE q.lane=? AND q.organization_id=o.organization_id)",
        [runId, lane],
      ),
      kept: count(
        "SELECT count(DISTINCT o.organization_id) n FROM salesnav_account_observations o JOIN salesnav_account_lane_qualifications q ON q.organization_id=o.organization_id WHERE o.run_id=? AND q.lane=? AND q.fit='kept'",
        [runId, lane],
      ),
      dropped: count(
        "SELECT count(DISTINCT o.organization_id) n FROM salesnav_account_observations o JOIN salesnav_account_lane_qualifications q ON q.organization_id=o.organization_id WHERE o.run_id=? AND q.lane=? AND q.fit='dropped'",
        [runId, lane],
      ),
    };
  }
}
