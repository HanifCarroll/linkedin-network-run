import { json } from "@sveltejs/kit";
import { openDatabase } from "../../../../db/database.ts";
import { viewerStateDir } from "../../../../view/state.ts";

export async function GET() {
  const opened = openDatabase(`${viewerStateDir()}/linkedin-tools.db`);
  try {
    const people = opened.database
      .query(`
      SELECT s.run_id AS runId, s.slot, s.rank, s.matched_role AS matchedRole,
        p.person_id AS personId, p.full_name AS name, p.current_title AS title,
        p.current_company AS company, p.current_description AS description,
        p.geo_region AS location, p.lead_url AS profileUrl,
        pr.lane, org.name AS organizationName, org.linkedin_company_url AS companyUrl,
        org.website_url AS websiteUrl, q.reason AS firmReason,
        q.evidence_json AS firmEvidence, q.unknowns_json AS firmUnknowns,
        fr.services, fr.concrete_fact AS concreteFact, fr.source_urls_json AS firmSourceUrls,
        s.reason AS selectionReason,
        COALESCE(r.review, 'needs_review') AS review, r.evidence_json AS evidence
      FROM salesnav_account_people_selections s
      JOIN salesnav_account_people_runs pr ON pr.id=s.run_id
      JOIN salesnav_account_people p ON p.person_id=s.person_id AND p.organization_id=pr.organization_id
      JOIN organizations org ON org.id=pr.organization_id
      JOIN salesnav_account_lane_qualifications q
        ON q.organization_id=pr.organization_id AND q.lane=pr.lane AND q.fit='kept'
      LEFT JOIN salesnav_studio_firm_research fr
        ON fr.organization_id=pr.organization_id AND fr.lane='studio'
      LEFT JOIN salesnav_account_people_reviews r ON r.run_id=s.run_id AND r.person_id=s.person_id
      ORDER BY pr.lane, org.name, CASE s.slot WHEN 'primary' THEN 0 ELSE 1 END, s.rank
    `)
      .all();
    return json({ ok: true, data: people });
  } finally {
    opened.database.close();
  }
}
