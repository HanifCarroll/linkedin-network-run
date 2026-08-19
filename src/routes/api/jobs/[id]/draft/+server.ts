import type { RequestHandler } from "@sveltejs/kit";
import { writeDraft } from "../../../../../../src/view/write.ts";
export const POST: RequestHandler = ({ request, params }) => writeDraft(request, params.id ?? "");
