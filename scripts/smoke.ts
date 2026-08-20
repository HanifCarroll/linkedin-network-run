#!/usr/bin/env bun

import { createHash } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { type CliIo, run } from "../src/cli.ts";
import { parseInvocation } from "../src/commands/arguments.ts";
import {
  jobsApplicationNext,
  jobsDraftNext,
  jobsEnrichNext,
  jobsEnrichRecord,
  jobsSendPrepare,
} from "../src/commands/jobs.ts";
import type { CliOperations } from "../src/commands/types.ts";
import { CliError } from "../src/core/errors.ts";
import { openDatabase } from "../src/db/database.ts";
import { recordChromeSend } from "../src/jobs/chrome-send.ts";
import { prepareContractOutreach, recordContractOutreach } from "../src/jobs/contract-outreach.ts";
import type { JobRow } from "../src/jobs/index.ts";
import {
  filterRun,
  HubSpotImportEngine,
  InstantlyHandoffEngine,
  JobsEngine,
  JobsNormalizer,
  normalizeProfileUrl,
  prospectIdForProfile,
} from "../src/jobs/index.ts";
import { SalesNavStore } from "../src/salesnav.ts";
import {
  isAccountResponseUrl,
  isAccountSearchUrl,
  SalesNavAccountStore,
  STUDIO_SAVED_SEARCH_ID,
} from "../src/salesnav-account.ts";
import {
  isAccountPeopleResponseUrl,
  isAccountPeopleSearchUrl,
  SalesNavPeopleStore,
} from "../src/salesnav-people.ts";
import {
  bucketFor,
  draftActionFor,
  groupJobs,
  groupOutreachKind,
  groupSearchHaystack,
  outreachKindFor,
  outreachKindLabel,
  primaryRoleFor,
  sectionCounts,
  showsApplicationReminder,
  visibleGroups,
} from "../src/view/grouping.ts";

const root = await mkdtemp(join(tmpdir(), "linkedin-tools-smoke-"));
const fakePlaywriter = join(root, "playwriter");
await writeFile(
  fakePlaywriter,
  `#!/usr/bin/env bun
const args=process.argv.slice(2);
if(args[0]==="--version"){console.log("playwriter/0.4.0");process.exit(0)}
if(args[0]==="session"&&args[1]==="list"){console.log("7  network\\n8  analytics");process.exit(0)}
console.error("unexpected fake Playwriter command");process.exit(2)
`,
);
await chmod(fakePlaywriter, 0o755);

try {
  // Focused Sales Navigator intake coverage: fresh migration 17 -> 18, strict parsing,
  // idempotency/conflicts, fs_salesProfile normalization, dedupe, qualification, status,
  // and a fake CDP responseReceived/loadingFinished split across batches.
  {
    const sourceUrl = "https://www.linkedin.com/sales/search/people?savedSearchId=2006360906";
    const responseUrl =
      "https://www.linkedin.com/sales-api/salesApiLeadSearch?q=savedSearchId&start=0&count=25&savedSearchId=2006360906&decorationId=ListCard";
    const db = openDatabase(join(root, "salesnav.db"));
    try {
      if (!db.migrations.applied.includes("salesnav_staffing_schema_v2"))
        throw new Error("Sales Nav migration 18 missing");
      const store = new SalesNavStore(db.database);
      const base = {
        command: "capture-start" as const,
        stateDir: root,
        runId: "sn-run",
        sourceUrl,
      };
      store.start(base, "t0");
      const lead = {
        entityUrn: "urn:li:fs_salesProfile:(abc123,NAME_SEARCH,foo)",
        fullName: "A Person",
        geoRegion: "Ireland",
        degree: "2nd",
        currentPositions: [
          {
            title: "Technical Recruiter",
            companyName: "Acme",
            companyUrn: "",
            description: "Software engineering hiring",
            tenureAtPosition: { numYears: 1, numMonths: 2 },
          },
        ],
        summary: "product recruiting",
        spotlightBadges: [
          { displayValue: "Recently promoted" },
          { displayValues: ["Viewed your profile"] },
        ],
      };
      const body = JSON.stringify({ elements: [lead], paging: { start: 0, count: 1, total: 1 } });
      const ingest = {
        command: "capture-ingest" as const,
        stateDir: root,
        runId: "sn-run",
        start: 0,
        payloadPath: "-",
        sourceUrl,
        responseUrl,
      };
      store.ingest(ingest, body, "t1");
      if (store.ingest(ingest, body, "t2").inserted)
        throw new Error("Sales Nav retry was not idempotent");
      let conflict = false;
      try {
        store.ingest(ingest, body.replace("Acme", "Other"), "t2");
      } catch (error) {
        conflict = error instanceof CliError && error.code === "SALESNAV_PAGE_CONFLICT";
      }
      if (!conflict) throw new Error("Sales Nav page conflict was accepted");
      let wrongResponse = false;
      try {
        store.ingest(
          { ...ingest, responseUrl: responseUrl.replace("2006360906", "999") },
          body,
          "t2",
        );
      } catch (error) {
        wrongResponse = error instanceof CliError && error.code === "SALESNAV_WRONG_RESPONSE";
      }
      if (!wrongResponse) throw new Error("wrong Sales Nav response search was accepted");
      let wrongStart = false;
      try {
        store.ingest(
          { ...ingest, start: 2, responseUrl: responseUrl.replace("start=0", "start=2") },
          body,
          "t2",
        );
      } catch (error) {
        wrongStart = error instanceof CliError && error.code === "SALESNAV_WRONG_START";
      }
      if (!wrongStart) throw new Error("wrong Sales Nav response start was accepted");
      const second = JSON.stringify({
        elements: [
          lead,
          {
            ...lead,
            entityUrn: "urn:li:fs_salesProfile:(drop1,NAME_SEARCH,foo)",
            fullName: "No Tech",
            currentPositions: [
              {
                title: "Recruiter",
                companyName: "Acme",
                companyUrn: "",
                description: "People hiring",
                tenureAtPosition: {},
              },
            ],
            summary: "",
          },
        ],
        paging: { start: 1, count: 2, total: 2 },
      });
      store.ingest(
        { ...ingest, start: 1, responseUrl: responseUrl.replace("start=0", "start=1") },
        second,
        "t3",
      );
      store.normalize({ command: "normalize", stateDir: root, runId: "sn-run" }, "t4");
      const qualified = store.qualify(
        { command: "qualify", stateDir: root, runId: "sn-run", policyVersion: "policy-v1" },
        "t5",
      );
      if (qualified.kept !== 1 || qualified.dropped !== 1)
        throw new Error("Sales Nav qualification failed");
      const row = db.database
        .query("SELECT * FROM salesnav_staffing_leads WHERE sales_nav_id='abc123'")
        .get() as Record<string, unknown>;
      if (
        row.current_tenure !== "1y 2m" ||
        row.lead_url !== "https://www.linkedin.com/sales/lead/abc123" ||
        row.spotlight_json !== '["Recently promoted","Viewed your profile"]'
      )
        throw new Error("Sales Nav direct-position parser failed");
      if (Number((store.status("sn-run") as { organizations: number }).organizations) !== 1)
        throw new Error("Sales Nav organization fallback dedupe failed");
      const finished = {
        command: "capture-finish" as const,
        stateDir: root,
        runId: "sn-run",
        state: "complete" as const,
        checkpointJson: JSON.stringify({ checkpoint: "done" }),
      };
      store.finish(finished, "t6");
      store.finish(finished, "t7");
      let finishConflict = false;
      try {
        store.finish({ ...finished, error: "different" }, "t8");
      } catch (error) {
        finishConflict = error instanceof CliError && error.code === "SALESNAV_FINISH_CONFLICT";
      }
      if (!finishConflict) throw new Error("Sales Nav finish conflict was accepted");
      const parsed = parseInvocation(["salesnav", "staffing", "status", "--run-id", "x"], {
        now: new Date(),
        env: { HOME: process.env.HOME ?? root },
      });
      if (parsed.kind !== "command" || parsed.command !== "salesnav staffing status")
        throw new Error("Sales Nav dispatch parse failed");
      let strict = false;
      try {
        parseInvocation(["salesnav", "staffing", "status", "--run-id", "x", "--payload", "-"], {
          now: new Date(),
          env: { HOME: process.env.HOME ?? root },
        });
      } catch (error) {
        strict = error instanceof CliError;
      }
      if (!strict) throw new Error("Sales Nav irrelevant option was accepted");
    } finally {
      db.database.close();
    }

    const helperState = join(root, "salesnav-helper-state");
    const helperDb = openDatabase(join(helperState, "linkedin-tools.db"));
    helperDb.database.close();
    const helperRunDb = openDatabase(join(helperState, "linkedin-tools.db"));
    new SalesNavStore(helperRunDb.database).start(
      { command: "capture-start", stateDir: helperState, runId: "helper-run", sourceUrl },
      "t0",
    );
    helperRunDb.database.close();
    // @ts-expect-error smoke imports the caller-owned JS handoff helper directly.
    const { captureAndIngestSalesNavPage } = await import("./linkedin-salesnav-chrome-helper.mjs");
    let reads = 0;
    const fakeBody = JSON.stringify({ elements: [], paging: { start: 0, count: 0, total: 0 } });
    const fakeCdp = {
      send: async (method: string) =>
        method === "Network.getResponseBody" ? { body: fakeBody, base64Encoded: false } : undefined,
      readEvents: async () =>
        reads++ === 0
          ? { cursor: 1, events: [] }
          : reads === 2
            ? {
                cursor: 2,
                events: [
                  {
                    method: "Network.responseReceived",
                    params: { requestId: "r1", response: { url: responseUrl, status: 200 } },
                  },
                ],
              }
            : {
                cursor: 3,
                events: [{ method: "Network.loadingFinished", params: { requestId: "r1" } }],
              },
    };
    const helperResult = await captureAndIngestSalesNavPage(
      {
        url: async () => `${sourceUrl}&query=(filters%3AList())&sessionId=synthetic`,
        title: async () => "",
        capabilities: { get: async () => fakeCdp },
      },
      async () => {},
      { runId: "helper-run", start: 0, sourceUrl, stateDir: helperState },
    );
    if (!helperResult?.ok) throw new Error("Sales Nav fake CDP capture failed");
  }

  // Account-first staffing coverage: exact search/response contracts, additive
  // migration, normalization, organization-scoped decisions, canonical retries,
  // and preservation of the existing people-first sample in the same database.
  {
    const accountQuery =
      "(filters:List((type:COMPANY_HEADCOUNT,values:List((id:C),(id:D))),(type:INDUSTRY,values:List((id:104))),(type:REGION,values:List((id:103644278)))),keywords:software OR engineering OR product)";
    const sourceUrl = `https://www.linkedin.com/sales/search/company?query=${encodeURIComponent(encodeURIComponent(accountQuery))}`;
    const responseUrl = `https://www.linkedin.com/sales-api/salesApiAccountSearch?q=searchQuery&query=${encodeURIComponent(accountQuery)}&start=0&count=25&decorationId=com.linkedin.sales.deco.desktop.searchv2.AccountSearchResult-4`;
    if (!isAccountSearchUrl(sourceUrl) || !isAccountResponseUrl(responseUrl, 0)) {
      throw new Error("Sales Nav account URL contract rejected the supported search");
    }
    const extraSize = sourceUrl.replace(
      encodeURIComponent(encodeURIComponent("(id:D)")),
      encodeURIComponent(encodeURIComponent("(id:D),(id:E)")),
    );
    if (isAccountSearchUrl(extraSize)) {
      throw new Error("Sales Nav account search accepted an extra headcount filter");
    }

    const db = openDatabase(join(root, "salesnav.db"));
    try {
      if (db.migrations.currentVersion < 19) {
        throw new Error("Sales Nav account migration 19 missing");
      }
      const peopleCount = Number(
        (
          db.database.query("SELECT count(*) n FROM salesnav_staffing_leads").get() as {
            n: number;
          }
        ).n,
      );
      if (peopleCount !== 2) throw new Error("account migration changed the people-first sample");

      const store = new SalesNavAccountStore(db.database);
      const start = {
        command: "account-capture-start" as const,
        stateDir: root,
        runId: "account-run",
        sourceUrl,
        checkpointJson: '{"page":1}',
      };
      store.start(start, "a0");
      const body = JSON.stringify({
        metadata: {},
        elements: [
          {
            companyName: "Acme Staffing",
            description: "Software and product staffing",
            industry: "Staffing and Recruiting",
            employeeCountRange: { start: 11, end: 50 },
            employeeDisplayCount: "11-50 employees",
            entityUrn: "urn:li:fs_salesCompany:42",
            spotlightBadges: [],
            trackingId: "track-42",
          },
        ],
        paging: { start: 0, count: 25, total: 1 },
      });
      const ingest = {
        command: "account-capture-ingest" as const,
        stateDir: root,
        runId: "account-run",
        start: 0,
        payloadPath: "-",
        sourceUrl,
        responseUrl,
      };
      store.ingest(ingest, body, "a1");
      store.start(start, "a2");
      if (store.ingest(ingest, body, "a2").inserted) {
        throw new Error("Sales Nav account retry was not idempotent");
      }
      store.normalize({ command: "account-normalize", stateDir: root, runId: "account-run" }, "a3");
      const next = store.next({
        command: "account-qualify-next",
        stateDir: root,
        runId: "account-run",
      });
      if (!next) throw new Error("Sales Nav account qualification queue was empty");
      if (
        next.name !== "Acme Staffing" ||
        next.linkedin_company_url !== "https://www.linkedin.com/sales/company/42"
      ) {
        throw new Error("Sales Nav account qualification packet is incomplete");
      }
      const organizationId = String(next.organization_id);
      const decision = {
        command: "account-qualify-record" as const,
        stateDir: root,
        runId: "account-run",
        organizationId,
        fit: "kept" as const,
        evidenceJson: '{"website":"reviewed","linkedin":"reviewed"}',
        unknownsJson: '["ownership"]',
        reason: "Relevant technical staffing firm",
        policyVersion: "staffing-account-v1",
      };
      const recorded = store.record(decision, "a4");
      if (recorded.accountId !== "42")
        throw new Error("account decision stored the wrong account ID");
      store.record(
        { ...decision, evidenceJson: '{"linkedin":"reviewed", "website":"reviewed"}' },
        "a5",
      );
      if (store.next({ ...decision, command: "account-qualify-next" }) !== null) {
        throw new Error("qualified organization remained in the account queue");
      }
      const org = db.database
        .query("SELECT * FROM organizations WHERE id=?")
        .get(organizationId) as Record<string, unknown>;
      if (org.linkedin_company_url !== "https://www.linkedin.com/sales/company/42") {
        throw new Error("Sales Nav account normalization stored the wrong company URL");
      }
      const qualification = db.database
        .query(
          "SELECT account_id,evidence_json FROM salesnav_account_qualifications WHERE organization_id=?",
        )
        .get(organizationId) as Record<string, unknown>;
      if (
        qualification.account_id !== "42" ||
        qualification.evidence_json !== '{"linkedin":"reviewed","website":"reviewed"}'
      ) {
        throw new Error("Sales Nav account qualification was not stored canonically");
      }
      const status = store.status("account-run");
      if (status.accounts !== 1 || status.pending !== 0 || status.kept !== 1) {
        throw new Error("Sales Nav account status counts are wrong");
      }
      const finish = {
        command: "account-capture-finish" as const,
        stateDir: root,
        runId: "account-run",
        state: "complete" as const,
        checkpointJson: '{"done":true}',
      };
      store.finish(finish, "a6");
      store.finish(finish, "a7");

      const studioKeyword =
        '("product studio" OR "digital product" OR "custom software" OR "web application") NOT (staffing OR recruiting OR "digital marketing")';
      const studioQuery = `(filters:List((type:COMPANY_HEADCOUNT,values:List((id:C))),(type:INDUSTRY,values:List((id:96),(id:99))),(type:REGION,values:List((id:103644278)))),keywords:${studioKeyword})`;
      const studioUrl = `https://www.linkedin.com/sales/search/company?savedSearchId=${STUDIO_SAVED_SEARCH_ID}`;
      const studioResponseUrl = `https://www.linkedin.com/sales-api/salesApiAccountSearch?q=searchQuery&query=${encodeURIComponent(studioQuery)}&start=0&count=25&decorationId=com.linkedin.sales.deco.desktop.searchv2.AccountSearchResult-4`;
      const staleStudioQuery = studioQuery.replace("(id:99)", "(id:7)");
      const staleStudioUrl = `https://www.linkedin.com/sales/search/company?query=${encodeURIComponent(encodeURIComponent(staleStudioQuery))}`;
      if (isAccountSearchUrl(staleStudioUrl)) {
        throw new Error("Sales Nav studio search accepted the retired Design Services ID");
      }
      const studioCliState = join(root, "studio-cli-state");
      const studioCliStart = await invoke(
        [
          "--json",
          "salesnav",
          "studio",
          "account-capture-start",
          "--run-id",
          "studio-cli-run",
          "--source-url",
          studioUrl,
          "--keyword-query",
          studioKeyword,
          "--state-dir",
          studioCliState,
        ],
        undefined,
      );
      const studioCliStartData = (studioCliStart.value as { data?: Record<string, unknown> }).data;
      if (
        studioCliStart.exitCode !== 0 ||
        studioCliStart.value.ok !== true ||
        studioCliStartData?.command !== "salesnav studio account-capture-start"
      ) {
        throw new Error("studio account start did not reach the CLI operation");
      }
      const { captureAndIngestSalesNavAccountPage } = await import(
        // @ts-expect-error smoke imports the caller-owned JS handoff helper directly.
        "./linkedin-salesnav-account-chrome-helper.mjs"
      );
      let accountReads = 0;
      let accountTabUrl = "https://www.linkedin.com/sales/home";
      const accountBody = JSON.stringify({
        metadata: {},
        elements: [],
        paging: { start: 0, count: 25, total: 0 },
      });
      let cursorReadMethods: string[] | undefined;
      const accountCdp = {
        send: async (method: string) =>
          method === "Fetch.getResponseBody"
            ? { body: accountBody, base64Encoded: false }
            : undefined,
        readEvents: async (options?: { methods?: string[] }) => {
          if (accountReads === 0) cursorReadMethods = options?.methods;
          return accountReads++ === 0
            ? { cursor: 1, events: [] }
            : {
                cursor: 2,
                events: [
                  {
                    method: "Fetch.requestPaused",
                    params: {
                      requestId: "account-r1",
                      request: { url: studioResponseUrl },
                      responseStatusCode: 200,
                    },
                  },
                ],
              };
        },
      };
      const accountHelperResult = await captureAndIngestSalesNavAccountPage(
        {
          url: async () => accountTabUrl,
          capabilities: { get: async () => accountCdp },
        },
        async () => {
          accountTabUrl = studioUrl;
        },
        {
          runId: "studio-cli-run",
          start: 0,
          sourceUrl: studioUrl,
          stateDir: studioCliState,
          lane: "studio",
          keywordQuery: studioKeyword,
        },
      );
      if (!accountHelperResult?.ok) {
        throw new Error("Sales Nav account helper rejected the home-to-saved-search handoff");
      }
      if (cursorReadMethods?.join(",") !== "Fetch.requestPaused") {
        throw new Error("Sales Nav account helper cursor included unrelated network events");
      }
      const emptyPeopleBoundary = await invoke(
        [
          "--json",
          "salesnav",
          "studio",
          "account-people-candidates",
          "--run-id",
          "studio-cli-run",
          "--state-dir",
          studioCliState,
        ],
        undefined,
      );
      const emptyPeopleData = (emptyPeopleBoundary.value as { data?: { accounts?: unknown[] } })
        .data;
      if (
        emptyPeopleBoundary.exitCode !== 0 ||
        emptyPeopleBoundary.value.ok !== true ||
        emptyPeopleData?.accounts?.length !== 0
      ) {
        throw new Error("studio people boundary did not reach the CLI operation");
      }
      const studioStart = {
        command: "account-capture-start" as const,
        lane: "studio" as const,
        stateDir: root,
        runId: "studio-run",
        sourceUrl: studioUrl,
        keywordQuery: studioKeyword,
      };
      store.start(studioStart, "s0");
      const studioBody = JSON.stringify({
        metadata: {},
        elements: [
          {
            companyName: "Acme Product Studio",
            description: "Product design and custom software delivery",
            industry: "IT Services and IT Consulting",
            employeeCountRange: { start: 11, end: 50 },
            employeeDisplayCount: "11-50 employees",
            entityUrn: "urn:li:fs_salesCompany:142",
            spotlightBadges: [],
            trackingId: "track-142",
          },
          {
            companyName: "Acme Marketing Studio",
            description: "Brand campaigns and social media",
            industry: "Design Services",
            employeeCountRange: { start: 11, end: 50 },
            employeeDisplayCount: "11-50 employees",
            entityUrn: "urn:li:fs_salesCompany:143",
            spotlightBadges: [],
            trackingId: "track-143",
          },
        ],
        paging: { start: 0, count: 25, total: 2 },
      });
      let conflict = false;
      try {
        store.start({ ...studioStart, lane: "staffing" }, "s1");
      } catch (error) {
        conflict = error instanceof CliError && error.code === "SALESNAV_WRONG_SEARCH";
      }
      if (!conflict) throw new Error("studio/staffing lane conflict was accepted");
      let wrongResponseRejected = false;
      try {
        store.ingest(
          {
            command: "account-capture-ingest",
            lane: "studio",
            stateDir: root,
            runId: "studio-run",
            start: 0,
            payloadPath: "-",
            sourceUrl: studioUrl,
            responseUrl,
          },
          studioBody,
          "s1",
        );
      } catch (error) {
        wrongResponseRejected =
          error instanceof CliError && error.code === "SALESNAV_WRONG_RESPONSE";
      }
      if (!wrongResponseRejected) {
        throw new Error("studio ingest accepted a staffing account-search response");
      }
      store.ingest(
        {
          command: "account-capture-ingest",
          lane: "studio",
          stateDir: root,
          runId: "studio-run",
          start: 0,
          payloadPath: "-",
          sourceUrl: studioUrl,
          responseUrl: studioResponseUrl,
        },
        studioBody,
        "s1",
      );
      store.normalize(
        { command: "account-normalize", lane: "studio", stateDir: root, runId: "studio-run" },
        "s2",
      );
      const studioNext = store.next({
        command: "account-qualify-next",
        lane: "studio",
        stateDir: root,
        runId: "studio-run",
      });
      if (!studioNext) throw new Error("studio qualification queue was empty");
      let researchRequired = false;
      try {
        store.record(
          {
            command: "account-qualify-record",
            lane: "studio",
            stateDir: root,
            runId: "studio-run",
            organizationId: String(studioNext.organization_id),
            fit: "kept",
            evidenceJson: "{}",
            unknownsJson: "[]",
            reason: "fit",
            policyVersion: "studio-v1",
          },
          "s3",
        );
      } catch (error) {
        researchRequired =
          error instanceof CliError && error.code === "SALESNAV_FIRM_RESEARCH_REQUIRED";
      }
      if (!researchRequired) throw new Error("studio keep did not require firm research");
      store.firmResearch(
        {
          command: "firm-research-record",
          lane: "studio",
          stateDir: root,
          runId: "studio-run",
          organizationId: String(studioNext.organization_id),
          sourceUrlsJson: '["https://www.linkedin.com/company/acme","https://acme.example"]',
          services: "Product design and custom software",
          concreteFact: "Built a customer portal",
          unknownsJson: '["team size"]',
          reviewedAt: "2026-08-19T00:00:00Z",
        },
        "s4",
      );
      store.record(
        {
          command: "account-qualify-record",
          lane: "studio",
          stateDir: root,
          runId: "studio-run",
          organizationId: String(studioNext.organization_id),
          fit: "kept",
          evidenceJson: "{}",
          unknownsJson: "[]",
          reason: "fit",
          policyVersion: "studio-v1",
        },
        "s5",
      );
      const studioDropped = store.next({
        command: "account-qualify-next",
        lane: "studio",
        stateDir: root,
        runId: "studio-run",
      });
      if (!studioDropped || studioDropped.account_id !== "143") {
        throw new Error("studio dropped account was not next in the qualification queue");
      }
      store.record(
        {
          command: "account-qualify-record",
          lane: "studio",
          stateDir: root,
          runId: "studio-run",
          organizationId: String(studioDropped.organization_id),
          fit: "dropped",
          evidenceJson: '{"linkedin":"reviewed","website":"reviewed"}',
          unknownsJson: "[]",
          reason: "Marketing only",
          policyVersion: "studio-v1",
        },
        "s6",
      );
      const studioCandidates = store.peopleCandidates({
        command: "account-people-candidates",
        lane: "studio",
        stateDir: root,
        runId: "studio-run",
      });
      if (
        studioCandidates.accounts.length !== 1 ||
        studioCandidates.accounts[0]?.account_id !== "142"
      ) {
        throw new Error("studio people boundary did not return only kept accounts");
      }
      const studioStatus = store.status("studio-run");
      if (
        studioStatus.accounts !== 2 ||
        studioStatus.pending !== 0 ||
        studioStatus.kept !== 1 ||
        studioStatus.dropped !== 1
      )
        throw new Error("studio qualification was not lane-scoped");
    } finally {
      db.database.close();
    }
  }

  // Company-scoped people capture: exact URL boundary, employer verification,
  // idempotent normalization, stable ranking, and one approval per firm.
  {
    const opened = openDatabase(join(root, "salesnav-people.db"));
    try {
      const database = opened.database,
        accountId = "3544281",
        organizationId = "org-people",
        accountRunId = "account-run-people",
        peopleRunId = "people-run",
        query = `(filters:List((type:CURRENT_COMPANY,values:List((id:${accountId},selectionType:INCLUDED)))))`,
        sourceUrl = `https://www.linkedin.com/sales/search/people?query=${encodeURIComponent(query)}`,
        responseUrl = `https://www.linkedin.com/sales-api/salesApiLeadSearch?q=searchQuery&query=${encodeURIComponent(query)}&start=0&count=25`,
        wrongQuery = `(filters:List((type:CURRENT_COMPANY,values:List((id:999,selectionType:INCLUDED)))),keywords:${accountId})`,
        wrongUrl = `https://www.linkedin.com/sales/search/people?query=${encodeURIComponent(wrongQuery)}`,
        urnQuery = `(filters:List((type:CURRENT_COMPANY,values:List((id:urn%3Ali%3Aorganization%3A${accountId},selectionType:INCLUDED)))))`,
        urnUrl = `https://www.linkedin.com/sales/search/people?query=${encodeURIComponent(urnQuery)}`;
      if (
        !isAccountPeopleSearchUrl(sourceUrl, accountId) ||
        !isAccountPeopleResponseUrl(responseUrl, 0, accountId)
      )
        throw new Error("company-scoped people URL was rejected");
      if (isAccountPeopleSearchUrl(wrongUrl, accountId))
        throw new Error("people source accepted an account ID outside CURRENT_COMPANY");
      if (!isAccountPeopleSearchUrl(urnUrl, accountId))
        throw new Error("people source rejected Sales Navigator's organization URN filter");
      database
        .prepare(
          "INSERT INTO organizations(id,dedupe_key,normalized_name,name,linkedin_company_url,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,'{}',?,?)",
        )
        .run(
          organizationId,
          "people-co",
          "people co",
          "People Co",
          `https://www.linkedin.com/sales/company/${accountId}`,
          "t0",
          "t0",
        );
      database
        .prepare(
          "INSERT INTO salesnav_account_runs(id,source_url,state,checkpoint_json,started_at,updated_at,lane,search_config_json,keyword_query) VALUES(?,?,'complete','{}','t0','t0','staffing','{}','software')",
        )
        .run(accountRunId, "https://www.linkedin.com/sales/search/company");
      database
        .prepare(
          "INSERT INTO salesnav_account_observations(run_id,start,account_id,organization_id,observed_at,source_evidence_json) VALUES(?,0,?,?,?,'{}')",
        )
        .run(accountRunId, accountId, organizationId, "t0");
      database
        .prepare(
          "INSERT INTO salesnav_account_lane_qualifications(lane,organization_id,source_run_id,account_id,fit,evidence_json,unknowns_json,reason,policy_version,filtered_at) VALUES('staffing',?,?,?,'kept','{}','[]','relevant firm','v1','t0')",
        )
        .run(organizationId, accountRunId, accountId);
      const store = new SalesNavPeopleStore(database);
      store.start(
        {
          command: "account-people-capture-start",
          lane: "staffing",
          stateDir: root,
          runId: peopleRunId,
          accountRunId,
          organizationId,
          sourceUrl,
        },
        "t1",
      );
      const body = JSON.stringify({
        paging: { start: 0, count: 25, total: 2 },
        elements: [
          {
            entityUrn: "urn:li:fs_salesProfile:(right,NAME_SEARCH,x)",
            fullName: "Right Recruiter",
            geoRegion: "United States",
            currentPositions: [
              {
                title: "Technical Recruiter",
                companyName: "People Co",
                companyUrn: `urn:li:fs_salesCompany:${accountId}`,
                description: "Software and engineering recruiting",
              },
            ],
          },
          {
            entityUrn: "urn:li:fs_salesProfile:(wrong,NAME_SEARCH,x)",
            fullName: "Wrong Recruiter",
            currentPositions: [
              {
                title: "Technical Recruiter",
                companyName: "Other Co",
                companyUrn: "urn:li:fs_salesCompany:999",
                description: "Software recruiting",
              },
            ],
          },
          {
            entityUrn: "urn:li:fs_salesProfile:(backup,NAME_SEARCH,x)",
            fullName: "Backup Recruiter",
            currentPositions: [
              {
                title: "Recruiter",
                companyName: "People Co",
                companyUrn: `urn:li:fs_salesCompany:${accountId}`,
                description: "Technical product recruiting",
              },
            ],
          },
        ],
      });
      store.ingest(
        {
          command: "account-people-capture-ingest",
          lane: "staffing",
          stateDir: root,
          runId: peopleRunId,
          start: 0,
          sourceUrl,
          responseUrl,
        },
        body,
        "t2",
      );
      store.finish(
        {
          command: "account-people-capture-finish",
          lane: "staffing",
          stateDir: root,
          runId: peopleRunId,
          state: "complete",
        },
        "t3",
      );
      const first = store.normalize(
          {
            command: "account-people-normalize",
            lane: "staffing",
            stateDir: root,
            runId: peopleRunId,
          },
          "t4",
        ),
        second = store.normalize(
          {
            command: "account-people-normalize",
            lane: "staffing",
            stateDir: root,
            runId: peopleRunId,
          },
          "t5",
        );
      if (
        first.normalizedPages !== 1 ||
        first.selected.length !== 2 ||
        second.normalizedPages !== 0
      )
        throw new Error("people normalization was not bounded and idempotent");
      if (database.query("SELECT 1 FROM salesnav_account_people WHERE sales_nav_id='wrong'").get())
        throw new Error("person at another company crossed the account boundary");
      const [primary, backup] = first.selected;
      if (!primary || !backup)
        throw new Error("people selection did not return primary and backup");
      const primaryId = String((primary as Record<string, unknown>).person_id),
        backupId = String((backup as Record<string, unknown>).person_id);
      store.review(
        {
          command: "account-people-review",
          lane: "staffing",
          stateDir: root,
          runId: peopleRunId,
          personId: primaryId,
          review: "approved",
          evidenceJson: "{}",
        },
        "t6",
      );
      let conflict = false;
      try {
        store.review(
          {
            command: "account-people-review",
            lane: "staffing",
            stateDir: root,
            runId: peopleRunId,
            personId: backupId,
            review: "approved",
            evidenceJson: "{}",
          },
          "t7",
        );
      } catch (error) {
        conflict = error instanceof CliError && error.code === "SALESNAV_APPROVAL_CONFLICT";
      }
      if (!conflict) throw new Error("two people were approved for one firm");
    } finally {
      opened.database.close();
    }
  }

  const doctorOutput = await invoke(
    [
      "--json",
      "doctor",
      "--state-dir",
      join(root, "state"),
      "--playwriter-bin",
      fakePlaywriter,
      "--network-session",
      "7",
      "--analytics-session",
      "8",
    ],
    undefined,
  );
  if (doctorOutput.exitCode !== 0 || doctorOutput.value?.ok !== true) {
    throw new Error("doctor smoke failed");
  }

  // Bounded temp-state check: migration 8 lands and a classify round-trips
  // through the engine into the row mapping (no browser, no live DB).
  {
    const opened = openDatabase(join(root, "classify.db"));
    try {
      if (!opened.migrations.applied.includes("jobs_classification_summaries")) {
        throw new Error("classification summaries migration (8) not applied");
      }
      const engine = new JobsEngine(opened.database);
      engine.storeCapturedJobs([{ id: "111", title: "Platform Engineer" }], "2026-08-03T00:00:00Z");
      const job = engine.classifyJob(
        "111",
        "  Infrastructure  ",
        " Salesforce ",
        "  Build the core platform  ",
        " Salesforce CPQ ",
        "2026-08-03T00:00:00Z",
      );
      if (
        job.workFocus !== "Infrastructure" ||
        job.productSystem !== "Salesforce" ||
        job.workSummary !== "Build the core platform" ||
        job.productSummary !== "Salesforce CPQ"
      ) {
        throw new Error("classification roundtrip smoke failed");
      }
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: triage is fit/review/team/detail/run gated,
  // records atomically, retries exactly, and conflicts without replacement.
  {
    const opened = openDatabase(join(root, "triage.db"));
    try {
      const engine = new JobsEngine(opened.database);
      engine.upsertJobs(
        [
          {
            id: "triage-1",
            title: "Product Engineer",
            company: "Acme",
            location: "Remote",
            postingUrl: "https://www.linkedin.com/jobs/view/triage-1/",
            hiringTeam: [
              {
                name: "HM",
                profileUrl: "https://www.linkedin.com/in/hm",
                degree: "2nd",
                headline: "VP",
              },
            ],
            hasHiringTeam: true,
          },
        ],
        "t0",
      );
      opened.database
        .prepare(
          "UPDATE jobs SET fit='kept', description='Build customer software', review='needs_review' WHERE id='triage-1'",
        )
        .run();
      opened.database.exec(
        "INSERT INTO capture_runs (id, source_url, started_at, updated_at) VALUES ('triage-run','x','t0','t0')",
      );
      opened.database.exec(
        "INSERT INTO job_observations (run_id,page_identity,job_id,observed_title,observed_at) VALUES ('triage-run','p','triage-1','Product Engineer','t0')",
      );
      if (
        engine.triageNext("triage-run")?.id !== "triage-1" ||
        engine.triageNext("other-run") !== null
      )
        throw new Error("triage next eligibility/run scope failed");
      const args = {
        id: "triage-1",
        bucket: "strong" as const,
        companySummary: "Acme makes customer software",
        workSummary: "Build customer software",
        responsibilities: ["Ship features"],
        skillMatches: ["TypeScript"],
        skillGaps: ["Unknown domain"],
        reason: "Explicit customer software and production ownership anchors",
        policyVersion: "jobs-triage-v1-20260819",
        now: "t1",
      };
      const first = engine.recordTriage(args);
      const retry = engine.recordTriage({ ...args, now: "t2" });
      if (first.triageBucket !== "strong" || JSON.stringify(first) !== JSON.stringify(retry))
        throw new Error("triage exact retry failed");
      let classifyConflict = false;
      try {
        engine.classifyJob(
          "triage-1",
          "Product",
          "Web application",
          "Replace the stored fit brief",
          "Customer software",
          "t2",
        );
      } catch (error) {
        classifyConflict = error instanceof CliError && error.code === "JOBS_TRIAGE_CONFLICT";
      }
      if (!classifyConflict) throw new Error("classification replaced a triaged fit brief");
      let conflict = false;
      try {
        engine.recordTriage({ ...args, bucket: "weak", now: "t3" });
      } catch (error) {
        conflict = error instanceof CliError && error.code === "JOBS_TRIAGE_CONFLICT";
      }
      if (!conflict) throw new Error("triage conflict failed");
      const eligibility = (id: string, update: string) => {
        engine.upsertJobs(
          [
            {
              id,
              title: id,
              company: "Acme",
              location: "Remote",
              postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
              hiringTeam: [
                {
                  name: "HM",
                  profileUrl: `https://www.linkedin.com/in/${id}`,
                  degree: "2nd",
                  headline: "VP",
                },
              ],
              hasHiringTeam: true,
            },
          ],
          "t0",
        );
        opened.database.prepare(`UPDATE jobs SET ${update} WHERE id=?`).run(id);
        let failed = false;
        try {
          engine.recordTriage({ ...args, id, now: "t4" });
        } catch (error) {
          failed = error instanceof CliError && error.code === "JOBS_TRIAGE_NOT_ELIGIBLE";
        }
        if (!failed) throw new Error(`triage eligibility failed for ${id}`);
      };
      eligibility("triage-pending", "fit='pending', description='x'");
      eligibility("triage-dropped", "fit='dropped', description='x'");
      eligibility("triage-no-description", "fit='kept', description=''");
      eligibility("triage-decided", "fit='kept', description='x', review='approved'");
      eligibility(
        "triage-no-team",
        "fit='kept', description='x', has_hiring_team=0, hiring_team_json='[]'",
      );
      if (
        !groupSearchHaystack([first]).includes("explicit customer") ||
        !groupSearchHaystack([first]).includes("strong")
      )
        throw new Error("triage search haystack failed");
      const ordered = ["triage-order-strong", "triage-order-weak"].map((id) => ({
        id,
        title: id,
        company: "Acme",
        location: "Remote",
        postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
        hiringTeam: [
          {
            name: id,
            profileUrl: `https://www.linkedin.com/in/${id}`,
            degree: "2nd",
            headline: "VP",
          },
        ],
        hasHiringTeam: true,
      }));
      engine.upsertJobs(ordered, "t0");
      for (const job of ordered)
        opened.database
          .prepare("UPDATE jobs SET fit='kept', description='x' WHERE id=?")
          .run(job.id);
      const strong = engine.recordTriage({ ...args, id: "triage-order-strong", now: "t5" });
      const weak = engine.recordTriage({
        ...args,
        id: "triage-order-weak",
        bucket: "weak",
        now: "t5",
      });
      const orderedGroups = groupJobs([weak, strong]);
      if (orderedGroups[0]?.jobs[0]?.id !== strong.id)
        throw new Error("displayed-primary triage ordering failed");
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: an approved person produces a stable HubSpot
  // handoff and partial receipts resume without duplicate local state.
  {
    const opened = openDatabase(join(root, "hubspot.db"));
    try {
      if (!opened.migrations.applied.includes("hubspot_imports")) {
        throw new Error("HubSpot imports migration (13) not applied");
      }
      const jobs = new JobsEngine(opened.database);
      jobs.upsertJobs(
        [
          {
            id: "hubspot-job",
            title: "Product Engineer",
            company: "Example Studio",
            location: "Remote",
            postingUrl: "https://www.linkedin.com/jobs/view/hubspot-job/",
            hiringTeam: [
              {
                name: "Alice Example",
                profileUrl: "https://www.linkedin.com/in/alice-example/?trk=jobs",
                degree: "2nd",
                headline: "Head of Engineering",
              },
            ],
            hasHiringTeam: true,
          },
        ],
        "2026-08-03T10:00:00Z",
      );
      opened.database
        .prepare(
          `UPDATE jobs SET fit = 'kept', review = 'approved', employment_type = 'Full-time',
           matched_term = 'product engineer', filter_reason = 'title matched'
           WHERE id = 'hubspot-job'`,
        )
        .run();
      const engine = new HubSpotImportEngine(opened.database);
      const first = engine.next("hubspot-job", "2026-08-03T10:01:00Z");
      const retry = engine.next("hubspot-job", "2026-08-03T10:02:00Z");
      if (first === null || retry === null || JSON.stringify(first) !== JSON.stringify(retry)) {
        throw new Error("HubSpot next was not stable on retry");
      }
      const prospectId = prospectIdForProfile(
        "https://www.linkedin.com/in/alice-example/?trk=jobs",
      );
      if (!JSON.stringify(first).includes(`${prospectId}:day-1`)) {
        throw new Error("HubSpot packet is missing the deterministic task marker");
      }
      const directPacket = JSON.stringify(first);
      if (
        !directPacket.includes("Offer short-term contract help") ||
        directPacket.includes("Submit application")
      ) {
        throw new Error("HubSpot direct route task mapping failed");
      }

      const contractJob = {
        id: "hubspot-contract-job",
        title: "Contract Product Engineer",
        company: "Contract Studio",
        location: "Remote",
        postingUrl: "https://www.linkedin.com/jobs/view/hubspot-contract-job/",
        hiringTeam: [
          {
            name: "Casey Contract",
            profileUrl: "https://www.linkedin.com/in/casey-contract/",
            degree: "2nd",
            headline: "Engineering Director",
          },
        ],
        hasHiringTeam: true,
      };
      jobs.upsertJobs([contractJob], "2026-08-03T10:00:00Z");
      opened.database
        .prepare(
          `UPDATE jobs SET fit = 'kept', review = 'approved', employment_type = 'Contract'
           WHERE id = 'hubspot-contract-job'`,
        )
        .run();
      const beforeApplication = engine.next("hubspot-contract-job", "2026-08-03T10:01:00Z");
      const beforeApplicationJson = JSON.stringify(beforeApplication);
      if (
        !beforeApplicationJson.includes("Submit application") ||
        !beforeApplicationJson.includes('"hs_task_type":"TODO"')
      ) {
        throw new Error("HubSpot contract pre-application task mapping failed");
      }
      jobs.recordApplied("hubspot-contract-job", undefined, "2026-08-03", "2026-08-03T10:02:00Z");
      jobs.upsertJobs([contractJob], "2026-08-03T10:02:30Z");
      if (jobs.requireJob("hubspot-contract-job").appliedAt !== "2026-08-03") {
        throw new Error("application checkpoint was not preserved by re-upsert");
      }
      const afterApplication = engine.next("hubspot-contract-job", "2026-08-03T10:03:00Z");
      const afterApplicationJson = JSON.stringify(afterApplication);
      if (
        !afterApplicationJson.includes("Send application follow-up") ||
        !afterApplicationJson.includes('"hs_task_type":"LINKED_IN_CONNECT"')
      ) {
        throw new Error("HubSpot contract post-application task mapping failed");
      }
      const failed = engine.record(
        { prospectId, error: "temporary HubSpot failure" },
        "2026-08-03T10:03:00Z",
      );
      if (failed.lastError === null) throw new Error("HubSpot error receipt was not stored");
      const company = engine.record({ prospectId, companyId: "101" }, "2026-08-03T10:04:00Z");
      if (company.companyId !== "101" || company.lastError !== null) {
        throw new Error("HubSpot success did not clear the stored error");
      }
      const replay = engine.record({ prospectId, companyId: "101" }, "2026-08-03T10:05:00Z");
      if (replay.updatedAt !== company.updatedAt) {
        throw new Error("identical HubSpot receipt replay was not a no-op");
      }
      let conflict = false;
      try {
        engine.record({ prospectId, companyId: "999" }, "2026-08-03T10:06:00Z");
      } catch (error) {
        conflict = error instanceof CliError && error.code === "HUBSPOT_RECEIPT_CONFLICT";
      }
      if (!conflict) throw new Error("conflicting HubSpot receipt was not rejected");
      opened.database
        .prepare("UPDATE jobs SET review = 'needs_review' WHERE id = 'hubspot-job'")
        .run();
      const complete = engine.record(
        {
          prospectId,
          contactId: "202",
          dealId: "303",
          taskId: "404",
          associationsComplete: true,
        },
        "2026-08-03T10:07:00Z",
      );
      if (complete.completedAt === null || !complete.associationsComplete) {
        throw new Error("HubSpot import did not complete after all receipts");
      }
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: Instantly handoff is gated by approval and complete HubSpot receipt;
  // recording an ambiguous email fails closed and a single email receipt is durable.
  {
    const opened = openDatabase(join(root, "hubspot.db"));
    try {
      opened.database.prepare("UPDATE jobs SET review = 'approved' WHERE id = 'hubspot-job'").run();
      const engine = new InstantlyHandoffEngine(opened.database);
      const first = engine.next("hubspot-job", "campaign-1", "2026-08-03T10:08:00Z");
      const packetJson = JSON.stringify(first);
      if (
        first === null ||
        !packetJson.includes("/api/v2/campaigns/campaign-1") ||
        !packetJson.includes("/api/v2/supersearch-enrichment") ||
        !packetJson.includes("/api/v2/leads") ||
        packetJson.includes("/api/v2/emails")
      )
        throw new Error("Instantly packet API contract failed");
      const prospectId = (first as { prospect: { prospectId: string } }).prospect.prospectId;
      let ambiguous = false;
      try {
        engine.record(
          { prospectId, email: "one@example.com", noEmail: true },
          "2026-08-03T10:09:00Z",
        );
      } catch (error) {
        ambiguous = error instanceof CliError && error.code === "INSTANTLY_AMBIGUOUS_EMAIL";
      }
      if (!ambiguous) throw new Error("Instantly ambiguous email was accepted");
      engine.record({ prospectId, enrichmentId: "enrich-1" }, "2026-08-03T10:09:30Z");
      const receipt = engine.record(
        {
          prospectId,
          email: "one@example.com",
          leadId: "lead-1",
          campaignStopOnReply: true,
        },
        "2026-08-03T10:10:00Z",
      );
      if (receipt.completedAt === null) throw new Error("Instantly receipt did not complete");
      const replay = engine.record(
        {
          prospectId,
          email: "one@example.com",
          leadId: "lead-1",
          campaignStopOnReply: true,
        },
        "2026-08-03T10:10:30Z",
      );
      if (replay.updatedAt !== receipt.updatedAt)
        throw new Error("Instantly identical completed receipt replay was not a no-op");
      let receiptConflict = false;
      try {
        engine.record(
          {
            prospectId,
            email: "different@example.com",
            leadId: "lead-1",
            campaignStopOnReply: true,
          },
          "2026-08-03T10:11:00Z",
        );
      } catch (error) {
        receiptConflict = error instanceof CliError && error.code === "INSTANTLY_HANDOFF_COMPLETE";
      }
      if (!receiptConflict) throw new Error("Instantly conflicting completed replay was accepted");
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: explicit terms, title/freshness filtering, and run scope.
  {
    const opened = openDatabase(join(root, "filter.db"));
    try {
      opened.database.exec(
        "INSERT INTO capture_runs (id, source_url, started_at, updated_at) VALUES ('r1','x','2026-08-01','2026-08-01'),('r2','x','2026-08-01','2026-08-01')",
      );
      const engine = new JobsEngine(opened.database);
      engine.storeCapturedJobs(
        [
          { id: "f1", title: " Product   Engineer " },
          { id: "f2", title: "Designer" },
          { id: "f3", title: "Software Engineer" },
        ],
        "2026-08-01",
      );
      opened.database.exec(
        "INSERT INTO job_observations (run_id,page_identity,job_id,observed_title,observed_at) VALUES ('r1','p','f1',' Product Engineer ','2026-08-01'),('r1','p','f2','Designer','2026-08-01'),('r2','p','f3','Software Engineer','2026-08-01')",
      );
      const result = filterRun(opened.database, {
        runId: "r1",
        terms: ['  "Product   Engineer"  '],
        policyVersion: "smoke-v1",
        maxAgeDays: 30,
        now: "2026-08-03T00:00:00Z",
      });
      const untouched = opened.database
        .query<{ fit: string }, []>("SELECT fit FROM jobs WHERE id='f3'")
        .get();
      if (result.kept !== 1 || result.dropped !== 1 || untouched?.fit !== "pending")
        throw new Error("filter smoke failed");
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: migration 9 lands; drafts round-trip with blank
  // lines; approval is guarded (non-draft, no profile, duplicate, sent); and
  // recipient uniqueness holds across normalization and sent jobs (no browser).
  {
    const opened = openDatabase(join(root, "review.db"));
    try {
      if (!opened.migrations.applied.includes("jobs_review")) {
        throw new Error("review migration (9) not applied");
      }
      const engine = new JobsEngine(opened.database);
      const member = {
        name: "Ana",
        profileUrl: "https://www.linkedin.com/in/ana",
        degree: "2nd",
        headline: "Hiring manager",
      };
      const memberVariant = {
        name: "Ana",
        profileUrl: "https://www.LINKEDIN.com/in/ANA/?miniProfileUrn=xyz#frag",
        degree: "2nd",
        headline: "Hiring manager",
      };
      const ben = {
        name: "Ben",
        profileUrl: "https://www.linkedin.com/in/ben",
        degree: "2nd",
        headline: "Hiring manager",
      };
      const job = (
        id: string,
        title: string,
        company: string,
        team: readonly { name: string; profileUrl: string; degree: string; headline: string }[],
      ) => ({
        id,
        title,
        company,
        location: "US",
        postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
        hiringTeam: team,
        hasHiringTeam: team.length > 0,
      });
      engine.upsertJobs(
        [
          job("a1", "Platform Engineer", "Acme", [member]),
          job("b2", "Staff Engineer", "Beta", [memberVariant]),
          job("c3", "Backend Engineer", "Gamma", [ben]),
          job("d4", "No Team", "Delta", []),
          job("e5", "Infra Engineer", "Epsilon", [member]),
        ],
        "2026-08-03T00:00:00Z",
      );

      // Migration 9 CHECK is enforced by SQLite (not only by JobsEngine).
      let checkEnforced = false;
      try {
        opened.database.exec(`UPDATE jobs SET review = 'bogus' WHERE id = 'a1'`);
      } catch {
        checkEnforced = true;
      }
      if (!checkEnforced) throw new Error("review CHECK constraint smoke failed");

      if (
        normalizeProfileUrl(memberVariant.profileUrl) !== normalizeProfileUrl(member.profileUrl)
      ) {
        throw new Error("profile URL normalization smoke failed");
      }

      const body =
        "Hi Ana.\n\nIf we work together one outcome is a clean handoff.\n\nRelevant proof sentence.\n\nWorth a quick conversation?";
      const draft = engine.storeDraft("a1", "Contract work", body, "2026-08-03T00:00:01Z");
      if (draft.subject !== "Contract work")
        throw new Error("draft subject roundtrip smoke failed");
      if (draft.message !== body) throw new Error("draft blank-line roundtrip smoke failed");
      if (draft.review !== "needs_review" || draft.status !== "drafted") {
        throw new Error("new draft should be needs_review + drafted");
      }

      // Intake approval does not require a draft; sending remains guarded later.
      const approvedWithoutDraft = engine.setReview("c3", "approved", "2026-08-03T00:00:02Z");
      if (approvedWithoutDraft.review !== "approved" || approvedWithoutDraft.status === "drafted") {
        throw new Error("approve without draft smoke failed");
      }

      // Approval guard: a drafted job without a usable profile URL cannot be approved.
      engine.storeDraft("d4", "", body, "2026-08-03T00:00:03Z");
      let noProfile = false;
      try {
        engine.setReview("d4", "approved", "2026-08-03T00:00:04Z");
      } catch (error) {
        noProfile = error instanceof CliError && error.code === "JOBS_NO_HIRING_TEAM";
      }
      if (!noProfile) throw new Error("approve no-profile guard smoke failed");

      const approved = engine.setReview("a1", "approved", "2026-08-03T00:00:05Z");
      if (approved.review !== "approved") throw new Error("approve smoke failed");

      const drafts = engine.approvedDrafts();
      if (drafts.length !== 1 || drafts[0]?.id !== "a1") {
        throw new Error("approvedDrafts selection smoke failed");
      }

      // Duplicate guard normalizes the URL: the variant profile conflicts with a1.
      engine.storeDraft("b2", "", body, "2026-08-03T00:00:06Z");
      let conflict = false;
      try {
        engine.setReview("b2", "approved", "2026-08-03T00:00:07Z");
      } catch (error) {
        conflict = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
      }
      if (!conflict) throw new Error("duplicate approved profile smoke failed");

      // Cross-run guard: after a1 is sent, a fresh draft for the same recipient
      // conflicts with the sent job, and a sent job cannot return to review.
      engine.markSent("a1", "2026-08-03T00:00:08Z");

      // Re-upserting a sent row must not reset it to collected (the CASE keeps
      // favorite/drafted/sent; only captured is promoted).
      engine.upsertJobs([job("a1", "Platform Engineer", "Acme", [member])], "2026-08-03T00:00:09Z");
      if (engine.requireJob("a1").status !== "sent") {
        throw new Error("upsert preserves sent status smoke failed");
      }

      engine.storeDraft("e5", "", body, "2026-08-03T00:00:09Z");
      let sentConflict = false;
      try {
        engine.setReview("e5", "approved", "2026-08-03T00:00:10Z");
      } catch (error) {
        sentConflict = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
      }
      if (!sentConflict) throw new Error("approve-vs-sent recipient conflict smoke failed");

      let sentReturn = false;
      try {
        engine.setReview("a1", "needs_review", "2026-08-03T00:00:11Z");
      } catch (error) {
        sentReturn = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!sentReturn) throw new Error("sent return-to-review guard smoke failed");

      let sentRedraft = false;
      try {
        engine.storeDraft("a1", "", body, "2026-08-03T00:00:12Z");
      } catch (error) {
        sentRedraft = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!sentRedraft) throw new Error("sent redraft guard smoke failed");
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: approval replacement is one transaction — the
  // exact conflicting id demotes the old approval and approves the current
  // draft, while stale/wrong targets and sent conflicts stay blocked.
  {
    const opened = openDatabase(join(root, "replace.db"));
    try {
      if (!opened.migrations.applied.includes("jobs_review")) {
        throw new Error("replace review migration (9) not applied");
      }
      const engine = new JobsEngine(opened.database);
      const alice = {
        name: "Alice",
        profileUrl: "https://www.linkedin.com/in/alice",
        degree: "2nd",
        headline: "HM",
      };
      const carol = {
        name: "Carol",
        profileUrl: "https://www.linkedin.com/in/carol",
        degree: "2nd",
        headline: "HM",
      };
      const job = (
        id: string,
        title: string,
        team: readonly { name: string; profileUrl: string; degree: string; headline: string }[],
      ) => ({
        id,
        title,
        company: "Co",
        location: "US",
        postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
        hiringTeam: team,
        hasHiringTeam: team.length > 0,
      });
      engine.upsertJobs(
        [
          job("f1", "Role One", [alice]),
          job("f2", "Role Two", [alice]),
          job("f3", "Role Three", [carol]),
        ],
        "2026-08-03T00:00:00Z",
      );
      const body = "Hi.\n\nBody.";
      engine.storeDraft("f1", "", body, "t1");
      engine.storeDraft("f2", "", body, "t1");
      engine.storeDraft("f3", "", body, "t1");
      engine.setReview("f1", "approved", "t1");

      // Normal duplicate rejection carries structured details.
      let duplicate = false;
      let duplicateDetails: Record<string, unknown> | undefined;
      try {
        engine.setReview("f2", "approved", "t1");
      } catch (error) {
        duplicate = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
        duplicateDetails = error instanceof CliError ? error.details : undefined;
      }
      if (
        !duplicate ||
        duplicateDetails?.jobId !== "f1" ||
        duplicateDetails?.status !== "drafted"
      ) {
        throw new Error("replace: normal duplicate rejection smoke failed");
      }

      // Atomic replacement: f2 replaces f1's approval in one transaction.
      const replaced = engine.setReview("f2", "approved", "t1", "f1");
      if (replaced.review !== "approved") throw new Error("replace: approval smoke failed");
      if (
        engine.requireJob("f1").review !== "needs_review" ||
        engine.requireJob("f1").status !== "drafted"
      ) {
        throw new Error("replace: old approval not demoted");
      }
      if (engine.approvedDrafts().length !== 1 || engine.approvedDrafts()[0]?.id !== "f2") {
        throw new Error("replace: approvals not exclusive");
      }

      // Stale/wrong replacement: a target that is not the current conflict is rejected.
      engine.storeDraft("f1", "", body, "t1");
      let stale = false;
      try {
        engine.setReview("f1", "approved", "t1", "f3");
      } catch (error) {
        stale = error instanceof CliError && error.code === "DUPLICATE_REPLACE_STALE";
      }
      if (!stale) throw new Error("replace: stale/wrong replacement smoke failed");
      if (engine.requireJob("f1").review !== "needs_review") {
        throw new Error("replace: stale changed current draft");
      }
      if (engine.requireJob("f3").review !== "needs_review") {
        throw new Error("replace: stale unapproved an arbitrary job");
      }

      // Sent conflict stays blocked: a replaceId never replaces a sent record.
      engine.markSent("f2", "t1");
      let sentBlocked = false;
      try {
        engine.setReview("f1", "approved", "t1", "f2");
      } catch (error) {
        sentBlocked =
          error instanceof CliError &&
          error.code === "DUPLICATE_APPROVED_PROFILE" &&
          error.details?.status === "sent";
      }
      if (!sentBlocked) throw new Error("replace: sent conflict not blocked");
      if (
        engine.requireJob("f2").status !== "sent" ||
        engine.requireJob("f1").review !== "needs_review"
      ) {
        throw new Error("replace: sent conflict mutated state");
      }
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: the recipient-group review operation atomically
  // skips or returns every non-sent role that shares the anchor's normalized
  // profile, never mutates a sent role, and leaves unrelated recipients alone.
  {
    const opened = openDatabase(join(root, "group.db"));
    try {
      const engine = new JobsEngine(opened.database);
      const alice = {
        name: "Alice",
        profileUrl: "https://www.linkedin.com/in/alice",
        degree: "2nd",
        headline: "HM",
      };
      const bob = {
        name: "Bob",
        profileUrl: "https://www.linkedin.com/in/bob",
        degree: "2nd",
        headline: "HM",
      };
      const job = (id: string, title: string, team: readonly (typeof alice)[]) => ({
        id,
        title,
        company: "Co",
        location: "US",
        postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
        hiringTeam: team,
        hasHiringTeam: team.length > 0,
      });
      engine.upsertJobs(
        [
          job("h1", "Role One", [alice]),
          job("h2", "Role Two", [alice]),
          job("h3", "Role Three", [alice]),
          job("h4", "Role Four", [bob]),
        ],
        "t0",
      );
      const body = "Hi.\n\nBody.";
      engine.storeDraft("h1", "", body, "t1");
      engine.storeDraft("h2", "", body, "t1");
      engine.storeDraft("h3", "", body, "t1");
      engine.storeDraft("h4", "", body, "t1");

      // Contact-level approval uniqueness: only one approved role per profile.
      engine.setReview("h1", "approved", "t2");
      let dup = false;
      try {
        engine.setReview("h3", "approved", "t3");
      } catch (error) {
        dup = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
      }
      if (!dup) throw new Error("group: contact-level approval uniqueness smoke failed");
      if (engine.approvedDrafts().length !== 1 || engine.approvedDrafts()[0]?.id !== "h1") {
        throw new Error("group: approvals not exclusive across a recipient");
      }

      // Group skip: every non-sent sibling for the same profile is skipped;
      // the unrelated recipient is untouched.
      engine.setGroupReview("h3", "skipped", "t4");
      for (const id of ["h1", "h2", "h3"]) {
        if (engine.requireJob(id).review !== "skipped") {
          throw new Error(`group skip: ${id} not skipped`);
        }
      }
      if (engine.requireJob("h4").review !== "needs_review") {
        throw new Error("group skip: unrelated recipient mutated");
      }

      // A later posting for the rejected person inherits the group decision.
      engine.upsertJobs([job("h5", "Role Five", [alice])], "t4a");
      if (engine.requireJob("h5").review !== "skipped") {
        throw new Error("group skip: later sibling did not inherit rejection");
      }

      // Group return: every non-sent sibling returns to needs_review.
      engine.setGroupReview("h2", "needs_review", "t5");
      for (const id of ["h1", "h2", "h3", "h5"]) {
        if (engine.requireJob(id).review !== "needs_review") {
          throw new Error(`group return: ${id} not returned`);
        }
      }

      // Sent immutability: when any grouped role is sent, the whole group
      // rejects skip/return before mutation and unsent siblings stay put.
      engine.setReview("h2", "approved", "t6");
      engine.markSent("h2", "t7");
      let skipRejected = false;
      try {
        engine.setGroupReview("h1", "skipped", "t8");
      } catch (error) {
        skipRejected = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!skipRejected) throw new Error("group skip on sent group not rejected");
      if (engine.requireJob("h1").review !== "needs_review") {
        throw new Error("group skip on sent group mutated unsent sibling h1");
      }
      if (engine.requireJob("h3").review !== "needs_review") {
        throw new Error("group skip on sent group mutated unsent sibling h3");
      }
      if (
        engine.requireJob("h2").status !== "sent" ||
        engine.requireJob("h2").review !== "approved"
      ) {
        throw new Error("group skip on sent group mutated the sent role");
      }

      let returnRejected = false;
      try {
        engine.setGroupReview("h1", "needs_review", "t9");
      } catch (error) {
        returnRejected = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!returnRejected) throw new Error("group return on sent group not rejected");
      if (engine.requireJob("h1").review !== "needs_review") {
        throw new Error("group return on sent group mutated unsent sibling h1");
      }
      if (engine.requireJob("h3").review !== "needs_review") {
        throw new Error("group return on sent group mutated unsent sibling h3");
      }
      if (
        engine.requireJob("h2").status !== "sent" ||
        engine.requireJob("h2").review !== "approved"
      ) {
        throw new Error("group return on sent group mutated the sent role");
      }
    } finally {
      opened.database.close();
    }
  }

  // Bounded pure-function check: the viewer grouping logic folds several roles
  // for one profile into one group (including a variant URL), gives a job with
  // no usable recipient its own fallback group, and picks bucket/primary per
  // the contact-centric precedence rules.
  {
    const alice = {
      name: "Alice",
      profileUrl: "https://www.linkedin.com/in/alice",
      degree: "2nd",
      headline: "HM",
    };
    const variant = {
      name: "Alice",
      profileUrl: "https://www.LINKEDIN.com/in/alice/?miniProfileUrn=xyz#frag",
      degree: "2nd",
      headline: "HM",
    };
    const row = (id: string, overrides: Partial<JobRow>): JobRow => ({
      id,
      title: "",
      company: "",
      location: "",
      postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
      hiringTeam: [],
      hasHiringTeam: false,
      status: "collected",
      message: null,
      collectedAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
      sentAt: null,
      description: "",
      workplaceType: "",
      employmentType: "",
      applyMethod: "",
      promoted: false,
      activelyReviewing: false,
      postedAt: "",
      applicantCount: "",
      benefits: [],
      enrichmentOutcome: "retry_required",
      enrichmentCapturedAt: null,
      enrichmentParserVersion: "",
      enrichmentEvidence: [],
      companyProfileUrl: "",
      companyEvidence: [],
      externalApplicationUrl: "",
      applicantTrackingSystem: "",
      geoId: "",
      workFocus: "",
      productSystem: "",
      workSummary: "",
      productSummary: "",
      subject: "",
      review: "needs_review",
      fit: "pending",
      filterReason: "",
      matchedTerm: "",
      filterPolicyVersion: "",
      filteredAt: null,
      triageBucket: "pending",
      companySummary: "",
      responsibilities: [],
      skillMatches: [],
      skillGaps: [],
      triageReason: "",
      triagePolicyVersion: "",
      triagedAt: null,
      appliedAt: null,
      applicationUrl: null,
      ...overrides,
    });
    const role = (id: string, review: JobRow["review"], updatedAt: string, team = alice) =>
      row(id, {
        title: `Role ${id}`,
        company: "Co",
        status: "drafted",
        review,
        updatedAt,
        hiringTeam: [team],
        hasHiringTeam: true,
      });
    const r1 = role("g1", "needs_review", "2026-08-03T00:00:01Z");
    const r2 = role("g2", "approved", "2026-08-03T00:00:02Z");
    const r3 = role("g3", "needs_review", "2026-08-03T00:00:03Z");
    const r4 = role("g4", "needs_review", "2026-08-03T00:00:04Z", variant);
    const noTeam = row("g5", { title: "No Recipient", status: "collected" });

    const groups = groupJobs([r1, r2, r3, r4, noTeam]);
    if (groups.length !== 2) throw new Error("grouping: expected 2 groups");
    const aliceGroup = groups.find((g) => g.jobs.length === 4);
    const fallback = groups.find((g) => g.jobs.length === 1 && g.jobs[0]?.id === "g5");
    if (aliceGroup === undefined || fallback === undefined) {
      throw new Error("grouping: expected alice group and fallback group");
    }
    if (bucketFor(aliceGroup.jobs) !== "approved") {
      throw new Error("grouping: approved bucket precedence");
    }
    if (primaryRoleFor(aliceGroup.jobs, undefined).id !== "g2") {
      throw new Error("grouping: primary defaults to approved role");
    }
    if (primaryRoleFor(aliceGroup.jobs, "g3").id !== "g3") {
      throw new Error("grouping: in-memory selection overrides approved");
    }
    const allSkipped = aliceGroup.jobs.map((j) => ({ ...j, review: "skipped" as const }));
    if (bucketFor(allSkipped) !== "skipped") {
      throw new Error("grouping: skipped only when every role skipped");
    }
    const withSent = aliceGroup.jobs.map((j) =>
      j.id === "g1" ? { ...j, status: "sent" as const } : j,
    );
    if (bucketFor(withSent) !== "sent") throw new Error("grouping: sent bucket precedence");
    if (primaryRoleFor(withSent, undefined).id !== "g1") {
      throw new Error("grouping: primary sent precedence over selection");
    }

    // Replacement UI: in an approved group, selecting a needs-review sibling is
    // editable and replacement-capable, while the approved owner stays read-only.
    const ownerAction = draftActionFor(aliceGroup.jobs, "g2");
    if (ownerAction.editable || ownerAction.replace || !ownerAction.canReturn) {
      throw new Error("grouping: approved owner should be read-only and returnable");
    }
    const siblingAction = draftActionFor(aliceGroup.jobs, "g3");
    if (!siblingAction.editable || !siblingAction.replace) {
      throw new Error(
        "grouping: needs-review sibling in approved group must be editable + replace",
      );
    }
    const plainAction = draftActionFor(
      aliceGroup.jobs.filter((j) => j.id !== "g2"),
      "g3",
    );
    if (!plainAction.editable || plainAction.replace) {
      throw new Error("grouping: needs-review group with no owner should approve, not replace");
    }
    const sentAction = draftActionFor(withSent, "g3");
    if (sentAction.editable || sentAction.canReturn) {
      throw new Error("grouping: sent group must be fully read-only");
    }
  }

  // Bounded pure-function check: section classification splits direct
  // outreach from application follow-up across the explicit contract
  // employment types, the two mislabeled full-time engagements, and
  // contract-mention false positives.
  {
    const kind = (employmentType: string, title: string, description: string) =>
      outreachKindFor({ employmentType, title, description });

    const contractIds = [
      "4451934421",
      "4452720575",
      "4451696307",
      "4452709098",
      "4454035786",
      "4447404037",
      "4452986849",
      "4451904629",
      "4443987736",
    ];
    for (const id of contractIds) {
      if (kind("Contract", `Role ${id}`, "") !== "application_followup") {
        throw new Error(`classification: contract role ${id} not application-follow-up`);
      }
    }
    if (kind("Full-time", "6 Month Contract to Hire", "") !== "application_followup") {
      throw new Error("classification: contract-to-hire not application-follow-up");
    }
    if (
      kind("Full-time", "", "Open to W2 /Individual C2C or 1099 candidates only") !==
      "application_followup"
    ) {
      throw new Error("classification: positive C2C/1099 engagement not application-follow-up");
    }
    const falsePositives = [
      "This is not a contract role.",
      "We advertise on contractor portals.",
      "test contract coverage",
      "customer contract values",
      // C2C/1099 with clear negation must stay direct.
      "This is not a C2C or contract role.",
      "Only W2, no C2C.",
      "This role is not open to C2C.",
    ];
    for (const phrase of falsePositives) {
      if (kind("Full-time", "", phrase) !== "direct") {
        throw new Error(`classification: false positive ${JSON.stringify(phrase)}`);
      }
    }
    if (kind("Full-time", "Platform Engineer", "W2 full-time employee") !== "direct") {
      throw new Error("classification: bare W2 must stay direct");
    }
    if (
      kind("Full-time", "", "We accept C2C and 1099 on this engagement") !== "application_followup"
    ) {
      throw new Error("classification: accepts C2C must be application-follow-up");
    }
  }

  // Bounded pure-function check: a mixed recipient group stays one queue item
  // in the application-follow-up section and defaults its primary selection to
  // the contract role unless sent/approved/override owns the decision.
  {
    const member = {
      name: "Sam",
      profileUrl: "https://www.linkedin.com/in/sam",
      degree: "2nd",
      headline: "HM",
    };
    const row = (id: string, overrides: Partial<JobRow>): JobRow => ({
      id,
      title: "",
      company: "",
      location: "",
      postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
      hiringTeam: [],
      hasHiringTeam: false,
      status: "collected",
      message: null,
      collectedAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
      sentAt: null,
      description: "",
      workplaceType: "",
      employmentType: "",
      applyMethod: "",
      promoted: false,
      activelyReviewing: false,
      postedAt: "",
      applicantCount: "",
      benefits: [],
      enrichmentOutcome: "retry_required",
      enrichmentCapturedAt: null,
      enrichmentParserVersion: "",
      enrichmentEvidence: [],
      companyProfileUrl: "",
      companyEvidence: [],
      externalApplicationUrl: "",
      applicantTrackingSystem: "",
      geoId: "",
      workFocus: "",
      productSystem: "",
      workSummary: "",
      productSummary: "",
      subject: "",
      review: "needs_review",
      fit: "pending",
      filterReason: "",
      matchedTerm: "",
      filterPolicyVersion: "",
      filteredAt: null,
      triageBucket: "pending",
      companySummary: "",
      responsibilities: [],
      skillMatches: [],
      skillGaps: [],
      triageReason: "",
      triagePolicyVersion: "",
      triagedAt: null,
      appliedAt: null,
      applicationUrl: null,
      ...overrides,
    });
    const role = (
      id: string,
      review: JobRow["review"],
      updatedAt: string,
      employmentType: string,
    ) =>
      row(id, {
        title: `Role ${id}`,
        company: "Co",
        status: "drafted",
        review,
        updatedAt,
        hiringTeam: [member],
        hasHiringTeam: true,
        employmentType,
      });
    const contract = role("k1", "needs_review", "2026-08-03T00:00:01Z", "Contract");
    const direct = role("k2", "needs_review", "2026-08-03T00:00:02Z", "Full-time");

    const mixed = groupJobs([contract, direct]);
    if (mixed.length !== 1 || mixed[0]?.jobs.length !== 2) {
      throw new Error("classification: mixed group must stay one recipient group");
    }
    const group = mixed[0];
    if (group === undefined) throw new Error("classification: mixed group missing");
    if (groupOutreachKind(group.jobs) !== "application_followup") {
      throw new Error("classification: mixed group must be application-follow-up");
    }
    if (primaryRoleFor(group.jobs, undefined).id !== "k1") {
      throw new Error("classification: mixed group defaults primary to contract role");
    }
    if (primaryRoleFor(group.jobs, "k2").id !== "k2") {
      throw new Error("classification: explicit selection overrides contract default");
    }
    const approvedDirect = group.jobs.map((j) =>
      j.id === "k2" ? { ...j, review: "approved" as const } : j,
    );
    if (primaryRoleFor(approvedDirect, undefined).id !== "k2") {
      throw new Error("classification: approved direct role owns the decision");
    }
    const sentDirect = group.jobs.map((j) =>
      j.id === "k2" ? { ...j, status: "sent" as const } : j,
    );
    if (primaryRoleFor(sentDirect, undefined).id !== "k2") {
      throw new Error("classification: sent role owns the decision");
    }
    const directOnly = groupJobs([direct]);
    if (directOnly.length !== 1 || groupOutreachKind(directOnly[0]?.jobs ?? []) !== "direct") {
      throw new Error("classification: direct-only group must be direct");
    }
    const contractOnly = groupJobs([contract]);
    if (
      contractOnly.length !== 1 ||
      groupOutreachKind(contractOnly[0]?.jobs ?? []) !== "application_followup"
    ) {
      throw new Error("classification: contract-only group must be application-follow-up");
    }
  }

  // Bounded pure-function check: the all-outreach section counts every group
  // once, status/text filters compose with any section, and the searchable
  // haystack covers employmentType plus the direct/applied type label.
  {
    const member = {
      name: "Sam",
      profileUrl: "https://www.linkedin.com/in/sam",
      degree: "2nd",
      headline: "HM",
    };
    const row = (id: string, overrides: Partial<JobRow>): JobRow => ({
      id,
      title: "",
      company: "",
      location: "",
      postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
      hiringTeam: [],
      hasHiringTeam: false,
      status: "collected",
      message: null,
      collectedAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
      sentAt: null,
      description: "",
      workplaceType: "",
      employmentType: "",
      applyMethod: "",
      promoted: false,
      activelyReviewing: false,
      postedAt: "",
      applicantCount: "",
      benefits: [],
      enrichmentOutcome: "retry_required",
      enrichmentCapturedAt: null,
      enrichmentParserVersion: "",
      enrichmentEvidence: [],
      companyProfileUrl: "",
      companyEvidence: [],
      externalApplicationUrl: "",
      applicantTrackingSystem: "",
      geoId: "",
      workFocus: "",
      productSystem: "",
      workSummary: "",
      productSummary: "",
      subject: "",
      review: "needs_review",
      fit: "pending",
      filterReason: "",
      matchedTerm: "",
      filterPolicyVersion: "",
      filteredAt: null,
      triageBucket: "pending",
      companySummary: "",
      responsibilities: [],
      skillMatches: [],
      skillGaps: [],
      triageReason: "",
      triagePolicyVersion: "",
      triagedAt: null,
      appliedAt: null,
      applicationUrl: null,
      ...overrides,
    });
    const contract = row("s1", {
      title: "Contract Platform Engineer",
      company: "Acme",
      employmentType: "Contract",
      status: "drafted",
      hiringTeam: [member],
      hasHiringTeam: true,
      updatedAt: "2026-08-03T00:00:01Z",
    });
    const direct = row("s2", {
      title: "Platform Engineer",
      company: "Beta",
      employmentType: "Full-time",
      status: "drafted",
      review: "approved",
      hiringTeam: [{ ...member, profileUrl: "https://www.linkedin.com/in/beta" }],
      hasHiringTeam: true,
      updatedAt: "2026-08-03T00:00:02Z",
    });
    const sentDirect = row("s3", {
      title: "Staff Engineer",
      company: "Gamma",
      employmentType: "Full-time",
      status: "sent",
      review: "approved",
      hiringTeam: [{ ...member, profileUrl: "https://www.linkedin.com/in/gamma" }],
      hasHiringTeam: true,
      updatedAt: "2026-08-03T00:00:03Z",
    });

    const groups = groupJobs([contract, direct, sentDirect]);
    if (groups.length !== 3) throw new Error("all-section: expected 3 groups");

    const counts = sectionCounts(groups);
    if (counts.direct !== 2 || counts.application_followup !== 1) {
      throw new Error("all-section: section counts wrong");
    }

    const all = visibleGroups(groups, "all", "all", "");
    if (all.length !== 3) throw new Error("all-section: All section must show every group once");

    const followup = visibleGroups(groups, "application_followup", "all", "");
    if (followup.length !== 1 || followup[0]?.jobs[0]?.id !== "s1") {
      throw new Error("all-section: application follow-up section wrong");
    }

    const needsReview = visibleGroups(groups, "all", "needs_review", "");
    if (needsReview.length !== 1 || needsReview[0]?.jobs[0]?.id !== "s1") {
      throw new Error("all-section: needs_review filter should compose with All");
    }

    const query = visibleGroups(groups, "all", "all", "contract");
    if (query.length !== 1 || query[0]?.jobs[0]?.id !== "s1") {
      throw new Error("all-section: employmentType/title text query failed");
    }

    const kindQuery = visibleGroups(groups, "all", "all", "apply");
    if (kindQuery.length !== 1 || kindQuery[0]?.jobs[0]?.id !== "s1") {
      throw new Error("all-section: apply type label not searchable");
    }

    if (
      outreachKindLabel("direct") !== "Direct" ||
      outreachKindLabel("application_followup") !== "Apply + follow up"
    ) {
      throw new Error("all-section: kind label wrong");
    }

    const hay = groupSearchHaystack([contract]);
    if (
      !hay.includes("contract") ||
      !hay.includes("apply follow up") ||
      !hay.includes("application follow-up")
    ) {
      throw new Error("all-section: haystack missing employmentType or type label");
    }

    // Reminder keying: an Apply + follow up group shows the reminder in All too, while
    // Direct groups never do.
    if (!showsApplicationReminder(followup[0]?.jobs ?? [])) {
      throw new Error("all-section: Apply + follow up group must show the reminder in All");
    }
    const directGroups = visibleGroups(groups, "direct", "all", "");
    if (directGroups.length !== 2 || directGroups.some((g) => showsApplicationReminder(g.jobs))) {
      throw new Error("all-section: Direct groups must not show the reminder");
    }
  }
  try {
    await jobsSendPrepare(
      { stateDir: join(root, "send"), allowSend: true },
      { now: () => "2026-08-03T12:00:00Z" },
    );
    throw new Error("jobs send should have thrown for no approved drafts");
  } catch (error) {
    if (!(error instanceof CliError) || error.code !== "JOBS_NOTHING_TO_SEND") throw error;
  }

  // Bounded temp-state check: a contract follow-up cannot reach browser
  // session resolution until its application checkpoint exists.
  {
    const stateDir = join(root, "send-contract");
    const opened = openDatabase(join(stateDir, "linkedin-tools.db"));
    try {
      const engine = new JobsEngine(opened.database);
      engine.upsertJobs(
        [
          {
            id: "send-contract-job",
            title: "Contract Product Engineer",
            company: "Contract Co",
            location: "Remote",
            postingUrl: "https://www.linkedin.com/jobs/view/send-contract-job/",
            hiringTeam: [
              {
                name: "Contract Manager",
                profileUrl: "https://www.linkedin.com/in/contract-manager/",
                degree: "2nd",
                headline: "Hiring manager",
              },
            ],
            hasHiringTeam: true,
          },
        ],
        "2026-08-03T12:00:00Z",
      );
      opened.database
        .prepare("UPDATE jobs SET employment_type = 'Contract' WHERE id = 'send-contract-job'")
        .run();
      engine.storeDraft(
        "send-contract-job",
        "Contract role",
        "Hi.\n\nI applied and wanted to follow up.",
        "2026-08-03T12:01:00Z",
      );
      engine.setReview("send-contract-job", "approved", "2026-08-03T12:02:00Z");
      if (engine.approvedDrafts().length !== 0) {
        throw new Error("unapplied contract draft entered bulk send eligibility");
      }
    } finally {
      opened.database.close();
    }
    try {
      await jobsSendPrepare(
        { stateDir, allowSend: true, id: "send-contract-job" },
        { now: () => "2026-08-03T12:03:00Z" },
      );
      throw new Error("contract send should require an application checkpoint");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "JOBS_APPLICATION_REQUIRED") throw error;
    }
    const reopened = openDatabase(join(stateDir, "linkedin-tools.db"));
    try {
      const engine = new JobsEngine(reopened.database);
      engine.recordApplied("send-contract-job", undefined, "2026-08-03", "2026-08-03T12:04:00Z");
      if (engine.approvedDrafts()[0]?.id !== "send-contract-job") {
        throw new Error("applied contract draft did not enter send eligibility");
      }
      engine.upsertJobs(
        [
          {
            id: "send-direct-job",
            title: "Product Engineer",
            company: "Direct Co",
            location: "Remote",
            postingUrl: "https://www.linkedin.com/jobs/view/send-direct-job/",
            hiringTeam: [
              {
                name: "Direct Manager",
                profileUrl: "https://www.linkedin.com/in/direct-manager/",
                degree: "2nd",
                headline: "Hiring manager",
              },
            ],
            hasHiringTeam: true,
          },
        ],
        "2026-08-03T12:04:30Z",
      );
      engine.storeDraft("send-direct-job", "Direct role", "Short note", "2026-08-03T12:04:31Z");
      engine.setReview("send-direct-job", "approved", "2026-08-03T12:04:32Z");
    } finally {
      reopened.database.close();
    }
    const invitationDb = openDatabase(join(stateDir, "linkedin-tools.db"));
    try {
      const packet = prepareContractOutreach(
        invitationDb.database,
        "send-contract-job",
        "2026-08-03T12:05:00Z",
      );
      recordContractOutreach(
        invitationDb.database,
        {
          attemptId: packet.attemptId,
          jobId: packet.jobId,
          route: packet.route,
          draftFingerprint: packet.draftFingerprint,
          state: "possible",
          evidence: { commitStarted: true },
        },
        "2026-08-03T12:05:01Z",
      );
      try {
        prepareContractOutreach(invitationDb.database, "send-contract-job", "2026-08-03T12:05:02Z");
        throw new Error("possible invitation did not block retry");
      } catch (error) {
        if (
          !(error instanceof CliError) ||
          error.code !== "JOBS_CONTRACT_OUTREACH_RECONCILIATION_REQUIRED"
        )
          throw error;
      }
      recordContractOutreach(
        invitationDb.database,
        {
          attemptId: packet.attemptId,
          jobId: packet.jobId,
          route: packet.route,
          draftFingerprint: packet.draftFingerprint,
          state: "proven_no_send",
          evidence: {
            reconciliation: { reason: "smoke", noRequestObserved: true, operatorConfirmed: true },
          },
        },
        "2026-08-03T12:05:03Z",
      );
      const retry = prepareContractOutreach(
        invitationDb.database,
        "send-contract-job",
        "2026-08-03T12:05:04Z",
      );
      try {
        recordContractOutreach(
          invitationDb.database,
          {
            attemptId: retry.attemptId,
            jobId: retry.jobId,
            route: retry.route,
            draftFingerprint: retry.draftFingerprint,
            state: "confirmed",
            evidence: {
              request: {
                method: "POST",
                url: "https://www.linkedin.com/learned-later",
                status: 201,
                bodySha256: "x",
              },
              invitation: { pending: true, profileUrl: retry.recipientUrl },
            },
          },
          "2026-08-03T12:05:05Z",
        );
        throw new Error("unlearned endpoint did not fail closed");
      } catch (error) {
        if (
          !(error instanceof CliError) ||
          error.code !== "JOBS_CONTRACT_OUTREACH_ENDPOINT_UNVERIFIED"
        )
          throw error;
      }
      const directPacket = prepareContractOutreach(
        invitationDb.database,
        "send-direct-job",
        "2026-08-03T12:05:06Z",
      );
      if (directPacket.route !== "direct" || directPacket.note !== "Short note")
        throw new Error("direct invitation packet contract failed");
      recordContractOutreach(
        invitationDb.database,
        {
          attemptId: directPacket.attemptId,
          jobId: directPacket.jobId,
          route: directPacket.route,
          draftFingerprint: directPacket.draftFingerprint,
          state: "confirmed",
          evidence: {
            request: {
              method: "POST",
              url: "https://www.linkedin.com/learned-later",
              status: 201,
              bodySha256: "x",
              recipientUrn: "urn:li:fsd_profile:direct",
            },
            invitation: { pending: true, profileUrl: directPacket.recipientUrl },
          },
        },
        "2026-08-03T12:05:07Z",
        { urls: ["https://www.linkedin.com/learned-later"], requireRecipientUrn: true },
      );
      const directRow = new JobsEngine(invitationDb.database).requireJob("send-direct-job");
      if (directRow.status === "sent")
        throw new Error("connection confirmation incorrectly marked job sent");
    } finally {
      invitationDb.database.close();
    }
  }

  // Chrome outreach receipt checks: reserve before the handoff, persist
  // incomplete possible evidence, reject conflicting replays, reconcile, and
  // release a proven-no-send reservation for a fresh attempt.
  {
    const stateDir = join(root, "send-receipts");
    const opened = openDatabase(join(stateDir, "linkedin-tools.db"));
    try {
      const engine = new JobsEngine(opened.database);
      const makeJob = (id: string) => {
        engine.upsertJobs(
          [
            {
              id,
              title: "Role",
              company: "Co",
              location: "Remote",
              postingUrl: `https://www.linkedin.com/jobs/view/${id}`,
              hiringTeam: [
                {
                  name: "Pat",
                  profileUrl: `https://www.linkedin.com/in/${id}/`,
                  degree: "2nd",
                  headline: "Hiring",
                },
              ],
              hasHiringTeam: true,
            },
          ],
          "2026-08-03T12:00:00Z",
        );
        engine.storeDraft(id, "Hi", "Hello", "2026-08-03T12:00:00Z");
        engine.setReview(id, "approved", "2026-08-03T12:00:00Z");
      };
      makeJob("receipt-one");
      const packet = (
        (await jobsSendPrepare(
          { stateDir, id: "receipt-one", allowSend: true },
          { now: () => "2026-08-03T12:01:00Z" },
        )) as { packet: Record<string, unknown> }
      ).packet;
      const base = {
        attemptId: packet.attemptId,
        jobId: packet.jobId,
        route: packet.route,
        draftFingerprint: packet.draftFingerprint,
        transport: "dm",
      };
      const possible = { commitStarted: true };
      const recorded = recordChromeSend(
        opened.database,
        { ...base, state: "possible", evidence: possible },
        "2026-08-03T12:02:00Z",
      );
      if (recorded.state !== "possible") throw new Error("possible receipt was not persisted");
      try {
        await jobsSendPrepare({ stateDir, id: "receipt-one", allowSend: true });
        throw new Error("possible receipt did not block prepare");
      } catch (error) {
        if (!(error instanceof CliError) || error.code !== "JOBS_SEND_RECONCILIATION_REQUIRED")
          throw error;
      }
      try {
        recordChromeSend(
          opened.database,
          {
            ...base,
            state: "possible",
            evidence: { commitStarted: false },
          },
          "2026-08-03T12:03:00Z",
        );
        throw new Error("conflicting replay was accepted");
      } catch (error) {
        if (!(error instanceof CliError) || error.code !== "JOBS_SEND_CONFLICTING_REPLAY")
          throw error;
      }
      recordChromeSend(
        opened.database,
        {
          ...base,
          state: "confirmed",
          evidence: {
            request: {
              method: "POST",
              url: "https://example.test/dm",
              status: 200,
              bodySha256: "x",
            },
            thread: {
              composerGone: true,
              messageVisible: true,
              profileUrl: "https://www.linkedin.com/in/receipt-one/",
              messageSha256: createHash("sha256").update("Hello").digest("hex"),
            },
          },
        },
        "2026-08-03T12:04:00Z",
        { dm: { urls: ["https://example.test/dm"], requireRecipientUrn: false } },
      );
      makeJob("receipt-two");
      const second = (
        (await jobsSendPrepare(
          { stateDir, id: "receipt-two", allowSend: true },
          { now: () => "2026-08-03T12:05:00Z" },
        )) as { packet: Record<string, unknown> }
      ).packet;
      const secondBase = {
        attemptId: second.attemptId,
        jobId: second.jobId,
        route: second.route,
        draftFingerprint: second.draftFingerprint,
        transport: "dm",
      };
      recordChromeSend(
        opened.database,
        {
          ...secondBase,
          state: "proven_no_send",
          evidence: {
            reconciliation: {
              reason: "no request observed",
              noRequestObserved: true,
              operatorConfirmed: true,
            },
          },
        },
        "2026-08-03T12:06:00Z",
      );
      const replacement = (await jobsSendPrepare(
        { stateDir, id: "receipt-two", allowSend: true },
        { now: () => "2026-08-03T12:07:00Z" },
      )) as { packet: Record<string, unknown> };
      if (replacement.packet.attemptId === second.attemptId)
        throw new Error("proven-no-send did not release the attempt");
    } finally {
      opened.database.close();
    }
  }

  // Caller-owned Chrome helper arms observation and persists uncertainty
  // before the visible mutation. It confirms only the exact configured POST
  // plus recipient-bound thread evidence.
  {
    // @ts-expect-error smoke imports the caller-owned JS handoff helper directly.
    const { sendJobMessage } = await import("./linkedin-jobs-outreach-chrome-helper.mjs");
    const events: string[] = [];
    let reads = 0;
    const tab = {
      capabilities: {
        get: async () => ({
          send: async () => ({}),
          readEvents: async () => {
            reads += 1;
            if (reads === 1) return { cursor: 1, events: [], truncated: false };
            return {
              cursor: 2,
              truncated: false,
              events: [
                {
                  method: "Network.requestWillBeSent",
                  params: {
                    requestId: "request-1",
                    request: {
                      method: "POST",
                      url: "https://www.linkedin.com/voyager/api/fixture-send",
                      postData: '{"recipient":"urn:li:fsd_profile:123"}',
                    },
                  },
                },
                {
                  method: "Network.responseReceived",
                  params: {
                    requestId: "request-1",
                    response: { status: 200 },
                  },
                },
              ],
            };
          },
        }),
      },
    };
    const result = await sendJobMessage(tab, {
      packet: {
        recipientUrl: "https://www.linkedin.com/in/fixture",
        message: "Hello",
        subject: "",
      },
      route: "direct",
      transport: "dm",
      endpointUrl: "https://www.linkedin.com/voyager/api/fixture-send",
      beforeAction: async () => events.push("reserved"),
      action: async () => events.push("clicked"),
      threadCheck: async () => ({
        composerGone: true,
        messageVisible: true,
        profileUrl: "https://www.linkedin.com/in/fixture",
      }),
    });
    if (events.join(",") !== "reserved,clicked" || result.state !== "confirmed")
      throw new Error("Chrome send helper did not reserve before the visible action");
  }

  // Capture handoff checks: SQLite owns durable state, repeated pages are a
  // no-op, malformed payloads are rejected before insert, and no browser runs.
  {
    const captureDb = openDatabase(join(root, "capture.db"));
    try {
      const { JobsCaptureStore } = await import("../src/jobs/capture.ts");
      const store = new JobsCaptureStore(captureDb.database);
      const run = store.startRun({
        id: "run-1",
        sourceUrl: "https://www.linkedin.com/jobs/search/?keywords=engineer",
        searchConfigJson: JSON.stringify({ keywords: "engineer" }),
        checkpointJson: JSON.stringify({ next: "page-2" }),
        now: "2026-08-03T12:00:00Z",
      });
      if (run.state !== "active") throw new Error("capture run did not start");
      const payload = JSON.stringify({
        included: [{ $type: "com.linkedin.voyager.dash.jobs.JobPosting" }],
      });
      const first = store.ingestPage({
        runId: "run-1",
        pageIdentity: "start:0",
        sourceUrl: run.sourceUrl,
        responseUrl: "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards",
        capturedAt: "2026-08-03T12:00:01Z",
        payloadText: payload,
      });
      const second = store.ingestPage({
        runId: "run-1",
        pageIdentity: "start:0",
        sourceUrl: run.sourceUrl,
        responseUrl: "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards",
        capturedAt: "2026-08-03T12:00:02Z",
        payloadText: payload,
      });
      if (!first.inserted || second.inserted)
        throw new Error("capture page ingest is not idempotent");
      if (first.run.checkpoint.next !== "page-2" || first.run.checkpoint.last_page !== "start:0") {
        throw new Error("capture checkpoint did not preserve existing keys");
      }
      const resumed = store.startRun({
        id: "run-1",
        sourceUrl: run.sourceUrl,
        searchConfigJson: JSON.stringify({ keywords: "engineer" }),
        now: "2026-08-03T12:00:02Z",
      });
      if (resumed.checkpoint.last_page !== "start:0") {
        throw new Error("capture run did not resume from its advanced checkpoint");
      }
      const nextPage = store.ingestPage({
        runId: "run-1",
        pageIdentity: "page:1",
        cursor: "cursor-1",
        sourceUrl: run.sourceUrl,
        responseUrl: "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards",
        capturedAt: "2026-08-03T12:00:03Z",
        payloadText: payload,
      });
      const nextRetry = store.ingestPage({
        runId: "run-1",
        pageIdentity: "page:1",
        cursor: "cursor-1",
        sourceUrl: run.sourceUrl,
        responseUrl: "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards",
        capturedAt: "2026-08-03T12:00:04Z",
        payloadText: payload,
      });
      if (
        !nextPage.inserted ||
        nextRetry.inserted ||
        nextRetry.run.checkpoint.last_page !== "page:1"
      ) {
        throw new Error("multi-page capture retry was not durable and idempotent");
      }
      try {
        store.ingestPage({
          runId: "run-1",
          pageIdentity: "bad",
          sourceUrl: run.sourceUrl,
          responseUrl: "https://example.test/jobs",
          capturedAt: "2026-08-03T12:00:03Z",
          payloadText: "not json",
        });
        throw new Error("malformed capture payload was accepted");
      } catch (error) {
        if (!(error instanceof CliError)) throw error;
      }
      try {
        store.ingestPage({
          runId: "run-1",
          pageIdentity: "not-jobs",
          sourceUrl: run.sourceUrl,
          responseUrl: "https://example.test/jobs",
          capturedAt: "2026-08-03T12:00:03Z",
          payloadText: JSON.stringify({ foo: 1 }),
        });
        throw new Error("arbitrary JSON object was accepted as a Jobs payload");
      } catch (error) {
        if (!(error instanceof CliError)) throw error;
      }
      try {
        store.startRun({
          id: "run-1",
          sourceUrl: "https://different",
          now: "2026-08-03T12:00:04Z",
        });
        throw new Error("capture run conflict was accepted");
      } catch (error) {
        if (!(error instanceof CliError) || error.code !== "CAPTURE_RUN_CONFLICT") throw error;
      }
      try {
        store.ingestPage({
          runId: "run-1",
          pageIdentity: "start:0",
          sourceUrl: run.sourceUrl,
          responseUrl: "https://different",
          capturedAt: "2026-08-03T12:00:04Z",
          payloadText: payload,
        });
        throw new Error("capture page conflict was accepted");
      } catch (error) {
        if (!(error instanceof CliError) || error.code !== "CAPTURE_PAGE_CONFLICT") throw error;
      }
      const finished = store.finishRun({
        id: "run-1",
        state: "complete",
        checkpointJson: JSON.stringify({ final: true }),
        now: "2026-08-03T12:00:04Z",
      });
      const retried = store.finishRun({
        id: "run-1",
        state: "complete",
        checkpointJson: JSON.stringify({ final: true }),
        now: "2026-08-03T12:00:05Z",
      });
      if (finished.state !== "complete" || retried.state !== "complete") {
        throw new Error("capture finish is not retry-safe");
      }
      try {
        store.finishRun({
          id: "run-1",
          state: "failed",
          error: "late",
          now: "2026-08-03T12:00:06Z",
        });
        throw new Error("terminal capture rewrite was accepted");
      } catch (error) {
        if (!(error instanceof CliError) || error.code !== "CAPTURE_FINISH_CONFLICT") throw error;
      }
      try {
        store.ingestPage({
          runId: "run-1",
          pageIdentity: "new",
          sourceUrl: run.sourceUrl,
          responseUrl: "https://example.test/jobs",
          capturedAt: "2026-08-03T12:00:06Z",
          payloadText: payload,
        });
        throw new Error("new page accepted after terminal capture finish");
      } catch (error) {
        if (!(error instanceof CliError) || error.code !== "CAPTURE_RUN_NOT_ACTIVE") throw error;
      }
      const helper = await readFile(
        new URL("../scripts/linkedin-jobs-chrome-helper.mjs", import.meta.url),
        "utf8",
      );
      if (
        !helper.includes('"--payload"') ||
        !helper.includes('"-"') ||
        !helper.includes("captureAndIngestJobsPage")
      ) {
        throw new Error("Chrome helper does not construct stdin ingest arguments");
      }
      const pages = captureDb.database
        .query("SELECT COUNT(*) AS count FROM capture_pages")
        .get() as { count: number };
      if (pages.count !== 2)
        throw new Error("capture pages duplicated or malformed payload persisted");
    } finally {
      captureDb.database.close();
    }
  }

  // Step 2 normalization: temporary SQLite, duplicate IDs across pages, rich
  // existing fields, and resume after one-page processing.
  {
    const normalizeDb = openDatabase(join(root, "normalize.db"));
    try {
      const { JobsCaptureStore } = await import("../src/jobs/capture.ts");
      const capture = new JobsCaptureStore(normalizeDb.database);
      capture.startRun({
        id: "normalize-run",
        sourceUrl: "https://www.linkedin.com/jobs/search",
        now: "2026-08-03T12:00:00Z",
      });
      const payload = (title: string, id: string) =>
        JSON.stringify({
          included: [
            {
              $type: "com.linkedin.voyager.dash.jobs.JobPosting",
              entityUrn: `urn:li:fsd_jobPosting:${id}`,
              title,
            },
          ],
        });
      for (const [page, title] of [
        ["page-1", "First"],
        ["page-2", "Second"],
      ] as const) {
        capture.ingestPage({
          runId: "normalize-run",
          pageIdentity: page,
          sourceUrl: "https://www.linkedin.com/jobs/search",
          responseUrl: "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards",
          capturedAt: `2026-08-03T12:00:0${page === "page-1" ? "1" : "2"}Z`,
          payloadText: payload(title, page === "page-1" ? "123" : "123"),
        });
      }
      new JobsEngine(normalizeDb.database).storeCapturedJobs(
        [{ id: "123", title: "Existing" }],
        "2026-08-03T12:00:00Z",
      );
      normalizeDb.database.prepare("UPDATE jobs SET company = 'Richer Co' WHERE id = '123'").run();
      const first = new JobsNormalizer(normalizeDb.database).normalize({
        runId: "normalize-run",
        limit: 1,
        now: "2026-08-03T12:01:00Z",
      });
      if (first.pagesProcessed !== 1 || first.jobsObserved !== 1 || first.remainingPages !== 1)
        throw new Error("normalization did not process one page");
      const second = new JobsNormalizer(normalizeDb.database).normalize({
        runId: "normalize-run",
        now: "2026-08-03T12:02:00Z",
      });
      if (second.newlyInserted !== 0 || second.deduplicated !== 1 || second.remainingPages !== 0)
        throw new Error("normalization did not deduplicate/resume");
      const rich = normalizeDb.database
        .query("SELECT company FROM jobs WHERE id = '123'")
        .get() as { company: string };
      if (rich.company !== "Richer Co") throw new Error("normalization erased richer job fields");
    } finally {
      normalizeDb.database.close();
    }
  }

  // draft-next is a read-only, deterministic companion handoff.
  {
    const stateDir = join(root, "draft-next");
    const db = openDatabase(join(stateDir, "linkedin-tools.db"));
    const engine = new JobsEngine(db.database);
    const member = (name: string, id: string) => ({
      name,
      profileUrl: `https://www.linkedin.com/in/${id}`,
      degree: "2nd",
      headline: "Hiring",
    });
    engine.upsertJobs(
      [
        {
          id: "direct",
          title: "Product Engineer",
          company: "Acme",
          location: "Remote",
          postingUrl: "https://www.linkedin.com/jobs/view/direct/",
          hiringTeam: [member("D Person", "d-person")],
          hasHiringTeam: true,
        },
        {
          id: "applied",
          title: "Contract Engineer",
          company: "Beta",
          location: "Remote",
          postingUrl: "https://www.linkedin.com/jobs/view/applied/",
          hiringTeam: [member("A Person", "a-person")],
          hasHiringTeam: true,
        },
        {
          id: "blocked",
          title: "Contract Designer",
          company: "Gamma",
          location: "Remote",
          postingUrl: "https://www.linkedin.com/jobs/view/blocked/",
          hiringTeam: [member("B Person", "b-person")],
          hasHiringTeam: true,
        },
        {
          id: "blocked-2",
          title: "Contract Researcher",
          company: "Delta",
          location: "Remote",
          postingUrl: "https://www.linkedin.com/jobs/view/blocked-2/",
          hiringTeam: [member("C Person", "c-person")],
          hasHiringTeam: true,
        },
      ],
      "2026-08-03T00:00:00Z",
    );
    db.database
      .prepare(
        "UPDATE jobs SET fit='kept', review='approved', triage_bucket='strong', description='Build a billing dashboard', employment_type=CASE WHEN id IN ('applied','blocked','blocked-2') THEN 'Contract' ELSE employment_type END, external_application_url=CASE WHEN id IN ('applied','blocked','blocked-2') THEN 'https://example.test/apply' ELSE external_application_url END, skill_matches_json=? WHERE id IN ('direct','applied','blocked','blocked-2')",
      )
      .run(JSON.stringify(["billing dashboards"]));
    const application = await jobsApplicationNext({ stateDir, id: "applied" });
    if (
      (
        application as {
          packet: {
            job: { applicationUrl: string | null };
            checkpoint: { required: boolean };
            stopPoints: string[];
          };
        }
      ).packet.job.applicationUrl !== "https://example.test/apply" ||
      !(application as { packet: { checkpoint: { required: boolean } } }).packet.checkpoint
        .required ||
      (application as { packet: { stopPoints: string[] } }).packet.stopPoints.length !== 4
    )
      throw new Error("application-next packet failed");
    const beforeCheckpoint = db.database
      .query("SELECT applied_at FROM jobs WHERE id='applied'")
      .get() as { applied_at: string | null };
    if (beforeCheckpoint.applied_at !== null)
      throw new Error("application handoff mutated checkpoint");
    engine.recordApplied(
      "applied",
      "https://example.test/apply",
      "2026-08-03",
      "2026-08-03T00:01:00Z",
    );
    db.database.close();
    const direct = await jobsDraftNext({ stateDir, id: "direct" });
    if (
      !(direct as { found: boolean }).found ||
      (direct as { packet: { job: JobRow } }).packet.job.id !== "direct"
    )
      throw new Error("draft-next direct packet failed");
    const applied = await jobsDraftNext({ stateDir, id: "applied" });
    if ((applied as { packet: { route: string } }).packet.route !== "application_followup")
      throw new Error("draft-next applied route failed");
    const automatic = await jobsDraftNext({ stateDir });
    if (
      !(automatic as { found: boolean }).found ||
      (automatic as { blockedApplications: number }).blockedApplications !== 2
    )
      throw new Error("draft-next did not report all blocked applications");
    const blockedDb = openDatabase(join(stateDir, "linkedin-tools.db"));
    blockedDb.database
      .prepare("UPDATE jobs SET message='existing' WHERE id IN ('direct','applied')")
      .run();
    blockedDb.database.close();
    const blockedAuto = await jobsDraftNext({ stateDir });
    if (
      (blockedAuto as { found: boolean }).found ||
      (blockedAuto as { blockedApplications: number }).blockedApplications !== 2
    )
      throw new Error("draft-next blocked application count failed");
    let blocked = false;
    try {
      await jobsDraftNext({ stateDir, id: "blocked" });
    } catch (error) {
      blocked = error instanceof CliError && error.code === "JOB_NOT_ELIGIBLE";
    }
    if (!blocked) throw new Error("draft-next accepted blocked application");
    let invalidId = false;
    try {
      parseInvocation(["jobs", "draft-next", "--id", ""], { now: new Date(), env: {} });
    } catch (error) {
      invalidId = error instanceof CliError;
    }
    if (!invalidId) throw new Error("draft-next accepted an empty id");
  }

  const calls: string[] = [];
  const fakeOperations: CliOperations = {
    salesnav: async () => ({ ok: true }),
    doctor: async () => ({ ready: true }),
    networkStatus: async () => {
      calls.push("network status");
      return { state: "not_started" };
    },
    networkReport: async () => {
      calls.push("network report");
      return { attempts: [] };
    },
    networkTick: async (input) => {
      if (input.batchSize !== 5 || input.maxRealSends !== 30 || input.sessionId !== "auto") {
        throw new Error("network smoke did not receive the final completion contract");
      }
      calls.push("network tick");
      return { state: "progress", sendsThisTick: 0 };
    },
    networkReconcile: async (input) => {
      if (input.sessionId !== "auto") throw new Error("network reconcile did not parse auto");
      calls.push("network reconcile");
      return { state: "progress" };
    },
    networkRunEnd: async (input) => {
      calls.push("network run-end");
      return { command: "network run-end", localDate: input.localDate, run: { status: "blocked" } };
    },
    networkSessionReset: async () => {
      calls.push("network session-reset");
      return { command: "network session-reset", reset: { ok: true } };
    },
    networkOpen: async (input) => {
      calls.push("network open");
      return { command: "network open", page: input.page, outcome: "succeeded" };
    },
    networkIncidentStatus: async () => {
      calls.push("network incident-status");
      return { active: false, incident: null };
    },
    networkIncidentClear: async () => {
      calls.push("network incident-clear");
      return { cleared: true };
    },
    analyticsExport: async (input) => {
      if (input.sessionId !== "auto") throw new Error("analytics export did not parse auto");
      calls.push("analytics export");
      return { status: "completed" };
    },
    migrationDryRun: async () => {
      calls.push("migration dry-run");
      return { proposalOnly: true };
    },
    jobsCaptureStart: async (input) => {
      if (input.runId !== "run-1") throw new Error("capture-start did not parse run id");
      calls.push("jobs capture-start");
      return { command: "jobs capture-start" };
    },
    jobsCaptureIngest: async (input) => {
      if (input.pageIdentity !== "page-1") throw new Error("capture-ingest did not parse page");
      calls.push("jobs capture-ingest");
      return { command: "jobs capture-ingest" };
    },
    jobsCaptureFinish: async (input) => {
      if (input.state !== "complete") throw new Error("capture-finish did not parse state");
      calls.push("jobs capture-finish");
      return { command: "jobs capture-finish" };
    },
    jobsNormalize: async (input) => {
      if (input.runId !== "run-1" || input.limit !== 2)
        throw new Error("normalize did not parse arguments");
      calls.push("jobs normalize");
      return { command: "jobs normalize" };
    },
    jobsFilter: async (input) => {
      if (
        input.runId !== "run-1" ||
        input.policyVersion !== "smoke-v1" ||
        input.maxAgeDays !== 14 ||
        input.terms.join(",") !== "product engineer"
      ) {
        throw new Error("jobs filter did not parse arguments");
      }
      calls.push("jobs filter");
      return { command: "jobs filter" };
    },
    jobsEnrichNext: async () => {
      calls.push("jobs enrich-next");
      return { found: false };
    },
    jobsEnrichRecord: async () => {
      calls.push("jobs enrich-record");
      return { outcome: "retry_required" };
    },
    jobsList: async () => {
      calls.push("jobs list");
      return { count: 0, jobs: [] };
    },
    jobsCheck: async () => {
      calls.push("jobs check");
      return { checked: 0, live: 0, dead: 0, unclear: 0 };
    },
    jobsFavorite: async () => {
      calls.push("jobs favorite");
      return { favorited: 0 };
    },
    jobsRemove: async () => {
      calls.push("jobs remove");
      return { removed: 0 };
    },
    jobsDraft: async (input) => {
      if (input.subject !== "Hi" || input.message !== "Body") {
        throw new Error("jobs draft did not parse --subject");
      }
      calls.push("jobs draft");
      return { job: { id: input.id } };
    },
    jobsDraftNext: async (input) => {
      if (input.id !== "111") throw new Error("jobs draft-next did not parse --id");
      calls.push("jobs draft-next");
      return { found: false, blockedApplications: 0 };
    },
    jobsApplicationNext: async (input) => {
      if (input.id !== "111") throw new Error("jobs application-next did not parse --id");
      calls.push("jobs application-next");
      return { found: false };
    },
    jobsApplied: async (input) => {
      if (input.id !== "111" || input.applicationUrl !== "https://example.com/apply") {
        throw new Error("jobs applied did not parse checkpoint");
      }
      calls.push("jobs applied");
      return { job: { id: input.id } };
    },
    jobsSendPrepare: async (input) => {
      if (!input.allowSend) throw new Error("jobs send-prepare did not require allow-send");
      calls.push("jobs send-prepare");
      return { packet: null };
    },
    jobsSendRecord: async () => {
      calls.push("jobs send-record");
      return { receipt: null };
    },
    jobsContractOutreachPrepare: async (input) => {
      if (!input.allowSend) throw new Error("contract outreach prepare did not require allow-send");
      calls.push("jobs contract-outreach-prepare");
      return { packet: null };
    },
    jobsContractOutreachRecord: async () => {
      calls.push("jobs contract-outreach-record");
      return { receipt: null };
    },
    jobsClassify: async () => {
      calls.push("jobs classify");
      return { job: { id: "111" } };
    },
    jobsTriageNext: async () => {
      calls.push("jobs triage-next");
      return { found: false };
    },
    jobsTriageRecord: async () => {
      calls.push("jobs triage-record");
      return { job: { id: "111" } };
    },
    jobsHubSpotNext: async (input) => {
      if (input.id !== "111") throw new Error("hubspot-next did not parse --id");
      calls.push("jobs hubspot-next");
      return { found: false, packet: null };
    },
    jobsHubSpotRecord: async (input) => {
      if (input.companyId !== "101" || input.associationsComplete !== true) {
        throw new Error("hubspot-record did not parse receipts");
      }
      calls.push("jobs hubspot-record");
      return { complete: false };
    },
    jobsInstantlyNext: async (input) => {
      if (input.id !== "111" || input.campaignId !== "campaign-1")
        throw new Error("instantly-next did not parse --id and --campaign-id");
      calls.push("jobs instantly-next");
      return { found: false };
    },
    jobsInstantlyRecord: async (input) => {
      if (input.prospectId !== "co:instantly:v1:x")
        throw new Error("instantly-record did not parse prospect");
      calls.push("jobs instantly-record");
      return { complete: false };
    },
    jobsFollowupNext: async (input) => {
      if (input.id !== "111") throw new Error("followup-next did not parse --id");
      calls.push("jobs followup-next");
      return {
        found: true,
        packet: {
          hubspot: {
            tasks: [
              { properties: { hs_task_status: "NOT_STARTED", hubspot_owner_id: "96636780" } },
            ],
          },
        },
      };
    },
    jobsFollowupRecord: async (input) => {
      if (input.prospectId !== `co:need-led:v1:${"x".repeat(64)}`)
        throw new Error("followup-record did not parse prospect");
      calls.push("jobs followup-record");
      return { complete: false };
    },
  };
  const common = {
    operations: fakeOperations,
    now: () => new Date("2026-08-03T12:00:00-03:00"),
    env: {
      HOME: root,
      LINKEDIN_TOOLS_ANALYTICS_ACCOUNT: "Hanif",
    },
  };
  await writeFile(join(root, "capture-payload.json"), JSON.stringify({ elements: [] }));
  await writeFile(join(root, "instantly-receipt.json"), JSON.stringify({ noEmail: true }));
  await writeFile(join(root, "followup-receipt.json"), JSON.stringify({ tasks: [] }));
  const commands: readonly (readonly string[])[] = [
    ["--json", "network", "status"],
    ["--json", "network", "report"],
    [
      "--json",
      "network",
      "tick",
      "--allow-send",
      "--batch-size",
      "5",
      "--max-real-sends",
      "30",
      "--session",
      "auto",
    ],
    ["--json", "network", "reconcile", "--session", "auto"],
    ["--json", "network", "session-reset"],
    [
      "--json",
      "analytics",
      "export",
      "--out",
      join(root, "analytics-{endDate}.xlsx"),
      "--download-root",
      join(root, "downloads"),
      "--period",
      "previous-7-days",
      "--session",
      "auto",
    ],
    ["--json", "migration", "dry-run", "--source-root", join(root, "legacy")],
    [
      "--json",
      "jobs",
      "capture-start",
      "--run-id",
      "run-1",
      "--source-url",
      "https://www.linkedin.com/jobs/search",
    ],
    [
      "--json",
      "jobs",
      "capture-ingest",
      "--run-id",
      "run-1",
      "--page",
      "page-1",
      "--payload",
      "-",
      "--source-url",
      "https://www.linkedin.com/jobs/search",
      "--response-url",
      "https://www.linkedin.com/voyager/api/voyagerJobsDashJobCards",
    ],
    ["--json", "jobs", "capture-finish", "--run-id", "run-1", "--state", "complete"],
    ["--json", "jobs", "normalize", "--run-id", "run-1", "--limit", "2"],
    [
      "--json",
      "jobs",
      "filter",
      "--run-id",
      "run-1",
      "--terms",
      '["product engineer"]',
      "--policy-version",
      "smoke-v1",
      "--max-age-days",
      "14",
    ],
    ["--json", "jobs", "triage-next", "--run-id", "run-1"],
    [
      "--json",
      "jobs",
      "triage-record",
      "--id",
      "111",
      "--bucket",
      "strong",
      "--company-summary",
      "Acme",
      "--work-summary",
      "Build it",
      "--responsibilities",
      '["Ship"]',
      "--skill-matches",
      '["TypeScript"]',
      "--skill-gaps",
      '["Unknown"]',
      "--reason",
      "Explicit anchors",
      "--policy-version",
      "jobs-triage-v1-20260819",
    ],
    [
      "--json",
      "jobs",
      "classify",
      "--id",
      "111",
      "--work-focus",
      "Infrastructure",
      "--product-system",
      "Salesforce",
      "--work-summary",
      "Build the core platform",
      "--product-summary",
      "Salesforce CPQ",
    ],
    ["--json", "jobs", "draft-next", "--id", "111"],
    ["--json", "jobs", "application-next", "--id", "111"],
    ["--json", "jobs", "draft", "--id", "111", "--subject", "Hi", "--message", "Body"],
    ["--json", "jobs", "applied", "--id", "111", "--application-url", "https://example.com/apply"],
    ["--json", "jobs", "send-prepare", "--allow-send", "--id", "111"],
    ["--json", "jobs", "send-record", "--payload", "-"],
    ["--json", "jobs", "contract-outreach-prepare", "--allow-send", "--id", "111"],
    ["--json", "jobs", "contract-outreach-record", "--payload", "-"],
    ["--json", "jobs", "hubspot-next", "--id", "111"],
    ["--json", "jobs", "instantly-next", "--id", "111", "--campaign-id", "campaign-1"],
    [
      "--json",
      "jobs",
      "instantly-record",
      "--prospect-id",
      "co:instantly:v1:x",
      "--payload",
      join(root, "instantly-receipt.json"),
    ],
    ["--json", "jobs", "followup-next", "--id", "111"],
    [
      "--json",
      "jobs",
      "followup-record",
      "--prospect-id",
      `co:need-led:v1:${"x".repeat(64)}`,
      "--payload",
      join(root, "followup-receipt.json"),
    ],
    [
      "--json",
      "jobs",
      "hubspot-record",
      "--prospect-id",
      `co:need-led:v1:${"a".repeat(64)}`,
      "--company-id",
      "101",
      "--associations-complete",
    ],
  ];
  for (const command of commands) {
    const output = await invoke(command, common);
    if (output.exitCode !== 0 || output.value?.ok !== true) {
      throw new Error(`smoke command failed: ${command.join(" ")}`);
    }
  }
  const denied = await invoke(["--json", "network", "tick"], common);
  if (denied.exitCode !== 3 || denied.value?.error?.code !== "SEND_NOT_AUTHORIZED") {
    throw new Error("send authorization smoke failed");
  }
  const jobsSendDenied = await invoke(["--json", "jobs", "send-prepare"], common);
  if (
    jobsSendDenied.exitCode !== 3 ||
    jobsSendDenied.value?.error?.code !== "SEND_NOT_AUTHORIZED"
  ) {
    throw new Error("jobs send authorization smoke failed");
  }
  const contractOutreachDenied = await invoke(
    ["--json", "jobs", "contract-outreach-prepare"],
    common,
  );
  if (
    contractOutreachDenied.exitCode !== 3 ||
    contractOutreachDenied.value?.error?.code !== "SEND_NOT_AUTHORIZED"
  ) {
    throw new Error("contract outreach authorization smoke failed");
  }
  const hubSpotReceiptDenied = await invoke(
    ["--json", "jobs", "hubspot-record", "--prospect-id", `co:need-led:v1:${"a".repeat(64)}`],
    common,
  );
  if (
    hubSpotReceiptDenied.exitCode !== 2 ||
    hubSpotReceiptDenied.value?.error?.code !== "INVALID_ARGUMENT"
  ) {
    throw new Error("HubSpot empty receipt validation smoke failed");
  }
  const classifyRejected = await invoke(
    [
      "--json",
      "jobs",
      "classify",
      "--id",
      "111",
      "--work-focus",
      "   ",
      "--product-system",
      "Salesforce",
      "--work-summary",
      "Build",
      "--product-summary",
      "Salesforce CPQ",
    ],
    common,
  );
  if (
    classifyRejected.exitCode !== 2 ||
    classifyRejected.value?.error?.code !== "INVALID_ARGUMENT"
  ) {
    throw new Error("classification validation smoke failed");
  }
  const summaryRejected = await invoke(
    [
      "--json",
      "jobs",
      "classify",
      "--id",
      "111",
      "--work-focus",
      "Infrastructure",
      "--product-system",
      "Salesforce",
      "--work-summary",
      "   ",
      "--product-summary",
      "Salesforce CPQ",
    ],
    common,
  );
  if (summaryRejected.exitCode !== 2 || summaryRejected.value?.error?.code !== "INVALID_ARGUMENT") {
    throw new Error("classification summary validation smoke failed");
  }
  // Chrome enrichment handoff: strict payloads, durable outcomes, idempotent retry, and run scope.
  {
    const stateDir = join(root, "enrichment");
    const db = openDatabase(join(stateDir, "linkedin-tools.db"));
    const engine = new JobsEngine(db.database);
    engine.storeCapturedJobs(
      [
        { id: "e1", title: "Role" },
        { id: "e2", title: "Other" },
      ],
      "2026-08-03T00:00:00Z",
    );
    engine.upsertJobs(
      [
        {
          id: "e1",
          title: "Role",
          company: "Acme",
          location: "Remote",
          postingUrl: "https://www.linkedin.com/jobs/view/e1/",
          hiringTeam: [],
          hasHiringTeam: false,
        },
        {
          id: "e2",
          title: "Other",
          company: "Acme",
          location: "Remote",
          postingUrl: "https://www.linkedin.com/jobs/view/e2/",
          hiringTeam: [],
          hasHiringTeam: false,
        },
      ],
      "2026-08-03T00:00:00Z",
    );
    db.database.prepare("UPDATE jobs SET fit='kept' WHERE id IN ('e1','e2')").run();
    db.database
      .prepare(
        "INSERT INTO capture_runs (id, source_url, started_at, updated_at, checkpoint_json) VALUES ('erun','https://example.test','2026-08-03','2026-08-03','{}')",
      )
      .run();
    db.database
      .prepare(
        "INSERT INTO job_observations (run_id,page_identity,job_id,observed_title,observed_at) VALUES ('erun','e1','e1','Role','2026-08-03')",
      )
      .run();
    db.database.close();
    const next = await jobsEnrichNext({ stateDir, runId: "erun" });
    if (!(next as { found: boolean }).found || (next as { job: JobRow }).job.id !== "e1")
      throw new Error("enrich-next run scope failed");
    const payload = {
      id: "e1",
      sourceUrl: "https://www.linkedin.com/jobs/view/e1/",
      outcome: "complete_no_hiring_team",
      title: "Role",
      company: "Acme",
      location: "Remote",
      postingUrl: "https://www.linkedin.com/jobs/view/e1/",
      description: "Full description",
      workplaceType: "Remote",
      employmentType: "Full-time",
      applyMethod: "Easy Apply",
      promoted: false,
      activelyReviewing: false,
      postedAt: "2 days ago",
      applicantCount: "10 applicants",
      benefits: [],
      hiringTeam: [],
      companyProfileUrl: "https://www.linkedin.com/company/acme",
      companyEvidence: ["Acme"],
      capturedAt: "2026-08-03T00:01:00Z",
      parserVersion: "jobs-chrome-enrichment-v1",
      externalApplicationUrl: "https://apply.example.test/e1",
      applicantTrackingSystem: "Greenhouse",
      geoId: "9001",
      rawResponses: [
        {
          component: "peopleWhoCanHelp",
          sourceUrl: "https://www.linkedin.com/jobs/view/e1/",
          responseUrl:
            "https://www.linkedin.com/voyager/api/flagship-web?componentId=peopleWhoCanHelp",
          status: 200,
          capturedAt: "2026-08-03T00:01:00Z",
          parserVersion: "jobs-chrome-enrichment-v2",
          body: "{}",
        },
      ],
      sourceEvidence: ["Role | Acme | Remote"],
    };
    const path = join(stateDir, "payload.json");
    const inconclusiveNoTeam = {
      ...payload,
      id: "e2",
      sourceUrl: "https://www.linkedin.com/jobs/view/e2/",
      postingUrl: "https://www.linkedin.com/jobs/view/e2/",
      title: "Other",
      rawResponses: payload.rawResponses.map((response) => ({
        ...response,
        sourceUrl: "https://www.linkedin.com/jobs/view/e2/",
        body: '0:{"props":{"textProps":{"children":["Meet the hiring team"]}}}',
      })),
    };
    await writeFile(path, JSON.stringify(inconclusiveNoTeam));
    try {
      await jobsEnrichRecord({ stateDir, payloadPath: path });
      throw new Error("inconclusive no-team outcome accepted");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "JOBS_ENRICHMENT_INVALID") throw error;
    }
    await writeFile(path, JSON.stringify(payload));
    const recorded = await jobsEnrichRecord({ stateDir, payloadPath: path });
    if ((recorded as { outcome: string }).outcome !== "complete_no_hiring_team")
      throw new Error("no-team outcome was not recorded");
    const repeated = await jobsEnrichRecord({ stateDir, payloadPath: path });
    if ((repeated as { job: JobRow }).job.enrichmentOutcome !== "complete_no_hiring_team")
      throw new Error("enrich-record was not idempotent");
    const persisted = openDatabase(join(stateDir, "linkedin-tools.db"));
    const rawCount = persisted.database
      .query<{ count: number }, [string]>(
        "SELECT COUNT(*) AS count FROM job_enrichment_responses WHERE job_id=?",
      )
      .get("e1")?.count;
    if (rawCount !== 1) throw new Error("raw enrichment response was not persisted");
    persisted.database.close();
    await writeFile(path, JSON.stringify({ ...payload, description: "different" }));
    try {
      await jobsEnrichRecord({ stateDir, payloadPath: path });
      throw new Error("conflicting replay accepted");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "JOBS_ENRICHMENT_CONFLICT") throw error;
    }
    const oversized = {
      ...payload,
      rawResponses: [
        {
          component: "document",
          sourceUrl: payload.sourceUrl,
          responseUrl: payload.sourceUrl,
          status: 200,
          capturedAt: payload.capturedAt,
          parserVersion: payload.parserVersion,
          body: "x".repeat(1_000_001),
        },
      ],
    };
    const tooMany = {
      ...payload,
      rawResponses: Array.from({ length: 5 }, (_, index) => ({
        component: "peopleWhoCanHelp",
        sourceUrl: payload.sourceUrl,
        responseUrl: `${payload.sourceUrl}${index}`,
        status: 200,
        capturedAt: payload.capturedAt,
        parserVersion: payload.parserVersion,
        body: "{}",
      })),
    };
    await writeFile(path, JSON.stringify(tooMany));
    try {
      await jobsEnrichRecord({ stateDir, payloadPath: path });
      throw new Error("too many raw responses accepted");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "INVALID_ARGUMENT") throw error;
    }
    await writeFile(path, JSON.stringify(oversized));
    try {
      await jobsEnrichRecord({ stateDir, payloadPath: path });
      throw new Error("oversized raw response accepted");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "INVALID_ARGUMENT") throw error;
    }
    const droppedDb = openDatabase(join(stateDir, "linkedin-tools.db"));
    droppedDb.database.prepare("UPDATE jobs SET fit='dropped' WHERE id='e2'").run();
    droppedDb.database.close();
    await writeFile(path, JSON.stringify({ ...payload, id: "e2" }));
    try {
      await jobsEnrichRecord({ stateDir, payloadPath: path });
      throw new Error("dropped job entered enrichment");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "JOB_NOT_ELIGIBLE") throw error;
    }
    const restoreDb = openDatabase(join(stateDir, "linkedin-tools.db"));
    restoreDb.database.prepare("UPDATE jobs SET fit='kept' WHERE id='e2'").run();
    restoreDb.database.close();
    const closed = {
      ...payload,
      id: "e2",
      sourceUrl: "https://www.linkedin.com/jobs/view/e2/?trk=foo",
      postingUrl: "",
      outcome: "closed",
      title: "",
      company: "",
      location: "",
      description: "",
    };
    await writeFile(path, JSON.stringify(closed));
    await jobsEnrichRecord({ stateDir, payloadPath: path });
    const check = openDatabase(join(stateDir, "linkedin-tools.db"));
    const rows = new JobsEngine(check.database);
    if (rows.requireJob("e2").enrichmentOutcome !== "closed")
      throw new Error("closed outcome was lost");
    check.database.close();
    await writeFile(
      path,
      JSON.stringify({ ...payload, id: "e1", sourceUrl: "https://bad.example/" }),
    );
    try {
      await jobsEnrichRecord({ stateDir, payloadPath: path });
      throw new Error("source mismatch accepted");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "JOBS_SOURCE_MISMATCH") throw error;
    }
  }
  const helperImport = new Function(
    "return import('./linkedin-jobs-chrome-helper.mjs')",
  ) as () => Promise<{
    captureDirectPage: (
      tab: unknown,
      sourceUrl: string,
      action: () => Promise<void>,
    ) => Promise<{ captured: readonly { component: string; body: string }[] }>;
    parseJobSnapshot: (
      snapshot: unknown,
      expected?: unknown,
    ) => {
      outcome: string;
      title: string;
      postedAt: string;
      description: string;
      hiringTeam: readonly { profileUrl: string }[];
      applicantCount: string;
      sourceEvidence: readonly string[];
      benefits: readonly string[];
      companyEvidence: readonly string[];
    };
    parseScopedRsc: (responses: readonly unknown[]) => {
      description: string;
      externalApplicationUrl: string;
      applicantTrackingSystem: string;
      geoId: string;
      hiringTeam: readonly { name: string; profileUrl: string; degree: string }[];
      companyEvidence: readonly string[];
      peopleHasUrls: boolean;
      peopleConclusiveEmpty: boolean;
    };
  }>;
  const { captureDirectPage, parseJobSnapshot, parseScopedRsc } = await helperImport();
  let fakeNavigated = false;
  const fakeBodies = new Map([
    ["doc", { body: "document" }],
    ["job-old", { body: "old" }],
    ["job-new", { body: "new" }],
    ["people", { body: "people" }],
    ["company", { body: "company" }],
  ]);
  const fakeEvents = [
    {
      method: "Network.responseReceived",
      params: {
        requestId: "doc",
        response: { url: "https://www.linkedin.com/jobs/view/1/", status: 200 },
      },
    },
    { method: "Network.loadingFinished", params: { requestId: "doc" } },
    {
      method: "Network.responseReceived",
      params: {
        requestId: "job-old",
        response: {
          url: "https://www.linkedin.com/voyager/api/flagship-web?componentId=aboutTheJob&parentSpanId=old",
          status: 200,
        },
      },
    },
    { method: "Network.loadingFinished", params: { requestId: "job-old" } },
    {
      method: "Network.responseReceived",
      params: {
        requestId: "job-new",
        response: {
          url: "https://www.linkedin.com/voyager/api/flagship-web?componentId=aboutTheJob&parentSpanId=new",
          status: 200,
        },
      },
    },
    { method: "Network.loadingFinished", params: { requestId: "job-new" } },
    {
      method: "Network.responseReceived",
      params: {
        requestId: "people",
        response: {
          url: "https://www.linkedin.com/voyager/api/flagship-web?componentId=peopleWhoCanHelp",
          status: 200,
        },
      },
    },
    { method: "Network.loadingFinished", params: { requestId: "people" } },
    {
      method: "Network.responseReceived",
      params: {
        requestId: "company",
        response: {
          url: "https://www.linkedin.com/voyager/api/flagship-web?componentId=aboutTheCompanyForJobDetails",
          status: 200,
        },
      },
    },
    { method: "Network.loadingFinished", params: { requestId: "company" } },
  ];
  const noisyEvents = [
    ...Array.from({ length: 250 }, (_, index) => ({
      method: "Network.requestWillBeSent",
      params: { requestId: `noise-${index}` },
    })),
    ...fakeEvents,
  ];
  let fakePoll = 0;
  let maxReadLimit = 0;
  const fakeCdp = {
    async send(method: string, args?: { requestId?: string }) {
      if (method === "Network.getResponseBody")
        return fakeBodies.get(args?.requestId ?? "") ?? { body: "" };
      return {};
    },
    async readEvents(args: { limit?: number }) {
      maxReadLimit = Math.max(maxReadLimit, args.limit ?? 0);
      fakePoll += 1;
      return { cursor: fakePoll, events: fakePoll === 2 ? noisyEvents : [], truncated: false };
    },
  };
  const captured = await captureDirectPage(
    { capabilities: { get: async () => fakeCdp } },
    "https://www.linkedin.com/jobs/view/1/",
    async () => {
      fakeNavigated = true;
    },
  );
  if (
    !fakeNavigated ||
    maxReadLimit !== 1000 ||
    captured.captured.filter((item: { component: string }) => item.component === "aboutTheJob")
      .length !== 1 ||
    captured.captured.find(
      (item: { component: string; body: string }) => item.component === "aboutTheJob",
    )?.body !== "new"
  )
    throw new Error("CDP same-component capture dedupe failed");
  const rscFixture = parseScopedRsc([
    {
      component: "aboutTheJob",
      body: '{"description":"Build durable systems for customers.","externalApplyUrl":"https://jobs.example/apply/1","applicantTrackingSystem":"Greenhouse","geoId":"123"}',
    },
    {
      component: "aboutTheCompanyForJobDetails",
      body: JSON.stringify({
        companyName: "Acme",
        industry: "Software",
        description: "x".repeat(2_000),
      }),
    },
    {
      component: "peopleWhoCanHelp",
      body: '{"title":"Meet the hiring team","name":"Jane Doe","profileUrl":"https://www.linkedin.com/in/jane-doe","degree":"2nd","headline":"Engineering Manager"}',
    },
  ]);
  if (
    rscFixture.description !== "Build durable systems for customers." ||
    rscFixture.externalApplicationUrl !== "https://jobs.example/apply/1" ||
    rscFixture.applicantTrackingSystem !== "Greenhouse" ||
    rscFixture.geoId !== "123" ||
    rscFixture.hiringTeam[0]?.profileUrl !== "https://linkedin.com/in/jane-doe" ||
    rscFixture.companyEvidence.some((value: string) => value.length > 1_000)
  )
    throw new Error("RSC enrichment parser failed");
  const flightFixture = parseScopedRsc([
    {
      component: "document",
      body: '0:{"offsiteApplyUrl":"https://apply.example/jobs/1","applicantTrackingSystemName":"Greenhouse","jobGeoId":"103644278"}\n1:{"props":{"textProps":{"children":["Senior Platform Engineer"]}}}',
    },
    {
      component: "aboutTheJob",
      body: '0:{"props":{"textProps":{"children":["About the job","Build reliable customer systems.","About the company"]}}}',
    },
    {
      component: "aboutTheCompanyForJobDetails",
      body: '0:{"props":{"textProps":{"children":["Acme","1,001-5,000 employees","12,000 followers","We build useful software."]}}}',
    },
    {
      component: "peopleWhoCanHelp",
      body: '0:{"props":{"textProps":{"children":["Meet the hiring team","https://www.linkedin.com/in/jane-doe","Jane Doe","• 2nd","Engineering Manager"]}}}',
    },
  ]);
  if (
    flightFixture.description !== "Build reliable customer systems." ||
    flightFixture.externalApplicationUrl !== "https://apply.example/jobs/1" ||
    flightFixture.applicantTrackingSystem !== "Greenhouse" ||
    flightFixture.geoId !== "103644278" ||
    flightFixture.hiringTeam[0]?.name !== "Jane Doe" ||
    flightFixture.hiringTeam[0]?.degree !== "2nd" ||
    flightFixture.companyEvidence.length < 3
  )
    throw new Error("real React Flight enrichment parser failed");
  const inconclusivePeople = parseScopedRsc([
    {
      component: "peopleWhoCanHelp",
      body: '0:{"props":{"textProps":{"children":["Meet the hiring team","https://www.linkedin.com/in/not-a-contact","People who can help"]}}}',
    },
  ]);
  if (
    !inconclusivePeople.peopleHasUrls ||
    inconclusivePeople.hiringTeam.length !== 0 ||
    inconclusivePeople.peopleConclusiveEmpty
  )
    throw new Error("inconclusive people response was treated as empty");
  const networkContactOnly = parseScopedRsc([
    {
      component: "peopleWhoCanHelp",
      body: '0:{"props":{"textProps":{"children":["People you can reach out to","https://www.linkedin.com/in/network-contact","Network Contact","• 2nd","Product Designer"]}}}',
    },
  ]);
  if (
    networkContactOnly.peopleHasUrls ||
    networkContactOnly.hiringTeam.length !== 0 ||
    !networkContactOnly.peopleConclusiveEmpty
  )
    throw new Error("general network contact was treated as a hiring-team contact");
  const emptyCompany = parseScopedRsc([{ component: "aboutTheCompanyForJobDetails", body: "{}" }]);
  if (emptyCompany.companyEvidence.length !== 0)
    throw new Error("empty company component erased fallback evidence");
  const fixture = parseJobSnapshot(
    {
      documentTitle:
        "Senior Software Engineer - Full Stack Developer - React, Node.js | Elsevier | LinkedIn",
      url: "https://www.linkedin.com/jobs/view/4452992815/?trk=foo",
      text: [
        "Senior Software Engineer - Full Stack Developer - React, Node.js",
        "Philadelphia, PA · 5 days ago · Over 100 applicants",
        "Promoted by hirer · Actively reviewing applicants",
        "On-site",
        "Full-time",
        "Easy Apply",
        "Meet the hiring team",
        "Jane Doe\n2nd\nEngineering Manager\nJob poster",
        "About the job",
        "Build software for researchers.",
        "Benefits found in job post",
        "Health insurance",
        "Set alert for similar jobs",
      ].join("\n"),
      team: [
        {
          profileUrl: "https://www.linkedin.com/in/jane-doe/?miniProfileUrn=1",
          innerText: "Jane Doe\n2nd\nEngineering Manager\nJob poster",
        },
      ],
    },
    { id: "4452992815", sourceUrl: "https://www.linkedin.com/jobs/view/4452992815/" },
  );
  if (
    fixture.outcome !== "complete_hiring_team" ||
    fixture.title === "" ||
    fixture.description !== "Build software for researchers." ||
    fixture.hiringTeam[0]?.profileUrl !== "https://linkedin.com/in/jane-doe/"
  )
    throw new Error("Chrome enrichment fixture parser regressed");
  const boundedFixture = parseJobSnapshot(
    {
      documentTitle: "Product Engineer | Acme | LinkedIn",
      url: "https://www.linkedin.com/jobs/view/4452992816/",
      text: [
        "Product Engineer",
        "United States · Reposted 1 day ago · 10 people clicked apply",
        "Remote",
        "Full-time",
        "About the job",
        "x".repeat(2_000),
        "Benefits found in job post",
        "y".repeat(600),
        "About the company",
        ...Array.from({ length: 30 }, (_, index) => `Company evidence ${index}`),
        "Set alert for similar jobs",
      ].join("\n"),
      team: [],
    },
    { id: "4452992816", sourceUrl: "https://www.linkedin.com/jobs/view/4452992816/" },
  );
  if (
    boundedFixture.postedAt !== "Reposted 1 day ago" ||
    boundedFixture.applicantCount !== "10 people clicked apply" ||
    !boundedFixture.sourceEvidence.includes("Remote") ||
    !boundedFixture.sourceEvidence.includes("Full-time") ||
    boundedFixture.sourceEvidence.some((value: string) => value.length > 1_000) ||
    boundedFixture.benefits.some((value: string) => value.length > 300) ||
    boundedFixture.companyEvidence.length > 20 ||
    boundedFixture.companyEvidence.some((value: string) => value.length > 1_000)
  )
    throw new Error("Chrome enrichment packet exceeded CLI bounds");
  if (calls.length !== commands.length) throw new Error("fake operation dispatch count mismatch");
  console.log(
    JSON.stringify({
      ok: true,
      data: {
        command: "smoke",
        doctor: "passed",
        fakeCommands: calls,
        liveBrowser: false,
        liveLinkedIn: false,
      },
    }),
  );
} finally {
  await rm(root, { recursive: true, force: true });
}

async function invoke(
  argv: readonly string[],
  dependencies:
    | {
        readonly operations: CliOperations;
        readonly now: () => Date;
        readonly env: Readonly<Record<string, string | undefined>>;
      }
    | undefined,
): Promise<{
  readonly exitCode: number;
  readonly value: {
    readonly ok?: boolean;
    readonly error?: { readonly code?: string };
  };
}> {
  const stdout: string[] = [];
  const stderr: string[] = [];
  const io: CliIo = {
    stdout: (value) => stdout.push(value),
    stderr: (value) => stderr.push(value),
  };
  const exitCode = await run(argv, { ...dependencies, io });
  if (stderr.length > 0) throw new Error(stderr.join("\n"));
  const value: unknown = JSON.parse(stdout.at(-1) ?? "null");
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("CLI smoke output was not a JSON object");
  }
  return { exitCode, value };
}
