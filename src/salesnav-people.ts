import type { Database } from "bun:sqlite";
import { randomUUID } from "node:crypto";
import { CliError } from "./core/errors.ts";
import { inTransaction } from "./db/database.ts";
import type { SalesNavInput } from "./salesnav.ts";

export const PEOPLE_PARSER_VERSION = "salesnav-account-people-v1";
const MAX_BODY_BYTES = 2_000_000;
const ROLE_TERMS = {
  staffing: ["technical recruiter", "recruiter", "account manager", "practice leader"],
  studio: [
    "owner",
    "co-founder",
    "founder",
    "ceo",
    "chief executive officer",
    "president",
    "cto",
    "chief technology officer",
    "coo",
    "chief operating officer",
    "cpo",
    "chief product officer",
    "vp",
    "vice president",
    "director",
  ],
} as const;
const FUNCTION_TERMS = {
  staffing: ["software", "engineering", "product", "technical"],
  studio: ["engineering", "product", "operations", "technology", "software"],
} as const;
type Row = Record<string, unknown>;
const fail = (code: string, message: string): never => {
  throw new CliError(code, message, { exitCode: 2 });
};
const obj = (v: unknown): Row =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : {};
const text = (v: unknown): string => (typeof v === "string" ? v.replace(/\s+/g, " ").trim() : "");
const canonical = (v: unknown): string => JSON.stringify(v ?? {}) ?? "{}";
const roleMatch = (title: string, term: string): boolean => {
  if (term === "owner") return title.includes("owner") && !title.includes("product owner");
  if (term === "president") return title.includes("president") && !title.includes("vice president");
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\b${escaped}\\b`, "i").test(title);
};
const idFrom = (v: unknown): string =>
  text(v).match(
    /(?:fs_salesProfile|salesLead|sales_profile|lead)[:/]?(?:\(|)([A-Za-z0-9_-]+)/i,
  )?.[1] ?? "";
const parse = (body: string) => {
  if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES)
    fail("SALESNAV_BODY_TOO_LARGE", "capture body exceeds 2 MB");
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch {
    return fail("SALESNAV_INVALID_BODY", "capture body must be valid JSON");
  }
  const row = obj(value),
    paging = obj(row.paging);
  if (
    !Array.isArray(row.elements) ||
    !Number.isInteger(paging.start) ||
    !Number.isInteger(paging.count) ||
    !Number.isInteger(paging.total) ||
    Number(paging.start) < 0 ||
    Number(paging.count) > 1000 ||
    Number(paging.total) > 1_000_000
  )
    fail("SALESNAV_INVALID_SCHEMA", "body requires bounded elements and paging");
  return { row, paging };
};
const decoded = (value: string): string => {
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
export const currentCompanyIds = (value: string): string[] => {
  const query = decoded(value),
    match = query.match(/type:CURRENT_COMPANY,values:List\((.*?)\)\)\)/);
  return match?.[1]
    ? [...match[1].matchAll(/\bid:([^,)]+)/g)].flatMap((item) =>
        item[1] ? [item[1].trim().split(":").at(-1) ?? ""] : [],
      )
    : [];
};
export const isAccountPeopleSearchUrl = (value: string, accountId: string): boolean => {
  try {
    const u = new URL(value);
    return (
      u.protocol === "https:" &&
      u.hostname === "www.linkedin.com" &&
      u.pathname === "/sales/search/people" &&
      currentCompanyIds(u.searchParams.get("query") ?? "").length === 1 &&
      currentCompanyIds(u.searchParams.get("query") ?? "")[0] === accountId
    );
  } catch {
    return false;
  }
};
const url = (value: string, start: number, accountId: string): boolean => {
  try {
    const u = new URL(value),
      ids = currentCompanyIds(u.searchParams.get("query") ?? "");
    return (
      u.protocol === "https:" &&
      u.hostname === "www.linkedin.com" &&
      u.pathname === "/sales-api/salesApiLeadSearch" &&
      u.searchParams.get("q") === "searchQuery" &&
      u.searchParams.get("count") === "25" &&
      Number(u.searchParams.get("start")) === start &&
      ids.length === 1 &&
      ids[0] === accountId
    );
  } catch {
    return false;
  }
};
export function isAccountPeopleResponseUrl(
  value: string,
  start: number,
  accountId: string,
): boolean {
  return url(value, start, accountId);
}

export class SalesNavPeopleStore {
  constructor(private readonly db: Database) {}
  private run(id: string): Row {
    const row = this.db
      .query<Row, [string]>("SELECT * FROM salesnav_account_people_runs WHERE id=?")
      .get(id);
    if (!row) fail("SALESNAV_RUN_NOT_FOUND", `people run ${id} does not exist`);
    return row as Row;
  }
  start(input: SalesNavInput, now: string): Row {
    const accountRun =
      this.db
        .query<Row, [string]>("SELECT * FROM salesnav_account_runs WHERE id=?")
        .get(input.accountRunId ?? "") ??
      fail("SALESNAV_RUN_NOT_FOUND", "account run does not exist");
    const organizationId =
      input.organizationId ?? fail("INVALID_ARGUMENT", "--organization-id is required");
    const account =
      this.db
        .query<Row, [string, string]>(
          "SELECT account_id,organization_id FROM salesnav_account_observations WHERE run_id=? AND organization_id=? LIMIT 1",
        )
        .get(String(accountRun.id), organizationId) ??
      fail("SALESNAV_ACCOUNT_NOT_KEPT", "account was not observed in the account run");
    const lane = String(accountRun.lane ?? input.lane);
    if (
      !this.db
        .query<Row, [string, string]>(
          "SELECT organization_id FROM salesnav_account_lane_qualifications WHERE lane=? AND organization_id=? AND fit='kept'",
        )
        .get(lane, organizationId)
    )
      fail(
        "SALESNAV_ACCOUNT_NOT_KEPT",
        "people capture requires a kept account in the account run",
      );
    const sourceUrl = input.sourceUrl ?? fail("INVALID_ARGUMENT", "--source-url is required");
    if (!isAccountPeopleSearchUrl(sourceUrl, String(account.account_id)))
      fail("SALESNAV_WRONG_SEARCH", "people source URL must be company-scoped to the kept account");
    const existing = this.db
      .query<Row, [string]>("SELECT * FROM salesnav_account_people_runs WHERE id=?")
      .get(input.runId);
    if (existing) {
      if (existing.source_url !== sourceUrl || existing.organization_id !== organizationId)
        fail("SALESNAV_RUN_CONFLICT", "people run metadata conflicts");
      return existing;
    }
    this.db
      .prepare(
        "INSERT INTO salesnav_account_people_runs(id,account_run_id,lane,organization_id,account_id,source_url,started_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
      )
      .run(
        input.runId,
        String(accountRun.id),
        lane,
        organizationId,
        String(account.account_id),
        sourceUrl,
        now,
        now,
      );
    return this.run(input.runId);
  }
  ingest(input: SalesNavInput, body: string, now: string) {
    const run = this.run(input.runId),
      start = input.start ?? -1,
      responseUrl = input.responseUrl ?? "";
    if (run.state !== "active")
      fail("SALESNAV_RUN_NOT_ACTIVE", `people run ${input.runId} is ${run.state}`);
    if (input.sourceUrl !== run.source_url || !url(responseUrl, start, String(run.account_id)))
      fail("SALESNAV_WRONG_RESPONSE", "response URL must be company-scoped to the kept account");
    const { paging } = parse(body);
    if (Number(paging.start) !== start || Number(paging.count) !== 25)
      fail("SALESNAV_WRONG_START", "paging does not match --start or count 25");
    const old = this.db
      .query<Row, [string, number]>(
        "SELECT * FROM salesnav_account_people_pages WHERE run_id=? AND start=?",
      )
      .get(input.runId, start);
    if (old) {
      if (old.response_url !== responseUrl || old.payload_json !== body)
        fail("SALESNAV_PAGE_CONFLICT", "retry conflicts with captured page");
      return { inserted: false, start };
    }
    this.db
      .prepare(
        "INSERT INTO salesnav_account_people_pages(run_id,start,source_url,response_url,status,paging_count,paging_total,parser_version,payload_json,captured_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
      )
      .run(
        input.runId,
        start,
        String(input.sourceUrl),
        responseUrl,
        200,
        Number(paging.count),
        Number(paging.total),
        PEOPLE_PARSER_VERSION,
        body,
        input.capturedAt ?? now,
      );
    return { inserted: true, start, count: paging.count, total: paging.total };
  }
  finish(input: SalesNavInput, now: string): Row {
    const old = this.run(input.runId),
      state = input.state ?? fail("INVALID_ARGUMENT", "--state is required");
    if (old.state !== "active") return old;
    this.db
      .prepare(
        "UPDATE salesnav_account_people_runs SET state=?,error=?,completed_at=?,updated_at=? WHERE id=?",
      )
      .run(state, input.error ?? null, now, now, input.runId);
    return this.run(input.runId);
  }
  normalize(input: SalesNavInput, now: string) {
    const run = this.run(input.runId),
      pages = this.db
        .query<Row, [string, number]>(
          `SELECT p.* FROM salesnav_account_people_pages p
           WHERE p.run_id=? AND NOT EXISTS (
             SELECT 1 FROM salesnav_account_people_page_normalizations n
             WHERE n.run_id=p.run_id AND n.start=p.start
           ) ORDER BY p.start LIMIT ?`,
        )
        .all(input.runId, input.limit ?? 500);
    if (run.state !== "complete")
      fail("SALESNAV_RUN_NOT_COMPLETE", "finish the people capture before normalization");
    for (const page of pages)
      inTransaction(this.db, () => {
        const { row } = parse(String(page.payload_json));
        let observed = 0;
        for (const raw of row.elements as unknown[]) {
          const lead = obj(raw),
            id = idFrom(lead.entityUrn ?? lead.objectUrn ?? lead.urn);
          if (!id) continue;
          const positions = Array.isArray(lead.currentPositions) ? lead.currentPositions : [],
            position = obj(
              positions.find((candidate) => {
                const item = obj(candidate),
                  resolved = obj(item.companyUrnResolutionResult),
                  urn = text(item.companyUrn || resolved.entityUrn);
                return (
                  urn.match(/fs_salesCompany:([A-Za-z0-9_-]+)/)?.[1] === String(run.account_id)
                );
              }),
            );
          if (!Object.keys(position).length) continue;
          const name = text(lead.fullName),
            title = text(position.title),
            company = text(position.companyName),
            evidence = canonical(lead);
          let person = this.db
            .query<Row, [string]>("SELECT * FROM people WHERE sales_nav_id=?")
            .get(id);
          if (!person) {
            const pid = randomUUID();
            this.db
              .prepare(
                "INSERT INTO people(id,sales_nav_id,name,created_at,updated_at) VALUES(?,?,?,?,?)",
              )
              .run(pid, id, name || id, now, now);
            person = { id: pid };
          }
          this.db
            .prepare(
              "INSERT INTO salesnav_account_people(sales_nav_id,person_id,organization_id,lead_url,full_name,current_title,current_company,current_description,geo_region,summary,source_evidence_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sales_nav_id) DO UPDATE SET person_id=excluded.person_id,organization_id=excluded.organization_id,full_name=excluded.full_name,current_title=excluded.current_title,current_company=excluded.current_company,current_description=excluded.current_description,geo_region=excluded.geo_region,summary=excluded.summary,source_evidence_json=excluded.source_evidence_json,updated_at=excluded.updated_at",
            )
            .run(
              id,
              String(person.id),
              String(run.organization_id),
              `https://www.linkedin.com/sales/lead/${id}`,
              name,
              title,
              company,
              text(position.description),
              text(lead.geoRegion),
              text(lead.summary),
              evidence,
              now,
            );
          this.db
            .prepare(
              "INSERT OR IGNORE INTO salesnav_account_people_observations(run_id,start,sales_nav_id,observed_at,source_evidence_json) VALUES(?,?,?,?,?)",
            )
            .run(input.runId, Number(page.start), id, now, evidence);
          observed++;
        }
        this.db
          .prepare(
            "INSERT INTO salesnav_account_people_page_normalizations(run_id,start,normalized_at,observed_count) VALUES(?,?,?,?)",
          )
          .run(input.runId, Number(page.start), now, observed);
      });
    return {
      runId: input.runId,
      normalizedPages: pages.length,
      selected: this.select(input.runId, String(run.lane)),
    };
  }
  select(runId: string, lane: string) {
    const rows = this.db
      .query<Row, [string]>(
        `SELECT p.*,o.start FROM salesnav_account_people p
         JOIN salesnav_account_people_observations o ON o.sales_nav_id=p.sales_nav_id
         JOIN salesnav_account_people_runs r ON r.id=o.run_id AND r.organization_id=p.organization_id
         WHERE o.run_id=? ORDER BY o.start,p.sales_nav_id`,
      )
      .all(runId);
    const selectedLane = lane === "studio" ? "studio" : "staffing",
      terms = ROLE_TERMS[selectedLane],
      functions = FUNCTION_TERMS[selectedLane];
    const ranked = rows
      .map((r) => {
        const title = String(r.current_title).toLowerCase(),
          roleEvidence =
            selectedLane === "staffing"
              ? `${title} ${String(r.current_description).toLowerCase()}`
              : title,
          functionEvidence = `${title} ${r.current_description} ${r.summary}`.toLowerCase(),
          role = terms.find((term) => roleMatch(roleEvidence, term));
        const fn = functions.find((term) => functionEvidence.includes(term)),
          roleIndex = role ? [...terms].indexOf(role) : Number.MAX_SAFE_INTEGER;
        return { r, role: role ?? "", fn: fn ?? "", roleIndex, score: role ? (fn ? 2 : 1) : 0 };
      })
      .filter((x) => x.score > 0)
      .sort(
        (a, b) =>
          a.roleIndex - b.roleIndex ||
          b.score - a.score ||
          String(a.r.current_title).localeCompare(String(b.r.current_title)) ||
          String(a.r.sales_nav_id).localeCompare(String(b.r.sales_nav_id)),
      );
    this.db.prepare("DELETE FROM salesnav_account_people_selections WHERE run_id=?").run(runId);
    const out = ranked.slice(0, 2);
    for (const [i, x] of out.entries())
      this.db
        .prepare(
          "INSERT INTO salesnav_account_people_selections(run_id,person_id,slot,rank,matched_role,reason) VALUES(?,?,?,?,?,?)",
        )
        .run(
          runId,
          String(x.r.person_id),
          i === 0 ? "primary" : "backup",
          i + 1,
          x.role,
          `matched ${x.role}`,
        );
    return out.map((x, i) => ({
      ...x.r,
      slot: i === 0 ? "primary" : "backup",
      matchedRole: x.role,
    }));
  }
  next(runId: string) {
    const run = this.run(runId);
    if (run.state !== "complete")
      fail("SALESNAV_RUN_NOT_COMPLETE", "finish the people capture before selecting people");
    return { runId, lane: run.lane, people: this.select(runId, String(run.lane)) };
  }
  review(input: SalesNavInput, now: string) {
    const review = input.review ?? fail("INVALID_ARGUMENT", "--review is required");
    if (!["needs_review", "approved", "rejected"].includes(review))
      fail("INVALID_ARGUMENT", "--review must be needs_review, approved, or rejected");
    const run = this.run(input.runId),
      personId = input.personId ?? fail("INVALID_ARGUMENT", "--person-id is required");
    if (
      !this.db
        .query<Row, [string, string]>(
          "SELECT 1 FROM salesnav_account_people_selections WHERE run_id=? AND person_id=?",
        )
        .get(input.runId, personId)
    )
      fail("SALESNAV_PERSON_NOT_SELECTED", "person is not selected for this run");
    const evidence = input.evidenceJson ?? "{}";
    JSON.parse(evidence);
    if (
      review === "approved" &&
      this.db
        .query<Row, [string, string]>(
          "SELECT 1 FROM salesnav_account_people_reviews WHERE run_id=? AND review='approved' AND person_id<>?",
        )
        .get(input.runId, personId)
    )
      fail("SALESNAV_APPROVAL_CONFLICT", "only one person may be approved for a firm");
    this.db
      .prepare(
        "INSERT INTO salesnav_account_people_reviews(run_id,person_id,review,evidence_json,reviewed_at) VALUES(?,?,?,?,?) ON CONFLICT(run_id,person_id) DO UPDATE SET review=excluded.review,evidence_json=excluded.evidence_json,reviewed_at=excluded.reviewed_at",
      )
      .run(input.runId, personId, review, evidence, now);
    return { runId: run.id, personId, review };
  }
}
