export type RelationshipLane = "staffing" | "studio";

export type RelationshipPerson = {
  readonly id: string;
  readonly title: string;
  readonly description?: string;
  readonly summary?: string;
};

const ROLE_TERMS = {
  staffing: ["technical recruiter", "recruiter", "account manager", "practice leader"],
  studio: [
    "owner",
    "co-founder",
    "founder",
    "ceo",
    "chief executive officer",
    "president",
    "cto",
    "chief technology officer",
    "coo",
    "chief operating officer",
    "cpo",
    "chief product officer",
    "vp",
    "vice president",
    "director",
  ],
} as const;

const FUNCTION_TERMS = {
  staffing: ["software", "engineering", "product", "technical"],
  studio: ["engineering", "product", "operations", "technology", "software"],
} as const;

const roleMatch = (title: string, term: string): boolean => {
  if (term === "owner") return title.includes("owner") && !title.includes("product owner");
  if (term === "president") return title.includes("president") && !title.includes("vice president");
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\b${escaped}\\b`, "i").test(title);
};

export function selectRelationshipPeople<T extends RelationshipPerson>(
  people: readonly T[],
  lane: RelationshipLane,
  limit = 2,
): Array<T & { slot: "primary" | "backup"; matchedRole: string }> {
  const terms = ROLE_TERMS[lane];
  const functions = FUNCTION_TERMS[lane];
  return people
    .map((person) => {
      const title = person.title.toLowerCase();
      const roleEvidence =
        lane === "staffing" ? `${title} ${(person.description ?? "").toLowerCase()}` : title;
      const functionEvidence =
        `${title} ${person.description ?? ""} ${person.summary ?? ""}`.toLowerCase();
      const role = terms.find((term) => roleMatch(roleEvidence, term));
      const fn = functions.find((term) => functionEvidence.includes(term));
      return {
        person,
        role: role ?? "",
        roleIndex: role ? [...terms].indexOf(role) : Number.MAX_SAFE_INTEGER,
        score: role ? (fn ? 2 : 1) : 0,
      };
    })
    .filter((candidate) => candidate.score > 0)
    .sort(
      (a, b) =>
        a.roleIndex - b.roleIndex ||
        b.score - a.score ||
        a.person.title.localeCompare(b.person.title) ||
        a.person.id.localeCompare(b.person.id),
    )
    .slice(0, limit)
    .map((candidate, index) => ({
      ...candidate.person,
      slot: index === 0 ? "primary" : "backup",
      matchedRole: candidate.role,
    }));
}
