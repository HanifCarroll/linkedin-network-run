import { readFileSync } from "node:fs";
import { join } from "node:path";
import { CliError } from "../core/errors.ts";
import { openDatabase } from "../db/database.ts";
import { JobsCaptureStore } from "../jobs/capture.ts";
import { prepareChromeSend, recordChromeSend } from "../jobs/chrome-send.ts";
import { prepareContractOutreach, recordContractOutreach } from "../jobs/contract-outreach.ts";
import { evidenceGaps } from "../jobs/filter.ts";
import { type FollowupRecordInput, JobsFollowupEngine } from "../jobs/followup.ts";
import { HubSpotImportEngine } from "../jobs/hubspot.ts";
import {
  buildCheckLivenessScript,
  buildCleanupTabsScript,
  filterRun,
  JobsEngine,
  JobsNormalizer,
  runJobsScript,
} from "../jobs/index.ts";
import { InstantlyHandoffEngine } from "../jobs/instantly.ts";
import type { JobsScriptOutcome } from "../jobs/playwriter.ts";
import type { JobEnrichment, JobEnrichmentResponse, JobRow } from "../jobs/types.ts";
import { TRIAGE_POLICY_VERSION } from "../jobs/types.ts";
import { PlaywriterClient } from "../playwriter/client.ts";
import { resolvePlaywriterSession, type SessionResolutionRequest } from "./sessions.ts";
import type {
  JobsApplicationNextInput,
  JobsAppliedInput,
  JobsCaptureFinishInput,
  JobsCaptureIngestInput,
  JobsCaptureStartInput,
  JobsCheckInput,
  JobsClassifyInput,
  JobsContractOutreachPrepareInput,
  JobsContractOutreachRecordInput,
  JobsDraftInput,
  JobsDraftNextInput,
  JobsEnrichNextInput,
  JobsEnrichRecordInput,
  JobsFavoriteInput,
  JobsFilterInput,
  JobsFollowupNextInput,
  JobsFollowupRecordInput,
  JobsHubSpotNextInput,
  JobsHubSpotRecordInput,
  JobsInstantlyNextInput,
  JobsInstantlyRecordInput,
  JobsListInput,
  JobsNormalizeInput,
  JobsRemoveInput,
  JobsSendPrepareInput,
  JobsSendRecordInput,
  JobsTriageNextInput,
  JobsTriageRecordInput,
} from "./types.ts";

export type JobsDependencies = {
  readonly resolveSession?: (request: SessionResolutionRequest) => Promise<number>;
  readonly runScript?: typeof runJobsScript;
  readonly resetSession?: (sessionId: number) => Promise<void>;
  readonly now?: () => string;
};

const nowDefault = () => new Date().toISOString();

const defaultDependencies: JobsDependencies = {
  resolveSession: resolvePlaywriterSession,
  runScript: runJobsScript,
  now: nowDefault,
};

const defaultResetSession = (input: {
  readonly stateDir: string;
  readonly playwriterBin: string;
}): ((sessionId: number) => Promise<void>) => {
  const client = new PlaywriterClient({
    executable: input.playwriterBin,
    invocationRoot: join(input.stateDir, "receipts", "playwriter", "jobs"),
    stateDir: input.stateDir,
  });
  return async (sessionId: number) => {
    await client.resetSession(sessionId);
  };
};

export async function jobsCaptureStart(
  input: JobsCaptureStartInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const run = new JobsCaptureStore(opened.database).startRun({
      id: input.runId,
      sourceUrl: input.sourceUrl,
      ...(input.searchConfigJson === undefined ? {} : { searchConfigJson: input.searchConfigJson }),
      ...(input.checkpointJson === undefined ? {} : { checkpointJson: input.checkpointJson }),
      now: now(),
    });
    return { command: "jobs capture-start", run };
  } finally {
    opened.database.close();
  }
}

export async function jobsCaptureIngest(
  input: JobsCaptureIngestInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  let payload: string;
  try {
    payload =
      input.payloadPath === "-" ? readFileSync(0, "utf8") : readFileSync(input.payloadPath, "utf8");
  } catch {
    throw new CliError("INVALID_ARGUMENT", `cannot read capture payload ${input.payloadPath}`, {
      exitCode: 2,
    });
  }
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const result = new JobsCaptureStore(opened.database).ingestPage({
      runId: input.runId,
      pageIdentity: input.pageIdentity,
      payloadText: payload,
      sourceUrl: input.sourceUrl,
      responseUrl: input.responseUrl,
      ...(input.cursor === undefined ? {} : { cursor: input.cursor }),
      capturedAt: input.capturedAt ?? now(),
    });
    return { command: "jobs capture-ingest", ...result };
  } finally {
    opened.database.close();
  }
}

export async function jobsCaptureFinish(
  input: JobsCaptureFinishInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const run = new JobsCaptureStore(opened.database).finishRun({
      id: input.runId,
      state: input.state,
      ...(input.checkpointJson === undefined ? {} : { checkpointJson: input.checkpointJson }),
      ...(input.error === undefined ? {} : { error: input.error }),
      now: now(),
    });
    return { command: "jobs capture-finish", run };
  } finally {
    opened.database.close();
  }
}

export async function jobsNormalize(
  input: JobsNormalizeInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    return new JobsNormalizer(opened.database).normalize({
      runId: input.runId,
      ...(input.limit === undefined ? {} : { limit: input.limit }),
      now: (dependencies.now ?? nowDefault)(),
    });
  } finally {
    opened.database.close();
  }
}

export async function jobsFilter(
  input: JobsFilterInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    return filterRun(opened.database, {
      runId: input.runId,
      terms: input.terms,
      policyVersion: input.policyVersion,
      ...(input.maxAgeDays === undefined ? {} : { maxAgeDays: input.maxAgeDays }),
      now: (dependencies.now ?? nowDefault)(),
    });
  } finally {
    opened.database.close();
  }
}

export async function jobsEnrichNext(input: JobsEnrichNextInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const engine = new JobsEngine(opened.database);
    const ids = input.runId === undefined ? undefined : runJobIds(opened.database, input.runId);
    const jobs = engine
      .listJobs({
        withHiringTeam: false,
        fit: "kept",
        ...(ids === undefined ? {} : { jobIds: ids }),
      })
      .filter(
        (job) => job.enrichmentOutcome === "retry_required" && job.triageBucket === "pending",
      );
    const selected = input.id === undefined ? jobs[0] : jobs.find((job) => job.id === input.id);
    if (input.id !== undefined && selected === undefined)
      throw new CliError(
        "JOB_NOT_ELIGIBLE",
        `job ${input.id} is not an eligible kept job in the requested run`,
        { exitCode: 2 },
      );
    return {
      command: "jobs enrich-next",
      found: selected !== undefined,
      ...(selected === undefined ? {} : { job: selected, sourceUrl: selected.postingUrl }),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsEnrichRecord(
  input: JobsEnrichRecordInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  let payload: unknown;
  try {
    payload = JSON.parse(
      input.payloadPath === "-" ? readFileSync(0, "utf8") : readFileSync(input.payloadPath, "utf8"),
    );
  } catch {
    throw new CliError("INVALID_ARGUMENT", "enrichment payload must be valid JSON", {
      exitCode: 2,
    });
  }
  const enrichment = parseEnrichment(payload);
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const job = new JobsEngine(opened.database).recordEnrichment(
      enrichment,
      (dependencies.now ?? nowDefault)(),
    );
    return { command: "jobs enrich-record", outcome: enrichment.outcome, job };
  } finally {
    opened.database.close();
  }
}

function parseEnrichment(value: unknown): JobEnrichment {
  if (!isRecord(value))
    throw new CliError("INVALID_ARGUMENT", "enrichment payload must be a JSON object", {
      exitCode: 2,
    });
  const allowed = new Set([
    "id",
    "sourceUrl",
    "outcome",
    "title",
    "company",
    "location",
    "postingUrl",
    "description",
    "workplaceType",
    "employmentType",
    "applyMethod",
    "promoted",
    "activelyReviewing",
    "postedAt",
    "applicantCount",
    "benefits",
    "hiringTeam",
    "companyProfileUrl",
    "companyEvidence",
    "externalApplicationUrl",
    "applicantTrackingSystem",
    "geoId",
    "rawResponses",
    "capturedAt",
    "parserVersion",
    "sourceEvidence",
  ]);
  const unknown = Object.keys(value).find((key) => !allowed.has(key));
  if (unknown !== undefined)
    throw new CliError("INVALID_ARGUMENT", `unknown enrichment field ${unknown}`, { exitCode: 2 });
  const text = (key: string, max: number): string => {
    const item = value[key];
    if (typeof item !== "string" || item.trim().length === 0 || item.length > max)
      throw new CliError(
        "INVALID_ARGUMENT",
        `${key} must be a non-empty string of at most ${max} characters`,
        { exitCode: 2 },
      );
    return item.trim();
  };
  const optionalText = (key: string, max: number) =>
    typeof value[key] === "string" ? String(value[key]).trim().slice(0, max) : "";
  const list = (key: string, maxItems: number, maxText: number): string[] => {
    const item = value[key];
    if (
      !Array.isArray(item) ||
      item.length > maxItems ||
      item.some((x) => typeof x !== "string" || x.length > maxText || x.trim() === "")
    )
      throw new CliError("INVALID_ARGUMENT", `${key} must be a bounded array of strings`, {
        exitCode: 2,
      });
    return item.map((x) => String(x).trim());
  };
  const outcome = text("outcome", 40) as JobEnrichment["outcome"];
  if (
    !["complete_hiring_team", "complete_no_hiring_team", "retry_required", "closed"].includes(
      outcome,
    )
  )
    throw new CliError("INVALID_ARGUMENT", "invalid enrichment outcome", { exitCode: 2 });
  const hiringTeamRaw = value.hiringTeam;
  if (!Array.isArray(hiringTeamRaw) || hiringTeamRaw.length > 50)
    throw new CliError("INVALID_ARGUMENT", "hiringTeam must be an array of at most 50 objects", {
      exitCode: 2,
    });
  const hiringTeam = hiringTeamRaw.map((member) => {
    if (
      !isRecord(member) ||
      Object.keys(member).some((key) => !["name", "profileUrl", "degree", "headline"].includes(key))
    )
      throw new CliError("INVALID_ARGUMENT", "invalid hiringTeam member", { exitCode: 2 });
    return {
      name: textFrom(member, "name", 200),
      profileUrl: textFrom(member, "profileUrl", 500),
      degree: textFrom(member, "degree", 20, true),
      headline: textFrom(member, "headline", 300, true),
    };
  });
  if (
    hiringTeam.some(
      (member) => !/^https:\/\/(?:www\.)?linkedin\.com\/in\//i.test(member.profileUrl),
    )
  )
    throw new CliError(
      "INVALID_ARGUMENT",
      "hiring-team profileUrl must be a LinkedIn profile URL",
      { exitCode: 2 },
    );
  const closed = outcome === "closed";
  const rawResponses = value.rawResponses === undefined ? [] : value.rawResponses;
  if (!Array.isArray(rawResponses) || rawResponses.length > 4)
    throw new CliError("INVALID_ARGUMENT", "rawResponses must contain at most four responses", {
      exitCode: 2,
    });
  const allowedComponents = new Set([
    "document",
    "aboutTheJob",
    "aboutTheCompanyForJobDetails",
    "peopleWhoCanHelp",
  ]);
  const seenComponents = new Set<string>();
  const parsedRawResponses = rawResponses.map((item) => {
    if (
      !isRecord(item) ||
      Object.keys(item).some(
        (key) =>
          ![
            "component",
            "sourceUrl",
            "responseUrl",
            "status",
            "capturedAt",
            "parserVersion",
            "body",
          ].includes(key),
      )
    )
      throw new CliError("INVALID_ARGUMENT", "invalid raw response", { exitCode: 2 });
    const component = textFrom(item, "component", 60);
    if (seenComponents.has(component))
      throw new CliError("INVALID_ARGUMENT", "duplicate raw response component", { exitCode: 2 });
    seenComponents.add(component);
    const body = textFrom(item, "body", component === "document" ? 1_000_000 : 120_000);
    const bodyBytes = new TextEncoder().encode(body).byteLength;
    if (bodyBytes > (component === "document" ? 1_000_000 : 120_000))
      throw new CliError("INVALID_ARGUMENT", "raw response body exceeds byte limit", {
        exitCode: 2,
      });
    if (
      !allowedComponents.has(component) ||
      typeof item.status !== "number" ||
      !Number.isInteger(item.status) ||
      item.status < 100 ||
      item.status > 599
    )
      throw new CliError("INVALID_ARGUMENT", "invalid raw response component or status", {
        exitCode: 2,
      });
    return {
      component,
      sourceUrl: textFrom(item, "sourceUrl", 1000),
      responseUrl: textFrom(item, "responseUrl", 2000),
      status: item.status,
      capturedAt: textFrom(item, "capturedAt", 100),
      parserVersion: textFrom(item, "parserVersion", 100),
      body,
    } as JobEnrichmentResponse;
  });
  return {
    id: text("id", 200),
    sourceUrl: text("sourceUrl", 1000),
    outcome,
    title: closed ? optionalText("title", 300) : text("title", 300),
    company: closed ? optionalText("company", 300) : text("company", 300),
    location: closed ? optionalText("location", 300) : text("location", 300),
    postingUrl: closed
      ? optionalText("postingUrl", 1000) || text("sourceUrl", 1000)
      : text("postingUrl", 1000),
    description:
      outcome === "complete_hiring_team" || outcome === "complete_no_hiring_team"
        ? text("description", 50_000)
        : optionalText("description", 50_000),
    workplaceType: optionalText("workplaceType", 100),
    employmentType: optionalText("employmentType", 100),
    applyMethod: optionalText("applyMethod", 100),
    promoted: value.promoted === true,
    activelyReviewing: value.activelyReviewing === true,
    postedAt: optionalText("postedAt", 100),
    applicantCount: optionalText("applicantCount", 100),
    benefits: list("benefits", 50, 300),
    hiringTeam,
    companyProfileUrl: optionalText("companyProfileUrl", 1000),
    companyEvidence: list("companyEvidence", 20, 1000),
    externalApplicationUrl: optionalText("externalApplicationUrl", 2000),
    applicantTrackingSystem: optionalText("applicantTrackingSystem", 200),
    geoId: optionalText("geoId", 100),
    rawResponses: parsedRawResponses,
    capturedAt: text("capturedAt", 100),
    parserVersion: text("parserVersion", 100),
    sourceEvidence: list("sourceEvidence", 50, 1000),
  };
}
function textFrom(value: Record<string, unknown>, key: string, max: number, empty = false): string {
  const item = value[key];
  if (typeof item !== "string" || item.length > max || (!empty && item.trim() === ""))
    throw new CliError("INVALID_ARGUMENT", `invalid ${key}`, { exitCode: 2 });
  return item.trim();
}

function runJobIds(database: import("bun:sqlite").Database, runId: string): string[] {
  return database
    .query<{ job_id: string }, [string]>(
      "SELECT DISTINCT job_id FROM job_observations WHERE run_id = ? ORDER BY job_id",
    )
    .all(runId)
    .map((row) => row.job_id);
}

function isSessionFailure(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /fetch failed|session lost|extension not connected|context was destroyed|page closed|relay|disconnect|econn|timed out|execution timeout/i.test(
    message,
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function jobsList(input: JobsListInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const rows = new JobsEngine(opened.database).listJobs({
      ...(input.status === undefined ? {} : { status: input.status }),
      withHiringTeam: input.withHiringTeam,
    });
    return { command: "jobs list", count: rows.length, jobs: rows };
  } finally {
    opened.database.close();
  }
}

export async function jobsCheck(
  input: JobsCheckInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });
  const resetSession = dependencies.resetSession ?? defaultResetSession(input);
  const dbPath = join(input.stateDir, "linkedin-tools.db");
  const now = dependencies.now ?? nowDefault;
  const batchSize = 8;
  const runPhase = async (script: string, timeoutMs: number): Promise<JobsScriptOutcome | null> => {
    try {
      return await runScript({
        playwriterBin: input.playwriterBin,
        sessionId,
        script,
        timeoutMs,
        stateDir: input.stateDir,
      });
    } catch (error) {
      if (isSessionFailure(error)) {
        await resetSession(sessionId).catch(() => {});
        return null;
      }
      throw error;
    }
  };
  const matchingRows = (): JobRow[] => {
    const opened = openDatabase(dbPath);
    try {
      return new JobsEngine(opened.database).listJobs({
        ...(input.status === undefined ? {} : { status: input.status }),
        withHiringTeam: input.withHiringTeam,
        uncheckedOnly: true,
      });
    } finally {
      opened.database.close();
    }
  };
  const removeDead = (ids: string[]): void => {
    if (ids.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).deleteJobs(ids);
    } finally {
      opened.database.close();
    }
  };
  const markChecked = (ids: string[]): void => {
    if (ids.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).markChecked(ids, now());
    } finally {
      opened.database.close();
    }
  };

  try {
    const cleanup = buildCleanupTabsScript();
    await runPhase(cleanup.script, cleanup.timeoutMs);
  } catch {
    // best-effort; the check loop surfaces real session failures
  }

  const pool = matchingRows();
  const toCheck = input.limit === undefined ? pool : pool.slice(0, input.limit);
  let checked = 0;
  let live = 0;
  let dead = 0;
  let unclear = 0;
  for (let i = 0; i < toCheck.length; i += batchSize) {
    const batch = toCheck.slice(i, i + batchSize).map((r) => ({ id: r.id }));
    const { script, timeoutMs } = buildCheckLivenessScript({ jobs: batch });
    const result = await runPhase(script, timeoutMs);
    if (result === null) break;
    const rows = Array.isArray(result.data?.checked) ? result.data.checked : [];
    const deadIds: string[] = [];
    const liveIds: string[] = [];
    for (const row of rows) {
      if (!isRecord(row)) continue;
      checked += 1;
      if (typeof row.error === "string" && row.error !== "") {
        unclear += 1;
        continue;
      }
      if (row.live === true) {
        live += 1;
        liveIds.push(String(row.id));
        continue;
      }
      dead += 1;
      deadIds.push(String(row.id));
    }
    removeDead(deadIds);
    markChecked(liveIds);
  }
  return { command: "jobs check", checked, live, dead, unclear };
}

export async function jobsFavorite(
  input: JobsFavoriteInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const rows = new JobsEngine(opened.database).favoriteJobs(input.ids, now());
    return { command: "jobs favorite", favorited: rows.length, jobs: rows };
  } finally {
    opened.database.close();
  }
}

export async function jobsRemove(input: JobsRemoveInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const removed = new JobsEngine(opened.database).deleteJobs(input.ids);
    return { command: "jobs remove", removed };
  } finally {
    opened.database.close();
  }
}

export async function jobsApplied(
  input: JobsAppliedInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const job = new JobsEngine(opened.database).recordApplied(
      input.id,
      input.applicationUrl,
      input.appliedAt,
      (dependencies.now ?? nowDefault)(),
    );
    return { command: "jobs applied", job };
  } finally {
    opened.database.close();
  }
}

export async function jobsApplicationNext(input: JobsApplicationNextInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const result = new JobsEngine(opened.database).applicationNext(input.id);
    return {
      command: "jobs application-next",
      found: result.packet !== null,
      ...(result.packet === null ? {} : { packet: result.packet }),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsDraftNext(input: JobsDraftNextInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const result = new JobsEngine(opened.database).draftNext(input.id);
    return {
      command: "jobs draft-next",
      found: result.packet !== null,
      blockedApplications: result.blockedApplications,
      ...(result.packet === null ? {} : { packet: result.packet }),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsDraft(
  input: JobsDraftInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const row = new JobsEngine(opened.database).storeDraft(
      input.id,
      input.subject,
      input.message,
      now(),
      input.connectionNote,
    );
    return { command: "jobs draft", job: row };
  } finally {
    opened.database.close();
  }
}

export async function jobsClassify(
  input: JobsClassifyInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const row = new JobsEngine(opened.database).classifyJob(
      input.id,
      input.workFocus,
      input.productSystem,
      input.workSummary,
      input.productSummary,
      now(),
    );
    return { command: "jobs classify", job: row };
  } finally {
    opened.database.close();
  }
}

export async function jobsTriageNext(input: JobsTriageNextInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const job = new JobsEngine(opened.database).triageNext(input.runId);
    return {
      command: "jobs triage-next",
      found: job !== null,
      ...(job === null
        ? {}
        : {
            packet: {
              rubric: {
                policyVersion: TRIAGE_POLICY_VERSION,
                anchors: [
                  "customer-facing software",
                  "full-stack product work",
                  "TypeScript/React/Node",
                  "integrations/automation/applied AI",
                  "end-to-end production ownership",
                ],
                definitions: {
                  strong: "multiple explicit anchors and the central work aligns",
                  possible: "partial alignment or material uncertainty",
                  weak: "little explicit evidence of those anchors or the central work appears elsewhere; still reviewable",
                },
              },
              job,
              evidenceGaps: evidenceGaps(job),
            },
          }),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsTriageRecord(
  input: JobsTriageRecordInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const job = new JobsEngine(opened.database).recordTriage({
      ...input,
      now: (dependencies.now ?? nowDefault)(),
    });
    return { command: "jobs triage-record", job };
  } finally {
    opened.database.close();
  }
}

export async function jobsHubSpotNext(
  input: JobsHubSpotNextInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const packet = new HubSpotImportEngine(opened.database).next(input.id, now());
    return { command: "jobs hubspot-next", found: packet !== null, packet };
  } finally {
    opened.database.close();
  }
}

export async function jobsFollowupNext(input: JobsFollowupNextInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const packet = new JobsFollowupEngine(opened.database).next(input.id);
    return { command: "jobs followup-next", found: packet !== null, packet };
  } finally {
    opened.database.close();
  }
}

export async function jobsFollowupRecord(
  input: JobsFollowupRecordInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  let payload: unknown;
  try {
    payload = JSON.parse(
      input.payloadPath === "-" ? readFileSync(0, "utf8") : readFileSync(input.payloadPath, "utf8"),
    );
  } catch {
    throw new CliError("INVALID_ARGUMENT", "follow-up receipt payload must be valid JSON", {
      exitCode: 2,
    });
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload))
    throw new CliError("INVALID_ARGUMENT", "follow-up receipt payload must be an object", {
      exitCode: 2,
    });
  const tasks = (payload as Record<string, unknown>).tasks;
  if (
    !Array.isArray(tasks) ||
    tasks.some((task) => task === null || typeof task !== "object" || Array.isArray(task))
  )
    throw new CliError(
      "FOLLOWUP_TASKS_INVALID",
      "payload.tasks must be an array of task receipts",
      { exitCode: 2 },
    );
  const parsed = tasks.map((task) => {
    const value = task as Record<string, unknown>;
    const associations = value.associations;
    if (
      typeof value.stage !== "string" ||
      typeof value.taskId !== "string" ||
      value.associationsComplete !== true ||
      associations === null ||
      typeof associations !== "object" ||
      Array.isArray(associations)
    )
      throw new CliError(
        "FOLLOWUP_TASKS_INVALID",
        "each task needs stage, taskId, associations, and associationsComplete=true",
        { exitCode: 2 },
      );
    const ids = associations as Record<string, unknown>;
    if (
      typeof ids.companyId !== "string" ||
      typeof ids.contactId !== "string" ||
      typeof ids.dealId !== "string"
    )
      throw new CliError(
        "FOLLOWUP_TASKS_INVALID",
        "each task needs companyId, contactId, and dealId associations",
        { exitCode: 2 },
      );
    return {
      stage: value.stage,
      taskId: value.taskId,
      associationsComplete: true as const,
      associations: { companyId: ids.companyId, contactId: ids.contactId, dealId: ids.dealId },
    };
  });
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const receipt = new JobsFollowupEngine(opened.database).record(
      { prospectId: input.prospectId, tasks: parsed } satisfies FollowupRecordInput,
      (dependencies.now ?? nowDefault)(),
    );
    return { command: "jobs followup-record", receipt };
  } finally {
    opened.database.close();
  }
}

export async function jobsInstantlyNext(
  input: JobsInstantlyNextInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const packet = new InstantlyHandoffEngine(opened.database).next(
      input.id,
      input.campaignId,
      (dependencies.now ?? nowDefault)(),
    );
    return { command: "jobs instantly-next", found: packet !== null, packet };
  } finally {
    opened.database.close();
  }
}

export async function jobsInstantlyRecord(
  input: JobsInstantlyRecordInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  let payload: unknown;
  try {
    payload = JSON.parse(
      input.payloadPath === "-" ? readFileSync(0, "utf8") : readFileSync(input.payloadPath, "utf8"),
    );
  } catch {
    throw new CliError("INVALID_ARGUMENT", "Instantly receipt payload must be valid JSON", {
      exitCode: 2,
    });
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload))
    throw new CliError("INVALID_ARGUMENT", "Instantly receipt payload must be an object", {
      exitCode: 2,
    });
  const p = payload as Record<string, unknown>;
  const allowed = new Set([
    "email",
    "emails",
    "noEmail",
    "campaignId",
    "leadId",
    "enrichmentId",
    "campaignStopOnReply",
    "error",
  ]);
  if (Object.keys(p).some((key) => !allowed.has(key)))
    throw new CliError("INVALID_ARGUMENT", "Instantly receipt contains an unknown field", {
      exitCode: 2,
    });
  const emails = p.emails;
  if (
    emails !== undefined &&
    (!Array.isArray(emails) || emails.length !== 1 || typeof emails[0] !== "string")
  )
    throw new CliError("INSTANTLY_AMBIGUOUS_EMAIL", "receipt must contain zero or one work email", {
      exitCode: 2,
    });
  if (typeof p.email === "string" && emails !== undefined)
    throw new CliError("INSTANTLY_AMBIGUOUS_EMAIL", "use email or emails, not both", {
      exitCode: 2,
    });
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    return {
      command: "jobs instantly-record",
      receipt: new InstantlyHandoffEngine(opened.database).record(
        {
          prospectId: input.prospectId,
          ...(typeof p.email === "string"
            ? { email: p.email }
            : Array.isArray(emails)
              ? { email: emails[0] as string }
              : {}),
          ...(p.noEmail === true ? { noEmail: true as const } : {}),
          ...(typeof p.campaignId === "string" ? { campaignId: p.campaignId } : {}),
          ...(typeof p.leadId === "string" ? { leadId: p.leadId } : {}),
          ...(typeof p.enrichmentId === "string" ? { enrichmentId: p.enrichmentId } : {}),
          ...(typeof p.campaignStopOnReply === "boolean"
            ? { campaignStopOnReply: p.campaignStopOnReply }
            : {}),
          ...(typeof p.error === "string" ? { error: p.error } : {}),
        },
        (dependencies.now ?? nowDefault)(),
      ),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsHubSpotRecord(
  input: JobsHubSpotRecordInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const receipt = new HubSpotImportEngine(opened.database).record(input, now());
    return {
      command: "jobs hubspot-record",
      complete: receipt.completedAt !== null,
      receipt,
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsSendPrepare(
  input: JobsSendPrepareInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    return {
      command: "jobs send-prepare",
      packet: prepareChromeSend(opened.database, input.id, (dependencies.now ?? nowDefault)()),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsContractOutreachPrepare(
  input: JobsContractOutreachPrepareInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    return {
      command: "jobs contract-outreach-prepare",
      packet: prepareContractOutreach(
        opened.database,
        input.id,
        (dependencies.now ?? nowDefault)(),
      ),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsContractOutreachRecord(
  input: JobsContractOutreachRecordInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  let payload: unknown;
  try {
    payload = JSON.parse(
      input.payloadPath === "-" ? readFileSync(0, "utf8") : readFileSync(input.payloadPath, "utf8"),
    );
  } catch {
    throw new CliError("INVALID_ARGUMENT", "contract outreach record payload must be valid JSON", {
      exitCode: 2,
    });
  }
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    return {
      command: "jobs contract-outreach-record",
      receipt: recordContractOutreach(opened.database, payload, (dependencies.now ?? nowDefault)()),
    };
  } finally {
    opened.database.close();
  }
}

export async function jobsSendRecord(
  input: JobsSendRecordInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  let payload: unknown;
  try {
    payload = JSON.parse(
      input.payloadPath === "-" ? readFileSync(0, "utf8") : readFileSync(input.payloadPath, "utf8"),
    );
  } catch {
    throw new CliError("INVALID_ARGUMENT", "send record payload must be valid JSON", {
      exitCode: 2,
    });
  }
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    return {
      command: "jobs send-record",
      receipt: recordChromeSend(opened.database, payload, (dependencies.now ?? nowDefault)()),
    };
  } finally {
    opened.database.close();
  }
}
