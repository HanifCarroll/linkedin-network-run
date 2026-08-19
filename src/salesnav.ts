import { randomUUID } from "node:crypto";
import { Database } from "bun:sqlite";
import { CliError } from "./core/errors.ts";
import { inTransaction } from "./db/database.ts";

export const SALESNAV_SEARCH_ID = "2006360906";
export const SALESNAV_PARSER_VERSION = "salesnav-staffing-v2";
const MAX_BODY_BYTES = 2_000_000;
const ROLE_TERMS = ["technical recruiter", "recruiter", "account manager"] as const;
const TECHNICAL_TERMS = ["software", "engineering", "product"] as const;
type Row = Record<string, unknown>;

export type SalesNavLane = "staffing" | "studio";
export type SalesNavInput = {
  readonly command:
    | "capture-start"
    | "capture-ingest"
    | "capture-finish"
    | "normalize"
    | "qualify"
    | "status"
    | "account-capture-start"
    | "account-capture-ingest"
    | "account-capture-finish"
    | "account-normalize"
    | "account-status"
    | "account-qualify-next"
    | "account-qualify-record"
    | "account-people-candidates"
    | "firm-research-record";
  readonly lane?: SalesNavLane;
  readonly stateDir: string;
  readonly runId: string;
  readonly sourceUrl?: string;
  readonly checkpointJson?: string;
  readonly searchConfigJson?: string;
  readonly start?: number;
  readonly payloadPath?: string;
  readonly responseUrl?: string;
  readonly capturedAt?: string;
  readonly state?: "complete" | "failed";
  readonly error?: string;
  readonly limit?: number;
  readonly policyVersion?: string;
  readonly organizationId?: string;
  readonly fit?: "kept" | "dropped";
  readonly evidenceJson?: string;
  readonly unknownsJson?: string;
  readonly reason?: string;
  readonly sourceUrlsJson?: string;
  readonly services?: string;
  readonly concreteFact?: string;
  readonly reviewedAt?: string;
};

const fail = (code: string, message: string): never => {
  throw new CliError(code, message, { exitCode: 2 });
};
const object = (value: unknown): Row =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Row) : {};
const string = (value: unknown): string =>
  typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
const canonical = (value: unknown): string =>
  Array.isArray(value)
    ? `[${value.map(canonical).join(",")}]`
    : value && typeof value === "object"
      ? `{${Object.entries(value as Row)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
          .join(",")}}`
      : (JSON.stringify(value) ?? "null");
const jsonObject = (value: string, name: string): Row => {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    return parsed as Row;
  } catch {
    return fail("INVALID_ARGUMENT", `${name} must be a JSON object`);
  }
};
const parseJson = (value: string, name: string): Row => {
  try {
    return JSON.parse(value) as Row;
  } catch {
    return fail("SALESNAV_INVALID_BODY", `${name} must be valid JSON`);
  }
};

export function isStaffingSearchUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.hostname === "www.linkedin.com" &&
      url.pathname === "/sales/search/people" &&
      url.searchParams.get("savedSearchId") === SALESNAV_SEARCH_ID
    );
  } catch {
    return false;
  }
}

export function isSalesApiLeadSearchUrl(value: string, expectedStart: number): boolean {
  try {
    const url = new URL(value);
    const start = url.searchParams.get("start");
    return (
      url.protocol === "https:" &&
      url.hostname === "www.linkedin.com" &&
      url.pathname === "/sales-api/salesApiLeadSearch" &&
      url.searchParams.get("q") === "savedSearchId" &&
      url.searchParams.get("savedSearchId") === SALESNAV_SEARCH_ID &&
      start !== null &&
      /^\d+$/.test(start) &&
      Number(start) === expectedStart &&
      Number(start) <= 1_000_000
    );
  } catch {
    return false;
  }
}

function leadId(value: unknown): string {
  const raw = string(value);
  return (
    raw.match(/urn:li:fs_salesProfile:\(([^,\s]+),/i)?.[1] ??
    raw.match(/urn:li:fs_salesProfile:([^,\s)]+)/i)?.[1] ??
    raw.match(/(?:urn:li:salesLead:|sales_profile:|lead:|\/sales\/lead\/)([A-Za-z0-9_-]+)/i)?.[1] ??
    ""
  );
}

function spotlight(value: unknown): string[] {
  const values: string[] = [];
  const visit = (item: unknown): void => {
    if (typeof item === "string") {
      if (item.trim()) values.push(string(item));
      return;
    }
    if (Array.isArray(item)) {
      item.forEach(visit);
      return;
    }
    const row = object(item);
    if ("displayValue" in row) visit(row.displayValue);
    if ("displayValues" in row) visit(row.displayValues);
    if ("strings" in row) visit(row.strings);
  };
  visit(value);
  return [...new Set(values)];
}

function payload(body: string): { payload: Row; paging: Row } {
  if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES)
    fail("SALESNAV_BODY_TOO_LARGE", "capture body exceeds 2 MB");
  const parsed = parseJson(body, "capture body");
  const paging = object(parsed.paging);
  const start = paging.start;
  const count = paging.count;
  const total = paging.total;
  if (
    !Array.isArray(parsed.elements) ||
    !Number.isInteger(start) ||
    !Number.isInteger(count) ||
    !Number.isInteger(total) ||
    (start as number) < 0 ||
    (count as number) < 0 ||
    (count as number) > 1000 ||
    (total as number) < 0 ||
    (total as number) > 1_000_000
  )
    fail("SALESNAV_INVALID_SCHEMA", "body requires bounded elements and paging");
  return { payload: parsed, paging };
}

function runRow(database: Database, runId: string): Row {
  const row = database
    .query<Row, [string]>("SELECT * FROM salesnav_staffing_runs WHERE id=?")
    .get(runId);
  if (!row) return fail("SALESNAV_RUN_NOT_FOUND", `salesnav run ${runId} does not exist`);
  return row;
}

export class SalesNavStore {
  constructor(private readonly database: Database) {}

  start(input: SalesNavInput, now: string): Row {
    const sourceUrl = input.sourceUrl ?? "";
    if (!isStaffingSearchUrl(sourceUrl))
      fail("SALESNAV_WRONG_SEARCH", "source URL must be the supported saved search");
    const checkpoint = input.checkpointJson ? jsonObject(input.checkpointJson, "--checkpoint") : {};
    const existing = this.database
      .query<Row, [string]>("SELECT * FROM salesnav_staffing_runs WHERE id=?")
      .get(input.runId);
    if (existing) {
      const oldCheckpoint = JSON.parse(String(existing.checkpoint_json)) as Row;
      if (
        existing.source_url !== sourceUrl ||
        (input.checkpointJson &&
          !Object.entries(checkpoint).every(
            ([key, value]) => canonical(oldCheckpoint[key]) === canonical(value),
          ))
      )
        fail("SALESNAV_RUN_CONFLICT", "capture run metadata conflicts");
      return existing;
    }
    this.database
      .prepare(
        "INSERT INTO salesnav_staffing_runs(id,source_url,saved_search_id,checkpoint_json,started_at,updated_at) VALUES(?,?,?,?,?,?)",
      )
      .run(input.runId, sourceUrl, SALESNAV_SEARCH_ID, canonical(checkpoint), now, now);
    return runRow(this.database, input.runId);
  }

  ingest(
    input: SalesNavInput,
    body: string,
    now: string,
  ): { inserted: boolean; start: number; count?: number; total?: number } {
    const run = runRow(this.database, input.runId);
    if (run.state !== "active")
      fail("SALESNAV_RUN_NOT_ACTIVE", `capture run ${input.runId} is ${run.state}`);
    const sourceUrl = input.sourceUrl ?? "";
    const responseUrl = input.responseUrl ?? "";
    if (sourceUrl !== run.source_url)
      fail("SALESNAV_SOURCE_CONFLICT", "ingest source URL differs from the run source");
    const start = input.start ?? -1;
    if (!isSalesApiLeadSearchUrl(responseUrl, start))
      fail(
        "SALESNAV_WRONG_RESPONSE",
        "response URL must match the Sales Navigator lead-search query",
      );
    const { paging } = payload(body);
    if (paging.start !== start) fail("SALESNAV_WRONG_START", "paging.start does not match --start");
    const count = paging.count as number;
    const total = paging.total as number;
    const existing = this.database
      .query<Row, [string, number]>(
        "SELECT * FROM salesnav_staffing_pages WHERE run_id=? AND start=?",
      )
      .get(input.runId, start);
    if (existing) {
      if (
        existing.source_url !== sourceUrl ||
        existing.response_url !== responseUrl ||
        existing.payload_json !== body ||
        existing.paging_count !== count ||
        existing.paging_total !== total
      )
        fail("SALESNAV_PAGE_CONFLICT", "retry conflicts with captured page");
      return { inserted: false, start };
    }
    this.database
      .prepare(
        "INSERT INTO salesnav_staffing_pages(run_id,start,source_url,response_url,status,paging_count,paging_total,parser_version,payload_json,captured_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
      )
      .run(
        input.runId,
        start,
        sourceUrl,
        responseUrl,
        200,
        count,
        total,
        SALESNAV_PARSER_VERSION,
        body,
        input.capturedAt ?? now,
      );
    this.database
      .prepare(
        "UPDATE salesnav_staffing_runs SET updated_at=?,checkpoint_json=json_set(checkpoint_json,'$.last_start',?) WHERE id=?",
      )
      .run(now, start, input.runId);
    return { inserted: true, start, count, total };
  }

  finish(input: SalesNavInput, now: string): Row {
    const existing = runRow(this.database, input.runId);
    const update = input.checkpointJson ? jsonObject(input.checkpointJson, "--checkpoint") : {};
    const oldCheckpoint = JSON.parse(String(existing.checkpoint_json)) as Row;
    const checkpoint = { ...oldCheckpoint, ...update };
    if (existing.state !== "active") {
      if (
        existing.state !== input.state ||
        existing.error !== (input.error ?? null) ||
        (input.checkpointJson && canonical(oldCheckpoint) !== canonical(checkpoint))
      )
        fail("SALESNAV_FINISH_CONFLICT", "capture finish conflicts");
      return existing;
    }
    this.database
      .prepare(
        "UPDATE salesnav_staffing_runs SET state=?,error=?,checkpoint_json=?,completed_at=?,updated_at=? WHERE id=?",
      )
      .run(
        input.state ?? fail("INVALID_ARGUMENT", "--state is required"),
        input.error ?? null,
        canonical(checkpoint),
        now,
        now,
        input.runId,
      );
    return runRow(this.database, input.runId);
  }

  normalize(input: SalesNavInput, now: string): { runId: string; normalizedPages: number } {
    runRow(this.database, input.runId);
    const pages = this.database
      .query<Row, [string, string, number]>(
        "SELECT * FROM salesnav_staffing_pages WHERE run_id=? AND start NOT IN (SELECT start FROM salesnav_staffing_page_normalizations WHERE run_id=?) ORDER BY start LIMIT ?",
      )
      .all(input.runId, input.runId, input.limit ?? 500);
    for (const page of pages) {
      inTransaction(this.database, () => this.normalizePage(input.runId, page, now));
    }
    return { runId: input.runId, normalizedPages: pages.length };
  }

  private normalizePage(runId: string, page: Row, now: string): void {
    const parsed = parseJson(String(page.payload_json), "stored capture body");
    const elements = parsed.elements as unknown[];
    for (const value of elements) {
      const lead = object(value);
      const id = leadId(lead.entityUrn ?? lead.objectUrn ?? lead.urn);
      if (!id) continue;
      const position = object(
        Array.isArray(lead.currentPositions) ? lead.currentPositions[0] : undefined,
      );
      const fullName = string(lead.fullName);
      const companyName = string(position.companyName);
      const companyUrn = string(position.companyUrn);
      const normalizedName = companyName
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, " ")
        .trim();
      const dedupeKey = companyUrn || `name:${normalizedName}`;
      let organization = this.database
        .query<Row, [string]>("SELECT * FROM organizations WHERE dedupe_key=?")
        .get(dedupeKey);
      if (!organization && normalizedName) {
        organization = this.database
          .query<Row, [string]>(
            "SELECT * FROM organizations WHERE normalized_name=? AND company_urn IS NULL LIMIT 1",
          )
          .get(normalizedName);
      }
      if (organization && companyUrn && !organization.company_urn) {
        this.database
          .prepare("UPDATE organizations SET company_urn=?, dedupe_key=?, updated_at=? WHERE id=?")
          .run(companyUrn, dedupeKey, now, String(organization.id));
      }
      if (!organization && normalizedName) {
        const idValue = randomUUID();
        this.database
          .prepare(
            "INSERT INTO organizations(id,dedupe_key,company_urn,normalized_name,name,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
          )
          .run(idValue, dedupeKey, companyUrn || null, normalizedName, companyName, now, now);
        organization = { id: idValue };
      }
      let person = this.database
        .query<Row, [string]>("SELECT * FROM people WHERE sales_nav_id=?")
        .get(id);
      if (!person) {
        const personId = randomUUID();
        this.database
          .prepare(
            "INSERT INTO people(id,sales_nav_id,name,created_at,updated_at) VALUES(?,?,?,?,?)",
          )
          .run(personId, id, fullName || id, now, now);
        person = { id: personId };
      }
      const positionTenure = object(position.tenureAtPosition);
      const tenure = [
        positionTenure.numYears ? `${positionTenure.numYears}y` : "",
        positionTenure.numMonths ? `${positionTenure.numMonths}m` : "",
      ]
        .filter(Boolean)
        .join(" ");
      const evidence = JSON.stringify(lead);
      this.database
        .prepare(
          "INSERT INTO salesnav_staffing_leads(sales_nav_id,person_id,organization_id,lead_url,full_name,geo_region,degree,current_title,current_company,current_company_urn,current_description,current_tenure,summary,spotlight_json,source_evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sales_nav_id) DO UPDATE SET person_id=excluded.person_id,organization_id=excluded.organization_id,lead_url=excluded.lead_url,full_name=excluded.full_name,geo_region=excluded.geo_region,degree=excluded.degree,current_title=excluded.current_title,current_company=excluded.current_company,current_company_urn=excluded.current_company_urn,current_description=excluded.current_description,current_tenure=excluded.current_tenure,summary=excluded.summary,spotlight_json=excluded.spotlight_json,source_evidence_json=excluded.source_evidence_json,updated_at=excluded.updated_at",
        )
        .run(
          id,
          String(person.id),
          organization ? String(organization.id) : null,
          `https://www.linkedin.com/sales/lead/${id}`,
          fullName,
          string(lead.geoRegion),
          string(lead.degree),
          string(position.title),
          companyName,
          companyUrn,
          string(position.description),
          tenure,
          string(lead.summary),
          JSON.stringify(spotlight(lead.spotlightBadges)),
          evidence,
          now,
          now,
        );
      this.database
        .prepare(
          "INSERT OR IGNORE INTO salesnav_staffing_observations(run_id,start,sales_nav_id,observed_at,source_evidence_json) VALUES(?,?,?,?,?)",
        )
        .run(runId, Number(page.start), id, now, evidence);
    }
    this.database
      .prepare(
        "INSERT INTO salesnav_staffing_page_normalizations(run_id,start,parser_version,observed_count,normalized_at) VALUES(?,?,?,?,?)",
      )
      .run(runId, Number(page.start), SALESNAV_PARSER_VERSION, elements.length, now);
  }

  qualify(input: SalesNavInput, now: string): { runId: string; kept: number; dropped: number } {
    runRow(this.database, input.runId);
    const leads = this.database
      .query<Row, [string]>(
        "SELECT * FROM salesnav_staffing_leads WHERE sales_nav_id IN (SELECT sales_nav_id FROM salesnav_staffing_observations WHERE run_id=?) ORDER BY sales_nav_id",
      )
      .all(input.runId);
    let kept = 0;
    for (const lead of leads) {
      const fields = {
        title: string(lead.current_title),
        description: string(lead.current_description),
        summary: string(lead.summary),
      };
      const haystack = Object.values(fields).join(" ").toLowerCase();
      const hasTerm = (term: string) =>
        new RegExp(`\\b${term.replace(/ /g, "\\s+")}\\b`, "i").test(haystack);
      const matchedRole = ROLE_TERMS.find(hasTerm);
      const matchedRoleTerms = matchedRole ? [matchedRole] : [];
      const matchedTechnicalTerms = TECHNICAL_TERMS.filter(hasTerm);
      const fit = Boolean(
        lead.person_id &&
          fields.title &&
          string(lead.current_company) &&
          matchedRoleTerms.length &&
          matchedTechnicalTerms.length,
      )
        ? "kept"
        : "dropped";
      const reason =
        fit === "kept"
          ? "stable identity, current title/company, role and technical matches"
          : "missing stable identity/current title/company or role/technical match";
      this.database
        .prepare(
          "INSERT INTO salesnav_staffing_qualifications(run_id,sales_nav_id,fit,matched_role_terms_json,matched_technical_terms_json,evidence_json,reason,policy_version,filtered_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,sales_nav_id) DO UPDATE SET fit=excluded.fit,matched_role_terms_json=excluded.matched_role_terms_json,matched_technical_terms_json=excluded.matched_technical_terms_json,evidence_json=excluded.evidence_json,reason=excluded.reason,policy_version=excluded.policy_version,filtered_at=excluded.filtered_at",
        )
        .run(
          input.runId,
          String(lead.sales_nav_id),
          fit,
          JSON.stringify(matchedRoleTerms),
          JSON.stringify(matchedTechnicalTerms),
          JSON.stringify(fields),
          reason,
          input.policyVersion ?? fail("INVALID_ARGUMENT", "--policy-version is required"),
          now,
        );
      if (fit === "kept") kept++;
    }
    return { runId: input.runId, kept, dropped: leads.length - kept };
  }

  status(runId: string): Row {
    const run = runRow(this.database, runId);
    const count = (sql: string, params: string[] = [runId]) =>
      Number(this.database.query<Row, string[]>(sql).get(...params)?.n ?? 0);
    return {
      run,
      pages: count("SELECT count(*) n FROM salesnav_staffing_pages WHERE run_id=?"),
      normalizedPages: count(
        "SELECT count(*) n FROM salesnav_staffing_page_normalizations WHERE run_id=?",
      ),
      people: count(
        "SELECT count(DISTINCT sales_nav_id) n FROM salesnav_staffing_observations WHERE run_id=?",
      ),
      organizations: count(
        "SELECT count(DISTINCT organization_id) n FROM salesnav_staffing_leads WHERE sales_nav_id IN (SELECT sales_nav_id FROM salesnav_staffing_observations WHERE run_id=?)",
      ),
      pending: count(
        "SELECT count(*) n FROM salesnav_staffing_observations o WHERE o.run_id=? AND NOT EXISTS (SELECT 1 FROM salesnav_staffing_qualifications q WHERE q.run_id=? AND q.sales_nav_id=o.sales_nav_id)",
        [runId, runId],
      ),
      kept: count(
        "SELECT count(*) n FROM salesnav_staffing_qualifications WHERE run_id=? AND fit='kept'",
      ),
      dropped: count(
        "SELECT count(*) n FROM salesnav_staffing_qualifications WHERE run_id=? AND fit='dropped'",
      ),
    };
  }
}
