import { json } from "@sveltejs/kit";
import type { RequestHandler } from "@sveltejs/kit";
import { openDatabase } from "../../../../../../../db/database.ts";
import { SalesNavPeopleStore } from "../../../../../../../salesnav-people.ts";
import { viewerStateDir } from "../../../../../../../view/state.ts";

export const POST: RequestHandler = async ({ params, request }) => {
  const body = (await request.json().catch(() => null)) as {
    review?: unknown;
    evidence?: unknown;
  } | null;
  if (!body || typeof body.review !== "string")
    return json(
      { ok: false, error: { code: "INVALID_ARGUMENT", message: "review is required" } },
      { status: 400 },
    );
  const opened = openDatabase(`${viewerStateDir()}/linkedin-tools.db`);
  try {
    const data = new SalesNavPeopleStore(opened.database).review(
      {
        command: "account-people-review",
        stateDir: viewerStateDir(),
        runId: params.runId ?? "",
        personId: params.personId ?? "",
        review: body.review as "needs_review" | "approved" | "rejected",
        evidenceJson: JSON.stringify(body.evidence ?? {}),
      },
      new Date().toISOString(),
    );
    return json({ ok: true, data });
  } catch (error) {
    const e = error as { code?: string; message?: string };
    return json(
      { ok: false, error: { code: e.code ?? "ERROR", message: e.message ?? "request failed" } },
      { status: 400 },
    );
  } finally {
    opened.database.close();
  }
};
