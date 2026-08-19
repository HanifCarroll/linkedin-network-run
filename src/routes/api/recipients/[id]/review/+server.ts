import type { RequestHandler } from "@sveltejs/kit";
import { writeGroupReview } from "../../../../../view/write.ts";
export const POST: RequestHandler = ({ request, params }) =>
  writeGroupReview(request, params.id ?? "");
