import { join } from "node:path";
import { json } from "@sveltejs/kit";
import { CliError } from "../core/errors.ts";
import { openDatabase } from "../db/database.ts";
import { JobsEngine } from "../jobs/engine.ts";
import { REVIEW_DECISIONS, type ReviewDecision } from "../jobs/types.ts";
import { outreachKindFor } from "./grouping.ts";
import { viewerStateDir } from "./state.ts";

const headers = { "content-type": "application/json; charset=utf-8" };
const dbPath = () => join(viewerStateDir(), "linkedin-tools.db");
const record = (v: unknown, label: string) => {
  if (typeof v !== "object" || v === null || Array.isArray(v))
    throw new CliError("INVALID_ARGUMENT", `${label} must be a JSON object`);
  return v as Record<string, unknown>;
};
async function body(request: Request) {
  if (!(request.headers.get("content-type") ?? "").includes("application/json"))
    throw new CliError("INVALID_ARGUMENT", "content-type must be application/json");
  try {
    return record(await request.json(), "body");
  } catch (e) {
    if (e instanceof CliError) throw e;
    throw new CliError("INVALID_ARGUMENT", "body must be valid JSON");
  }
}
function respond(error: unknown) {
  const e =
    error instanceof CliError
      ? error
      : new CliError("VIEWER_ERROR", String((error as Error)?.message ?? error));
  const status =
    e.code === "JOB_NOT_FOUND"
      ? 404
      : e.code === "INVALID_ARGUMENT"
        ? 400
        : e.code.startsWith("DUPLICATE_")
          ? 409
          : 500;
  return json(
    {
      ok: false,
      error: {
        code: e.code,
        message: e.message,
        ...(e.details === undefined ? {} : { details: e.details }),
      },
    },
    { status, headers },
  );
}
function withEngine<T>(fn: (engine: JobsEngine) => T) {
  const opened = openDatabase(dbPath());
  try {
    return fn(new JobsEngine(opened.database));
  } finally {
    opened.database.close();
  }
}
async function write(
  request: Request,
  fn: (engine: JobsEngine, value: Record<string, unknown>) => unknown,
) {
  if (
    request.headers.get("origin") &&
    request.headers.get("origin") !==
      `http://127.0.0.1:${process.env.PORT ?? process.env.LINKEDIN_TOOLS_VIEW_PORT ?? 4567}`
  )
    return json(
      { ok: false, error: { code: "FORBIDDEN", message: "cross-origin request rejected" } },
      { status: 403, headers },
    );
  try {
    const value = await body(request);
    return json({ ok: true, data: withEngine((engine) => fn(engine, value)) }, { headers });
  } catch (e) {
    return respond(e);
  }
}
export const writeDraft = (r: Request, id: string) =>
  write(r, (e, v) => {
    if (typeof v.subject !== "string" || typeof v.message !== "string")
      throw new CliError("INVALID_ARGUMENT", "draft body requires string subject and message");
    return e.storeDraft(id, v.subject, v.message, new Date().toISOString());
  });
export const writeReview = (r: Request, id: string) =>
  write(r, (e, v) => {
    if (typeof v.review !== "string" || !REVIEW_DECISIONS.includes(v.review as ReviewDecision))
      throw new CliError(
        "INVALID_ARGUMENT",
        `review must be one of ${REVIEW_DECISIONS.join(", ")}`,
      );
    const replaceId = v.replaceId;
    if (replaceId !== undefined && (typeof replaceId !== "string" || replaceId.length === 0))
      throw new CliError("INVALID_ARGUMENT", "replaceId must be a non-empty string");
    return e.setReview(
      id,
      v.review as ReviewDecision,
      new Date().toISOString(),
      replaceId as string | undefined,
    );
  });
export const writeApplication = (r: Request, id: string) =>
  write(r, (e, v) => {
    const job = e.requireJob(id);
    if (outreachKindFor(job) !== "application_followup")
      throw new CliError(
        "INVALID_ARGUMENT",
        "application checkpoint is only available for application-follow-up jobs",
      );
    if (typeof v.appliedAt !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v.appliedAt))
      throw new CliError("INVALID_ARGUMENT", "appliedAt must be a YYYY-MM-DD date");
    if (v.applicationUrl !== undefined && typeof v.applicationUrl !== "string")
      throw new CliError("INVALID_ARGUMENT", "applicationUrl must be a string");
    const applicationUrl = v.applicationUrl?.trim();
    return e.recordApplied(
      id,
      applicationUrl === "" ? undefined : applicationUrl,
      v.appliedAt,
      new Date().toISOString(),
    );
  });
export const writeGroupReview = (r: Request, id: string) =>
  write(r, (e, v) => {
    if (v.review !== "skipped" && v.review !== "needs_review")
      throw new CliError("INVALID_ARGUMENT", "group review must be skipped or needs_review");
    return e.setGroupReview(id, v.review, new Date().toISOString());
  });
