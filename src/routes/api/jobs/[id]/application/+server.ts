import type { RequestHandler } from "@sveltejs/kit";
import { writeApplication } from "../../../../../../src/view/write.ts";

export const POST: RequestHandler = ({ request, params }) =>
  writeApplication(request, params.id ?? "");
