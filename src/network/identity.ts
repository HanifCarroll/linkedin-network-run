import type { Database } from "bun:sqlite";
import type { SourceId } from "./config.ts";
import type { IdentityInput } from "./types.ts";

export type PersonRow = {
  id: string;
  sales_nav_id: string | null;
  public_url: string | null;
  lead_key: string | null;
  name: string;
};

export type AliasEvidence = {
  invocationId: string;
  observationId: string;
  sourceId: SourceId;
};

export function canonicalPublicUrl(value: string | undefined): string | undefined {
  if (value === undefined || value.trim() === "") return undefined;
  const url = new URL(value);
  if (url.hostname !== "www.linkedin.com" && url.hostname !== "linkedin.com") {
    throw new Error("public URL must be a LinkedIn URL");
  }
  const match = url.pathname.match(/^\/in\/([^/]+)/);
  if (match?.[1] === undefined) throw new Error("public URL must use /in/<slug>");
  return `https://www.linkedin.com/in/${match[1].toLowerCase()}`;
}

export function canonicalIdentity(person: PersonRow): string {
  return person.sales_nav_id ?? person.public_url ?? person.lead_key ?? "";
}

function findBy(
  database: Database,
  column: "sales_nav_id" | "public_url" | "lead_key",
  value: string | undefined,
): PersonRow | null {
  if (value === undefined) return null;
  const direct = database
    .query<PersonRow, [string]>(
      `SELECT id, sales_nav_id, public_url, lead_key, name FROM people WHERE ${column} = ?`,
    )
    .get(value);
  if (direct !== null) return direct;
  return database
    .query<PersonRow, [string, string]>(
      `SELECT p.id, p.sales_nav_id, p.public_url, p.lead_key, p.name
       FROM person_aliases a JOIN people p ON p.id = a.person_id
       WHERE a.kind = ? AND a.value = ?`,
    )
    .get(column, value);
}

function normalizedIdentities(input: IdentityInput): {
  salesNavId?: string;
  publicUrl?: string;
  leadKey?: string;
} {
  const result: { salesNavId?: string; publicUrl?: string; leadKey?: string } = {};
  const salesNavId = input.salesNavId?.trim();
  const publicUrl = canonicalPublicUrl(input.publicUrl);
  const leadKey = input.leadKey?.trim();
  if (salesNavId) result.salesNavId = salesNavId;
  if (publicUrl) result.publicUrl = publicUrl;
  if (leadKey) result.leadKey = leadKey;
  return result;
}

export function findExistingPerson(database: Database, input: IdentityInput): PersonRow | null {
  const { salesNavId, publicUrl, leadKey } = normalizedIdentities(input);
  const matches = [
    findBy(database, "sales_nav_id", salesNavId),
    findBy(database, "public_url", publicUrl),
    findBy(database, "lead_key", leadKey),
  ].filter((row): row is PersonRow => row !== null);
  const uniqueIds = new Set(matches.map((row) => row.id));
  if (uniqueIds.size > 1) throw new Error("identity conflict");
  return matches[0] ?? null;
}

function exactAliasEvidence(
  database: Database,
  evidence: AliasEvidence | undefined,
  expected: { salesNavId?: string; publicUrl?: string; leadKey?: string },
  existing: PersonRow,
): {
  anchorKind: "sales_nav_id" | "public_url" | "lead_key";
  anchorValue: string;
  evidenceJson: string;
} {
  if (evidence === undefined) {
    throw new Error("new aliases require exact source observation evidence");
  }
  const row = database
    .query<
      {
        identity_evidence_json: string;
        invocation_id: string;
        person_id: string | null;
        source_id: string;
      },
      [string]
    >(
      `SELECT identity_evidence_json, invocation_id, person_id, source_id
       FROM source_observations WHERE id = ?`,
    )
    .get(evidence.observationId);
  if (
    row === null ||
    row.invocation_id !== evidence.invocationId ||
    row.person_id !== existing.id ||
    row.source_id !== evidence.sourceId
  ) {
    throw new Error("alias evidence does not match the exact source observation");
  }
  const observed = JSON.parse(row.identity_evidence_json) as Record<string, string | undefined>;
  for (const [key, value] of Object.entries(expected)) {
    if (value !== undefined && observed[key] !== value) {
      throw new Error("alias evidence does not connect the supplied identities");
    }
  }
  const anchor = (
    [
      ["sales_nav_id", "salesNavId", existing.sales_nav_id],
      ["public_url", "publicUrl", existing.public_url],
      ["lead_key", "leadKey", existing.lead_key],
    ] as const
  ).find(([, inputKey, value]) => value !== null && observed[inputKey] === value);
  if (anchor === undefined || anchor[2] === null) {
    throw new Error("alias evidence does not include an existing anchor identity");
  }
  return {
    anchorKind: anchor[0],
    anchorValue: anchor[2],
    evidenceJson: JSON.stringify({
      anchorKind: anchor[0],
      anchorValue: anchor[2],
      invocationId: evidence.invocationId,
      observationId: evidence.observationId,
      sourceId: evidence.sourceId,
    }),
  };
}

export function resolveOrCreatePerson(
  database: Database,
  input: IdentityInput,
  now: string,
  id: string = crypto.randomUUID(),
  aliasEvidence?: AliasEvidence,
): PersonRow {
  const { salesNavId, publicUrl, leadKey } = normalizedIdentities(input);
  if (salesNavId === undefined && publicUrl === undefined && leadKey === undefined) {
    throw new Error("a stable identity is required");
  }

  const existing = findExistingPerson(database, input) ?? undefined;

  if (existing === undefined) {
    database
      .query(
        `INSERT INTO people
          (id, sales_nav_id, public_url, lead_key, name, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(id, salesNavId ?? null, publicUrl ?? null, leadKey ?? null, input.name, now, now);
    return {
      id,
      sales_nav_id: salesNavId ?? null,
      public_url: publicUrl ?? null,
      lead_key: leadKey ?? null,
      name: input.name,
    };
  }

  const identityValues = [
    ["sales_nav_id", salesNavId, existing.sales_nav_id],
    ["public_url", publicUrl, existing.public_url],
    ["lead_key", leadKey, existing.lead_key],
  ] as const;
  const newAliases: Array<["sales_nav_id" | "public_url" | "lead_key", string]> = [];
  for (const [kind, value, prior] of identityValues) {
    if (value !== undefined && prior === null) newAliases.push([kind, value]);
  }
  for (const [column, value, prior] of identityValues) {
    if (value !== undefined && prior !== null && prior !== value) {
      throw new Error(`identity conflict for ${column}`);
    }
  }

  const expectedAliases: { salesNavId?: string; publicUrl?: string; leadKey?: string } = {};
  if (salesNavId !== undefined) expectedAliases.salesNavId = salesNavId;
  if (publicUrl !== undefined) expectedAliases.publicUrl = publicUrl;
  if (leadKey !== undefined) expectedAliases.leadKey = leadKey;
  const evidence =
    newAliases.length === 0
      ? undefined
      : exactAliasEvidence(database, aliasEvidence, expectedAliases, existing);
  database
    .query(
      `UPDATE people SET
         sales_nav_id = COALESCE(sales_nav_id, ?),
         public_url = COALESCE(public_url, ?),
         lead_key = COALESCE(lead_key, ?),
         updated_at = ?
       WHERE id = ?`,
    )
    .run(salesNavId ?? null, publicUrl ?? null, leadKey ?? null, now, existing.id);
  for (const [kind, value] of newAliases) {
    database
      .query(
        `INSERT INTO person_aliases
         (person_id, kind, value, evidence, created_at, anchor_kind, anchor_value,
          evidence_observation_id, evidence_invocation_id, evidence_source_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        existing.id,
        kind,
        value,
        requiredEvidence(evidence).evidenceJson,
        now,
        requiredEvidence(evidence).anchorKind,
        requiredEvidence(evidence).anchorValue,
        requiredAliasEvidence(aliasEvidence).observationId,
        requiredAliasEvidence(aliasEvidence).invocationId,
        requiredAliasEvidence(aliasEvidence).sourceId,
      );
  }
  return {
    ...existing,
    sales_nav_id: existing.sales_nav_id ?? salesNavId ?? null,
    public_url: existing.public_url ?? publicUrl ?? null,
    lead_key: existing.lead_key ?? leadKey ?? null,
  };
}

function requiredEvidence<T>(evidence: T | undefined): T {
  if (evidence === undefined) throw new Error("alias evidence is required");
  return evidence;
}

function requiredAliasEvidence(evidence: AliasEvidence | undefined): AliasEvidence {
  if (evidence === undefined) throw new Error("alias evidence is required");
  return evidence;
}
