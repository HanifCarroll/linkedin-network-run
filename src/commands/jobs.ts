import { readFileSync } from "node:fs";
import { join } from "node:path";
import { CliError } from "../core/errors.ts";
import { openDatabase } from "../db/database.ts";
import { JobsCaptureStore } from "../jobs/capture.ts";
import { HubSpotImportEngine } from "../jobs/hubspot.ts";
import {
  buildCheckLivenessScript,
  buildCleanupTabsScript,
  buildDetailScript,
  buildEnrichPoolScript,
  buildSendScript,
  filterRun,
  JobsEngine,
  JobsNormalizer,
  recipientProfileUrl,
  runJobsScript,
} from "../jobs/index.ts";
import type { JobsScriptOutcome } from "../jobs/playwriter.ts";
import type { CollectedJob, JobDetail, JobRow } from "../jobs/types.ts";
import { PlaywriterClient } from "../playwriter/client.ts";
import { resolvePlaywriterSession, type SessionResolutionRequest } from "./sessions.ts";
import type {
  JobsCaptureFinishInput,
  JobsCaptureIngestInput,
  JobsCaptureStartInput,
  JobsCheckInput,
  JobsClassifyInput,
  JobsDetailInput,
  JobsDraftInput,
  JobsEnrichInput,
  JobsFavoriteInput,
  JobsFilterInput,
  JobsHubSpotNextInput,
  JobsHubSpotRecordInput,
  JobsListInput,
  JobsNormalizeInput,
  JobsRemoveInput,
  JobsSendInput,
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

export async function jobsEnrich(
  input: JobsEnrichInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const now = dependencies.now ?? nowDefault;
  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });
  const resetSession = dependencies.resetSession ?? defaultResetSession(input);
  const dbPath = join(input.stateDir, "linkedin-tools.db");
  const batchSize = 3;
  // Stay well under Chrome's ~5-minute MV3 single-event termination: a soft
  // 3-minute budget plus one in-flight batch keeps each invocation below the
  // limit that reclaims the extension service worker mid-drain.
  const BUDGET_MS = 180_000;
  const startedAt = Date.now();
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
  const capturedRows = (): JobRow[] => {
    const opened = openDatabase(dbPath);
    try {
      return new JobsEngine(opened.database).listJobs({
        status: "captured",
        withHiringTeam: false,
        fit: "kept",
        ...(input.runId === undefined ? {} : { jobIds: runJobIds(opened.database, input.runId) }),
      });
    } finally {
      opened.database.close();
    }
  };
  const upsert = (jobs: CollectedJob[]): void => {
    if (jobs.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).upsertJobs(jobs, now());
    } finally {
      opened.database.close();
    }
  };
  const deleteJobs = (ids: string[]): void => {
    if (ids.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).deleteJobs(ids);
    } finally {
      opened.database.close();
    }
  };

  // Trim accumulated blank automation tabs before draining: MV3 service
  // workers get reclaimed under tab-buildup memory pressure, so this reduces
  // reconnect-drop frequency. Best-effort; the enrich loop surfaces the real
  // session failure if the bridge is already down.
  try {
    const cleanup = buildCleanupTabsScript();
    await runPhase(cleanup.script, cleanup.timeoutMs);
  } catch {
    // ignore — enrichment below reports the actual failure
  }

  const pool = capturedRows();
  const toEnrich = input.limit === undefined ? pool : pool.slice(0, input.limit);
  let enriched = 0;
  for (let i = 0; i < toEnrich.length && Date.now() - startedAt < BUDGET_MS; i += batchSize) {
    const batch = toEnrich.slice(i, i + batchSize).map((r) => ({ id: r.id, title: r.title }));
    const { script, timeoutMs } = buildEnrichPoolScript({ jobs: batch });
    const result = await runPhase(script, timeoutMs);
    if (result === null) break;
    const completed = Array.isArray(result.data?.completed) ? result.data.completed : [];
    const real = completed.filter(
      (job): job is CollectedJob => isRecord(job) && String(job.company ?? "").length > 0,
    );
    // Removed postings ("Job id provided may not be valid...") load with the
    // generic "Jobs" title and no company. Drop them so they don't sit at the
    // top of the pool and burn the budget every run; transient empties stay
    // captured for a later retry.
    const deadIds = completed
      .filter((job) => isRecord(job) && job.dead === true && String(job.company ?? "") === "")
      .map((job) => String(job.id))
      .filter(Boolean);
    if (deadIds.length > 0) deleteJobs(deadIds);
    upsert(real);
    enriched += real.length;
  }
  const remaining = capturedRows().length;
  return { command: "jobs enrich", enriched, remaining };
}

export async function jobsDetail(
  input: JobsDetailInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const now = dependencies.now ?? nowDefault;
  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });
  const resetSession = dependencies.resetSession ?? defaultResetSession(input);
  const dbPath = join(input.stateDir, "linkedin-tools.db");
  // One job per playwriter invocation: the detail result carries the full
  // description (~2.5–6 KB), and the playwriter relay truncates an execute's
  // response text at 10 000 chars (executor.js MAX_LENGTH). A 3-job batch
  // exceeds that and returns unparseable JSON. Single jobs stay well under it.
  const batchSize = 1;
  const BUDGET_MS = 180_000;
  const startedAt = Date.now();
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
  const pendingRows = (): JobRow[] => {
    const opened = openDatabase(dbPath);
    try {
      return new JobsEngine(opened.database).listJobs({
        withHiringTeam: true,
        needsDetail: true,
        fit: "kept",
        ...(input.runId === undefined ? {} : { jobIds: runJobIds(opened.database, input.runId) }),
      });
    } finally {
      opened.database.close();
    }
  };
  const store = (details: JobDetail[]): void => {
    if (details.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).storeJobDetails(details, now());
    } finally {
      opened.database.close();
    }
  };
  const deleteJobs = (ids: string[]): void => {
    if (ids.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).deleteJobs(ids);
    } finally {
      opened.database.close();
    }
  };

  try {
    const cleanup = buildCleanupTabsScript();
    await runPhase(cleanup.script, cleanup.timeoutMs);
  } catch {
    // ignore — the detail loop below surfaces the real session failure
  }

  const pool = pendingRows();
  const toDetail = input.limit === undefined ? pool : pool.slice(0, input.limit);
  let detailed = 0;
  for (let i = 0; i < toDetail.length && Date.now() - startedAt < BUDGET_MS; i += batchSize) {
    const batch = toDetail.slice(i, i + batchSize).map((r) => ({ id: r.id }));
    const { script, timeoutMs } = buildDetailScript({ jobs: batch });
    const result = await runPhase(script, timeoutMs);
    if (result === null) break;
    const completed = Array.isArray(result.data?.completed) ? result.data.completed : [];
    const real = completed.filter(
      (d): d is JobDetail =>
        isRecord(d) && typeof d.description === "string" && d.description.length > 0,
    );
    const deadIds = completed
      .filter((d) => isRecord(d) && d.dead === true)
      .map((d) => String(d.id))
      .filter(Boolean);
    if (deadIds.length > 0) deleteJobs(deadIds);
    store(real);
    detailed += real.length;
  }
  const remaining = pendingRows().length;
  return { command: "jobs detail", detailed, remaining };
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

export async function jobsSend(
  input: JobsSendInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const now = dependencies.now ?? nowDefault;

  // Validate targets and seed the defensive recipient set from already-sent
  // jobs before any browser session is created, so a no-op send fails without
  // opening a session.
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  let targets: JobRow[];
  const sentRecipients = new Set<string>();
  try {
    const engine = new JobsEngine(opened.database);
    for (const sent of engine.sentJobs()) {
      const profile = recipientProfileUrl(sent);
      if (profile !== null) sentRecipients.add(profile);
    }
    if (input.id === undefined) {
      targets = engine.approvedDrafts();
    } else {
      const job = engine.requireJob(input.id);
      if (job.review !== "approved")
        throw new CliError("JOBS_NOT_APPROVED", `job ${job.id} is not an approved draft`, {
          exitCode: 2,
        });
      if (job.status !== "drafted")
        throw new CliError("JOBS_NOT_DRAFTED", `job ${job.id} is not a pending draft`, {
          exitCode: 2,
        });
      targets = [job];
    }
  } finally {
    opened.database.close();
  }
  if (targets.length === 0)
    throw new CliError("JOBS_NOTHING_TO_SEND", "no approved drafts to send", { exitCode: 2 });

  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });

  const results: unknown[] = [];
  const seenRecipients = new Set(sentRecipients);
  for (const job of targets) {
    const member = job.hiringTeam[0];
    if (member === undefined)
      throw new CliError(
        "JOBS_NO_HIRING_TEAM",
        `job ${job.id} has no hiring team member to message`,
        {
          exitCode: 2,
        },
      );
    if (job.message === null || job.message.trim().length === 0)
      throw new CliError("JOBS_NO_DRAFT", `job ${job.id} has no drafted message`, { exitCode: 2 });
    const profile = recipientProfileUrl(job);
    if (profile === null)
      throw new CliError(
        "JOBS_NO_HIRING_TEAM",
        `job ${job.id} has no usable hiring-team profile URL`,
        { exitCode: 2 },
      );
    // Defensive duplicate-recipient guard: never message one profile twice,
    // across runs or within one run. Already-sent/approved recipients are
    // skipped without changing state.
    if (seenRecipients.has(profile)) {
      results.push({
        jobId: job.id,
        title: job.title,
        member: member.name,
        status: "skipped",
        detail: "duplicate recipient (already sent or approved)",
      });
      continue;
    }
    seenRecipients.add(profile);
    const { script, timeoutMs } = buildSendScript({
      jobId: job.id,
      memberName: member.name,
      profileUrl: member.profileUrl,
      subject: job.subject,
      message: job.message,
    });
    const outcome = await runScript({
      playwriterBin: input.playwriterBin,
      sessionId,
      script,
      timeoutMs,
      stateDir: input.stateDir,
    });
    const data = outcome.data as Record<string, unknown>;
    const status = data.status;
    const openedAgain = openDatabase(join(input.stateDir, "linkedin-tools.db"));
    try {
      if (status === "sent") new JobsEngine(openedAgain.database).markSent(job.id, now());
    } finally {
      openedAgain.database.close();
    }
    results.push({ jobId: job.id, title: job.title, member: member.name, ...data });
  }
  return {
    command: "jobs send",
    sent: results.filter((r) => (r as Record<string, unknown>).status === "sent").length,
    skipped: results.filter((r) => (r as Record<string, unknown>).status !== "sent").length,
    results,
  };
}
