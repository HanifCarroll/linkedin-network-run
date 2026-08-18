import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { CliError } from "../core/errors.ts";
import { openDatabase } from "../db/database.ts";
import { JobsEngine } from "../jobs/engine.ts";
import { REVIEW_DECISIONS, type ReviewDecision } from "../jobs/types.ts";

const here = dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.LINKEDIN_TOOLS_VIEW_PORT ?? 4567);
const origin = `http://127.0.0.1:${port}`;
const jsonHeaders = { "content-type": "application/json; charset=utf-8" };

function stateDir(): string {
  const idx = process.argv.indexOf("--state-dir");
  const flag = idx >= 0 ? process.argv[idx + 1] : undefined;
  return resolve(
    flag ??
      process.env.LINKEDIN_TOOLS_STATE_DIR ??
      join(homedir(), "Library", "Application Support", "linkedin-tools-next"),
  );
}

const dbPath = join(stateDir(), "linkedin-tools.db");
if (!(await Bun.file(dbPath).exists())) {
  console.error(`jobs viewer: no database at ${dbPath}`);
  console.error(
    "run jobs capture-start/capture-ingest; raw pages await Step 2 normalization, or set --state-dir / LINKEDIN_TOOLS_STATE_DIR",
  );
  process.exit(1);
}

const indexHtml = await Bun.file(join(here, "index.html")).text();

const groupingBundle = await buildGroupingBundle();

async function buildGroupingBundle(): Promise<string> {
  const result = await Bun.build({
    entrypoints: [join(here, "grouping.ts")],
    target: "browser",
    format: "esm",
    minify: false,
    sourcemap: "none",
  });
  if (!result.success) {
    throw new Error(`grouping bundle failed: ${result.logs.map((log) => log.message).join("; ")}`);
  }
  const output = result.outputs.find((entry) => entry.kind === "entry-point");
  if (output === undefined) throw new Error("grouping bundle produced no entry point");
  return output.text();
}

function readJobs(): unknown {
  // WAL-mode databases can't be opened with SQLITE_OPEN_READONLY (the -shm
  // file needs directory write access), so open normally and enforce
  // read-only at the SQL layer instead.
  const opened = openDatabase(dbPath);
  try {
    opened.database.exec("PRAGMA query_only = ON;");
    return new JobsEngine(opened.database).listJobs({ withHiringTeam: true });
  } finally {
    opened.database.close();
  }
}

/** Write path: open normally and route the mutation through JobsEngine. */
function withJobsEngine<T>(operation: (engine: JobsEngine) => T): T {
  const opened = openDatabase(dbPath);
  try {
    return operation(new JobsEngine(opened.database));
  } finally {
    opened.database.close();
  }
}

function isSameOrigin(request: Request): boolean {
  const requestOrigin = request.headers.get("origin");
  if (requestOrigin === null) return true; // non-browser client (curl etc.)
  return requestOrigin === origin;
}

async function readJsonBody(request: Request): Promise<unknown> {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new CliError("INVALID_ARGUMENT", "content-type must be application/json");
  }
  const text = await request.text();
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new CliError("INVALID_ARGUMENT", "body must be valid JSON");
  }
}

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new CliError("INVALID_ARGUMENT", `${label} must be a JSON object`);
  }
  return value as Record<string, unknown>;
}

function draftBody(value: unknown): { readonly subject: string; readonly message: string } {
  const record = asRecord(value, "draft body");
  const subject = record.subject;
  const message = record.message;
  if (typeof subject !== "string" || typeof message !== "string") {
    throw new CliError("INVALID_ARGUMENT", "draft body requires string subject and message");
  }
  return { subject, message };
}

function reviewBody(value: unknown): {
  readonly review: ReviewDecision;
  readonly replaceId?: string;
} {
  const record = asRecord(value, "review body");
  const review = record.review;
  if (typeof review !== "string" || !REVIEW_DECISIONS.includes(review as ReviewDecision)) {
    throw new CliError("INVALID_ARGUMENT", `review must be one of ${REVIEW_DECISIONS.join(", ")}`);
  }
  const replaceId = record.replaceId;
  if (replaceId !== undefined && (typeof replaceId !== "string" || replaceId.length === 0)) {
    throw new CliError("INVALID_ARGUMENT", "replaceId must be a non-empty string");
  }
  return { review: review as ReviewDecision, ...(replaceId === undefined ? {} : { replaceId }) };
}

function groupReviewBody(value: unknown): { readonly review: "skipped" | "needs_review" } {
  const record = asRecord(value, "group review body");
  const review = record.review;
  if (review !== "skipped" && review !== "needs_review") {
    throw new CliError("INVALID_ARGUMENT", "group review must be skipped or needs_review");
  }
  return { review };
}

function errorResponse(error: unknown): Response {
  const cli =
    error instanceof CliError
      ? error
      : new CliError("VIEWER_ERROR", String((error as Error)?.message ?? error));
  const status =
    cli.code === "DUPLICATE_APPROVED_PROFILE" || cli.code === "DUPLICATE_REPLACE_STALE"
      ? 409
      : cli.code === "JOB_NOT_FOUND"
        ? 404
        : cli.code === "INVALID_ARGUMENT"
          ? 400
          : 500;
  return new Response(
    JSON.stringify({
      ok: false,
      error: {
        code: cli.code,
        message: cli.message,
        ...(cli.details === undefined ? {} : { details: cli.details }),
      },
    }),
    { status, headers: jsonHeaders },
  );
}

function jsonOk(data: unknown): Response {
  return new Response(JSON.stringify({ ok: true, data }), { headers: jsonHeaders });
}

async function handleWrite(request: Request, pathname: string): Promise<Response> {
  if (!isSameOrigin(request)) {
    return new Response(
      JSON.stringify({
        ok: false,
        error: { code: "FORBIDDEN", message: "cross-origin request rejected" },
      }),
      { status: 403, headers: jsonHeaders },
    );
  }
  const draftMatch = /^\/api\/jobs\/([^/]+)\/draft$/.exec(pathname);
  const reviewMatch = /^\/api\/jobs\/([^/]+)\/review$/.exec(pathname);
  const groupReviewMatch = /^\/api\/recipients\/([^/]+)\/review$/.exec(pathname);
  const id = draftMatch?.[1] ?? reviewMatch?.[1] ?? groupReviewMatch?.[1];
  if (id === undefined) return new Response("not found", { status: 404 });
  try {
    const body = await readJsonBody(request);
    const now = new Date().toISOString();
    if (draftMatch !== null) {
      const { subject, message } = draftBody(body);
      const job = withJobsEngine((engine) => engine.storeDraft(id, subject, message, now));
      return jsonOk(job);
    }
    if (groupReviewMatch !== null) {
      const { review } = groupReviewBody(body);
      const jobs = withJobsEngine((engine) => engine.setGroupReview(id, review, now));
      return jsonOk(jobs);
    }
    const { review, replaceId } = reviewBody(body);
    const job = withJobsEngine((engine) => engine.setReview(id, review, now, replaceId));
    return jsonOk(job);
  } catch (error) {
    return errorResponse(error);
  }
}

Bun.serve({
  hostname: "127.0.0.1",
  port,
  async fetch(request) {
    const { pathname } = new URL(request.url);
    if (request.method === "POST") return handleWrite(request, pathname);
    if (pathname === "/") {
      return new Response(indexHtml, { headers: { "content-type": "text/html; charset=utf-8" } });
    }
    if (pathname === "/jobs.json") {
      try {
        return jsonOk(readJobs());
      } catch (error) {
        return errorResponse(error);
      }
    }
    if (pathname === "/groups.js") {
      return new Response(groupingBundle, {
        headers: { "content-type": "text/javascript; charset=utf-8" },
      });
    }
    return new Response("not found", { status: 404 });
  },
});

console.log(`outreach review queue → http://127.0.0.1:${port}`);
