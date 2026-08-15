import { Database } from "bun:sqlite";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { JobsEngine } from "../jobs/engine.ts";

const here = dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.LINKEDIN_TOOLS_VIEW_PORT ?? 4567);

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
  console.error("run jobs collect/enrich first, or set --state-dir / LINKEDIN_TOOLS_STATE_DIR");
  process.exit(1);
}

const indexHtml = await Bun.file(join(here, "index.html")).text();

function readJobs(): unknown {
  // WAL-mode databases can't be opened with SQLITE_OPEN_READONLY (the -shm
  // file needs directory write access), so open normally and enforce
  // read-only at the SQL layer instead.
  const db = new Database(dbPath);
  try {
    db.exec("PRAGMA query_only = ON;");
    return new JobsEngine(db).listJobs({ withHiringTeam: true });
  } finally {
    db.close();
  }
}
Bun.serve({
  hostname: "127.0.0.1",
  port,
  fetch(request) {
    const { pathname } = new URL(request.url);
    if (pathname === "/") {
      return new Response(indexHtml, { headers: { "content-type": "text/html; charset=utf-8" } });
    }
    if (pathname === "/jobs.json") {
      try {
        return new Response(JSON.stringify(readJobs()), {
          headers: { "content-type": "application/json; charset=utf-8" },
        });
      } catch (error) {
        return new Response(JSON.stringify({ error: String((error as Error)?.message ?? error) }), {
          status: 500,
          headers: { "content-type": "application/json; charset=utf-8" },
        });
      }
    }
    return new Response("not found", { status: 404 });
  },
});

console.log(`jobs viewer → http://127.0.0.1:${port}`);
