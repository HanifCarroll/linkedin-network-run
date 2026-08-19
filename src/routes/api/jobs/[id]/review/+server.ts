import type { RequestHandler } from "@sveltejs/kit";
import { writeReview } from "../../../../../../src/view/write.ts";
export const POST: RequestHandler = ({ request, params }) => writeReview(request, params.id ?? "");
