import { json } from "@sveltejs/kit";
import { load } from "../../+page.server.ts";
export async function GET() {
  return json({ ok: true, data: (await load()).jobs });
}
