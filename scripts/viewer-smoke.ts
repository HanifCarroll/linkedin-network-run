import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { bootstrapDatabase, openDatabase } from "../src/db/database.ts";
import { JobsEngine } from "../src/jobs/engine.ts";

const stateDir = await mkdtemp(join(tmpdir(), "linkedin-viewer-"));
const port = 4876;
const dbPath = join(stateDir, "linkedin-tools.db");
bootstrapDatabase(dbPath);
const opened = openDatabase(dbPath);
new JobsEngine(opened.database).upsertJobs(
  [
    {
      id: "viewer-smoke-job",
      title: "Product Engineer",
      company: "Example Co",
      location: "Remote",
      postingUrl: "https://www.linkedin.com/jobs/view/viewer-smoke-job/",
      hiringTeam: [
        {
          name: "Alex",
          profileUrl: "https://www.linkedin.com/in/alex",
          degree: "1st",
          headline: "Hiring manager",
        },
      ],
      hasHiringTeam: true,
    },
    {
      id: "viewer-smoke-contract",
      title: "Contract Product Engineer",
      company: "Contract Example Co",
      location: "Remote",
      postingUrl: "https://www.linkedin.com/jobs/view/viewer-smoke-contract/",
      hiringTeam: [
        {
          name: "Casey",
          profileUrl: "https://www.linkedin.com/in/casey",
          degree: "2nd",
          headline: "Hiring manager",
        },
      ],
      hasHiringTeam: true,
    },
  ],
  new Date().toISOString(),
);
opened.database.exec(
  `UPDATE jobs SET fit = 'kept', triage_bucket = 'strong', status = 'collected'
   WHERE id IN ('viewer-smoke-job', 'viewer-smoke-contract');
   UPDATE jobs SET employment_type = 'Contract' WHERE id = 'viewer-smoke-contract';`,
);
opened.database.close();
const server = Bun.spawn(["bun", "./build/index.js"], {
  env: { ...process.env, LINKEDIN_TOOLS_STATE_DIR: stateDir, PORT: String(port) },
  stdout: "ignore",
  stderr: "pipe",
});
try {
  let response: Response | undefined;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      response = await fetch(`http://127.0.0.1:${port}/`);
      break;
    } catch {
      await Bun.sleep(100);
    }
  }
  if (response === undefined || !response.ok) throw new Error("viewer did not start");
  const html = await response.text();
  if (!html.includes("Outreach Review Queue")) throw new Error("viewer shell missing");
  const directApplication = await fetch(
    `http://127.0.0.1:${port}/api/jobs/viewer-smoke-job/application`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ appliedAt: "2026-08-20", applicationUrl: "" }),
    },
  );
  if (directApplication.status !== 400)
    throw new Error(`direct job exposed application checkpoint: ${directApplication.status}`);
  const contractApplication = await fetch(
    `http://127.0.0.1:${port}/api/jobs/viewer-smoke-contract/application`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ appliedAt: "2026-08-20", applicationUrl: "" }),
    },
  );
  if (contractApplication.status !== 200 || !(await contractApplication.json()).ok) {
    throw new Error(`contract application checkpoint failed: ${contractApplication.status}`);
  }
  const jobs = await fetch(`http://127.0.0.1:${port}/api/jobs`);
  if ((await jobs.json()).ok !== true) throw new Error("jobs endpoint envelope missing");
  const draft = await fetch(`http://127.0.0.1:${port}/api/jobs/viewer-smoke-job/draft`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      connectionNote: "A short connection note",
      subject: "Hello",
      message: "A saved follow-up draft",
    }),
  });
  if (draft.status !== 200 || !(await draft.json()).ok) throw new Error("draft write failed");
  const approved = await fetch(`http://127.0.0.1:${port}/api/jobs/viewer-smoke-job/review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ review: "approved" }),
  });
  if (approved.status !== 200 || !(await approved.json()).ok)
    throw new Error("approve write failed");
  const rejected = await fetch(`http://127.0.0.1:${port}/api/recipients/viewer-smoke-job/review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ review: "skipped" }),
  });
  if (rejected.status !== 200 || !(await rejected.json()).ok)
    throw new Error("reject write failed");
  const invalid = await fetch(`http://127.0.0.1:${port}/api/jobs/missing/draft`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  });
  if (invalid.status !== 400) throw new Error(`invalid draft status: ${invalid.status}`);
  const persisted = openDatabase(dbPath);
  try {
    const job = new JobsEngine(persisted.database)
      .listJobs({ withHiringTeam: false })
      .find((candidate) => candidate.id === "viewer-smoke-job");
    if (
      job?.connectionNote !== "A short connection note" ||
      job.subject !== "Hello" ||
      job.message !== "A saved follow-up draft"
    )
      throw new Error("draft did not persist through JobsEngine");
    if (job.review !== "skipped") throw new Error("review decision did not persist");
    const contract = new JobsEngine(persisted.database).requireJob("viewer-smoke-contract");
    if (contract.appliedAt !== "2026-08-20" || contract.applicationUrl !== null) {
      throw new Error("contract application checkpoint did not persist without an optional URL");
    }
  } finally {
    persisted.database.close();
  }
  console.log(
    JSON.stringify({ ok: true, data: { viewer: "passed", liveBrowser: false, realState: false } }),
  );
} finally {
  server.kill();
  await server.exited;
  await rm(stateDir, { recursive: true, force: true });
}
