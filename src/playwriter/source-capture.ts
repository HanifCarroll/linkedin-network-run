import { sha256Json, terminalFingerprint } from "../core/evidence-contract.ts";
import type { NetworkSourceContract, NetworkSourceId } from "./types.ts";
import { assertAllowedWorkflowUrl } from "./urls.ts";

const CONTRACT_FIELDS = [
  "schemaVersion",
  "kind",
  "contractVersion",
  "sourceId",
  "sourceName",
  "savedSearchId",
  "searchUrl",
  "contractFingerprint",
] as const;

const definitions = [
  {
    sourceId: "hubspot-agency-ops",
    sourceName: "Consulting - HubSpot Agency Ops",
    savedSearchId: "1980844577",
  },
  {
    sourceId: "hubspot-b2b-revops",
    sourceName: "Consulting - HubSpot B2B RevOps",
    savedSearchId: "1980870185",
  },
] as const;

function makeContract(definition: (typeof definitions)[number]): NetworkSourceContract {
  const base = {
    schemaVersion: 1 as const,
    kind: "network_source_contract" as const,
    contractVersion: 1 as const,
    sourceId: definition.sourceId,
    sourceName: definition.sourceName,
    savedSearchId: definition.savedSearchId,
    searchUrl: `https://www.linkedin.com/sales/search/people?savedSearchId=${definition.savedSearchId}`,
  };
  return Object.freeze({ ...base, contractFingerprint: sha256Json(base) });
}

export const NETWORK_SOURCE_CONTRACTS: readonly NetworkSourceContract[] = Object.freeze(
  definitions.map(makeContract),
);

export function resolveNetworkSourceContract(rawUrl: string): NetworkSourceContract {
  assertAllowedWorkflowUrl("candidateResults", rawUrl);
  const contract = NETWORK_SOURCE_CONTRACTS.find((item) => item.searchUrl === rawUrl);
  if (contract === undefined) throw new TypeError("source contract mismatch");
  return contract;
}

export function networkSourceContract(sourceId: NetworkSourceId): NetworkSourceContract {
  const contract = NETWORK_SOURCE_CONTRACTS.find((item) => item.sourceId === sourceId);
  if (contract === undefined) throw new TypeError("source contract mismatch");
  return contract;
}

export function assertNetworkSourceContract(
  value: unknown,
): asserts value is NetworkSourceContract {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new TypeError("source contract must be object");
  const candidate = value as Record<string, unknown>;
  const keys = Object.keys(candidate);
  if (
    keys.length !== CONTRACT_FIELDS.length ||
    !keys.every((key) => (CONTRACT_FIELDS as readonly string[]).includes(key))
  )
    throw new TypeError("source contract fields are invalid");
  if (typeof candidate.searchUrl !== "string") throw new TypeError("source contract URL invalid");
  const expected = resolveNetworkSourceContract(candidate.searchUrl);
  for (const field of CONTRACT_FIELDS)
    if (candidate[field] !== expected[field]) throw new TypeError("source contract mismatch");
}

export { terminalFingerprint };
