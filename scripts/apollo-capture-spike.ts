import { type RelationshipLane, selectRelationshipPeople } from "../src/relationship-selection.ts";

type Row = Record<string, unknown>;
const obj = (value: unknown): Row | null =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Row) : null;
const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");
const integer = (value: unknown): number | null =>
  Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;
const required = (value: unknown, name: string): string => {
  const result = text(value);
  if (!result) throw new Error(`${name} is required`);
  return result;
};

export function normalizeApolloAccounts(value: unknown) {
  const root = obj(value);
  if (!root || !Array.isArray(root.organizations))
    throw new Error("Apollo account response requires organizations[]");
  return root.organizations.map((value, index) => {
    const row = obj(value);
    if (!row) throw new Error(`organizations[${index}] must be an object`);
    return {
      apolloId: required(row.id, `organizations[${index}].id`),
      name: required(row.name, `organizations[${index}].name`),
      websiteUrl: text(row.website_url) || null,
      linkedinUrl: text(row.linkedin_url) || null,
      primaryDomain: text(row.primary_domain) || null,
      employeeCount: integer(row.estimated_num_employees),
      industry: text(row.industry) || null,
      location:
        [text(row.city), text(row.state), text(row.country)].filter(Boolean).join(", ") || null,
      sourceEvidence: row,
    };
  });
}

export function normalizeApolloPeople(value: unknown, lane: RelationshipLane) {
  const root = obj(value);
  if (!root || !Array.isArray(root.people))
    throw new Error("Apollo people response requires people[]");
  const people = root.people.map((value, index) => {
    const row = obj(value);
    if (!row) throw new Error(`people[${index}] must be an object`);
    const organization = obj(row.organization);
    const firstName = required(row.first_name, `people[${index}].first_name`);
    const lastName = text(row.last_name) || text(row.last_name_obfuscated);
    return {
      id: required(row.id, `people[${index}].id`),
      name: [firstName, lastName].filter(Boolean).join(" "),
      title: required(row.title, `people[${index}].title`),
      company: text(organization?.name) || null,
      linkedinUrl: text(row.linkedin_url) || null,
      sourceEvidence: row,
    };
  });
  return { people, selected: selectRelationshipPeople(people, lane) };
}

function selfCheck() {
  const accounts = normalizeApolloAccounts({
    organizations: [{ id: "org-1", name: "Example", country: "United States" }],
  });
  const staffing = normalizeApolloPeople(
    {
      people: [
        { id: "p1", first_name: "A", title: "Product Owner" },
        { id: "p2", first_name: "B", title: "Technical Recruiter" },
      ],
    },
    "staffing",
  );
  const studio = normalizeApolloPeople(
    {
      people: [
        { id: "p3", first_name: "C", title: "Vice President, Sales" },
        { id: "p4", first_name: "D", title: "Founder & CTO" },
      ],
    },
    "studio",
  );
  if (
    accounts[0]?.apolloId !== "org-1" ||
    staffing.selected[0]?.id !== "p2" ||
    studio.selected[0]?.id !== "p4"
  )
    throw new Error("Apollo spike self-check failed");
  return {
    accounts: accounts.length,
    staffing: staffing.selected.length,
    studio: studio.selected.length,
  };
}

const [kind, lane] = Bun.argv.slice(2);
try {
  const data =
    kind === "self-check"
      ? selfCheck()
      : kind === "accounts"
        ? normalizeApolloAccounts(await Bun.stdin.json())
        : kind === "people" && (lane === "staffing" || lane === "studio")
          ? normalizeApolloPeople(await Bun.stdin.json(), lane)
          : (() => {
              throw new Error(
                "usage: apollo-capture-spike.ts self-check|accounts|people staffing|studio",
              );
            })();
  console.log(JSON.stringify({ ok: true, data }));
} catch (error) {
  console.error(
    JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }),
  );
  process.exitCode = 2;
}
